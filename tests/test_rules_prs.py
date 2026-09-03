from moot.config import Party
from moot.rules import Actor, Verb, approving_parties, committer_party, legal_pr_verbs, pingpong_exceeded, pr_refusal
from tests.factories import CFG2, CFG3, mk_board, mk_pr

CLAUDE_PM = Actor(Party("pm", "claude"), "moot-claude-bot")
CLAUDE_ORCH = Actor(Party("group", "claude"), "moot-claude-bot")
CODEX_PM = Actor(Party("pm", "codex"), "moot-codex-bot")
GEMINI_PM = Actor(Party("pm", "gemini"), "moot-gemini-bot")
HUMAN = Actor(Party("human"), "empowerScott")


def rule(board, cfg, actor, verb, pr):
    r = pr_refusal(board, cfg, actor, verb, pr)
    return None if r is None else r.rule


def test_committer_and_approving_parties():
    pr = mk_pr(20, 13, committer="moot-codex-bot", approvals=("moot-claude-bot", "moot-codex-bot", "empowerScott", "randouser"))
    assert committer_party(CFG2, pr) == "codex"
    assert approving_parties(CFG2, pr) == {"claude", "human:empowerScott"}


def test_approve_rules():
    pr = mk_pr(20, 13, committer="moot-codex-bot")
    b = mk_board(CFG2, pulls=[pr])
    assert rule(b, CFG2, CLAUDE_PM, Verb.APPROVE, pr) is None
    assert rule(b, CFG2, CLAUDE_ORCH, Verb.APPROVE, pr) is None
    assert rule(b, CFG2, HUMAN, Verb.APPROVE, pr) is None
    assert rule(b, CFG2, CODEX_PM, Verb.APPROVE, pr) == "codex made the last commit on this PR and cannot approve its own head"
    fixed = mk_pr(21, 14, committer="moot-claude-bot", author="moot-codex-bot")  # claude pushed a fix onto codex's PR
    b2 = mk_board(CFG2, pulls=[fixed])
    assert rule(b2, CFG2, CODEX_PM, Verb.APPROVE, fixed) is None
    assert rule(b2, CFG2, CLAUDE_PM, Verb.APPROVE, fixed) == "claude made the last commit on this PR and cannot approve its own head"
    challenged = mk_board(CFG2, pulls=[pr], open_challenges=frozenset({"#20"}))
    assert rule(challenged, CFG2, CLAUDE_PM, Verb.APPROVE, pr) == "PR has an open CHALLENGE"
    closed = mk_pr(22, 15, state="closed", merged=True)
    assert rule(mk_board(CFG2, pulls=[closed]), CFG2, CLAUDE_PM, Verb.APPROVE, closed) == "PR is not open"


def test_request_changes_and_push_fix_need_a_different_committer():
    pr = mk_pr(20, 13, committer="moot-codex-bot")
    b = mk_board(CFG2, pulls=[pr])
    for verb in (Verb.REQUEST_CHANGES, Verb.PUSH_FIX):
        assert rule(b, CFG2, CLAUDE_PM, verb, pr) is None
        assert rule(b, CFG2, HUMAN, verb, pr) is None
        assert rule(b, CFG2, CODEX_PM, verb, pr) == f"codex made the last commit on this PR; only other parties may {verb.value}"


def test_merge_quorum_two_groups():
    unreviewed = mk_pr(20, 13, committer="moot-codex-bot")
    assert rule(mk_board(CFG2, pulls=[unreviewed]), CFG2, CLAUDE_PM, Verb.MERGE, unreviewed) == \
        "0/1 approvals from non-committer parties (have: [])"
    self_approved = mk_pr(20, 13, committer="moot-codex-bot", approvals=("moot-codex-bot",))
    assert "0/1 approvals" in rule(mk_board(CFG2, pulls=[self_approved]), CFG2, CLAUDE_PM, Verb.MERGE, self_approved)
    approved = mk_pr(20, 13, committer="moot-codex-bot", approvals=("moot-claude-bot",))
    b = mk_board(CFG2, pulls=[approved])
    assert rule(b, CFG2, CLAUDE_PM, Verb.MERGE, approved) is None
    assert rule(b, CFG2, CODEX_PM, Verb.MERGE, approved) is None  # either PM may merge
    assert rule(b, CFG2, HUMAN, Verb.MERGE, approved) is None
    assert rule(b, CFG2, CLAUDE_ORCH, Verb.MERGE, approved) == "only a PM or a human may merge"
    human_approved = mk_pr(21, 14, committer="moot-codex-bot", approvals=("empowerScott",))
    assert rule(mk_board(CFG2, pulls=[human_approved]), CFG2, CODEX_PM, Verb.MERGE, human_approved) is None


def test_merge_quorum_three_groups():
    one = mk_pr(20, 13, committer="moot-codex-bot", approvals=("moot-claude-bot",))
    assert rule(mk_board(CFG3, pulls=[one]), CFG3, CLAUDE_PM, Verb.MERGE, one) == \
        "1/2 approvals from non-committer parties (have: ['claude'])"
    two = mk_pr(20, 13, committer="moot-codex-bot", approvals=("moot-claude-bot", "moot-gemini-bot"))
    assert rule(mk_board(CFG3, pulls=[two]), CFG3, GEMINI_PM, Verb.MERGE, two) is None
    human_plus_one = mk_pr(21, 14, committer="moot-codex-bot", approvals=("moot-claude-bot", "empowerScott"))
    assert rule(mk_board(CFG3, pulls=[human_plus_one]), CFG3, CLAUDE_PM, Verb.MERGE, human_plus_one) is None


def test_merge_blockers_and_stale_approvals():
    approved = mk_pr(20, 13, committer="moot-codex-bot", approvals=("moot-claude-bot",))
    assert rule(mk_board(CFG2, pulls=[approved], halted=True), CFG2, CLAUDE_PM, Verb.MERGE, approved) == "HALT is in effect"
    assert rule(mk_board(CFG2, pulls=[approved], open_challenges=frozenset({"#20"})), CFG2, CLAUDE_PM, Verb.MERGE, approved) == "PR has an open CHALLENGE"
    red = mk_pr(20, 13, committer="moot-codex-bot", approvals=("moot-claude-bot",), checks="red")
    assert rule(mk_board(CFG2, pulls=[red]), CFG2, CLAUDE_PM, Verb.MERGE, red) == "required checks are red, not green"
    pending = mk_pr(20, 13, committer="moot-codex-bot", approvals=("moot-claude-bot",), checks="pending")
    assert rule(mk_board(CFG2, pulls=[pending]), CFG2, CLAUDE_PM, Verb.MERGE, pending) == "required checks are pending, not green"
    stale = mk_pr(20, 13, committer="moot-codex-bot", stale_approvals=("moot-claude-bot",))
    assert "0/1 approvals" in rule(mk_board(CFG2, pulls=[stale]), CFG2, CLAUDE_PM, Verb.MERGE, stale)


def test_pingpong_and_legal_pr_verbs():
    pr = mk_pr(20, 13, committer="moot-codex-bot", approvals=("moot-claude-bot",), extra_changes_requested_history=3)
    assert pingpong_exceeded(CFG2, pr)
    assert not pingpong_exceeded(CFG2, mk_pr(21, 14, extra_changes_requested_history=2))
    b = mk_board(CFG2, pulls=[pr])
    assert legal_pr_verbs(b, CFG2, CLAUDE_PM, pr) == ["approve", "request-changes", "push-fix", "merge"]
    assert legal_pr_verbs(b, CFG2, CODEX_PM, pr) == ["merge"]
