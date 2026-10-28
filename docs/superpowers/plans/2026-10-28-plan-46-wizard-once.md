# Plan 46 — the wizard starts a group once and keeps one identity (2026-10-28)

**Goal.** The wizard cannot start a group twice or give it two GitHub Apps, and a changed App never posts under the old one.

**From the spec.** Section 18 (App auth), section 20 (the short way).

**What happened.** On the first real run (`smidley/meld-test-maze`): codex's install redirect and the page's start both called `start` within two seconds and launched two codex supervisors, so two codex orchestrators posted CONFIG and ROSTER and codex asked the owner about a concurrent session. The page's App link stayed clickable after the install; a second click created `meld-test-maze-claude123`, the file was rewritten to it, and claude's roles kept the first App's cached token and posted as `meld-test-maze-claude[bot]`, a stranger to the board: the claude PM flagged its own group's comments 25 times.

## Decisions (each reversible)

1. **One start at a time, and never over a live supervisor.** `SetupSession.start` runs under a lock; `cli.detach_supervisor` refuses when the supervisor record is alive, so the wizard's launcher and `meld run --detach` share the check.
2. **A finished App flow creates nothing more.** Its first page says the App is installed; the page's link for an installed App goes to the App on GitHub.
3. **The token cache is tied to the App.** It records the App and installation it was minted for and is ignored for any other; a reader with no expectation still reads it.

## Tasks

1. `firstrun.py`, `appsetup.py`, `appauth.py`, `cli.py`, `viewer/static/app.js`; `tests/test_firstrun.py`, `tests/test_appsetup.py`, `tests/test_appauth.py`.
2. Spec sections 18 and 20, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings.

## Carried

- Why both supervisors of the first real run died at about 10:02 with empty logs is not known from the records.
