# Plan 82 — a person's commands use the account that owns the repository (2026-11-20)

**Goal.** The board and every verb typed as the person reach a private product repository when `gh`'s active account is another one.

**From the spec.** Section 20 (bootstrap: plan 45's account rule for the wizard), section 18 (identities and credentials).

**What the livedrop start showed.** The wizard ran from a terminal whose active `gh` account is the work account. It created `smidley/livedrop`, four Apps and four supervisors as `smidley` (`firstrun.gh_token_for`, plan 45), adopted the board, and the page said "Cannot reach meld: /api/state: 502 GET repos/smidley/livedrop/milestones?state=open&per_page=100&page=1 failed with HTTP 404". `meld status` in the clone printed the same 404; with `GH_TOKEN=$(gh auth token --user smidley)` it printed the board. `context.load_context` builds a human party's client with `token=None`, so `gh api` runs as the active account.

## Decisions (each reversible)

1. **Plan 45's rule, for every human client.** `cli._human_token(repo)`: the `gh` account named like the repository's owner when it is logged in, else the active account. One rule for the wizard, the board, each verb, doctor and setup.
2. **An exported token wins.** With `GH_TOKEN` or `GITHUB_TOKEN` set, nothing is resolved; the launch configs and recipes that pass a token keep working unchanged.
3. **The command line resolves; `load_context` receives.** `load_context(human_token=)` is a callable like `adapter_for=`, consulted for a human party only. Tests that pass none run as before, and `FakeGh` answers `gh auth token` with "none".
4. **Not taken:** trying each listed `[humans]` account. It needs a rule for two listed accounts logged in at once and a second concept beside plan 45's.

## Tasks

1. `context.py` (`human_token`), `cli.py` (`_human_token`, `_human_gh`, the five call sites), `tests/fakegh.py`, `tests/test_context.py`.
2. Spec section 20, `README.md`, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green. On this machine, with the work account active and no token exported, `meld status` in `~/code/livedrop` prints the board as `smidley`.

## Carried

- A listed human on an organisation's repository, active as another account, is still read as the active one.
- `antigravity/claude-opus-4-6-thinking` lists efforts `agy` refuses, and setup picks it for an Antigravity orchestrator.
- The wizard offers a CLI whose login it could not confirm (Grok), and its roles then crash "Not signed in".
