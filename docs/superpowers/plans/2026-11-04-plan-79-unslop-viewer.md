# Plan 79 — the page unslopped (2026-11-04)

**Goal.** The viewer keeps its design and loses the handful of tells that make a screen read as generated.

**From the spec.** Section 22 (the viewer, plans 54 to 59).

**What the audit found** (unslop by Damir Sokolovsky, CC BY 4.0; light slop, so polish mode): `#timeline .dot` breathed on a 4.5 s loop; `.flowline` drifted on a 2.4 s loop under every lane heading; two easing curves; `.ask.selected` carried a 3px amber glow and `settle` a 3px coloured ring; `.lane h3` and `#config-nav .navhead` were uppercase with wide tracking; `#headline` had negative tracking at 18px; synthesized transcript lines were italic; twelve type sizes including 13.5, 12.5, 10.5 and 10px; one-off values such as `gap: 6px 14px`, `padding: 4px 11px`, `padding: 5px 9px`, 14, 18, 13, 9 and 15px; party chips were pills with 45% coloured borders; the progress fills animated `width`; the strip's single-sentence lines ended in a period and said `oldest 1398 min`.

## Direction

- Product: Meld's watch page shows one person where two AI groups stand on a GitHub milestone and what, if anything, needs them.
- Character: calm, exact, watchful.
- World: an instrument panel on a night shift. The ink ramp and the one amber accent stay.
- Moments: the activity band, one dot per post in each group's colour across time; the questions card, the only warm thing on the page.
- Signature: the live dot. Only what is running moves.

## Decisions (each reversible)

1. **Polish, not rebuild.** The palette, the system font, the layout, the lanes and the owner-approved wording of hints and headings stay.
2. **Motion only for life.** The breathing dots and the idle lane drift go; the working ring, the live node ring, the step blink and the flowing edge stay; one curve, `cubic-bezier(0.22, 1, 0.36, 1)`.
3. **Weights 400 and 500**, with 600 kept for `HALTED` alone.
4. **Tags, not pills,** for party chips and badges: 4px radius, a 30% hairline. Panels 12px, cards 8px, controls 6px, so nested radii add up.
5. **Fills scale.** `.bar .fill` and `.icard .ifill` set `transform: scaleX()`; `app.js` writes the transform instead of a width.
6. **Strip copy.** A single-sentence line has no trailing period; `_age` says minutes under ninety, then hours, then days. Two-sentence lines keep their periods.
7. **The band's grid.** A hairline per quarter hour and mono tick labels in `drawTimeline`.

## Tasks

1. `app.css`, `app.html`, `app.js`, `chat.py`; `tests/test_chat.py`, `tests/test_viewer.py`.
2. Spec section 22, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green; the page over linkcheck rendered and compared with the audit list.

## Carried

- The drawer still animates `height` and the settings `<details>` its content height; both are sheet open/close and would need a layout change to move to transform.
- The agent map truncates role chips ("orchestrator · stopped · restarted 59 …"); a wider chip or a second line is layout work beyond a polish.
- Everything in this pass applies to the wizard too, but the wizard was not rendered this run.
