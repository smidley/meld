# Plan 45 — the wizard uses the gh account that owns the repository (2026-10-28)

**Goal.** A person logged in to `gh` as two accounts can name a repository under either and the wizard just works.

**From the spec.** Section 20 (the short way).

**What happened.** The owner ran `meld start` from a terminal where `gh`'s active account is their work account, and named `smidley/<name>`. The wizard read GitHub as the work account, saw an owner that was not that login, took it for an organisation, and GitHub answered 404 on `POST orgs/smidley/repos`. The scratch run had only worked because its launch config handed the wizard smidley's token.

## Decisions (each reversible)

1. **Ask `gh` for the owner's account first.** `gh_token_for(owner)` runs `gh auth token --user <owner>` and falls back to `gh auth token`; the wizard's client and its push use that token. *Not taken:* a wizard field for the account (one more thing to get wrong) or requiring `gh auth switch` (a step outside the page).
2. **A 404 on the organisation create says what would work.** GitHub's 404 covers both "not your organisation" and "a person's name"; the error names the login GitHub sees, the `<login>/<name>` alternative, and `gh auth login` as the owner.

## Tasks

1. `firstrun.py` (`gh_token_for`, `_gh_token(owner)`, `_push(owner)`, the create error), `cli._human_gh`; `tests/test_firstrun.py`.
2. Spec section 20, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings.

## Carried

- The wizard's facts line still reports `gh`'s active account as `gh_login` until a repository is named.
