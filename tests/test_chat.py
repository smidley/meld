import json
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
    board = mk_board(CFG2, issues=[mk_issue(14, status="needs_human")], comments=comments)
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
    # I4: a human's own `meld tag --held` posts `[human] NEEDS-HUMAN #14`. The item is on the inbox because the issue
    # is needs_human, exactly as before -- the human's ask adds no item, it just says what the hold is waiting for.
    reheld = resolved + [mk_comment(CFG2, 13, "empowerScott", "[human] NEEDS-HUMAN #14\n\nheld: delete -- decide before the worker runs")]
    box3 = inbox(mk_board(CFG2, issues=[mk_issue(14, status="needs_human")], comments=reheld), CFG2, NOW)
    assert [i["issue"] for i in box3["needs_human"]] == [14] and "held: delete" in box3["needs_human"][0]["ask"]


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


def test_cli_chat_once_exits_non_zero_when_the_line_failed(tmp_path, monkeypatch):
    # F10: a scripted --once caller reads the exit status, not the inline "error:" line; 2 is the refused/usage-error code.
    fake, human, repo, origin = make_product(tmp_path, group=None)
    monkeypatch.setattr(cli, "RUN", fake)
    env = env_for(tmp_path, group=None)
    bad = CliRunner().invoke(cli.main, ["--config", str(repo / ".meld.toml"), "--compact", "chat", "--once", "/bogus"], env=env)
    assert bad.exit_code == 2, bad.output
    data = json.loads(bad.output.splitlines()[-1])
    assert data["posted"] == [] and len(data["errors"]) == 1 and "unknown command /bogus" in data["errors"][0]
    fake.route("POST", R + "issues/10/comments", {"message": "Internal Server Error"}, status=500)
    down = CliRunner().invoke(cli.main, ["--config", str(repo / ".meld.toml"), "--compact", "chat", "--once", "/halt"], env=env)
    assert down.exit_code == 2 and "HTTP 500" in json.loads(down.output.splitlines()[-1])["errors"][0]
    fake.route("POST", R + "issues/10/comments", {"id": 310, "html_url": "u10"})
    ok = CliRunner().invoke(cli.main, ["--config", str(repo / ".meld.toml"), "--compact", "chat", "--once", "/halt"], env=env)
    assert ok.exit_code == 0, ok.output


def test_budget_items_clear_only_on_a_human_answer_to_their_ref():
    comments = [
        mk_comment(1, "meld-claude-bot", "[claude:pm] BUDGET budget-1\n\nmedium tier at 90%", "2026-09-05T10:00:00Z"),
        mk_comment(2, "meld-codex-bot", "[codex:pm] BUDGET budget-2\n\nlarge tier at 95%", "2026-09-05T10:01:00Z"),
        mk_comment(3, "empowerScott", "[human] DIRECTIVE budget-1\n\nraise medium to 120", "2026-09-05T10:02:00Z"),
    ]
    box = inbox(mk_board(comments=comments), CFG2, datetime(2026, 9, 5, 11, tzinfo=timezone.utc))
    assert [b["ref"] for b in box["budget"]] == ["budget-2"]


def test_run_chat_recent_prints_the_thread_and_resolved_takes_any_grammar_ref(tmp_path):
    """Handoff residual 13: /resolved refused anything but #N, so a BUDGET (budget-<n>) was answered from the raw CLI,
    and the console could show only comments newer than a /wait. /recent prints the newest N through the same
    renderer as `meld thread`, and /provided, /resolved and /revert take any ref the grammar accepts."""
    from meld.chat import thread_lines
    from meld.status import build_status
    fake, human, repo, origin = make_product(tmp_path, group=None)
    _, board = build_status(human)
    ok = sorted((c for c in board.comments if c.ok), key=lambda c: (c.created_at, c.id))
    assert len(ok) >= 2
    out_lines: list[str] = []
    result = run_chat(human, ["/recent 2", "/resolved budget-1 raised the cap", "/provided #13 the key is in 1Password", "/resolved fix it now", "/recent x", "/quit"], out_lines.append)
    newest = [f"#{c.id} {c.created_at} {c.header.party.label} {c.header.verb}" for c in ok[-2:]]
    assert [line.split("\n")[0][:len(n)] for line, n in zip(out_lines[2:4], newest)] == newest   # the headline is line 0, the inbox line 1
    assert f"#{ok[0].id} " not in "".join(out_lines[2:4])
    posted = [c.body["body"].splitlines()[0] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]
    assert posted == ["[human] RESOLVED budget-1", "[human] PROVIDED #13"]
    assert len(result["errors"]) == 2 and "needs a ref like #13 or budget-1" in result["errors"][0] and "/recent takes a count" in result["errors"][1]
    assert thread_lines(board, last=0) == thread_lines(board) and len(thread_lines(board, last=1)) == 1


def test_cli_thread_prints_the_newest_comments_oldest_first(tmp_path, monkeypatch):
    from meld.status import build_status
    fake, human, repo, origin = make_product(tmp_path, group=None)
    monkeypatch.setattr(cli, "RUN", fake)
    env = env_for(tmp_path, group=None)
    _, board = build_status(human)
    ok = sorted((c for c in board.comments if c.ok), key=lambda c: (c.created_at, c.id))
    result = CliRunner().invoke(cli.main, ["--config", str(repo / ".meld.toml"), "thread", "--last", "1"], env=env)
    assert result.exit_code == 0, result.output
    assert result.output.startswith(f"#{ok[-1].id} {ok[-1].created_at} ") and f"#{ok[0].id} " not in result.output
    whole = CliRunner().invoke(cli.main, ["--config", str(repo / ".meld.toml"), "thread", "--last", "0"], env=env)
    assert whole.exit_code == 0 and all(f"#{c.id} {c.created_at} " in whole.output for c in ok)
    assert whole.output.index(f"#{ok[0].id} ") < whole.output.index(f"#{ok[-1].id} ")


def test_thread_entries_are_the_thread_as_data(tmp_path):
    """Plan 17: the viewer's conversation panel reads the same thread `meld thread` prints, as data, oldest first."""
    from meld.chat import thread_entries, thread_lines
    from meld.status import build_status
    fake, human, repo, origin = make_product(tmp_path, group=None)
    _, board = build_status(human)
    entries = thread_entries(board)
    assert len(entries) == len(thread_lines(board)) >= 2
    assert [e["id"] for e in entries] == sorted(e["id"] for e in entries)
    assert {"id", "at", "party", "kind", "group", "verb", "ref", "qualifier", "text", "housekeeping", "agreement"} <= set(entries[0])
    assert entries[0]["party"].startswith("[") and entries[0]["kind"] in ("group", "pm", "human", "joint")
    assert all(e["housekeeping"] == (e["verb"] in ("STATUS", "LEASE", "EXPIRED", "RELEASED", "RESUMED", "CONFIG", "ROSTER", "ROSTER-CHANGE", "LIMITED")) for e in entries)
    assert thread_entries(board, last=1) == entries[-1:] and thread_entries(board, last=0) == entries


def test_decisions_present_everything_waiting_on_the_human_with_its_choices():
    """Plan 18: the page's decision cards. Each pending item is one decision with the request bodies /api/action takes;
    a choice is offered only when rules.py would allow it, and nothing here decides legality on its own."""
    from dataclasses import replace
    from meld.board import Challenge
    from meld.chat import decisions
    from meld.config import Party
    from meld.rules import Actor
    HUMAN = Actor(Party("human"), "empowerScott")
    comments = [
        mk_comment(CFG2, 1, "meld-codex-bot", "[codex] NEEDS-HUMAN #14\n\nCreate a Stripe sandbox key and put it in STRIPE_KEY"),
        mk_comment(CFG2, 2, "meld-claude-bot", "[claude] BUDGET\n\nlarge tier exhausted"),
        mk_comment(CFG2, 3, "meld-claude-bot", "[claude] PROPOSE ans-1\n\nA"),
        mk_comment(CFG2, 4, "meld-codex-bot", "[codex] DISSENT ans-1\n\nB"),
        mk_comment(CFG2, 5, "meld-claude-bot", "[joint] ANSWER ans-1 DISAGREE\n\n## Positions\n- claude: A\n- codex: B\n\nverified-by: x"),
        mk_comment(CFG2, 6, "meld-codex-bot", "[codex] PROPOSE plan-add-1\n\n- id: x | title: X | worker: backend | model: claude-sonnet-5 | effort: low | size: S"),
        mk_comment(CFG2, 7, "meld-claude-bot", "[claude] AGREE plan-add-1\n\nfine"),
        mk_comment(CFG2, 8, "meld-codex-bot", "[joint] PLAN-ADD plan-add-1\n\n- id: x | title: X | worker: backend | model: claude-sonnet-5 | effort: low | size: S\n\nverified-by: y"),
        mk_comment(CFG2, 9, "meld-codex-bot", "[codex] CHALLENGE #13\n\nthe CI claim names no command"),
    ]
    board = mk_board(CFG2, issues=[mk_issue(14, status="needs_human"), mk_issue(13, status="in_review", claimed_by="claude")], comments=comments)
    board = replace(board, open_challenges=frozenset({"#13"}), challenges={"#13": Challenge("#13", "codex", 9, "2026-09-03T10:09:00Z")})
    out = decisions(board, CFG2, HUMAN, NOW)
    by_kind = {d["kind"]: d for d in out}
    assert [d["kind"] for d in out] == ["needs_human", "budget", "disagree", "plan_add", "challenge"]
    nh = by_kind["needs_human"]
    assert nh["title"] == "Issue #14 needs you: Issue 14" and "Stripe sandbox key" in nh["summary"]
    # plan 51: a question is a bubble from someone with one typed reply; buttons only where the answer is one word
    assert nh["choices"] == [] and nh["reply"] == {"verb": "post", "action": "RESOLVED", "ref": "#14"} and nh["from"] == "codex" and "never the value" in nh["hint"]
    assert "What to do" not in nh["summary"]
    assert by_kind["budget"]["reply"] == {"verb": "post", "action": "DIRECTIVE", "ref": "dir-1"} and by_kind["budget"]["choices"] == []   # an old BUDGET with no ref
    assert by_kind["disagree"]["reply"]["action"] == "DIRECTIVE" and [c["body"]["action"] for c in by_kind["disagree"]["choices"]] == ["APPROVE"] and "claude: A" in by_kind["disagree"]["summary"]
    assert by_kind["plan_add"]["reply"] is None and by_kind["challenge"]["reply"] is None and by_kind["challenge"]["from"] == "codex"
    assert by_kind["plan_add"]["choices"] == [{"label": "Acknowledge", "body": {"verb": "plan-ack", "ref": "plan-add-1"}, "needs_text": False}]
    assert [c["body"] for c in by_kind["challenge"]["choices"]] == [{"verb": "challenge", "action": "uphold", "ref": "#13"}, {"verb": "challenge", "action": "dismiss", "ref": "#13"}]
    # plan 20: each card knows the comment it came from, so the page can jump to that turn
    assert {k: d["comment_id"] for k, d in by_kind.items()} == {"needs_human": 1, "budget": 2, "disagree": 5, "plan_add": 8, "challenge": 9}
    assert "names no command" in by_kind["challenge"]["summary"] and "codex" in by_kind["challenge"]["title"]
    # the plan awaiting approval, in planning, with a verified [joint] PLAN
    plan = "- id: a | title: Add health endpoint | worker: backend | model: claude-sonnet-5 | effort: medium | size: S\n- id: b | title: Docs | worker: docs | model: claude-sonnet-5 | effort: low | size: S"
    planning = mk_board(CFG2, phase="planning", comments=[
        mk_comment(CFG2, 1, "meld-claude-bot", f"[claude] PROPOSE plan-3\n\n{plan}"),
        mk_comment(CFG2, 2, "meld-codex-bot", "[codex] AGREE plan-3\n\nfine"),
        mk_comment(CFG2, 3, "meld-claude-bot", f"[joint] PLAN plan-3\n\n{plan}\n\nverified-by: meld status")])
    approve = decisions(planning, CFG2, HUMAN, NOW)
    assert [d["kind"] for d in approve] == ["plan_approve"]
    assert approve[0]["summary"] == "2 items: Add health endpoint; Docs"   # plan 54: pluralised properly and approve[0]["choices"] == [{"label": "Approve the plan", "body": {"verb": "plan-approve"}, "needs_text": False}]
    assert approve[0]["comment_id"] == 3
    assert decisions(mk_board(CFG2), CFG2, HUMAN, NOW) == []


def test_headline_says_where_the_milestone_stands_in_plain_words():
    """Plan 19: the first sentence a person reads, from meld status's own JSON and the decisions."""
    from dataclasses import replace
    from meld.chat import headline
    from meld.config import Party
    from meld.rules import Actor
    from meld.status import render_status
    from tests.factories import REG, mk_pr, mk_run
    HUMAN = Actor(Party("human"), "empowerScott")
    planning = render_status(mk_board(CFG2, phase="planning"), CFG2, HUMAN, REG, NOW)
    assert headline(planning, []) == "Planning milestone 3: the groups are agreeing a plan on the coordination issue; nothing waits on you."
    assert headline(planning, [{"kind": "plan_approve"}]) == "Planning milestone 3: the groups have agreed a plan and it is ready for your approval."
    board = mk_board(CFG2, issues=[mk_issue(11), mk_issue(12, status="in_progress", claimed_by="claude"), mk_issue(13, status="in_review", claimed_by="codex"),
                                   mk_issue(14, status="needs_human"), mk_issue(9, status="closed", state="closed")],
                     pulls=[mk_pr(20, 13, committer="meld-codex-bot")],
                     runs={"claude": {12: mk_run("claude", 12, alive=True)}, "codex": {}})
    executing = render_status(board, CFG2, HUMAN, REG, NOW)
    assert headline(executing, [{"kind": "needs_human"}, {"kind": "budget"}]) == (
        "Executing milestone 3: issues: 1 in progress, 1 in review, 1 ready, 1 waiting on a person, 1 closed; 1 worker running; 1 open PR; "
        "2 decisions wait on you.")
    assert headline(executing, [{"kind": "needs_human"}]).endswith("1 decision waits on you.")
    assert headline(render_status(replace(board, halted=True), CFG2, HUMAN, REG, NOW), []).startswith("Halted by a human: nothing moves until a RESUME;")
    done = render_status(mk_board(CFG2, issues=[mk_issue(9, status="closed", state="closed")]), CFG2, HUMAN, REG, NOW)
    assert headline(done, []) == "Milestone 3 is complete: every issue is closed; nothing waits on you."


def test_a_needs_human_about_something_other_than_an_issue_still_waits_on_the_person():
    """The second live run's codex PM posted `NEEDS-HUMAN 40` about a merged PR; no issue carried the label, so the page
    said nothing waited. Such a post is a decision until a person answers on that ref, and a bare number reads as #n."""
    from meld.chat import decisions, inbox
    from meld.config import Party
    from meld.rules import Actor
    HUMAN = Actor(Party("human"), "empowerScott")
    ask = mk_comment(CFG2, 1, "meld-codex-bot", "[codex:pm] NEEDS-HUMAN 40\n\nMeld audit state is inconsistent for PR #40.\nverified-by: meld status")
    board = mk_board(CFG2, issues=[mk_issue(14)], comments=[ask])
    (item,) = inbox(board, CFG2, NOW)["needs_human"]
    assert item["issue"] is None and item["ref"] == "#40" and item["party"] == "codex:pm" and item["comment_id"] == 1
    (card,) = decisions(board, CFG2, HUMAN, NOW)
    assert card["kind"] == "needs_human" and card["ref"] == "#40"
    assert card["title"] == "codex:pm needs you about #40: Meld audit state is inconsistent for PR #40."
    assert card["reply"] == {"verb": "post", "action": "RESOLVED", "ref": "#40"} and card["from"] == "codex:pm"
    answered = mk_board(CFG2, issues=[mk_issue(14)], comments=[ask, mk_comment(CFG2, 2, "empowerScott", "[human] RESOLVED #40\n\nfixed in meld")])
    assert inbox(answered, CFG2, NOW)["needs_human"] == [] and decisions(answered, CFG2, HUMAN, NOW) == []


def test_a_journey_says_where_an_issue_is_and_what_comes_next():
    """Plan 29: the owner saw "2 of 3 issues done" and nothing about how the third was getting there. From status's own
    JSON: seven steps done/now/next with details, one sentence for now, one for what follows, and a progress line."""
    from datetime import datetime, timezone
    from meld.chat import journey, journeys, progress_line
    now = datetime(2026, 10, 26, 22, 0, tzinfo=timezone.utc)
    status = {"quorum": 2,
              "issues": [{"number": 49, "title": "Formats", "status": "in_review", "claimed_by": "claude", "worker": "backend", "challenged": False},
                         {"number": 47, "title": "Docs", "status": "closed", "claimed_by": None, "worker": "docs", "challenged": False},
                         {"number": 50, "title": "Next", "status": "ready", "claimed_by": None, "worker": None, "challenged": False}],
              "pulls": [{"number": 55, "issue": 49, "state": "open", "merged": False, "approvals": [], "changes_requested": [], "checks": "green", "audited_by": {}},
                        {"number": 51, "issue": 47, "state": "closed", "merged": True, "approvals": ["codex", "claude"], "changes_requested": [], "checks": "green", "audited_by": {"codex": "pass"}}],
              "runs": [{"group": "claude", "issue": 49, "kind": "build", "pr": None, "state": "collected", "outcome": "done", "worker": "backend", "started_at": "2026-10-26T21:30:00Z"},
                       {"group": "codex", "issue": 55, "kind": "review", "pr": 55, "state": "running", "outcome": None, "worker": "reviewer", "started_at": "2026-10-26T21:48:00Z"}]}
    j = journey(status["issues"][0], status, now)
    assert [(s["name"], s["state"]) for s in j["steps"]] == [("claimed", "done"), ("built", "done"), ("pull request", "done"), ("reviewed", "now"),
                                                             ("merged", "next"), ("audited", "next"), ("closed", "next")]
    assert j["steps"][1]["detail"] == "claude's backend worker" and j["steps"][2]["detail"] == "PR #55"
    assert j["now"] == "codex is reviewing PR #55, 12 min in" and j["next"] == "then merged, audited, closed"
    done = journey(status["issues"][1], status, now)
    assert all(s["state"] == "done" for s in done["steps"]) and done["now"] == "done" and done["next"] == ""
    assert done["steps"][5]["detail"] == "codex: pass"
    fresh = journey(status["issues"][2], status, now)
    assert fresh["steps"][0]["state"] == "now" and fresh["now"] == "ready for a group to claim"
    assert set(journeys(status, now)) == {49, 47, 50}
    assert progress_line(status, now) == "#49: codex is reviewing PR #55, 12 min in · #50: ready for a group to claim"
    many = {**status, "issues": status["issues"] + [{"number": k, "title": "t", "status": "ready", "claimed_by": None, "worker": None, "challenged": False} for k in (60, 61)]}
    assert progress_line(many, now) == "4 left: 1 at reviewed, 3 at claimed"
    waiting = {**status, "runs": [status["runs"][0]]}
    assert journey(status["issues"][0], waiting, now)["now"] == "PR #55 waits for a review (0 of 2 approvals)"
    approved = {**waiting, "pulls": [{**status["pulls"][0], "approvals": ["codex", "claude"]}, status["pulls"][1]]}
    assert journey(status["issues"][0], approved, now)["now"] == "PR #55 is approved and ready to merge"


def test_a_journey_says_the_model_was_at_capacity_and_the_build_is_retried():
    """Plan 50."""
    from datetime import datetime, timezone
    from meld.chat import journey
    status = {"quorum": 1, "issues": [{"number": 3, "title": "Site", "status": "in_progress", "claimed_by": "codex", "worker": "frontend", "challenged": False}],
              "pulls": [], "runs": [{"group": "codex", "issue": 3, "kind": "build", "pr": None, "state": "collected", "outcome": "capacity", "worker": "frontend", "started_at": "2026-10-28T17:35:00Z"}]}
    j = journey(status["issues"][0], status, datetime(2026, 10, 28, 17, 40, tzinfo=timezone.utc))
    assert j["now"] == "codex's model was at capacity; the build is retried after a short pause"


def test_an_ask_withdrawn_by_its_own_group_leaves_the_inbox(tmp_path):
    """Plan 52."""
    from meld.chat import inbox
    from tests.factories import mk_board, mk_comment, mk_issue
    ask = mk_comment(CFG2, 1, "meld-codex-bot", "[codex:pm] NEEDS-HUMAN #14\n\nrestore the service")
    free = mk_comment(CFG2, 2, "meld-codex-bot", "[codex:pm] NEEDS-HUMAN service-1\n\nthe browser is down")
    board = mk_board(CFG2, issues=[mk_issue(14, status="needs_human")], comments=[ask, free])
    assert [i["ref"] for i in inbox(board, CFG2, NOW)["needs_human"]] == ["#14", "service-1"]
    gone = [mk_comment(CFG2, 3, "meld-codex-bot", "[codex] RESOLVED #14\n\nback"), mk_comment(CFG2, 4, "meld-codex-bot", "[codex] RESOLVED service-1\n\nback")]
    assert inbox(mk_board(CFG2, issues=[mk_issue(14, status="needs_human")], comments=[ask, free, *gone]), CFG2, NOW)["needs_human"] == []


def _role(state="alive", launch_no=1, reason="start", frozen=None):
    return {"state": state, "pid": 1, "started_at": None, "proc_start": "", "frozen": frozen, "launch_no": launch_no, "reason": reason}


def _procs(**per_group):
    """`processes` as `runners.processes` shapes it: per group a supervisor and two roles; a group given as "dead" has nothing alive."""
    out = {}
    for g, spec in per_group.items():
        if spec == "dead":
            out[g] = {"supervisor": {"state": "dead"}, "roles": {"orchestrator": _role("dead"), "pm": _role("dead")}}
        else:
            out[g] = {"supervisor": {"state": "alive"}, "roles": {"orchestrator": _role(), "pm": spec if isinstance(spec, dict) else _role()}}
    return out


def _executing(issues=None, **kw):
    from meld.config import Party
    from meld.rules import Actor
    from meld.status import render_status
    from tests.factories import REG, mk_pr, mk_run
    issues = [mk_issue(11), mk_issue(12, status="in_progress", claimed_by="claude"), mk_issue(9, status="closed", state="closed")] if issues is None else issues
    board = mk_board(CFG2, issues=issues, pulls=[mk_pr(20, 12, committer="meld-claude-bot")], runs={"claude": {12: mk_run("claude", 12, alive=True)}, "codex": {}}, **kw)
    return render_status(board, CFG2, Actor(Party("human"), "empowerScott"), REG, NOW)


ALIVE = {"claude": None, "codex": None}
RECENT = "2026-09-03T11:55:00Z"     # five minutes before NOW: not silence


def test_the_status_strip_says_each_state_in_the_reviews_words():
    """Plan 54: one line, one colour, computed beside headline(); the wording of the usability review's table."""
    from meld.chat import status_strip
    strip = lambda status, decisions=(), procs=None, limits=None, **kw: status_strip(   # noqa: E731
        status, list(decisions), _procs(**ALIVE) if procs is None else procs, limits or {"frozen": {}, "usage": {}}, NOW, **{"last_post_at": RECENT, **kw})
    planning = {"milestone": 1, "phase": "planning", "issues": [], "runs": []}
    assert strip(planning) == {"level": "quiet", "text": "Planning milestone 1 — the groups are agreeing a plan. Nothing needs you.", "action": None, "detail": ""}
    ready = strip(planning, [{"kind": "plan_approve", "age_minutes": None}])
    assert ready["level"] == "attention" and ready["text"] == "The plan for milestone 1 is ready for your approval." and ready["action"] == {"verb": "plan-approve"}
    building = strip(_executing())
    assert building["level"] == "ok" and building["text"] == "Building milestone 3 — 1 worker running, 1 of 3 issues done. Nothing needs you."
    asked = strip(_executing(), [{"kind": "needs_human", "age_minutes": 25}, {"kind": "budget", "age_minutes": 8}])
    assert asked["level"] == "attention" and asked["text"] == "2 questions need you — oldest 25 min." and asked["action"] is None
    assert strip(_executing(), [{"kind": "needs_human", "age_minutes": 3}])["text"] == "1 question needs you — oldest 3 min."
    one_frozen = {"frozen": {"codex": {"pm": {"until": "2026-09-03T17:09:00Z", "expired": False}}}, "usage": {}}
    frozen = strip(_executing(), limits=one_frozen)
    assert frozen["level"] == "attention" and frozen["text"] == "codex is out of quota until 5:09 pm; claude is still building."
    all_frozen = {"frozen": {"codex": {"pm": {"until": "2026-09-03T17:09:00Z", "expired": False}},
                             "claude": {"orchestrator": {"until": "2026-09-03T18:00:00Z", "expired": False}}}, "usage": {}}
    every = strip(_executing(), limits=all_frozen)
    assert every["level"] == "bad" and every["text"] == "Every agent is out of quota; next try 5:09 pm."
    looping = strip(_executing(), procs=_procs(claude=None, codex=_role("dead", launch_no=39, reason="crash")))
    assert looping["level"] == "bad" and looping["text"] == "codex's PM has stopped (restarted 38 times). Open its transcript."
    stuck = strip(_executing(issues=[mk_issue(3, status="blocked"), mk_issue(12, status="in_progress", claimed_by="claude")]))
    assert stuck["level"] == "attention" and stuck["text"] == "#3 is blocked: the worker could not finish. 1 worker still running."
    silent = strip(_executing(), last_post_at="2026-09-03T11:35:00Z")   # the run started 10:00, the last post 11:35
    assert silent["level"] == "attention" and silent["text"] == "Nothing has happened for 25 min — last post 11:35 am."
    halted = strip(_executing(halted=True))
    assert halted["level"] == "bad" and halted["text"] == "Paused by you. Nothing moves until you resume." and halted["action"] == {"verb": "post", "action": "RESUME"}
    done_status = {"milestone": 1, "phase": "executing", "issues": [{"number": n, "status": "closed"} for n in (1, 2, 3)], "runs": [], "audit": {"pending": [40]}}
    done = strip(done_status, limits={"frozen": {}, "usage": {"claude": {"cost_usd": 41.2}, "codex": {"cost_usd": 0.0}}})
    assert done == {"level": "ok", "text": "Milestone 1 is complete — 3 of 3 issues closed, $41.20 spent.", "action": None, "detail": "1 merged PR still awaits an audit."}
    assert strip({**done_status, "audit": {"pending": []}})["text"] == "Milestone 1 is complete — 3 of 3 issues closed."
    assert strip({**done_status, "audit": {"pending": [40, 41]}})["detail"] == "2 merged PRs still await an audit."


def test_the_status_strip_keeps_the_reviews_precedence_and_names_the_next_state_as_the_detail():
    from meld.chat import status_strip
    looping = _procs(claude=None, codex=_role("dead", launch_no=39, reason="crash"))
    frozen = {"frozen": {"claude": {"pm": {"until": "2026-09-03T17:09:00Z", "expired": False}}}, "usage": {}}
    asks = [{"kind": "needs_human", "age_minutes": 25}]
    everything = status_strip(_executing(halted=True, issues=[mk_issue(3, status="blocked")]), asks, looping, frozen, NOW, last_post_at="2026-09-03T10:00:00Z")
    assert everything["text"].startswith("Paused by you.") and everything["detail"] == "1 question needs you — oldest 25 min."
    not_halted = status_strip(_executing(issues=[mk_issue(3, status="blocked")]), asks, looping, frozen, NOW, last_post_at="2026-09-03T10:00:00Z")
    assert not_halted["text"].startswith("1 question needs you") and not_halted["detail"].startswith("codex's PM has stopped")
    no_asks = status_strip(_executing(issues=[mk_issue(3, status="blocked")]), [], looping, frozen, NOW, last_post_at="2026-09-03T10:00:00Z")
    assert no_asks["text"].startswith("codex's PM has stopped") and no_asks["detail"] == "claude is out of quota until 5:09 pm; codex is still running."
    quiet = status_strip(_executing(issues=[mk_issue(3, status="blocked")]), [], _procs(**ALIVE), {"frozen": {}, "usage": {}}, NOW, last_post_at="2026-09-03T10:00:00Z")
    assert quiet["text"].startswith("#3 is blocked") and quiet["detail"].startswith("Nothing has happened for 120 min")
    silent = status_strip(_executing(), [], _procs(**ALIVE), {"frozen": {}, "usage": {}}, NOW, last_post_at="2026-09-03T10:00:00Z")
    assert silent["text"].startswith("Nothing has happened") and silent["detail"] == "Building milestone 3 — 1 worker running, 1 of 3 issues done. Nothing needs you."


def test_a_role_is_crash_looping_past_five_launches_and_silence_starts_past_the_grace():
    from meld.chat import status_strip
    limits = {"frozen": {}, "usage": {}}
    five = status_strip(_executing(), [], _procs(claude=None, codex=_role("alive", launch_no=5, reason="crash")), limits, NOW, last_post_at=RECENT)
    assert five["level"] == "ok"
    six = status_strip(_executing(), [], _procs(claude=None, codex=_role("alive", launch_no=6, reason="crash")), limits, NOW, last_post_at=RECENT)
    assert six["level"] == "bad" and six["text"] == "codex's PM has stopped (restarted 5 times). Open its transcript."
    exited = status_strip(_executing(), [], _procs(claude=None, codex=_role("alive", launch_no=6, reason="exit")), limits, NOW, last_post_at=RECENT)
    assert exited["level"] == "ok"                                                                # six clean recycles are not a loop
    stopped = status_strip(_executing(), [], _procs(claude=None, codex=_role("dead", launch_no=2, reason="exit")), limits, NOW, last_post_at=RECENT)
    assert stopped["text"] == "codex's PM has stopped. Open its transcript."
    at_grace = status_strip(_executing(), [], _procs(**ALIVE), limits, NOW, last_post_at="2026-09-03T11:45:00Z", silence_grace_minutes=15)
    assert at_grace["level"] == "ok"                                                              # 15 min is not past a 15-min grace
    past = status_strip(_executing(), [], _procs(**ALIVE), limits, NOW, last_post_at="2026-09-03T11:44:00Z", silence_grace_minutes=15)
    assert past["text"] == "Nothing has happened for 16 min — last post 11:44 am."
    wider = status_strip(_executing(), [], _procs(**ALIVE), limits, NOW, last_post_at="2026-09-03T11:44:00Z", silence_grace_minutes=30)
    assert wider["level"] == "ok"
    nothing_yet = status_strip({"milestone": 1, "phase": "planning", "issues": [], "runs": []}, [], _procs(**ALIVE), limits, NOW)
    assert nothing_yet["level"] == "quiet"                                                        # no post and no run: nothing to measure from


def test_agent_rows_say_what_each_group_is_doing_in_plain_words():
    from meld.chat import agent_rows
    status = _executing()
    procs = _procs(claude=None, codex=_role("dead", launch_no=39, reason="crash"))
    procs["gemini"] = {"supervisor": {"state": "alive"}, "roles": {"orchestrator": _role(), "pm": _role("dead", launch_no=2, reason="limit", frozen={"at": NOW})}}
    procs["mistral"] = {"supervisor": {"state": "dead"}, "roles": {"orchestrator": _role("dead"), "pm": _role("dead")}}
    limits = {"frozen": {"gemini": {"pm": {"until": "2026-09-03T17:09:00Z", "expired": False}}},
              "usage": {"claude": {"input_tokens": 904000, "output_tokens": 256000, "cached_tokens": 12, "cost_usd": 28.4123, "runs": 11},
                        "codex": {"input_tokens": 5000, "output_tokens": 100, "cached_tokens": 0, "cost_usd": 0.0, "runs": 9},
                        "gemini": {"unknown": True}}}
    status["budget"] = {"spent": {"claude": {"medium": 1, "large": 1}, "codex": {"large": 9}, "gemini": {}, "mistral": {}}, "cap": {"small": 200, "medium": 80, "large": 20}}
    rows = {r["group"]: r for r in agent_rows(status, procs, limits, NOW)}
    assert rows["claude"] == {"group": "claude", "state": "running", "summary": "running · orchestrator ok · PM ok", "doing": "building #12",
                              "frozen_until": None, "restarts": 0, "cost_usd": 28.41, "tokens": {"in": 904000, "out": 256000, "cached": 12}, "runs": "1 of 20 large, 1 of 80 medium"}
    assert rows["codex"]["state"] == "partly" and rows["codex"]["summary"] == "partly running · orchestrator ok · PM stopped, restarted 38 times"
    assert rows["codex"]["restarts"] == 38 and rows["codex"]["cost_usd"] is None and rows["codex"]["runs"] == "9 of 20 large" and rows["codex"]["doing"] == ""
    assert rows["gemini"]["state"] == "frozen" and rows["gemini"]["frozen_until"] == "5:09 pm"
    assert rows["gemini"]["summary"] == "out of quota until 5:09 pm · orchestrator ok · PM out of quota until 5:09 pm" and rows["gemini"]["cost_usd"] is None
    assert rows["mistral"]["state"] == "stopped" and rows["mistral"]["summary"] == "stopped · orchestrator stopped · PM stopped"
    assert rows["mistral"]["tokens"] == {"in": 0, "out": 0, "cached": 0} and rows["mistral"]["runs"] == "" and rows["mistral"]["cost_usd"] is None
    for word in ("awake", "dead", "frozen", "dispatched", "launch"):
        assert not any(word in r["summary"] for r in rows.values()), word
    reviewing = _executing()
    reviewing["runs"] = [{"group": "codex", "kind": "review", "issue": 20, "pr": 20, "state": "running"}, {"group": "codex", "kind": "audit", "issue": 21, "state": "running"}]
    assert {r["group"]: r["doing"] for r in agent_rows(reviewing, _procs(**ALIVE), limits, NOW)} == {"claude": "", "codex": "reviewing PR #20, checking PR #21 after merge"}


def test_a_journey_says_what_an_issue_waits_for():
    """Plan 56."""
    from datetime import datetime, timezone
    from meld.chat import journey
    status = {"quorum": 1, "issues": [{"number": 4, "title": "Tests", "status": "ready", "claimed_by": None, "worker": "qa", "challenged": False, "waits_for": [3]}], "pulls": [], "runs": []}
    assert journey(status["issues"][0], status, datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc))["now"] == "waits for #3 to close first"
