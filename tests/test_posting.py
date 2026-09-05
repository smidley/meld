import json

import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.actions import Refused
from meld.context import load_context
from meld.posting import PostError, post
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.fakeadapter import no_skills
from tests.fakegh import FakeGh

R = "repos/acme/widgets/"
BASE_COMMENTS = [
    {"id": 101, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:00:00Z", "body": "[claude] ROSTER\n\nbackend"},
]


def make(tmp_path, group="claude", role="orchestrator", comments=None, approved=True):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    if comments is not None:
        fake.route("GET", R + "issues/10/comments", comments)
    if not approved:
        issues = json.loads((__import__("pathlib").Path(__file__).parent / "fixtures/board1/issues.json").read_text())
        for i in issues:
            if i["number"] == 10:
                i["labels"] = [{"name": "meld:coordination"}]
        fake.route("GET", R + "issues", issues)
    fake.route("POST", R + "issues/10/comments", {"id": 555, "html_url": "https://github.com/acme/widgets/issues/10#issuecomment-555"})
    ctx = load_context(tmp_path / ".meld.toml", env=env_for(tmp_path, group=group, role=role), run=fake, adapter_for=no_skills)
    return fake, ctx


def posted(fake):
    return [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint.endswith("/comments")]


def posted_headers(fake):
    return [b.splitlines()[0] for b in posted(fake)]


def test_post_simple_verb(tmp_path):
    fake, ctx = make(tmp_path)
    out = post(ctx, "DRIFT", text="Scope moved: #12 now includes SSO.\n\nverified-by: meld status")
    assert out["comment_id"] == 555 and out["header"] == "[claude] DRIFT"
    assert posted(fake)[0].startswith("[claude] DRIFT\n\nScope moved")


def test_verb_not_allowed_for_party(tmp_path):
    _, pm = make(tmp_path, role="pm")
    with pytest.raises(PostError, match="DRIFT is not a verb \\[claude:pm\\] may post"):
        post(pm, "DRIFT", text="x")
    _, human = make(tmp_path, group=None)
    with pytest.raises(PostError, match="humans cannot post \\[joint\\]"):
        post(human, "PLAN", ref="plan-1", joint=True, text="x")


def test_lease_and_expired_are_framework_only(tmp_path):
    _, pm = make(tmp_path, role="pm")
    with pytest.raises(PostError, match="LEASE is written by meld itself"):
        post(pm, "LEASE", ref="#12", text="until: 2099-01-01T00:00:00Z")
    with pytest.raises(PostError, match="EXPIRED is written by meld itself"):
        post(pm, "EXPIRED", ref="#12", text="released")
    _, orch = make(tmp_path, role="orchestrator")
    with pytest.raises(PostError, match="LEASE is written by meld itself"):
        post(orch, "LEASE", ref="#12", text="until: 2099-01-01T00:00:00Z")
    _, human = make(tmp_path, group=None)
    with pytest.raises(PostError, match="EXPIRED is written by meld itself"):
        post(human, "EXPIRED", ref="#12", text="x")


def test_cli_post_lease_is_a_usage_error(tmp_path, monkeypatch):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    fake.route("POST", R + "issues/10/comments", {"id": 7, "html_url": "u"})
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "post", "LEASE", "#12",
                                           "-m", "until: 2099-01-01T00:00:00Z"], env=env_for(tmp_path, role="pm"))
    assert result.exit_code == 2 and "never through meld post" in json.loads(result.output)["error"]
    assert posted(fake) == []


def test_validation_errors_surface(tmp_path):
    _, ctx = make(tmp_path)
    # CHALLENGE and EVIDENCE are COMMAND_VERBS as of Task 4 and are refused by post() before grammar
    # validation ever runs (see test_framework_and_command_verbs_are_refused_by_post), so NEEDS-HUMAN
    # exercises the same "requires a ref" validate() path that CHALLENGE used to.
    with pytest.raises(PostError, match="NEEDS-HUMAN requires a ref"):
        post(ctx, "NEEDS-HUMAN", text="doubt")
    with pytest.raises(PostError, match="meld challenge"):
        post(ctx, "EVIDENCE", ref="#12", text="trust me")


def test_joint_plan_when_agreed_during_planning(tmp_path):
    comments = BASE_COMMENTS + [
        {"id": 102, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:10:00Z",
         "body": "[claude] PROPOSE plan-1\n\n1. health endpoint\n2. login form\n\nverified-by: meld roster"},
        {"id": 103, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:11:00Z", "body": "[codex] AGREE plan-1\n\nok"},
    ]
    fake, ctx = make(tmp_path, comments=comments, approved=False)
    out = post(ctx, "PLAN", ref="plan-1", joint=True)
    assert out["header"] == "[joint] PLAN plan-1"
    body = posted(fake)[0]
    assert "1. health endpoint" in body and "verified-by: meld roster" in body


def test_joint_refused_when_open_or_not_proposer_or_wrong_phase(tmp_path):
    open_comments = BASE_COMMENTS + [
        {"id": 102, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:10:00Z",
         "body": "[claude] PROPOSE plan-1\n\nplan\n\nverified-by: x"},
    ]
    _, ctx = make(tmp_path, comments=open_comments, approved=False)
    with pytest.raises(Refused, match="agreement on plan-1 is open") as ei:
        post(ctx, "PLAN", ref="plan-1", joint=True)
    assert ei.value.refusal.verb.value == "post" and ei.value.refusal.target == "plan-1"
    agreed = open_comments + [{"id": 103, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:11:00Z", "body": "[codex] AGREE plan-1\n\nok"}]
    _, codex = make(tmp_path, group="codex", comments=agreed, approved=False)
    with pytest.raises(Refused, match="only the proposer \\(claude\\)"):
        post(codex, "PLAN", ref="plan-1", joint=True)
    fake_exec, executing = make(tmp_path, comments=agreed, approved=True)
    with pytest.raises(Refused, match="PLAN may only be posted during planning") as ei:
        post(executing, "PLAN", ref="plan-1", joint=True)
    # a rule-level post refusal is announced on the coordination issue
    assert posted_headers(fake_exec) == ["[claude] PROTOCOL-VIOLATION plan-1"]
    assert ei.value.violation_posted is True


def test_joint_disagree_requires_qualifier_and_lists_positions(tmp_path):
    comments = BASE_COMMENTS + [
        {"id": 102, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:10:00Z",
         "body": "[codex] DRAFT ask-1\n\nUse Postgres.\n\nverified-by: docs"},
        {"id": 103, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:11:00Z",
         "body": "[claude] DISSENT ask-1\n\nSQLite is enough for v1."},
    ]
    fake, codex = make(tmp_path, group="codex", comments=comments)
    with pytest.raises(PostError, match="no majority; post with qualifier DISAGREE"):
        post(codex, "ANSWER", ref="ask-1", joint=True)
    out = post(codex, "ANSWER", ref="ask-1", qualifier="DISAGREE", joint=True, text="verified-by: meld status")
    assert out["header"] == "[joint] ANSWER ask-1 DISAGREE"
    body = posted(fake)[0]
    assert "## Positions" in body and "Use Postgres." in body and "SQLite is enough" in body


def test_amend_cap_enforced(tmp_path):
    comments = BASE_COMMENTS + [
        {"id": 102, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:10:00Z", "body": "[claude] PROPOSE plan-1\n\nv1"},
        {"id": 103, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:11:00Z", "body": "[codex] AMEND plan-1\n\nv2"},
        {"id": 104, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:12:00Z", "body": "[claude] AMEND plan-1\n\nv3"},
        {"id": 105, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:13:00Z", "body": "[codex] AMEND plan-1\n\nv4"},
    ]
    _, ctx = make(tmp_path, comments=comments)
    with pytest.raises(Refused, match="amend cap \\(3\\) reached"):
        post(ctx, "AMEND", ref="plan-1", text="v5")
    assert post(ctx, "AGREE", ref="plan-1", text="fine")["header"] == "[claude] AGREE plan-1"


def test_reproposal_is_refused(tmp_path):
    comments = BASE_COMMENTS + [
        {"id": 102, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:10:00Z", "body": "[codex] PROPOSE plan-1\n\nv1"},
    ]
    _, ctx = make(tmp_path, comments=comments)
    with pytest.raises(Refused, match="already has a proposal; post AMEND"):
        post(ctx, "PROPOSE", ref="plan-1", text="v2")


def test_cli_post_with_message_and_error_exit(tmp_path, monkeypatch):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    fake.route("POST", R + "issues/10/comments", {"id": 7, "html_url": "u"})
    monkeypatch.setattr(cli, "RUN", fake)
    ok = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "post", "PING", "-m", "anyone there?"],
                            env=env_for(tmp_path, role="orchestrator"))
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.output)["header"] == "[claude] PING"
    # CHALLENGE is a COMMAND_VERB as of Task 4 (refused by post() before validation); NEEDS-HUMAN
    # exercises the same "requires a ref" usage-error path CHALLENGE used to.
    bad = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "post", "NEEDS-HUMAN", "-m", "x"],
                             env=env_for(tmp_path, role="orchestrator"))
    assert bad.exit_code == 2 and "requires a ref" in json.loads(bad.output)["error"]


AGREED_PLAN = BASE_COMMENTS + [
    {"id": 102, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:10:00Z",
     "body": "[claude] PROPOSE plan-1\n\n1. health endpoint\n2. login form\n\nverified-by: meld roster"},
    {"id": 103, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:11:00Z", "body": "[codex] AGREE plan-1\n\nok"},
]


def test_joint_publishes_the_agreed_text_and_appends_an_addendum(tmp_path):
    fake, ctx = make(tmp_path, comments=AGREED_PLAN, approved=False)
    out = post(ctx, "PLAN", ref="plan-1", joint=True, text="Sequencing note: ship the endpoint first.")
    assert out["header"] == "[joint] PLAN plan-1"
    body = posted(fake)[0]
    agreed = "1. health endpoint\n2. login form\n\nverified-by: meld roster"
    assert body == f"[joint] PLAN plan-1\n\n{agreed}\n\n## Addendum\nSequencing note: ship the endpoint first.\n"
    # the caller's -m never substitutes for the agreed text
    assert body.split("\n\n", 1)[1].startswith("1. health endpoint")


def test_joint_refused_when_already_posted(tmp_path):
    comments = AGREED_PLAN + [
        {"id": 104, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:12:00Z",
         "body": "[joint] PLAN plan-1\n\n1. health endpoint\n\nverified-by: meld roster"},
    ]
    _, ctx = make(tmp_path, comments=comments, approved=False)
    with pytest.raises(Refused, match="plan-1 already has a \\[joint\\] comment"):
        post(ctx, "PLAN", ref="plan-1", joint=True)


CLOSED_PLAN = AGREED_PLAN + [
    {"id": 104, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:12:00Z",
     "body": "[joint] PLAN plan-1\n\n1. health endpoint\n2. login form\n\nverified-by: meld roster"},
]


def test_agreement_verbs_are_refused_once_a_joint_comment_closed_the_ref(tmp_path):
    for group, verb in (("codex", "AMEND"), ("codex", "DISSENT"), ("claude", "AGREE"), ("codex", "PROPOSE"), ("codex", "DRAFT")):
        fake, ctx = make(tmp_path, group=group, comments=CLOSED_PLAN, approved=False)
        with pytest.raises(Refused, match="plan-1 is closed by a \\[joint\\] comment; open a new ref"):
            post(ctx, verb, ref="plan-1", text="v2")
        assert posted_headers(fake) == [f"[{group}] PROTOCOL-VIOLATION plan-1"]
    # the human's AMEND is a human input on the plan, not an agreement-round verb, and is still posted
    fake, human = make(tmp_path, group=None, comments=CLOSED_PLAN, approved=False)
    assert post(human, "AMEND", ref="plan-1", text="ship the endpoint first")["header"] == "[human] AMEND plan-1"


def test_an_unverified_joint_does_not_block_the_proposers_verified_one(tmp_path):
    # codex's hand-written [joint] is not verified (wrong author); it must not stop claude, the proposer, from posting the real one.
    forged = AGREED_PLAN + [{"id": 104, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:12:00Z",
                            "body": "[joint] PLAN plan-1\n\n1. health endpoint\n\nverified-by: x"}]
    fake, ctx = make(tmp_path, comments=forged, approved=False)
    out = post(ctx, "PLAN", ref="plan-1", joint=True)
    assert out["header"] == "[joint] PLAN plan-1"
    assert posted(fake)[0].startswith("[joint] PLAN plan-1\n\n1. health endpoint\n2. login form\n\nverified-by: meld roster")


def test_multiline_dissent_is_indented_inside_its_bullet(tmp_path):
    comments = BASE_COMMENTS + [
        {"id": 102, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:10:00Z",
         "body": "[codex] DRAFT ask-1\n\nUse Postgres.\nIt scales."},
        {"id": 103, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:11:00Z",
         "body": "[claude] DISSENT ask-1\n\nSQLite is enough for v1.\nOne file, no ops."},
    ]
    fake, codex = make(tmp_path, group="codex", comments=comments)
    post(codex, "ANSWER", ref="ask-1", qualifier="DISAGREE", joint=True, text="verified-by: meld status")
    body = posted(fake)[0]
    assert "- **codex** (proposal): Use Postgres.\n  It scales." in body
    assert "- **claude**: SQLite is enough for v1.\n  One file, no ops." in body


def test_joint_plan_add_refused_during_planning(tmp_path):
    comments = BASE_COMMENTS + [
        {"id": 102, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:10:00Z",
         "body": "[claude] PROPOSE add-1\n\nAdd #15 rate limiting.\n\nverified-by: meld status"},
        {"id": 103, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:11:00Z", "body": "[codex] AGREE add-1\n\nok"},
    ]
    _, ctx = make(tmp_path, comments=comments, approved=False)
    with pytest.raises(Refused, match="PLAN-ADD may only be posted during executing"):
        post(ctx, "PLAN-ADD", ref="add-1", joint=True)


def test_cli_post_rule_refusal_exits_two_with_the_refusal_json(tmp_path, monkeypatch):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    fake.route("GET", R + "issues/10/comments", BASE_COMMENTS + [
        {"id": 102, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:10:00Z",
         "body": "[codex] PROPOSE plan-1\n\nv1"},
    ])
    fake.route("POST", R + "issues/10/comments", {"id": 9, "html_url": "u"})
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact",
                                           "post", "PROPOSE", "plan-1", "-m", "v2"],
                                env=env_for(tmp_path, role="orchestrator"))
    assert result.exit_code == 2, result.output
    data = json.loads(result.output)
    assert data["verb"] == "post" and data["target"] == "plan-1"
    assert "already has a proposal" in data["rule"] and data["violation_posted"] is True
    assert posted_headers(fake) == ["[claude] PROTOCOL-VIOLATION plan-1"]


def test_cli_post_rejects_message_and_body_file_together(tmp_path, monkeypatch):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    (tmp_path / "body.md").write_text("from a file")
    monkeypatch.setattr(cli, "RUN", FakeGh())
    result = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "post", "PING",
                                           "-m", "inline", "--body-file", str(tmp_path / "body.md")],
                                env=env_for(tmp_path, role="orchestrator"))
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


THREE_GROUPS = PROJECT_TOML + """
[groups.gemini]
cli = "gemini"
bot = "meld-gemini-bot"
token_env = "MELD_TOKEN_GEMINI"
[groups.gemini.orchestrator]
model = "g"
effort = "high"
[groups.gemini.pm]
model = "g"
effort = "medium"
"""


def test_joint_majority_puts_dissent_before_the_addendum(tmp_path):
    (tmp_path / ".meld.toml").write_text(THREE_GROUPS)
    fake = FakeGh()
    load_board1_routes(fake)
    fake.route("GET", R + "issues/10/comments", BASE_COMMENTS + [
        {"id": 102, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:10:00Z",
         "body": "[claude] PROPOSE dec-1\n\nUse Postgres.\n\nverified-by: meld status"},
        {"id": 103, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:11:00Z",
         "body": "[codex] AGREE dec-1\n\nok"},
        {"id": 104, "user": {"login": "meld-gemini-bot"}, "created_at": "2026-09-03T09:12:00Z",
         "body": "[gemini] DISSENT dec-1\n\nSQLite is enough."},
    ])
    fake.route("POST", R + "issues/10/comments", {"id": 556, "html_url": "u"})
    ctx = load_context(tmp_path / ".meld.toml", env=env_for(tmp_path, role="orchestrator"), run=fake, adapter_for=no_skills)
    out = post(ctx, "DECISION", ref="dec-1", joint=True, text="Revisit in Q2.")
    assert out["header"] == "[joint] DECISION dec-1"
    body = posted(fake)[0]
    assert body == ("[joint] DECISION dec-1\n\nUse Postgres.\n\nverified-by: meld status\n\n"
                    "## DISSENT\n- **gemini**: SQLite is enough.\n\n## Addendum\nRevisit in Q2.\n")
    assert body.index("## DISSENT") < body.index("## Addendum")


def test_framework_and_command_verbs_are_refused_by_post(tmp_path):
    fake, ctx = make(tmp_path, role="pm")
    with pytest.raises(PostError, match="written by meld itself"):
        post(ctx, "IGNORED-COMMENT", text="x")
    fake, orch = make(tmp_path, role="orchestrator")
    with pytest.raises(PostError, match="meld challenge"):
        post(orch, "CHALLENGE", ref="#12", text="doubt")
    with pytest.raises(PostError, match="meld challenge"):
        post(orch, "UPHELD", ref="#12", text="yes")


def test_released_is_framework_only(tmp_path):
    from tests.productfixtures import make_product
    # make_product's role defaults to "pm"; an orchestrator context needs MELD_ROLE=orchestrator (tests.conftest.env_for),
    # not role="group" -- "group" is the Party.kind RELEASED is authorized for, not the env value that produces it.
    fake, ctx, repo, origin = make_product(tmp_path, group="claude", role="orchestrator")
    with pytest.raises(PostError, match="RELEASED"):
        post(ctx, "RELEASED", ref="#11", text="x")
