import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.board import Challenge
from meld.config import ConfigError, Party
from meld.context import find_config, load_context
from meld.rules import Actor
from meld.status import render_status
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.factories import CFG2, REG, mk_board, mk_comment, mk_issue, mk_pr
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


def test_refuse_worker_is_the_one_refusal_every_verb_shares(project: Path):
    """`meld tail` needs no board, so it never calls load_context; it calls this helper directly instead of
    carrying a second copy of the check (cli.tail, tests/test_cli.py)."""
    from meld.context import refuse_worker
    with pytest.raises(ConfigError, match="meld may not be run by a worker"):
        refuse_worker({"MELD_WORKER": "1"})
    assert refuse_worker({}) is None and refuse_worker({"MELD_WORKER": "0"}) is None


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
    assert issues[11]["legal"] == ["claim", "remember"]
    assert issues[12]["challenged"] and "dispatch" not in issues[12]["legal"] and "release" in issues[12]["legal"]
    assert issues[12]["claimed_by"] == "claude" and issues[12]["claimants"] == ["claude"]
    assert issues[14]["status"] == "needs_human"
    assert issues[12]["lease"] is None
    assert data["needs_human"][0]["issue"] == 14 and data["needs_human"][0]["age_minutes"] > 0
    pulls = {p["number"]: p for p in data["pulls"]}
    assert pulls[20]["committer_party"] == "codex" and pulls[20]["approvals"] == ["claude"]
    # review is not listed: claude already approved this head, and a group gets one review slot per head
    assert pulls[20]["legal"] == ["approve", "request-changes", "push-fix", "merge", "remember"]
    fake.route("GET", "repos/acme/widgets/pulls/20/reviews", [])   # no verdict on the head yet: review is offered
    again = CliRunner().invoke(cli.main, ["--config", str(project / ".meld.toml"), "--compact", "status"], env=env_for(project))
    assert "review" in {p["number"]: p for p in json.loads(again.output)["pulls"]}[20]["legal"]
    assert pulls[19]["state"] == "closed" and pulls[19]["legal"] == ["remember"]
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
    assert {i["number"]: i["legal"] for i in data["issues"]}[11] == ["tag", "remember"]


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
    assert pulls[20]["legal"] == ["request-changes", "push-fix", "remember"]   # claude already approved this head


def test_status_reports_an_unreadable_commit_status_in_state_errors(project: Path, monkeypatch):
    fake = FakeGh()
    load_board1_routes(fake)
    fake.route("GET", "repos/acme/widgets/commits/abc123/status", {"message": "Resource not accessible by personal access token"}, status=403)
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(project / ".meld.toml"), "--compact", "status"], env=env_for(project))
    assert result.exit_code == 0, result.output                    # every command keeps working without the permission
    data = json.loads(result.output)
    pulls = {p["number"]: p for p in data["pulls"]}
    assert pulls[20]["approvals"] == ["claude"] and "merge" in pulls[20]["legal"]   # GitHub approvals still count
    (note,) = data["state_errors"]
    assert note.startswith("PR #20: commit statuses unavailable (") and note.endswith("check the token's Commit statuses permission")


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
    # No issues are tracked on this board fixture: a milestone with no issues yet is NOT complete (planning; the vacuous
    # reading told the first live run's PM the milestone was done), and codex's batch is due on merge count/age alone.
    assert codex == {"pending": [19], "merges_pending": 1, "oldest_pending_hours": 6.0, "due": True, "milestone_complete": False}
    claude = render_status(board, CFG2, CLAUDE_PM, REG, now)["audit"]
    assert claude == {"pending": [], "merges_pending": 0, "oldest_pending_hours": None, "due": False, "milestone_complete": False}
    fresh = replace(merged, merged_at="2026-09-03T11:30:00Z")
    # An open issue keeps the milestone incomplete, so this checks the age/batch-count gate alone (spec 21 item 10
    # would otherwise make a fresh, low-volume merge due anyway once the milestone reads as finished).
    still_open = mk_board(CFG2, issues=[mk_issue(11)], pulls=[fresh])
    assert render_status(still_open, CFG2, CODEX_PM, REG, now)["audit"] == {
        "pending": [19], "merges_pending": 1, "oldest_pending_hours": 0.5, "due": False, "milestone_complete": False}
    done = replace(board, audits={19: {"codex": "pass"}})
    assert render_status(done, CFG2, CODEX_PM, REG, now)["audit"]["pending"] == []
    assert render_status(board, CFG2, HUMAN, REG, now)["audit"]["pending"] == []


def test_audit_schedule_excludes_merged_memory_prs():
    body = "Records the agreed decision `dec-1`.\n\nmeld-memory: decision dec-1 " + "a" * 64 + "\nverified-by: x"
    memory = replace(mk_pr(40, 0, committer="meld-claude-bot", state="closed", merged=True), body=body, head_ref="meld/memory-dec-1",
                     merged_at="2026-09-03T06:00:00Z")
    ordinary = replace(mk_pr(19, 9, committer="meld-claude-bot", state="closed", merged=True), merged_at="2026-09-03T06:00:00Z")
    now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    both = render_status(mk_board(CFG2, pulls=[memory, ordinary]), CFG2, CODEX_PM, REG, now)
    assert both["audit"] == {"pending": [19], "merges_pending": 1, "oldest_pending_hours": 6.0, "due": True, "milestone_complete": False}
    assert {p["number"]: p["legal"] for p in both["pulls"]} == {19: ["audit", "remember"], 40: ["remember"]}
    only_memory = render_status(mk_board(CFG2, pulls=[memory]), CFG2, CODEX_PM, REG, now)["audit"]
    assert only_memory == {"pending": [], "merges_pending": 0, "oldest_pending_hours": None, "due": False, "milestone_complete": False}
    # a marker smuggled into an ordinary build PR's body (head meld/9-slug) does not exempt it from audit (N1)
    smuggled = replace(ordinary, body=body)
    assert render_status(mk_board(CFG2, pulls=[smuggled]), CFG2, CODEX_PM, REG, now)["audit"]["pending"] == [19]


def test_status_pull_fields_and_challenge_details():
    cfg = replace(CFG2, security_paths=("src/auth/*",))
    pr = replace(mk_pr(20, 13), files=("src/auth/login.py",))
    ch = Challenge("#20", "codex", 104, "2026-09-03T10:04:00Z")
    board = replace(mk_board(cfg, pulls=[pr]), challenges={"#20": ch}, open_challenges=frozenset({"#20"}), audits={20: {"codex": "pass"}})
    data = render_status(board, cfg, CLAUDE_PM, REG, datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc))
    (p,) = data["pulls"]
    assert p["touches_security_paths"] is True and p["files"] == 1 and p["merged"] is False and p["merged_at"] == "" and p["audited_by"] == {"codex": "pass"}
    assert p["scope_violations"] == [] and p["blocked_deletions"] == []
    assert data["coordination"]["challenges"] == [{"ref": "#20", "challenger": "codex", "comment_id": 104, "opened_at": "2026-09-03T10:04:00Z"}]
    assert data["coordination"]["open_challenges"] == ["#20"]


def test_status_agreements_report_verification():
    thread = [mk_comment(CFG2, 1, "meld-claude-bot", "[claude] PROPOSE plan-1\n\nsplit"),
              mk_comment(CFG2, 2, "meld-codex-bot", "[codex] AGREE plan-1\n\nyes"),
              mk_comment(CFG2, 3, "meld-codex-bot", "[joint] PLAN plan-1\n\nsplit\n\nverified-by: x")]
    data = render_status(mk_board(CFG2, comments=thread), CFG2, CLAUDE_PM, REG, datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc))
    assert data["coordination"]["agreements"]["plan-1"] == {"status": "agreed", "joint_verified": False, "legal": []}


def test_status_agreements_carry_legal_actions(board1_gh, tmp_path):
    from meld.status import render_status
    from meld.rules import Actor
    from meld.config import Party
    from tests.factories import CFG2, REG, mk_board, mk_comment
    comments = [
        mk_comment(101, "meld-claude-bot", "[claude] PROPOSE dec-1\n\nUse Postgres.", "2026-09-03T10:00:00Z"),
        mk_comment(102, "meld-codex-bot", "[codex] AGREE dec-1\n\nyes", "2026-09-03T10:01:00Z"),
        mk_comment(103, "meld-claude-bot", "[joint] DECISION dec-1\n\nUse Postgres.\n\nverified-by: agreed", "2026-09-03T10:02:00Z"),
    ]
    data = render_status(mk_board(comments=comments), CFG2, Actor(Party("pm", "claude"), "meld-claude-bot"), REG)
    assert data["coordination"]["agreements"]["dec-1"]["legal"] == ["decide"]


def test_audit_is_due_when_the_milestone_is_complete():
    from datetime import datetime, timezone
    from meld.config import Party
    from meld.rules import Actor
    from meld.status import audit_summary
    from tests.factories import CFG2, mk_board, mk_issue, mk_pr
    merged = mk_pr(19, 9, author="meld-codex-bot", state="closed", merged=True)
    board = mk_board(issues={9: mk_issue(9, "ready", state="closed")}, pulls={19: merged})
    out = audit_summary(board, CFG2, Actor(Party("pm", "claude"), "meld-claude-bot"), datetime(2026, 9, 5, tzinfo=timezone.utc))
    assert out["pending"] == [19] and out["milestone_complete"] is True and out["due"] is True


def test_milestone_complete_needs_at_least_one_issue_and_all_of_them_closed():
    from meld.status import audit_summary
    now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    assert audit_summary(mk_board(CFG2), CFG2, CLAUDE_PM, now)["milestone_complete"] is False                       # planning: no issues yet
    closed = mk_issue(11, status="ready", state="closed")
    assert audit_summary(mk_board(CFG2, issues=[closed]), CFG2, CLAUDE_PM, now)["milestone_complete"] is True
    assert audit_summary(mk_board(CFG2, issues=[closed, mk_issue(12)]), CFG2, CLAUDE_PM, now)["milestone_complete"] is False


def test_audits_skip_pull_requests_authored_by_a_human():
    """The first live run audited the operator's own setup and budget PRs seven times over: a human's PR is not worker
    output and no group is accountable for it, so it is neither pending nor legal to audit."""
    from meld.rules import Verb, pr_refusal
    from meld.status import audit_summary
    human_pr = replace(mk_pr(30, 0, committer="empowerScott", author="empowerScott", state="closed", merged=True), merged_at="2026-09-03T06:00:00Z")
    bot_pr = replace(mk_pr(31, 9, committer="meld-claude-bot", state="closed", merged=True), merged_at="2026-09-03T06:00:00Z")
    board = mk_board(CFG2, pulls=[human_pr, bot_pr])
    now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    assert audit_summary(board, CFG2, CODEX_PM, now)["pending"] == [31]
    refusal = pr_refusal(board, CFG2, CODEX_PM, Verb.AUDIT, human_pr)
    assert refusal is not None and "not authored by a group" in refusal.rule
