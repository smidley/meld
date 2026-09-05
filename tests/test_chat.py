from datetime import datetime, timezone

import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.chat import inbox, next_ask_id, run_chat
from meld.dispatch import DispatchError
from tests.conftest import env_for
from tests.factories import CFG2, mk_board, mk_comment, mk_issue
from tests.productfixtures import R, fixture, make_product

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def test_inbox_collects_what_needs_a_person():
    comments = [
        mk_comment(CFG2, 1, "meld-codex-bot", "[codex] NEEDS-HUMAN #14\n\nCreate a Stripe sandbox key and put it in STRIPE_KEY"),
        mk_comment(CFG2, 2, "meld-claude-bot", "[claude] BUDGET\n\nlarge tier exhausted"),
        mk_comment(CFG2, 3, "meld-claude-bot", "[claude] PROPOSE ans-1\n\nA"),
        mk_comment(CFG2, 4, "meld-codex-bot", "[codex] DISSENT ans-1\n\nB"),
        mk_comment(CFG2, 5, "meld-claude-bot", "[joint] ANSWER ans-1 DISAGREE\n\n## Positions\n- claude: A\n- codex: B\n\nverified-by: x"),
        mk_comment(CFG2, 6, "meld-codex-bot", "[joint] PLAN-ADD plan-add-1\n\n- id: x | title: X | worker: backend | model: m | effort: low | size: S\n\nverified-by: y"),
        mk_comment(CFG2, 7, "empowerScott", "[human] ASK ask-1\n\nwhy postgres?"),
        mk_comment(CFG2, 8, "empowerScott", "[human] ASK ask-2\n\nstatus?"),
        mk_comment(CFG2, 9, "meld-claude-bot", "[joint] ANSWER ask-2\n\nall good\n\nverified-by: meld status"),
    ]
    board = mk_board(CFG2, issues=[mk_issue(14, status="needs_human", assignees=("meld-codex-bot",))], comments=comments)
    box = inbox(board, CFG2, NOW)
    assert box["needs_human"][0]["issue"] == 14 and "Stripe sandbox key" in box["needs_human"][0]["ask"]
    assert box["budget"][0]["group"] == "claude" and box["disagree"] == [{"ref": "ans-1", "verb": "ANSWER", "comment_id": 5}]
    assert box["plan_add"] == [{"ref": "plan-add-1", "comment_id": 6}]
    assert [(a["ref"], a["answered"]) for a in box["asks"]] == [("ask-1", False), ("ask-2", True)]
    assert next_ask_id(board) == "ask-3"
    resolved = comments + [mk_comment(CFG2, 10, "empowerScott", "[human] PROVIDED #14\n\nkey is in 1Password: STRIPE_KEY"),
                           mk_comment(CFG2, 11, "empowerScott", "[human] ACK plan-add-1\n\nok"),
                           mk_comment(CFG2, 12, "empowerScott", "[human] DIRECTIVE dir-1\n\nuse A")]
    box2 = inbox(mk_board(CFG2, issues=[mk_issue(14, status="needs_human")], comments=resolved), CFG2, NOW)
    assert box2["needs_human"] == [] and box2["plan_add"] == [] and box2["budget"] == [] and box2["disagree"] == [{"ref": "ans-1", "verb": "ANSWER", "comment_id": 5}]


def test_run_chat_posts_asks_and_human_verbs(tmp_path):
    fake, human, repo, origin = make_product(tmp_path, group=None)
    fake.route("POST", R + "issues/13/labels", [])
    lines = ["why did we pick postgres?", "/halt", "/directive ship the login form first", "/challenge open #13 the CI claim names no command", "/inbox", "/quit"]
    out_lines: list[str] = []
    result = run_chat(human, lines, out_lines.append)
    posted = [c.body["body"].splitlines()[0] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]
    assert posted == ["[human] ASK ask-1", "[human] HALT", "[human] DIRECTIVE dir-1", "[human] CHALLENGE #13"]
    assert result["posted"] == posted and result["errors"] == []
    assert any("needs_human" in line or "Needs a human" in line for line in out_lines)


def test_run_chat_reports_errors_and_refuses_non_humans(tmp_path):
    fake, human, repo, origin = make_product(tmp_path, group=None)
    result = run_chat(human, ["/provided", "/bogus", "/quit"], lambda s: None)
    assert len(result["errors"]) == 2 and result["posted"] == []
    fake2, pm, *_ = make_product(tmp_path / "p")
    with pytest.raises(DispatchError, match="humans"):
        run_chat(pm, ["hello"], lambda s: None)


def test_run_chat_reports_malformed_slash_commands_without_raising(tmp_path):
    fake, human, repo, origin = make_product(tmp_path, group=None)
    lines = ["/challenge open", "/provided", "/revert abc", "/foo", "/quit"]
    result = run_chat(human, lines, lambda s: None)
    assert len(result["errors"]) == 4
    for cmd, err in zip(("/challenge", "/provided", "/revert", "/foo"), result["errors"]):
        assert cmd in err
    assert result["posted"] == []
    assert not any(c.method == "POST" and c.endpoint == R + "issues/10/comments" for c in fake.calls)


def test_run_chat_survives_github_errors_per_line(tmp_path):
    fake, human, repo, origin = make_product(tmp_path, group=None)
    fake.route("POST", R + "issues/10/comments", {"message": "Internal Server Error"}, status=500)
    result = run_chat(human, ["why postgres?", "/halt", "/quit"], lambda s: None)
    assert result["posted"] == []
    assert len(result["errors"]) == 2
    assert all("HTTP 500" in e for e in result["errors"])

    fake2, human2, repo2, origin2 = make_product(tmp_path / "healthy", group=None)
    result2 = run_chat(human2, ["/halt", "/quit"], lambda s: None)
    assert result2["posted"] == ["[human] HALT"] and result2["errors"] == []


def test_run_chat_challenge_echoes_normalised_ref(tmp_path):
    fake, human, repo, origin = make_product(tmp_path, group=None)
    fake.route("POST", R + "issues/13/labels", [])
    result = run_chat(human, ["/challenge open 13 text", "/quit"], lambda s: None)
    assert result["posted"] == ["[human] CHALLENGE #13"]
    assert result["errors"] == []


def test_run_chat_wait_does_not_reshow_own_post(tmp_path):
    fake, human, repo, origin = make_product(tmp_path, group=None)
    comments = fixture("comments_10.json")
    next_id = [105]
    original_run = fake.__call__

    def sync_after_comment(cmd, **kwargs):
        result = original_run(cmd, **kwargs)
        last = fake.calls[-1]
        if last.method == "POST" and last.endpoint == R + "issues/10/comments":
            comments.append({"id": next_id[0], "user": {"login": "empowerScott"},
                              "created_at": "2026-09-04T00:00:00Z", "body": last.body["body"]})
            next_id[0] += 1
            fake.route("GET", R + "issues/10/comments", comments)
        return result

    human.gh._run = sync_after_comment
    out_lines: list[str] = []
    result = run_chat(human, ["why postgres?", "/wait", "/quit"], out_lines.append)
    assert result["errors"] == []
    ask_index = out_lines.index("[human] ASK ask-1")
    assert not any("ask-1" in line for line in out_lines[ask_index + 1:])


def test_run_chat_wait_shows_concurrent_comment_but_not_own_post(tmp_path):
    fake, human, repo, origin = make_product(tmp_path, group=None)
    comments = fixture("comments_10.json")
    own_id = fake.routes[("POST", R + "issues/10/comments")][1]["id"]
    concurrent_id = own_id + 1
    original_run = fake.__call__

    def sync_after_comment(cmd, **kwargs):
        result = original_run(cmd, **kwargs)
        last = fake.calls[-1]
        if last.method == "POST" and last.endpoint == R + "issues/10/comments":
            comments.append({"id": own_id, "user": {"login": "empowerScott"},
                              "created_at": "2026-09-04T00:00:00Z", "body": last.body["body"]})
            # A concurrent arrival from another party, landing with a higher id than the
            # human's own comment -- simulates it showing up between the post and the refresh.
            comments.append({"id": concurrent_id, "user": {"login": "meld-codex-bot"},
                              "created_at": "2026-09-04T00:00:01Z",
                              "body": "[codex] NEEDS-HUMAN #12\n\nneed a decision on the retry policy"})
            fake.route("GET", R + "issues/10/comments", comments)
        return result

    human.gh._run = sync_after_comment
    out_lines: list[str] = []
    result = run_chat(human, ["/halt", "/wait", "/quit"], out_lines.append)
    assert result["errors"] == []
    halt_index = out_lines.index("[human] HALT")
    after_halt = out_lines[halt_index + 1:]
    assert any("[codex] NEEDS-HUMAN #12" in line for line in after_halt)
    assert not any("[human] HALT" in line for line in after_halt)


def test_run_chat_tolerates_comment_response_without_id(tmp_path):
    fake, human, repo, origin = make_product(tmp_path, group=None)
    fake.route("POST", R + "issues/10/comments", {"html_url": "u10"})
    fake.route("POST", R + "issues/13/labels", [])
    result = run_chat(human, ["why postgres?", "/challenge open #13 text", "/quit"], lambda s: None)
    assert result["errors"] == []
    assert result["posted"] == ["[human] ASK ask-1", "[human] CHALLENGE #13"]


def test_run_chat_wait_shows_concurrent_comment_after_challenge(tmp_path):
    fake, human, repo, origin = make_product(tmp_path, group=None)
    fake.route("POST", R + "issues/13/labels", [])
    comments = fixture("comments_10.json")
    own_id = fake.routes[("POST", R + "issues/10/comments")][1]["id"]
    concurrent_id = own_id + 1
    original_run = fake.__call__

    def sync_after_comment(cmd, **kwargs):
        result = original_run(cmd, **kwargs)
        last = fake.calls[-1]
        if last.method == "POST" and last.endpoint == R + "issues/10/comments":
            comments.append({"id": own_id, "user": {"login": "empowerScott"},
                              "created_at": "2026-09-04T00:00:00Z", "body": last.body["body"]})
            comments.append({"id": concurrent_id, "user": {"login": "meld-codex-bot"},
                              "created_at": "2026-09-04T00:00:01Z",
                              "body": "[codex] NEEDS-HUMAN #12\n\nneed a decision on the retry policy"})
            fake.route("GET", R + "issues/10/comments", comments)
        return result

    human.gh._run = sync_after_comment
    out_lines: list[str] = []
    result = run_chat(human, ["/challenge open #13 text", "/wait", "/quit"], out_lines.append)
    assert result["errors"] == []
    challenge_index = out_lines.index("[human] CHALLENGE #13")
    after_challenge = out_lines[challenge_index + 1:]
    assert any("[codex] NEEDS-HUMAN #12" in line for line in after_challenge)
    assert not any("[human] CHALLENGE #13" in line for line in after_challenge)


def test_cli_inbox_and_chat_once(tmp_path, monkeypatch):
    fake, human, repo, origin = make_product(tmp_path, group=None)
    monkeypatch.setattr(cli, "RUN", fake)
    env = env_for(tmp_path, group=None)
    result = CliRunner().invoke(cli.main, ["--config", str(repo / ".meld.toml"), "--compact", "inbox"], env=env)
    assert result.exit_code == 0, result.output
    assert '"needs_human"' in result.output
    once = CliRunner().invoke(cli.main, ["--config", str(repo / ".meld.toml"), "--compact", "chat", "--once", "what is next?"], env=env)
    assert once.exit_code == 0, once.output
    assert "[human] ASK ask-1" in once.output
    repl = CliRunner().invoke(cli.main, ["--config", str(repo / ".meld.toml"), "--compact", "chat"], env=env, input="/resume\n/quit\n")
    assert repl.exit_code == 0, repl.output
    assert "[human] RESUME" in repl.output
