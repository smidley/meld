# Plan 20 — reading the run (2026-10-26)

**Goal.** After plans 17–19 the page narrates the milestone, presents the decisions and shows the conversation; the transcript columns still showed every tool call and result, which buries what each agent actually said, and a decision card gave no way to the thread turn behind it.

**From the spec.** Section 22.

## Decisions (each reversible)

1. **A transcript column shows narration by default.** `message`, run start and end, usage and errors stay; `tool_call`, `tool_result` and `thinking` are hidden until "show tool calls" in the header is ticked. CSS-only (`#streams.narration`), so the stream and its scroll pinning are untouched. *Not taken:* hiding tool traffic server-side, which would make the page a filter of the transcript rather than a view of it.
2. **A decision card knows its comment.** `chat.decisions` carries `comment_id` (the inbox's needs-human items gained it; the plan awaiting approval uses the verified `[joint]` comment's id); the card's title scrolls the conversation to that turn and widens the filter if the turn is hidden.
3. **The handoff catches up**: plans 17–19 merged as PRs #33–#35 while each plan's handoff still called the next one "this branch"; this plan's handoff says what is on `main`.

## Tasks

1. `chat.inbox` (`comment_id` on needs-human items), `chat.decisions` (`comment_id` on every card); `tests/test_chat.py`.
2. `app.html` (the checkbox), `app.css`, `app.js` (`showTurn`, the `narration` class); `tests/test_viewer.py`.
3. README, spec section 22, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings; the page checked in a browser against the playground, whose September audit transcript renders as narration with the checkbox off and as the full event list with it on.

## Carried

- The checkbox is page-wide; a per-column choice was not needed yet.
- A card for an item whose ask was never posted (`comment_id` None) has no jump.
