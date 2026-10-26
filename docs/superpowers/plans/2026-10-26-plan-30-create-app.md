# Plan 30 — a group's GitHub identity, made by Meld (2026-10-26)

**Goal.** The last manual step between "add a group" and "it runs" is gone: no bot account to create, no token to mint or paste.

**From the spec.** Section 18 (App auth), section 20 (bootstrap).

**What the owner said.** After both new CLIs passed their first real run and only the GitHub account and token stood in the way: "that part needs to be automated by meld".

## Decisions (each reversible)

1. **An App, not an account.** GitHub creates no user accounts by API, but it creates an App from a manifest with one click and installs it with a second, and an App group already mints its own tokens (plan 6a). *Not taken:* driving the account sign-up page.
2. **The manifest flow, on the loopback.** `appsetup.AppSetup` serves one page that POSTs the manifest to GitHub, catches `/callback?code=` and converts it (`POST /app-manifests/{code}/conversions`, unauthenticated, the App's id, slug and private key in the answer), stores the key 0600 under `$MELD_HOME/keys/<group>.pem`, sends the person to `apps/<slug>/installations/new`, and catches the manifest's `setup_url` redirect `/installed?installation_id=` (falling back to `GET /app/installations` as the App). Only then is `[groups.<g>]` rewritten as an App group, through `Config.from_dict` and `dumps` like every write of the file.
3. **Exactly the four permissions a group needs**, plus Checks write (a review's verdict is also a check run) and Metadata read, no webhook, private. The client secret in GitHub's answer is never stored.
4. **The group must already exist in the file** (the page's Add a group, or by hand); the command converts its identity and touches nothing else.

## Tasks

1. `appsetup.py` (`manifest`, `convert_manifest`, `list_installations`, `AppSetup`), `cli.py` (`create-app`), `doctor.py` (the token fix names it), `app.js` (the header's not-running line and the add-a-group note name it); `tests/test_appsetup.py`.
2. Spec section 20, README, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings; the flow driven in tests without a socket or a browser. The real two clicks are the owner's, on `smidley/meld-playground`, for `gemini` and `grok`.
