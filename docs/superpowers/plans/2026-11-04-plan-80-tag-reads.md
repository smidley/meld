# Plan 80 — `meld tag N` reads (2026-11-04)

**Goal.** Reading an issue's declarations never posts a protocol violation.

**From the spec.** Section 7 (declarations on issues), section 12 (refusals and `PROTOCOL-VIOLATION`).

**What the linkcheck run showed.** Minutes after PR #7 merged, claude's orchestrator ran `meld tag 4 2>&1 | tail -30`, then the same for #5 and #6, to confirm the scopes it had declared. `tagging.tag` refused each with "provide at least one of --worker, --model, --effort, --size, --skill, --scope, --may-delete, --held", and `actions.refuse` posted each refusal as `[claude] PROTOCOL-VIOLATION #N`, three in a row on the coordination issue. The same happened once during planning on #3.

## Decisions (each reversible)

1. **A bare `meld tag N` is a read.** It returns `{issue, read: true, worker, model, effort, size, skills, scope, may_delete, held}` from the board, before the legality check, so any party that can read the board can read (a PM included), and nothing is written or posted.
2. **The usage refusal goes.** There is no longer a way to call `tag` that sets nothing, so the message has no case left.

## Tasks

1. `tagging.py` (`declarations`, the early return); `prompts.py` `COMMANDS`; `tests/test_tagging.py`.
2. Spec section 7, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green; the next `meld tag N` an orchestrator runs on linkcheck prints JSON and the thread gains no violation.

## Carried

- `meld status` could carry the same declarations per issue so a role never needs the read at all; the PM prompt's loop would then not mention `tag` reads.
