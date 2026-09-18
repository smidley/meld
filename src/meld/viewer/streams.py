"""Which transcripts the viewer can show, and one of them as a stream of server-sent events (spec section 22).

A stream is a log file under `$MELD_HOME/logs`, named by its path there. Discovery pairs each one with the record
of the process that wrote it, so the page can say whether the writer is alive, dead, or unknown; the event stream
is plan 8a's `Tail` framed as SSE, replaying from the `seq` a client last saw and ending only when the writer is
confirmed gone.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import asdict
from pathlib import Path

from meld.config import Config
from meld.prompts import ROLES
from meld.runners import read_record, role_run_path
from meld.state import StateStore, liveness, pid_alive, proc_start_of
from meld.transcript import Event, Tail, newest_log, sweep_logs

#: A transcript ending in one of these needs no synthesized end: the CLI said how it ended.
TERMINAL_KINDS = ("run_finished", "usage_limit", "cli_error")
#: Idle polls between writer checks (and heartbeats): the check may run `ps`, and six seconds is prompt enough.
CHECK_EVERY = 6
END = b"event: end\ndata: {}\n\n"
PING = b": ping\n\n"


def stream_id(logs_root: Path, path: Path) -> str:
    """`<group>/<file>.log`: a log's name to the page, and the only form `stream_path` accepts back."""
    return path.relative_to(logs_root).as_posix()


def stream_path(logs_root: Path, stream: str) -> Path | None:
    """The log a stream id names, or None for anything that is not `<group>/<file>.log` under `logs_root`.

    Two components, a `.log` suffix, nothing empty or dotted, and the file must exist: a request cannot name a
    file outside the logs directory, because no id resolves to one.
    """
    parts = stream.split("/")
    if len(parts) != 2 or not parts[1].endswith(".log") or any(p in ("", ".", "..") for p in parts):
        return None
    path = logs_root / parts[0] / parts[1]
    try:
        # A name a filesystem call chokes on (ENAMETOOLONG) or a symlink that resolves outside `logs_root`
        # (`logs/claude/evil.log -> ../../secret`) must answer None like any other bad id, not raise or escape.
        return path if path.is_file() and path.resolve().is_relative_to(logs_root.resolve()) else None
    except OSError:
        return None


def _role_record(home: Path, group: str, role: str) -> tuple[dict | None, Path | None]:
    """A role's recorded current launch and the log it names -- the log only when it still exists."""
    record = read_record(role_run_path(home, group, role))
    log = record.get("log_path") if record else None
    path = Path(log) if isinstance(log, str) and log else None
    return record, (path if path is not None and path.is_file() else None)


def _worker_records(home: Path) -> Iterator[tuple[dict, object]]:
    # `alive` is a no-op here: liveness is answered by `state.liveness`, not by this probe, and load_runs would
    # otherwise signal every recorded pid just to fill a field nothing below reads.
    for group_runs in StateStore(home, None).load_runs(alive=lambda pid: True).values():
        for rec in group_runs.values():
            yield {"pid": rec.pid, "started_at": rec.started_at, "proc_start": rec.proc_start}, rec


def record_for(cfg: Config, home: Path, path: Path) -> dict | None:
    """The record of the process writing `path`: a role's current launch, or a worker's run record, else None."""
    for group in cfg.groups:
        for role in ROLES:
            record, log = _role_record(home, group, role)
            if record is not None and log == path:
                return record
    for record, rec in _worker_records(home):
        if rec.log_path == str(path):
            return record
    return None


def discover(cfg: Config, home: Path, *, alive: Callable[[int], bool] = pid_alive,
             start_of: Callable[[int], str] = proc_start_of) -> list[dict]:
    """Every transcript worth a column: each configured role's recorded launch (else its newest log, `unknown`),
    then every worker run still on record. Also the viewer's turn of the log cap: `sweep_logs` runs here because
    this is the call the page polls (spec 22)."""
    logs_root = home / "logs"
    sweep_logs(logs_root, cfg.logs_max_bytes_per_run)
    out: list[dict] = []

    def entry(path: Path, *, group: str, kind: str, role: str | None, issue: int | None, record: dict | None) -> None:
        try:
            sid = stream_id(logs_root, path)
        except ValueError:
            return                                      # a log recorded outside logs/ has no id, so no stream
        out.append({"id": sid, "group": group, "kind": kind, "role": role, "issue": issue, "log_path": str(path),
                    "bytes": path.stat().st_size, "liveness": liveness(record, alive=alive, start_of=start_of)})

    for group in cfg.groups:
        for role in ROLES:
            record, path = _role_record(home, group, role)
            if path is None:
                record, path = None, newest_log(logs_root, f"{group}/{role}")
            if path is not None:
                entry(path, group=group, kind="role", role=role, issue=None, record=record)
    for record, rec in _worker_records(home):
        path = Path(rec.log_path)
        if path.is_file():
            entry(path, group=rec.group, kind=rec.kind, role=None, issue=rec.issue, record=record)
    return sorted(out, key=lambda s: (s["group"], s["kind"] != "role", s["role"] or "", s["issue"] or 0))


def frame(event: Event) -> bytes:
    """One SSE frame: the event's `seq` as the id (what `Last-Event-ID` carries back), its kind as the event name."""
    return f"id: {event.seq}\nevent: {event.kind}\ndata: {json.dumps(asdict(event), ensure_ascii=False)}\n\n".encode()


def events(path: Path, parse: Callable[[str, str], list[Event]], *, after: int = 0, now: Callable[[], str],
           writer: Callable[[], dict], sleep: Callable[[float], None], interval: float = 1.0,
           check_every: int = CHECK_EVERY, max_polls: int | None = None) -> Iterator[bytes]:
    """One transcript as SSE frames, from `after` onwards, until its writer is confirmed gone.

    Resume is by count: a fresh `Tail` stamps the file's events 1..n, so the client gets `after+1..n`. A client
    whose `after` exceeds n was reading a log that has since been trimmed; it is told so with one `log_truncated`
    frame and then the whole file. Then the log is followed. Every `check_every` idle polls the writer is asked
    after (`writer()` is `state.liveness` for its record, which may run `ps`): `dead` flushes the unterminated
    last line, synthesizes the terminal event a Codex transcript never writes (spec 22's table leaves that to the
    reader), and ends the stream; anything else sends a comment heartbeat and keeps following.
    """
    tail = Tail(path)
    replay = tail.read(parse, now())
    if after > len(replay):
        yield frame(Event(seq=0, at=now(), kind="log_truncated",
                          text=f"{path.name} was trimmed since this page last read it; showing it from the start"))
        after = 0
    last_kind = replay[-1].kind if replay else None
    for ev in replay[after:]:
        yield frame(ev)
    idle = polls = 0
    while max_polls is None or polls < max_polls:
        polls += 1
        fresh = tail.read(parse, now())
        for ev in fresh:
            last_kind = ev.kind
            yield frame(ev)
        if fresh:
            idle = 0
        else:
            idle += 1
            if idle % check_every == 0:
                if writer().get("state") == "dead":
                    for ev in tail.flush(parse, now()):
                        last_kind = ev.kind
                        yield frame(ev)
                    if last_kind not in TERMINAL_KINDS:
                        yield frame(Event(seq=tail.seq + 1, at=now(), kind="run_finished",
                                          text="process exited; the transcript ends without a terminal event",
                                          meta={"synthesized": True}))
                    yield END
                    return
                yield PING
        sleep(interval)
