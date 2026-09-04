"""Disposable local state under $MELD_HOME: run records, snapshots, cursors, caches."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping

from meld.atomic import write_atomic
from meld.board import Board, RunRecord


def meld_home(env: Mapping[str, str]) -> Path:
    return Path(env.get("MELD_HOME", "~/.meld")).expanduser()


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class StateStore:
    def __init__(self, home: Path, group: str | None, role: str | None = None):
        self.home = home
        self.group = group
        self.role = role
        who = group or "human"
        # The ETag cache is per group (one token, one conditional-request set); the board
        # snapshot is per party, so an orchestrator and its PM never overwrite each other.
        self.cache_dir = home / "cache" / who
        self.state_dir = (home / "state" / who / role) if role else (home / "state" / who)
        self.runs_root = home / "runs"
        self.errors: list[str] = []

    # ---- snapshot ----------------------------------------------------------

    @property
    def snapshot_path(self) -> Path:
        return self.state_dir / "snapshot.json"

    def load_snapshot(self) -> dict | None:
        if not self.snapshot_path.exists():
            return None
        try:
            return json.loads(self.snapshot_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            self.errors.append(f"{self.snapshot_path}: {e.__class__.__name__}: {e}")
            return None

    def save_snapshot(self, digest: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(self.snapshot_path, json.dumps(digest, sort_keys=True))

    # ---- run records -------------------------------------------------------

    def write_run(self, record: RunRecord) -> Path:
        d = self.runs_root / record.group
        d.mkdir(parents=True, exist_ok=True)
        data = asdict(record)
        data.pop("alive", None)
        path = d / f"{record.issue}.json"
        write_atomic(path, json.dumps(data, sort_keys=True))
        return path

    def _read_record(self, path: Path, alive: Callable[[int], bool]) -> RunRecord:
        data = json.loads(path.read_text())
        return RunRecord(
            group=data["group"], issue=int(data["issue"]), kind=data.get("kind", "build"), pr=data.get("pr"),
            pid=int(data["pid"]), started_at=data["started_at"], worktree=data["worktree"], log_path=data["log_path"],
            model=data["model"], effort=data["effort"], tier=data["tier"], milestone=int(data["milestone"]),
            alive=alive(int(data["pid"])), collected=bool(data.get("collected", False)),
        )

    def load_runs(self, alive: Callable[[int], bool] = pid_alive) -> dict[str, dict[int, RunRecord]]:
        out: dict[str, dict[int, RunRecord]] = {}
        if not self.runs_root.exists():
            return out
        for gdir in sorted(p for p in self.runs_root.iterdir() if p.is_dir()):
            out[gdir.name] = {}
            for f in sorted(gdir.glob("*.json")):
                try:
                    rec = self._read_record(f, alive)
                    out[gdir.name][rec.issue] = rec
                except (json.JSONDecodeError, KeyError, ValueError, OSError) as e:
                    self.errors.append(f"{f}: {e.__class__.__name__}: {e}")
                    continue
        return out

    def load_spend(self, milestone: int) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        if not self.runs_root.exists():
            return out
        for gdir in sorted(p for p in self.runs_root.iterdir() if p.is_dir()):
            counts: dict[str, int] = {}
            files = list(gdir.glob("*.json")) + list((gdir / "archive").glob("*.json"))
            for f in files:
                try:
                    data = json.loads(f.read_text())
                    if int(data.get("milestone", -1)) != milestone:
                        continue
                    counts[data["tier"]] = counts.get(data["tier"], 0) + 1
                except (json.JSONDecodeError, KeyError, ValueError, OSError) as e:
                    self.errors.append(f"{f}: {e.__class__.__name__}: {e}")
                    continue
            out[gdir.name] = counts
        return out


# ---- digests ---------------------------------------------------------------

def board_digest(board: Board) -> dict:
    return {
        "phase": board.phase,
        "halted": board.halted,
        "issues": {str(n): {"status": i.status, "assignees": list(i.assignees), "labels": sorted(i.labels)}
                   for n, i in sorted(board.issues.items())},
        "pulls": {str(n): {"head": p.head_sha, "approvals": sorted(p.approvals_on_head()),
                           "changes_requested": sorted(p.changes_requested_on_head()), "checks": p.checks, "state": p.state}
                  for n, p in sorted(board.pulls.items())},
        "runs": {f"{g}/#{n}": ("running" if r.alive else "collected" if r.collected else "finished")
                 for g, recs in sorted(board.runs.items()) for n, r in sorted(recs.items())},
        "last_comment_id": max((c.id for c in board.comments), default=0),
        "open_challenges": sorted(board.open_challenges),
    }


CHANGE_KINDS = ("snapshot", "phase", "halted", "issue", "pr", "run", "comments", "challenges")


@dataclass(frozen=True)
class Change:
    """One difference between two board digests, with a machine-readable kind and a human line."""
    kind: str
    ref: str
    before: object
    after: object
    text: str


def diff_changes(old: dict | None, new: dict) -> list[Change]:
    if old is None:
        return [Change("snapshot", "", None, None, "initial snapshot")]
    out: list[Change] = []
    for key in ("phase", "halted"):
        if old.get(key) != new.get(key):
            out.append(Change(key, "", old.get(key), new.get(key), f"{key}: {old.get(key)} -> {new.get(key)}"))
    for n in sorted(set(old.get("issues", {})) | set(new.get("issues", {})), key=int):
        a, b = old.get("issues", {}).get(n), new.get("issues", {}).get(n)
        ref = f"#{n}"
        if a is None:
            out.append(Change("issue", ref, None, b["status"], f"issue #{n}: new ({b['status']})"))
        elif b is None:
            out.append(Change("issue", ref, a["status"], None, f"issue #{n}: gone"))
        elif a["status"] != b["status"]:
            out.append(Change("issue", ref, a["status"], b["status"], f"issue #{n}: {a['status']} -> {b['status']}"))
        elif a != b:
            out.append(Change("issue", ref, a, b, f"issue #{n}: labels or assignees changed"))
    for n in sorted(set(old.get("pulls", {})) | set(new.get("pulls", {})), key=int):
        a, b = old.get("pulls", {}).get(n), new.get("pulls", {}).get(n)
        ref = f"pr #{n}"
        if a is None:
            out.append(Change("pr", ref, None, b, f"pr #{n}: new"))
        elif b is None:
            out.append(Change("pr", ref, a, None, f"pr #{n}: gone"))
        else:
            if a["head"] != b["head"]:
                out.append(Change("pr", ref, a["head"], b["head"], f"pr #{n}: new head {b['head']}"))
            if a["approvals"] != b["approvals"]:
                out.append(Change("pr", ref, a["approvals"], b["approvals"], f"pr #{n}: approvals {a['approvals']} -> {b['approvals']}"))
            if a["changes_requested"] != b["changes_requested"]:
                out.append(Change("pr", ref, a["changes_requested"], b["changes_requested"],
                                  f"pr #{n}: changes_requested {a['changes_requested']} -> {b['changes_requested']}"))
            if a["checks"] != b["checks"]:
                out.append(Change("pr", ref, a["checks"], b["checks"], f"pr #{n}: checks {a['checks']} -> {b['checks']}"))
            if a["state"] != b["state"]:
                out.append(Change("pr", ref, a["state"], b["state"], f"pr #{n}: {a['state']} -> {b['state']}"))
    for k in sorted(set(old.get("runs", {})) | set(new.get("runs", {}))):
        a, b = old.get("runs", {}).get(k), new.get("runs", {}).get(k)
        if a != b:
            out.append(Change("run", k, a, b, f"run {k}: {a or 'none'} -> {b or 'none'}"))
    if old.get("last_comment_id") != new.get("last_comment_id"):
        out.append(Change("comments", "", old.get("last_comment_id"), new.get("last_comment_id"),
                          f"comments: new up to #{new.get('last_comment_id')}"))
    if old.get("open_challenges") != new.get("open_challenges"):
        out.append(Change("challenges", "", old.get("open_challenges"), new.get("open_challenges"),
                          f"open_challenges: {old.get('open_challenges')} -> {new.get('open_challenges')}"))
    return out


def filter_changes(changes: list[Change], kinds: Iterable[str] | None) -> list[Change]:
    """Keep only the requested kinds; an initial snapshot always counts."""
    if not kinds:
        return list(changes)
    wanted = set(kinds) | {"snapshot"}
    return [c for c in changes if c.kind in wanted]


def diff_digest(old: dict | None, new: dict) -> list[str]:
    return [c.text for c in diff_changes(old, new)]
