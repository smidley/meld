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
