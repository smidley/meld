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
from typing import Callable, Mapping, Sequence

API = "https://api.github.com"


def api_base() -> str:
    """Where the App calls go: GitHub, or the rehearsal's stand-in when `MELD_REHEARSAL_GITHUB` names it (plan 48)."""
    return os.environ.get("MELD_REHEARSAL_GITHUB") or API
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
            try:
                return resp.status, json.loads(resp.read().decode() or "{}")
            except ValueError:
                return resp.status, {}
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
                            repositories: Sequence[str] | None = None,
                            post: Callable[[str, dict[str, str], bytes | None], tuple[int, dict]] | None = None,
                            sign: Callable[[bytes, str], bytes] | None = None) -> InstallationToken:
    """`repositories` are bare repository NAMES (`widgets`, not `acme/widgets`). Passing them scopes the token to
    those repositories, which is both least privilege and the only way the response's own `repositories` list is
    evidence of coverage: an unscoped mint may answer with no list at all."""
    jwt = jwt_for_app(app_id, pem, now=now, sign=sign)
    url = f"{api_base()}/app/installations/{installation_id}/access_tokens"
    headers = _bearer(jwt)
    body: bytes | None = None
    if repositories:
        body = json.dumps({"repositories": list(repositories)}).encode()
        headers = {**headers, "Content-Type": "application/json"}
    status, data = (post or (lambda u, h, b: _http("POST", u, h, b)))(url, headers, body)
    message = data.get("message", "") if isinstance(data, dict) else ""
    if status == 422 and repositories:
        # GitHub's answer when a requested repository is not part of this installation.
        raise AppAuthError(f"installation {installation_id} does not cover {', '.join(repositories)} (HTTP 422: {message})")
    if status not in (200, 201) or not isinstance(data, dict) or not data.get("token"):
        raise AppAuthError(f"installation token request failed with HTTP {status}: {message or 'no token in response'}")
    granted = data.get("repositories") or []
    repos = tuple(str(r.get("full_name", "")) for r in granted if isinstance(r, dict)) if isinstance(granted, list) else ()
    permissions = data.get("permissions") or {}
    permissions = permissions if isinstance(permissions, dict) else {}
    return InstallationToken(str(data["token"]), str(data.get("expires_at", "")), permissions, repos)


def app_slug(app_id: int, pem: str, *, now: int | None = None, get: Callable[[str, dict[str, str]], tuple[int, dict]] | None = None,
             sign: Callable[[bytes, str], bytes] | None = None) -> str:
    """The App's slug (its bot login is `<slug>[bot]`), from GET /app authenticated as the App."""
    jwt = jwt_for_app(app_id, pem, now=now, sign=sign)
    status, data = (get or (lambda u, h: _http("GET", u, h, None)))(f"{api_base()}/app", _bearer(jwt))
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
    def __init__(self, path: Path, app_id: int | None = None, installation_id: int | None = None):
        self.path = path
        self.app_id, self.installation_id = app_id, installation_id   # the identity a cached token must have been minted for

    def load(self) -> InstallationToken | None:
        try:
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict) or not isinstance(data.get("token"), str) or not isinstance(data.get("expires_at"), str):
                return None
            # Plan 46: a token minted for another App is not this group's. The first real run re-created a group's App,
            # the file named the new one, and the roles kept posting under the old one from this cache for an hour.
            if self.app_id is not None and (data.get("app_id"), data.get("installation_id")) != (self.app_id, self.installation_id):
                return None
            permissions = data.get("permissions", {})
            if not isinstance(permissions, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in permissions.items()):
                raise ValueError("cached permissions is not a dict of str to str")
            repositories = data.get("repositories", [])
            if not isinstance(repositories, list) or not all(isinstance(r, str) for r in repositories):
                raise ValueError("cached repositories is not a list of str")
            return InstallationToken(data["token"], data["expires_at"], permissions, tuple(repositories))
        except (OSError, ValueError, TypeError):
            return None

    def save(self, tok: InstallationToken) -> None:
        # A group's orchestrator and PM share $MELD_HOME/cache/<group>, so two refreshes can land at once: the temp
        # file must be unique per save, or the second saver's os.replace moves the first saver's file out from under
        # it (the first then dies on FileNotFoundError). mkstemp creates it 0600 and never reuses a name.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix="app-token-", suffix=".tmp")
        tmp = Path(name)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"token": tok.token, "expires_at": tok.expires_at, "permissions": tok.permissions, "repositories": list(tok.repositories),
                           "app_id": self.app_id, "installation_id": self.installation_id}, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


class AppAuth:
    """Token provider for one App group: cached installation token, re-minted with REFRESH_MARGIN_SECONDS to spare."""

    def __init__(self, app_id: int, installation_id: int, pem: str, cache_path: Path, *, now: Callable[[], int] | None = None,
                 post=None, sign=None, repositories: Sequence[str] | None = None):
        self.app_id, self.installation_id, self.pem = app_id, installation_id, pem
        self.cache = TokenCache(cache_path, app_id, installation_id)
        self._now = now or (lambda: int(time.time()))
        self._post, self._sign = post, sign
        self.repositories = tuple(repositories or ())
        self.last: InstallationToken | None = None

    def permissions(self) -> dict[str, str]:
        """The installation's granted permissions as the last mint reported them (plan 62): the cached token's, else a
        fresh one's. Empty when nothing is known."""
        try:
            self.token(False)
        except AppAuthError:
            return {}
        return dict((self.last or self.cache.load() or InstallationToken("", "", {}, ())).permissions)

    def token(self, force: bool = False) -> str:
        now = self._now()
        cached = self.last or self.cache.load()
        if cached is not None and not force and cached.seconds_left(now) > REFRESH_MARGIN_SECONDS:
            self.last = cached
            return cached.token
        fresh = mint_installation_token(self.app_id, self.installation_id, self.pem, now=now, repositories=self.repositories or None,
                                        post=self._post, sign=self._sign)
        self.cache.save(fresh)
        self.last = fresh
        return fresh.token
