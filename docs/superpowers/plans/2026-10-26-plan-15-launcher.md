# Plan 15 — the launcher (2026-10-26)

**Goal.** Handoff residual 17: the only way to start Meld's supervisors in the background was a one-shot script outside the repo, and nothing in Meld could say whether one was running or stop it. A demonstration starts with "start the system".

**From the spec.** Section 3's runners paragraph, section 22's tri-state liveness and the records `run_group` already writes about itself.

## Decisions (each reversible)

1. **`meld run GROUP --detach` re-executes `python -m meld run GROUP` in its own session** with stdio on `$MELD_HOME/logs/<group>/supervisor.log`, and refuses while that group's supervisor record reads `alive`. *Not taken:* `fork()`, which on macOS is unsafe once certain frameworks are loaded; re-execution is what the outside script did and is portable.
2. **`meld stop GROUP` signals by record, never by pid file alone.** It reads the supervisor's own `supervisor.json` and the tri-state liveness check, so a reused pid is reported as dead rather than killed; SIGTERM first (the supervisor's handler stops its roles and returns), SIGKILL after `--grace`. A dead or unknown supervisor is reported with exit 0.
3. **`meld ps` is local.** `runners.processes` reads the supervisor and role records and freezes under `$MELD_HOME/state`; nothing is asked of GitHub.
4. **Restart after a reboot or a kill stays outside v1.** It is a launchd or systemd unit around `meld run GROUP`, and the README says so.
5. **The test stands a sleeping python in for the supervisor** through `cli.detach_argv`/`ALIVE`/`START_OF` seams, so no real CLI is ever launched and `ps` is never run.

## Tasks

1. `src/meld/__main__.py`; `runners.processes`; `cli.run --detach`, `cli.stop`, `cli.ps`, the seams; `tests/test_runners.py`.
2. `Taskfile.yml`: `up`, `down`, `ps`.
3. README rows and paragraph, quick-start step 6, spec section 3; `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings. The one test detaches the stand-in, checks it owns its session and its log exists, writes the record the real supervisor writes, sees the second `--detach` refused and `--once` with it refused, reads `ps` alive and unknown, stops it by SIGTERM, reads `stopped: false` with the reason on the second stop and `dead` in `ps`, and sees an unknown group refused.

## Carried

- No restart after a reboot or a kill (decision 4).
- `meld stop` on a supervisor started in the foreground works the same, but its roles' own processes are the supervisor's to stop; a SIGKILL after the grace period leaves them to their own exit.
- The viewer still computes its own liveness summary rather than calling `runners.processes`.
