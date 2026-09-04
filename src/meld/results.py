"""Verdict files: what a collected review or audit run produced, kept until `meld review`/`meld audit` consumes it.

Layout under $MELD_HOME/results/<group>/:
  <kind>-<n>.json                      the unconsumed verdict for PR n (at most one)
  superseded/<kind>-<n>-<stamp>.json   an unconsumed verdict displaced by a newer collect
  submitted/<kind>-<n>-<head7>-<stamp>.json   a consumed verdict plus what meld did with it
"""
from __future__ import annotations

import json
from pathlib import Path

from meld.atomic import write_atomic


class ResultsError(ValueError):
    pass


def results_dir(home: Path, group: str) -> Path:
    return home / "results" / group


def result_path(home: Path, group: str, kind: str, number: int) -> Path:
    return results_dir(home, group) / f"{kind}-{number}.json"


def _stamp(now_iso: str) -> str:
    return now_iso.replace("-", "").replace(":", "")


def _unique(path: Path) -> Path:
    candidate, k = path, 0
    while candidate.exists():
        k += 1
        candidate = path.with_name(f"{path.stem}-{k}{path.suffix}")
    return candidate


def store_result(home: Path, group: str, kind: str, number: int, payload: dict, now_iso: str) -> Path:
    path = result_path(home, group, kind, number)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():  # never overwrite a verdict nobody has acted on
        aside = _unique(path.parent / "superseded" / f"{kind}-{number}-{_stamp(now_iso)}.json")
        aside.parent.mkdir(parents=True, exist_ok=True)
        path.rename(aside)
    write_atomic(path, json.dumps(payload, indent=2))
    return path


def load_result(home: Path, group: str, kind: str, number: int) -> dict | None:
    path = result_path(home, group, kind, number)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ResultsError(f"{path}: malformed verdict file: {e}") from e
    if not isinstance(data, dict):
        raise ResultsError(f"{path}: verdict file must hold a JSON object")
    return data


def mark_submitted(home: Path, group: str, kind: str, number: int, extra: dict, now_iso: str) -> Path:
    data = load_result(home, group, kind, number)
    if data is None:
        raise ResultsError(f"no collected {kind} result for #{number} in {group}")
    data = {**data, **extra, "submitted_at": now_iso}
    head7 = (data.get("head_sha") or "nohead")[:7]
    target = _unique(results_dir(home, group) / "submitted" / f"{kind}-{number}-{head7}-{_stamp(now_iso)}.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(target, json.dumps(data, indent=2))
    result_path(home, group, kind, number).unlink()
    return target
