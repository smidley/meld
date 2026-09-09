import json
import os
import sys
import time
from pathlib import Path

from click.testing import CliRunner

import meld.cli as cli
from meld.adapters import UnknownAdapter
from meld.appauth import AppAuthError
from meld.config import Config
from meld.doctor import run_checks, summary, sweep
from meld.github import GitHub
from meld.models import ModelRegistry
from tests.conftest import PROJECT_TOML
from tests.fakeadapter import FakeAdapter
from tests.fakegh import FakeGh

import tomllib

R = "repos/acme/widgets/"


def cfg():
    return Config.from_dict(tomllib.loads(PROJECT_TOML))


def fakes(*, claude_login="meld-claude-bot", codex_login="meld-codex-bot", human="empowerScott", statuses_ok=True):
    by_token: dict[str | None, FakeGh] = {}
    for token, login in ((None, human), ("tok-c", claude_login), ("tok-x", codex_login)):
        f = FakeGh()
        f.route("GET", "user", {"login": login, "id": 7})
        f.route("GET", "users/meld-claude-bot", {"login": "meld-claude-bot", "id": 11})
        f.route("GET", "users/meld-codex-bot", {"login": "meld-codex-bot", "id": 12})
        f.route("GET", R + "branches/main", {"commit": {"sha": "abc123"}})
        f.route("GET", R + "assignees/meld-claude-bot", None, status=204)
        f.route("GET", R + "assignees/meld-codex-bot", None, status=204)
        f.route("GET", R + "issues", [])
        f.route("GET", R + "pulls", [])
        if statuses_ok:
            f.route("GET", R + "commits/abc123/status", {"state": "pending", "statuses": []})
        else:
            f.route("GET", R + "commits/abc123/status", {"message": "Resource not accessible"}, status=403)
        f.route("POST", R + "statuses/abc123", {"id": 1}, status=201)
        by_token[token] = f
    return by_token


def run(tmp_path, by_token, *, env=None, which=lambda b: "/bin/" + b, registry=None, probe_status=False, adapters=None, user_home=None):
    env = {"MELD_TOKEN_CLAUDE": "tok-c", "MELD_TOKEN_CODEX": "tok-x", **(env or {})}
    adapters = adapters or {"claude": FakeAdapter(ceiling=600, name="claude"), "codex": FakeAdapter(ceiling=600, name="codex")}
    return run_checks(cfg(), tmp_path, env=env, gh_factory=lambda token: GitHub("acme/widgets", token=token, run=by_token[token]),
                      registry=registry or ModelRegistry.load(),
                      adapter_for=lambda name: adapters[name], which=which, home=tmp_path / "home", probe_status=probe_status,
                      user_home=user_home or tmp_path / "userhome")


def by_name(checks):
    return {c.name: c for c in checks}


def test_doctor_passes_a_healthy_setup(tmp_path):
    checks = by_name(run(tmp_path, fakes()))
    assert checks["token:claude"].ok and "meld-claude-bot" in checks["token:claude"].detail
    assert checks["gh-login"].ok and checks["cli:claude"].ok and checks["git-identity:codex"].ok
    assert checks["timeout-ceiling:claude"].ok and checks["token-permissions:claude"].ok
    assert checks["models:claude"].ok
    # PROJECT_TOML's codex model is the deliberately-invalid "gpt-x" (shared with the unknown-model test below), so
    # even an otherwise healthy setup fails models:codex against any real registry; nothing else fails or warns as a failure.
    assert summary(run(tmp_path, fakes()))["failed"] == ["models:codex"]


def test_doctor_checks_every_bot_login_is_an_assignable_user(tmp_path):
    """Final-review Critical 1: the claim lock is the issue assignee, and GitHub validates assignees against the
    repo's assignable users -- so a bot login that is not assignable can never claim. Every group is checked, PAT
    and App alike, through the human's own gh login (assignability is a property of the repository)."""
    checks = by_name(run(tmp_path, fakes()))
    assert checks["assignable:claude"].ok is True and "meld-claude-bot" in checks["assignable:claude"].detail
    by_token = fakes()
    del by_token[None].routes[("GET", R + "assignees/meld-codex-bot")]      # 404: not a collaborator
    checks = by_name(run(tmp_path, by_token))
    assert checks["assignable:codex"].ok is False and "collaborator" in checks["assignable:codex"].fix


def test_doctor_fails_when_a_token_belongs_to_the_wrong_account(tmp_path):
    checks = by_name(run(tmp_path, fakes(codex_login="someone-else")))
    assert checks["token:codex"].ok is False and "someone-else" in checks["token:codex"].detail and "meld-codex-bot" in checks["token:codex"].detail


def test_doctor_flags_missing_tokens_binaries_and_unknown_models(tmp_path):
    checks = by_name(run(tmp_path, fakes(), env={"MELD_TOKEN_CODEX": ""}, which=lambda b: None))
    assert checks["token:codex"].ok is False and "MELD_TOKEN_CODEX" in checks["token:codex"].detail
    assert checks["cli:claude"].ok is False
    assert checks["models:codex"].ok is False and "gpt-x" in checks["models:codex"].detail   # PROJECT_TOML's codex model is not in REG


def test_doctor_warns_on_settings_env_and_permissions_and_ceilings(tmp_path):
    home = tmp_path / "userhome"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(json.dumps({"env": {"MELD_TOKEN_CLAUDE": "leak", "MELD_APP_KEY_CLAUDE": "leak", "OTHER": "x"}}))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"env": {"GH_TOKEN": "leak"}}))
    slow = {"claude": FakeAdapter(ceiling=100, name="claude"), "codex": FakeAdapter(ceiling=600, name="codex")}
    checks = by_name(run(tmp_path, fakes(statuses_ok=False), adapters=slow, user_home=home))
    assert checks["settings-env"].ok is None and "MELD_TOKEN_CLAUDE" in checks["settings-env"].detail and "GH_TOKEN" in checks["settings-env"].detail
    assert "MELD_APP_KEY_CLAUDE" in checks["settings-env"].detail       # an App key re-injected into a worker is the same leak
    assert checks["token-permissions:claude"].ok is False and "Commit statuses" in checks["token-permissions:claude"].detail
    assert checks["timeout-ceiling:claude"].ok is False and "300" in checks["timeout-ceiling:claude"].detail


def test_doctor_probe_status_posts_once_per_group_only_when_asked(tmp_path):
    by_token = fakes()
    run(tmp_path, by_token)
    assert not [c for c in by_token["tok-c"].calls if c.method == "POST"]
    run(tmp_path, by_token, probe_status=True)
    posts = [c for c in by_token["tok-c"].calls if c.method == "POST" and "statuses/abc123" in c.endpoint]
    assert len(posts) == 1 and posts[0].body["context"] == "meld/doctor" and posts[0].body["state"] == "success"


def test_doctor_warns_when_memory_is_large_and_origin_is_not_https(tmp_path):
    (tmp_path / ".meld" / "memory").mkdir(parents=True)
    (tmp_path / ".meld" / "memory" / "lessons.md").write_text("x" * (2 * 1024 * 1024 + 1))
    checks = by_name(run(tmp_path, fakes()))
    assert checks["memory-size"].ok is None and "2 MB" in checks["memory-size"].detail
    assert checks["origin-https"].ok is None   # tmp_path is not a git repo: reported as a warning, never a crash


def test_cli_doctor_reports_token_mismatch_and_exits_1(tmp_path, monkeypatch):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    monkeypatch.setenv("MELD_TOKEN_CLAUDE", "tok-c")
    monkeypatch.setenv("MELD_TOKEN_CODEX", "tok-x")
    # All gh calls in the CLI path go through one RUN, so the same (None-token) FakeGh answers `GET user` for the
    # human channel and for both group tokens alike: every viewer_login() sees the human, not the configured bot.
    monkeypatch.setattr(cli, "RUN", fakes()[None])
    result = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "doctor"])
    assert result.exit_code == 1, result.output
    data = json.loads(result.output)
    assert "token:claude" in data["failed"] and "token:codex" in data["failed"]


def _touch(path: Path, age_days: float = 0.0, text: str = "{}"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(path, (old, old))


def test_sweep_lists_then_removes_only_what_it_listed(tmp_path):
    home = tmp_path / "home"
    _touch(home / "results" / "claude" / "superseded" / "review-20-1.json", age_days=10)
    _touch(home / "results" / "claude" / "submitted" / "review-21-abc-1.json", age_days=1)      # too young
    _touch(home / "results" / "claude" / "review-22.json", text='{"head_sha": "x", "event": "APPROVE"}')  # memory-PR record, PR merged
    _touch(home / "results" / "claude" / "review-23.json", text='{"head_sha": "x", "event": "APPROVE"}')  # PR still open
    _touch(home / "runs" / "claude" / "11.json", text='{"issue": 11}')
    _touch(home / "runs" / "claude" / "11-gitconfig", text="")
    (home / "runs" / "claude" / "11-gh").mkdir(parents=True)
    _touch(home / "runs" / "claude" / "12-gitconfig", text="", age_days=1)                        # no 12.json: orphan, old enough to sweep
    (home / "runs" / "claude" / "12-gh").mkdir(parents=True)
    old = time.time() - 1 * 86400
    os.utime(home / "runs" / "claude" / "12-gh", (old, old))                                      # _touch only backdates files; a dir needs its own utime
    _touch(home / "runs" / "claude" / "13-gitconfig", text="")                                    # no 13.json either, but fresh: not a candidate yet
    (home / "runs" / "claude" / "13-gh").mkdir(parents=True)
    (home / "worktrees" / "claude" / "memory-dec-9").mkdir(parents=True)                          # crash debris
    os.utime(home / "worktrees" / "claude" / "memory-dec-9", (old, old))                          # old enough to sweep
    (home / "worktrees" / "claude" / "memory-dec-10").mkdir(parents=True)                         # a memory PR still mid-commit: too fresh to touch
    _touch(home / "state" / "claude" / "pm" / "ci-red.json", text='{"20": {"head": "h", "commented": true, "swapped": true}, "23": {"head": "h", "commented": true, "swapped": false}}')
    states = {20: "closed", 22: "merged", 23: "open"}
    out = sweep(home, cfg(), repo_root=tmp_path, pull_states=states, older_than_days=7)
    kinds = {(c["kind"], Path(c["path"]).name) for c in out["candidates"]}
    assert ("superseded", "review-20-1.json") in kinds and ("submitted", "review-21-abc-1.json") not in kinds
    assert ("memory-review-record", "review-22.json") in kinds and ("memory-review-record", "review-23.json") not in kinds
    assert ("run-scratch", "12-gitconfig") in kinds and ("run-scratch", "12-gh") in kinds and ("run-scratch", "11-gh") not in kinds
    assert ("run-scratch", "13-gitconfig") not in kinds and ("run-scratch", "13-gh") not in kinds     # fresh: too young to be a worker-starting-now false positive
    assert ("memory-worktree", "memory-dec-9") in kinds and ("memory-worktree", "memory-dec-10") not in kinds     # fresh: may still be mid-commit
    assert ("ci-red-record", "20") in kinds and ("ci-red-record", "23") not in kinds
    assert out["dry_run"] is True and out["removed"] == []
    assert (home / "results" / "claude" / "superseded" / "review-20-1.json").exists()
    applied = sweep(home, cfg(), repo_root=tmp_path, pull_states=states, older_than_days=7, apply=True)
    assert sorted(applied["removed"]) == sorted(c["path"] for c in out["candidates"])
    assert not (home / "results" / "claude" / "superseded" / "review-20-1.json").exists()
    assert not (home / "runs" / "claude" / "12-gh").exists() and (home / "runs" / "claude" / "11-gh").exists()
    assert (home / "runs" / "claude" / "13-gh").exists() and (home / "runs" / "claude" / "13-gitconfig").exists()
    assert not (home / "worktrees" / "claude" / "memory-dec-9").exists()
    assert (home / "worktrees" / "claude" / "memory-dec-10").exists()
    remaining = json.loads((home / "state" / "claude" / "pm" / "ci-red.json").read_text())
    assert set(remaining) == {"23"}


def test_sweep_without_board_state_skips_pr_dependent_kinds(tmp_path):
    home = tmp_path / "home"
    _touch(home / "results" / "claude" / "review-22.json", text='{"head_sha": "x", "event": "APPROVE"}')
    out = sweep(home, cfg(), repo_root=tmp_path, pull_states=None)
    assert out["candidates"] == [] and "board" in " ".join(out["skipped"])


def test_sweep_apply_skips_a_ci_red_record_whose_file_became_a_list(tmp_path, monkeypatch):
    """Review fix, Minor 1: the state file can change shape between listing and apply (someone else rewrote it, or
    unrelated corruption) -- apply must not crash, and must report the candidate as skipped rather than silently
    claiming it as removed."""
    home = tmp_path / "home"
    ci_red = home / "state" / "claude" / "pm" / "ci-red.json"
    original_text = '{"20": {"head": "h", "commented": true, "swapped": true}}'
    _touch(ci_red, text=original_text)
    states = {20: "closed"}
    real_loads = json.loads
    calls = {"n": 0}

    def flaky_loads(s, *a, **kw):
        if s == original_text:
            calls["n"] += 1
            if calls["n"] > 1:      # 1st call is sweep's own listing phase; every call after is apply's re-read
                return []
        return real_loads(s, *a, **kw)
    monkeypatch.setattr(json, "loads", flaky_loads)
    out = sweep(home, cfg(), repo_root=tmp_path, pull_states=states, apply=True)
    assert len(out["candidates"]) == 1 and out["candidates"][0]["kind"] == "ci-red-record"
    assert out["removed"] == []
    assert any(str(ci_red) in s for s in out["skipped"])


def test_sweep_apply_does_not_follow_a_memory_worktree_symlink(tmp_path):
    """Review fix, Minor 2: shutil.rmtree(..., ignore_errors=True) silently does nothing to a symlink (it refuses
    to follow one, and ignore_errors swallows that refusal) -- apply must confirm removal actually happened before
    counting a memory-worktree candidate as removed, and must never touch what the symlink points at."""
    home = tmp_path / "home"
    external = tmp_path / "outside-home" / "real-target"
    external.mkdir(parents=True)
    (external / "marker.txt").write_text("keep me")
    old = time.time() - 1 * 86400
    os.utime(external, (old, old))                                     # sweep stats through the symlink to its target
    link = home / "worktrees" / "claude" / "memory-x"
    link.parent.mkdir(parents=True)
    link.symlink_to(external, target_is_directory=True)
    out = sweep(home, cfg(), repo_root=tmp_path, pull_states=None, apply=True)
    kinds = {(c["kind"], Path(c["path"]).name) for c in out["candidates"]}
    assert ("memory-worktree", "memory-x") in kinds
    assert out["removed"] == []
    assert any(str(link) in s for s in out["skipped"])
    assert link.exists() and external.exists() and (external / "marker.txt").exists()


def test_doctor_cli_check_distinguishes_found_but_broken_from_missing(tmp_path):
    """Task 5 review tidy-up: `which` finding the binary but `--version` failing is a different problem than the
    binary being absent, and the detail text must say so rather than reusing "not found on PATH"."""
    class BrokenVersionAdapter(FakeAdapter):
        def version_argv(self):
            return [sys.executable, "-c", "import sys; sys.exit(1)"]
    adapters = {"claude": BrokenVersionAdapter(ceiling=600, name="claude"), "codex": FakeAdapter(ceiling=600, name="codex")}
    checks = by_name(run(tmp_path, fakes(), adapters=adapters))
    assert checks["cli:claude"].ok is False
    assert checks["cli:claude"].detail == f"{sys.executable}: found but --version failed"


def test_doctor_reports_unknown_cli_and_skips_dependent_checks_for_that_group(tmp_path):
    """Task 5 review tidy-up: an unrecognized [groups.<g>].cli must not raise out of `run_checks` -- it fails
    cli:<g> with a clear reason, skips the adapter-dependent timeout-ceiling:<g> check (as a warning, not a
    failure), and leaves every other check for that group (and every check for other groups) unaffected."""
    bad_cfg = Config.from_dict(tomllib.loads(PROJECT_TOML.replace('cli = "codex"', 'cli = "bogus"')))

    def adapter_for(name):
        if name == "bogus":
            raise UnknownAdapter(name)
        return FakeAdapter(ceiling=600, name=name)
    by_token = fakes()
    checks = by_name(run_checks(bad_cfg, tmp_path, env={"MELD_TOKEN_CLAUDE": "tok-c", "MELD_TOKEN_CODEX": "tok-x"},
                                gh_factory=lambda token: GitHub("acme/widgets", token=token, run=by_token[token]),
                                registry=ModelRegistry.load(), adapter_for=adapter_for, which=lambda b: "/bin/" + b,
                                home=tmp_path / "home", user_home=tmp_path / "userhome"))
    assert checks["cli:codex"].ok is False and checks["cli:codex"].detail == "unknown cli 'bogus' in .meld.toml"
    assert checks["timeout-ceiling:codex"].ok is None and "skipped" in checks["timeout-ceiling:codex"].detail
    # Independent checks for the same group still ran -- an unknown cli does not abort the rest of the group.
    assert checks["token:codex"].ok is True
    # The other group is entirely unaffected.
    assert checks["cli:claude"].ok is True and checks["timeout-ceiling:claude"].ok is True


def app_cfg():
    text = PROJECT_TOML.replace('token_env = "MELD_TOKEN_CLAUDE"', 'auth = "app"\napp_id = 1234\ninstallation_id = 55\nprivate_key_env = "MELD_APP_KEY_CLAUDE"').replace('bot = "meld-claude-bot"', 'bot = "meld-claude[bot]"')
    return Config.from_dict(tomllib.loads(text))


def app_http(*, slug="meld-claude", perms=None, repos=("acme/widgets",), token_status=201, token_body=None):
    perms = perms if perms is not None else {"contents": "write", "issues": "write", "pull_requests": "write", "statuses": "write", "checks": "write"}
    calls = []

    def get(url, headers):
        calls.append(("GET", url))
        return 200, {"slug": slug, "id": 1234}

    def post(url, headers, body):
        calls.append(("POST", url, body))
        if token_body is not None:
            return token_status, token_body
        return token_status, {"token": "ghs_doc", "expires_at": "2999-01-01T00:00:00Z", "permissions": perms, "repositories": [{"full_name": r} for r in repos]}

    return get, post, calls


def run_app(tmp_path, by_token, keypair_pem, *, cfg_=None, get=None, post=None, env=None):
    env = {"MELD_APP_KEY_CLAUDE": keypair_pem.replace("\n", "\\n"), "MELD_TOKEN_CODEX": "tok-x", **(env or {})}
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
    assert ("GET", "https://api.github.com/app") in calls
    # Final-review Important 2: the mint is scoped to the product repo by name, so the response proves coverage.
    posts = [c for c in calls if c[0] == "POST"]
    assert posts and posts[0][1] == "https://api.github.com/app/installations/55/access_tokens"
    assert json.loads(posts[0][2]) == {"repositories": ["widgets"]}


def test_doctor_fails_the_installation_check_when_the_scoped_mint_is_refused(tmp_path, keypair_pem):
    """Final-review Important 2: a 422 means the installation does not include the product repo -- a failure with a
    detail that names the repo, never a silent pass."""
    by_token = fakes()
    get, post, _ = app_http(token_status=422, token_body={"message": "There is at least one repository that does not exist or is not accessible"})
    checks = by_name(run_app(tmp_path, by_token, keypair_pem, get=get, post=post))
    inst = checks["app-installation:claude"]
    assert inst.ok is False and "does not cover widgets" in inst.detail


def test_doctor_warns_when_the_app_has_no_checks_permission(tmp_path, keypair_pem):
    """Final-review Minor 8: Checks was documented as "optional", but without it every review this group publishes
    records check_run_error. A warning, not a failure -- the review itself still lands."""
    by_token = fakes()
    get, post, _ = app_http(perms={"contents": "write", "issues": "write", "pull_requests": "write", "statuses": "write"})
    checks = by_name(run_app(tmp_path, by_token, keypair_pem, get=get, post=post))
    inst = checks["app-installation:claude"]
    assert inst.ok is None and "check_run_error" in inst.detail and "Checks" in inst.fix


def test_doctor_warns_when_the_mint_returns_no_repository_list(tmp_path, keypair_pem):
    """Final-review Important 2: absent evidence is a warning, never a pass -- "no list" used to map to "covers"."""
    by_token = fakes()
    get, post, _ = app_http(repos=())
    checks = by_name(run_app(tmp_path, by_token, keypair_pem, get=get, post=post))
    inst = checks["app-installation:claude"]
    assert inst.ok is None and "unverified" in inst.detail and "acme/widgets" in inst.detail


def test_doctor_says_an_app_bot_cannot_hold_the_assignee_claim_lock(tmp_path, keypair_pem):
    """Final-review Critical 1: `GET /repos/{repo}/assignees/{login}` 404s for an App's bot user (probe 2026-09-09),
    so the check fails for an App group and its fix text names the limitation and plan 6b rather than a collaborator
    invite that cannot exist. The login is percent-encoded: a bare `[bot]` would not survive the URL."""
    by_token = fakes()
    by_token[None].route("GET", "users/meld-claude[bot]", {"login": "meld-claude[bot]", "id": 99})
    get, post, _ = app_http()
    checks = by_name(run_app(tmp_path, by_token, keypair_pem, get=get, post=post))
    assert checks["assignable:claude"].ok is False
    assert "cannot claim and build" in checks["assignable:claude"].fix and "plan 6b" in checks["assignable:claude"].fix
    assert any(c.endpoint == R + "assignees/meld-claude%5Bbot%5D" for c in by_token[None].calls)
    assert checks["assignable:codex"].ok is True           # the PAT group in the same config is unaffected


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


def test_cli_doctor_sweep_tolerates_an_app_auth_failure_while_building_board_state(tmp_path, monkeypatch):
    """Task 3 review tidy-up: `doctor --sweep` builds board state through `load_context`, which for an App group
    can raise AppAuthError (e.g. a bad key or a failed installation-token mint reached while resolving the
    group's GitHub client). That must fall back to pull_states=None like BoardError/GitHubError/ConfigError
    already do, not crash the whole invocation."""
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    monkeypatch.setenv("MELD_HOME", str(tmp_path / "home"))

    def _raise(*a, **kw):
        raise AppAuthError("installation token failed")
    monkeypatch.setattr(cli, "load_context", _raise)
    result = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "doctor", "--sweep", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["dry_run"] is True
