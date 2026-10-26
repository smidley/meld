import http.client
import json
import re
import threading
from types import SimpleNamespace

import pytest

from meld.config import load_config
from meld.context import load_context
from meld.github import GitHubError
from meld.runners import freeze_path, role_run_path, supervisor_path, write_record
from meld.viewer import server
from meld.viewer.server import (LOOPBACK_HOSTS, Request, Response, Viewer, config_version, new_token, serve,
                                url)
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.fakeadapter import FakeAdapter
from tests.fakegh import FakeGh

NOW = "2026-09-18T12:00:00Z"
TOKEN = "t0k3n-fixed-for-tests"


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def viewer_for(tmp_path, *, group: str | None = None, role: str = "pm", **overrides):
    """The human's context over the recorded board (PROJECT_TOML configures groups `claude` and `codex`), and a
    Viewer with every seam injected. `group=`/`role=` build a group's or PM's own context instead of the
    human's -- used only by the test that pins the viewer's refusal to act as anything else."""
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    # load_board1_routes wires only the GETs it takes to assemble the board; a human's own coordination comment
    # (plan 8c's /api/action, the first write the viewer suite makes) needs its own POST route, the same one
    # test_posting.py's and test_actions.py's fixture-builders register for the same reason.
    fake.route("POST", "repos/acme/widgets/issues/10/comments",
               {"id": 555, "html_url": "https://github.com/acme/widgets/issues/10#issuecomment-555"})
    ctx = load_context(tmp_path / ".meld.toml", env=env_for(tmp_path, group=group, role=role), run=fake,
                       adapter_for=lambda name: FakeAdapter())
    kw = dict(token=TOKEN, port=8765, config_path=tmp_path / ".meld.toml", now=lambda: NOW, clock=Clock(),
              sleep=lambda s: None, alive=lambda pid: True,
              start_of=lambda pid: "T0", disk_usage=lambda p: SimpleNamespace(total=0, used=0, free=3 * 1024 ** 3),
              check_every=1, max_polls=1)
    kw.update(overrides)
    return Viewer(ctx, **kw), ctx, fake


def get(v: Viewer, path: str, *, token: str | None = TOKEN, host: str = "127.0.0.1:8765", method: str = "GET",
        headers: dict | None = None) -> Response:
    target = path + (("&" if "?" in path else "?") + f"t={token}" if token is not None else "")
    return v.handle(Request.parse(method, target, {"Host": host, **(headers or {})}))


def body(resp: Response) -> dict:
    assert not resp.streaming
    return json.loads(resp.body)


def put(v, payload, **kw):
    """`PUT /api/config`. A payload that carries a `config` but no `version` key gets the file's current one,
    so the tests that are not about R34's stale-save check do not have to restate it; the ones that *are*
    pass their own (a wrong one, an empty one, or the key left out entirely, which `raw_put` below sends)."""
    if isinstance(payload, dict) and "config" in payload and "version" not in payload:
        payload = {**payload, "version": config_version(v.config_path.read_text())}
    return raw_put(v, payload, **kw)


def raw_put(v, payload, **kw):
    """`PUT /api/config` with the body exactly as given -- no version filled in."""
    target = f"/api/config?t={kw.pop('token', TOKEN)}"
    headers = {"Host": kw.pop("host", "127.0.0.1:8765"), "Content-Type": "application/json"}
    return v.handle(Request.parse("PUT", target, headers, json.dumps(payload).encode()))


def test_request_parse_splits_the_target_and_lower_cases_header_names():
    req = Request.parse("get", "/api/events?stream=claude%2Fpm-1.log&t=abc&after=4", {"Host": "127.0.0.1:1", "Last-Event-ID": "7"})
    assert req.method == "GET" and req.path == "/api/events"
    assert req.query == {"stream": ["claude/pm-1.log"], "t": ["abc"], "after": ["4"]}
    assert req.headers == {"host": "127.0.0.1:1", "last-event-id": "7"}
    assert Request.parse("GET", "", {}).path == "/"


def test_new_token_is_urlsafe_and_fresh_per_call():
    a, b = new_token(), new_token()
    assert a != b and len(a) >= 43 and set(a) <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


@pytest.mark.parametrize("token, host", [
    (None, "127.0.0.1:8765"),                 # no token
    ("", "127.0.0.1:8765"),                   # empty token
    ("t0k3n-fixed-for-testS", "127.0.0.1:8765"),  # one character off
    (TOKEN, "evil.example:8765"),             # a rebinding host with the right port
    (TOKEN, "127.0.0.1:9999"),                # our host, another port
    (TOKEN, "127.0.0.1"),                     # no port at all
    (TOKEN, ""),                              # no Host header value
])
def test_the_gate_answers_403_with_an_empty_body(tmp_path, token, host):
    v, _, _ = viewer_for(tmp_path)
    resp = get(v, "/api/streams", token=token, host=host)
    assert (resp.status, resp.body) == (403, b"")


@pytest.mark.parametrize("host", LOOPBACK_HOSTS)
def test_every_loopback_host_with_the_served_port_is_admitted(tmp_path, host):
    v, _, _ = viewer_for(tmp_path)
    assert get(v, "/api/streams", host=f"{host}:8765").status == 200


def test_no_cors_header_is_ever_sent_and_the_security_headers_always_are(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    for resp in (get(v, "/api/streams"), get(v, "/api/streams", token=None), get(v, "/nowhere")):
        names = {k.lower() for k, _ in resp.headers}
        assert "access-control-allow-origin" not in names
        assert {"cache-control", "x-content-type-options", "referrer-policy"} <= names


def test_unknown_routes_are_404_and_the_wrong_method_is_405_behind_the_gate(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    assert get(v, "/nowhere").status == 404 and "no route" in body(get(v, "/nowhere"))["error"]
    assert get(v, "/api/config", method="DELETE").status == 405     # plan 8d: only GET and PUT
    assert get(v, "/api/action").status == 405                      # POST only
    assert get(v, "/api/state", method="POST").status == 405
    assert get(v, "/nowhere", token=None).status == 403             # the gate comes first


def test_api_state_carries_the_status_liveness_freezes_disk_and_logs(tmp_path):
    v, ctx, _ = viewer_for(tmp_path)
    home = ctx.store.home
    write_record(supervisor_path(home, "claude"), {"pid": 4242, "started_at": NOW, "proc_start": "T0"})
    write_record(freeze_path(home, "claude", "pm"), {"at": NOW, "posted": True, "resets_at": "2026-09-18T15:00:00Z"})
    (home / "logs" / "claude").mkdir(parents=True)
    (home / "logs" / "claude" / "pm-1.log").write_bytes(b"x" * (3 * 1024 * 1024))

    resp = get(v, "/api/state")
    assert resp.status == 200 and resp.content_type.startswith("application/json")
    state = body(resp)
    assert state["status"]["repo"] == "acme/widgets" and "budget" in state["status"]
    # Controller ruling: `render_status` labels the human party `actor.party.label`, which for kind="human" is
    # "[human]" (config.Party.label falls to `f"[{self.kind}]"`), not the bare "human" the brief's assertion
    # named. Reported as a concern rather than changed silently -- see the task report.
    assert state["status"]["party"] == "[human]"
    assert state["supervisors"]["claude"]["state"] == "alive" and state["supervisors"]["codex"]["state"] == "unknown"
    assert state["frozen"]["claude"]["pm"]["resets_at"] == "2026-09-18T15:00:00Z" and state["frozen"]["claude"]["orchestrator"] is None
    assert state["disk"] == {"free_mb": 3072, "warn_free_mb": ctx.cfg.logs_warn_free_mb}
    assert state["logs"] == {"total_mb": 3, "warn_total_mb": ctx.cfg.logs_warn_total_mb} and state["generated_at"] == NOW


def test_api_state_is_cached_single_flight_for_the_ttl_and_a_failure_is_not(tmp_path, monkeypatch):
    clock = Clock()
    v, ctx, _ = viewer_for(tmp_path, clock=clock, state_ttl=5.0)
    real, calls = server.build_status, []

    def counting(c, now=None):
        calls.append(now)
        if len(calls) == 3:
            raise GitHubError("gh: rate limited")
        return real(c, now=now)

    monkeypatch.setattr(server, "build_status", counting)
    assert get(v, "/api/state").status == 200 and get(v, "/api/state").status == 200 and len(calls) == 1
    clock.t += 5.0
    assert get(v, "/api/state").status == 200 and len(calls) == 2
    clock.t += 5.0
    failed = get(v, "/api/state")
    assert failed.status == 502 and body(failed) == {"error": "gh: rate limited"}
    assert get(v, "/api/state").status == 200 and len(calls) == 4      # the failure was not cached


def test_api_state_answers_502_rather_than_a_traceback_when_gh_itself_is_missing(tmp_path, monkeypatch):
    """`github._request` does not convert the FileNotFoundError a missing or mid-upgrade `gh` raises out of
    subprocess; unhandled it escapes `handle`, drops the connection and prints a traceback every ten seconds."""
    v, _, _ = viewer_for(tmp_path)

    def no_gh(c, now=None):
        raise FileNotFoundError(2, "No such file or directory", "gh")

    monkeypatch.setattr(server, "build_status", no_gh)
    resp = get(v, "/api/state")
    assert resp.status == 502 and "gh" in body(resp)["error"]
    assert get(v, "/api/state").status == 502                          # and, like the others, never cached


def test_api_state_reports_an_unreadable_disk_as_null_rather_than_failing(tmp_path):
    def broken(p):
        raise OSError("statvfs")

    v, _, _ = viewer_for(tmp_path, disk_usage=broken)
    assert body(get(v, "/api/state"))["disk"]["free_mb"] is None


def test_api_streams_lists_what_discover_finds(tmp_path):
    v, ctx, _ = viewer_for(tmp_path)
    home = ctx.store.home
    log = home / "logs" / "claude" / "pm-1.log"
    log.parent.mkdir(parents=True)
    log.write_text("hello\n")
    write_record(role_run_path(home, "claude", "pm"),
                 {"pid": 7, "started_at": NOW, "proc_start": "T0", "log_path": str(log), "reason": "start", "launch_no": 1})
    streams = body(get(v, "/api/streams"))["streams"]
    assert [(s["id"], s["kind"], s["liveness"]["state"]) for s in streams] == [("claude/pm-1.log", "role", "alive")]


def test_api_events_streams_every_transcript_and_resumes_from_the_cursor(tmp_path):
    # max_polls=2: the replay happens inside the loop now, so the idle check that ends a stream is the second poll.
    v, ctx, _ = viewer_for(tmp_path, alive=lambda pid: False, max_polls=2)   # the writer is confirmed gone
    home = ctx.store.home
    log = home / "logs" / "claude" / "pm-1.log"
    log.parent.mkdir(parents=True)
    log.write_text("one\ntwo\n")
    write_record(role_run_path(home, "claude", "pm"),
                 {"pid": 7, "started_at": NOW, "proc_start": "T0", "log_path": str(log), "reason": "start", "launch_no": 1})

    resp = get(v, "/api/events")                                   # no stream=: everything discover finds
    assert resp.status == 200 and resp.content_type == "text/event-stream; charset=utf-8" and resp.streaming
    text = b"".join(resp.body).decode()
    assert text.startswith("id: claude/pm-1.log=1\nevent: message\n") and '"stream": "claude/pm-1.log"' in text
    assert '"text": "two"' in text and 'event: run_finished' in text and '"synthesized": true' in text
    assert text.endswith('event: end\ndata: {"stream": "claude/pm-1.log"}\n\n')

    named = b"".join(get(v, "/api/events?stream=claude/pm-1.log").body).decode()
    assert named.startswith("id: claude/pm-1.log=1\n")
    resumed = b"".join(get(v, "/api/events", headers={"Last-Event-ID": "claude/pm-1.log=1"}).body).decode()
    assert '"text": "one"' not in resumed and '"text": "two"' in resumed
    by_query = b"".join(get(v, "/api/events?after=claude%2Fpm-1.log%3D2").body).decode()
    assert "event: message" not in by_query and by_query.startswith("event: run_finished\n")
    junk = b"".join(get(v, "/api/events", headers={"Last-Event-ID": "seven"}).body).decode()
    assert junk.startswith("id: claude/pm-1.log=1\n")               # unreadable cursor: from the start


@pytest.mark.parametrize("stream", ["claude/nope.log", "../.meld.toml", "ghost/pm-1.log", "claude"])
def test_api_events_answers_404_for_a_stream_that_is_not_a_log_of_a_configured_group(tmp_path, stream):
    v, ctx, _ = viewer_for(tmp_path)
    (ctx.store.home / "logs" / "ghost").mkdir(parents=True)
    (ctx.store.home / "logs" / "ghost" / "pm-1.log").write_text("x\n")   # exists on disk, but `ghost` is no group
    resp = get(v, f"/api/events?stream={stream}")
    assert resp.status == 404 and "error" in body(resp)


def test_api_events_with_no_stream_is_every_stream_not_an_error(tmp_path):
    v, ctx, _ = viewer_for(tmp_path, alive=lambda pid: False)
    (ctx.store.home / "logs" / "claude").mkdir(parents=True)
    (ctx.store.home / "logs" / "claude" / "pm-1.log").write_text("hi\n")
    assert get(v, "/api/events").status == 200 and get(v, "/api/events?stream=").status == 200


def test_the_page_and_its_assets_are_served_behind_the_gate_with_the_token_substituted(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    page = get(v, "/")
    assert page.status == 200 and page.content_type == "text/html; charset=utf-8"
    html = page.body.decode()
    assert "__TOKEN__" not in html and f"/static/app.css?t={TOKEN}" in html and f"/static/app.js?t={TOKEN}" in html
    assert html.lstrip().lower().startswith("<!doctype html>") and '<meta charset="utf-8">' in html
    assert get(v, "/", token=None).status == 403 and get(v, "/static/app.js", token=None).status == 403
    assert get(v, "/static/app.css").content_type == "text/css; charset=utf-8"
    assert get(v, "/static/app.js").content_type == "text/javascript; charset=utf-8"
    assert get(v, "/", method="POST").status == 405


def test_the_page_references_nothing_off_the_machine_and_renders_text_as_text(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    html, css, js = (get(v, p).body.decode() for p in ("/", "/static/app.css", "/static/app.js"))
    for text in (html, css, js):
        # The SVG namespace is a name createElementNS needs, not a reference: nothing is fetched from it.
        # ... and GitHub is where the page's references point: a link a person opens, never a resource the page loads
        # (ghUrl is the one place that spells the host, pinned below).
        scrubbed = text.replace("http://www.w3.org/2000/svg", "").replace("https://github.com/", "")
        assert "http://" not in scrubbed and "https://" not in scrubbed and "//cdn" not in scrubbed and "@import" not in scrubbed
    assert js.count("https://github.com/") == 1 and "<link" not in html.lower().replace('<link rel="stylesheet" href="/static/app.css', "")
    assert "innerHTML" not in js and "textContent" in js and "EventSource" in js
    assert 'className = "badge' not in js
    assert "classList" in js
    assert "MAX_SOURCES" not in js and "openWaiting" not in js      # one connection now, so no cap and no queue
    assert js.count("new EventSource") == 1
    assert "msg.stream" in js and "feed" in js
    assert 'id="feed"' in html
    for route in ("/api/state", "/api/streams", "/api/events"):
        assert route in js
    assert 'name="referrer" content="no-referrer"' in html


def test_the_page_renders_the_board_and_offers_only_verbs_it_can_drive(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    html, js = get(v, "/").body.decode(), get(v, "/static/app.js").body.decode()
    assert 'id="issues"' in html and 'id="pulls"' in html and 'id="agreements"' in html
    assert 'id="row"' in html                       # the row template the board clones
    # DRIVEABLE lists the ten verbs the API can run at all; the page turns one into a button only if FIELDS
    # also has an entry for it -- a verb outside DRIVEABLE and a driveable one the bar can't spell (today just
    # `tag`, still named here via DRIVEABLE) both render as text instead, each for its own stated reason.
    assert "DRIVEABLE" in js and "dataset.action" in js
    for verb in ("claim", "release", "merge", "decide", "remember", "challenge", "post", "tag",
                 "plan-approve", "plan-ack"):
        assert f'"{verb}"' in js
    # "renderBoard(s)" alone would still match the function's own signature (`function renderBoard(s) {`) if the
    # call site were deleted -- verified by experiment (see the fix-round report); the semicolon is what pins
    # this to the call inside renderState.
    assert "renderBoard(s);" in js
    assert "dataset.ref" in js and "dataset.number" in js
    # The button/text decision is the bar's own capability (FIELDS), not merely what the API can run
    # (DRIVEABLE) -- that's the fix this round made, and it's what keeps a legal `tag` from being a button that
    # silently does nothing.
    assert "if (!FIELDS[verb])" in js
    # Pinning the other half: DRIVEABLE must still be read (not dead code left over from the old branch), and
    # the dim text's own title must never claim ownership of a verb -- rules.py, not this page, says whose
    # verb it is (approve/request-changes/push-fix/reopen are all a human's, and none of them is DRIVEABLE).
    assert "word.title = why" in js and "DRIVEABLE.has(verb)" in js
    assert "not to you" not in js
    # Nor may it claim a CLI path it does not have: `approve`, `request-changes` and `push-fix` are all legal
    # for a human and none of them runs from any `meld` command (review.py refuses a non-PM actor outright), so
    # the undriveable branch says only what this page lacks. "runs from the CLI" survives exactly twice -- the
    # driveable branch's own true string, and the comment clause above it saying why that one is a fact.
    assert "this page has no control for it" in js
    assert js.count("runs from the CLI") == 2


def test_the_page_can_drive_exactly_the_verbs_the_api_implements(tmp_path):
    """`DRIVEABLE` is the page's list of verbs it has a control for; `VERBS` is what `/api/action` implements. A
    verb in one and not the other is either a button that can only ever 500, or a capability with no way to reach
    it — and this is the half of "offers only verbs it can drive" that Python can actually check."""
    from meld.viewer.actions import VERBS
    v, _, _ = viewer_for(tmp_path)
    js = get(v, "/static/app.js").body.decode()
    literal = re.search(r"const DRIVEABLE = new Set\(\[(.*?)\]\)", js, re.S)
    assert literal, "DRIVEABLE is no longer where this test can read it"
    assert set(re.findall(r'"([^"]+)"', literal.group(1))) == set(VERBS)
    # And the half below it: `FIELDS` is what the bar can spell, and every button the board renders comes from
    # a `FIELDS` entry. A key outside `VERBS` would therefore be a button that can only ever answer 400
    # "unknown verb". Subset, not equality: `tag` is in `VERBS` with no `FIELDS` entry on purpose (its eight
    # fields have no form yet), which is the case that renders as text instead.
    fields = re.search(r"const FIELDS = \{(.*?)\};", js, re.S)
    assert fields, "FIELDS is no longer where this test can read it"
    keys = set(re.findall(r'"([^"]+)":', fields.group(1)))
    assert keys and keys <= set(VERBS), sorted(keys - set(VERBS))


def test_the_page_has_one_action_bar_that_posts_and_renders_a_refusal_verbatim(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    html, js = get(v, "/").body.decode(), get(v, "/static/app.js").body.decode()
    assert 'id="bar"' in html and 'id="action"' in html and 'id="run"' in html and 'id="result"' in html
    assert '"/api/action"' in js and 'method: "POST"' in js and 'party: "human"' in js
    # every header verb a human may post is offered, and the command verbs are not (they go through challenge)
    for verb in ("ASK", "DIRECTIVE", "HALT", "RESUME", "REVERT", "PROVIDED", "RESOLVED", "ACK", "AMEND",
                 "APPROVE", "NEEDS-HUMAN"):
        assert f'"{verb}"' in js
    for verb in ("LEASE", "EXPIRED", "RELEASED", "LIMITED"):
        assert f'"{verb}"' not in js
    assert "refused" in js and "rule" in js and "innerHTML" not in js


def test_the_page_has_a_config_form_that_reads_and_writes_the_known_schema(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    html, js = get(v, "/").body.decode(), get(v, "/static/app.js").body.decode()
    assert 'id="config"' in html and 'id="config-fields"' in html and 'id="config-save"' in html
    assert '"/api/config"' in js and 'method: "PUT"' in js
    # identity is shown and not editable (spec 22): the form never turns these into inputs
    assert "READONLY_TABLES" in js and '"repo"' in js and '"humans"' in js
    assert "READONLY_GROUP_FIELDS" in js
    for field in ("bot", "auth", "token_env", "app_id", "installation_id", "private_key_env", "private_key_file"):
        assert f'"{field}"' in js
    # the form is generated, not hand-written: no per-field id list
    assert "configState.models" in js                            # model choices come from the catalogue (R14)
    assert "innerHTML" not in js
    # R22 (correcting R21's own pin): a bare "isTable" in js is vacuous -- the name also occurs in the
    # definition and in both of collectConfig's own guards, so deleting *just* the two render guards below
    # (the actual R15/R21 behaviour: a table value must never reach field()) left the substring in place and
    # this test passing at 51/51. renderConfig's two guards are the predicate "!isTable(value)" inside a
    # `.filter()` (one per loop, over `values`/`group`'s own entries -- both destructure their pair as
    # `value`); collectConfig's two guards are the differently-named "!isTable(node)" and can't be confused
    # with these. Pinning the exact count catches either one being deleted. Still only a source-string check
    # -- it proves the two call sites are still in the file's bytes, not that they run correctly.
    assert js.count("!isTable(value)") == 2
    # R25: the visible label is relative to its heading (e.g. "orchestrator.model" under [groups.claude]),
    # so two sibling fields that only differ in their last segment don't both truncate to the same text; the
    # full dotted path stays in the title and in dataset.path, which collectConfig still walks unchanged.
    assert "field(card, `groups.${name}.${role}.model`" in js and "label: `${role} model`" in js


def test_the_config_form_does_not_write_a_silent_zero_and_keeps_effort_choices_in_sync(tmp_path):
    """R18/R19/R23, all found by a real Chrome driving the real page -- Python can only pin the source, not
    drive a browser, so these are string/regex checks on the shipped fix rather than a behavioural test.
    R23 found that two of these bit nothing: `"effortChoices" in js` and `addEventListener("change"` in js
    are both satisfied by code that predates this round (the catalogue lookup; the action bar's own
    listener), and a bare `"linkModelAndEffort" in js` pins the function's *definition*, not that anything
    calls it. Each assertion below is written to actually die when the fix it names is undone.

    R18: `Number("")` is `0`, and Chrome's own `<input type=number>` also hands back `""` for typed garbage
    ("26x") -- so a cleared or mistyped number field must never be collected as a silent 0; it must be
    refused locally, before anything is sent -- and the refusal must actually stop the save, not just show a
    message and send the request anyway. R19: changing a model <select> must repopulate its sibling effort
    <select>, or the pair can silently fall out of sync and the server 409s on a combination this page itself
    offered."""
    v, _, _ = viewer_for(tmp_path)
    js = get(v, "/static/app.js").body.decode()
    assert "Number.isFinite" in js and 'input.value.trim() === ""' in js
    assert "fix before saving" in js
    # R23: the refusal's `return;` -- not just its message -- must survive. Pinned as one contiguous piece of
    # text, so removing only the `return;` (leaving showConfigResult in place) breaks this too.
    assert 'showConfigResult(`fix before saving -- not a number: ${invalid.join(", ")}`, false);\n      return;' in js
    # R23: "linkModelAndEffort(" appears twice when the fix is wired up -- once for the function's own
    # declaration, once more for a real call site. 1 occurrence means it was defined but never called.
    assert js.count("linkModelAndEffort(") >= 2
    # "saving ..." is in progress, not a success -- must not be painted green.
    assert 'showConfigResult("saving …", null)' in js
    # R33: a number input carries the weakest floor any field has and an integer step, so the spinner cannot
    # walk a setting into a fraction. Pinned as one contiguous block, because neither line is worth anything
    # without the other and because the surrounding comment mentions `min` and `step` on their own.
    assert 'input.min = "0";\n      input.step = "1";' in js


def test_the_config_form_sends_back_the_version_it_was_given(tmp_path):
    """R34. Without it the server cannot tell a stale form from a current one, and the save silently
    reverts whatever the file gained since this panel read it. Pinned as the whole `body:` expression:
    `configState.version` occurs nowhere else, and removing just the version -- leaving the fetch intact --
    has to fail here too."""
    v, _, _ = viewer_for(tmp_path)
    js = get(v, "/static/app.js").body.decode()
    assert "body: JSON.stringify({ config, version: configState.version })," in js


def test_api_config_returns_the_file_the_path_and_the_model_catalogue(tmp_path):
    v, ctx, _ = viewer_for(tmp_path)
    out = body(get(v, "/api/config"))
    assert out["path"] == str(tmp_path / ".meld.toml")
    assert out["config"]["repo"]["name"] == ctx.cfg.repo_name
    assert out["config"]["groups"]["claude"]["orchestrator"]["model"] == ctx.cfg.groups["claude"].orchestrator.model
    assert out["models"]["claude"] and all({"id", "efforts", "tier"} <= set(m) for m in out["models"]["claude"])
    assert "note" in out                                   # a write reconfigures nothing already running
    assert out["version"] == config_version((tmp_path / ".meld.toml").read_text())   # R34: what PUT must name


def test_the_config_note_says_what_a_write_does_not_that_one_has_happened(tmp_path):
    """R35. `loadConfig` paints this note green the moment the panel opens, and the note began "saved to the
    file; ..." -- so the first thing a person saw on a panel they had not touched was their own path
    followed by a claim that something had been saved, and after an actual save it read "saved -- saved to
    the file; ...". The note is a statement about what writing does; the page's own prefix supplies the
    tense, on both lines."""
    v, _, _ = viewer_for(tmp_path)
    note = body(get(v, "/api/config"))["note"]
    js = get(v, "/static/app.js").body.decode()
    # the two lines the page composes from it, quoted from app.js so a reworded prefix has to come back here
    assert "showConfigResult(`${configState.path} — ${configState.note}`, true)" in js
    assert "showConfigResult(`saved — ${out.note}`, true)" in js
    assert "saved" not in note and "wrote" not in note, note
    assert f"{tmp_path / '.meld.toml'} — {note}".startswith(
        f"{tmp_path / '.meld.toml'} — writing it reconfigures nothing already running")
    assert f"saved — {note}".startswith("saved — writing it reconfigures nothing already running")


def test_api_config_get_canonicalises_a_starter_file_and_still_names_unknown_keys(tmp_path):
    """R20: a fresh `meld setup` file has no [budget]/[polling]/[runners]/[timeouts]/[logs]/[viewer] table at
    all, yet spec 22 promises the form every one of those settings -- the raw parse alone cannot show a field
    the file never wrote. `GET` must show the schema's effective values (what `dumps`/`Config.from_dict`
    agree the file means), not the bare file, while still surfacing -- and still refusing by name on `PUT` --
    a key the schema does not know, so canonicalising never becomes a second, silent way to drop one.

    R24: every value asserted below is deliberately steered away from its own schema default (branch "main",
    private true, quorum 2) or has no default at all (`notify`'s webhook env is `None` when unset, which
    `dumps` omits as a line entirely rather than writing some other value) -- a canonicaliser that silently
    discarded `[review]`/`[notify]`/[repo]'s own overrides and fell back to computed defaults would satisfy
    the *previous* version of this test just as well as a correct one; it cannot satisfy this one.
    """
    from meld.models import ModelRegistry
    from meld.setup import starter_toml
    v, _, _ = viewer_for(tmp_path)
    registry = ModelRegistry.load()
    text = starter_toml("acme/widgets", "develop", "empowerScott", ["claude", "codex"], registry)
    text = text.replace("private = true", "private = false").replace("quorum = 2", "quorum = 5")
    text += '\n[notes]\nkept = "hand-added, unknown to the schema"\n'
    (tmp_path / ".meld.toml").write_text(text)

    cfg = body(get(v, "/api/config"))["config"]
    # every table spec 22 names, though the starter file itself never wrote one of them
    for table in ("budget", "polling", "runners", "timeouts", "logs", "viewer"):
        assert table in cfg, f"{table} missing from a starter file's GET"
    # values the file set, each one chosen to differ from its own schema default (or to have none at all)
    assert cfg["repo"] == {"name": "acme/widgets", "default_branch": "develop", "private": False}
    assert cfg["review"]["quorum"] == 5
    assert cfg["notify"]["slack_dm_webhook_env"] == "MELD_SLACK_DM_WEBHOOK"
    # an unknown key still rides along, visible -- and is still refused by name on a PUT
    assert cfg["notes"]["kept"] == "hand-added, unknown to the schema"
    resp = put(v, {"config": cfg})
    assert resp.status == 400 and "notes" in body(resp)["error"]


def test_api_config_get_falls_back_to_the_raw_parse_when_it_cannot_be_canonicalised(tmp_path):
    """A file too broken for `Config.from_dict` to parse (here: `claude`'s group is missing its required
    `cli`) must still answer with *something* a person can read and fix, rather than GET failing just because
    canonicalising it turned out to be impossible."""
    v, _, _ = viewer_for(tmp_path)
    config_path = tmp_path / ".meld.toml"
    config_path.write_text(PROJECT_TOML.replace('cli = "claude"\n', ""))   # claude's group no longer parses
    resp = get(v, "/api/config")
    assert resp.status == 200
    cfg = body(resp)["config"]
    assert "cli" not in cfg["groups"]["claude"]                 # the raw parse, verbatim -- not canonicalised
    assert cfg["groups"]["codex"]["bot"] == "meld-codex-bot"    # the rest of the raw file is still legible
    assert "budget" not in cfg                                  # the fallback is the bare file, not filled in


def test_a_get_reports_a_serializer_bug_as_a_500_rather_than_silently_falling_back(tmp_path, monkeypatch):
    """R37.1. `_canonical_config` catches a bad *file* and falls back to the raw parse -- but `tomllib`'s own
    `TOMLDecodeError` is a subclass of `ValueError`, so the one clause used to swallow a `dumps` that emits
    unparseable TOML as well, and `GET` answered 200 with the raw parse while `PUT` reported the identical
    bug as a 500. Rendering and reading back is the serializer's business, not the file's: only
    `Config.from_dict` is inside the `try` now."""
    v, _, _ = viewer_for(tmp_path)
    before = body(get(v, "/api/config"))["config"]
    assert "budget" in before                                  # canonicalised, so the fallback is visible
    monkeypatch.setattr(server, "dumps", lambda cfg: "this is = = not toml\n")
    resp = get(v, "/api/config")
    assert resp.status == 500
    assert "TOMLDecodeError" in body(resp)["error"]


def test_a_get_survives_a_quoted_key_that_contains_a_dot_inside_a_known_table(tmp_path):
    """R37.2 at the route. `[review]` with `"a.b" = 1` is one unknown key two levels deep, not a three-level
    one -- but the walk that copies an unknown key back into the canonical config split its dotted rendering
    and indexed `parsed["review"]["a"]`, so `GET` answered 500 `KeyError: 'a'` and the whole form was
    unavailable for that file. Everything else on this route works hard to make sure `GET` answers."""
    v, _, _ = viewer_for(tmp_path)
    path = tmp_path / ".meld.toml"
    path.write_text(path.read_text() + '\n[review]\n"a.b" = 1\n')
    resp = get(v, "/api/config")
    assert resp.status == 200, body(resp)
    cfg = body(resp)["config"]
    assert cfg["review"]["a.b"] == 1        # carried over verbatim, beside a fully canonical [review]
    assert cfg["review"]["quorum"] == 2
    # and still refused by name on PUT, where the dotted rendering is all a message needs
    refusal = put(v, {"config": cfg})
    assert refusal.status == 400 and "review.a.b" in body(refusal)["error"]


def test_a_get_names_the_two_states_that_block_every_save(tmp_path):
    """R36. Both behaviours are spec-mandated and correctly implemented; what was missing is the diagnosis
    alongside. (a) A hand-added unknown key renders as an ordinary editable field, so a person learned only
    on Save -- `400 unknown setting(s): notes.kept` -- that *nothing* could be written until they hand-edit
    the file, and that blanking the box does not remove the key. (b) When `Config.from_dict` raises and the
    read falls back to the raw parse, the body carried no sign of it at all: a sparser form than usual, and
    every save refused by a parser message the person was never shown."""
    v, _, _ = viewer_for(tmp_path)
    path = tmp_path / ".meld.toml"
    clean = body(get(v, "/api/config"))
    assert clean["unknown"] == [] and "error" not in clean      # a clean file says so rather than staying silent

    path.write_text(path.read_text() + '\n[notes]\nkept = "hand-added"\n')
    out = body(get(v, "/api/config"))
    # a whole unknown *table* is reported as the table, the same word the route's own 400 already uses
    assert out["unknown"] == ["notes"] and "error" not in out
    assert out["config"]["notes"]["kept"] == "hand-added"       # still carried over verbatim, as spec 22 wants

    path.write_text(path.read_text() + "\n[review]\nquorumm = 9\n")     # and a key inside a table it knows
    assert body(get(v, "/api/config"))["unknown"] == ["notes", "review.quorumm"]

    path.write_text(PROJECT_TOML.replace('cli = "claude"\n', ""))     # claude's group no longer parses
    out = body(get(v, "/api/config"))
    assert "missing required setting groups.claude.cli" in out["error"]
    assert "budget" not in out["config"]                        # the raw-parse fallback -- which now says so


def test_a_file_that_fails_a_floor_can_be_fixed_in_the_form_and_saved(tmp_path):
    """R44, and the reason the fallback line had to be reworded. Twenty-four settings can now fail to parse
    on a value the raw parse still renders perfectly well, so the common way to reach `_canonical_config`'s
    fallback is no longer a structurally sparser file -- it is one number out of range, sitting in a box
    the form is already showing. Typing a legal value into that box and saving works, which is exactly
    what the old status line said was impossible.

    Contrast the sibling case: a *missing* required key has no box to type into, because the raw parse has
    no field for a key the file never wrote, and that one really does need the file opened by hand. The
    line now names both and asserts neither."""
    v, _, _ = viewer_for(tmp_path)
    path = tmp_path / ".meld.toml"
    path.write_text(PROJECT_TOML + "\n[review]\nquorum = 0\n")
    out = body(get(v, "/api/config"))
    assert "review.quorum must be an integer of at least 1" in out["error"]      # the fallback, diagnosed
    assert out["config"]["review"]["quorum"] == 0                                # ... and still editable

    current = out["config"]
    current["review"]["quorum"] = 2                                              # fixed in the form alone
    # PROJECT_TOML's codex model is the deliberately-invalid "gpt-x" (see the sibling write test).
    current["groups"]["codex"]["orchestrator"]["model"] = current["groups"]["codex"]["pm"]["model"] = "gpt-5.6-sol"
    resp = put(v, {"config": current, "version": out["version"]})
    assert resp.status == 200, body(resp)
    assert load_config(path).review_quorum == 2
    after = body(resp)
    assert "error" not in after and after["config"]["review"]["quorum"] == 2     # and the panel is clean now

    # the other shape, for contrast: no field to type into, so the file really must be opened
    path.write_text(PROJECT_TOML.replace('cli = "claude"\n', ""))
    out = body(get(v, "/api/config"))
    assert "missing required setting groups.claude.cli" in out["error"]
    assert "cli" not in out["config"]["groups"]["claude"]


def test_a_put_ignores_the_read_side_keys_the_way_it_already_ignores_the_others(tmp_path):
    """`unknown` and `error` are diagnosis, not settings: `PUT` re-derives the unknown keys from the
    candidate itself, so a body that omits them, or lies about them, changes nothing -- exactly as it
    already ignores `path`, `models` and `note`."""
    v, _, _ = viewer_for(tmp_path)
    out = body(get(v, "/api/config"))
    current = out["config"]
    # PROJECT_TOML's codex model is the deliberately-invalid "gpt-x" (see the sibling write test).
    current["groups"]["codex"]["orchestrator"]["model"] = current["groups"]["codex"]["pm"]["model"] = "gpt-5.6-sol"
    resp = put(v, {"config": current, "version": out["version"], "unknown": ["review.nonsense"],
                   "error": "invented", "path": "/somewhere/else", "models": {}, "note": "ignored"})
    assert resp.status == 200, body(resp)
    answered = body(resp)
    assert answered["path"] == str(tmp_path / ".meld.toml") and answered["unknown"] == []
    assert "error" not in answered and answered["note"] == server.Viewer.CONFIG_NOTE


def test_the_config_form_shows_the_diagnosis_the_form_itself_cannot(tmp_path):
    """R36 on the page. The line on open is refusal-coloured and names the blocker, instead of the green
    note over a form whose every save is already doomed; and each unknown key is marked where it sits, so
    it is findable in a grid of forty fields."""
    v, _, _ = viewer_for(tmp_path)
    js, css = get(v, "/static/app.js").body.decode(), get(v, "/static/app.css").body.decode()
    # the branch itself, pinned contiguously: a `configTrouble` that is defined and never consulted, or
    # consulted and still painted green, has to fail here.
    assert ("const trouble = configTrouble();\n"
            "    if (trouble) showConfigResult(`${configState.path} — ${trouble}`, false);\n"
            "    else showConfigResult(`${configState.path} — ${configState.note}`, true);") in js
    assert "if (configState.error) {" in js                     # the fallback state
    # R44. The line here used to promise "every save is refused until it is hand-edited", which the
    # sibling test below proves false. Both halves are pinned: the dead claim must not come back, and the
    # honest one -- which says where the fix may be, rather than asserting it cannot be made here -- must
    # survive. Each is contiguous inside a single template literal, so neither can be half-removed.
    assert "every save is refused until it is hand-edited" not in js
    assert "a save is refused until the problem is fixed: ${configState.error}" in js
    assert " — change it here if the form shows that field, or edit the file by hand if it does not" in js
    assert "no save can pass while the file carries" in js      # the unknown-key state
    assert 'labelEl.classList.add("unknown");' in js            # marked in the form, not only in the line
    # an unknown *table* is reported as the table, so its fields are unknown by inheritance -- a bare
    # set-membership test would mark `review.quorumm` and leave `notes.kept` looking like a real setting
    assert "for (let i = 1; i <= parts.length; i++) if (unknown.has(parts.slice(0, i).join(\".\"))) return true;" in js
    assert "#config-fields label.unknown > span" in css         # ... and the mark is visible


def test_a_put_writes_the_file_canonically_and_answers_with_what_it_wrote(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    current = body(get(v, "/api/config"))["config"]
    # `.get("budget", {})` is now just habit, not defence (R26): since R20, GET's config is canonicalised, so
    # `current["budget"]` always exists already -- PROJECT_TOML itself still carries no [budget] table, but
    # that no longer means anything is missing by the time it reaches here.
    current["budget"] = {**current.get("budget", {}), "large": 26}
    # PROJECT_TOML's codex model is the deliberately-invalid "gpt-x" (test_doctor.py's VALID_MODELS_TOML swaps
    # it the same way): config_checks would 409 on it against the real catalogue regardless of this test's own
    # change, which is not what this test is about.
    current["groups"]["codex"]["orchestrator"]["model"] = current["groups"]["codex"]["pm"]["model"] = "gpt-5.6-sol"
    resp = put(v, {"config": current})
    assert resp.status == 200, body(resp)
    written = (tmp_path / ".meld.toml").read_text()
    assert written.startswith("#") and "large = 26" in written
    assert load_config(tmp_path / ".meld.toml").budget["large"] == 26
    assert body(resp)["config"]["budget"]["large"] == 26       # the answer is the file, re-read
    assert body(get(v, "/api/config"))["config"]["budget"]["large"] == 26


def test_a_put_from_a_stale_form_is_refused_rather_than_reverting_the_hand_edit(tmp_path):
    """R34. `GET`, hand-edit a value on disk, `PUT` the form you were already holding: that answered 200 and
    the hand edit was simply gone. `loadConfig` re-reads only when `configState` is null, so a panel opened
    an hour ago still holds the hour-old copy -- and re-reading on every panel open is deliberately *not*
    the fix, because closing and reopening the <details> would then discard a person's in-progress edits,
    trading a rare clobber for a frequent one. The version `GET` returns is what closes it instead."""
    v, _, _ = viewer_for(tmp_path)
    path = tmp_path / ".meld.toml"
    first = body(get(v, "/api/config"))
    stale = first["config"]
    # PROJECT_TOML's codex model is the deliberately-invalid "gpt-x", which config_checks would 409 on
    # regardless of this test's own change (see the sibling write test).
    stale["groups"]["codex"]["orchestrator"]["model"] = stale["groups"]["codex"]["pm"]["model"] = "gpt-5.6-sol"

    path.write_text(path.read_text() + "\n[lease]\ngrace_minutes = 42\n")      # the hand edit this panel never saw
    hand_edited = path.read_text()

    refusal = put(v, {"config": stale, "version": first["version"]})
    assert refusal.status == 409
    assert "changed on disk" in body(refusal)["error"]
    assert path.read_text() == hand_edited                       # the hand edit is still there

    # the version the refusal names is the file's current one, and a reload makes the same save work
    fresh = body(get(v, "/api/config"))
    assert body(refusal)["version"] == fresh["version"] != first["version"]
    assert fresh["config"]["lease"]["grace_minutes"] == 42
    current = fresh["config"]
    current["groups"]["codex"]["orchestrator"]["model"] = current["groups"]["codex"]["pm"]["model"] = "gpt-5.6-sol"
    ok = put(v, {"config": current, "version": fresh["version"]})
    assert ok.status == 200, body(ok)
    assert load_config(path).lease_grace_minutes == 42            # and it survives the save that follows


def test_a_put_needs_the_version_its_get_returned(tmp_path):
    """The key itself is required, not merely honoured when present: a client that never learned about it
    would otherwise keep the old, silently-clobbering behaviour."""
    v, _, _ = viewer_for(tmp_path)
    current = body(get(v, "/api/config"))["config"]
    before = (tmp_path / ".meld.toml").read_text()
    for payload in ({"config": current},                                   # omitted
                    {"config": current, "version": None},                  # null
                    {"config": current, "version": ""},                    # empty
                    {"config": current, "version": 7}):                    # not a string
        resp = raw_put(v, payload)
        assert resp.status == 400, payload
        assert "version" in body(resp)["error"], payload
    assert (tmp_path / ".meld.toml").read_text() == before


def test_a_successful_put_answers_with_a_version_the_next_save_can_use(tmp_path):
    """The page re-renders from the answer, so the panel that just saved must come back current rather than
    instantly stale against the file it wrote itself."""
    v, _, _ = viewer_for(tmp_path)
    first = body(get(v, "/api/config"))
    current = first["config"]
    current["budget"] = {**current["budget"], "large": 23}
    current["groups"]["codex"]["orchestrator"]["model"] = current["groups"]["codex"]["pm"]["model"] = "gpt-5.6-sol"
    saved = body(put(v, {"config": current, "version": first["version"]}))
    assert saved["ok"] is True
    assert saved["version"] == config_version((tmp_path / ".meld.toml").read_text()) != first["version"]
    again = put(v, {"config": saved["config"], "version": saved["version"]})
    assert again.status == 200, body(again)


def test_a_put_refuses_a_key_the_schema_does_not_know_and_names_it(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    current = body(get(v, "/api/config"))["config"]
    before = (tmp_path / ".meld.toml").read_text()
    # `.get("review", {})` is the same now-just-habit pattern as the budget test above (R26): since R20,
    # `current["review"]` always exists already (canonicalised, defaults filled in), so the {} fallback can
    # never actually be reached -- kept anyway, matching the sibling test's idiom, rather than a bare
    # current["review"][...] that would depend on that continuing to be true.
    current["review"] = {**current.get("review", {}), "quorumm": 9}
    resp = put(v, {"config": current})
    assert resp.status == 400 and "review.quorumm" in body(resp)["error"]
    assert (tmp_path / ".meld.toml").read_text() == before      # the original is untouched


def test_a_put_refuses_a_config_the_parser_rejects(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    current = body(get(v, "/api/config"))["config"]
    before = (tmp_path / ".meld.toml").read_text()
    del current["groups"]["claude"]["cli"]
    resp = put(v, {"config": current})
    assert resp.status == 400 and "cli" in body(resp)["error"]
    assert (tmp_path / ".meld.toml").read_text() == before


def test_a_put_refuses_a_config_the_doctor_checks_reject(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    current = body(get(v, "/api/config"))["config"]
    before = (tmp_path / ".meld.toml").read_text()
    current["groups"]["claude"]["orchestrator"]["model"] = "gpt-imaginary"
    resp = put(v, {"config": current})
    assert resp.status == 409
    out = body(resp)
    assert any(c["name"] == "models:claude" and c["ok"] is False for c in out["checks"])
    assert "gpt-imaginary" in json.dumps(out["checks"])
    assert (tmp_path / ".meld.toml").read_text() == before


def test_a_put_needs_a_json_object_with_a_config(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    before = (tmp_path / ".meld.toml").read_text()
    for payload in ({}, {"config": None}, {"config": []}, {"config": {}}):
        assert put(v, payload).status == 400, payload
    bad = v.handle(Request.parse("PUT", f"/api/config?t={TOKEN}", {"Host": "127.0.0.1:8765"}, b"not json"))
    assert bad.status == 400
    assert (tmp_path / ".meld.toml").read_text() == before


@pytest.mark.parametrize("mutate, expect", [
    # A cleared number field. Since R33 every numeric read goes through `_at_least`, so the refusal names the
    # *setting* as well as the value -- `int()`'s own "invalid literal for int() with base 10: ''" named
    # nothing a person could locate on the page.
    (lambda c: c.setdefault("review", {}).update(quorum=""),
     "review.quorum must be an integer of at least 1 approving groups, got ''"),
    # a non-numeric string in a group setting, one level deeper
    (lambda c: c["groups"]["claude"].update(concurrency="lots"),
     "groups.claude.concurrency must be an integer of at least 1 concurrent dispatches, got 'lots'"),
    # json.loads accepts the non-standard Infinity/NaN literals as floats. They used to reach `int()` and
    # raise OverflowError/ValueError from deep inside it; `_at_least` refuses them as the non-integers they
    # are, before any conversion. The except clause still widens past ConfigError for the shapes that get
    # past the guards entirely (`review = 5` -> AttributeError).
    (lambda c: c.setdefault("budget", {}).update(large=float("inf")),
     "budget.large must be an integer of at least 0 runs, got inf"),
    (lambda c: c.setdefault("budget", {}).update(large=float("nan")),
     "budget.large must be an integer of at least 0 runs, got nan"),
    # R33, the three the form itself can produce and every one of which used to answer 200. A zero
    # concurrency refuses every dispatch for that group (`rules.py`'s `n >= group.concurrency`); a zero poll
    # interval is `waiting.py`'s backoff, starting at 0.0 and doubling to 0.0 forever; a fractional review
    # concurrency was silently truncated by `int()` and re-rendered as the truncation with no sign of it.
    (lambda c: c["groups"]["claude"].update(concurrency=0),
     "groups.claude.concurrency must be an integer of at least 1 concurrent dispatches, got 0"),
    (lambda c: c.setdefault("polling", {}).update(interval_seconds=-5),
     "polling.interval_seconds must be an integer of at least 1 seconds, got -5"),
    (lambda c: c["groups"]["claude"].update(review_concurrency=2.5),
     "groups.claude.review_concurrency must be an integer of at least 1 concurrent reviews, got 2.5"),
    # and the one place a zero is still legal, for contrast: it is the *negative* that is refused.
    (lambda c: c.setdefault("lease", {}).update(grace_minutes=-1),
     "lease.grace_minutes must be an integer of at least 0 minutes, got -1"),
    # R41. Not a number but a duration, and the same failure: `dispatch` sets a worker's deadline to
    # `when + timeout_seconds(size)`, so a zero launches one already past it -- a paid CLI session per
    # launch. Every size shares one read site; `tests/test_config.py` covers all three and every spelling
    # of zero, this pins that the form's own route refuses one.
    (lambda c: c.setdefault("timeouts", {}).update(S="0m"),
     "timeouts.S must be a duration greater than zero, got '0m'"),
    (lambda c: c.setdefault("timeouts", {}).update(L=0),
     "timeouts.L must be a duration greater than zero, got '0'"),
], ids=["cleared-number", "non-numeric-string", "infinity", "nan",
        "zero-concurrency", "negative-interval", "fractional-concurrency", "negative-grace",
        "zero-timeout", "bare-zero-timeout"])
def test_a_put_refuses_an_unparseable_number_with_400_not_a_traceback(tmp_path, mutate, expect):
    v, _, _ = viewer_for(tmp_path)
    current = body(get(v, "/api/config"))["config"]
    before = (tmp_path / ".meld.toml").read_text()
    mutate(current)
    resp = put(v, {"config": current})
    assert resp.status == 400
    assert expect in body(resp)["error"]
    assert (tmp_path / ".meld.toml").read_text() == before


def test_a_put_still_accepts_a_zero_where_zero_is_the_setting(tmp_path):
    """The other half of R33, and the rule stated: a floor goes on a field where `0` is a lie, not on every
    field. A budget of 0 ("no runs of this tier"), a lease grace of 0 ("the lease expires at the
    deadline"), an amend cap of 0 ("propose, then AGREE or DISSENT") and an audit batch of 0 hours ("do not
    wait on age; audit as soon as anything is pending") each mean exactly what they say, and a parser that
    refused them would be taking away a usable policy for no gain.

    Its counterpart is the `zero-concurrency` case in the parametrized refusal above, and the sharpest
    contrast is inside `[audit]` itself: `batch_hours = 0` is kept here while `batch_merges = 0` is
    refused, because `audit.due` guards both with `bool(pending)` and a batch of nothing can only mean 1."""
    v, _, _ = viewer_for(tmp_path)
    current = body(get(v, "/api/config"))["config"]
    current["budget"] = {**current["budget"], "large": 0, "wallclock_hours": 0}
    current["lease"] = {**current["lease"], "grace_minutes": 0}
    current["agreement"] = {**current["agreement"], "amend_cap": 0}
    current["audit"] = {**current["audit"], "batch_hours": 0}
    # PROJECT_TOML's codex model is the deliberately-invalid "gpt-x"; config_checks would 409 on it
    # regardless of this test's own change (see the sibling write test).
    current["groups"]["codex"]["orchestrator"]["model"] = current["groups"]["codex"]["pm"]["model"] = "gpt-5.6-sol"
    resp = put(v, {"config": current})
    assert resp.status == 200, body(resp)
    written = load_config(tmp_path / ".meld.toml")
    assert (written.budget["large"], written.budget_wallclock_hours) == (0, 0)
    assert (written.lease_grace_minutes, written.amend_cap, written.audit_batch_hours) == (0, 0, 0)
    assert written.audit_batch_merges == 5      # ... while its sibling in the same table keeps its floor


def test_a_get_of_a_file_with_a_bare_toml_date_answers_rather_than_raising(tmp_path):
    """`load_config` accepts a bare TOML date/time/datetime anywhere without complaint (nothing in the schema
    uses one, so nothing refuses it either); `tomllib` hands it back as a real `datetime.date`, which `json.dumps`
    cannot serialize. GET must answer, not drop the connection with a traceback."""
    v, _, _ = viewer_for(tmp_path)
    config_path = tmp_path / ".meld.toml"
    config_path.write_text(config_path.read_text() + "\n[notes]\nlast_reviewed = 2026-09-01\n")
    resp = get(v, "/api/config")
    assert resp.status == 500
    assert "error" in body(resp) and "date" in body(resp)["error"].lower()


def _reject_nonstandard_json(token: str):
    raise ValueError(f"not strict JSON: {token}")


def test_a_get_of_a_file_with_a_bare_toml_nan_or_inf_answers_500_not_unparseable_json(tmp_path):
    """TOML float syntax allows a bare inf/nan/+nan/-inf literal anywhere; `json.dumps`'s default would
    silently emit the matching non-standard NaN/Infinity token (valid Python, not valid JSON) instead of
    raising, so `_read_config`'s `allow_nan=False` is what turns this into the same clean 500 the date case
    gets, rather than a 200 a browser's `JSON.parse` would choke on. The control check is the real pin: an
    ordinary, successful GET's body must itself survive a strict parse, proving the happy path never depended
    on the leniency this scoped change turns off.

    `nan` and `inf` are exercised one at a time rather than in one shared file: `json.dumps` aborts at the
    first non-compliant float it meets, so a file carrying both at once only ever proves the first of them
    (whichever key order puts first) triggers this -- the other would sit unreached, tested in appearance only.
    Each case gets its own 500 and, matching the sibling date test's own pin of "date", its own message
    naming the value that caused it -- "a regression that kept the 500 but swapped in an uninformative
    message" (the gap a prior round of review found) would fail either one.
    """
    v, _, _ = viewer_for(tmp_path)
    config_path = tmp_path / ".meld.toml"
    base = config_path.read_text()

    control = get(v, "/api/config")
    assert control.status == 200
    json.loads(control.body, parse_constant=_reject_nonstandard_json)   # raises if the body isn't strict JSON

    config_path.write_text(base + "\n[notes]\nspend_variance = nan\n")
    resp = get(v, "/api/config")
    assert resp.status == 500
    assert "nan" in body(resp)["error"].lower()

    config_path.write_text(base + "\n[notes]\nceiling = inf\n")
    resp = get(v, "/api/config")
    assert resp.status == 500
    assert "inf" in body(resp)["error"].lower()


def test_a_put_reports_a_render_that_cannot_reproduce_its_own_input_as_a_500(tmp_path, monkeypatch):
    """A render that cannot reproduce its own input is `dumps`'s bug, not the candidate's -- reported as one,
    with the fields that differ, and nothing is written."""
    v, _, _ = viewer_for(tmp_path)
    current = body(get(v, "/api/config"))["config"]
    before = (tmp_path / ".meld.toml").read_text()
    real = server.dumps
    monkeypatch.setattr(server, "dumps", lambda cfg: real(cfg).replace("large = 20", "large = 99"))
    resp = put(v, {"config": current})
    assert resp.status == 500
    assert body(resp)["differs"] == ["budget"]
    assert (tmp_path / ".meld.toml").read_text() == before


def test_the_config_route_answers_502_when_the_viewer_has_no_configuration_file(tmp_path):
    """`meld watch` always passes one, so this is the guard rather than a reachable state -- but it is the
    first line of the route and nothing exercised it, which is how a guard quietly stops guarding."""
    v, _, _ = viewer_for(tmp_path, config_path=None)
    for resp in (get(v, "/api/config"), raw_put(v, {"config": {"repo": {"name": "a/b"}}, "version": "x"})):
        assert resp.status == 502
        assert "without a configuration file" in body(resp)["error"]


def test_a_write_that_fails_answers_502_and_leaves_the_file_untouched(tmp_path, monkeypatch):
    """The last rung of the refusal ladder, and the only one that runs after every check has passed: a full
    disk or a read-only directory must come back as the 502 it is rather than a traceback. The stand-in
    below fails the way `write_atomic` actually fails on a full disk -- the sidecar written, the
    `os.replace` never reached -- because a stub that raised before touching anything would leave the
    "untouched" half of this test proving nothing. (The abandoned sidecar is not cleaned up; `atomic.py`
    is a shared helper with other callers and is out of this branch's scope.)"""
    v, _, _ = viewer_for(tmp_path)
    path = tmp_path / ".meld.toml"
    before = path.read_text()
    out = body(get(v, "/api/config"))
    current = out["config"]
    current["budget"] = {**current["budget"], "large": 24}
    # PROJECT_TOML's codex model is the deliberately-invalid "gpt-x" (see the sibling write test): without
    # this the 409 from config_checks would come first and the write would never be attempted at all.
    current["groups"]["codex"]["orchestrator"]["model"] = current["groups"]["codex"]["pm"]["model"] = "gpt-5.6-sol"

    def full_disk(target, text):
        target.with_suffix(target.suffix + ".tmp").write_text(text)     # as far as write_atomic gets
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(server, "write_atomic", full_disk)
    resp = put(v, {"config": current, "version": out["version"]})
    assert resp.status == 502
    assert "No space left on device" in body(resp)["error"] and str(path) in body(resp)["error"]
    assert path.read_text() == before                        # the target itself never moved
    monkeypatch.undo()
    assert put(v, {"config": current, "version": out["version"]}).status == 200   # the real writer still works
    assert load_config(path).budget["large"] == 24


def test_the_config_route_is_behind_the_gate_and_takes_only_get_and_put(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    assert get(v, "/api/config", token=None).status == 403
    assert get(v, "/api/config", host="evil.example:8765").status == 403
    assert get(v, "/api/config", method="POST").status == 405
    assert get(v, "/api/config").status == 200


def test_a_successful_put_drops_the_cached_state(tmp_path):
    """Which is a provable no-op today, and the point of pinning it is that it stays one deliberately. The
    numbers `/api/state` derives from the configuration come from `ctx.cfg`, what this process loaded at
    launch, and a write to the file cannot change them -- plan decision 6 makes that staleness the decision
    rather than a bug. This test pins the drop, not a change in the answer, and an earlier version of this
    comment claimed the second."""
    v, _, _ = viewer_for(tmp_path)
    assert get(v, "/api/state").status == 200 and v._state_cache is not None
    current = body(get(v, "/api/config"))["config"]
    current["budget"] = {**current.get("budget", {}), "large": 21}
    # See the sibling write test: PROJECT_TOML's codex model is deliberately-invalid "gpt-x", which config_checks
    # would otherwise 409 on regardless of this test's own change.
    current["groups"]["codex"]["orchestrator"]["model"] = current["groups"]["codex"]["pm"]["model"] = "gpt-5.6-sol"
    assert put(v, {"config": current}).status == 200
    assert v._state_cache is None


def test_serve_binds_an_os_assigned_loopback_port_and_answers_over_a_socket(tmp_path):
    """The one socket test: the wiring from bytes on a connection to `Viewer.handle` and back, including a
    streaming body and the silenced request log."""
    v, ctx, _ = viewer_for(tmp_path, alive=lambda pid: False, max_polls=2)     # the writer is confirmed gone: the stream ends
    home = ctx.store.home
    log = home / "logs" / "claude" / "pm-1.log"
    log.parent.mkdir(parents=True)
    log.write_text("hello\n")
    # Without a run record `record_for` answers None and `liveness` short-circuits to "unknown" without ever
    # consulting `alive` -- the same pattern the sibling in-process test above uses to make the writer "dead".
    write_record(role_run_path(home, "claude", "pm"),
                 {"pid": 7, "started_at": NOW, "proc_start": "T0", "log_path": str(log), "reason": "start", "launch_no": 1})
    httpd = serve(v, port=0)
    try:
        port = httpd.server_address[1]
        assert httpd.server_address[0] == "127.0.0.1" and port > 0 and v.port == port
        assert url(httpd, v.token) == f"http://127.0.0.1:{port}/?t={TOKEN}"
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        def fetch(target, headers=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("GET", target, headers=headers or {})
            resp = conn.getresponse()
            data = resp.read()
            conn.close()
            return resp, data

        resp, data = fetch(f"/api/streams?t={TOKEN}")
        assert resp.status == 200 and resp.getheader("Content-Type") == "application/json; charset=utf-8"
        assert json.loads(data)["streams"][0]["id"] == "claude/pm-1.log" and resp.getheader("Content-Length") == str(len(data))
        resp, data = fetch("/api/streams")
        assert (resp.status, data) == (403, b"")
        resp, data = fetch(f"/?t={TOKEN}")
        assert resp.status == 200 and data.lstrip().lower().startswith(b"<!doctype html>")
        resp, data = fetch(f"/api/events?stream=claude/pm-1.log&t={TOKEN}")
        assert resp.status == 200 and resp.getheader("Content-Type") == "text/event-stream; charset=utf-8"
        assert data.startswith(b"id: claude/pm-1.log=1\nevent: message\n")
        assert data.endswith(b'event: end\ndata: {"stream": "claude/pm-1.log"}\n\n')
        resp, data = fetch(f"/api/streams?t={TOKEN}", headers={"Host": f"evil.example:{port}"})
        assert (resp.status, data) == (403, b"")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_body_larger_than_the_cap_is_refused_at_the_transport(tmp_path):
    """A Content-Length above the cap is answered 413 without reading the body, like a malformed request line."""
    import http.client
    v, _, _ = viewer_for(tmp_path)
    httpd = serve(v, port=0)
    try:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.putrequest("POST", f"/api/action?t={TOKEN}")
        conn.putheader("Content-Length", str(server.MAX_BODY_BYTES + 1))
        conn.putheader("Content-Type", "application/json")
        conn.endheaders()
        resp = conn.getresponse()
        assert resp.status == 413
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_state_carries_the_thread_and_the_page_has_a_conversation_panel(tmp_path):
    """Plan 17: cross-agent communication on the page. /api/state carries the newest coordination comments as data
    (oldest first, appended by id in the browser), and the page has the panel, the filter and the party colours --
    all through textContent, never innerHTML."""
    from meld.viewer.server import THREAD_LAST
    v, ctx, _ = viewer_for(tmp_path)
    state = body(get(v, "/api/state"))
    thread = state["thread"]
    assert isinstance(thread, list) and thread and len(thread) <= THREAD_LAST
    assert [t["id"] for t in thread] == sorted(t["id"] for t in thread)
    assert {"id", "at", "party", "kind", "group", "verb", "ref", "qualifier", "text"} <= set(thread[0])
    html = get(v, "/").body.decode()
    assert 'id="conversation"' in html and 'id="turns"' in html and 'id="convo-filter"' in html and 'id="streams"' in html
    js = get(v, "/static/app.js").body.decode()
    assert "renderThread" in js and "state.thread" in js and "colorFor" in js and "innerHTML" not in js
    css = get(v, "/static/app.css").body.decode()
    assert '#conversation[data-filter="quiet"] li.housekeeping' in css


def test_state_carries_the_decisions_and_the_page_renders_them_as_cards(tmp_path):
    """Plan 18: everything waiting on the human is one card with its choices, through the page's one write path."""
    v, ctx, _ = viewer_for(tmp_path)
    state = body(get(v, "/api/state"))
    assert isinstance(state["decisions"], list)
    for d in state["decisions"]:
        assert {"id", "kind", "title", "summary", "choices"} <= set(d) and all({"label", "body", "needs_text"} <= set(c) for c in d["choices"])
    html = get(v, "/").body.decode()
    assert 'id="decisions"' in html and 'id="card"' in html
    js = get(v, "/static/app.js").body.decode()
    assert "renderDecisions" in js and "postAction" in js and "state.decisions" in js and "innerHTML" not in js


def test_state_carries_the_headline_and_the_page_shows_it(tmp_path):
    """Plan 19: one plain sentence under the header, composed on the server from the same board."""
    v, ctx, _ = viewer_for(tmp_path)
    state = body(get(v, "/api/state"))
    assert isinstance(state["headline"], str) and state["headline"].endswith(".") and "milestone" in state["headline"].lower()
    html = get(v, "/").body.decode()
    assert 'id="headline"' in html
    js = get(v, "/static/app.js").body.decode()
    assert "state.headline" in js and "innerHTML" not in js


def test_the_page_shows_narration_by_default_and_a_card_jumps_to_its_turn(tmp_path):
    """Plan 20: the transcript columns hide tool traffic until "show tool calls" is ticked, and a decision card's title
    scrolls the conversation to the comment it came from."""
    v, ctx, _ = viewer_for(tmp_path)
    html = get(v, "/").body.decode()
    assert 'id="show-tools"' in html
    js = get(v, "/static/app.js").body.decode()
    assert "showTurn" in js and 'classList.add("narration"' in js and "innerHTML" not in js
    css = get(v, "/static/app.css").body.decode()
    assert "#streams.narration li.tool_call" in css


def test_the_page_is_laid_out_as_header_decisions_conversation_work_and_a_tab_drawer(tmp_path):
    """Plan 21: the page reads top to bottom as what is happening, what needs you, what the agents say beside the work
    as lanes, and a tab row whose drawer holds the transcripts, pull requests, agreements, the bar and configuration."""
    v, ctx, _ = viewer_for(tmp_path)
    html = get(v, "/").body.decode()
    for marker in ('id="headline"', 'id="supervisors"', 'id="decisions"', 'id="conversation"', 'id="work"', 'class="kanban"',
                   'id="tabs"', 'id="drawer"', 'data-tab="streams"', 'data-tab="act"', 'data-tab="config"', 'id="bar"', 'id="config"'):
        assert marker in html, marker
    assert html.index('id="headline"') < html.index('id="decisions"') < html.index('id="conversation"') < html.index('id="work"') < html.index('id="tabs"')
    js = get(v, "/static/app.js").body.decode()
    assert "LANES" in js and "issueCard" in js and "openTab" in js and 'openTab("act")' in js and "innerHTML" not in js
    css = get(v, "/static/app.css").body.decode()
    assert "#issues.kanban" in css and "#drawer.closed" in css and "system-ui" in css


def test_the_page_is_guided_in_plain_words_and_follows_the_conversation(tmp_path):
    """Plan 22: the bar is a list of things a person can do, each explained before Run; the conversation filter is worded
    for a person and the list follows the newest turn; the board shows who is working on what, animated."""
    v, ctx, _ = viewer_for(tmp_path)
    html = get(v, "/").body.decode()
    for marker in ('id="explain"', 'id="newest"', "the conversation", "everything, including status reports", "decisions and your messages", 'for="action"'):
        assert marker in html, marker
    js = get(v, "/static/app.js").body.decode()
    for marker in ("INTENTS", "INTENT_FOR_ACTION", "followNewest", "following", "liveRuns", "prevLane", '" working"', '" moved"', "is building this now", "awaiting review"):
        assert marker in js, marker
    assert "innerHTML" not in js
    css = get(v, "/static/app.css").body.decode()
    for marker in (".icard.working", ".icard.moved", "#turns li.fresh", "@keyframes pulse", "#supervisors .agent.busy"):
        assert marker in css, marker
    state = body(get(v, "/api/state"))
    assert all("worker" in r for r in state["status"]["runs"])


def test_the_tag_form_offers_every_clis_models_and_sends_only_what_changed(tmp_path):
    """Plan 23: the one verb that had no form. The bar's tag intent draws model and effort choices from the catalogue
    the configuration form already serves, grouped by CLI, and a row's `tag` renders as a button at last."""
    v, ctx, _ = viewer_for(tmp_path)
    html = get(v, "/").body.decode()
    for marker in ('id="tag-model"', 'id="tag-effort"', 'id="tag-size"', 'id="tag-worker"', "leave as is"):
        assert marker in html, marker
    js = get(v, "/static/app.js").body.decode()
    assert '"tag": ["ref", "tag"]' in js and "fillTagChoices" in js and 'tag: "tag"' in js and "effortChoices(cli, id)" in js and "innerHTML" not in js
    assert "payload.model = model" in js and '["worker", "tag-worker"]' in js
    config = body(get(v, "/api/config"))
    assert set(config["models"]) >= {"claude", "codex"} and all("efforts" in spec for specs in config["models"].values() for spec in specs)


def test_transcripts_put_live_columns_first_and_hide_finished_ones_by_default(tmp_path):
    """Plan 24, from the live run: September's un-reaped worker runs filled the transcripts tab with dead columns. Live
    columns come first, dead ones are classed and hidden behind "show finished"."""
    v, ctx, _ = viewer_for(tmp_path)
    html = get(v, "/").body.decode()
    assert 'id="show-finished"' in html
    js = get(v, "/static/app.js").body.decode()
    assert 'classList.add("narration", "live-only")' in js and "rank[a.liveness.state]" in js and 'classList.toggle("dead"' in js and "innerHTML" not in js
    css = get(v, "/static/app.css").body.decode()
    assert "#streams.live-only .column.dead" in css
    assert "following: true" in js and "scrollColumns" in js and 'if (name === "streams") scrollColumns();' in js   # each column follows its newest line


def test_a_card_opens_a_tree_of_everything_that_touched_the_issue(tmp_path):
    """Plan 24, from the live run: which sub-agent is working on which task. A card's title opens a dialog whose tree
    shows the holder, each build run with its worker and model, the pull request with its verdicts, each review and
    audit run, and the thread turns about it, every run linking to its transcript column."""
    v, ctx, _ = viewer_for(tmp_path)
    html = get(v, "/").body.decode()
    assert 'id="task"' in html and 'id="task-tree"' in html and 'id="task-close"' in html
    js = get(v, "/static/app.js").body.decode()
    for marker in ("openTask", "showTranscript", "runLine", "treeItem", "col.meta = s", "lastStatus = s", "audited_by", "innerHTML"):
        assert (marker in js) == (marker != "innerHTML"), marker
    state = body(get(v, "/api/state"))
    assert all({"model", "effort", "outcome"} <= set(r) for r in state["status"]["runs"])


def test_the_page_draws_its_overview_as_inline_svg(tmp_path):
    """Plan 24: a live map of the groups, their roles and running workers; progress and budget bars; the task view as a
    graph. All drawn node by node with createElementNS -- no markup strings, no external library."""
    v, ctx, _ = viewer_for(tmp_path)
    html = get(v, "/").body.decode()
    for marker in ('id="map"', 'id="task-graph"', 'id="progress-fill"', 'id="budget-bars"', 'id="timeline"'):
        assert marker in html, marker
    js = get(v, "/static/app.js").body.decode()
    for marker in ("createElementNS", "drawMap", "drawBars", "drawTaskGraph", "state.processes", "drawEdge",
                   "drawTimeline", "drawRing", "runFraction", "arrowFor", "showTurn(t.id)", "clock12", "flowing", "animationDelay"):
        assert marker in js, marker
    assert "innerHTML" not in js and "http://www.w3.org/2000/svg" in js
    state = body(get(v, "/api/state"))
    assert set(state["processes"]) == set(state["supervisors"]) and all("roles" in v for v in state["processes"].values())


def test_the_work_lanes_show_the_flow(tmp_path):
    """A card that changed lane travels there (FLIP through the Web Animations API, from where it was to where it is), each
    lane carries a flow line drifting in the direction of work that races for a minute after a card arrives, and a card
    with a worker on it shows a bar of that run's elapsed time. Motion is off under prefers-reduced-motion."""
    v, ctx, _ = viewer_for(tmp_path)
    js = get(v, "/static/app.js").body.decode()
    for marker in ("flowline", "card.animate(", "getBoundingClientRect", "dataset.issue", "laneFlowUntil", "iprogress", "runFraction(r)"):
        assert marker in js, marker
    css = get(v, "/static/app.css").body.decode()
    for marker in (".flowline", ".flowline.flowing", "@keyframes flow", "@keyframes settle", ".icard .iprogress", "prefers-reduced-motion"):
        assert marker in css, marker


def test_the_form_adds_a_group_for_another_ai(tmp_path):
    """Plan 25: the configuration form is sections in plain words with one card per AI group, and a group for another
    AI (Gemini, Grok) is added from the adapters the server names, with that CLI's catalogue defaults, then saved through
    the same validated PUT as any other change."""
    from meld.adapters import ADAPTER_NAMES
    v, _, _ = viewer_for(tmp_path)
    out = body(get(v, "/api/config"))
    assert out["adapters"] == list(ADAPTER_NAMES) and "gemini" in out["models"] and "grok" in out["models"]
    js = get(v, "/static/app.js").body.decode()
    for marker in ("const WORDS", "GROUP_WORDS", "Add a group", "newGroupTemplate", "adapterNames", "gcard", "newGroups", '"Remove"', ".gcard.removed"):
        assert marker in js, marker
    css = get(v, "/static/app.css").body.decode()
    assert ".gcard" in css and ".gadd" in css and ".gcard.fresh" in css
    # the template the page builds, applied server-side: a gemini group with the catalogue's large model saves
    candidate = out["config"]
    for g in candidate["groups"].values():     # the fixture's own groups name a model the catalogue never had ("gpt-x")
        known = out["models"][g["cli"]][0]
        for role in ("orchestrator", "pm"):
            g[role] = {"model": known["id"], "effort": known["efforts"][0]}
    large = next(m for m in out["models"]["gemini"] if m["tier"] == "large")
    candidate["groups"]["gemini"] = {"cli": "gemini", "bot": "meld-gemini-bot", "token_env": "MELD_TOKEN_GEMINI", "auth": "pat",
                                     "concurrency": 8, "review_concurrency": 4,
                                     "orchestrator": {"model": large["id"], "effort": large["efforts"][-1]}, "pm": {"model": large["id"], "effort": large["efforts"][-1]}}
    resp = put(v, {"config": candidate, "version": out["version"]})
    assert resp.status == 200, resp.body
    text = (tmp_path / ".meld.toml").read_text()
    assert "[groups.gemini]" in text and 'cli = "gemini"' in text and f'model = "{large["id"]}"' in text
    # ... and removing it again (what the card's Remove does on Save) is the same PUT without the key
    again = body(get(v, "/api/config"))
    del again["config"]["groups"]["gemini"]
    assert put(v, {"config": again["config"], "version": again["version"]}).status == 200
    assert "[groups.gemini]" not in (tmp_path / ".meld.toml").read_text()


def test_every_reference_on_the_page_links_to_github(tmp_path):
    """Plan 26: the repository, the milestone, each issue and pull request number, each thread comment and each decision
    card link to the thing itself on GitHub, opened in a new tab with no referrer; without a repository they stay text."""
    v, _, _ = viewer_for(tmp_path)
    js = get(v, "/static/app.js").body.decode()
    for marker in ("function ghUrl", "function link(", "function refLink", "/milestone/${id}", "/issues/${id}", "#issuecomment-${id}",
                   'a.target = "_blank"', 'a.rel = "noopener noreferrer"', 'ghUrl("repo")', 'ghUrl("milestone", s.milestone)',
                   'ghUrl("issue", i.number)', 'ghUrl("issue", pr.number)', 'ghUrl("comment", t.id)', 'ghUrl("comment", d.comment_id)',
                   "if (!href) return text(parent, value);"):
        assert marker in js, marker
    assert "lastStatus = s;   // first" in js                    # the repository is known before anything links through it


def test_a_configured_group_that_is_not_running_says_what_it_still_needs(tmp_path):
    """Plan 27: the owner added two groups through the form and saw nothing. /api/state now carries `setup` per group --
    its CLI on PATH, its token in the environment, whether its supervisor runs -- and the header lists each group that is
    configured but not running with what this machine says it needs and the command that starts it."""
    v, _, _ = viewer_for(tmp_path, which=lambda binary: None, env={})
    state = body(get(v, "/api/state"))
    assert set(state["setup"]) == {"claude", "codex"} and state["config_error"] is None
    for g, s in state["setup"].items():
        assert s["running"] is False and s["cli_found"] is False and s["token_set"] is False and s["token_env"].startswith("MELD_TOKEN_")
    found = viewer_for(tmp_path, which=lambda binary: "/usr/local/bin/" + binary, env={"MELD_TOKEN_CLAUDE": "x", "MELD_TOKEN_CODEX": "y"})[0]
    assert all(s["cli_found"] and s["token_set"] for s in body(get(found, "/api/state"))["setup"].values())
    html, js = get(v, "/").body.decode(), get(v, "/static/app.js").body.decode()
    assert 'id="setup"' in html
    for marker in ("function renderSetup", "is configured but not running", "meld run ${g} --detach", "state.config_error", "install the ${s.cli} CLI"):
        assert marker in js, marker


def test_the_page_picks_up_a_group_saved_to_the_file_without_a_restart(tmp_path):
    """The form writes the file; the page used to keep the configuration it started with until a restart, so a group
    saved there was invisible. The state route re-reads the file when its modification time changes, keeps the last
    good configuration when the new file does not load, and says so."""
    import os
    v, _, _ = viewer_for(tmp_path, state_ttl=0, which=lambda binary: None, env={})
    path = tmp_path / ".meld.toml"
    assert set(body(get(v, "/api/state"))["supervisors"]) == {"claude", "codex"}
    later = path.stat().st_mtime + 2
    path.write_text(path.read_text() + "\n[groups.gemini]\ncli = \"gemini\"\nbot = \"meld-gemini-bot\"\ntoken_env = \"MELD_TOKEN_GEMINI\"\n"
                    "[groups.gemini.orchestrator]\nmodel = \"gemini-3-pro-preview\"\neffort = \"default\"\n"
                    "[groups.gemini.pm]\nmodel = \"gemini-3-pro-preview\"\neffort = \"default\"\n")
    os.utime(path, (later, later))
    state = body(get(v, "/api/state"))
    assert "gemini" in state["supervisors"] and state["setup"]["gemini"]["cli"] == "gemini" and state["setup"]["gemini"]["running"] is False
    path.write_text("this is not toml = [")
    os.utime(path, (later + 2, later + 2))
    broken = body(get(v, "/api/state"))
    assert "gemini" in broken["supervisors"] and broken["config_error"] and ".meld.toml" in broken["config_error"]


def test_a_group_can_carry_its_own_environment_and_cli_settings_through_the_form(tmp_path):
    """Plan 28: each group card shows two boxes, `env` and `cli_config` (a pair per line), and "Add a group" takes an
    optional model server: a Codex group gets the -c overrides that name a custom provider, a Claude group the base URL.
    The server parses the tables, writes them back with quoted dotted keys, and reads them again."""
    v, _, _ = viewer_for(tmp_path)
    js = get(v, "/static/app.js").body.decode()
    for marker in ("function mapField", "function parseMap", 'dataset.kind = "map"', "function endpointSettings", "model_providers.${name}.base_url",
                   "ANTHROPIC_BASE_URL", "model server URL", "meld add-model"):
        assert marker in js, marker
    out = body(get(v, "/api/config"))
    candidate = out["config"]
    for g in candidate["groups"].values():
        known = out["models"][g["cli"]][0]
        for role in ("orchestrator", "pm"):
            g[role] = {"model": known["id"], "effort": known["efforts"][0]}
    candidate["groups"]["codex"]["env"] = {"OPENAI_API_KEY": "ollama"}
    candidate["groups"]["codex"]["cli_config"] = {"model_provider": "ollama", "model_providers.ollama.base_url": "http://server:11434/v1", "disable_response_storage": True}
    resp = put(v, {"config": candidate, "version": out["version"]})
    assert resp.status == 200, resp.body
    written = (tmp_path / ".meld.toml").read_text()
    assert "[groups.codex.env]" in written and 'OPENAI_API_KEY = "ollama"' in written
    assert '"model_providers.ollama.base_url" = "http://server:11434/v1"' in written and "disable_response_storage = true" in written
    again = body(get(v, "/api/config"))["config"]["groups"]["codex"]
    assert again["env"] == {"OPENAI_API_KEY": "ollama"} and again["cli_config"]["disable_response_storage"] is True


def test_the_cards_draw_each_issues_journey_and_the_bar_says_what_is_left(tmp_path):
    """Plan 29: /api/state carries `journeys` and `progress`; a card draws the seven steps and prints now and next."""
    v, _, _ = viewer_for(tmp_path)
    state = body(get(v, "/api/state"))
    assert set(state["journeys"]) == {str(i["number"]) for i in state["status"]["issues"]} or set(state["journeys"]) == {i["number"] for i in state["status"]["issues"]}
    one = next(iter(state["journeys"].values()))
    assert [s["name"] for s in one["steps"]] == ["claimed", "built", "pull request", "reviewed", "merged", "audited", "closed"] and "now" in one
    assert isinstance(state["progress"], str)
    js, css = get(v, "/static/app.js").body.decode(), get(v, "/static/app.css").body.decode()
    for marker in ("lastJourneys", 'className = "journey"', "jstep ${step.state}", "state.progress", '"jnow"'):
        assert marker in js, marker
    assert ".icard .jstep.now" in css and ".icard .jstep.done" in css


def test_the_viewers_token_survives_a_restart(tmp_path):
    """The owner's open tab went blank at every restart during the second live run: the token now lives at
    $MELD_HOME/state/viewer-token (0600) and a restart prints the same URL; deleting the file mints a new one."""
    import stat
    from meld.viewer.server import stable_token
    home = tmp_path / "home"
    a = stable_token(home)
    assert len(a) >= 32 and stable_token(home) == a
    path = home / "state" / "viewer-token"
    assert path.read_text() == a and stat.S_IMODE(path.stat().st_mode) == 0o600
    path.write_text("short")                           # a damaged file is replaced, never used
    b = stable_token(home)
    assert b != "short" and len(b) >= 32 and path.read_text() == b
    path.unlink()
    assert stable_token(home) not in (a, b)
