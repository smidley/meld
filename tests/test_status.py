import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.config import ConfigError, Party
from meld.context import find_config, load_context
from meld.rules import Actor
from meld.status import render_status
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.factories import CFG2, REG, mk_board, mk_pr
from tests.fakeadapter import no_skills
from tests.fakegh import FakeGh

# Task 8's audit-schedule test needs actor fixtures; mirrors the local CLAUDE_PM/CODEX_PM/HUMAN
# constants already duplicated in tests/test_rules_issues.py and tests/test_rules_prs.py (this
# repo defines them per test file rather than sharing them from tests/factories.py).
CLAUDE_PM = Actor(Party("pm", "claude"), "meld-claude-bot")
CODEX_PM = Actor(Party("pm", "codex"), "meld-codex-bot")
HUMAN = Actor(Party("human"), "empowerScott")


@pytest.fixture
def project(tmp_path: Path):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    (tmp_path / "sub").mkdir()
    return tmp_path


def test_find_config_walks_up(project: Path):
    assert find_config(project / "sub") == project / ".meld.toml"
    with pytest.raises(ConfigError):
        find_config(Path("/"))


def test_load_context_requires_token_for_groups(project: Path):
    fake = FakeGh()
    env = env_for(project)
    del env["MELD_TOKEN_CLAUDE"]
    with pytest.raises(ConfigError, match="MELD_TOKEN_CLAUDE"):
        load_context(project / ".meld.toml", env=env, run=fake, adapter_for=no_skills)


def test_load_context_refuses_to_run_inside_a_worker(project: Path):
    fake = FakeGh()
    fake.route("GET", "user", {"login": "empowerScott"})
    for env in (env_for(project), env_for(project, group=None)):
        with pytest.raises(ConfigError, match="meld may not be run by a worker"):
            load_context(project / ".meld.toml", env={**env, "MELD_WORKER": "1"}, run=fake, adapter_for=no_skills)
    assert fake.calls == []  # refused before touching GitHub


def test_load_context_human_uses_viewer_login(project: Path):
    fake = FakeGh()
    fake.route("GET", "user", {"login": "empowerScott"})
    ctx = load_context(project / ".meld.toml", env=env_for(project, group=None), run=fake, adapter_for=no_skills)
    assert ctx.party.kind == "human" and ctx.actor_login == "empowerScott" and ctx.gh.token is None


def test_status_command_reports_board_and_legal_actions(project: Path, monkeypatch):
    fake = FakeGh()
    load_board1_routes(fake)
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(project / ".meld.toml"), "--compact", "status"], env=env_for(project))
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["party"] == "[claude:pm]" and data["actor"] == "meld-claude-bot" and data["phase"] == "executing"
    assert data["quorum"] == 1
    issues = {i["number"]: i for i in data["issues"]}
    assert issues[11]["legal"] == ["claim"]
    assert issues[12]["challenged"] and "dispatch" not in issues[12]["legal"] and "release" in issues[12]["legal"]
    assert issues[14]["status"] == "needs_human"
    assert issues[12]["lease"] is None
    assert data["needs_human"][0]["issue"] == 14 and data["needs_human"][0]["age_minutes"] > 0
    pulls = {p["number"]: p for p in data["pulls"]}
    assert pulls[20]["committer_party"] == "codex" and pulls[20]["approvals"] == ["claude"]
    assert pulls[20]["legal"] == ["approve", "request-changes", "push-fix", "merge", "review"]
    assert pulls[19]["state"] == "closed" and pulls[19]["legal"] == []
    assert data["coordination"]["open_challenges"] == ["#12"] and data["coordination"]["ignored_comments"] == 1
    assert data["budget"]["cap"] == {"small": 200, "medium": 80, "large": 20}
    assert "digest" in data
    assert data["state_errors"] == []


def test_status_reports_leases(project: Path, monkeypatch):
    fake = FakeGh()
    load_board1_routes(fake)
    comments = json.loads((Path(__file__).parent / "fixtures" / "board1" / "comments_10.json").read_text())
    comments.append({"id": 110, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:20:00Z",
                     "body": "[claude:pm] LEASE #12\n\nuntil: 2000-01-01T00:00:00Z"})
    fake.route("GET", "repos/acme/widgets/issues/10/comments", comments)
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(project / ".meld.toml"), "--compact", "status"], env=env_for(project))
    data = json.loads(result.output)
    lease = {i["number"]: i for i in data["issues"]}[12]["lease"]
    assert lease == {"group": "claude", "until": "2000-01-01T00:00:00Z", "expired": True}

    # A lease on an issue that has since left in-progress (e.g. moved to in-review)
    # must not be reported: the lease only governs who may RELEASE an in-progress claim.
    issues = json.loads((Path(__file__).parent / "fixtures" / "board1" / "issues.json").read_text())
    for i in issues:
        if i["number"] == 12:
            i["labels"] = [{"name": "meld:in-review"} if l["name"] == "meld:in-progress" else l for l in i["labels"]]
    fake.route("GET", "repos/acme/widgets/issues", issues)
    result2 = CliRunner().invoke(cli.main, ["--config", str(project / ".meld.toml"), "--compact", "status"], env=env_for(project))
    data2 = json.loads(result2.output)
    assert {i["number"]: i for i in data2["issues"]}[12]["lease"] is None


def test_status_as_orchestrator_cannot_claim(project: Path, monkeypatch):
    fake = FakeGh()
    load_board1_routes(fake)
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(project / ".meld.toml"), "--compact", "status"],
                                env=env_for(project, role="orchestrator"))
    data = json.loads(result.output)
    assert {i["number"]: i["legal"] for i in data["issues"]}[11] == ["tag"]


def test_status_as_human_uses_gh_identity(project: Path, monkeypatch):
    fake = FakeGh()
    load_board1_routes(fake)
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(project / ".meld.toml"), "--compact", "status"],
                                env=env_for(project, group=None))
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["party"] == "[human]" and data["actor"] == "empowerScott"
    assert {i["number"]: i["legal"] for i in data["issues"]}[11] == ["claim", "tag"]


def test_gh_failure_is_a_clean_cli_error(project: Path, monkeypatch):
    fake = FakeGh()  # no "user" route: gh returns 404 -> GitHubError from viewer_login
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(project / ".meld.toml"), "--compact", "status"],
                                env=env_for(project, group=None))
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Error:" in result.output and "GET user" in result.output


def test_models_command_lists_adapter_models(project: Path, monkeypatch):
    fake = FakeGh()
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(project / ".meld.toml"), "--compact", "models"], env=env_for(project))
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["adapter"] == "claude"
    assert any(m["id"] == "claude-sonnet-5" for m in data["models"])


def test_orchestrator_and_pm_split_state_but_share_the_cache(project: Path):
    fake = FakeGh()
    load_board1_routes(fake)
    orch = load_context(project / ".meld.toml", env=env_for(project, role="orchestrator"), run=fake, adapter_for=no_skills)
    pm = load_context(project / ".meld.toml", env=env_for(project, role="pm"), run=fake, adapter_for=no_skills)
    human = load_context(project / ".meld.toml", env=env_for(project, group=None), run=fake, adapter_for=no_skills)
    assert orch.store.state_dir != pm.store.state_dir
    assert orch.store.state_dir.name == "orchestrator" and pm.store.state_dir.name == "pm"
    assert orch.store.cache_dir == pm.store.cache_dir
    assert human.store.state_dir == human.store.home / "state" / "human"


def test_status_tolerates_an_unknown_head_committer(project: Path, monkeypatch):
    fake = FakeGh()
    load_board1_routes(fake)
    fake.route("GET", "repos/acme/widgets/pulls/20/commits", [{"sha": "abc123", "author": None, "committer": None}])
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(project / ".meld.toml"), "--compact", "status"], env=env_for(project))
    assert result.exit_code == 0, result.output
    pulls = {p["number"]: p for p in json.loads(result.output)["pulls"]}
    assert pulls[20]["committer_party"] is None
    assert pulls[20]["legal"] == ["request-changes", "push-fix", "review"]


def test_models_command_lists_every_adapter_for_a_human(project: Path, monkeypatch):
    fake = FakeGh()
    fake.route("GET", "user", {"login": "empowerScott"})
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(project / ".meld.toml"), "--compact", "models"],
                                env=env_for(project, group=None))
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "adapter" not in data
    assert [a["adapter"] for a in data["adapters"]] == ["claude", "codex"]
    assert any(m["id"] == "claude-sonnet-5" for m in data["adapters"][0]["models"])
    assert data["adapters"][1]["models"], "codex models are listed too"


def test_models_command_still_honours_an_explicit_adapter_for_a_human(project: Path, monkeypatch):
    fake = FakeGh()
    fake.route("GET", "user", {"login": "empowerScott"})
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(project / ".meld.toml"), "--compact", "models",
                                           "--adapter", "codex"],
                                env=env_for(project, group=None))
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["adapter"] == "codex"


def test_status_reports_the_audit_schedule_for_the_calling_group():
    merged = replace(mk_pr(19, 9, committer="meld-claude-bot", state="closed", merged=True), merged_at="2026-09-03T06:00:00Z")
    board = mk_board(CFG2, pulls=[merged])
    now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    codex = render_status(board, CFG2, CODEX_PM, REG, now)["audit"]
    assert codex == {"pending": [19], "merges_pending": 1, "oldest_pending_hours": 6.0, "due": True}
    claude = render_status(board, CFG2, CLAUDE_PM, REG, now)["audit"]
    assert claude == {"pending": [], "merges_pending": 0, "oldest_pending_hours": None, "due": False}
    fresh = replace(merged, merged_at="2026-09-03T11:30:00Z")
    assert render_status(mk_board(CFG2, pulls=[fresh]), CFG2, CODEX_PM, REG, now)["audit"]["due"] is False
    done = replace(board, audits={19: {"codex": "pass"}})
    assert render_status(done, CFG2, CODEX_PM, REG, now)["audit"]["pending"] == []
    assert render_status(board, CFG2, HUMAN, REG, now)["audit"]["pending"] == []
