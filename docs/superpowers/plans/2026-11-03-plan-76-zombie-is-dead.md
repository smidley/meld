# Plan 76 — a zombie is dead (2026-11-03)

**Goal.** `meld stop` tells the truth and `meld run` never refuses to start a group whose supervisor has exited.

**From the spec.** Section 15 (runners), the PID-reuse caveat under known gaps.

**What the linkcheck run showed.** The owner ran `meld stop codex` to pick up plan 75. The supervisor exited on SIGTERM, but it had been started by the wizard inside the `meld start` process, whose only `waitpid` on it is the one `pid_alive` makes when the page is polled; nobody polled, so the supervisor sat defunct. `stop` saw `kill(pid, 0)` succeed, escalated to SIGKILL, and printed `stopped: false`; `meld run codex --detach` refused "a supervisor for codex is already running". Polling the page reaped it and the restart went through.

## Decisions (each reversible)

1. **Read the state column.** `pid_alive` runs `ps -o stat= -p <pid>` after `kill(pid, 0)` and returns False on `Z`. `ps` is already the liveness check's source for start times, so this adds no new dependency; without `ps` the answer stays "alive", as before.
2. **Not a double fork.** Re-parenting the detached supervisor to launchd would also avoid the zombie, but `detach_supervisor` returns the child's pid and the wizard's tests read it; the ps column fixes every reader at once.

## Tasks

1. `state.py`; `tests/test_state.py`.
2. Spec known-gaps line, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green; the next `meld stop <group>` on linkcheck should print `stopped: true, how: term` and `meld run <group> --detach` should start.

## Carried

- The `meld start` process still holds the zombie until it polls. Harmless now that every reader calls it dead; a `waitpid(-1, WNOHANG)` on the viewer's poll loop would clear it sooner.
