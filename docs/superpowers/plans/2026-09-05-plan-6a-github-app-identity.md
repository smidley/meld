# Plan 6a: GitHub App identities per group

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each Meld group act as a GitHub App installation (`meld-claude[bot]`, `meld-codex[bot]`) instead of a machine-user account with a personal access token, so bootstrapping a product needs no extra GitHub accounts or mailboxes.

**Architecture:** A new leaf module `appauth.py` mints short-lived installation tokens from an App's private key (RS256 JWT signed with the `openssl` CLI, token fetched over HTTPS with `urllib`, cached under `$MELD_HOME/cache/<group>/` with mode 0600, refreshed before expiry). `GitHub` learns a `token_provider` so every `gh api` call carries a fresh token and a 401 triggers one refresh-and-retry. `config` gains a per-group `auth` mode (`pat`, the default, unchanged; or `app` with `app_id`, `installation_id`, and a private-key source). `context` wires the provider in; `doctor` verifies App auth end to end from the token response's own `permissions`; `setup` emits the App config shape and the exact steps to create and install the Apps; `review` additionally publishes a neutral check run when the group is an App (spec 21 item 4's original intent). Workers still receive no credentials.

**Tech Stack:** Python 3.13, stdlib only (`json`, `base64`, `subprocess` for `openssl`, `urllib.request`, `tempfile`, `os`), click; `openssl` CLI (present on macOS and Linux) for RS256 signing; hermetic tests generate a throwaway RSA key with `openssl genrsa` and inject the HTTPS call.

**Spec:** `docs/superpowers/specs/2026-09-03-meld-design.md` — sections 5 (configuration), 6 (GitHub data model: one login per group), 13 (statuses vs check runs), 18 (security: tokens, worker isolation), 20 (bootstrap). This plan adds the App identity option to those sections (Task 6).

## Global Constraints

- Python `>=3.12` (pinned 3.13 locally); runtime dependency `click>=8.1` only — no PyJWT, no `cryptography`; RS256 signing shells out to `openssl` (verified present by `meld doctor`).
- The private key never appears in argv, logs, state, result dicts, error messages, worker environments, or the repo: it reaches `openssl` through a 0600 temp file that is deleted immediately after signing; installation tokens are cached only in `$MELD_HOME/cache/<group>/app-token.json` with mode 0600 and never printed.
- `scrub_env` removes `MELD_APP_KEY_*` from worker environments exactly as it removes `MELD_TOKEN_*`; `meld doctor`'s settings-env check flags both prefixes.
- One login per group is preserved: an App group's `bot` is `<app-slug>[bot]`, and every existing rule (`party_of_login`, `approving_parties`, author/committer checks) works unchanged on that login.
- PAT groups keep working exactly as before; `auth` defaults to `pat`; mixed configurations (one PAT group, one App group) are supported.
- Hermetic tests: no network (the HTTPS mint is injected), no real `gh`, `GIT_CONFIG_GLOBAL=/dev/null`; `openssl` is invoked for real (key generation + signing + verification are local).
- Conventional commit messages, no attribution trailers. Repo-local git identity is `Scott Brant <421573+smidley@users.noreply.github.com>`.
- Baseline at the plan's base (`origin/main` = 8820500, plan 5a merged): `uv run pytest -q` → 488 passed, no warnings. Each task leaves the suite green.

## Decisions (rulings made while writing the plan; recorded so executors do not re-litigate)

1. **One App per group**, installed on the product repo by the human. An App has one identity (`<slug>[bot]`), so a shared App cannot tell the groups apart; two Apps keep every distinct-login rule intact.
2. **Signing via the `openssl` CLI**, not a Python dependency. RS256 is RSASSA-PKCS1-v1_5 with SHA-256, which is exactly `openssl dgst -sha256 -sign`. The key material is passed through a 0600 temporary file (never argv), removed in a `finally`.
3. **Minting over HTTPS with `urllib.request`** rather than `gh api`: `gh` injects its own `Authorization` header from `GH_TOKEN`, and the JWT must be sent as `Bearer`. Every other call still goes through `gh api` with the installation token in `GH_TOKEN`. The HTTPS call is injectable for tests.
4. **Token cache on disk, 0600, per group**: `$MELD_HOME/cache/<group>/app-token.json` holding `{"token", "expires_at"}`; a token is reused while more than 5 minutes remain, else re-minted. Rationale: PM loops run `meld` dozens of times a minute; minting per process would waste the App's rate budget. `$MELD_HOME` is already disposable state with secrets-adjacent files (`GH_CONFIG_DIR` scratch).
5. **Config shape**: `[groups.<g>] auth = "app"`, `app_id = <int>`, `installation_id = <int>`, and exactly one of `private_key_env = "MELD_APP_KEY_<G>"` (PEM text, literal `\n` sequences allowed) or `private_key_file = "<path>"`; `token_env` is not required for App groups and is ignored if present. `bot` stays required and must end with `[bot]` for App groups (setup prints the slug; doctor verifies it against `GET /app`).
6. **Refresh on 401**: `GitHub._call` asks the provider for a token before each call; on a 401 with a provider present it forces a refresh and retries once.
7. **Doctor for App groups** replaces the PAT identity/permission probes with: JWT mint succeeds; `GET /app` slug matches `bot`; installation token mint succeeds and its `permissions` map grants `contents`, `issues`, `pull_requests`, `statuses` = `write` (and reports `checks`); the installation's repositories include the product repo.
8. **Check runs for App groups**: `meld review` still records the verdict as a `meld/review/<group>` commit status (the quorum reads statuses); when the reviewing group is an App it also creates a check run named `meld/review/<group>` with conclusion `neutral`, title = the verdict, so the PR's Checks tab shows the review without ever blocking merge.
9. **App creation stays manual** (browser flow); `meld setup` prints exact steps and the config keys; a manifest-flow helper is a later plan.
10. **`GitHub.token` becomes a property** so `push_branch(wt, branch, ctx.gh.token)` call sites in `dispatch`, `review`, and `memory` keep working and always get a fresh installation token.

## File structure

| File | Responsibility |
|---|---|
| `src/meld/appauth.py` (new) | JWT for an App, installation-token minting, token cache, `AppAuth` provider, `app_slug` |
| `src/meld/config.py` | `GroupConfig.auth/app_id/installation_id/private_key_env/private_key_file`; validation |
| `src/meld/adapters/base.py` | `SCRUBBED_PREFIXES` gains `MELD_APP_KEY_` |
| `src/meld/github.py` | `token_provider`, `token` property, 401 refresh, `create_check_run` |
| `src/meld/context.py` | builds `AppAuth` for App groups |
| `src/meld/doctor.py` | `app:<g>` checks |
| `src/meld/setup.py` | starter TOML comments for App auth; `next` steps |
| `src/meld/review.py` | neutral check run for App groups |
| `README.md`, `docs/superpowers/specs/2026-09-03-meld-design.md`, `docs/HANDOFF.md` | docs |

---

### Task 1: `appauth.py` — JWT, installation token, cache

**Files:**
- Create: `src/meld/appauth.py`
- Test: `tests/test_appauth.py` (new)

**Interfaces:**
- Consumes: `openssl` CLI, `urllib.request`; the token cache is written via a sibling `.tmp` file and `os.replace` (see `TokenCache.save`), not `atomic.write_atomic`.
- Produces: `appauth.AppAuthError(RuntimeError)`; `appauth.b64url(data: bytes) -> str`; `appauth.jwt_for_app(app_id: int, pem: str, *, now: int | None = None, sign: Callable[[bytes, str], bytes] | None = None) -> str` (claims `iat = now - 60`, `exp = now + 540`, `iss = str(app_id)`); `appauth.sign_rs256(message: bytes, pem: str, run=subprocess.run) -> bytes` (default signer); `appauth.InstallationToken(token: str, expires_at: str, permissions: dict[str, str], repositories: tuple[str, ...])`; `appauth.mint_installation_token(app_id, installation_id, pem, *, now=None, post: Callable[[str, dict[str, str], bytes | None], tuple[int, dict]] | None = None, sign=None) -> InstallationToken`; `appauth.app_slug(app_id, pem, *, now=None, get=None, sign=None) -> str`; `appauth.load_pem(env: Mapping[str, str], private_key_env: str | None, private_key_file: str | None) -> str` (unescapes literal `\n`); `appauth.TokenCache(path: Path)` with `.load() -> InstallationToken | None` and `.save(tok)` (0600); `appauth.AppAuth(app_id, installation_id, pem, cache_path, *, now=None, post=None, sign=None)` with `.token(force: bool = False) -> str` (reuses the cached token while > 300 s remain) and `.last: InstallationToken | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_appauth.py`:

```python
import base64
import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from meld.appauth import AppAuth, AppAuthError, InstallationToken, TokenCache, app_slug, b64url, jwt_for_app, load_pem, mint_installation_token, sign_rs256

NOW = 1_800_000_000


@pytest.fixture(scope="module")
def keypair(tmp_path_factory):
    d = tmp_path_factory.mktemp("keys")
    subprocess.run(["openssl", "genrsa", "-out", str(d / "key.pem"), "2048"], check=True, capture_output=True)
    subprocess.run(["openssl", "rsa", "-in", str(d / "key.pem"), "-pubout", "-out", str(d / "pub.pem")], check=True, capture_output=True)
    return (d / "key.pem").read_text(), d / "pub.pem"


def test_jwt_has_the_github_claims_and_a_verifiable_signature(keypair, tmp_path):
    pem, pub = keypair
    token = jwt_for_app(1234, pem, now=NOW)
    header_b64, payload_b64, sig_b64 = token.split(".")
    header = json.loads(base64.urlsafe_b64decode(header_b64 + "=="))
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    assert header == {"alg": "RS256", "typ": "JWT"}
    assert payload == {"iat": NOW - 60, "exp": NOW + 540, "iss": "1234"}
    sig = base64.urlsafe_b64decode(sig_b64 + "==")
    (tmp_path / "sig").write_bytes(sig)
    (tmp_path / "msg").write_bytes(f"{header_b64}.{payload_b64}".encode())
    proc = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(pub), "-signature", str(tmp_path / "sig"), str(tmp_path / "msg")],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_sign_rs256_never_puts_the_key_in_argv(keypair):
    pem, _ = keypair
    seen = []

    def run(argv, **kw):
        seen.append(argv)
        return subprocess.run(argv, **kw)

    sign_rs256(b"hello", pem, run=run)
    assert seen and all("PRIVATE KEY" not in " ".join(a) for a in seen)
    assert "-sign" in seen[0]


def test_mint_installation_token_posts_a_bearer_jwt_and_parses_the_response(keypair):
    pem, _ = keypair
    calls = []

    def post(url, headers, body):
        calls.append((url, headers, body))
        return 201, {"token": "ghs_abc", "expires_at": "2027-01-01T00:00:00Z",
                     "permissions": {"contents": "write", "issues": "write", "pull_requests": "write", "statuses": "write", "checks": "write"},
                     "repositories": [{"full_name": "acme/widgets"}]}

    tok = mint_installation_token(1234, 55, pem, now=NOW, post=post)
    assert tok == InstallationToken("ghs_abc", "2027-01-01T00:00:00Z",
                                    {"contents": "write", "issues": "write", "pull_requests": "write", "statuses": "write", "checks": "write"},
                                    ("acme/widgets",))
    url, headers, body = calls[0]
    assert url == "https://api.github.com/app/installations/55/access_tokens"
    assert headers["Authorization"].startswith("Bearer ") and headers["Accept"] == "application/vnd.github+json"
    assert headers["X-GitHub-Api-Version"] == "2022-11-28" and body is None


def test_mint_reports_github_errors_without_leaking_the_jwt(keypair):
    pem, _ = keypair

    def post(url, headers, body):
        return 401, {"message": "A JSON web token could not be decoded"}

    with pytest.raises(AppAuthError) as e:
        mint_installation_token(1234, 55, pem, now=NOW, post=post)
    assert "401" in str(e.value) and "could not be decoded" in str(e.value) and "eyJ" not in str(e.value)


def test_app_slug_reads_get_app(keypair):
    pem, _ = keypair
    seen = {}

    def get(url, headers):
        seen["url"], seen["headers"] = url, headers
        return 200, {"slug": "meld-claude", "id": 1234}

    assert app_slug(1234, pem, now=NOW, get=get) == "meld-claude"
    assert seen["url"] == "https://api.github.com/app" and seen["headers"]["Authorization"].startswith("Bearer ")


def test_load_pem_prefers_file_then_env_and_unescapes_newlines(tmp_path):
    key = tmp_path / "k.pem"
    key.write_text("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n")
    assert load_pem({}, None, str(key)).startswith("-----BEGIN")
    env = {"MELD_APP_KEY_CLAUDE": "-----BEGIN RSA PRIVATE KEY-----\\nabc\\n-----END RSA PRIVATE KEY-----"}
    assert load_pem(env, "MELD_APP_KEY_CLAUDE", None) == "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"
    with pytest.raises(AppAuthError, match="MELD_APP_KEY_CLAUDE"):
        load_pem({}, "MELD_APP_KEY_CLAUDE", None)
    with pytest.raises(AppAuthError, match="exactly one"):
        load_pem(env, "MELD_APP_KEY_CLAUDE", str(key))


def test_token_cache_is_private_and_tolerates_garbage(tmp_path):
    cache = TokenCache(tmp_path / "cache" / "claude" / "app-token.json")
    assert cache.load() is None
    cache.save(InstallationToken("ghs_1", "2027-01-01T00:00:00Z", {"contents": "write"}, ("acme/widgets",)))
    assert oct(os.stat(cache.path).st_mode & 0o777) == "0o600"
    assert cache.load().token == "ghs_1"
    cache.path.write_text("{not json")
    assert cache.load() is None


def test_app_auth_reuses_a_fresh_token_and_remints_when_stale(keypair, tmp_path):
    pem, _ = keypair
    minted = []

    def post(url, headers, body):
        minted.append(1)
        exp = datetime.fromtimestamp(NOW + 3600, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return 201, {"token": f"ghs_{len(minted)}", "expires_at": exp, "permissions": {}, "repositories": []}

    auth = AppAuth(1234, 55, pem, tmp_path / "app-token.json", now=lambda: NOW, post=post)
    assert auth.token() == "ghs_1" and auth.token() == "ghs_1" and len(minted) == 1
    late = AppAuth(1234, 55, pem, tmp_path / "app-token.json", now=lambda: NOW + 3600 - 200, post=post)
    assert late.token() == "ghs_2" and len(minted) == 2          # under 5 minutes left: re-minted
    assert late.token(force=True) == "ghs_3" and len(minted) == 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_appauth.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.appauth'`.

- [ ] **Step 3: Write `src/meld/appauth.py`**

```python
"""GitHub App authentication for a group: a short-lived JWT signed with the App's private key, exchanged for an
installation access token that every `gh api` call then carries (spec 18, plan 6a).

Stdlib only: RS256 (RSASSA-PKCS1-v1_5 + SHA-256) is `openssl dgst -sha256 -sign`; the key reaches openssl through a
0600 temporary file that is deleted immediately (never argv); the one HTTPS call that cannot go through `gh api`
(the JWT must be sent as `Bearer`, and gh would overwrite the header) uses urllib and is injectable for tests.
Tokens are cached per group under $MELD_HOME/cache with mode 0600 and re-minted with 5 minutes to spare.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

API = "https://api.github.com"
JWT_LIFETIME_SECONDS = 540      # GitHub caps App JWTs at 10 minutes; 9 leaves room for clock skew on top of iat-60
REFRESH_MARGIN_SECONDS = 300
_ISO = "%Y-%m-%dT%H:%M:%SZ"


class AppAuthError(RuntimeError):
    pass


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sign_rs256(message: bytes, pem: str, run=subprocess.run) -> bytes:
    """PKCS#1 v1.5 SHA-256 signature via the openssl CLI. The key goes through a private temp file, never argv."""
    fd, path = tempfile.mkstemp(prefix="meld-app-", suffix=".pem")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(pem if pem.endswith("\n") else pem + "\n")
        os.chmod(path, 0o600)
        proc = run(["openssl", "dgst", "-sha256", "-sign", path], input=message, capture_output=True)
    except OSError as e:
        raise AppAuthError(f"cannot run openssl to sign the App JWT: {e}") from e
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode(errors="replace").strip()
        raise AppAuthError(f"openssl could not sign with the App private key: {err[:200]}")
    return proc.stdout


def jwt_for_app(app_id: int, pem: str, *, now: int | None = None, sign: Callable[[bytes, str], bytes] | None = None) -> str:
    ts = int(time.time()) if now is None else int(now)
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = b64url(json.dumps({"iat": ts - 60, "exp": ts + JWT_LIFETIME_SECONDS, "iss": str(app_id)}, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    signature = (sign or sign_rs256)(signing_input, pem)
    return f"{header}.{payload}.{b64url(signature)}"


def _http(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except ValueError:
            return e.code, {"message": e.reason}
    except (urllib.error.URLError, OSError) as e:
        raise AppAuthError(f"{type(e).__name__}: could not reach api.github.com") from e


def _bearer(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "meld"}


@dataclass(frozen=True)
class InstallationToken:
    token: str
    expires_at: str
    permissions: dict[str, str]
    repositories: tuple[str, ...]

    def seconds_left(self, now: int) -> float:
        try:
            exp = datetime.strptime(self.expires_at, _ISO).replace(tzinfo=timezone.utc)
        except ValueError:
            return 0.0
        return exp.timestamp() - now


def mint_installation_token(app_id: int, installation_id: int, pem: str, *, now: int | None = None,
                            post: Callable[[str, dict[str, str], bytes | None], tuple[int, dict]] | None = None,
                            sign: Callable[[bytes, str], bytes] | None = None) -> InstallationToken:
    jwt = jwt_for_app(app_id, pem, now=now, sign=sign)
    url = f"{API}/app/installations/{installation_id}/access_tokens"
    status, data = (post or (lambda u, h, b: _http("POST", u, h, b)))(url, _bearer(jwt), None)
    if status not in (200, 201) or not isinstance(data, dict) or not data.get("token"):
        message = data.get("message", "") if isinstance(data, dict) else ""
        raise AppAuthError(f"installation token request failed with HTTP {status}: {message or 'no token in response'}")
    repos = tuple(str(r.get("full_name", "")) for r in data.get("repositories", []) if isinstance(r, dict))
    return InstallationToken(str(data["token"]), str(data.get("expires_at", "")), dict(data.get("permissions") or {}), repos)


def app_slug(app_id: int, pem: str, *, now: int | None = None, get: Callable[[str, dict[str, str]], tuple[int, dict]] | None = None,
             sign: Callable[[bytes, str], bytes] | None = None) -> str:
    """The App's slug (its bot login is `<slug>[bot]`), from GET /app authenticated as the App."""
    jwt = jwt_for_app(app_id, pem, now=now, sign=sign)
    status, data = (get or (lambda u, h: _http("GET", u, h, None)))(f"{API}/app", _bearer(jwt))
    if status != 200 or not isinstance(data, dict) or not data.get("slug"):
        raise AppAuthError(f"GET /app failed with HTTP {status}: {data.get('message', '') if isinstance(data, dict) else ''}")
    return str(data["slug"])


def load_pem(env: Mapping[str, str], private_key_env: str | None, private_key_file: str | None) -> str:
    if bool(private_key_env) == bool(private_key_file):
        raise AppAuthError("an App group needs exactly one of private_key_env or private_key_file")
    if private_key_file:
        try:
            return Path(private_key_file).expanduser().read_text()
        except OSError as e:
            raise AppAuthError(f"cannot read private_key_file {private_key_file}: {type(e).__name__}") from e
    value = env.get(private_key_env or "")
    if not value:
        raise AppAuthError(f"{private_key_env} is not set in the environment")
    return value.replace("\\n", "\n")


class TokenCache:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> InstallationToken | None:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("token"), str) or not isinstance(data.get("expires_at"), str):
            return None
        return InstallationToken(data["token"], data["expires_at"], dict(data.get("permissions") or {}), tuple(data.get("repositories") or ()))

    def save(self, tok: InstallationToken) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"token": tok.token, "expires_at": tok.expires_at, "permissions": tok.permissions, "repositories": list(tok.repositories)}, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)


class AppAuth:
    """Token provider for one App group: cached installation token, re-minted with REFRESH_MARGIN_SECONDS to spare."""

    def __init__(self, app_id: int, installation_id: int, pem: str, cache_path: Path, *, now: Callable[[], int] | None = None,
                 post=None, sign=None):
        self.app_id, self.installation_id, self.pem = app_id, installation_id, pem
        self.cache = TokenCache(cache_path)
        self._now = now or (lambda: int(time.time()))
        self._post, self._sign = post, sign
        self.last: InstallationToken | None = None

    def token(self, force: bool = False) -> str:
        now = self._now()
        cached = self.last or self.cache.load()
        if cached is not None and not force and cached.seconds_left(now) > REFRESH_MARGIN_SECONDS:
            self.last = cached
            return cached.token
        fresh = mint_installation_token(self.app_id, self.installation_id, self.pem, now=now, post=self._post, sign=self._sign)
        self.cache.save(fresh)
        self.last = fresh
        return fresh.token
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_appauth.py -q`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/meld/appauth.py tests/test_appauth.py
git commit -m "feat: GitHub App authentication primitives (JWT via openssl, installation tokens, private cache)"
```

### Task 2: Config — `auth = "app"` per group; `MELD_APP_KEY_*` scrubbed from workers

**Files:**
- Modify: `src/meld/config.py`, `src/meld/adapters/base.py`
- Test: `tests/test_config.py`, `tests/test_adapters.py`

**Interfaces:**
- Produces: `GroupConfig.auth: str = "pat"`, `app_id: int | None = None`, `installation_id: int | None = None`, `private_key_env: str | None = None`, `private_key_file: str | None = None`; `token_env` becomes `str | None` (required only when `auth == "pat"`); `GroupConfig.is_app` property; `adapters.base.SCRUBBED_PREFIXES == ("MELD_TOKEN_", "MELD_APP_KEY_")`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_config.py`)

```python
def test_app_group_config_parses_and_validates():
    import pytest
    from meld.config import Config, ConfigError
    base = {"repo": {"name": "acme/widgets"}, "humans": {"accounts": ["empowerScott"]}}
    role = {"model": "claude-opus-5", "effort": "high"}
    app = {"cli": "claude", "bot": "meld-claude[bot]", "auth": "app", "app_id": 1234, "installation_id": 55,
           "private_key_env": "MELD_APP_KEY_CLAUDE", "orchestrator": role, "pm": role}
    cfg = Config.from_dict({**base, "groups": {"claude": app}})
    g = cfg.groups["claude"]
    assert g.is_app and g.app_id == 1234 and g.installation_id == 55 and g.private_key_env == "MELD_APP_KEY_CLAUDE" and g.token_env is None
    pat = {"cli": "codex", "bot": "meld-codex-bot", "token_env": "MELD_TOKEN_CODEX", "orchestrator": role, "pm": role}
    assert Config.from_dict({**base, "groups": {"codex": pat}}).groups["codex"].auth == "pat"
    with pytest.raises(ConfigError, match="token_env"):
        Config.from_dict({**base, "groups": {"codex": {k: v for k, v in pat.items() if k != "token_env"}}})
    with pytest.raises(ConfigError, match=r"\[bot\]"):
        Config.from_dict({**base, "groups": {"claude": {**app, "bot": "meld-claude-bot"}}})
    with pytest.raises(ConfigError, match="app_id"):
        Config.from_dict({**base, "groups": {"claude": {k: v for k, v in app.items() if k != "app_id"}}})
    with pytest.raises(ConfigError, match="exactly one"):
        Config.from_dict({**base, "groups": {"claude": {**app, "private_key_file": "/tmp/k.pem"}}})
    with pytest.raises(ConfigError, match="auth"):
        Config.from_dict({**base, "groups": {"claude": {**app, "auth": "oauth"}}})


def test_app_key_env_is_scrubbed_from_workers():
    from meld.adapters.base import SCRUBBED_PREFIXES, scrub_env
    assert "MELD_APP_KEY_" in SCRUBBED_PREFIXES
    out = scrub_env({"MELD_APP_KEY_CLAUDE": "pem", "MELD_TOKEN_CODEX": "t", "PATH": "/bin"})
    assert "MELD_APP_KEY_CLAUDE" not in out and "MELD_TOKEN_CODEX" not in out and out["PATH"] == "/bin"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -q -k "app_group or app_key"` → FAIL (`GroupConfig` has no `auth`).

- [ ] **Step 3: Implement**

`config.py` — `GroupConfig` gains, after `git_email`:

```python
    auth: str = "pat"                       # "pat" (token_env) or "app" (GitHub App installation)
    app_id: int | None = None
    installation_id: int | None = None
    private_key_env: str | None = None
    private_key_file: str | None = None

    @property
    def is_app(self) -> bool:
        return self.auth == "app"
```

and `token_env: str | None`. In `from_dict`'s group loop replace the `token_env=str(_require(g, "token_env", path))` line and add validation:

```python
            auth = str(g.get("auth", "pat"))
            if auth not in ("pat", "app"):
                raise ConfigError(f"{path}.auth must be 'pat' or 'app', got {auth!r}")
            token_env = str(g["token_env"]) if g.get("token_env") else None
            if auth == "pat" and not token_env:
                raise ConfigError(f"missing required setting {path}.token_env (or set auth = \"app\")")
            app_id = installation_id = None
            key_env = key_file = None
            bot = str(_require(g, "bot", path))
            if auth == "app":
                app_id = int(_require(g, "app_id", path))
                installation_id = int(_require(g, "installation_id", path))
                key_env = str(g["private_key_env"]) if g.get("private_key_env") else None
                key_file = str(g["private_key_file"]) if g.get("private_key_file") else None
                if bool(key_env) == bool(key_file):
                    raise ConfigError(f"{path}: an App group needs exactly one of private_key_env or private_key_file")
                if not bot.endswith("[bot]"):
                    raise ConfigError(f"{path}.bot must be the App's bot login, '<slug>[bot]', got {bot!r}")
```

and pass `bot=bot, token_env=token_env, auth=auth, app_id=app_id, installation_id=installation_id, private_key_env=key_env, private_key_file=key_file` into `GroupConfig(...)`. Check every existing use of `group.token_env` (`context.load_context`, `doctor.run_checks`, `setup.starter_toml`) still type-checks with `str | None` — Tasks 3–5 change those call sites; for now they keep working for PAT groups.

`adapters/base.py`: `SCRUBBED_PREFIXES = ("MELD_TOKEN_", "MELD_APP_KEY_")` and update the comment/docstring naming both.

- [ ] **Step 4: Run the suite; commit**

Run: `uv run pytest -q` → green (488 + 2).

```bash
git add src/meld/config.py src/meld/adapters/base.py tests/test_config.py
git commit -m "feat: groups may authenticate as a GitHub App (auth = app); App keys are scrubbed from workers"
```

### Task 3: `GitHub` token provider with refresh; `context` wires `AppAuth`

**Files:**
- Modify: `src/meld/github.py`, `src/meld/context.py`
- Test: `tests/test_github.py`, `tests/test_context.py` (create if absent)

**Interfaces:**
- Consumes: `appauth.AppAuth`, `appauth.load_pem`, `appauth.AppAuthError`, `StateStore.cache_dir`.
- Produces: `GitHub.__init__(repo, token=None, run=..., cache_dir=None, token_provider: Callable[[bool], str] | None = None)`; `GitHub.token` property (provider's current token, else the static token); `_call` retries once with `token_provider(True)` on HTTP 401 when a provider is set; `context.load_context` builds `AppAuth(...)` for App groups (cache at `store.cache_dir / "app-token.json"`) and passes `token_provider=auth.token`; `Context.app_auth: AppAuth | None`; `ConfigError` wraps `AppAuthError` at load time.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_github.py`:

```python
def test_github_uses_the_token_provider_and_refreshes_once_on_401(tmp_path):
    from tests.fakegh import FakeGh
    from meld.github import GitHub
    fake = FakeGh()
    fake.route("GET", "repos/acme/widgets/issues/1", {"number": 1})
    tokens = iter(["ghs_old", "ghs_new"])
    asked = []

    def provider(force=False):
        asked.append(force)
        return next(tokens) if force or not asked[:-1] else "ghs_old"

    gh = GitHub("acme/widgets", run=fake, token_provider=provider)
    assert gh.token == "ghs_old"                       # first ask, not forced
    fake.route("GET", "repos/acme/widgets/issues/2", {"message": "Bad credentials"}, status=401)
    import pytest
    from meld.github import GitHubError
    with pytest.raises(GitHubError, match="401"):
        gh.get("repos/acme/widgets/issues/2", use_cache=False)
    assert True in asked                               # a forced refresh was requested after the 401
    assert fake.envs[-1]["GH_TOKEN"] == "ghs_new"      # and the retry carried the refreshed token
```

(The static-token path is already covered by existing tests: `GitHub(..., token="tok")` must keep setting `GH_TOKEN`.)

Create `tests/test_context.py` (or append if it exists):

```python
def test_load_context_builds_app_auth_for_an_app_group(tmp_path, keypair_pem):
    from meld.context import load_context
    from tests.fakegh import FakeGh
    from tests.conftest import load_board1_routes
    toml = (tmp_path / ".meld.toml")
    toml.write_text('''
[repo]
name = "acme/widgets"
[humans]
accounts = ["empowerScott"]
[groups.claude]
cli = "claude"
bot = "meld-claude[bot]"
auth = "app"
app_id = 1234
installation_id = 55
private_key_env = "MELD_APP_KEY_CLAUDE"
[groups.claude.orchestrator]
model = "claude-opus-5"
effort = "high"
[groups.claude.pm]
model = "claude-sonnet-5"
effort = "medium"
''')
    fake = FakeGh()
    load_board1_routes(fake)
    minted = []

    def post(url, headers, body):
        minted.append(url)
        return 201, {"token": "ghs_ctx", "expires_at": "2999-01-01T00:00:00Z", "permissions": {}, "repositories": []}

    import meld.context as context_mod
    env = {"MELD_HOME": str(tmp_path / "home"), "MELD_GROUP": "claude", "MELD_ROLE": "pm", "MELD_APP_KEY_CLAUDE": keypair_pem.replace("\n", "\\n")}
    ctx = load_context(toml, env=env, run=fake, adapter_for=lambda n: None, app_post=post)
    assert ctx.actor_login == "meld-claude[bot]" and ctx.app_auth is not None
    assert ctx.gh.token == "ghs_ctx" and minted == ["https://api.github.com/app/installations/55/access_tokens"]
    assert (tmp_path / "home" / "cache" / "claude" / "app-token.json").exists()
```

Add a session-scoped `keypair_pem` fixture to `tests/conftest.py` that runs `openssl genrsa 2048` once (reuse the shape of `tests/test_appauth.py`'s fixture; move that fixture into conftest so both files share it).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_github.py tests/test_context.py -q -k "provider or app_auth"` → FAIL (`unexpected keyword argument 'token_provider'`).

- [ ] **Step 3: Implement**

`github.py`:

```python
class GitHub:
    def __init__(self, repo: str, token: str | None = None, run: RunFn = subprocess.run, cache_dir: Path | None = None,
                 token_provider: Callable[[bool], str] | None = None):
        self.repo = repo
        self._token = token
        self._provider = token_provider
        ...

    @property
    def token(self) -> str | None:
        """The credential every call carries: the provider's current installation token for an App group, else the static PAT."""
        return self._provider(False) if self._provider else self._token
```

In `_call`, replace the `env["GH_TOKEN"] = self.token` block with a small inner function that runs the request with a given token, then:

```python
        token = self.token
        status, resp_headers, parsed = self._request(cmd, body, token)
        if status == 401 and self._provider is not None:
            # An installation token expired between the provider's freshness check and the call: refresh once and retry.
            status, resp_headers, parsed = self._request(cmd, body, self._provider(True))
        return status, resp_headers, parsed
```

where `_request` contains the existing subprocess call plus status/header parsing (the rate-limit check stays inside it). Keep `self.token` assignments elsewhere working: grep for `gh.token =` (none expected) and for `GitHub(` constructions (`context.load_context`, `cli.setup`, tests) — all keep the positional/keyword `token=`.

`context.py`: `Context` gains `app_auth: AppAuth | None = None`; `load_context(..., app_post=None)`:

```python
    token: str | None = None
    provider = None
    app_auth: AppAuth | None = None
    if party.group:
        g = cfg.groups[party.group]
        if g.is_app:
            try:
                pem = load_pem(env, g.private_key_env, g.private_key_file)
            except AppAuthError as e:
                raise ConfigError(f"[groups.{g.name}]: {e}") from e
            app_auth = AppAuth(g.app_id, g.installation_id, pem, store.cache_dir / "app-token.json", post=app_post)  # type: ignore[arg-type]
            provider = app_auth.token
        else:
            token = env.get(g.token_env or "")
            if not token:
                raise ConfigError(f"{g.token_env} is not set in the environment")
    gh = GitHub(cfg.repo_name, token=token, run=run, cache_dir=store.cache_dir, token_provider=provider)
```

and pass `app_auth=app_auth` into `Context(...)`. A mint failure at first use surfaces as `AppAuthError` from `gh.token`; add `AppAuthError` to `cli._guard`'s tuple so it prints cleanly.

- [ ] **Step 4: Run the suite; commit**

Run: `uv run pytest -q` → green.

```bash
git add src/meld/github.py src/meld/context.py src/meld/cli.py tests/test_github.py tests/test_context.py tests/conftest.py tests/test_appauth.py
git commit -m "feat: the GitHub client takes a token provider; App groups mint installation tokens at load time"
```

### Task 4: `meld doctor` verifies App groups end to end

**Files:**
- Modify: `src/meld/doctor.py`
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `appauth.load_pem`, `appauth.app_slug`, `appauth.mint_installation_token`, `appauth.AppAuthError`, `GroupConfig.is_app`.
- Produces: `run_checks(..., app_post=None, app_get=None)` — for an App group the checks `token:<g>` and `token-permissions:<g>` are replaced by `app:<g>` (PEM readable, JWT signs, `GET /app` slug + `[bot]` == configured `bot`), `app-installation:<g>` (installation token mints; `permissions` grant `contents/issues/pull_requests/statuses` = `write`, `checks` reported; `repositories` include the product repo or the installation covers all repos), plus `openssl` (the CLI is on PATH) once per run; `git-identity:<g>` unchanged (`GET users/<slug>[bot]` resolves).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_doctor.py`; reuse the conftest `keypair_pem` fixture from Task 3)

```python
def app_cfg():
    text = PROJECT_TOML.replace('token_env = "MELD_TOKEN_CLAUDE"', 'auth = "app"\napp_id = 1234\ninstallation_id = 55\nprivate_key_env = "MELD_APP_KEY_CLAUDE"').replace('bot = "meld-claude-bot"', 'bot = "meld-claude[bot]"')
    return Config.from_dict(tomllib.loads(text))


def app_http(*, slug="meld-claude", perms=None, repos=("acme/widgets",), token_status=201):
    perms = perms if perms is not None else {"contents": "write", "issues": "write", "pull_requests": "write", "statuses": "write", "checks": "write"}
    calls = []

    def get(url, headers):
        calls.append(("GET", url))
        return 200, {"slug": slug, "id": 1234}

    def post(url, headers, body):
        calls.append(("POST", url))
        return token_status, {"token": "ghs_doc", "expires_at": "2999-01-01T00:00:00Z", "permissions": perms, "repositories": [{"full_name": r} for r in repos]}

    return get, post, calls


def run_app(tmp_path, by_token, keypair_pem, *, cfg_=None, get=None, post=None, env=None):
    env = {"MELD_APP_KEY_CLAUDE": keypair_pem.replace("\n", "\\n"), "MELD_TOKEN_CODEX": "tok-x", **(env or {})}
    by_token["ghs_doc"] = by_token[None]
    return run_checks(cfg_ or app_cfg(), tmp_path, env=env, gh_factory=lambda token: GitHub("acme/widgets", token=token, run=by_token[token]),
                      registry=ModelRegistry.load(), adapter_for=lambda name: FakeAdapter(ceiling=600, name=name), which=lambda b: "/bin/" + b,
                      home=tmp_path / "home", user_home=tmp_path / "userhome", app_get=get, app_post=post)


def test_doctor_verifies_an_app_group(tmp_path, keypair_pem):
    by_token = fakes()
    by_token[None].route("GET", "users/meld-claude[bot]", {"login": "meld-claude[bot]", "id": 99})
    get, post, calls = app_http()
    checks = by_name(run_app(tmp_path, by_token, keypair_pem, get=get, post=post))
    assert checks["openssl"].ok
    assert checks["app:claude"].ok and "meld-claude[bot]" in checks["app:claude"].detail
    assert checks["app-installation:claude"].ok and "checks: write" in checks["app-installation:claude"].detail
    assert "token:claude" not in checks and "token-permissions:claude" not in checks
    assert checks["token:codex"].ok                      # the PAT group still gets its own checks
    assert checks["git-identity:claude"].ok and "99+meld-claude[bot]@users.noreply.github.com" in checks["git-identity:claude"].detail
    assert ("GET", "https://api.github.com/app") in calls and ("POST", "https://api.github.com/app/installations/55/access_tokens") in calls


def test_doctor_flags_a_slug_mismatch_missing_permission_and_uninstalled_repo(tmp_path, keypair_pem):
    by_token = fakes()
    by_token[None].route("GET", "users/meld-claude[bot]", {"login": "meld-claude[bot]", "id": 99})
    get, post, _ = app_http(slug="other-app", perms={"contents": "write", "issues": "write", "pull_requests": "read", "statuses": "write"}, repos=("acme/other",))
    checks = by_name(run_app(tmp_path, by_token, keypair_pem, get=get, post=post))
    assert checks["app:claude"].ok is False and "other-app[bot]" in checks["app:claude"].detail
    inst = checks["app-installation:claude"]
    assert inst.ok is False and "pull_requests: read" in inst.detail and "acme/widgets" in inst.detail


def test_doctor_reports_a_missing_key_and_a_failed_mint(tmp_path, keypair_pem):
    by_token = fakes()
    checks = by_name(run_app(tmp_path, by_token, keypair_pem, env={"MELD_APP_KEY_CLAUDE": ""}))
    assert checks["app:claude"].ok is False and "MELD_APP_KEY_CLAUDE" in checks["app:claude"].detail
    get, post, _ = app_http(token_status=401)
    checks = by_name(run_app(tmp_path, by_token, keypair_pem, get=get, post=post))
    assert checks["app-installation:claude"].ok is False and "401" in checks["app-installation:claude"].detail
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_doctor.py -q -k app` → FAIL (`unexpected keyword argument 'app_get'`).

- [ ] **Step 3: Implement**

In `run_checks` add parameters `app_post=None, app_get=None`. Before the per-group loop add the `openssl` check:

```python
    checks.append(Check("openssl", bool(which("openssl")), "openssl on PATH" if which("openssl") else "openssl not found; App groups cannot sign their JWT",
                        "" if which("openssl") else "install openssl (brew install openssl) or use PAT groups"))
```

Inside the loop, branch on `group.is_app`:

```python
        if group.is_app:
            login = None
            pem = None
            try:
                pem = load_pem(env, group.private_key_env, group.private_key_file)
                slug = app_slug(group.app_id, pem, get=app_get)  # type: ignore[arg-type]
                login = f"{slug}[bot]"
                checks.append(Check(f"app:{group.name}", login == group.bot, f"App {group.app_id} is {login}; configured bot is {group.bot}",
                                    "" if login == group.bot else f"set [groups.{group.name}].bot = \"{login}\""))
            except AppAuthError as e:
                checks.append(Check(f"app:{group.name}", False, f"App auth failed: {e}", "check private_key_env/private_key_file and app_id"))
            if pem is not None:
                try:
                    tok = mint_installation_token(group.app_id, group.installation_id, pem, post=app_post)  # type: ignore[arg-type]
                    need = {"contents": "write", "issues": "write", "pull_requests": "write", "statuses": "write"}
                    wrong = {k: tok.permissions.get(k, "none") for k, v in need.items() if tok.permissions.get(k) != v}
                    covers = not tok.repositories or cfg.repo_name in tok.repositories
                    detail = "permissions: " + ", ".join(f"{k}: {tok.permissions.get(k, 'none')}" for k in (*need, "checks"))
                    if not covers:
                        detail += f"; installation does not include {cfg.repo_name} (has: {', '.join(tok.repositories)})"
                    checks.append(Check(f"app-installation:{group.name}", not wrong and covers, detail,
                                        "" if not wrong and covers else "edit the App's permissions (Contents, Issues, Pull requests, Commit statuses: read & write; Checks optional) and install it on the repo"))
                except AppAuthError as e:
                    checks.append(Check(f"app-installation:{group.name}", False, f"installation token failed: {e}", "check installation_id (from the installation URL) and that the App is installed on the repo"))
        else:
            <the existing token:<g> block, unchanged>
```

and make the existing `token-permissions:<g>` block run only `if not group.is_app` (App permissions come from the token response). Keep `git-identity:<g>` as is — `GET users/<slug>[bot]` resolves for App bots. Import `AppAuthError, app_slug, load_pem, mint_installation_token` from `meld.appauth`. Update the module docstring (App groups).

- [ ] **Step 4: Run the suite; commit**

Run: `uv run pytest tests/test_doctor.py -q && uv run pytest -q` → green.

```bash
git add src/meld/doctor.py tests/test_doctor.py
git commit -m "feat: meld doctor verifies App groups: key, JWT, slug, installation token permissions, repo coverage"
```

### Task 5: `meld setup` explains and emits App auth; README and spec

**Files:**
- Modify: `src/meld/setup.py`, `README.md`, `docs/superpowers/specs/2026-09-03-meld-design.md`
- Test: `tests/test_setup.py`

**Interfaces:**
- Produces: `starter_toml(..., app: bool = False)` — with `app=True` the group blocks are written in App form (`auth = "app"`, `app_id = 0`, `installation_id = 0`, `private_key_env = "MELD_APP_KEY_<G>"`, `bot = "meld-<name>[bot]"`) and the PAT lines are omitted; with `app=False` (default) the PAT form is written and an App-form block is appended commented out; `setup(..., app=False)` passes it through and its `next` steps explain App creation when `app` is true; CLI `meld setup --app`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_setup.py`)

```python
def test_starter_toml_app_form_and_commented_alternative():
    registry = ModelRegistry.load()
    text = starter_toml("acme/widgets", "main", "empowerScott", ["claude", "codex"], registry, app=True)
    data = tomllib.loads(text)
    g = data["groups"]["claude"]
    assert g["auth"] == "app" and g["bot"] == "meld-claude[bot]" and g["private_key_env"] == "MELD_APP_KEY_CLAUDE" and "token_env" not in g
    assert g["app_id"] == 0 and g["installation_id"] == 0
    pat = starter_toml("acme/widgets", "main", "empowerScott", ["claude"], registry)
    assert tomllib.loads(pat)["groups"]["claude"]["token_env"] == "MELD_TOKEN_CLAUDE"
    assert '# auth = "app"' in pat and '# private_key_env = "MELD_APP_KEY_CLAUDE"' in pat


def test_setup_app_flag_writes_app_form_and_next_steps(tmp_path):
    fake = FakeGh()
    route_repo(fake)
    fake.route("GET", R + "labels", [])
    fake.route("POST", R + "labels", {"name": "x"}, status=201)
    fake.route("GET", R + "milestones", [])
    fake.route("POST", R + "milestones", {"number": 1, "title": "M1"}, status=201)
    fake.route("GET", R + "issues", [])
    fake.route("POST", R + "issues", {"number": 2}, status=201)
    out = setup(gh_for(fake), repo="acme/widgets", goal="g", milestone_title="M1", human="empowerScott", target_dir=tmp_path,
                registry=ModelRegistry.load(), which=lambda b: "/bin/" + b, protect=False, app=True)
    assert 'auth = "app"' in (tmp_path / ".meld.toml").read_text()
    assert any("github.com/settings/apps/new" in step for step in out["next"]) and any("installation_id" in step for step in out["next"])
```

- [ ] **Step 2: Run to verify they fail** → `TypeError: unexpected keyword argument 'app'`.

- [ ] **Step 3: Implement**

`_group_block(name, registry, commented, app=False)`: when `app` is true emit `bot = "meld-{name}[bot]"`, `auth = "app"`, `app_id = 0`, `installation_id = 0`, `private_key_env = "MELD_APP_KEY_{NAME}"` instead of `token_env`; when false, keep the PAT lines and append, after the role tables, the commented alternative:

```python
        lines += [f"# To run this group as a GitHub App instead of a machine user (no extra GitHub account), replace bot/token_env with:",
                  f'# bot = "meld-{name}[bot]"', '# auth = "app"', "# app_id = 0", "# installation_id = 0", f'# private_key_env = "MELD_APP_KEY_{name.upper()}"']
```

`starter_toml(..., app=False)` threads it through; `setup(..., app=False)` too, and when `app` is true the `next` list becomes:

```python
        next_steps = [
            "for each group, create a GitHub App at https://github.com/settings/apps/new named meld-<group>: Homepage any URL, Webhook OFF, "
            "Repository permissions: Contents, Issues, Pull requests, Commit statuses (read & write), Checks (read & write, optional); Where can it be installed: only this account",
            "generate a private key on the App page and store the .pem in 1Password; note the App ID",
            f"install the App on {repo} (Install App → only select repositories); the installation_id is the number at the end of the installation URL",
            f"fill app_id and installation_id in {config_path.name} and export MELD_APP_KEY_<GROUP> with the PEM contents (op run injects it)",
            "run `meld doctor` to verify: it prints the App's bot login to put in [groups.<g>].bot if it differs",
            "run `meld run <group>` for each group, then `meld chat`",
        ]
```

CLI: `--app` flag on `meld setup` → `app=app`. README Quick start step 2 gains the App alternative in one sentence ("or, to avoid extra GitHub accounts, `meld setup --app` and follow its printed steps to create one GitHub App per group"). Spec: §5 config example gains the App form for one group; §18 gains a bullet describing App auth (JWT via openssl, installation tokens cached 0600, `MELD_APP_KEY_*` scrubbed); §20 step 1 offers the App path; §21 "Carried into plan 6" removes the App item and adds "App creation via the manifest flow".

- [ ] **Step 4: Run the suite; commit**

```bash
git add src/meld/setup.py src/meld/cli.py README.md docs/superpowers/specs/2026-09-03-meld-design.md tests/test_setup.py
git commit -m "feat: meld setup --app writes App-form config and prints the App creation steps; spec and README document App identities"
```

### Task 6: Neutral check runs for App reviews; handoff

**Files:**
- Modify: `src/meld/github.py`, `src/meld/review.py`, `docs/HANDOFF.md`
- Test: `tests/test_review.py`

**Interfaces:**
- Produces: `GitHub.create_check_run(name, head_sha, conclusion, title, summary) -> dict` (`POST repos/<r>/check-runs` with `status: "completed"`); `review.review` calls it after `create_status` when `ctx.app_auth is not None` (never for PAT groups; failures are contained like `status_error`, reported as `check_run_error`).

- [ ] **Step 1: Write the failing test** (append to `tests/test_review.py`; reuse the file's approve-path arrangement)

```python
def test_app_groups_publish_a_neutral_check_run_alongside_the_status(tmp_path):
    fake, ctx = _review_ready(tmp_path)          # the existing approve happy-path arrangement, extracted into a helper if inline
    from meld.appauth import AppAuth, InstallationToken
    ctx.app_auth = AppAuth(1, 1, "unused", tmp_path / "t.json", post=lambda u, h, b: (201, {"token": "ghs", "expires_at": "2999-01-01T00:00:00Z", "permissions": {}, "repositories": []}))
    fake.route("POST", "repos/acme/widgets/check-runs", {"id": 9}, status=201)
    out = review(ctx, 20, now=NOW)
    run = [c for c in fake.calls if c.method == "POST" and c.endpoint.endswith("check-runs")]
    assert len(run) == 1 and run[0].body["name"] == "meld/review/codex" and run[0].body["conclusion"] == "neutral" and run[0].body["head_sha"] == out["head"]
    assert "check_run_error" not in out
```

Also assert in an existing PAT-path test that no `check-runs` POST happens.

- [ ] **Step 2: Run to verify it fails** → `AttributeError: create_check_run` / no POST.

- [ ] **Step 3: Implement**

`github.py`:

```python
    def create_check_run(self, name: str, head_sha: str, conclusion: str, title: str, summary: str) -> dict:
        """Requires App authentication (GitHub refuses check runs from personal tokens)."""
        return self.post(self._r("check-runs"), {"name": name, "head_sha": head_sha, "status": "completed", "conclusion": conclusion,
                                                 "output": {"title": title[:255], "summary": summary[:65535]}})
```

`review.py`, in both the ordinary and memory paths right after the `create_status` try/except: `if ctx.app_auth is not None: try: ctx.gh.create_check_run(status_context(group.name), sha, "neutral", f"{group.name} review: {verdict}", body[:60000]) except GitHubError as e: out["check_run_error"] = str(e)` (compose the dict so the key is added only on failure). `Context.app_auth` exists from Task 3.

`docs/HANDOFF.md`: milestone line (plan 6a on branch `plan-6a-github-app-identity`, test count), repo map (`appauth.py`), "Decisions for the owner" (Decisions 1–10 above, each with the alternative not taken), "What is next" (plan 5b Tasks 11–13 resume on `plan-5-setup-doctor-skills`; plan 6b candidates: manifest-flow App creation; login-status checks per CLI).

- [ ] **Step 4: Run the suite; commit**

```bash
git add src/meld/github.py src/meld/review.py docs/HANDOFF.md tests/test_review.py
git commit -m "feat: App groups publish a neutral check run with each review; handoff for plan 6a"
```

---

## Self-review

- **Spec coverage:** §5 config (Task 2, Task 5), §6 one login per group (Decision 1, Task 2's `[bot]` rule), §13 check runs (Task 6), §18 secrets/isolation (Tasks 1–3: temp-file signing, 0600 cache, `MELD_APP_KEY_` scrub), §20 bootstrap (Task 5), doctor (Task 4).
- **Type consistency:** `mint_installation_token(app_id, installation_id, pem, *, now, post, sign) -> InstallationToken`; `post(url, headers, body) -> (status, dict)`; `get(url, headers) -> (status, dict)`; `AppAuth(app_id, installation_id, pem, cache_path, *, now, post, sign).token(force=False)`; `GitHub(repo, token, run, cache_dir, token_provider)` with `token_provider(force: bool) -> str`; `run_checks(..., app_post, app_get)`; `starter_toml(..., app)`; `setup(..., app)`; `create_check_run(name, head_sha, conclusion, title, summary)` — used identically wherever they appear.
- **Placeholders:** Task 6's `_review_ready` names the existing approve arrangement in `tests/test_review.py`; the implementer extracts it if inline (the plan says so). No other stand-ins.
