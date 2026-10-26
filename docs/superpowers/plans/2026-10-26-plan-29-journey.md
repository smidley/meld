# Plan 29 — each issue's journey (2026-10-26)

**Goal.** A person can see how the work is getting done, not only how much of it is.

**From the spec.** Section 22 (what the page may do), section 13 (the review flow), section 14 (audits).

**What the owner said.** "The UI shows that milestone 2 is being worked on, and that 2 of 3 issues are done, but it's not clear how progress is being made beyond that."

## Decisions (each reversible)

1. **Seven steps, from the status JSON alone.** claimed, built, pull request, reviewed, merged, audited, closed; `chat.journey` derives done/now/next from the issue's status and claimant, its build runs' outcomes, its pull request's approvals against the quorum, checks, merge and audits, and the review or audit runs on it. *Not taken:* a new state machine or new fields in `meld status`; the page keeps no grammar.
2. **Two sentences per issue.** What is happening now (with the running worker's minutes) and what follows (the remaining steps). Blocked, waiting on a person and under challenge say so first.
3. **One line under the bar.** Up to three open issues each get their "now"; past three, a count per step.

## Tasks

1. `chat.py` (`STEPS`, `journey`, `journeys`, `progress_line`), `viewer/server.py`; `app.js` (the `.journey` strip and `.jnow` line on every card; the board key includes journeys so a ticking minute redraws), `app.css`; `tests/test_chat.py`, `tests/test_viewer.py`.
2. Spec section 22, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings. On the live playground page: #49's strip with reviewed under way and the line naming codex's review of PR #55.
