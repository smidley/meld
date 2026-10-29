# Plan 65 — a refusal names the mistake, and nobody explains it again (2026-10-29)

**Goal.** A wrong command costs one refusal, not a thread of commentary.

**From the spec.** Section 8 (posting), section 9 (dispatch).

**What the maze run showed.** Claude's PM ran `meld dispatch 17 --kind audit` on an open issue. The refusal said "PR is not on a meld/ branch of the active repo", which is false (there is no PR 17); it was posted as a `PROTOCOL-VIOLATION`, and both orchestrators then posted a `PING` explaining the dependency gate to both PMs. Four sessions woke for one mistyped kind.

## Decisions (each reversible)

1. **`dispatch --kind review|audit` on an issue number says so**: "#17 is an issue, not a pull request; `--kind audit` takes a PR number". A number that is neither keeps the old words.
2. **No party comments on a `PROTOCOL-VIOLATION`.** The orchestrator prompt adds it to the list of posts never acknowledged.

## Tasks

1. `dispatch.py`, `prompts/orchestrator.md`; test in `test_dispatch.py`.
2. Spec section 8, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green.

## Carried

- Running roles keep their prompt until relaunch.
