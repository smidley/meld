import pytest

from moot.assemble import BoardError, build_board, checks_state, find_active_milestone, issue_number_from_pr
from tests.factories import CFG2, mk_run


def test_find_active_milestone(board1_gh):
    _, gh = board1_gh
    assert find_active_milestone(gh) == (3, 10)


def test_find_active_milestone_raises_without_coordination_issue(board1_gh):
    fake, gh = board1_gh
    fake.route("GET", "repos/acme/widgets/issues", [])
    with pytest.raises(BoardError, match="coordination"):
        find_active_milestone(gh)


def test_build_board_end_to_end(board1_gh):
    _, gh = board1_gh
    runs = {"claude": {12: mk_run("claude", 12)}, "codex": {}}
    spend = {"claude": {"medium": 1}, "codex": {}}
    b = build_board(gh, CFG2, runs, spend)
    assert (b.milestone, b.coordination_issue, b.phase, b.halted) == (3, 10, "executing", False)
    assert sorted(b.issues) == [11, 12, 13, 14]          # coordination issue and PR entry excluded
    assert b.issues[11].status == "ready" and b.issues[12].assignees == ("moot-claude-bot",)
    assert b.issues[14].status == "needs_human"
    assert sorted(b.pulls) == [19, 20]                    # non-moot branch excluded
    pr = b.pulls[20]
    assert pr.issue_number == 13 and pr.head_committer == "moot-codex-bot" and pr.checks == "green"
    assert pr.approvals_on_head() == {"moot-claude-bot"}
    assert b.pulls[19].merged and b.pulls[19].reviews == ()
    assert b.open_challenges == {"#12"} and b.is_challenged("#12")
    assert b.ignored_comments == 1
    assert b.run_for("claude", 12).alive
    assert b.spend == spend
    assert [c.header.verb for c in b.comments if c.ok] == ["ROSTER", "APPROVE", "CHALLENGE"]


def test_checks_state():
    assert checks_state({"total_count": 0, "check_runs": []}) == "none"
    assert checks_state({"total_count": 1, "check_runs": [{"status": "in_progress", "conclusion": None}]}) == "pending"
    assert checks_state({"total_count": 2, "check_runs": [{"status": "completed", "conclusion": "success"}, {"status": "completed", "conclusion": "skipped"}]}) == "green"
    assert checks_state({"total_count": 1, "check_runs": [{"status": "completed", "conclusion": "failure"}]}) == "red"


def test_issue_number_from_pr():
    assert issue_number_from_pr("Closes #13", "moot/13-x") == 13
    assert issue_number_from_pr("fixes   #7 and more", "whatever") == 7
    assert issue_number_from_pr("no keyword", "moot/42-slug") == 42
    assert issue_number_from_pr("nothing", "feature/x") is None


def test_build_board_fetches_issues_once(board1_gh):
    fake, gh = board1_gh
    build_board(gh, CFG2, {"claude": {}, "codex": {}}, {"claude": {}, "codex": {}})
    issue_list_calls = [c for c in fake.calls if c.method == "GET" and c.endpoint == "repos/acme/widgets/issues"]
    assert len(issue_list_calls) == 1


def test_build_board_with_explicit_milestone_missing_coordination(board1_gh):
    fake, gh = board1_gh
    fake.route("GET", "repos/acme/widgets/issues", [])
    with pytest.raises(BoardError, match="coordination"):
        build_board(gh, CFG2, {}, {}, milestone=3)
