"""Block until the board changes or a timeout elapses."""
from __future__ import annotations

import time

from moot.github import RateLimited
from moot.state import diff_digest
from moot.status import build_status

_MAX_BACKOFF = 300.0


def _nap(sleep, clock, deadline: float, want: float) -> None:
    """Sleep for `want` seconds, but never past the deadline (and never negative)."""
    sleep(max(0.0, min(want, deadline - clock())))


def wait_for_change(ctx, timeout: float, interval: float, *, sleep=time.sleep, clock=time.monotonic) -> dict:
    deadline = clock() + timeout
    prev = ctx.store.load_snapshot()
    backoff = float(interval)
    while True:
        try:
            data, _ = build_status(ctx)
        except RateLimited:
            if clock() >= deadline:
                return {"timed_out": True, "rate_limited": True, "changes": []}
            _nap(sleep, clock, deadline, min(backoff, _MAX_BACKOFF))
            backoff = min(backoff * 2, _MAX_BACKOFF)
            continue
        digest = data["digest"]
        changes = diff_digest(prev, digest)
        if changes:
            ctx.store.save_snapshot(digest)
            data.update({"changes": changes, "timed_out": False, "rate_limited": False})
            return data
        if clock() >= deadline:
            data.update({"changes": [], "timed_out": True, "rate_limited": False})
            return data
        _nap(sleep, clock, deadline, interval)
