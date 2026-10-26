# Plan 10 — demo readiness (2026-10-26)

**Goal.** The two things a demonstration runs first — `meld doctor` before the run and `meld watch` during it — must tell the truth and read well. The first live run (2026-09-11) began with a logged-out `claude` that doctor reported green, and the viewer's pull-request rows have carried no title since plan 8c. Both are what an audience sees first.

**From the spec.** Section 20 step 2 (doctor's contract), section 21 "Carried into plan 6" items 2 (CLI login status) and 14 (three-outcome probes), and section 22 (the viewer). Spec section 3's decisions are not touched.

## Decisions (each reversible)

1. **The CLI's own words are the login oracle, never the exit code alone.** `Adapter.login_argv()` names the command (`claude auth status --json`, `codex login status`) and `Adapter.login_status(stdout, stderr, returncode)` reads it: the `loggedIn` key for Claude Code, a first line of `Logged in using ...` or `Not logged in` for Codex. Anything else is `None`. *Not taken:* trust exit 0. *Cost if wrong:* a CLI that changes its wording reads NOT MEASURED until the adapter is taught the new text — visible, and the fix text says to run the command by hand.
2. **NOT MEASURED is a field, not a third `ok` value.** `Check.measured` (default `True`) is `False` when a check could not run or could not read its answer; `summary` lists those under `not_measured` and keeps `warnings` for a measured condition that does not block. `summary.ok` still means "nothing failed", so exit codes do not change. *Not taken:* exit 1 on NOT MEASURED, which would block an operator on a probe Meld itself cannot run. *Cost if wrong:* a green doctor with a non-empty `not_measured` has not verified those checks, and the operator has to read the key.
3. **A CLI that is not installed gets no login check.** `cli:<g>` has already failed; a second failure for the same cause says nothing new.
4. **The board rows say what the API already knew.** A pull request's title (`render_status` now carries `p.title`; `meld status` gains the field too), a non-green check (`checks pending`/`checks failure`, the usual reason a merge button is missing), `worker <name>` on an issue instead of a bare word, and `state_errors` beside the needs-human lines. A board identical to the one already drawn is not rebuilt, so most polls can no longer swallow a click or drop focus. *Not taken:* diffing rows in place, which would keep focus across a real change too.

## Tasks

1. Adapter protocol (`adapters/base.py`), the two adapters, `tests/fakeadapter.py` (`login: bool | None`); tests in `test_adapter_claude.py` and `test_adapter_codex.py`.
2. `doctor._login_check`, `Check.measured`, `_run` shared by `_version`/`_help_text`, `summary.not_measured`; the pre-existing skipped/cannot-read outcomes marked `measured=False`; tests in `test_doctor.py`.
3. `status.py` (`title` on pulls), `viewer/static/app.js` (title, checks, worker label, state errors, no-op skip), `app.css` (`fit-content(9rem)` ref column so `plan-add-1` is not clipped).
4. Docs: spec §20 step 2 and §21 items 2 and 14; README's Doctor section; `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings. `meld doctor` run read-only against `smidley/meld-playground` (all checks pass, `cli-login:claude` and `cli-login:codex` included). `meld watch` against the same clone, opened in a browser: titles on pull-request rows, agreement refs unclipped.

## Carried

- `summary.ok` does not go red on `not_measured` (decision 2).
- The no-op skip serialises the board on every poll to compare it; a focused control still loses focus when the board does change.
- No transcript of either CLI logged *out* has been captured; the `Not logged in` and `"loggedIn": false` shapes come from the CLIs' documentation and the logged-in shapes observed on this machine.
