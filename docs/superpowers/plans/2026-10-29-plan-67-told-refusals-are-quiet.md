# Plan 67 — a refusal the board already stated is quiet (2026-10-29)

**Goal.** Running a verb `meld status` listed as refused costs the party one refusal and nobody else anything.

**From the spec.** Section 14 (`PROTOCOL-VIOLATION` and its throttle), section 12 (`meld status`'s `refused` table).

**What the maze run showed.** While #4 waited on a person, claude's PM ran, launch after launch, `dispatch 20 --kind review` on its own group's PR, `dispatch 17` on a ready issue and `claim 17` on an issue that waits for #4. Every one of them was in its own `meld status` output under `refused`, with the reason. Each attempt posted a `PROTOCOL-VIOLATION` (the throttle only stops an exact repeat) and claude's orchestrator answered each with a `PING`.

## Decisions (each reversible)

1. **`actions.refuse` checks the table.** For a numeric target on the board, the refusal is compared with `issue_verb_rules` or `pr_verb_rules` for this party (a dispatch refusal's `cannot review: <rule>` counts by its suffix); a match posts nothing. A refusal the table could not have stated, or a target not on the board, still posts.
2. **The preamble says it**: a verb under `refused` will be refused again for the reason given; when `idle` is true, `meld wait`.

## Tasks

1. `actions.py`, `prompts/_preamble.md`; tests in `test_actions.py` (the plan 1 claim test now expects no post).
2. Spec section 14, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green.

## Carried

- Running roles keep their prompt until relaunch.
