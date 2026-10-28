# Plan 54 — the dashboard, designed (2026-10-28)

**Goal.** A page a person can monitor for hours: what needs them first, one calm visual system, plain words.

**From the spec.** Section 22 (the page), with the usability review and the design direction under `docs/superpowers/plans/plan-54/` as the working papers.

**What the owner said.** "The UI for the dashboard needs a lot of work. Dispatch some sub agents to help with the design and usability here."

## Decisions (each reversible)

1. **What needs the person goes first.** A one-line status strip (`chat.status_strip`, with the review's wording per state and a fixed precedence: halted, needs you, a stopped or crash-looping or out-of-quota role, a blocked issue or silence, moving, done) opens the page; the questions sit directly under it; the rest of the header slims to the bars.
2. **One visual system.** The design direction's tokens: three surface tones instead of borders, tabular numerals, one amber accent that means "needs you", six fixed group colours for identity, a single button system, ambient motion. No renames.
3. **Plain words at render time.** Verbs, roles and states are translated on the page with the raw token on hover (`VERB_WORDS`, `roleWords`, `partyWords`, `STEP_SHORT`); the server speaks them too in the strip and the agent rows (`chat.agent_rows`).
4. **The page stays stacked**; the drawer is a bottom sheet over a page that scrolls; the timeline lives in an Activity tab; the agents map stays, restyled, carrying the agent rows' facts.
5. **Cards say one thing**: the issue, one sentence of where it is, a running worker's bar, the verbs; lanes bucket by the journey's step.
6. **A `plan issues` call that lands after the person's approval with nothing left to create is a no-op**, not a violation (the rehearsal hit the race one run in three).

## Tasks

1. `chat.py` (`status_strip`, `agent_rows`), `viewer/server.py` (the payload), `plan.py`; `viewer/static/app.html`, `app.css`, `app.js`; tests throughout.
2. Spec section 22, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings; screenshots at 1680 and 1280 with injected questions, cards in every lane and a running worker; the strip's `bad` level seen live on the playground's crash-looping roles.

## Carried

- The map's group node repeats the role chips' words; the sub-label could carry only the first clause.
- The strip's `ok` and `quiet` levels were checked through their classes, not live.
- The token-limit monitor is a freeze notice, not a bar: no provider reports a quota ceiling.
