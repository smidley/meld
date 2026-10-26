# Plan 27 — a group that is not running says so (2026-10-26)

**Goal.** A group saved through the form is visible at once, and the page says what it still needs before it can run.

**From the spec.** Section 22 (what the page may do), section 20 (bootstrap).

**What the owner said.** "I added grok and gemini, but I don't see them doing anything." The groups were in the file; the page kept the configuration it started with, neither CLI was on PATH, neither token was set, neither bot login existed, and nothing had started a supervisor.

## Decisions (each reversible)

1. **The state route re-reads the file on change.** `Viewer._refresh_config` compares `.meld.toml`'s modification time and reloads through `load_config`; a file that does not load keeps the last good configuration and reports `config_error`. The human's context holds no group credentials, so the reload costs nothing. *Not taken:* reloading on every poll (a parse per five seconds for a file that rarely changes), or asking the person to restart.
2. **Local facts only.** `setup` per group is the CLI binary on PATH (`which`, injectable), the token variable in the viewer's own environment (`env`, injectable; an App group needs none) and the supervisor's liveness. The bot login and the repository access need the network and stay with `meld doctor`, which the note points at.
3. **The command is on the page.** `meld run <group> --detach`, in the product clone, because that step is the one nothing on the page can do for the person.

## Tasks

1. `viewer/server.py` (`_refresh_config`, `_setup`, `which`/`env` seams), `app.html` (`#setup`), `app.js` (`renderSetup`, the map's "not started", the add-a-group note), `app.css`; `tests/test_viewer.py`.
2. Spec section 22, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings. On the live playground page with the owner's saved `gemini` and `grok` groups: both drawn on the map as "not started", each listed under it with the missing CLI, the unset token variable and the start command.
