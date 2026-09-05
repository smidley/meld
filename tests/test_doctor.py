import json

from click.testing import CliRunner

import meld.cli as cli
from meld.config import Config
from meld.doctor import run_checks, summary
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
    (home / ".claude" / "settings.json").write_text(json.dumps({"env": {"MELD_TOKEN_CLAUDE": "leak", "OTHER": "x"}}))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"env": {"GH_TOKEN": "leak"}}))
    slow = {"claude": FakeAdapter(ceiling=100, name="claude"), "codex": FakeAdapter(ceiling=600, name="codex")}
    checks = by_name(run(tmp_path, fakes(statuses_ok=False), adapters=slow, user_home=home))
    assert checks["settings-env"].ok is None and "MELD_TOKEN_CLAUDE" in checks["settings-env"].detail and "GH_TOKEN" in checks["settings-env"].detail
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
