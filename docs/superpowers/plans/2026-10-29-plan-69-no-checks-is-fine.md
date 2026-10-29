# Plan 69 — no checks is nothing to wait for (2026-10-29)

**Goal.** A repository without CI, or with Actions switched off, can still merge through Meld.

**From the spec.** Section 13 (merge).

**What the maze run showed.** The owner's GitHub Actions quota for the month ran out. With Actions off, every new head has no check runs, `checks` reads `none`, and `rules._merge` refused with "required checks are none, not green": nothing could ever merge again.

## Decisions (each reversible)

1. **`none` passes the checks clause.** A head with no check runs has nothing to wait for. `red`, `pending` and `unreadable` still refuse; the plan 68 refusal of a rebuild while the jobs never ran stays.

## Tasks

1. `rules.py`; `tests/test_rules_prs.py`.
2. Spec section 13, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green.

## Carried

- A PR whose head already carries failed runs (the maze's PR #20 at `c3377df`) stays red until a new commit; with Actions off that commit gets no runs and merges.
