# Plan 24 — the overview (2026-10-26)

**Goal.** The page as a picture a person can take in at a glance during the second live run: live activity first, the agents and their work drawn rather than listed, the work's motion visible, and one click from a task to everything that touched it. Plus the three run-found defects the same session exposed.

**From the spec.** Section 22 (the viewer), section 14 (audits), section 7 (`NEEDS-HUMAN`).

**What the owner said, in order.** "The transcripts section is not very usable since there's a lot of dead stuff in the way. I want to be able to see live activity." "Each transcript should also auto-scroll to the latest message." "Can we do something so that it's easier to see which sub-agent is working on each particular task? Maybe some kind of web/tree view that can appear if you click on a task?" "I was thinking of something a little more graphical. This whole app needs to be more visually appealing since it's for a human to get an overview about what's going on." "The work flow view should have animations showing how the work is flowing."

## Decisions (each reversible)

1. **Live first, finished hidden.** Transcript columns are ordered alive, unknown, dead; finished ones are hidden unless "show finished" is ticked; each column follows its newest line until the reader scrolls up, and resumes when they scroll back down. *Not taken:* removing finished columns outright (a person reading a failure needs them).
2. **Inline SVG, drawn node by node.** The agents map (group → roles → running workers), the thread timeline, the progress and budget bars and the task graph are built with `createElementNS`, never markup strings, and with no library: the page keeps its no-external-reference rule, and the off-machine guard exempts only the SVG namespace URL. *Not taken:* a charting library from a CDN.
3. **The task graph is the run's own records.** Clicking an issue draws its issue, its build runs, its PRs and the review and audit runs on them (`/api/state` runs carry `model`, `effort` and `outcome` for it; `processes` carries the roles). *Not taken:* a per-task log of the thread (the timeline and conversation already show it).
4. **Motion means something.** A running worker's ring and bar are elapsed over allowed time against the server's clock; a card that changed lane travels there (FLIP through the Web Animations API); a lane's flow line races for a minute after a card arrives; attention posts on the timeline are ringed. Nothing moves for decoration, and nothing moves under `prefers-reduced-motion`.
5. **Audits stay inside the milestone.** `rules.outside_milestone`: a merged PR whose issue is not in the active milestone was audited on that milestone's thread, which the board never reads; it is neither pending nor legal to audit. Found live: the run re-audited milestone 1 at large tier until the caps bounded it.
6. **A consumed verdict says so.** `meld audit` records the verdict's outcome on the run record and, called again on a collected run with no verdict file, says the verdict was consumed instead of asking for a collect. Found live: the codex PM read "run meld collect first" as a stuck result and called for a human.
7. **A `NEEDS-HUMAN` about anything waits on the person.** `chat.inbox` lists one whose ref is not a needs-human issue (a PR, a bare number, no ref) until a person posts on that ref; the page's headline and cards carry it. Found live: `NEEDS-HUMAN 40` and "nothing waits on you" on the same page.

## Tasks

1. `app.js`/`app.css`/`app.html`: live-first columns, following, the map, bars, timeline, task dialog and graph, flow animations; `server.py` (`processes`), `status.py` (run fields); `tests/test_viewer.py`.
2. `rules.py`, `status.py`, `audit.py`, `review.py`, `chat.py`; `tests/test_status.py`, `tests/test_audit.py`, `tests/test_dispatch.py`, `tests/test_chat.py`.
3. Spec sections 14 and 22, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings. Every drawing checked in the browser against the live second run on `smidley/meld-playground`: the map with two running workers and their rings, the timeline's lanes and attention dots, the task graph for #47, a card travelling from in review to done, the needs-human card for `NEEDS-HUMAN 40`.

## Carried

- The plan grammar drops an item whose title contains `|` without a word (`plan.ITEM_RE`); the approval card can appear before every item has an issue. Both seen live; neither fixed here.
- `meld review` does not yet record its outcome on the run record the way `meld audit` now does.
