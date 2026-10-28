# Plan 49 — the work lanes at full width, with the journey named (2026-10-28)

**Goal.** Progress on each issue reads at a glance.

**From the spec.** Section 22 (the overview).

**What the owner said.** "The work lanes are too cramped. We need to be able to see that progress better."

## Decisions (each reversible)

1. **Work above the conversation, full width.** Four lanes across the page instead of four in half of it; the conversation takes the rest of the height below. *Not taken:* lanes as horizontal rows (the left-to-right flow animation is what says where work goes).
2. **The journey's step names are visible.** Seven labelled bars per card (`pull request` shortened to `PR` on the chip, the full name in the tooltip); the "now" line is in the foreground colour. The strip had hidden its labels with `font-size: 0`.
3. **Narrow screens get two lanes per row.**

## Tasks

1. `viewer/static/app.html` (section order), `app.css` (`#live` as a column, `#work`/`#conversation` sizing, the journey, the kanban gaps), `app.js` (the chip label); `tests/test_viewer.py` (the order and the style pins).
2. Spec section 22, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings; checked on the playground board at 1700px wide.

## Carried

- The header above the lanes (map, timeline, limits, setup rows) still takes most of a laptop's height when every group has something to say; the lanes scroll inside their own panel.
