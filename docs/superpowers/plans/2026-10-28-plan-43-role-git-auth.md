# Plan 43 — a role's git authenticates as its group (2026-10-28)

**Goal.** The orchestrator and PM can read the current code of a private repository from the clone they run in.

**From the spec.** Section 9 (roles run in the clone), section 18 (credentials).

**What happened.** The framework's own fetches and pushes carry the group's token (plans 37, 37b), but the roles' own git in the clone used the machine's credentials, which cannot read `smidley/meld-scratch`: the scratch PM reported "repository not found" from every git identity, and the roles planned later milestones from the code as it was at setup.

## Decisions (each reversible)

1. **A helper that asks `meld token`, not a token in the environment.** An App group's installation token expires hourly and a role session lives for hours; the helper fetches a fresh one on every git call. `meld token` prints the calling group's credential bare, refuses a human (no group token) and a worker (every verb does). *Not taken:* `MELD_PUSH_TOKEN` in the role's environment, stale after an hour.
2. **The machine's helpers are cleared first**, so a keychain credential for another account never answers for the product's origin.
3. **The prompt says when to use it:** fast-forward the clone before planning a milestone; both groups share the clone and both only fast-forward.

## Tasks

1. `gitops.py` (`ROLE_CREDENTIAL_HELPER`, `git_config_env`, shared with `dispatch.worker_env`), `runners.role_env`, `cli.token`, `prompts/orchestrator.md`; `tests/test_runners.py`.
2. Spec section 18, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings.

## Carried

- `meld token` relies on `meld` being on the role's PATH, as every other verb the role runs does.
