from meld.config import Config
from meld.grammar import agreement_for, parse_comment
from tests.factories import mk_comment

CFG2 = Config.from_dict({
    "repo": {"name": "acme/widgets"},
    "humans": {"accounts": ["empowerScott"]},
    "agreement": {"amend_cap": 2},
    "groups": {
        "claude": {"cli": "claude", "bot": "meld-claude-bot", "token_env": "T1",
                   "orchestrator": {"model": "m", "effort": "high"}, "pm": {"model": "m", "effort": "medium"}},
        "codex": {"cli": "codex", "bot": "meld-codex-bot", "token_env": "T2",
                  "orchestrator": {"model": "m", "effort": "high"}, "pm": {"model": "m", "effort": "medium"}},
    },
})
CFG3 = Config.from_dict({
    "repo": {"name": "acme/widgets"},
    "humans": {"accounts": ["empowerScott"]},
    "agreement": {"amend_cap": 2},
    "groups": {
        "claude": {"cli": "claude", "bot": "meld-claude-bot", "token_env": "T1",
                   "orchestrator": {"model": "m", "effort": "high"}, "pm": {"model": "m", "effort": "medium"}},
        "codex": {"cli": "codex", "bot": "meld-codex-bot", "token_env": "T2",
                  "orchestrator": {"model": "m", "effort": "high"}, "pm": {"model": "m", "effort": "medium"}},
        "gemini": {"cli": "gemini", "bot": "meld-gemini-bot", "token_env": "T3",
                   "orchestrator": {"model": "m", "effort": "high"}, "pm": {"model": "m", "effort": "medium"}},
    },
})

BOTS = {"claude": "meld-claude-bot", "codex": "meld-codex-bot", "gemini": "meld-gemini-bot"}


def c(cfg, i, group, verb, ref, text="body", joint=False, author=None):
    party = "joint" if joint else group
    login = author or (BOTS[group] if group in BOTS else group)
    return parse_comment({"id": i, "user": {"login": login}, "created_at": f"2026-09-03T10:{i:02d}:00Z",
                          "body": f"[{party}] {verb} {ref}\n\n{text}"}, cfg)


def test_none_when_no_proposal():
    a = agreement_for([], "plan-1", CFG2)
    assert a.status == "none" and a.text is None


def test_propose_then_agree_is_agreed():
    cs = [c(CFG2, 1, "claude", "PROPOSE", "plan-1", "v1"), c(CFG2, 2, "codex", "AGREE", "plan-1")]
    a = agreement_for(cs, "plan-1", CFG2)
    assert a.status == "agreed" and a.text == "v1" and a.proposer == "claude" and a.agreed == {"claude", "codex"}


def test_amend_resets_agreement_and_counts_round():
    cs = [c(CFG2, 1, "claude", "PROPOSE", "plan-1", "v1"), c(CFG2, 2, "codex", "AMEND", "plan-1", "v2")]
    a = agreement_for(cs, "plan-1", CFG2)
    assert a.status == "open" and a.text == "v2" and a.proposer == "codex" and a.agreed == {"codex"} and a.rounds == 1
    assert a.can_amend(CFG2)
    cs.append(c(CFG2, 3, "claude", "AMEND", "plan-1", "v3"))
    a = agreement_for(cs, "plan-1", CFG2)
    assert a.rounds == 2 and not a.can_amend(CFG2) and a.status == "open"


def test_two_groups_split_is_disagree_after_all_vote():
    cs = [c(CFG2, 1, "claude", "PROPOSE", "plan-1", "v1"), c(CFG2, 2, "codex", "DISSENT", "plan-1", "I object")]
    a = agreement_for(cs, "plan-1", CFG2)
    assert a.status == "disagree" and a.dissent == {"codex": "I object"}


def test_three_groups_majority():
    cs = [c(CFG3, 1, "claude", "PROPOSE", "plan-1", "v1"), c(CFG3, 2, "codex", "AGREE", "plan-1"),
          c(CFG3, 3, "gemini", "DISSENT", "plan-1", "no")]
    a = agreement_for(cs, "plan-1", CFG3)
    assert a.status == "majority" and a.agreed == {"claude", "codex"} and a.dissent == {"gemini": "no"}


def test_unauthorized_and_other_ref_comments_are_ignored():
    cs = [c(CFG2, 1, "claude", "PROPOSE", "plan-1", "v1"),
          c(CFG2, 2, "codex", "AGREE", "plan-1", author="randouser"),
          c(CFG2, 3, "codex", "AGREE", "plan-2")]
    a = agreement_for(cs, "plan-1", CFG2)
    assert a.status == "open" and a.agreed == {"claude"}


def test_draft_behaves_like_propose_and_joint_marks_posted():
    cs = [c(CFG2, 1, "codex", "DRAFT", "ask-1", "answer"), c(CFG2, 2, "claude", "AGREE", "ask-1"),
          c(CFG2, 3, "codex", "ANSWER", "ask-1", "answer\n\nverified-by: meld status", joint=True)]
    a = agreement_for(cs, "ask-1", CFG2)
    assert a.status == "agreed" and a.joint_posted


def test_reproposal_counts_as_a_round():
    cs = [c(CFG2, 1, "claude", "PROPOSE", "plan-1", "v1"), c(CFG2, 2, "codex", "PROPOSE", "plan-1", "v2")]
    a = agreement_for(cs, "plan-1", CFG2)
    assert a.rounds == 1 and a.text == "v2" and a.proposer == "codex" and a.agreed == {"codex"}
    cs.append(c(CFG2, 3, "claude", "DRAFT", "plan-1", "v3"))
    a = agreement_for(cs, "plan-1", CFG2)
    assert a.rounds == 2 and not a.can_amend(CFG2)


def test_votes_before_a_proposal_are_ignored():
    cs = [c(CFG2, 1, "claude", "AGREE", "plan-1"), c(CFG2, 2, "codex", "DISSENT", "plan-1", "no")]
    a = agreement_for(cs, "plan-1", CFG2)
    assert a.status == "none" and a.text is None and a.proposer is None
    assert a.agreed == frozenset() and a.dissent == {} and a.rounds == 0


def test_joint_comment_is_verified_only_from_the_proposers_bot_after_the_round_closed():
    thread = [mk_comment(CFG2, 1, "meld-claude-bot", "[claude] PROPOSE plan-1\n\nsplit the work"),
              mk_comment(CFG2, 2, "meld-codex-bot", "[codex] AGREE plan-1\n\nyes")]
    good = thread + [mk_comment(CFG2, 3, "meld-claude-bot", "[joint] PLAN plan-1\n\nsplit the work\n\nverified-by: meld roster")]
    a = agreement_for(good, "plan-1", CFG2)
    assert a.joint_posted and a.joint_verified and a.joint_verb == "PLAN" and a.joint_author == "meld-claude-bot"
    forged = thread + [mk_comment(CFG2, 3, "meld-codex-bot", "[joint] PLAN plan-1\n\nsplit the work\n\nverified-by: x")]
    b = agreement_for(forged, "plan-1", CFG2)
    assert b.joint_posted and not b.joint_verified and b.joint_author == "meld-codex-bot"
    early = [thread[0], mk_comment(CFG2, 2, "meld-claude-bot", "[joint] PLAN plan-1\n\nsplit the work\n\nverified-by: x")]
    c = agreement_for(early, "plan-1", CFG2)
    assert c.joint_posted and not c.joint_verified       # posted while the round was still open
