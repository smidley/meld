"""Block until the board changes or a timeout elapses."""
from __future__ import annotations

import time
from dataclasses import asdict
from typing import Iterable

from meld.github import RateLimited
from meld.state import diff_changes, filter_changes
from meld.status import build_status

_MAX_BACKOFF = 300.0


def _nap(sleep, clock, deadline: float, want: float) -> None:
    """Sleep for `want` seconds, but never past the deadline (and never negative)."""
    sleep(max(0.0, min(want, deadline - clock())))


def wait_for_change(ctx, timeout: float, interval: float, *, kinds: Iterable[str] | None = None,
                    sleep=time.sleep, clock=time.monotonic) -> dict:
    deadline = clock() + timeout
    prev = ctx.store.load_snapshot()
    backoff = float(interval)
    while True:
        try:
            data, _ = build_status(ctx)
        except RateLimited:
            if clock() >= deadline:
                return {"timed_out": True, "rate_limited": True, "changes": [], "change_records": []}
            _nap(sleep, clock, deadline, min(backoff, _MAX_BACKOFF))
            backoff = min(backoff * 2, _MAX_BACKOFF)
            continue
        digest = data["digest"]
        all_changes = diff_changes(prev, digest)
        if all_changes:
            ctx.store.save_snapshot(digest)
            prev = digest
        relevant = filter_changes(all_changes, kinds)
        if relevant:
            data.update({"changes": [c.text for c in relevant], "change_records": [asdict(c) for c in relevant],
                         "timed_out": False, "rate_limited": False})
            return data
        if clock() >= deadline:
            data.update({"changes": [], "change_records": [], "timed_out": True, "rate_limited": False})
            return data
        _nap(sleep, clock, deadline, interval)
