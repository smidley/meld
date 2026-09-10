import json
from pathlib import Path

import pytest

from meld.assemble import BoardError, build_board, checks_state, find_active_milestone, issue_number_from_pr
from meld.github import RateLimited
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
    assert b.issues[11].status == "ready" and b.issues[12].claimed_by == "claude"
    assert b.issues[14].status == "needs_human"
    assert sorted(b.pulls) == [19, 20]                    # non-meld branch excluded
    pr = b.pulls[20]
    assert pr.issue_number == 13 and pr.head_committer == "meld-codex-bot" and pr.checks == "green"
    assert pr.approvals_on_head() == {"meld-claude-bot"}
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
    assert issue_number_from_pr("Closes #13", "meld/13-x") == 13
    assert issue_number_from_pr("fixes   #7 and more", "whatever") == 7
    assert issue_number_from_pr("no keyword", "meld/42-slug") == 42
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


def test_head_committer_is_none_when_the_last_commit_maps_to_no_account(board1_gh):
    fake, gh = board1_gh
    fake.route("GET", "repos/acme/widgets/pulls/20/commits",
               [{"sha": "abc122", "author": {"login": "meld-codex-bot"}, "committer": {"login": "meld-codex-bot"}},
                {"sha": "abc123", "author": None, "committer": None}])
    b = build_board(gh, CFG2, {}, {})
    assert b.pulls[20].head_committer is None
    assert b.pulls[20].author == "meld-codex-bot"   # the PR author is never used as a stand-in
    # an empty login is falsy but not None, and must normalise to None too
    fake.route("GET", "repos/acme/widgets/pulls/20/commits", [{"sha": "abc123", "author": None, "committer": {"login": ""}}])
    assert build_board(gh, CFG2, {}, {}).pulls[20].head_committer is None


def test_build_board_reads_leases_and_plan_text(board1_gh):
    fake, gh = board1_gh
    comments = json.loads((Path(__file__).parent / "fixtures" / "board1" / "comments_10.json").read_text())
    comments.append({"id": 110, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:20:00Z",
                     "body": "[claude:pm] LEASE #12\n\nuntil: 2026-09-03T10:30:00Z\nrun: claude/#12"})
    fake.route("GET", "repos/acme/widgets/issues/10/comments", comments)
    b = build_board(gh, CFG2, {"claude": {}, "codex": {}}, {"claude": {}, "codex": {}}, now="2026-09-03T11:00:00Z")
    assert b.leases[12].group == "claude" and b.leases[12].until == "2026-09-03T10:30:00Z" and b.leases[12].comment_id == 110
    assert b.lease_expired(12)
    assert b.plan_text == "approved plan text"
    assert b.now == "2026-09-03T11:00:00Z"


def test_build_board_drops_a_lease_once_another_group_holds_the_issue(board1_gh):
    fake, gh = board1_gh
    comments = json.loads((Path(__file__).parent / "fixtures" / "board1" / "comments_10.json").read_text())
    # fixture #13 is assigned to meld-codex-bot; a stale claude lease on it asserts nothing any more
    comments.append({"id": 110, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:20:00Z",
                     "body": "[claude:pm] LEASE #13\n\nuntil: 2026-09-03T10:30:00Z\nrun: claude/#13"})
    comments.append({"id": 111, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:21:00Z",
                     "body": "[codex:pm] LEASE #14\n\nuntil: 2026-09-03T10:30:00Z\nrun: codex/#14"})
    fake.route("GET", "repos/acme/widgets/issues/10/comments", comments)
    b = build_board(gh, CFG2, {}, {})
    assert 13 not in b.leases and b.leases[14].group == "codex"   # #14 is assigned to meld-codex-bot


def test_board_carries_files_challenges_audits_and_merge_metadata(board1_gh):
    fake, gh = board1_gh
    board = build_board(gh, CFG2, {}, {}, milestone=3, now="2026-09-03T12:00:00Z")
    assert board.pulls[20].files == ("ci.yml", ".github/workflows/ci.yml")
    assert board.pulls[19].merged and board.pulls[19].merged_at and board.pulls[19].files == ()   # merged PRs skip the files fetch
    assert board.challenges["#12"].challenger == "codex" and board.open_challenges == frozenset({"#12"})
    assert board.audits == {}


def test_board_carries_the_head_statuses(board1_gh):
    fake, gh = board1_gh
    fake.route("GET", "repos/acme/widgets/commits/abc123/status",
               {"state": "success", "statuses": [{"context": "meld/review/codex", "state": "success"}, {"context": "ci", "state": "pending"}]})
    board = build_board(gh, CFG2, {}, {})
    assert board.pulls[20].statuses == {"meld/review/codex": "success", "ci": "pending"}
    assert board.pulls[19].statuses == {}                # merged PRs are never read


def test_missing_commit_statuses_permission_degrades_to_an_empty_status_map(board1_gh):
    fake, gh = board1_gh
    fake.route("GET", "repos/acme/widgets/commits/abc123/status", {"message": "Resource not accessible by personal access token"}, status=403)
    errors: list[str] = []
    board = build_board(gh, CFG2, {}, {}, errors=errors)    # the board still builds: statuses only ever add approvals
    assert board.pulls[20].statuses == {} and board.pulls[20].head_committer == "meld-codex-bot"
    (note,) = errors
    assert note.startswith("PR #20: commit statuses unavailable (GET repos/acme/widgets/commits/abc123/status") and "HTTP 403" in note
    assert note.endswith("check the token's Commit statuses permission")
    build_board(gh, CFG2, {}, {}, errors=errors)
    assert len(errors) == 1                                # one line per PR, however many boards a process builds
    assert build_board(gh, CFG2, {}, {}).pulls[20].statuses == {}   # with no collector the problem is simply swallowed
    fake.rate_limit_next = True                            # a rate limit is not a permissions problem and still propagates
    with pytest.raises(RateLimited):
        build_board(gh, CFG2, {}, {}, errors=errors)


def test_build_board_fills_the_bot_logins_and_reads_claims_from_labels(tmp_path):
    from tests.productfixtures import make_product
    from meld.status import build_status
    fake, ctx, repo, origin = make_product(tmp_path)
    _, board = build_status(ctx)
    assert board.bots == {"claude": "meld-claude-bot", "codex": "meld-codex-bot"}
    assert board.issues[12].claimed_by == "claude" and board.issues[13].claimed_by == "codex" and board.issues[11].claimed_by is None
