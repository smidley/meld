from meld.board import (
    Issue,
    Lease,
    assigned_leases,
    derive_halted,
    derive_leases,
    derive_open_challenges,
    derive_phase,
    parse_lease_until,
    utc_now_iso,
)
from tests.factories import CFG2, mk_board, mk_comment, mk_issue, mk_lease, mk_pr, mk_run


def test_issue_status_precedence_and_labels():
    i = mk_issue(1, status="ready")
    assert i.status == "ready"
    assert i.worker == "backend" and i.model == "claude-sonnet-5" and i.effort == "medium" and i.size == "S"
    both = Issue(2, "t", "", frozenset({"meld:blocked", "meld:needs-human", "meld:in-progress"}), (), "open", 3)
    assert both.status == "needs_human"
    assert Issue(3, "t", "", frozenset(), (), "open", 3).status == "unlabeled"
    assert Issue(4, "t", "", frozenset({"meld:ready"}), (), "closed", 3).status == "closed"
    assert mk_issue(5, extra_labels=("meld:challenged",)).challenged
    assert mk_issue(6, worker=None).worker is None


def test_pr_review_math_uses_latest_review_per_author_on_head():
    pr = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-claude-bot",), stale_approvals=("empowerScott",))
    assert pr.approvals_on_head() == {"meld-claude-bot"}
    assert pr.changes_requested_on_head() == frozenset()
    pr2 = mk_pr(21, 14, changes_requested=("meld-claude-bot",), extra_changes_requested_history=2)
    assert pr2.changes_requested_on_head() == {"meld-claude-bot"}
    assert pr2.changes_requested_total() == 3
    assert pr2.latest_reviews()["meld-claude-bot"].state == "CHANGES_REQUESTED"


def test_derive_phase_and_halted():
    assert derive_phase(frozenset({"meld:coordination"})) == "planning"
    assert derive_phase(frozenset({"meld:coordination", "meld:plan-approved"})) == "executing"
    cs = [mk_comment(CFG2, 1, "empowerScott", "[human] HALT\n\nstop"),
          mk_comment(CFG2, 2, "randouser", "[human] RESUME\n\ngo")]
    assert derive_halted(cs) is True  # imposter RESUME ignored
    cs.append(mk_comment(CFG2, 3, "empowerScott", "[human] RESUME\n\ngo"))
    assert derive_halted(cs) is False


def test_derive_open_challenges_closes_on_verdict_or_retract():
    cs = [mk_comment(CFG2, 1, "meld-codex-bot", "[codex] CHALLENGE #12\n\ndoubt"),
          mk_comment(CFG2, 2, "meld-codex-bot", "[codex] CHALLENGE #20\n\ndoubt"),
          mk_comment(CFG2, 3, "meld-claude-bot", "[claude] RETRACT #20\n\nfair"),
          mk_comment(CFG2, 4, "meld-codex-bot", "[codex] CHALLENGE plan-1\n\ndoubt"),
          mk_comment(CFG2, 5, "empowerScott", "[human] DISMISSED plan-1\n\nfine")]
    assert derive_open_challenges(cs) == {"#12"}


def test_board_helpers():
    b = mk_board(CFG2,
                 issues=[mk_issue(11), mk_issue(12, status="in_progress", assignees=("meld-claude-bot",)),
                         mk_issue(13, status="in_review", assignees=("meld-claude-bot",))],
                 pulls=[mk_pr(20, 13)],
                 runs={"claude": {12: mk_run("claude", 12)}, "codex": {}},
                 open_challenges=frozenset({"#12"}))
    assert b.in_progress_count("meld-claude-bot") == 1
    assert b.pr_for_issue(13).number == 20 and b.pr_for_issue(11) is None
    assert b.run_for("claude", 12).alive and b.run_for("codex", 12) is None
    assert b.is_challenged("#12") and not b.is_challenged("#11")
    assert not mk_run("claude", 1, alive=True).finished
    assert mk_run("claude", 1, alive=False).finished
    assert not mk_run("claude", 1, alive=False, collected=True).finished


def test_parse_lease_until_and_derive_leases():
    assert parse_lease_until("until: 2026-09-03T13:00:00Z\nrun: claude/#12") == "2026-09-03T13:00:00Z"
    assert parse_lease_until("no lease here") is None
    cs = [mk_comment(CFG2, 1, "meld-claude-bot", "[claude:pm] LEASE #12\n\nuntil: 2026-09-03T13:00:00Z"),
          mk_comment(CFG2, 2, "meld-claude-bot", "[claude:pm] LEASE #12\n\nuntil: 2026-09-03T13:30:00Z"),
          mk_comment(CFG2, 3, "meld-codex-bot", "[codex:pm] LEASE #13\n\nuntil: 2026-09-03T14:00:00Z"),
          mk_comment(CFG2, 4, "randouser", "[codex:pm] LEASE #14\n\nuntil: 2099-01-01T00:00:00Z"),
          mk_comment(CFG2, 5, "meld-claude-bot", "[claude:pm] EXPIRED #13\n\nlease expired")]
    leases = derive_leases(cs)
    assert leases == {12: Lease(12, "claude", "2026-09-03T13:30:00Z", 2)}
    assert len(utc_now_iso()) == 20 and utc_now_iso().endswith("Z")
    b = mk_board(CFG2, issues=[mk_issue(12, status="in_progress", assignees=("meld-claude-bot",))],
                 leases={12: mk_lease(12, until="2026-09-03T11:00:00Z")}, now="2026-09-03T12:00:00Z")
    assert b.lease_expired(12) and not b.lease_expired(99)


def test_malformed_lease_until_is_dropped():
    assert parse_lease_until("until: 2026-09-03T13:00:00+00:00") is None
    assert parse_lease_until("until: notadate") is None
    assert parse_lease_until("until: 2026-09-03T13:00:00Z") == "2026-09-03T13:00:00Z"
    cs = [mk_comment(CFG2, 1, "meld-claude-bot", "[claude:pm] LEASE #12\n\nuntil: soon")]
    assert derive_leases(cs) == {}


def test_assigned_leases_keeps_only_leases_whose_bot_still_holds_the_issue():
    bots = {"claude": "meld-claude-bot", "codex": "meld-codex-bot"}
    issues = {i.number: i for i in (
        mk_issue(12, status="in_progress", assignees=("meld-claude-bot",)),   # claude holds it: lease stands
        mk_issue(13, status="in_progress", assignees=("meld-codex-bot",)),    # codex re-claimed it: claude's stale lease is void
        mk_issue(14, status="ready"),                                         # released: nobody holds it
    )}
    leases = {12: mk_lease(12, "claude"), 13: mk_lease(13, "claude"), 14: mk_lease(14, "claude"), 15: mk_lease(15, "claude"),
              16: mk_lease(16, "gemini")}
    assert assigned_leases(leases, issues, bots) == {12: leases[12]}
    assert assigned_leases({13: mk_lease(13, "codex")}, issues, bots) == {13: mk_lease(13, "codex")}
