# Plan 66 — one open ask per problem (2026-10-29)

**Goal.** A person gets one card for one problem, whatever ref the asker chooses.

**From the spec.** Section 8 (one open ask per ref, plan 41; quiet refusal, plan 63).

**What the maze run showed.** With `NEEDS-HUMAN #4` open and its repeat refused, claude's PM posted the same words as `NEEDS-HUMAN ask-2`. The page then showed two questions for one missing permission.

## Decisions (each reversible)

1. **`rules.ask_rule` reads the text.** A group's or PM's `NEEDS-HUMAN` whose ref has no open ask, but whose text names an issue or PR (`#N`) that has one, is refused naming that ask; the refusal is quiet (plan 63). A human's own is never refused.
2. **The PM prompt says it**: one open ask per ref and one per problem.

## Tasks

1. `rules.py`, `posting.py`, `prompts/pm.md`; test in `test_posting.py`.
2. Spec section 8, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green.

## Carried

- The maze's `ask-2` stays open until a person answers it or claude's PM withdraws it.
