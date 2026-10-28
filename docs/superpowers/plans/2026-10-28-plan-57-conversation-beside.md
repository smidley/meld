# Plan 57 — the conversation beside the work (2026-10-28)

**Goal.** The page scrolls one thing: the conversation keeps its own scroll in a column beside the lanes, and the header's bars cannot overlap.

**From the spec.** Section 20, the overview paragraph.

**What the owner said.** "Let's put the conversation flow on the left or right side of the screen instead of at the bottom. It's hard to scroll things right now." And, with a screenshot of the milestone row laid over the budget bars: "also seeing some weird overlap at the top."

## Decisions (each reversible)

1. **Right, sticky, viewport-high.** `#live` is a two-column grid: the work takes what is left, the conversation `clamp(22rem, 30vw, 34rem)`, sticky at the top with `100vh` minus the dock, its turn list scrolling inside. Right rather than left because the lanes read left to right from to-do and the conversation is what one glances at.
2. **The lanes answer to their own width.** `#work` is a size container; under 820 px the lanes drop to two (the rule sits after the four-lane rule so it wins). Under 980 px of viewport the pair stacks as before.
3. **Bars are rows.** `#bars` stacks the milestone bar over the budget bars and the progress text wraps; the overlap could not be reproduced in Chromium at 1000 to 1600 px, so the fix removes the possibility rather than the cause (probably Safari's handling of a nowrap flex row).

## Tasks

1. `viewer/static/app.css`; `tests/test_viewer.py` (the layout test).
2. Spec section 20, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green; the live maze page at 1000, 1280 and 1600 px: the conversation sticks and scrolls on its own, the lanes are four at 1280 and two at 1000, the bars are two rows.

## Carried

- The owner's browser is not confirmed; if the overlap was Safari, this plan fixes it blind.
