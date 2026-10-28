# Plan 51 — questions for the person are a conversation (2026-10-28)

**Goal.** When an agent needs the person, the page asks the way a person would be asked: a message, and a box to reply in.

**From the spec.** Section 22 (the page), section 14 (needs-human).

**What the owner said.** "If there's a question that needs to be answered by a human, it needs to be brought up in chat window form. Not the boxes that have Provided or Resolved buttons. That is confusing."

## Decisions (each reversible)

1. **One typed reply per question.** A needs-human ask is answered with `RESOLVED` and the typed text; a disagreement or a budget alert with a `DIRECTIVE`. `PROVIDED` stays available under Act as the human; the agents read the words either way and both unlock the issue. *Not taken:* keeping two send verbs on the box, which is the choice the owner found confusing.
2. **Buttons only where the answer is one word.** "Approve the plan", "Acknowledge", "Approve as proposed" are chips in the bubble; a challenge ruling takes the typed text and two ways to send it (upheld, dismissed).
3. **One reply box, the newest question selected.** Clicking a bubble selects it; the box says who it is replying to and how many are open; Enter sends when there is one way to send.
4. **The server says who asks and what a reply posts** (`from`, `reply`, `hint`), so the page decides nothing about verbs.

## Tasks

1. `chat.py` (`decisions`: `from`, `reply`, `hint`, fewer `choices`), `viewer/static/app.html` (`#asks`, `#reply`, the `ask` template), `app.css`, `app.js` (`renderDecisions`, `renderReplyBox`, `sendReply`); `tests/test_chat.py`, `tests/test_viewer.py`.
2. Spec section 22, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings; checked by eye with three open questions injected on the playground board.

## Carried

- Answered questions leave the conversation at once; the person's own replies are in the thread below, not under the question.
