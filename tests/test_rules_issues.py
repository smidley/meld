from moot.config import Party
from moot.rules import Actor, Refusal, Verb, issue_refusal, legal_issue_verbs, party_of_login
from tests.factories import CFG2, REG, mk_board, mk_comment, mk_issue, mk_pr, mk_run

ROSTER = frozenset({"backend", "frontend", "security", "qa", "reviewer", "auditor", "docs"})
CLAUDE_PM = Actor(Party("pm", "claude"), "moot-claude-bot", ROSTER)
CLAUDE_ORCH = Actor(Party("group", "claude"), "moot-claude-bot", ROSTER)
CODEX_PM = Actor(Party("pm", "codex"), "moot-codex-bot", ROSTER)
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
    taken = mk_issue(13, assignees=("moot-codex-bot",))
    assert rule(mk_board(CFG2, issues=[taken]), CLAUDE_PM, Verb.CLAIM, taken) == "already claimed by moot-codex-bot"
    no_worker = mk_issue(14, worker=None)
    assert rule(mk_board(CFG2, issues=[no_worker]), CLAUDE_PM, Verb.CLAIM, no_worker) == "issue has no worker: label"
    marketing = mk_issue(15, worker="marketing")
    assert "not in claude's roster" in rule(mk_board(CFG2, issues=[marketing]), CLAUDE_PM, Verb.CLAIM, marketing)


def test_claim_respects_concurrency_cap():
    busy = [mk_issue(n, status="in_progress", assignees=("moot-claude-bot",)) for n in range(100, 108)]
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

def test_dispatch_legal_for_assignee_pm():
    i = mk_issue(12, status="in_progress", assignees=("moot-claude-bot",), size="M")
    assert rule(mk_board(CFG2, issues=[i]), CLAUDE_PM, Verb.DISPATCH, i) is None


def test_dispatch_refusals():
    i = mk_issue(12, status="in_progress", assignees=("moot-claude-bot",))
    assert rule(mk_board(CFG2, issues=[i], halted=True), CLAUDE_PM, Verb.DISPATCH, i) == "HALT is in effect"
    assert rule(mk_board(CFG2, issues=[i]), CLAUDE_ORCH, Verb.DISPATCH, i) == "only the PM may dispatch"
    assert rule(mk_board(CFG2, issues=[i]), HUMAN, Verb.DISPATCH, i) == "only the PM may dispatch"
    assert rule(mk_board(CFG2, issues=[i]), CODEX_PM, Verb.DISPATCH, i) == "issue is not assigned to moot-codex-bot"
    ready = mk_issue(13)
    assert rule(mk_board(CFG2, issues=[ready]), CLAUDE_PM, Verb.DISPATCH, ready) == "issue is ready, not in-progress"
    running = mk_board(CFG2, issues=[i], runs={"claude": {12: mk_run("claude", 12)}, "codex": {}})
    assert "already exists" in rule(running, CLAUDE_PM, Verb.DISPATCH, i)
    uncollected = mk_board(CFG2, issues=[i], runs={"claude": {12: mk_run("claude", 12, alive=False)}, "codex": {}})
    assert "already exists" in rule(uncollected, CLAUDE_PM, Verb.DISPATCH, i)
    collected = mk_board(CFG2, issues=[i], runs={"claude": {12: mk_run("claude", 12, alive=False, collected=True)}, "codex": {}})
    assert rule(collected, CLAUDE_PM, Verb.DISPATCH, i) is None
    assert rule(mk_board(CFG2, issues=[i], open_challenges=frozenset({"#12"})), CLAUDE_PM, Verb.DISPATCH, i) == "issue has an open CHALLENGE"
    labelled = mk_issue(12, status="in_progress", assignees=("moot-claude-bot",), extra_labels=("moot:challenged",))
    assert rule(mk_board(CFG2, issues=[labelled]), CLAUDE_PM, Verb.DISPATCH, labelled) == "issue has an open CHALLENGE"


def test_dispatch_validates_model_effort_size():
    missing = mk_issue(12, status="in_progress", assignees=("moot-claude-bot",), effort=None, size=None)
    assert rule(mk_board(CFG2, issues=[missing]), CLAUDE_PM, Verb.DISPATCH, missing) == "issue is missing labels: effort, size"
    bad_effort = mk_issue(12, status="in_progress", assignees=("moot-claude-bot",), effort="turbo")
    assert "effort 'turbo' not valid" in rule(mk_board(CFG2, issues=[bad_effort]), CLAUDE_PM, Verb.DISPATCH, bad_effort)
    bad_size = mk_issue(12, status="in_progress", assignees=("moot-claude-bot",), size="XL")
    assert rule(mk_board(CFG2, issues=[bad_size]), CLAUDE_PM, Verb.DISPATCH, bad_size) == "size 'XL' is not one of S, M, L"
    too_big = mk_issue(12, status="in_progress", assignees=("moot-claude-bot",), model="claude-haiku-4-5-20251001", size="L")
    assert rule(mk_board(CFG2, issues=[too_big]), CLAUDE_PM, Verb.DISPATCH, too_big) == \
        "size L does not fit model claude-haiku-4-5-20251001 (context class medium)"


# ---- collect -----------------------------------------------------------------

def test_collect_rules():
    i = mk_issue(12, status="in_progress", assignees=("moot-claude-bot",))
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
    mine = mk_issue(12, status="in_progress", assignees=("moot-claude-bot",))
    b = mk_board(CFG2, issues=[mine])
    assert rule(b, CLAUDE_PM, Verb.RELEASE, mine) is None
    assert rule(b, CLAUDE_ORCH, Verb.RELEASE, mine) is None
    assert rule(b, CODEX_PM, Verb.RELEASE, mine) == "issue is not assigned to moot-codex-bot"
    assert rule(b, HUMAN, Verb.RELEASE, mine) is None
    ready = mk_issue(13)
    assert rule(mk_board(CFG2, issues=[ready]), HUMAN, Verb.RELEASE, ready) == \
        "issue is ready; release is only legal from in-progress or blocked"
    blocked = mk_issue(14, status="blocked", assignees=("moot-claude-bot",))
    assert rule(mk_board(CFG2, issues=[blocked]), CLAUDE_PM, Verb.RELEASE, blocked) is None


# ---- reopen ------------------------------------------------------------------

def test_reopen_requires_fail_audit_from_non_author_group():
    closed = mk_issue(9, state="closed")
    pr = mk_pr(19, 9, committer="moot-claude-bot", state="closed", merged=True)
    audit = mk_comment(CFG2, 7, "moot-codex-bot", "[codex] AUDIT #9 FAIL\n\nacceptance criterion 2 unmet\n\nverified-by: pytest -k healthz")
    b = mk_board(CFG2, issues=[closed], pulls=[pr], comments=[audit])
    assert rule(b, CODEX_PM, Verb.REOPEN, closed) is None
    assert rule(b, CLAUDE_PM, Verb.REOPEN, closed) == "no AUDIT #9 FAIL posted by claude"
    self_audit = mk_comment(CFG2, 8, "moot-claude-bot", "[claude] AUDIT #9 FAIL\n\nnope")
    b2 = mk_board(CFG2, issues=[closed], pulls=[pr], comments=[audit, self_audit])
    assert rule(b2, CLAUDE_PM, Verb.REOPEN, closed) == "claude authored the PR that closed #9"
    assert rule(b, HUMAN, Verb.REOPEN, closed) is None
    open_issue = mk_issue(11)
    assert rule(mk_board(CFG2, issues=[open_issue]), HUMAN, Verb.REOPEN, open_issue) == "issue is not closed"


def test_legal_issue_verbs_and_party_of_login():
    i = mk_issue(12, status="in_progress", assignees=("moot-claude-bot",))
    b = mk_board(CFG2, issues=[i], runs={"claude": {12: mk_run("claude", 12, alive=False)}, "codex": {}})
    assert legal_issue_verbs(b, CFG2, CLAUDE_PM, i, REG) == ["release", "collect"]
    assert party_of_login(CFG2, "moot-codex-bot") == "codex"
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
    audit = mk_comment(CFG2, 7, "moot-codex-bot", "[codex] AUDIT #9 FAIL\n\nunmet criterion")
    b = mk_board(CFG2, issues=[closed], comments=[audit])
    assert rule(b, CODEX_PM, Verb.REOPEN, closed) is None
