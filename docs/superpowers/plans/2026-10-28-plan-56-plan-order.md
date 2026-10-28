# Plan 56 — the plan's order is enforced (2026-10-28)

**Goal.** An item the plan put after another is not worked until that one has closed.

**From the spec.** Section 20 (the plan item format), section 9 (claim and dispatch).

**What the maze run showed.** The plan said "#2, then #3, then #4" in prose. Claude's PM claimed and dispatched #4 while #3's pull request (#12) was still in review; the worker for #4 found no maze on `main` and stopped to ask a person whether #3 had merged.

## Decisions (each reversible)

1. **`after:` on the item line.** `- id: … | size: M | after: engine, site` names the items this one waits for. The orchestrator prompt tells the planners to use it instead of writing the order into prose or criteria.
2. **The issue body carries it** as `plan-after: engine, site`, next to `plan-item:` and `plan-ref:`. `Board.waits_for(issue)` resolves the ids to open issue numbers through the other issues' `plan-item:` markers; an item not on the board is not waited for.
3. **`rules.py` refuses** `claim` and `dispatch` with `waits for #31 to close first (the plan's \`after\`)` while any named issue is open. Closing is the signal, not merging, because closing is what the reviewer's merge does and what a person does when they drop an item.
4. **The page says so.** `meld status` carries `waits_for`; a ready or claimed issue's journey reads `waits for #31 to close first`.

## Tasks

1. `plan.py` (`ITEM_RE`, `PlanItem.after`, the body), `board.py` (`waits_for`), `rules.py` (`_claim`, `_dispatch`), `status.py`, `chat.py` (`journey`), `prompts/orchestrator.md`; tests in `test_plan.py`, `test_rules_issues.py`, `test_chat.py`.
2. Spec section 20, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings.

## Carried

- An issue that waits for a closed-but-reverted item is not caught; the plan treats closing as final.
- The maze's #4 was created before this plan and has no `plan-after:` line; the owner releases it by hand.
