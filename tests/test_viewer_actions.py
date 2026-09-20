import json

import pytest

from meld.viewer import actions as va
from tests.test_viewer import TOKEN, body, get, viewer_for


def comments(fake):
    """Every coordination comment posted through this fake, newest last — the spelling test_actions.py uses."""
    return [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint.endswith("/comments")]


def act(v, payload, **kw):
    """POST /api/action with `payload` as the JSON body."""
    from meld.viewer.server import Request
    target = f"/api/action?t={kw.pop('token', TOKEN)}"
    headers = {"Host": kw.pop("host", "127.0.0.1:8765"), "Content-Type": "application/json"}
    return v.handle(Request.parse("POST", target, headers, json.dumps(payload).encode()))


def test_the_dispatch_table_is_exactly_the_ten_verbs_section_22_names():
    assert sorted(va.VERBS) == ["challenge", "claim", "decide", "merge", "plan-ack", "plan-approve",
                                "post", "release", "remember", "tag"]


def test_an_action_runs_the_same_function_the_cli_runs(tmp_path):
    v, ctx, fake = viewer_for(tmp_path)
    resp = act(v, {"party": "human", "verb": "post", "action": "ASK", "ref": "ask-1", "text": "who owns #13?"})
    assert resp.status == 200
    out = body(resp)
    assert out["ok"] is True and out["verb"] == "post" and out["result"]["header"].startswith("[human] ASK ask-1")
    posted = [c for c in comments(fake) if "ASK ask-1" in c]
    assert len(posted) == 1 and "who owns #13?" in posted[0]


def test_a_refusal_comes_back_whole_with_the_rule_that_named_it(tmp_path):
    v, ctx, _ = viewer_for(tmp_path)
    # board1: #13 is meld:in-review (also claimed by codex, but the status check refuses before claimant does).
    resp = act(v, {"party": "human", "verb": "claim", "number": 13})
    assert resp.status == 409
    out = body(resp)
    assert out["verb"] == "claim" and out["target"] == "#13" and "in_review" in out["rule"]
    assert out["rule"] in out["refused"] and out["violation_posted"] is None   # a human's refusal posts nothing


def test_the_page_may_act_as_no_other_party(tmp_path):
    v, _, fake = viewer_for(tmp_path)
    before = len(comments(fake))
    for party in ("claude", "joint", None, ""):
        resp = act(v, {"party": party, "verb": "post", "ref": "ask-1", "text": "x"})
        assert resp.status == 400 and "human" in body(resp)["error"]
    assert len(comments(fake)) == before


def test_the_viewer_refuses_to_act_as_a_group_even_when_the_body_says_human(tmp_path):
    """Fix round 1: the body's "party": "human" is the page's own claim; the process actually running as
    `MELD_GROUP=claude` must be checked too, or a page served from a group's environment could post -- and be
    refused -- as that group, PROTOCOL-VIOLATION included. Reproduces the reviewer's probe against `plan-8c`."""
    v, _, fake = viewer_for(tmp_path, group="claude", role="orchestrator")
    before = len(comments(fake))
    resp = act(v, {"party": "human", "verb": "post", "action": "PROPOSE", "ref": "ask-1", "text": "who owns #13?"})
    assert resp.status == 400 and "claude" in body(resp)["error"]
    resp = act(v, {"party": "human", "verb": "claim", "number": 13})
    assert resp.status == 400 and "claude" in body(resp)["error"]
    assert len(comments(fake)) == before   # neither call reached the board -- no PROPOSE, no PROTOCOL-VIOLATION


@pytest.mark.parametrize("payload, wants", [
    ({"party": "human", "verb": "nope"}, "unknown verb"),
    ({"party": "human", "verb": "claim"}, "integer 'number'"),
    ({"party": "human", "verb": "claim", "number": "13"}, "integer 'number'"),
    ({"party": "human", "verb": "claim", "number": True}, "integer 'number'"),
    ({"party": "human", "verb": "post"}, "non-empty 'action'"),
    ({"party": "human", "verb": "remember", "ref": "#13"}, "non-empty 'text'"),
    ({"party": "human", "verb": "plan-ack"}, "non-empty 'ref'"),
    ({"party": "human", "verb": "tag", "number": 11, "skills": "qa"}, "list of strings"),
    ({"party": "human", "verb": "release", "number": 12, "force": "no"}, "boolean 'force'"),
    # Not the coercion's own 400 but `PostError`'s: `posting.post` raises at its `allowed_verbs` check, because
    # PROPOSE is an agreement verb a human may not post. A usage error, as its docstring says -- the call is
    # malformed, not a legal call the rules refuse -- so it answers 400 like every row above it, and 409 stays
    # the one status that carries a `Refused` (spec 22).
    ({"party": "human", "verb": "post", "action": "PROPOSE", "ref": "ask-1", "text": "x"}, "not a verb"),
])
def test_an_argument_the_verb_cannot_use_never_reaches_it(tmp_path, payload, wants):
    v, _, fake = viewer_for(tmp_path)
    before = len(comments(fake))
    resp = act(v, payload)
    assert resp.status == 400 and wants in body(resp)["error"]
    assert len(comments(fake)) == before


def test_a_malformed_body_is_a_400_not_a_traceback(tmp_path):
    from meld.viewer.server import Request
    v, _, _ = viewer_for(tmp_path)
    headers = {"Host": "127.0.0.1:8765"}
    for raw in (b"", b"not json", b"[1, 2]", b'"a string"'):
        resp = v.handle(Request.parse("POST", f"/api/action?t={TOKEN}", headers, raw))
        assert resp.status == 400 and "error" in body(resp)


def test_a_github_failure_is_502_and_an_unexpected_error_is_500(tmp_path, monkeypatch):
    from meld.github import GitHubError
    v, _, _ = viewer_for(tmp_path)
    monkeypatch.setitem(va.VERBS, "claim", lambda ctx, b: (_ for _ in ()).throw(GitHubError("gh: 500")))
    assert act(v, {"party": "human", "verb": "claim", "number": 11}).status == 502
    monkeypatch.setitem(va.VERBS, "claim", lambda ctx, b: (_ for _ in ()).throw(RuntimeError("boom")))
    resp = act(v, {"party": "human", "verb": "claim", "number": 11})
    assert resp.status == 500 and "RuntimeError: boom" in body(resp)["error"]


def test_a_successful_action_drops_the_cached_state(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    assert get(v, "/api/state").status == 200 and v._state_cache is not None
    act(v, {"party": "human", "verb": "post", "action": "ASK", "ref": "ask-1", "text": "hello"})
    assert v._state_cache is None
    assert get(v, "/api/state").status == 200 and v._state_cache is not None
    act(v, {"party": "human", "verb": "claim", "number": 13})        # refused, so the board did not move
    assert v._state_cache is not None


def test_the_gate_comes_before_the_action(tmp_path):
    v, _, fake = viewer_for(tmp_path)
    before = len(comments(fake))
    assert act(v, {"party": "human", "verb": "post", "ref": "ask-1", "text": "x"}, token="wrong").status == 403
    assert act(v, {"party": "human", "verb": "post", "ref": "ask-1", "text": "x"}, host="evil.example:8765").status == 403
    assert len(comments(fake)) == before


@pytest.mark.parametrize("payload", [
    {"verb": "claim", "number": 11},
    {"verb": "release", "number": 12},
    {"verb": "release", "number": 12, "force": True},
    {"verb": "post", "action": "ASK", "ref": "ask-9", "text": "anyone?"},
    {"verb": "tag", "number": 11, "worker": "backend", "model": "claude-sonnet-5", "effort": "high",
     "size": "M", "skills": ["qa"], "scope": ["src/*.py"], "may_delete": ["old.py"], "held": ["delete"]},
    {"verb": "challenge", "action": "open", "ref": "#13", "text": "the evidence does not show it"},
    {"verb": "decide", "ref": "vehicle-1"},
    {"verb": "remember", "ref": "#13", "text": "what went wrong"},
    {"verb": "plan-approve"},
    {"verb": "plan-ack", "ref": "plan-add-1"},
    {"verb": "merge", "number": 14},
])
def test_every_verb_binds_the_arguments_its_own_function_takes(tmp_path, payload):
    """Not about what the board allows -- about the call itself. A misspelled keyword or a swapped positional in
    the dispatch table is a TypeError the coercion cannot see, and it would reach a person as a 500 with the
    suite green. So: a valid body must never come back 400 (the coercion rejected it) or 500 (the call did)."""
    v, _, _ = viewer_for(tmp_path)
    resp = act(v, {"party": "human", **payload})
    assert resp.status in (200, 409, 502), (payload, body(resp))
