# Plan 35 — product context (2026-10-26)

**Goal.** A product that already has documentation or partial work can hand it to Meld, so the AIs build on it rather than from nothing.

**From the spec.** Section 18 (`.meld/` files), section 10 (briefs), section 9 (roles), section 20 (the wizard).

**What the owner said.** "We need to be able to optionally feed additional context into the app. For example, if I have a project that was being worked on with documentation, or partial work, I need to be able to give that to meld in some way so that it can use it for context to build on."

## Decisions (each reversible)

1. **One file, `.meld/CONTEXT.md`.** Free text: what exists, where it stands, constraints, files to read first. It lives beside `STANDARDS.md`, is committed with the setup files, and is the product's to edit. *Not taken:* uploading documents into Meld; the repository already holds them, and the file points at them.
2. **Three places, from the one file.** Whole into both role prompts (planners need all of it), capped into every worker brief with a pointer to the file (a brief is already long), and as a `## Context` section under the goal on the coordination issue (the one page the human and every orchestrator read first).
3. **Never overwritten.** The wizard writes the file only when it does not exist; what the person typed on a second run is not applied over a file they may have edited.
4. **The orchestrator is told what it is for.** Its prompt reads the context and the files it names before proposing: partial work is where the plan starts, not something to redo.

## Tasks

1. `brief.py` (`CONTEXT_FILE`, `product_context`, `context_for_brief`, the brief section), `prompts.py` (the role section), `prompts/orchestrator.md`, `setup.py` (`repo_files(context)`, `ensure_coordination_issue(context)`, `setup(context)`), `firstrun.py`, `viewer/server.py`, the wizard's field; `tests/test_brief.py`, `tests/test_setup.py`, `tests/test_firstrun.py`, `tests/test_viewer.py`.
2. Spec section 18, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings.

## Carried

- A running product gains no new way to change the context mid-milestone beyond editing the file; the role prompts pick it up at their next launch and briefs at their next dispatch.
