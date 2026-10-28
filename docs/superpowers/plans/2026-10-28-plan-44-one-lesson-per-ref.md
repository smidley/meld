# Plan 44 — one lesson per ref (2026-10-28)

**Goal.** A lesson is recorded once per item and improved by supersession, not re-recorded.

**From the spec.** Section 8 (`remember`), section 14 (memory files and supersession).

**What happened.** The scratch product's claude PM ran `meld remember #4` five times in one day with five different texts; each opened a memory PR the other PM had to review and merge, and `lessons.md` now has five entries on one issue.

## Decisions (each reversible)

1. **The rule reads the board.** `rules.recorded_lesson(board, ref)` is the open or merged memory PR whose marker names `lesson <ref>`; `remember_rule` refuses a second `remember` on that ref unless it supersedes, naming the earlier PR and saying how. `meld status` lists it the same way. *Not taken:* deduplicating by text (the five entries were all different) or a count cap (arbitrary).
2. **Supersession is the way to say more.** The existing `--supersedes "<heading>"` retires the earlier entry and carries the fuller one; the prompt says so.
3. **Across groups too.** The other group folds its view into a superseding entry rather than adding a parallel one.

## Tasks

1. `rules.py` (`recorded_lesson`, `remember_rule(supersedes=)`), `memory.remember`, `prompts/orchestrator.md`; `tests/test_memory.py`.
2. Spec section 8, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings.

## Carried

- The refusal cannot name the entry's heading (the board does not carry `lessons.md`); it points at `meld recall`.
