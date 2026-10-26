from meld.config import Party
from meld.rules import Actor, Verb, decide_rule, legal_agreement_verbs, legal_pr_verbs, plan_rule, remember_rule
from tests.factories import CFG2, mk_board, mk_comment, mk_issue, mk_pr

CLAUDE = Actor(Party("group", "claude"), "meld-claude-bot")
CLAUDE_PM = Actor(Party("pm", "claude"), "meld-claude-bot")
CODEX_PM = Actor(Party("pm", "codex"), "meld-codex-bot")
HUMAN = Actor(Party("human"), "empowerScott")


def decision_thread(joint_author="meld-claude-bot", verb="DECISION", qualifier=None):
    return [
        mk_comment(101, "meld-claude-bot", "[claude] PROPOSE dec-1\n\nUse Postgres.", "2026-09-03T10:00:00Z"),
        mk_comment(102, "meld-codex-bot", "[codex] AGREE dec-1\n\nyes", "2026-09-03T10:01:00Z"),
        mk_comment(103, joint_author, f"[joint] {verb} dec-1{' ' + qualifier if qualifier else ''}\n\nUse Postgres.\n\nverified-by: agreed", "2026-09-03T10:02:00Z"),
    ]


def test_decide_rule_admits_only_the_verified_proposer():
    board = mk_board(comments=decision_thread())
    assert decide_rule(board, CFG2, CLAUDE, "dec-1") is None
    assert decide_rule(board, CFG2, CLAUDE_PM, "dec-1") is None
    assert decide_rule(board, CFG2, CODEX_PM, "dec-1") == "only the proposer (claude) may record dec-1"
    assert decide_rule(board, CFG2, HUMAN, "dec-1") == "only the proposer group's orchestrator or PM may record a decision"
    forged = mk_board(comments=decision_thread(joint_author="meld-codex-bot"))
    assert decide_rule(forged, CFG2, CLAUDE, "dec-1") == "the [joint] DECISION dec-1 comment is not verified (wrong author or posted before the round closed)"
    assert decide_rule(mk_board(comments=[]), CFG2, CLAUDE, "dec-1") == "no verified [joint] DECISION dec-1 on the coordination issue"


def test_decide_rule_refuses_an_open_memory_pr_for_the_ref():
    pr = mk_pr(40, 0, head_sha="mem0001", author="meld-claude-bot")
    pr = pr.__class__(**{**pr.__dict__, "head_ref": "meld/memory-dec-1", "body": "meld-memory: decision dec-1 " + "a" * 64, "issue_number": None})
    board = mk_board(comments=decision_thread(), pulls={40: pr})
    assert decide_rule(board, CFG2, CLAUDE, "dec-1") == "dec-1 is already recorded or has an open memory PR"


def test_legal_agreement_verbs_lists_decide_for_the_proposer_only():
    board = mk_board(comments=decision_thread())
    assert legal_agreement_verbs(board, CFG2, CLAUDE, "dec-1") == ["decide"]
    assert legal_agreement_verbs(board, CFG2, CODEX_PM, "dec-1") == []


def test_plan_rule_matches_plan_py_refusals():
    thread = [
        mk_comment(201, "meld-claude-bot", "[claude] PROPOSE plan-3\n\n- id: a | title: A | worker: backend | model: claude-sonnet-5 | effort: medium | size: S", "2026-09-03T10:00:00Z"),
        mk_comment(202, "meld-codex-bot", "[codex] AGREE plan-3\n\nyes", "2026-09-03T10:01:00Z"),
        mk_comment(203, "meld-claude-bot", "[joint] PLAN plan-3\n\n- id: a | title: A | worker: backend | model: claude-sonnet-5 | effort: medium | size: S\n\nverified-by: agreed", "2026-09-03T10:02:00Z"),
    ]
    planning = mk_board(comments=thread, phase="planning")
    assert plan_rule(planning, CFG2, CLAUDE, "issues", "plan-3") is None
    assert plan_rule(planning, CFG2, CLAUDE_PM, "issues", "plan-3") == "only the proposer group's orchestrator may create plan issues"
    assert plan_rule(planning, CFG2, HUMAN, "approve", "plan-3") is None
    assert plan_rule(planning, CFG2, CLAUDE, "approve", "plan-3") == "only a human may approve the plan"
    executing = mk_board(comments=thread, phase="executing")
    assert plan_rule(executing, CFG2, CLAUDE, "issues", "plan-3") == "plan-3 was already approved; its issues can no longer be created"
    assert plan_rule(executing, CFG2, HUMAN, "approve", "plan-3") == "the plan is already approved (milestone is executing)"
    assert plan_rule(executing, CFG2, HUMAN, "ack", "plan-add-1") == "no verified [joint] PLAN-ADD plan-add-1 on the coordination issue"
    assert legal_agreement_verbs(planning, CFG2, HUMAN, "plan-3") == ["plan-approve"]
    assert legal_agreement_verbs(planning, CFG2, CLAUDE, "plan-3") == ["plan-issues"]


def test_remember_rule_needs_a_resolved_board_item():
    board = mk_board(issues={11: mk_issue(11, "ready"), 14: mk_issue(14, "needs_human")})
    assert remember_rule(board, CFG2, CLAUDE_PM, "#11") is None
    assert remember_rule(board, CFG2, CLAUDE_PM, "#14") == "#14 is still needs_human; a lesson is recorded once the item resolves"
    assert remember_rule(board, CFG2, CLAUDE_PM, "#99") == "#99 is not an issue or PR on the board"
    assert remember_rule(board, CFG2, HUMAN, "#11") == "only an orchestrator or PM may record a lesson"


def test_legal_pr_verbs_reports_the_author_groups_comment_review_path():
    pr = mk_pr(20, 13, committer="meld-codex-bot", author="meld-claude-bot")
    board = mk_board(pulls={20: pr}, issues={13: mk_issue(13, "in_review")})
    legal = legal_pr_verbs(board, CFG2, CLAUDE_PM, pr)
    assert "approve" in legal and "request-changes" in legal and "remember" in legal
    own_head = mk_pr(21, 12, committer="meld-claude-bot", author="meld-claude-bot")
    board2 = mk_board(pulls={21: own_head}, issues={12: mk_issue(12, "in_review")})
    assert "approve" not in legal_pr_verbs(board2, CFG2, CLAUDE_PM, own_head)


def test_decide_rule_refuses_once_the_memory_pr_has_merged():
    """Handoff residual 15: `legal` listed `decide` for a ref whose decision file had already landed, because this rule
    looked only at open memory PRs and the merged one was found only by memory.decide reading origin."""
    pr = mk_pr(40, 0, head_sha="mem0001", author="meld-claude-bot", state="closed", merged=True)
    pr = pr.__class__(**{**pr.__dict__, "head_ref": "meld/memory-dec-1", "body": "meld-memory: decision dec-1 " + "a" * 64, "issue_number": None})
    board = mk_board(comments=decision_thread(), pulls={40: pr})
    assert decide_rule(board, CFG2, CLAUDE, "dec-1") == "dec-1 is already recorded or has an open memory PR"
    assert legal_agreement_verbs(board, CFG2, CLAUDE, "dec-1") == []
