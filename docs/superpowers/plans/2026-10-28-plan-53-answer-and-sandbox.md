# Plan 53 — a retry hears the answer, and a worker knows where it runs (2026-10-28)

**Goal.** A worker that stops for a person never asks the same thing twice, and a Codex worker never asks a person for a browser.

**From the spec.** Section 10 (briefs), section 9 (the rubric), section 14 (needs-human).

**What happened.** On the maze run the codex worker on #3 finished the site but reported `blocked`, asking a person to run the browser checks its sandbox cannot (Chromium dies at MachPortRendezvous on macOS, WebKit exits 134). The ask reached the owner; a retry would have read nothing of the answer and blocked again.

## Decisions (each reversible)

1. **The brief carries the answer.** `dispatch.person_answer` pairs the newest `NEEDS-HUMAN #n` with the comment that closed it (a listed human's, or the asking group's own withdrawal); `brief.person_answered` renders `## The person answered` with both, and tells the worker to act on it or finish `failed`.
2. **The adapter says what its worker must know about its sandbox.** `Adapter.worker_notes()`; Codex's says a browser or GUI cannot launch, verify what you can, finish `done` with browser-only checks listed as unverified, never `blocked` for a browser; Claude's says nothing. The brief renders it as `## Where you run`.
3. **The rubric routes browser work.** The orchestrator prompt lists "needs a browser" among the tagging inputs: to a group whose workers can launch one, or the reviewer does those checks.

## Tasks

1. `adapters/base.py` (the hook), `adapters/codex.py`, `adapters/claude.py`, `brief.py`, `dispatch.py` (`person_answer`, `worker_notes`), `prompts/orchestrator.md`; `tests/test_brief.py`, `tests/test_dispatch.py`.
2. Spec section 10, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings.

## Carried

- A reviewer is not told to run the browser checks the builder listed as unverified; the review prompt's "verify claims in the PR body" covers it only by implication.
