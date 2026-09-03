from moot.board import Issue, derive_halted, derive_open_challenges, derive_phase
from tests.factories import CFG2, mk_board, mk_comment, mk_issue, mk_pr, mk_run


def test_issue_status_precedence_and_labels():
    i = mk_issue(1, status="ready")
    assert i.status == "ready"
    assert i.worker == "backend" and i.model == "claude-sonnet-5" and i.effort == "medium" and i.size == "S"
    both = Issue(2, "t", "", frozenset({"moot:blocked", "moot:needs-human", "moot:in-progress"}), (), "open", 3)
    assert both.status == "needs_human"
    assert Issue(3, "t", "", frozenset(), (), "open", 3).status == "unlabeled"
    assert Issue(4, "t", "", frozenset({"moot:ready"}), (), "closed", 3).status == "closed"
    assert mk_issue(5, extra_labels=("moot:challenged",)).challenged
    assert mk_issue(6, worker=None).worker is None


def test_pr_review_math_uses_latest_review_per_author_on_head():
    pr = mk_pr(20, 13, committer="moot-codex-bot", approvals=("moot-claude-bot",), stale_approvals=("empowerScott",))
    assert pr.approvals_on_head() == {"moot-claude-bot"}
    assert pr.changes_requested_on_head() == frozenset()
    pr2 = mk_pr(21, 14, changes_requested=("moot-claude-bot",), extra_changes_requested_history=2)
    assert pr2.changes_requested_on_head() == {"moot-claude-bot"}
    assert pr2.changes_requested_total() == 3
    assert pr2.latest_reviews()["moot-claude-bot"].state == "CHANGES_REQUESTED"


def test_derive_phase_and_halted():
    assert derive_phase(frozenset({"moot:coordination"})) == "planning"
    assert derive_phase(frozenset({"moot:coordination", "moot:plan-approved"})) == "executing"
    cs = [mk_comment(CFG2, 1, "empowerScott", "[human] HALT\n\nstop"),
          mk_comment(CFG2, 2, "randouser", "[human] RESUME\n\ngo")]
    assert derive_halted(cs) is True  # imposter RESUME ignored
    cs.append(mk_comment(CFG2, 3, "empowerScott", "[human] RESUME\n\ngo"))
    assert derive_halted(cs) is False


def test_derive_open_challenges_closes_on_verdict_or_retract():
    cs = [mk_comment(CFG2, 1, "moot-codex-bot", "[codex] CHALLENGE #12\n\ndoubt"),
          mk_comment(CFG2, 2, "moot-codex-bot", "[codex] CHALLENGE #20\n\ndoubt"),
          mk_comment(CFG2, 3, "moot-claude-bot", "[claude] RETRACT #20\n\nfair"),
          mk_comment(CFG2, 4, "moot-codex-bot", "[codex] CHALLENGE plan-1\n\ndoubt"),
          mk_comment(CFG2, 5, "empowerScott", "[human] DISMISSED plan-1\n\nfine")]
    assert derive_open_challenges(cs) == {"#12"}


def test_board_helpers():
    b = mk_board(CFG2,
                 issues=[mk_issue(11), mk_issue(12, status="in_progress", assignees=("moot-claude-bot",)),
                         mk_issue(13, status="in_review", assignees=("moot-claude-bot",))],
                 pulls=[mk_pr(20, 13)],
                 runs={"claude": {12: mk_run("claude", 12)}, "codex": {}},
                 open_challenges=frozenset({"#12"}))
    assert b.in_progress_count("moot-claude-bot") == 1
    assert b.pr_for_issue(13).number == 20 and b.pr_for_issue(11) is None
    assert b.run_for("claude", 12).alive and b.run_for("codex", 12) is None
    assert b.is_challenged("#12") and not b.is_challenged("#11")
    assert not mk_run("claude", 1, alive=True).finished
    assert mk_run("claude", 1, alive=False).finished
    assert not mk_run("claude", 1, alive=False, collected=True).finished
