# Plan 18 — the decisions (2026-10-26)

**Goal.** The owner's direction: the UI must be very guided — when something is up for human approval, present the person with a concise decision. The page listed what needed a person as a line in the header and left the verb, the ref and the message to be typed into a bar.

**From the spec.** Section 14 (the inbox: what needs a person), section 8's rules for `plan approve`/`ack` and the challenge verdicts, section 22 (the page acts as the human through one path).

## Decisions (each reversible)

1. **The server composes the decision; the page only draws it.** `chat.decisions(board, cfg, actor, now)` turns the inbox's items — plus the plan awaiting approval and the open challenges the human may settle — into cards: a title, a few lines of summary, and choices that are the exact `/api/action` bodies to send (`verb` the dispatch name, `action` the header verb, `ref`), flagged `needs_text` where a message is required. A choice is listed only when `rules.py` answers None for it. *Not taken:* letting the page assemble bodies from verb names, which would put a second grammar in JavaScript.
2. **One request path.** Cards post through the same `postAction` the bar uses; success refreshes the state (the card disappears when the board no longer needs the person), a refusal shows verbatim in the card.
3. **A card is rebuilt only when the set changes**, so a message being typed survives the ten-second poll.
4. **A budget alert with no ref is answered with a directive** on the next `dir-<n>`, composed server-side, because any human verb but ASK answers it (the inbox's rule).
5. **No card for an open ask** (the human's own question) — it is information, not a decision.

## Tasks

1. `chat.decisions`, `chat._lines`; `viewer/server.py` (the key in `/api/state`); `tests/test_chat.py`.
2. `app.html` (`#decisions`, the card template), `app.css`, `app.js` (`renderDecisions`, `postAction` shared with the bar); `tests/test_viewer.py`.
3. README, spec section 22, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings. The chat test drives every kind of decision and the plan awaiting approval; the viewer test reads the key and the page. Served against `smidley/meld-playground` and opened in a browser, with three sample cards rendered through the page's own `renderDecisions`: title, summary, message box, buttons, in one row under the header.

## Carried

- `meld inbox` (the CLI) keeps its own shape; the decisions are the viewer's.
- A card's refusal is shown in the card but the card stays until the next poll agrees it is gone.
- No runtime test of the page's JavaScript.
