# Plan 71 — `remember 4` is `remember #4` (2026-10-29)

**Goal.** Every verb reads a bare number as the issue or PR it names.

**From the spec.** Section 8 (`9` and `#9` are one ref), section 14 (`remember`).

**What the maze run showed.** Claude's orchestrator ran `meld remember 4` after the owner answered #4 and was refused with "4 is not an issue or PR on the board", a violation post and a retry.

## Decisions (each reversible)

1. **`remember_rule` and the `remember` command canonicalise a bare number** to `#<n>` before anything else.

## Tasks

1. `rules.py`, `cli.py`; `tests/test_rules_agreements.py`.
2. Spec section 14, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green.

## Carried

- None.
