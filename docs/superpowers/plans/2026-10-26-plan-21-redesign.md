# Plan 21 — the page, laid out (2026-10-26)

**Goal.** The owner, after plans 17–20: "the UI is not intuitive. It's very text heavy and it isn't clear what I should be looking at." Everything on the page was the same small monospace text, the machine stats sat at the top, three text lists competed with the conversation, and the raw transcripts took the most room.

**From the spec.** Section 22 (the viewer).

## Decisions (each reversible)

1. **A hierarchy, top to bottom.** The header says what is happening (repo, milestone, a phase pill, the headline sentence, the groups as chips in their colours with the supervisor state in plain words); the decisions say what needs you; the middle, which takes the room, is the conversation beside the work; a tab row at the bottom opens a drawer for the rest — transcripts, pull requests, agreements, the action bar, configuration — one tab at a time, and the machine stats (spend, disk, logs, feed) sit demoted beside the tabs.
2. **The work is four lanes.** To do (planned, ready, unlabeled), in progress (in progress, blocked, needs a person), in review, done. An issue is a card: number and title, its holder in that group's colour, its flags, its verb buttons; the refused rules on hover. *Not taken:* a table, which is what the text list already was.
3. **Prose is proportional; transcripts and inputs are monospace.** The page's default font is the system UI font; the transcript columns, the bar's inputs and the configuration inputs keep the monospace they need.
4. **Every id and hook survives.** The restructure keeps every element id the script and the tests name, so no route, poll or form changed; the one casualty was the board panel's click delegate, which is now the document's and opens the "Act as the human" tab so the filled bar is in view.

## Tasks

1. `app.html` rewritten; `app.css` rewritten; `app.js`: `LANES`, `issueCard`, the agent chips, `openTab`, the delegate.
2. `tests/test_viewer.py`; README, spec section 22, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings. Served against `smidley/meld-playground` and opened in a browser: the header, a sample decision card, the conversation and the lanes with the finished milestone's seven issues under Done, the drawer's tabs, a verb button filling the bar.

## Carried

- The lanes are fixed at four; a product with a `planned` phase in progress shows those under "to do".
- No runtime test of the page's JavaScript; the browser check is by hand.
