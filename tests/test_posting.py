import json

import pytest
from click.testing import CliRunner

import moot.cli as cli
from moot.context import load_context
from moot.posting import PostError, post
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.fakegh import FakeGh

R = "repos/acme/widgets/"
BASE_COMMENTS = [
    {"id": 101, "user": {"login": "moot-claude-bot"}, "created_at": "2026-09-03T09:00:00Z", "body": "[claude] ROSTER\n\nbackend"},
]


def make(tmp_path, group="claude", role="orchestrator", comments=None, approved=True):
    (tmp_path / ".moot.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    if comments is not None:
        fake.route("GET", R + "issues/10/comments", comments)
    if not approved:
        issues = json.loads((__import__("pathlib").Path(__file__).parent / "fixtures/board1/issues.json").read_text())
        for i in issues:
            if i["number"] == 10:
                i["labels"] = [{"name": "moot:coordination"}]
        fake.route("GET", R + "issues", issues)
    fake.route("POST", R + "issues/10/comments", {"id": 555, "html_url": "https://github.com/acme/widgets/issues/10#issuecomment-555"})
    ctx = load_context(tmp_path / ".moot.toml", env=env_for(tmp_path, group=group, role=role), run=fake)
    return fake, ctx


def posted(fake):
    return [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint.endswith("/comments")]


def test_post_simple_verb(tmp_path):
    fake, ctx = make(tmp_path)
    out = post(ctx, "DRIFT", text="Scope moved: #12 now includes SSO.\n\nverified-by: moot status")
    assert out["comment_id"] == 555 and out["header"] == "[claude] DRIFT"
    assert posted(fake)[0].startswith("[claude] DRIFT\n\nScope moved")


def test_verb_not_allowed_for_party(tmp_path):
    _, pm = make(tmp_path, role="pm")
    with pytest.raises(PostError, match="DRIFT is not a verb \\[claude:pm\\] may post"):
        post(pm, "DRIFT", text="x")
    _, human = make(tmp_path, group=None)
    with pytest.raises(PostError, match="humans cannot post \\[joint\\]"):
        post(human, "PLAN", ref="plan-1", joint=True, text="x")


def test_validation_errors_surface(tmp_path):
    _, ctx = make(tmp_path)
    with pytest.raises(PostError, match="CHALLENGE requires a ref"):
        post(ctx, "CHALLENGE", text="doubt")
    with pytest.raises(PostError, match="EVIDENCE requires a verified-by"):
        post(ctx, "EVIDENCE", ref="#12", text="trust me")


def test_joint_plan_when_agreed_during_planning(tmp_path):
    comments = BASE_COMMENTS + [
        {"id": 102, "user": {"login": "moot-claude-bot"}, "created_at": "2026-09-03T09:10:00Z",
         "body": "[claude] PROPOSE plan-1\n\n1. health endpoint\n2. login form\n\nverified-by: moot roster"},
        {"id": 103, "user": {"login": "moot-codex-bot"}, "created_at": "2026-09-03T09:11:00Z", "body": "[codex] AGREE plan-1\n\nok"},
    ]
    fake, ctx = make(tmp_path, comments=comments, approved=False)
    out = post(ctx, "PLAN", ref="plan-1", joint=True)
    assert out["header"] == "[joint] PLAN plan-1"
    body = posted(fake)[0]
    assert "1. health endpoint" in body and "verified-by: moot roster" in body


def test_joint_refused_when_open_or_not_proposer_or_wrong_phase(tmp_path):
    open_comments = BASE_COMMENTS + [
        {"id": 102, "user": {"login": "moot-claude-bot"}, "created_at": "2026-09-03T09:10:00Z",
         "body": "[claude] PROPOSE plan-1\n\nplan\n\nverified-by: x"},
    ]
    _, ctx = make(tmp_path, comments=open_comments, approved=False)
    with pytest.raises(PostError, match="agreement on plan-1 is open"):
        post(ctx, "PLAN", ref="plan-1", joint=True)
    agreed = open_comments + [{"id": 103, "user": {"login": "moot-codex-bot"}, "created_at": "2026-09-03T09:11:00Z", "body": "[codex] AGREE plan-1\n\nok"}]
    _, codex = make(tmp_path, group="codex", comments=agreed, approved=False)
    with pytest.raises(PostError, match="only the proposer \\(claude\\)"):
        post(codex, "PLAN", ref="plan-1", joint=True)
    _, executing = make(tmp_path, comments=agreed, approved=True)
    with pytest.raises(PostError, match="PLAN may only be posted during planning"):
        post(executing, "PLAN", ref="plan-1", joint=True)


def test_joint_disagree_requires_qualifier_and_lists_positions(tmp_path):
    comments = BASE_COMMENTS + [
        {"id": 102, "user": {"login": "moot-codex-bot"}, "created_at": "2026-09-03T09:10:00Z",
         "body": "[codex] DRAFT ask-1\n\nUse Postgres.\n\nverified-by: docs"},
        {"id": 103, "user": {"login": "moot-claude-bot"}, "created_at": "2026-09-03T09:11:00Z",
         "body": "[claude] DISSENT ask-1\n\nSQLite is enough for v1."},
    ]
    fake, codex = make(tmp_path, group="codex", comments=comments)
    with pytest.raises(PostError, match="no majority; post with qualifier DISAGREE"):
        post(codex, "ANSWER", ref="ask-1", joint=True)
    out = post(codex, "ANSWER", ref="ask-1", qualifier="DISAGREE", joint=True, text="verified-by: moot status")
    assert out["header"] == "[joint] ANSWER ask-1 DISAGREE"
    body = posted(fake)[0]
    assert "## Positions" in body and "Use Postgres." in body and "SQLite is enough" in body


def test_amend_cap_enforced(tmp_path):
    comments = BASE_COMMENTS + [
        {"id": 102, "user": {"login": "moot-claude-bot"}, "created_at": "2026-09-03T09:10:00Z", "body": "[claude] PROPOSE plan-1\n\nv1"},
        {"id": 103, "user": {"login": "moot-codex-bot"}, "created_at": "2026-09-03T09:11:00Z", "body": "[codex] AMEND plan-1\n\nv2"},
        {"id": 104, "user": {"login": "moot-claude-bot"}, "created_at": "2026-09-03T09:12:00Z", "body": "[claude] AMEND plan-1\n\nv3"},
        {"id": 105, "user": {"login": "moot-codex-bot"}, "created_at": "2026-09-03T09:13:00Z", "body": "[codex] AMEND plan-1\n\nv4"},
    ]
    _, ctx = make(tmp_path, comments=comments)
    with pytest.raises(PostError, match="amend cap \\(3\\) reached"):
        post(ctx, "AMEND", ref="plan-1", text="v5")
    assert post(ctx, "AGREE", ref="plan-1", text="fine")["header"] == "[claude] AGREE plan-1"


def test_reproposal_is_refused(tmp_path):
    comments = BASE_COMMENTS + [
        {"id": 102, "user": {"login": "moot-codex-bot"}, "created_at": "2026-09-03T09:10:00Z", "body": "[codex] PROPOSE plan-1\n\nv1"},
    ]
    _, ctx = make(tmp_path, comments=comments)
    with pytest.raises(PostError, match="already has a proposal; post AMEND"):
        post(ctx, "PROPOSE", ref="plan-1", text="v2")


def test_cli_post_with_message_and_error_exit(tmp_path, monkeypatch):
    (tmp_path / ".moot.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    fake.route("POST", R + "issues/10/comments", {"id": 7, "html_url": "u"})
    monkeypatch.setattr(cli, "RUN", fake)
    ok = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".moot.toml"), "--compact", "post", "PING", "-m", "anyone there?"],
                            env=env_for(tmp_path, role="orchestrator"))
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.output)["header"] == "[claude] PING"
    bad = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".moot.toml"), "--compact", "post", "CHALLENGE", "-m", "x"],
                             env=env_for(tmp_path, role="orchestrator"))
    assert bad.exit_code == 2 and "requires a ref" in json.loads(bad.output)["error"]
