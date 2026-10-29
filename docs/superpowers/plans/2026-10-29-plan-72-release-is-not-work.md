# Plan 72 — releasing what you hold is not work (2026-10-29)

**Goal.** A role holding an issue it cannot move reads as idle.

**From the spec.** Section 12 (`meld status` `idle`, plan 64).

**What the maze run showed.** With #4 waiting on a person, claude's PM held it and `release` was legal on it, as it always is for the holder; `idle` therefore read false, the idle sleep never engaged, and the PM relaunched every two to three minutes at $4 to $8 an hour with nothing to do.

## Decisions (each reversible)

1. **`release` is passive** in the idle computation, beside `tag`, `remember`, `challenge`, `propose`, `draft` and `ask`.

## Tasks

1. `status.py`; `tests/test_status.py`.
2. Spec section 12, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green.

## Carried

- None.
