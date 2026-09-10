from meld.config import Party
from meld.rules import ISSUE_VERBS, PR_VERBS, Actor, Refusal, Verb, issue_refusal, legal_issue_verbs, party_of_login
from tests.factories import CFG2, REG, mk_board, mk_comment, mk_issue, mk_lease, mk_pr, mk_run

ROSTER = frozenset({"backend", "frontend", "security", "qa", "reviewer", "auditor", "docs"})
CLAUDE_PM = Actor(Party("pm", "claude"), "meld-claude-bot", ROSTER)
CLAUDE_ORCH = Actor(Party("group", "claude"), "meld-claude-bot", ROSTER)
CODEX_PM = Actor(Party("pm", "codex"), "meld-codex-bot", ROSTER)
HUMAN = Actor(Party("human"), "empowerScott")


def rule(board, actor, verb, issue):
    r = issue_refusal(board, CFG2, actor, verb, issue, REG)
    return None if r is None else r.rule


# ---- claim -------------------------------------------------------------------

def test_claim_legal_for_pm_on_ready_issue():
    b = mk_board(CFG2, issues=[mk_issue(11)])
    assert rule(b, CLAUDE_PM, Verb.CLAIM, b.issues[11]) is None
    assert rule(b, HUMAN, Verb.CLAIM, b.issues[11]) is None


def test_claim_refusals_in_priority_order():
    i = mk_issue(11)
    assert rule(mk_board(CFG2, issues=[i], halted=True), CLAUDE_PM, Verb.CLAIM, i) == "HALT is in effect"
    assert "planning" in rule(mk_board(CFG2, issues=[i], phase="planning"), CLAUDE_PM, Verb.CLAIM, i)
    assert rule(mk_board(CFG2, issues=[i]), CLAUDE_ORCH, Verb.CLAIM, i) == "only a PM or a human may claim"
    blocked = mk_issue(12, status="blocked")
    assert rule(mk_board(CFG2, issues=[blocked]), CLAUDE_PM, Verb.CLAIM, blocked) == "issue is blocked, not ready"
    taken = mk_issue(13, claimed_by="codex")
    assert rule(mk_board(CFG2, issues=[taken]), CLAUDE_PM, Verb.CLAIM, taken) == "already claimed by codex"
    no_worker = mk_issue(14, worker=None)
    assert rule(mk_board(CFG2, issues=[no_worker]), CLAUDE_PM, Verb.CLAIM, no_worker) == "issue has no worker: label"
    marketing = mk_issue(15, worker="marketing")
    assert "not in claude's roster" in rule(mk_board(CFG2, issues=[marketing]), CLAUDE_PM, Verb.CLAIM, marketing)


def test_claim_respects_concurrency_cap():
    busy = [mk_issue(n, status="in_progress", claimed_by="claude") for n in range(100, 108)]
    fresh = mk_issue(11, model="gpt-test")  # known to codex, unknown to claude; the cap check comes before the model check
    b = mk_board(CFG2, issues=[*busy, fresh])
    assert rule(b, CLAUDE_PM, Verb.CLAIM, fresh) == "claude is at its concurrency cap (8/8)"
    assert rule(b, CODEX_PM, Verb.CLAIM, fresh) is None


def test_claim_requires_known_model_and_budget():
    no_model = mk_issue(11, model=None)
    assert rule(mk_board(CFG2, issues=[no_model]), CLAUDE_PM, Verb.CLAIM, no_model) == "issue has no model: label"
    odd = mk_issue(12, model="gpt-test")
    assert rule(mk_board(CFG2, issues=[odd]), CLAUDE_PM, Verb.CLAIM, odd) == "model 'gpt-test' is unknown for adapter 'claude'"
    assert rule(mk_board(CFG2, issues=[odd]), CODEX_PM, Verb.CLAIM, odd) is None
    i = mk_issue(13)
    spent = mk_board(CFG2, issues=[i], spend={"claude": {"medium": 80}, "codex": {}})
    assert rule(spent, CLAUDE_PM, Verb.CLAIM, i) == "claude has exhausted its medium-tier budget (80/80)"
    assert rule(spent, CODEX_PM, Verb.CLAIM, i) == "model 'claude-sonnet-5' is unknown for adapter 'codex'"


# ---- dispatch ----------------------------------------------------------------

def test_dispatch_legal_for_the_claiming_pm():
    i = mk_issue(12, status="in_progress", claimed_by="claude", size="M")
    assert rule(mk_board(CFG2, issues=[i]), CLAUDE_PM, Verb.DISPATCH, i) is None


def test_dispatch_refusals():
    i = mk_issue(12, status="in_progress", claimed_by="claude")
    assert rule(mk_board(CFG2, issues=[i], halted=True), CLAUDE_PM, Verb.DISPATCH, i) == "HALT is in effect"
    assert rule(mk_board(CFG2, issues=[i]), CLAUDE_ORCH, Verb.DISPATCH, i) == "only the PM may dispatch"
    assert rule(mk_board(CFG2, issues=[i]), HUMAN, Verb.DISPATCH, i) == "only the PM may dispatch"
    assert rule(mk_board(CFG2, issues=[i]), CODEX_PM, Verb.DISPATCH, i) == "issue is claimed by claude, not codex"
    ready = mk_issue(13)
    assert rule(mk_board(CFG2, issues=[ready]), CLAUDE_PM, Verb.DISPATCH, ready) == "issue is ready, not in-progress"
    running = mk_board(CFG2, issues=[i], runs={"claude": {12: mk_run("claude", 12)}, "codex": {}})
    assert "already exists" in rule(running, CLAUDE_PM, Verb.DISPATCH, i)
    uncollected = mk_board(CFG2, issues=[i], runs={"claude": {12: mk_run("claude", 12, alive=False)}, "codex": {}})
    assert "already exists" in rule(uncollected, CLAUDE_PM, Verb.DISPATCH, i)
    collected = mk_board(CFG2, issues=[i], runs={"claude": {12: mk_run("claude", 12, alive=False, collected=True)}, "codex": {}})
    assert rule(collected, CLAUDE_PM, Verb.DISPATCH, i) is None
    assert rule(mk_board(CFG2, issues=[i], open_challenges=frozenset({"#12"})), CLAUDE_PM, Verb.DISPATCH, i) == "issue has an open CHALLENGE"
    labelled = mk_issue(12, status="in_progress", claimed_by="claude", extra_labels=("meld:challenged",))
    assert rule(mk_board(CFG2, issues=[labelled]), CLAUDE_PM, Verb.DISPATCH, labelled) == "issue has an open CHALLENGE"


def test_dispatch_validates_model_effort_size():
    missing = mk_issue(12, status="in_progress", claimed_by="claude", effort=None, size=None)
    assert rule(mk_board(CFG2, issues=[missing]), CLAUDE_PM, Verb.DISPATCH, missing) == "issue is missing labels: effort, size"
    bad_effort = mk_issue(12, status="in_progress", claimed_by="claude", effort="turbo")
    assert "effort 'turbo' not valid" in rule(mk_board(CFG2, issues=[bad_effort]), CLAUDE_PM, Verb.DISPATCH, bad_effort)
    bad_size = mk_issue(12, status="in_progress", claimed_by="claude", size="XL")
    assert rule(mk_board(CFG2, issues=[bad_size]), CLAUDE_PM, Verb.DISPATCH, bad_size) == "size 'XL' is not one of S, M, L"
    too_big = mk_issue(12, status="in_progress", claimed_by="claude", model="claude-haiku-4-5-20251001", size="L")
    assert rule(mk_board(CFG2, issues=[too_big]), CLAUDE_PM, Verb.DISPATCH, too_big) == \
        "size L does not fit model claude-haiku-4-5-20251001 (context class medium)"


# ---- collect -----------------------------------------------------------------

def test_collect_rules():
    i = mk_issue(12, status="in_progress")
    none = mk_board(CFG2, issues=[i])
    assert rule(none, CLAUDE_PM, Verb.COLLECT, i) == "no run record for #12 in claude"
    alive = mk_board(CFG2, issues=[i], runs={"claude": {12: mk_run("claude", 12)}, "codex": {}})
    assert rule(alive, CLAUDE_PM, Verb.COLLECT, i) == "worker is still running"
    done = mk_board(CFG2, issues=[i], runs={"claude": {12: mk_run("claude", 12, alive=False)}, "codex": {}})
    assert rule(done, CLAUDE_PM, Verb.COLLECT, i) is None
    assert rule(done, CLAUDE_ORCH, Verb.COLLECT, i) == "only the PM may collect"
    twice = mk_board(CFG2, issues=[i], runs={"claude": {12: mk_run("claude", 12, alive=False, collected=True)}, "codex": {}})
    assert rule(twice, CLAUDE_PM, Verb.COLLECT, i) == "run already collected"


# ---- release -----------------------------------------------------------------

def test_release_rules():
    mine = mk_issue(12, status="in_progress", claimed_by="claude")
    b = mk_board(CFG2, issues=[mine])
    assert rule(b, CLAUDE_PM, Verb.RELEASE, mine) is None
    assert rule(b, CLAUDE_ORCH, Verb.RELEASE, mine) is None
    assert rule(b, CODEX_PM, Verb.RELEASE, mine) == "issue is not claimed by codex"
    assert rule(b, HUMAN, Verb.RELEASE, mine) is None
    ready = mk_issue(13)
    assert rule(mk_board(CFG2, issues=[ready]), HUMAN, Verb.RELEASE, ready) == \
        "issue is ready; release is only legal from in-progress or blocked, or from ready with a stale claim label"
    blocked = mk_issue(14, status="blocked", claimed_by="claude")
    assert rule(mk_board(CFG2, issues=[blocked]), CLAUDE_PM, Verb.RELEASE, blocked) is None


def test_any_pm_may_release_an_expired_lease():
    claimed = mk_issue(12, status="in_progress", claimed_by="claude")
    live = mk_board(CFG2, issues=[claimed], leases={12: mk_lease(12, until="2026-09-03T13:00:00Z")}, now="2026-09-03T12:00:00Z")
    assert rule(live, CODEX_PM, Verb.RELEASE, claimed) == "issue is not claimed by codex"
    expired = mk_board(CFG2, issues=[claimed], leases={12: mk_lease(12, until="2026-09-03T11:00:00Z")}, now="2026-09-03T12:00:00Z")
    assert rule(expired, CODEX_PM, Verb.RELEASE, claimed) is None
    assert rule(expired, CLAUDE_ORCH, Verb.RELEASE, claimed) is None  # the owner may always release its own
    codex_orch = Actor(Party("group", "codex"), "meld-codex-bot", ROSTER)
    assert rule(expired, codex_orch, Verb.RELEASE, claimed) == "issue is not claimed by codex"


def test_release_reaches_a_ready_issue_that_carries_a_stale_claim_label():
    """Decision 11: a PM killed between claim's label add and its status swap leaves meld:ready plus its own claim
    label. Release is legal there for a claimant and for a human, so the orphan is recoverable through meld."""
    stale = mk_issue(11, status="ready", claimed_by="claude")
    b = mk_board(CFG2, issues=[stale])
    assert rule(b, CLAUDE_PM, Verb.RELEASE, stale) is None
    assert rule(b, CLAUDE_ORCH, Verb.RELEASE, stale) is None
    assert rule(b, HUMAN, Verb.RELEASE, stale) is None
    # no lease exists (nothing was dispatched), so another group is still refused by the ownership clause
    assert rule(b, CODEX_PM, Verb.RELEASE, stale) == "issue is not claimed by codex"
    # a contested ready issue is releasable by either claimant, which sheds both labels
    contested = mk_issue(12, status="ready", extra_labels=("meld:claimed-by:claude", "meld:claimed-by:codex"))
    cb = mk_board(CFG2, issues=[contested])
    assert rule(cb, CLAUDE_PM, Verb.RELEASE, contested) is None
    assert rule(cb, CODEX_PM, Verb.RELEASE, contested) is None
    # an unclaimed issue in any other status still refuses, and the refusal names the stale-label case
    for status in ("ready", "in_review", "planned"):
        free = mk_issue(13, status=status)
        assert rule(mk_board(CFG2, issues=[free]), HUMAN, Verb.RELEASE, free) == \
            f"issue is {status}; release is only legal from in-progress or blocked, or from ready with a stale claim label"


def test_a_group_re_claims_past_its_own_stale_claim_label():
    """Decision 11: the group's own stale label is not another party's, so its next loop self-heals the issue."""
    own = mk_issue(11, status="ready", claimed_by="claude")
    b = mk_board(CFG2, issues=[own])
    assert rule(b, CLAUDE_PM, Verb.CLAIM, own) is None
    assert rule(b, CODEX_PM, Verb.CLAIM, own) == "already claimed by claude"
    human_held = mk_issue(12, status="ready", claimed_by="human")
    hb = mk_board(CFG2, issues=[human_held])
    assert rule(hb, HUMAN, Verb.CLAIM, human_held) is None          # one shared human label: a human re-claims past it
    assert rule(hb, CLAUDE_PM, Verb.CLAIM, human_held) == "already claimed by human"
    assert rule(b, HUMAN, Verb.CLAIM, own) == "already claimed by claude"
    contested = mk_issue(13, status="ready", extra_labels=("meld:claimed-by:claude", "meld:claimed-by:codex"))
    cb = mk_board(CFG2, issues=[contested])
    assert rule(cb, CLAUDE_PM, Verb.CLAIM, contested) == "already claimed by claude, codex"
    assert rule(cb, CODEX_PM, Verb.CLAIM, contested) == "already claimed by claude, codex"


# ---- reopen ------------------------------------------------------------------

def test_reopen_requires_fail_audit_from_non_author_group():
    closed = mk_issue(9, state="closed")
    pr = mk_pr(19, 9, committer="meld-claude-bot", state="closed", merged=True)
    audit = mk_comment(CFG2, 7, "meld-codex-bot", "[codex] AUDIT #9 FAIL\n\nacceptance criterion 2 unmet\n\nverified-by: pytest -k healthz")
    b = mk_board(CFG2, issues=[closed], pulls=[pr], comments=[audit])
    assert rule(b, CODEX_PM, Verb.REOPEN, closed) is None
    assert rule(b, CLAUDE_PM, Verb.REOPEN, closed) == "no AUDIT #9 FAIL posted by claude"
    self_audit = mk_comment(CFG2, 8, "meld-claude-bot", "[claude] AUDIT #9 FAIL\n\nnope")
    b2 = mk_board(CFG2, issues=[closed], pulls=[pr], comments=[audit, self_audit])
    assert rule(b2, CLAUDE_PM, Verb.REOPEN, closed) == "claude authored the PR that closed #9"
    assert rule(b, HUMAN, Verb.REOPEN, closed) is None
    open_issue = mk_issue(11)
    assert rule(mk_board(CFG2, issues=[open_issue]), HUMAN, Verb.REOPEN, open_issue) == "issue is not closed"


def test_reopen_accepts_a_fail_audit_posted_on_the_pr_that_closed_the_issue():
    closed = mk_issue(9, state="closed")
    pr = mk_pr(19, 9, committer="meld-codex-bot", state="closed", merged=True)
    pr_audit = mk_comment(CFG2, 7, "meld-claude-bot", "[claude] AUDIT #19 FAIL\n\nacceptance criterion 2 unmet")
    b = mk_board(CFG2, issues=[closed], pulls=[pr], comments=[pr_audit])
    assert rule(b, CLAUDE_PM, Verb.REOPEN, closed) is None
    unrelated = mk_comment(CFG2, 8, "meld-codex-bot", "[codex] AUDIT #999 FAIL\n\nsome other PR entirely")
    b2 = mk_board(CFG2, issues=[closed], pulls=[pr], comments=[unrelated])
    assert rule(b2, CODEX_PM, Verb.REOPEN, closed) == "no AUDIT #9 FAIL posted by codex"


def test_legal_issue_verbs_and_party_of_login():
    i = mk_issue(12, status="in_progress", claimed_by="claude")
    b = mk_board(CFG2, issues=[i], runs={"claude": {12: mk_run("claude", 12, alive=False)}, "codex": {}})
    # plan 5 task 1 adds Verb.REMEMBER to ISSUE_VERBS; remember_rule is legal here too (an in-progress, unchallenged,
    # non-blocked/needs_human issue), so it now closes out the list alongside release/collect
    assert legal_issue_verbs(b, CFG2, CLAUDE_PM, i, REG) == ["release", "collect", "remember"]
    assert party_of_login(CFG2, "meld-codex-bot") == "codex"
    assert party_of_login(CFG2, "empowerScott") == "human:empowerScott"
    assert party_of_login(CFG2, "randouser") is None


def test_refusal_carries_verb_target_and_renders():
    i = mk_issue(11)
    b = mk_board(CFG2, issues=[i])
    r = issue_refusal(b, CFG2, CLAUDE_ORCH, Verb.CLAIM, i, REG)
    assert isinstance(r, Refusal)
    assert r.verb is Verb.CLAIM and r.target == "#11" and r.rule == "only a PM or a human may claim"
    assert str(r) == "claim #11 refused: only a PM or a human may claim"
    assert issue_refusal(b, CFG2, CLAUDE_PM, Verb.CLAIM, i, REG) is None


def test_reopen_is_legal_when_no_closing_pr_is_recorded():
    closed = mk_issue(9, state="closed")
    audit = mk_comment(CFG2, 7, "meld-codex-bot", "[codex] AUDIT #9 FAIL\n\nunmet criterion")
    b = mk_board(CFG2, issues=[closed], comments=[audit])
    assert rule(b, CODEX_PM, Verb.REOPEN, closed) is None


def test_post_is_refusable_but_is_not_a_board_verb():
    # POST exists so a post refusal can name a verb, but it belongs to no issue and no PR,
    # so it must never surface in a legal-action list.
    assert Verb.POST.value == "post"
    assert Verb.POST not in ISSUE_VERBS and Verb.POST not in PR_VERBS
    issue = mk_issue(11)
    board = mk_board(CFG2, issues=[issue])
    assert "post" not in legal_issue_verbs(board, CFG2, CLAUDE_PM, issue, REG)


# ---- reap and tag --------------------------------------------------------------

def test_reap_legality_for_build_runs():
    rec = mk_run("claude", 11, alive=False, collected=True)
    open_issue = mk_issue(11, status="in_progress")
    assert rule(mk_board(CFG2, issues=[open_issue], runs={"claude": {11: rec}}), CLAUDE_PM, Verb.REAP, open_issue) == \
        "issue is still open and its PR is not merged"
    merged = mk_pr(21, 11, committer="meld-claude-bot", state="closed", merged=True)
    assert rule(mk_board(CFG2, issues=[open_issue], pulls=[merged], runs={"claude": {11: rec}}), CLAUDE_PM, Verb.REAP, open_issue) is None
    closed = mk_issue(11, state="closed")
    b = mk_board(CFG2, issues=[closed], runs={"claude": {11: rec}})
    assert rule(b, CLAUDE_PM, Verb.REAP, closed) is None
    assert rule(mk_board(CFG2, issues=[closed]), CLAUDE_PM, Verb.REAP, closed) == "no run record for #11 in claude"
    uncollected = mk_board(CFG2, issues=[closed], runs={"claude": {11: mk_run("claude", 11, alive=False, collected=False)}})
    assert rule(uncollected, CLAUDE_PM, Verb.REAP, closed) == "run has not been collected; collect first"
    assert rule(b, CLAUDE_ORCH, Verb.REAP, closed) == "only the PM may reap"
    assert "reap" in legal_issue_verbs(b, CFG2, CLAUDE_PM, closed, REG)


def test_tag_legality():
    i = mk_issue(11)
    b = mk_board(CFG2, issues=[i])
    assert rule(b, CLAUDE_ORCH, Verb.TAG, i) is None and rule(b, HUMAN, Verb.TAG, i) is None
    assert rule(b, CLAUDE_PM, Verb.TAG, i) == "only the orchestrator or a human may tag"
    closed = mk_issue(12, state="closed")
    assert rule(mk_board(CFG2, issues=[closed]), CLAUDE_ORCH, Verb.TAG, closed) == "issue is closed"
    assert "tag" in legal_issue_verbs(b, CFG2, CLAUDE_ORCH, i, REG) and "tag" not in legal_issue_verbs(b, CFG2, CLAUDE_PM, i, REG)


def test_claim_and_dispatch_follow_the_claim_label():
    held_by_claude = mk_issue(12, status="in_progress", claimed_by="claude")
    board = mk_board(CFG2, issues=[held_by_claude])
    assert rule(board, CODEX_PM, Verb.DISPATCH, held_by_claude) == "issue is claimed by claude, not codex"
    free = mk_issue(11, status="ready", model="gpt-test")  # codex-known model: the model check is not what's under test
    board = mk_board(CFG2, issues=[free])
    assert rule(board, CODEX_PM, Verb.CLAIM, free) is None
    contested = mk_issue(13, status="in_progress", extra_labels=("meld:claimed-by:claude", "meld:claimed-by:codex"))
    board = mk_board(CFG2, issues=[contested])
    assert rule(board, CODEX_PM, Verb.DISPATCH, contested) == "issue is claimed by claude, codex, not codex"
    assert rule(board, CODEX_PM, Verb.RELEASE, contested) is None          # either claimant may release
    ready_claimed = mk_issue(14, status="ready", claimed_by="claude")
    board = mk_board(CFG2, issues=[ready_claimed])
    assert rule(board, CODEX_PM, Verb.CLAIM, ready_claimed) == "already claimed by claude"
    unclaimed = mk_issue(15, status="in_progress")            # a release that died after shedding the label
    board = mk_board(CFG2, issues=[unclaimed])
    assert rule(board, CLAUDE_PM, Verb.DISPATCH, unclaimed) == "issue is not claimed by claude"
    two_labels = mk_issue(16, status="ready", extra_labels=("meld:claimed-by:claude", "meld:claimed-by:codex"))
    board = mk_board(CFG2, issues=[two_labels])
    assert rule(board, CLAUDE_PM, Verb.CLAIM, two_labels) == "already claimed by claude, codex"


def test_a_pm_may_release_a_ready_stale_claim_once_the_holders_lease_expired():
    """Decision 11 reaches the ordinary lease rule too: a ready issue still carrying claude's label whose surviving lease
    has expired is releasable by another PM, exactly as an in-progress one is; while the lease is live it is not."""
    stale = mk_issue(12, status="ready", claimed_by="claude")
    live = mk_board(CFG2, issues=[stale], leases={12: mk_lease(12, until="2026-09-03T13:00:00Z")}, now="2026-09-03T12:00:00Z")
    assert rule(live, CODEX_PM, Verb.RELEASE, stale) == "issue is not claimed by codex"
    expired = mk_board(CFG2, issues=[stale], leases={12: mk_lease(12, until="2026-09-03T11:00:00Z")}, now="2026-09-03T12:00:00Z")
    assert rule(expired, CODEX_PM, Verb.RELEASE, stale) is None
