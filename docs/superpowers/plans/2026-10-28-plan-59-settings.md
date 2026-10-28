# Plan 59 — the settings pane (2026-10-28)

**Goal.** A person can find one setting, see what it is called in the file, change it and know what Save will write.

**From the spec.** Section 20, the overview paragraph; section 22 (the configuration form).

**What the owner said.** "Settings UI needs an overhaul." The pane was sixty-three fields in three columns under one scrolling box, with one Save at the bottom.

## Decisions (each reversible)

1. **Sections, one at a time.** A list on the left: the file's tables in the plain words the form already had (Repository, People, Review rules, …), then one entry per AI group in its colour and Add an AI. The right side shows the chosen section only; the choice survives a save.
2. **A row per setting.** The words, the key in the file under them in monospace (`review.quorum`; a group's keys shown relative to the group), and the control on the right at a sane width. The dotted path stays in `dataset.path`, so `collectConfig` is unchanged.
3. **Edits are visible before Save.** Every control remembers its rendered value; an edited row's words turn amber, the section's list entry carries the count, and the foot reads "3 changes to save" or "no changes". The foot stays in view with Save and the result.
4. **No `<details>`.** The pane is a plain div; opening the Settings tab loads the configuration.

## Tasks

1. `viewer/static/app.{html,css,js}`; the markers in `tests/test_viewer.py`.
2. Spec section 20, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green; on a read-only viewer over the live maze: the list showed every section and both groups, a changed number counted in the foot and on its section's entry, and the section stayed chosen while switching.

## Carried

- The words per setting are the labels the form had; a second line explaining each one is content work for later.
