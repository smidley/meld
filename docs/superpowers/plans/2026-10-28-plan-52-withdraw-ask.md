# Plan 52 — the group that asked withdraws a stale ask (2026-10-28)

**Goal.** A person is asked only while the ask stands.

**From the spec.** Section 14 (needs-human and the human's answer), section 8 (posting legality).

**What the owner said.** "If it's stale, the agents need to recognize that the ask isn't needed anymore, and make a note of it and resolve it themselves."

**What happened.** On `smidley/meld-test-maze` the codex PM posted `NEEDS-HUMAN #3` asking the owner to restore a headless browser service while the codex orchestrator's session was restarting; the orchestrator restored it two minutes later and claude confirmed the owner had nothing to do; the ask stayed on the owner's page until they typed an answer, because only a person could post one.

## Decisions (each reversible)

1. **`RESOLVED` from the asking group closes its own ask.** `Board.answers(c, ask)` is the one predicate: a listed human's `PROVIDED`/`RESOLVED` after the ask, or the asking group's own `RESOLVED` after a bot-posted ask. `needs_human_answered`, `open_ask` and the inbox read it. *Not taken:* any group withdrawing any ask (the asking group knows whether the need is gone; another group tells it).
2. **A person's hold is a person's to clear.** `needs_human_answered` requires every human-posted ask to have a human answer after it, whatever bots asked or withdrew since.
3. **`rules.withdraw_rule` says what a group's `RESOLVED` may touch**, and `meld post` applies it; the prompts tell both roles to withdraw the moment an ask is stale.

## Tasks

1. `board.py` (`answers`, `open_ask`, `needs_human_answered`), `chat.py` (`inbox`), `grammar.py` (`RESOLVED` for groups and PMs), `rules.py` (`withdraw_rule`), `posting.py`, `prompts/orchestrator.md`, `prompts/pm.md`; `tests/test_board.py`, `tests/test_posting.py`, `tests/test_chat.py`.
2. Spec section 14, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings.

## Carried

- Nothing detects a stale ask for the agents; the prompt tells them to look. A rule that notices, say, an ask about a service that answers again, would need to know what the ask was about.
