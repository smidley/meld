# Plan 84 — the plan gate survives a label added by hand (2026-11-22)

**Goal.** A person who approves a plan the way the coordination issue told them to can still release its issues, and `meld plan approve` approves the plan that is actually on the board.

**From the spec.** Section 14 (the plan gate), section 20 (what `meld setup` writes).

**What the livedrop run showed.** M2's plan was agreed and verified at 19:44 UTC as `[joint] PLAN plan-m2`, with issues #49-#59 `meld:planned`. The owner posted "I approve M2" and, at 20:21, added `meld:plan-approved` to the coordination issue, as its last line said to. `board.phase` is that label, so the milestone read `executing`; no issue changed. Claude's orchestrator posted `NEEDS-HUMAN plan-m2` asking for `meld plan approve`. Run at 22:40, it answered "plan plan-2 refused: the plan is already approved (milestone is executing)". Two faults: `plan_rule` refuses on the phase alone, and `plan_approve` builds the ref as `plan-{milestone}` while the orchestrators had named theirs `plan-m2`. The owner released the issues with `gh issue edit`.

## Decisions (each reversible)

1. **The gate's ref is read from the board.** `rules.gate_plan_ref`: the verified [joint] PLAN on the coordination issue; `plan-<milestone>` when it is among several or when there is none.
2. **An approval is unfinished while the plan's own issues are planned.** In `executing`, `approve` is legal as long as an issue carrying `plan-ref: <ref>` is still `planned`, and flips only those; with none left it is the old refusal.
3. **The label is still added last.** Unchanged: a failed flip in `planning` leaves a retryable board.
4. **The issue says what to run.** `ensure_coordination_issue` names `meld plan approve` and says the label by hand releases nothing.
5. **Not taken:** making the label the approval (a PM's `tick` releasing the issues when a listed person adds it). The gate would then be a GitHub label any collaborator can set, with no rule deciding it.

## Tasks

1. `rules.py` (`gate_plan_ref`, `plan_rule`), `plan.py` (`plan_approve`), `setup.py` (the issue text), `cli.py` (help), `tests/test_plan.py`, `tests/test_setup.py`.
2. Spec section 14, `README.md`, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green.

## Carried

- A later milestone on a configured product has no verb; `meld setup` refuses over `.meld.toml`.
- An existing coordination issue keeps the old sentence.
