import json
import os
import sys
import time
from pathlib import Path

from click.testing import CliRunner

import meld.cli as cli
from meld.adapters import UnknownAdapter
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
    _touch(home / "runs" / "claude" / "12-gitconfig", text="")                                    # no 12.json: orphan
    (home / "runs" / "claude" / "12-gh").mkdir(parents=True)
    (home / "worktrees" / "claude" / "memory-dec-9").mkdir(parents=True)                          # crash debris
    _touch(home / "state" / "claude" / "pm" / "ci-red.json", text='{"20": {"head": "h", "commented": true, "swapped": true}, "23": {"head": "h", "commented": true, "swapped": false}}')
    states = {20: "closed", 22: "merged", 23: "open"}
    out = sweep(home, cfg(), repo_root=tmp_path, pull_states=states, older_than_days=7)
    kinds = {(c["kind"], Path(c["path"]).name) for c in out["candidates"]}
    assert ("superseded", "review-20-1.json") in kinds and ("submitted", "review-21-abc-1.json") not in kinds
    assert ("memory-review-record", "review-22.json") in kinds and ("memory-review-record", "review-23.json") not in kinds
    assert ("run-scratch", "12-gitconfig") in kinds and ("run-scratch", "12-gh") in kinds and ("run-scratch", "11-gh") not in kinds
    assert ("memory-worktree", "memory-dec-9") in kinds
    assert ("ci-red-record", "20") in kinds and ("ci-red-record", "23") not in kinds
    assert out["dry_run"] is True and out["removed"] == []
    assert (home / "results" / "claude" / "superseded" / "review-20-1.json").exists()
    applied = sweep(home, cfg(), repo_root=tmp_path, pull_states=states, older_than_days=7, apply=True)
    assert sorted(applied["removed"]) == sorted(c["path"] for c in out["candidates"])
    assert not (home / "results" / "claude" / "superseded" / "review-20-1.json").exists()
    assert not (home / "runs" / "claude" / "12-gh").exists() and (home / "runs" / "claude" / "11-gh").exists()
    assert not (home / "worktrees" / "claude" / "memory-dec-9").exists()
    remaining = json.loads((home / "state" / "claude" / "pm" / "ci-red.json").read_text())
    assert set(remaining) == {"23"}


def test_sweep_without_board_state_skips_pr_dependent_kinds(tmp_path):
    home = tmp_path / "home"
    _touch(home / "results" / "claude" / "review-22.json", text='{"head_sha": "x", "event": "APPROVE"}')
    out = sweep(home, cfg(), repo_root=tmp_path, pull_states=None)
    assert out["candidates"] == [] and "board" in " ".join(out["skipped"])


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
