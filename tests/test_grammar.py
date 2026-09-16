from meld.config import Config, Party
from meld.grammar import (
    Header,
    compose,
    has_verified_by,
    is_authorized,
    parse_comment,
    parse_header,
    validate,
)

CFG = Config.from_dict({
    "repo": {"name": "acme/widgets"},
    "humans": {"accounts": ["empowerScott"]},
    "groups": {
        "claude": {"cli": "claude", "bot": "meld-claude-bot", "token_env": "T1",
                   "orchestrator": {"model": "m", "effort": "high"}, "pm": {"model": "m", "effort": "medium"}},
        "codex": {"cli": "codex", "bot": "meld-codex-bot", "token_env": "T2",
                  "orchestrator": {"model": "m", "effort": "high"}, "pm": {"model": "m", "effort": "medium"}},
    },
})


def test_parse_header_variants():
    assert parse_header("[claude] ROSTER", CFG) == Header(Party("group", "claude"), "ROSTER", None, None)
    assert parse_header("[codex:pm] REVIEW-REQUEST #42", CFG) == Header(Party("pm", "codex"), "REVIEW-REQUEST", "#42", None)
    assert parse_header("[human] ASK ask-7", CFG) == Header(Party("human"), "ASK", "ask-7", None)
    assert parse_header("[joint] ANSWER ask-7 DISAGREE", CFG) == Header(Party("joint"), "ANSWER", "ask-7", "DISAGREE")
    assert parse_header("  [claude] DRIFT  ", CFG) == Header(Party("group", "claude"), "DRIFT", None, None)


def test_the_supervisor_may_post_limited_for_either_role():
    # Spec section 7 lists LIMITED beside RESUMED for both parties, but the verb sets had not caught up. Without
    # the verb, `parse_header` returns None for the supervisor's own freeze notice, so the board would read the
    # one comment explaining a silent role as an off-protocol comment from a bot.
    assert parse_header("[claude] LIMITED", CFG) == Header(Party("group", "claude"), "LIMITED", None, None)
    assert parse_header("[codex:pm] LIMITED", CFG) == Header(Party("pm", "codex"), "LIMITED", None, None)
    body = compose(Header(Party("group", "claude"), "LIMITED", None, None),
                   "claude's orchestrator hit its usage limit and is frozen.\nresets: 2026-09-12T18:00:00Z")
    c = parse_comment({"id": 1, "user": {"login": "meld-claude-bot"}, "body": body}, CFG)
    assert c.ok and c.header.verb == "LIMITED" and "resets:" in c.text


def test_a_human_may_post_needs_human():
    # I4: a human's own `meld tag --held` stamps the hold with its own ask, and HANDOFF decision 11's "post a new
    # NEEDS-HUMAN #N by hand" needs the verb to exist for the human party at all -- `parse_header` returns None for
    # a verb outside the party's set, so before this the comment was unparseable and stamped nothing.
    assert parse_header("[human] NEEDS-HUMAN #11", CFG) == Header(Party("human"), "NEEDS-HUMAN", "#11", None)
    assert validate(Header(Party("human"), "NEEDS-HUMAN", None, None), "x") == ["NEEDS-HUMAN requires a ref"]
    c = parse_comment({"id": 1, "user": {"login": "empowerScott"}, "body": "[human] NEEDS-HUMAN #11\n\nheld: delete"}, CFG)
    assert c.ok and c.header.verb == "NEEDS-HUMAN"
    stranger = parse_comment({"id": 2, "user": {"login": "passerby"}, "body": "[human] NEEDS-HUMAN #11\n\nheld: delete"}, CFG)
    assert not stranger.ok


def test_parse_header_rejects_garbage():
    assert parse_header("hello there", CFG) is None
    assert parse_header("[mystery] ROSTER", CFG) is None
    assert parse_header("[claude] roster", CFG) is None  # verbs are upper case
    assert parse_header("[claude] MADE-UP-VERB", CFG) is None
    assert parse_header("[claude] REVIEW-REQUEST #1", CFG) is None  # PM-only verb


def test_validate_required_fields():
    assert validate(Header(Party("group", "claude"), "CHALLENGE", None, None), "x") == ["CHALLENGE requires a ref"]
    assert validate(Header(Party("joint"), "PLAN", None, None), "no evidence") == ["PLAN requires a verified-by: line"]
    assert validate(Header(Party("joint"), "PLAN", None, None), "text\n\nverified-by: pytest -q (3 passed)") == []
    assert validate(Header(Party("group", "claude"), "EVIDENCE", "#4", None), "x") == ["EVIDENCE requires a verified-by: line"]


def test_has_verified_by_is_case_insensitive_and_needs_content():
    assert has_verified_by("a\nVerified-By: ran make test\n")
    assert not has_verified_by("verified-by:\n")
    assert not has_verified_by("nothing")


def test_authorization():
    assert is_authorized("meld-claude-bot", Header(Party("group", "claude"), "ROSTER", None, None), CFG)
    assert is_authorized("meld-claude-bot", Header(Party("pm", "claude"), "STATUS", None, None), CFG)
    assert not is_authorized("meld-codex-bot", Header(Party("group", "claude"), "ROSTER", None, None), CFG)
    assert is_authorized("empowerScott", Header(Party("human"), "HALT", None, None), CFG)
    assert not is_authorized("randouser", Header(Party("human"), "HALT", None, None), CFG)
    assert is_authorized("meld-codex-bot", Header(Party("joint"), "PLAN", None, None), CFG)
    assert not is_authorized("empowerScott", Header(Party("joint"), "PLAN", None, None), CFG)


def test_parse_comment_end_to_end():
    raw = {"id": 5, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T10:00:00Z",
           "body": "[claude] PROPOSE plan-1\n\nSplit into 4 issues."}
    c = parse_comment(raw, CFG)
    assert c.ok
    assert c.header.verb == "PROPOSE" and c.header.ref == "plan-1"
    assert c.text == "Split into 4 issues."

    imposter = parse_comment({**raw, "user": {"login": "randouser"}}, CFG)
    assert imposter.header is not None and not imposter.authorized and not imposter.ok

    plain = parse_comment({**raw, "body": "just chatting"}, CFG)
    assert plain.header is None and not plain.ok and plain.text == "just chatting"


def test_compose_round_trips():
    h = Header(Party("pm", "codex"), "REVIEW-REQUEST", "#42", None)
    body = compose(h, "Please review.\n\nverified-by: pytest")
    assert body.startswith("[codex:pm] REVIEW-REQUEST #42\n\n")
    assert parse_header(body.splitlines()[0], CFG) == h


def test_pm_lease_and_expired_verbs():
    assert parse_header("[codex:pm] LEASE #12", CFG) == Header(Party("pm", "codex"), "LEASE", "#12", None)
    assert parse_header("[codex:pm] EXPIRED #12", CFG) == Header(Party("pm", "codex"), "EXPIRED", "#12", None)
    assert parse_header("[codex] LEASE #12", CFG) is None
    assert validate(Header(Party("pm", "codex"), "LEASE", None, None), "until: x") == ["LEASE requires a ref"]
