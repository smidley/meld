# Plan 60 — a person's answer lifts the attempt cap (2026-10-28)

**Goal.** When the attempt budget stops an issue and asks a person, the person's answer is enough to go again.

**From the spec.** Section 9 (the attempt budget, plan 55), section 8 (`NEEDS-HUMAN` and its answers).

**What the maze run showed.** #4 reached `budget.attempts_per_issue` on three builds that all ran before #3 merged (plan 56 did not exist yet), claude's PM posted `NEEDS-HUMAN #4` as told, and nothing a person could post or press made `dispatch` legal again: the cap read the last build record and the count never reset.

## Decisions (each reversible)

1. **The answer is the decision.** `Board.answered_since(issue, at)`: a listed human's `RESOLVED`/`PROVIDED #issue` newer than `at`. `rules._dispatch` skips the cap when the answer is newer than the capped build's end (or start, if it never ended); `dispatch` starts the next build's `attempt` at 1 in that case. An answer from before the build does not count.
2. **Nothing new to post.** The ask the PM already posts is answered the way every ask is: a reply on the page, which posts `RESOLVED`.

## Tasks

1. `board.py`, `rules.py`, `dispatch.py`; tests in `test_rules_issues.py` and `test_dispatch.py`.
2. Spec section 9, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green.

## Carried

- The three premature builds still show on #4's task view; the record is history, not a defect.
