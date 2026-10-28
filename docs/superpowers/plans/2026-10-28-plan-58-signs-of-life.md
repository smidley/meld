# Plan 58 — signs of life (2026-10-28)

**Goal.** A person looking at the page can tell a quiet run from a stuck one without opening a transcript.

**From the spec.** Section 20, the overview paragraph; section 22 (transcripts).

**What the owner said.** "Is it frozen?" while every role was waiting on the board or reading; then "we need to have more visual indications of things like this so the human doesn't think it's stuck." And, on the same page: "activity dots should always be visible like before. conversation should be hidden by default."

## Decisions (each reversible)

1. **Each live process says what it is doing.** `transcript.last_event` reads the last 64 KB of a log and returns the newest message, thinking, tool call or result; `chat.activity_words` turns it into a few words (waiting for the board to change, checking the board, reading files, editing app.js, running git push, running tests, thinking, wrote a message); `chat.activity` keys them `group/role` and `group/kind/issue` with the log's last-write time. The viewer puts them in `/api/state` as `activity` and the newest as `pulse`.
2. **The page shows ages that move.** A role node reads `PM · waiting for the board to change` over `last wrote 21 s ago · restarted 25 times`; a worker node's second line is its activity and age; under the strip, `codex orchestrator is reading a result, just now`. Ages count up every second between polls.
3. **Writing is life.** The strip's "Nothing has happened for N min" counts the newest activity as well as the newest post and run.
4. **The activity dots are a band on the page** above the work, as they were before plan 54 moved them to a drawer tab; the Activity tab is gone.
5. **The conversation is hidden by default.** Show conversation in the work's heading brings the column back; Hide in its heading removes it; the choice is remembered in the browser; a dot or a card asking for a turn shows it.

## Tasks

1. `transcript.py`, `chat.py`, `viewer/server.py`, `viewer/static/app.{html,css,js}`; tests in `test_transcript.py`, `test_chat.py`, `test_viewer.py`.
2. Spec section 20, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green; a read-only viewer from this branch over the live maze (port 8768): every live role named its activity, the pulse line counted up, the band showed 74 dots, the conversation was hidden and the toggle showed and hid it.

## Carried

- The Settings pane is a wall of 63 fields in three columns; the owner asked for an overhaul (plan 59).
- A worker's activity words are clipped to 16 characters on its node; the full words are in the pulse line and the tooltip.
