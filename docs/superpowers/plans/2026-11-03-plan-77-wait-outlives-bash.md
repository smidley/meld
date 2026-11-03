# Plan 77 — a wait outlives the Bash tool (2026-11-03)

**Goal.** A claude role's `meld wait` runs for the timeout it asked for, so an idle board is actually cheap (plan 64).

**From the spec.** Section 15 (the Claude adapter), section 17 (launches and resumes).

**What the linkcheck run showed.** With the plan waiting for the owner's approval, claude's PM launched 31 times in 77 minutes ($5.59). Each launch ran `meld tick` then `meld wait --filter ... --timeout 280`, and ended "Still no change" after about 133 s. The transcripts' Bash heartbeats stop at `elapsed_time_seconds: 120` in every one of 19 logs; no claude wait ever returned `timed_out: true` and no `idle_until` was ever written. Claude Code's Bash tool default timeout is 120 s (`BASH_DEFAULT_TIMEOUT_MS`), the cap 600 s (`BASH_MAX_TIMEOUT_MS`); the adapter told roles the ceiling was ten minutes (`BASH_TOOL_CEILING_SECONDS`) but never set the variables, and the role did not pass a per-call timeout. Plan 39's relaunch floor then set the cadence at exactly one wait timeout. The maze's claude PM "relaunching every three minutes" in plans 63 and 64 was probably the same mechanism.

## Decisions (each reversible)

1. **Set both variables to the ceiling** in `ClaudeAdapter.env_for`. Default and cap are the same number, so a role that passes no timeout gets the ceiling, and one that asks for more is capped where the adapter already said.
2. **Not a prompt change.** Telling the role to pass `timeout=` per call depends on the model remembering; the environment does not.

## Tasks

1. `adapters/claude.py`; `tests/test_adapter_claude.py`.
2. Spec section 15, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green; after the claude supervisor is restarted on linkcheck, a PM log should show heartbeats past 120 s, `timed_out: true` with `idle_until`, and the next launch reason `idle` about fifteen minutes later.

## Carried

- The other adapters' command ceilings (`command_timeout_ceiling_seconds`) deserve the same check against what their CLIs actually enforce.
