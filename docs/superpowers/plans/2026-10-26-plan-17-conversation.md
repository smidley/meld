# Plan 17 — the conversation (2026-10-26)

**Goal.** The owner's direction, mid-day: the UI must be very easy to use and understand, and must show the cross-agent communication. The page showed the board, the processes and the raw transcripts, but not the thing Meld is — the agents negotiating on the coordination issue.

**From the spec.** Section 22 (the viewer), section 7 (the grammar whose comments the panel shows), section 14 (the human console, whose `meld thread` this reuses).

## Decisions (each reversible)

1. **The thread rides in `/api/state`.** `chat.thread_entries(board, last=THREAD_LAST)` renders the same comments `meld thread` prints, as data, from the board `/api/state` already builds — no second route, no second board. 200 newest; the page appends by comment id and never rebuilds, so scrolling is never yanked. *Not taken:* a separate `/api/thread` with a cursor, which would need its own board cache.
2. **The grammar classifies, the page draws.** Each entry carries `housekeeping` (`STATUS`, leases, liveness, `CONFIG`/`ROSTER`) and `agreement` (the grammar's `AGREEMENT_VERBS`) flags computed in Python, so the page keeps no verb lists — which is also what keeps the existing pin that the bar never names a framework-only verb honest.
3. **One colour per party.** Groups in configuration order from a five-colour palette, the human green, the framework's `[joint]` voice lilac; the same colour titles that group's transcript columns.
4. **Housekeeping is hidden by default.** The filter offers everything, without housekeeping, and agreements-and-humans; the choice is CSS-only (`data-filter` on the panel).
5. **Rows and bars explain themselves.** Panel headings carry a hint, the bar says "You, as the human:", placeholders show a real ref, and an agreement row names its proposer and the proposal's first line.

## Tasks

1. `chat.thread_entries`, `HOUSEKEEPING_VERBS`; `viewer/server.py` (`THREAD_LAST`, the key in `/api/state`); `status.render_status` agreements (`proposer`, `summary`).
2. `app.html` (the `#live` row: the conversation column then the transcripts; hints; bar label), `app.css`, `app.js` (`renderThread`, `colorFor`, the filter).
3. Tests in `test_chat.py`, `test_status.py`, `test_viewer.py`; README, spec section 22, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings. Served against `smidley/meld-playground` and opened in a browser: the thread renders oldest-first and pins to the newest turn, `[claude]` and `[codex:pm]` carry different colours, the agreement rows read as sentences, the filter hides and shows, and the console is clean.

## Carried

- The page's JavaScript still has no runtime test; the browser check is by hand.
- A comment edited on GitHub after the page rendered it is not re-rendered (the page appends by id).
- `audit.challenged` and `needs_human` asks are not yet surfaced in the conversation panel; they are on the board and in the header.
