# Plan 39 — a relaunch floor (2026-10-27)

**Goal.** A role whose CLI ends its session every turn no longer relaunches every couple of minutes.

**From the spec.** Section 17 (the supervisor's relaunch rows), section 9 (roles wait on the board with `meld wait`).

**What happened.** On the scratch product the codex orchestrator launched 649 times and the PM 388 times overnight. `codex exec` is single-turn: when the role stops talking the process exits 0, the supervisor saw a clean exit after more than a minute and relaunched at once, and the role had taken to `meld wait --timeout 45` instead of the prompt's 300. Each launch re-reads the whole prompt and the board for nothing.

## Decisions (each reversible)

1. **The floor is the wait timeout.** After a clean exit the role is relaunched no sooner than `polling.wait_timeout_seconds` after it was launched. Nothing on the board moves faster than one wait would have blocked for, so a role that exits early loses nothing by sleeping the remainder. A role that lived a full wait relaunches at once, as before. *Not taken:* a new config knob, and a backoff that grows across clean exits (a clean exit is the steady state, not a failure).
2. **`meld wait` holds the other end.** A role's `--timeout` under `ROLE_WAIT_FLOOR_SECONDS` (120, or the configured wait timeout when that is smaller) is raised to it, and the reply carries `timeout_clamped` and a note saying why. A human's timeout and a `--timeout 0` one-shot read are left alone. *Not taken:* refusing the call; the role would retry with the same number.
3. **The count is visible.** `processes()` carries each role's `launch_no` from its run record and the overview's role node reads "launch N" past the first, so the next loop of this kind shows on the page instead of in a log directory.

## Tasks

1. `runners.py` (the floor in `run_group`'s clean-exit branch, `launch_no` in `processes()`), `cli.py` (`ROLE_WAIT_FLOOR_SECONDS`, the clamp in `wait`), `viewer/static/app.js` (the role node); `tests/test_runners.py`, `tests/test_wait.py`, `tests/test_viewer.py`.
2. Spec section 17, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings.

## Carried

- The codex prompt still says "wait 300, never shorter than 120"; the clamp makes the second half true in code, the first half is still the role's choice.
- A role that ends its turn without calling `meld wait` at all (it decided there was nothing to do) sleeps the floor too, which is the intended behaviour, but the overview says only "launch N", not why the role went quiet.
