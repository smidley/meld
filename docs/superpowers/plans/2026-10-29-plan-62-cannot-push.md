# Plan 62 — a worker is told what its App cannot push (2026-10-29)

**Goal.** A build never spends itself on a change the group cannot push.

**From the spec.** Section 9, the build brief's sections; `appsetup.PERMISSIONS` and doctor's permission check.

**What the maze run showed.** #4's worker edited `.github/workflows/tests.yml`. The claude App was created without the Workflows permission, so GitHub refused the push, `collect` failed twice, the PM asked a person, and the fix (grant on the App, then accept on the installation) took three asks and an evening. The limit was knowable before the build started: the installation token the group mints names every permission it holds.

## Decisions (each reversible)

1. **`AppAuth.permissions()`** returns the installation's granted permissions as the last mint reported them (the cached token's, else a fresh mint's; empty when nothing is known).
2. **`dispatch.push_limits(permissions)`** turns a missing Workflows permission into words for a worker; `""` for a PAT group or a complete App.
3. **The brief carries `## What you cannot push`** after `## Where you run`: do not create or edit workflow files; if the task cannot be done without one, make every other change, finish `blocked`, and name the workflow change in `needs_human`.

## Tasks

1. `appauth.py`, `dispatch.py`, `brief.py`; tests in `test_appauth.py`, `test_brief.py`.
2. Spec section 9, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green.

## Carried

- Other permission-bound pushes (a protected path, a ruleset) are not named; this plan names the one that happened.
- The maze's two Apps still lack Workflows: write until the owner accepts the pending permission on each installation.
