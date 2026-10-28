# Plan 41 — one open ask per ref (2026-10-27)

**Goal.** A person gets one card per thing that needs them, not one per party per hour.

**From the spec.** Section 8 (posting legality), section 14 (`NEEDS-HUMAN` and the human's answer), section 17.

**What happened.** On the scratch product the claude PM, the claude orchestrator and the codex orchestrator posted nine `NEEDS-HUMAN` on PR #9 and two more on `merge-conflict-9-17` over four hours while the first ask stood unanswered; the page showed nine decision cards saying the same thing.

## Decisions (each reversible)

1. **The rule is in `rules.py` and reads the board.** `Board.open_ask(ref)` is the newest `NEEDS-HUMAN` on the ref with no listed human's `PROVIDED`/`RESOLVED` after it; `rules.ask_rule` refuses a group's or PM's repeat naming that comment and its party, and `posting.post` applies it before composing. `9` and `#9` are one ref. *Not taken:* a per-party allowance (a second party's ask on the same ref says the same thing in other words).
2. **A human's own is never refused.** A person re-gates an answered issue by posting `NEEDS-HUMAN` again; that stays.
3. **The prompts say so** in one line each, so a role does not try, get refused, and try again in other words.

## Tasks

1. `board.py` (`open_ask`), `rules.py` (`ask_rule`), `posting.py`, `prompts/pm.md`, `prompts/orchestrator.md`; `tests/test_posting.py`.
2. Spec section 17, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings.

## Carried

- A free-text ref that means the same thing as a numbered one (`pr9-conflict` beside `#9`) is a different ref and is not refused; the prompts ask for `#<n>`.
