import base64
import json
import os
import subprocess
from datetime import datetime, timezone

import pytest

from meld.appauth import AppAuth, AppAuthError, InstallationToken, TokenCache, app_slug, jwt_for_app, load_pem, mint_installation_token, sign_rs256

NOW = 1_800_000_000


@pytest.fixture(scope="module")
def keypair(keypair_pem, tmp_path_factory):
    """(pem, pub_path) for this module: the private key comes from the session-scoped keypair_pem fixture
    (shared with tests/test_context.py); only the public key still needs deriving here, for the tests below
    that verify signatures against it."""
    d = tmp_path_factory.mktemp("keys")
    pub = d / "pub.pem"
    subprocess.run(["openssl", "rsa", "-pubout", "-out", str(pub)], input=keypair_pem, capture_output=True, text=True, check=True)
    return keypair_pem, pub


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
    assert headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert body is None and "Content-Type" not in headers          # unscoped: no body at all


def test_mint_scopes_the_token_to_the_named_repositories(keypair):
    """Final-review Important 2: the mint asks for the product repo by NAME, so the response's `repositories` is
    evidence of coverage rather than an unscoped token's silence."""
    pem, _ = keypair
    calls = []

    def post(url, headers, body):
        calls.append((headers, body))
        return 201, {"token": "ghs_abc", "expires_at": "2027-01-01T00:00:00Z", "permissions": {},
                     "repositories": [{"full_name": "acme/widgets"}]}

    tok = mint_installation_token(1234, 55, pem, now=NOW, repositories=["widgets"], post=post)
    headers, body = calls[0]
    assert json.loads(body) == {"repositories": ["widgets"]}
    assert headers["Content-Type"] == "application/json"
    assert tok.repositories == ("acme/widgets",)


def test_mint_turns_a_422_on_a_scoped_request_into_a_coverage_error(keypair):
    """Final-review Important 2: GitHub answers 422 when a requested repository is not part of the installation."""
    pem, _ = keypair

    def post(url, headers, body):
        return 422, {"message": "There is at least one repository that does not exist or is not accessible"}

    with pytest.raises(AppAuthError) as e:
        mint_installation_token(1234, 55, pem, now=NOW, repositories=["widgets"], post=post)
    assert "does not cover widgets" in str(e.value) and "55" in str(e.value) and "eyJ" not in str(e.value)


def test_app_auth_scopes_every_mint_to_its_repositories(keypair, tmp_path):
    pem, _ = keypair
    bodies = []

    def post(url, headers, body):
        bodies.append(body)
        return 201, {"token": f"ghs_{len(bodies)}", "expires_at": "2027-01-01T00:00:00Z", "permissions": {},
                     "repositories": [{"full_name": "acme/widgets"}]}

    auth = AppAuth(1234, 55, pem, tmp_path / "app-token.json", now=lambda: NOW, post=post, repositories=["widgets"])
    assert auth.token() == "ghs_1" and auth.token(force=True) == "ghs_2"
    assert [json.loads(b) for b in bodies] == [{"repositories": ["widgets"]}] * 2


def test_mint_installation_token_treats_null_repositories_as_empty(keypair):
    pem, _ = keypair

    def post(url, headers, body):
        return 201, {"token": "ghs_abc", "expires_at": "2027-01-01T00:00:00Z", "permissions": {}, "repositories": None}

    tok = mint_installation_token(1234, 55, pem, now=NOW, post=post)
    assert tok == InstallationToken("ghs_abc", "2027-01-01T00:00:00Z", {}, ())


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


def test_token_cache_save_survives_a_concurrent_save_in_the_same_directory(tmp_path, monkeypatch):
    """Final-review Minor 6: an orchestrator and its PM share $MELD_HOME/cache/<group>, so two refreshes can save at
    once. With a fixed sibling temp name the second saver's os.replace moves the first saver's file out from under it
    and the first raises FileNotFoundError. Each save must own a unique temp file."""
    import meld.appauth as appauth
    a = TokenCache(tmp_path / "app-token.json")
    b = TokenCache(tmp_path / "app-token.json")
    real_replace = os.replace
    reentered = []

    def replace_racing(src, dst):
        if not reentered:                     # the other process completes its whole save mid-flight, exactly once
            reentered.append(1)
            b.save(InstallationToken("ghs_b", "2027-01-01T00:00:00Z", {}, ()))
        return real_replace(src, dst)

    monkeypatch.setattr(appauth.os, "replace", replace_racing)
    a.save(InstallationToken("ghs_a", "2027-01-01T00:00:00Z", {}, ()))
    assert a.load().token == "ghs_a"                                   # the later saver won, and neither crashed
    assert oct(os.stat(a.path).st_mode & 0o777) == "0o600"
    assert list(tmp_path.glob("*.tmp")) == []                          # no temp file left behind


def test_token_cache_save_removes_its_temp_file_when_the_replace_fails(tmp_path, monkeypatch):
    import meld.appauth as appauth

    def boom(src, dst):
        raise OSError("no space left on device")

    monkeypatch.setattr(appauth.os, "replace", boom)
    cache = TokenCache(tmp_path / "app-token.json")
    with pytest.raises(OSError):
        cache.save(InstallationToken("ghs_1", "2027-01-01T00:00:00Z", {}, ()))
    assert list(tmp_path.glob("*.tmp")) == [] and not cache.path.exists()


def test_token_cache_load_tolerates_a_file_written_before_repositories_existed(tmp_path):
    """Final-review Important 2: caches written by an earlier meld carry no `repositories` key; they stay usable."""
    cache = TokenCache(tmp_path / "app-token.json")
    cache.path.write_text(json.dumps({"token": "t", "expires_at": "2027-01-01T00:00:00Z", "permissions": {"contents": "write"}}))
    tok = cache.load()
    assert tok is not None and tok.token == "t" and tok.repositories == ()


def test_token_cache_load_returns_none_when_permissions_is_the_wrong_shape(tmp_path):
    cache = TokenCache(tmp_path / "app-token.json")
    cache.path.write_text(json.dumps({"token": "t", "expires_at": "2027-01-01T00:00:00Z", "permissions": "oops"}))
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
