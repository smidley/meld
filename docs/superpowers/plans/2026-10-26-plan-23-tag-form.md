# Plan 23 — the tag form (2026-10-26)

**Goal.** The last verb the page offered but could not run: `tag`, the orchestrators' and a person's choice of an issue's worker, model, effort and size, rendered since plan 8c as text saying the page had no form for it.

**From the spec.** Section 22 (the page's verbs), section 9 (the rubric the tag follows), section 6 (the labels).

## Decisions (each reversible)

1. **Choices from the catalogue the page already serves.** `/api/config` carries `models` per CLI; the tag intent fills its model select from it, grouped by CLI, and the effort select from the chosen model's efforts — loading the configuration on demand if the form has not been opened. *Not taken:* a second catalogue route.
2. **Every CLI's models are offered.** A human tags an issue for whichever group will claim it, and `meld tag` validates a human's model against no group (only a group's own tag is checked against its adapter), so the form lists them all rather than guessing the claimant.
3. **Blank keeps the label.** The request carries only the fields the person set; `meld tag` leaves the rest as they are, and refuses a request that sets nothing.
4. **Scope, may-delete, skills and held categories stay on the CLI.** They are declarations, not the tag's four labels; the form says so by not offering them.

## Tasks

1. `app.html` (the four controls), `app.css`, `app.js` (`INTENTS.tag`, `FIELDS.tag`, `fillTagChoices`, the payload); `tests/test_viewer.py`.
2. README, spec section 22, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings. The page checked in the browser against the playground: the intent, its selects filled from the catalogue, a row's `tag` as a button.

## Carried

- Scope, may-delete, skills and held categories have no form.
