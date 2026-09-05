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
