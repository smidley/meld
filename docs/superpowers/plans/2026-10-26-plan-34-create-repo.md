# Plan 34 — the wizard creates the repository (2026-10-26)

**Goal.** "If a repo isn't already created, meld should create it."

**From the spec.** Section 20 (bootstrap, the short way).

## Decisions (each reversible)

1. **A 404 is the one answer that means create.** `ensure_repository` reads the repository; only a 404 leads to `POST user/repos` (the person owns the name) or `POST orgs/<owner>/repos`, private, no auto-init. Any other error is reported as itself.
2. **This folder becomes the repository's default branch.** `ensure_pushed`: `git init -b <default>` when the folder is not a repository, an initial commit (a README when it is empty) when it has none, `origin` when it has no remote, then one push with the person's own git credentials. A failed push is the error, before labels or issues exist.
3. **Only when just created.** An existing repository is never pushed to by the wizard: a local folder is not assumed to be its history.

## Tasks

1. `firstrun.py` (`_git_state`, `ensure_repository`, `ensure_pushed`, `create` first ensuring the repository; `facts` carries `repo_exists` and `git`), the wizard's repository note and result line; `tests/test_firstrun.py`.
2. Spec section 20, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings; the fake GitHub grows the repository on the create call; a fake git remembers init, commit, remote and push.
