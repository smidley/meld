from meld.config import Party
from meld.rules import (
    AUTHOR_RULE,
    Actor,
    Verb,
    approving_parties,
    author_party,
    committer_party,
    legal_pr_verbs,
    pingpong_exceeded,
    pr_refusal,
    review_verdict_rule,
    status_context,
)
from tests.factories import CFG2, CFG3, mk_board, mk_cfg, mk_pr

CLAUDE_PM = Actor(Party("pm", "claude"), "meld-claude-bot")
CLAUDE_ORCH = Actor(Party("group", "claude"), "meld-claude-bot")
CODEX_PM = Actor(Party("pm", "codex"), "meld-codex-bot")
GEMINI_PM = Actor(Party("pm", "gemini"), "meld-gemini-bot")
HUMAN = Actor(Party("human"), "empowerScott")
ROSTER = frozenset({"backend", "reviewer", "auditor"})
CODEX_ORCH = Actor(Party("group", "codex"), "meld-codex-bot", ROSTER)


def rule(board, cfg, actor, verb, pr):
    r = pr_refusal(board, cfg, actor, verb, pr)
    return None if r is None else r.rule


def prule(board, actor, verb, pr):
    """Alias for the Task 4 review/audit/reap tests below, which never vary cfg from CFG2."""
    return rule(board, CFG2, actor, verb, pr)


def test_committer_and_approving_parties():
    pr = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-claude-bot", "meld-codex-bot", "empowerScott", "randouser"))
    assert committer_party(CFG2, pr) == "codex"
    assert approving_parties(CFG2, pr) == {"claude", "human:empowerScott"}


def test_approve_rules():
    pr = mk_pr(20, 13, committer="meld-codex-bot")
    b = mk_board(CFG2, pulls=[pr])
    assert rule(b, CFG2, CLAUDE_PM, Verb.APPROVE, pr) is None
    assert rule(b, CFG2, CLAUDE_ORCH, Verb.APPROVE, pr) is None
    assert rule(b, CFG2, HUMAN, Verb.APPROVE, pr) is None
    assert rule(b, CFG2, CODEX_PM, Verb.APPROVE, pr) == "codex made the last commit on this PR and cannot approve its own head"
    fixed = mk_pr(21, 14, committer="meld-claude-bot", author="meld-codex-bot")  # claude pushed a fix onto codex's PR
    b2 = mk_board(CFG2, pulls=[fixed])
    # codex no longer holds the head, but GitHub refuses an APPROVE from the account that opened the PR: the GitHub verb
    # is refused, and codex's verdict reaches the quorum through its `meld/review/codex` status instead (below).
    assert rule(b2, CFG2, CODEX_PM, Verb.APPROVE, fixed) == AUTHOR_RULE
    assert rule(b2, CFG2, CLAUDE_PM, Verb.APPROVE, fixed) == "claude made the last commit on this PR and cannot approve its own head"
    challenged = mk_board(CFG2, pulls=[pr], open_challenges=frozenset({"#20"}))
    assert rule(challenged, CFG2, CLAUDE_PM, Verb.APPROVE, pr) == "PR has an open CHALLENGE"
    closed = mk_pr(22, 15, state="closed", merged=True)
    assert rule(mk_board(CFG2, pulls=[closed]), CFG2, CLAUDE_PM, Verb.APPROVE, closed) == "PR is not open"


def test_request_changes_and_push_fix_need_a_different_committer():
    pr = mk_pr(20, 13, committer="meld-codex-bot")
    b = mk_board(CFG2, pulls=[pr])
    for verb in (Verb.REQUEST_CHANGES, Verb.PUSH_FIX):
        assert rule(b, CFG2, CLAUDE_PM, verb, pr) is None
        assert rule(b, CFG2, HUMAN, verb, pr) is None
        assert rule(b, CFG2, CODEX_PM, verb, pr) == f"codex made the last commit on this PR; only other parties may {verb.value}"


def test_merge_quorum_two_groups():
    unreviewed = mk_pr(20, 13, committer="meld-codex-bot")
    assert rule(mk_board(CFG2, pulls=[unreviewed]), CFG2, CLAUDE_PM, Verb.MERGE, unreviewed) == \
        "0/1 approvals from non-committer parties (have: [])"
    self_approved = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-codex-bot",))
    assert "0/1 approvals" in rule(mk_board(CFG2, pulls=[self_approved]), CFG2, CLAUDE_PM, Verb.MERGE, self_approved)
    approved = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-claude-bot",))
    b = mk_board(CFG2, pulls=[approved])
    assert rule(b, CFG2, CLAUDE_PM, Verb.MERGE, approved) is None
    assert rule(b, CFG2, CODEX_PM, Verb.MERGE, approved) is None  # either PM may merge
    assert rule(b, CFG2, HUMAN, Verb.MERGE, approved) is None
    assert rule(b, CFG2, CLAUDE_ORCH, Verb.MERGE, approved) == "only a PM or a human may merge"
    human_approved = mk_pr(21, 14, committer="meld-codex-bot", approvals=("empowerScott",))
    assert rule(mk_board(CFG2, pulls=[human_approved]), CFG2, CODEX_PM, Verb.MERGE, human_approved) is None


def test_merge_quorum_three_groups():
    one = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-claude-bot",))
    assert rule(mk_board(CFG3, pulls=[one]), CFG3, CLAUDE_PM, Verb.MERGE, one) == \
        "1/2 approvals from non-committer parties (have: ['claude'])"
    two = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-claude-bot", "meld-gemini-bot"))
    assert rule(mk_board(CFG3, pulls=[two]), CFG3, GEMINI_PM, Verb.MERGE, two) is None
    human_plus_one = mk_pr(21, 14, committer="meld-codex-bot", approvals=("meld-claude-bot", "empowerScott"))
    assert rule(mk_board(CFG3, pulls=[human_plus_one]), CFG3, CLAUDE_PM, Verb.MERGE, human_plus_one) is None


def test_merge_blockers_and_stale_approvals():
    approved = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-claude-bot",))
    assert rule(mk_board(CFG2, pulls=[approved], halted=True), CFG2, CLAUDE_PM, Verb.MERGE, approved) == "HALT is in effect"
    assert rule(mk_board(CFG2, pulls=[approved], open_challenges=frozenset({"#20"})), CFG2, CLAUDE_PM, Verb.MERGE, approved) == "PR has an open CHALLENGE"
    red = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-claude-bot",), checks="red")
    assert rule(mk_board(CFG2, pulls=[red]), CFG2, CLAUDE_PM, Verb.MERGE, red) == "required checks are red, not green"
    pending = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-claude-bot",), checks="pending")
    assert rule(mk_board(CFG2, pulls=[pending]), CFG2, CLAUDE_PM, Verb.MERGE, pending) == "required checks are pending, not green"
    stale = mk_pr(20, 13, committer="meld-codex-bot", stale_approvals=("meld-claude-bot",))
    assert "0/1 approvals" in rule(mk_board(CFG2, pulls=[stale]), CFG2, CLAUDE_PM, Verb.MERGE, stale)


def test_merge_refuses_out_of_scope_changes_and_unlisted_deletions():
    from tests.factories import mk_board, mk_issue, mk_pr
    body = "## Scope\n- src/meld/*.py\n\n## May delete\n- src/meld/legacy.py\n"
    issue = mk_issue(12, status="in_review", claimed_by="claude", body=body)
    ok = mk_pr(20, 12, files=("src/meld/board.py",), approvals=("meld-codex-bot",))          # whatever the file's mergeable arrangement is
    out = mk_pr(21, 12, files=("src/meld/board.py", "README.md"), approvals=("meld-codex-bot",))
    dele = mk_pr(22, 12, files=("src/meld/board.py", "src/meld/legacy.py", "src/meld/old.py"), removed=("src/meld/legacy.py", "src/meld/old.py"), approvals=("meld-codex-bot",))
    board = mk_board(CFG2, issues=[issue], pulls=[ok, out, dele], phase="executing")
    assert rule(board, CFG2, CLAUDE_PM, Verb.MERGE, ok) is None
    assert rule(board, CFG2, CLAUDE_PM, Verb.MERGE, out) == "PR changes files outside #12's declared scope: README.md"
    assert rule(board, CFG2, CLAUDE_PM, Verb.MERGE, dele) == "PR deletes files not listed under '## May delete' in #12: src/meld/old.py"


def test_pingpong_and_legal_pr_verbs():
    pr = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-claude-bot",), extra_changes_requested_history=3)
    assert pingpong_exceeded(CFG2, pr)
    assert not pingpong_exceeded(CFG2, mk_pr(21, 14, extra_changes_requested_history=2))
    b = mk_board(CFG2, pulls=[pr])
    # claude is not the head committer, but it already approved this head, so review is not listed (one review slot per
    # group per head, spec 13 step 2); remember (plan 5 task 1) is legal on any resolved, unchallenged board item
    # regardless of committer/author, so it closes out both lists
    assert legal_pr_verbs(b, CFG2, CLAUDE_PM, pr) == ["approve", "request-changes", "push-fix", "merge", "remember"]
    assert "review" in legal_pr_verbs(b, CFG2, CLAUDE_PM, mk_pr(22, 15, committer="meld-codex-bot"))
    assert legal_pr_verbs(b, CFG2, CODEX_PM, pr) == ["merge", "remember"]


def test_unknown_head_committer_blocks_approve_and_merge():
    unknown = "head committer unknown; cannot verify who made the last commit"
    pr = mk_pr(20, 13, committer=None, author="meld-codex-bot", approvals=("meld-claude-bot",))
    b = mk_board(CFG2, pulls=[pr])
    assert committer_party(CFG2, pr) is None
    assert rule(b, CFG2, CLAUDE_PM, Verb.APPROVE, pr) == unknown
    assert rule(b, CFG2, HUMAN, Verb.APPROVE, pr) == unknown
    assert rule(b, CFG2, CODEX_PM, Verb.MERGE, pr) == unknown
    # neither verb can approve anything, so both stay legal for a non-author party; the author (codex) may still
    # push-fix its own PR, but GitHub refuses its REQUEST_CHANGES review like its APPROVE
    for verb in (Verb.REQUEST_CHANGES, Verb.PUSH_FIX):
        assert rule(b, CFG2, CLAUDE_PM, verb, pr) is None
    assert rule(b, CFG2, CODEX_PM, Verb.PUSH_FIX, pr) is None
    assert rule(b, CFG2, CODEX_PM, Verb.REQUEST_CHANGES, pr) == AUTHOR_RULE
    # an unknown committer cannot be reviewed away from (party_of_login is None, so claude != it), so review would be
    # legal here even though approve/merge are blocked -- except that claude already approved this head, and a group
    # gets one review run per head; remember (plan 5 task 1) is legal regardless (it never looks at committer/author)
    assert legal_pr_verbs(b, CFG2, CLAUDE_PM, pr) == ["request-changes", "push-fix", "remember"]
    assert "review" in legal_pr_verbs(b, CFG2, CLAUDE_PM, mk_pr(20, 13, committer=None, author="meld-codex-bot"))


def test_author_party_is_refused_the_github_verbs_but_keeps_push_fix_and_review():
    fixed = mk_pr(21, 14, committer="meld-claude-bot", author="meld-codex-bot")   # claude push-fixed codex's PR
    b = mk_board(CFG2, pulls=[fixed])
    assert author_party(CFG2, fixed) == "codex" and committer_party(CFG2, fixed) == "claude"
    assert rule(b, CFG2, CODEX_PM, Verb.APPROVE, fixed) == AUTHOR_RULE
    assert rule(b, CFG2, CODEX_PM, Verb.REQUEST_CHANGES, fixed) == AUTHOR_RULE
    assert rule(b, CFG2, CODEX_PM, Verb.PUSH_FIX, fixed) is None
    # plan 5 task 1: approve/request-changes now show up too -- codex (the author) reaches them through the
    # comment-review path (review_verdict_rule), the same path `meld review` actually takes for its own PR's head
    assert legal_pr_verbs(b, CFG2, CODEX_PM, fixed) == ["approve", "request-changes", "push-fix", "review", "remember"]
    # a human's own PR: GitHub refuses the human's approval too, so status stops advertising it
    human_pr = mk_pr(22, 15, committer="meld-claude-bot", author="empowerScott")
    assert rule(mk_board(CFG2, pulls=[human_pr]), CFG2, HUMAN, Verb.APPROVE, human_pr) == AUTHOR_RULE
    # the committer rule still wins when the author also holds the head
    own = mk_pr(20, 13, committer="meld-codex-bot")
    assert rule(mk_board(CFG2, pulls=[own]), CFG2, CODEX_PM, Verb.APPROVE, own) == "codex made the last commit on this PR and cannot approve its own head"


def test_approving_parties_counts_the_author_group_through_its_success_status():
    fixed = mk_pr(21, 14, committer="meld-claude-bot", author="meld-codex-bot")
    assert approving_parties(CFG2, fixed) == frozenset()
    ok = mk_pr(21, 14, committer="meld-claude-bot", author="meld-codex-bot", statuses={status_context("codex"): "success"})
    assert approving_parties(CFG2, ok) == {"codex"}
    assert rule(mk_board(CFG2, pulls=[ok]), CFG2, CLAUDE_PM, Verb.MERGE, ok) is None      # the status clears the N=2 quorum
    assert rule(mk_board(CFG2, pulls=[ok]), CFG2, CODEX_PM, Verb.MERGE, ok) is None
    for state in ("pending", "failure", "error"):
        assert approving_parties(CFG2, mk_pr(21, 14, committer="meld-claude-bot", author="meld-codex-bot",
                                             statuses={status_context("codex"): state})) == frozenset()
    # only the author's own context counts, and only for the author
    assert approving_parties(CFG2, mk_pr(21, 14, committer="meld-claude-bot", author="meld-codex-bot",
                                         statuses={status_context("claude"): "success"})) == frozenset()
    # never when the author still holds the head (its status cannot legally exist, and a forged one must not count)
    assert approving_parties(CFG2, mk_pr(20, 13, committer="meld-codex-bot", statuses={status_context("codex"): "success"})) == frozenset()
    # a human author is not a group: humans approve in the UI, and every group reviews a human PR
    human_pr = mk_pr(22, 15, committer="meld-claude-bot", author="empowerScott", statuses={"meld/review/human:empowerScott": "success"})
    assert approving_parties(CFG2, human_pr) == frozenset()
    # GitHub approvals and the status path add up under N=3
    two = mk_pr(21, 14, committer="meld-claude-bot", author="meld-codex-bot", approvals=("meld-gemini-bot",), statuses={status_context("codex"): "success"})
    assert approving_parties(CFG3, two) == {"codex", "gemini"}
    assert rule(mk_board(CFG3, pulls=[two]), CFG3, GEMINI_PM, Verb.MERGE, two) is None


def test_review_verdict_rule_lets_the_author_group_review_a_head_another_group_pushed():
    fixed = mk_pr(21, 14, committer="meld-claude-bot", author="meld-codex-bot")
    b = mk_board(CFG2, pulls=[fixed])
    assert review_verdict_rule(b, CFG2, CODEX_PM, fixed, "approve") is None                 # legal, as a comment review
    assert review_verdict_rule(b, CFG2, CODEX_PM, fixed, "request-changes") is None
    assert review_verdict_rule(b, CFG2, CODEX_PM, fixed, "push-fix") is None
    assert review_verdict_rule(b, CFG2, CLAUDE_PM, fixed, "approve") == "claude made the last commit on this PR and cannot approve its own head"
    challenged = mk_board(CFG2, pulls=[fixed], open_challenges=frozenset({"#21"}))
    assert review_verdict_rule(challenged, CFG2, CODEX_PM, fixed, "approve") == "PR has an open CHALLENGE"
    assert review_verdict_rule(challenged, CFG2, CODEX_PM, fixed, "request-changes") is None   # like every request-changes
    # while the author still holds the head, nothing changes: its own head is not reviewable
    own = mk_pr(20, 13, committer="meld-codex-bot")
    assert review_verdict_rule(mk_board(CFG2, pulls=[own]), CFG2, CODEX_PM, own, "approve") == \
        "codex made the last commit on this PR and cannot review its own head"
    assert review_verdict_rule(mk_board(CFG2, pulls=[own]), CFG2, CODEX_PM, own, "request-changes") == \
        "codex made the last commit on this PR and cannot review its own head"
    unknown = mk_pr(21, 14, committer=None, author="meld-codex-bot")
    assert review_verdict_rule(mk_board(CFG2, pulls=[unknown]), CFG2, CODEX_PM, unknown, "approve") == \
        "head committer unknown; cannot verify who made the last commit"
    closed = mk_pr(22, 15, committer="meld-claude-bot", author="meld-codex-bot", state="closed", merged=True)
    assert review_verdict_rule(mk_board(CFG2, pulls=[closed]), CFG2, CODEX_PM, closed, "approve") == "PR is not open"
    # everyone else gets exactly pr_refusal's answer
    assert review_verdict_rule(b, CFG2, CLAUDE_PM, fixed, "request-changes") == "claude made the last commit on this PR; only other parties may request-changes"
    plain = mk_pr(20, 13, committer="meld-codex-bot")
    assert review_verdict_rule(mk_board(CFG2, pulls=[plain]), CFG2, CLAUDE_PM, plain, "approve") is None


from dataclasses import replace

from meld.rules import CONCERNS, review_concern
from tests.factories import CFG3, mk_run


def test_review_dispatch_legality():
    pr = mk_pr(20, 13, committer="meld-codex-bot")
    b = mk_board(CFG2, pulls=[pr])
    assert prule(b, CLAUDE_PM, Verb.REVIEW, pr) is None
    assert "only other parties may review" in prule(b, CODEX_PM, Verb.REVIEW, pr)
    assert prule(b, CLAUDE_ORCH, Verb.REVIEW, pr) == "only the PM may dispatch a review"
    assert prule(mk_board(CFG2, pulls=[pr], halted=True), CLAUDE_PM, Verb.REVIEW, pr) == "HALT is in effect"
    assert prule(mk_board(CFG2, pulls=[pr], open_challenges=frozenset({"#20"})), CLAUDE_PM, Verb.REVIEW, pr) == "PR has an open CHALLENGE"
    unknown = mk_pr(21, 14, committer=None)   # unknown head committer: a review may be dispatched; approve is refused later
    assert prule(mk_board(CFG2, pulls=[unknown]), CLAUDE_PM, Verb.REVIEW, unknown) is None
    busy = mk_board(CFG2, pulls=[pr], runs={"claude": {n: mk_run("claude", n, kind="review", pr=n) for n in range(30, 34)}})
    assert prule(busy, CLAUDE_PM, Verb.REVIEW, pr) == "claude is at its review concurrency cap (4/4)"
    waiting = mk_board(CFG2, pulls=[pr], runs={"claude": {20: mk_run("claude", 20, alive=False, collected=False, kind="review", pr=20)}})
    assert prule(waiting, CLAUDE_PM, Verb.REVIEW, pr) == "a run already exists for this PR (running or uncollected)"
    assert "review" in legal_pr_verbs(b, CFG2, CLAUDE_PM, pr) and "review" not in legal_pr_verbs(b, CFG2, CODEX_PM, pr)


def test_audit_dispatch_legality():
    merged = mk_pr(19, 9, committer="meld-claude-bot", state="closed", merged=True)
    b = mk_board(CFG2, pulls=[merged])
    assert prule(b, CODEX_PM, Verb.AUDIT, merged) is None
    assert prule(b, CLAUDE_PM, Verb.AUDIT, merged) == "cannot audit a PR authored by own group"
    assert prule(b, CODEX_ORCH, Verb.AUDIT, merged) == "only the PM may dispatch an audit"
    open_pr = mk_pr(20, 13)
    assert prule(mk_board(CFG2, pulls=[open_pr]), CODEX_PM, Verb.AUDIT, open_pr) == "audit targets a merged PR"
    assert prule(replace(b, audits={19: {"codex": "pass"}}), CODEX_PM, Verb.AUDIT, merged) == "codex already audited #19"
    assert prule(mk_board(CFG2, pulls=[merged], halted=True), CODEX_PM, Verb.AUDIT, merged) == "HALT is in effect"
    # plan 5 task 1: remember is legal too -- a lesson may be recorded about a merged, resolved PR
    assert legal_pr_verbs(b, CFG2, CODEX_PM, merged) == ["audit", "remember"]


def test_reap_legality_for_pr_runs():
    pr = mk_pr(20, 13)
    collected = mk_board(CFG2, pulls=[pr], runs={"claude": {20: mk_run("claude", 20, alive=False, collected=True, kind="review", pr=20)}})
    assert prule(collected, CLAUDE_PM, Verb.REAP, pr) is None          # a collected review run is reapable while the PR is open
    assert prule(mk_board(CFG2, pulls=[pr]), CLAUDE_PM, Verb.REAP, pr) == "no run record for #20 in claude"
    assert prule(collected, CLAUDE_ORCH, Verb.REAP, pr) == "only the PM may reap"


def test_review_concern_is_assigned_only_with_three_or_more_groups():
    pr = mk_pr(20, 13, committer="meld-codex-bot")
    assert review_concern(CFG2, pr, "claude") is None
    assert review_concern(CFG3, pr, "claude") == CONCERNS[0] and review_concern(CFG3, pr, "gemini") == CONCERNS[1]
    assert review_concern(CFG3, pr, "codex") is None                    # the head committer's group does not review
    # after a push-fix, the PR's author (claude) is a different party than the head committer (codex):
    # the author remains an eligible reviewer, per spec 13 step 3 (A's PM dispatches its own reviewer on the new head)
    pr = mk_pr(20, 13, committer="meld-codex-bot", author="meld-claude-bot")
    assert review_concern(CFG3, pr, "claude") == CONCERNS[0] and review_concern(CFG3, pr, "gemini") == CONCERNS[1]
    assert review_concern(CFG3, pr, "codex") is None                    # the head committer's group still does not review


def test_memory_pr_detection_and_review_dispatch_refusal():
    body = "Records the agreed decision `dec-1`.\n\nmeld-memory: decision dec-1 " + "a" * 64 + "\nverified-by: x"
    pr = replace(mk_pr(40, 0, committer="meld-claude-bot"), body=body, head_ref="meld/memory-dec-1")
    assert pr.memory == ("decision", "dec-1", "a" * 64) and mk_pr(20, 13).memory is None
    b = mk_board(CFG2, pulls=[pr])
    assert prule(b, CODEX_PM, Verb.REVIEW, pr) == "memory PR: run `meld review 40` directly, no worker needed"
    assert prule(b, CODEX_PM, Verb.APPROVE, pr) is None and "made the last commit" in prule(b, CLAUDE_PM, Verb.APPROVE, pr)


def test_only_meld_memory_branches_are_memory_prs():
    # N1: a worker's summary reaches an ordinary PR's body; a marker there must not turn a build PR into a "memory PR"
    # (which would refuse its reviewer, never return its issue to in_progress on REQUEST_CHANGES, and skip its audit).
    body = "Closes #13\n\nmeld-memory: lesson #13 " + "a" * 64 + "\n\nverified-by: x"
    smuggled = replace(mk_pr(20, 13, committer="meld-codex-bot"), body=body)            # head_ref meld/13-slug, as collect names them
    assert smuggled.memory is None
    b = mk_board(CFG2, pulls=[smuggled])
    assert prule(b, CLAUDE_PM, Verb.REVIEW, smuggled) is None                        # a reviewer can still be dispatched
    merged = replace(smuggled, state="closed", merged=True)
    assert prule(mk_board(CFG2, pulls=[merged]), CLAUDE_PM, Verb.AUDIT, merged) is None   # and it is audited once merged
    genuine = replace(mk_pr(40, 0, committer="meld-claude-bot"), body=body, head_ref="meld/memory-lesson-13-20260903T120000Z")
    assert genuine.memory == ("lesson", "#13", "a" * 64)                              # the real thing: memory branch AND marker


def test_a_merged_memory_pr_owes_no_audit():
    body = "Records the agreed decision `dec-1`.\n\nmeld-memory: decision dec-1 " + "a" * 64 + "\nverified-by: x"
    merged = replace(mk_pr(40, 0, committer="meld-claude-bot", state="closed", merged=True), body=body, head_ref="meld/memory-dec-1")
    b = mk_board(CFG2, pulls=[merged])
    assert prule(b, CODEX_PM, Verb.AUDIT, merged) == "memory PR: verified against the board at review, never audited"
    # plan 5 task 1: remember_rule doesn't exempt a memory PR from being a valid ref itself, so it's the one legal verb left
    assert legal_pr_verbs(b, CFG2, CODEX_PM, merged) == ["remember"]
    ordinary = mk_pr(19, 9, committer="meld-claude-bot", state="closed", merged=True)
    assert prule(mk_board(CFG2, pulls=[ordinary]), CODEX_PM, Verb.AUDIT, ordinary) is None


def test_review_dispatch_is_refused_for_a_head_the_group_already_reviewed():
    from dataclasses import replace
    from meld.board import Review
    fresh = mk_pr(20, 13, committer="meld-codex-bot")
    b = mk_board(CFG2, pulls=[fresh])
    assert rule(b, CFG2, CLAUDE_PM, Verb.REVIEW, fresh) is None
    already = "claude already reviewed head abc123; a new review needs a new head"
    assert rule(b, CFG2, CLAUDE_PM, Verb.REVIEW, mk_pr(20, 13, committer="meld-codex-bot", changes_requested=("meld-claude-bot",))) == already
    assert rule(b, CFG2, CLAUDE_PM, Verb.REVIEW, mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-claude-bot",))) == already
    # a verdict on an earlier head, another party's verdict, or a dismissed verdict leaves the slot open
    older = replace(fresh, reviews=(Review("meld-claude-bot", "CHANGES_REQUESTED", "old000", "2026-09-03T09:00:00Z"),))
    dismissed = replace(fresh, reviews=(Review("meld-claude-bot", "DISMISSED", "abc123", "2026-09-03T09:00:00Z"),))
    human = mk_pr(20, 13, committer="meld-codex-bot", changes_requested=("empowerScott",))
    plain = replace(fresh, reviews=(Review("meld-claude-bot", "COMMENTED", "abc123", "2026-09-03T09:00:00Z", body="looks fine so far"),
                                    Review("meld-claude-bot", "PENDING", "abc123", "2026-09-03T09:10:00Z")))
    for pr in (older, dismissed, human, plain):
        assert rule(b, CFG2, CLAUDE_PM, Verb.REVIEW, pr) is None
    # the author group's own verdicts after another group's push-fix reach the board as marked COMMENT reviews
    # (assemble._review): the promoted request-changes and the still-COMMENTED approve both spend claude's slot
    own = mk_pr(20, 13, committer="meld-codex-bot", author="meld-claude-bot", statuses={"meld/review/claude": "success"})
    approve = replace(own, reviews=(Review("meld-claude-bot", "COMMENTED", "abc123", "2026-09-03T11:00:00Z", as_author=True,
                                           body="meld-verdict: approve\n\nVerdict: approve"),))
    changes = replace(own, reviews=(Review("meld-claude-bot", "CHANGES_REQUESTED", "abc123", "2026-09-03T11:00:00Z", as_author=True,
                                           body="meld-verdict: request-changes\n\nVerdict: request-changes"),))
    for pr in (approve, changes):
        assert rule(mk_board(CFG2, pulls=[pr]), CFG2, CLAUDE_PM, Verb.REVIEW, pr) == already
    # a PR touching review.security_paths gets a second slot for the security reviewer, and no third
    sec_cfg = mk_cfg(security_paths=["auth/**"])
    one = mk_pr(20, 13, committer="meld-codex-bot", changes_requested=("meld-claude-bot",), files=("auth/login.py",))
    assert rule(mk_board(sec_cfg, pulls=[one]), sec_cfg, CLAUDE_PM, Verb.REVIEW, one) is None
    two = replace(one, reviews=one.reviews + (Review("meld-claude-bot", "APPROVED", "abc123", "2026-09-03T11:00:00Z"),))
    assert rule(mk_board(sec_cfg, pulls=[two]), sec_cfg, CLAUDE_PM, Verb.REVIEW, two) == (
        "claude already used both its review slots (reviewer and security) on head abc123; a new review needs a new head")
