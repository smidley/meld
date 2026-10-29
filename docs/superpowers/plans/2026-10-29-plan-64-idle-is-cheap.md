# Plan 64 — an idle board is cheap (2026-10-29)

**Goal.** A role whose board offers it nothing costs a launch every quarter hour, not every five minutes.

**From the spec.** Section 12 (`meld wait`), section 17 (the supervisor's relaunch floor, plan 39; the resume chain, plan 55).

**What the maze run showed.** With #4 waiting on an App permission only the owner could grant, claude's PM had nothing legal to do. It still woke every wait timeout, re-read the board, found nothing, exited, and was relaunched after the floor: launches 87 to 110 in two hours and $40 of spend while the board could not move.

## Decisions (each reversible)

1. **`meld status` says `idle`.** True for a role with no active verb on any issue, PR or open agreement and no live run of its group; `tag`, `remember`, `challenge`, `propose` and `ask` are not activity.
2. **A wait that times out idle leaves a note.** `wait_for_change` writes `state/<group>/<role>/idle.json` with `until` = now + `runners.idle_minutes` (default 15, floor 1) and reports `idle_until`.
3. **The supervisor honours it.** After a clean exit, past plan 39's floor, it sleeps until `until` (at most `runners.idle_minutes`) and launches with reason `idle`, whose launch-block words tell the role to check the board and wait again if nothing changed. A note whose instant has passed changes nothing; a human's answer is seen at the next launch, at most `idle_minutes` later.

## Tasks

1. `status.py`, `state.py` (`StateStore.write_idle`/`read_idle`), `waiting.py`, `runners.py` (`idle_path`, `_idle_seconds`), `prompts.py` (`idle` reason), `config.py` (`runners.idle_minutes`), the settings words in `app.js`; tests in `test_status.py`, `test_wait.py`, `test_runners.py`, `test_config.py`.
2. Spec section 12, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green.

## Carried

- The supervisor does not watch GitHub; a person's answer reaches an idle role at the next launch, up to `idle_minutes` later.
