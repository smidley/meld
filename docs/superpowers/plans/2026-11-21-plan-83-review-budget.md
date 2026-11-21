# Plan 83 — a review or an audit is held to the budget (2026-11-21)

**Goal.** A group cannot start a review or an audit run once the tier that run is recorded under is spent.

**From the spec.** Section 17 (budgets per milestone by model tier), section 13 (review and audit dispatch).

**What the livedrop run showed.** `[budget] large = 20`. By 12:00 UTC on its first day codex had run 25 large-tier reviews and audits over seven pull requests. Its orchestrator posted `BUDGET` at 18/20, 19/20, 20/20 and 24/20, then `NEEDS-HUMAN codex-budget`: "please correct the Codex PM dispatch controls so no additional large-tier runs start beyond the configured cap". The PM had no control to correct: `rules._claim` is the only rule that reads `board.spend`, and `dispatch._dispatch_pr` launches every review and audit on `group.orchestrator.model` (gpt-6.1-sol, large) after `pr_refusal`, which never looked at the budget. `meld status` showed `spent.codex.large = 25`, `cap.large = 20`, and `review` as legal.

## Decisions (each reversible)

1. **The same cap, the same sentence.** `rules._tier_spent` is the one budget sentence; `_claim` and `_run_slot` both use it, and `_run_slot` adds the model and the cap a person raises.
2. **The tier is the one `dispatch` records.** `models.tier_of(group.cli, group.orchestrator.model) or "medium"`, exactly as `_dispatch_pr` writes the run record.
3. **The catalogue is an optional argument.** `pr_refusal`, `pr_verb_rules` and `legal_pr_verbs` take `models=None`; `status`, `dispatch` and `actions` pass it. A caller asking about merge or a verdict leaves it out, and the many existing tests keep their calls.
4. **Nearer caps answer first.** Concurrency and an existing run are named before the budget, as they were.
5. **Not taken:** running a review on the PM's model or another cheaper tier when the orchestrator's is spent. It changes who reviews what and needs a rule for which work may be reviewed by a smaller model.

## Tasks

1. `rules.py` (`_tier_spent`, `_run_slot`, the `models` argument), `status.py`, `actions.py`, `dispatch.py`, `prompts/pm.md`, `tests/test_rules_prs.py`.
2. Spec section 17, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green. `meld status` as livedrop's codex PM reads without error from this branch (its tier is under the cap the owner raised to 60, so `review` stays legal).

## Carried

- A cheaper tier for routine reviews.
- A person's answer that lifts a `review.pingpong_cap` block; a way for a PM to discard an out-of-scope pull request; duplicate `model:`/`effort:` labels after two groups tag one issue; the supervisor of a group removed from the file.
