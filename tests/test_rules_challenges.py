from dataclasses import replace

from meld.board import Challenge
from meld.config import Party
from meld.rules import Actor, challenge_rule, challenged_party
from tests.factories import CFG2, CFG3, mk_board, mk_comment, mk_issue, mk_pr

ROSTER = frozenset({"backend", "reviewer", "auditor"})
CLAUDE_ORCH = Actor(Party("group", "claude"), "meld-claude-bot", ROSTER)
CODEX_ORCH = Actor(Party("group", "codex"), "meld-codex-bot", ROSTER)
GEMINI_ORCH = Actor(Party("group", "gemini"), "meld-gemini-bot", ROSTER)
CLAUDE_PM = Actor(Party("pm", "claude"), "meld-claude-bot", ROSTER)
HUMAN = Actor(Party("human"), "empowerScott")
OPEN = Challenge("#20", "claude", 500, "2026-09-03T10:00:00Z")


def with_challenge(board, ch=OPEN):
    return replace(board, challenges={ch.ref: ch}, open_challenges=frozenset({ch.ref}))


def test_challenged_party_resolution():
    pr = mk_pr(20, 13, committer="meld-codex-bot", author="meld-claude-bot")
    issue = mk_issue(12, status="in_progress", claimed_by="claude")
    proposal = mk_comment(CFG2, 1, "meld-codex-bot", "[codex] PROPOSE plan-1\n\nsplit the work")
    b = mk_board(CFG2, issues=[issue], pulls=[pr], comments=[proposal])
    assert challenged_party(b, CFG2, "#20") == "codex"       # the head committer's party answers for a PR
    assert challenged_party(b, CFG2, "#12") == "claude"      # the claim holder's group answers for an issue
    assert challenged_party(b, CFG2, "plan-1") == "codex"    # the proposer answers for an agreement ref
    assert challenged_party(b, CFG2, "ask-9") is None
    unknown = mk_pr(21, 14, committer=None, author="meld-claude-bot")
    assert challenged_party(mk_board(CFG2, pulls=[unknown]), CFG2, "#21") == "claude"   # falls back to the PR author


def test_challenge_open_rules():
    pr = mk_pr(20, 13, committer="meld-codex-bot")
    b = mk_board(CFG2, pulls=[pr])
    assert challenge_rule(b, CFG2, CLAUDE_ORCH, "open", "#20") is None
    assert challenge_rule(b, CFG2, HUMAN, "open", "#20") is None
    assert challenge_rule(b, CFG2, CODEX_ORCH, "open", "#20") == "a party cannot challenge its own claim"
    assert challenge_rule(b, CFG2, CLAUDE_PM, "open", "#20") == "only an orchestrator or a human may act on challenges"
    assert challenge_rule(with_challenge(b), CFG2, HUMAN, "open", "#20") == "#20 already has an open CHALLENGE by claude"
    assert challenge_rule(b, CFG2, HUMAN, "bogus", "#20") == "unknown challenge action 'bogus'"


def test_challenge_closers_are_restricted():
    pr = mk_pr(20, 13, committer="meld-codex-bot")
    b = with_challenge(mk_board(CFG2, pulls=[pr]))
    assert challenge_rule(b, CFG2, CODEX_ORCH, "evidence", "#20") is None                     # the challenged party answers
    assert challenge_rule(b, CFG2, CLAUDE_ORCH, "evidence", "#20") == "only the challenged party (codex) or a human may post EVIDENCE"
    assert challenge_rule(b, CFG2, CLAUDE_ORCH, "retract", "#20") is None                     # the challenger withdraws
    assert challenge_rule(b, CFG2, CODEX_ORCH, "retract", "#20") is None                      # or the challenged party concedes
    assert challenge_rule(b, CFG2, CLAUDE_ORCH, "uphold", "#20") == "the challenger cannot decide its own challenge"
    assert challenge_rule(b, CFG2, CODEX_ORCH, "dismiss", "#20") == "the challenged party cannot decide the challenge against it"
    assert challenge_rule(b, CFG2, HUMAN, "uphold", "#20") is None                            # with two groups the human adjudicates
    b3 = with_challenge(mk_board(CFG3, pulls=[pr]))
    assert challenge_rule(b3, CFG3, GEMINI_ORCH, "dismiss", "#20") is None                    # a third group may decide
    assert challenge_rule(b3, CFG3, GEMINI_ORCH, "retract", "#20") == "only the challenger (claude) or the challenged party (codex) or a human may RETRACT"
    assert challenge_rule(b, CFG2, HUMAN, "uphold", "#21") == "#21 has no open CHALLENGE"


def test_challenged_party_of_an_issue_is_its_claim_holder():
    board = mk_board(CFG2, issues=[mk_issue(12, status="in_progress", claimed_by="codex"),
                                    mk_issue(13, status="in_progress", claimed_by="human"),
                                    mk_issue(14, status="in_progress", extra_labels=("meld:claimed-by:claude", "meld:claimed-by:codex"))])
    assert challenged_party(board, CFG2, "#12") == "codex"
    assert challenged_party(board, CFG2, "#13") is None      # a human holder is not a group
    assert challenged_party(board, CFG2, "#14") is None      # contested: nobody owns it
