import http.client
import json
import os
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from meld.context import load_context
from meld.github import GitHubError
from meld.runners import freeze_path, role_run_path, supervisor_path, write_record
from meld.state import StateStore
from meld.viewer import server
from meld.viewer.server import LOOPBACK_HOSTS, Request, Response, Viewer, new_token, serve, url
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.factories import mk_run
from tests.fakeadapter import FakeAdapter
from tests.fakegh import FakeGh

NOW = "2026-09-18T12:00:00Z"
TOKEN = "t0k3n-fixed-for-tests"


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def viewer_for(tmp_path, **overrides):
    """The human's context over the recorded board (PROJECT_TOML configures groups `claude` and `codex`), and a
    Viewer with every seam injected."""
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    ctx = load_context(tmp_path / ".meld.toml", env=env_for(tmp_path, group=None), run=fake,
                       adapter_for=lambda name: FakeAdapter())
    kw = dict(token=TOKEN, port=8765, now=lambda: NOW, clock=Clock(), sleep=lambda s: None, alive=lambda pid: True,
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
    assert get(v, "/api/action", method="POST").status == 404       # plan 8c
    assert get(v, "/api/config", method="PUT").status == 404        # plan 8d
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


def test_api_events_streams_a_transcript_and_resumes_from_last_event_id(tmp_path):
    v, ctx, _ = viewer_for(tmp_path, alive=lambda pid: False)     # the writer is confirmed gone: the stream ends
    home = ctx.store.home
    log = home / "logs" / "claude" / "pm-1.log"
    log.parent.mkdir(parents=True)
    log.write_text("one\ntwo\n")
    write_record(role_run_path(home, "claude", "pm"),
                 {"pid": 7, "started_at": NOW, "proc_start": "T0", "log_path": str(log), "reason": "start", "launch_no": 1})

    resp = get(v, "/api/events?stream=claude/pm-1.log")
    assert resp.status == 200 and resp.content_type == "text/event-stream; charset=utf-8" and resp.streaming
    text = b"".join(resp.body).decode()
    assert text.startswith("id: 1\nevent: message\n") and '"text": "two"' in text and text.endswith("event: end\ndata: {}\n\n")
    assert 'event: run_finished' in text and '"synthesized": true' in text

    resumed = b"".join(get(v, "/api/events?stream=claude/pm-1.log", headers={"Last-Event-ID": "1"}).body).decode()
    assert "id: 1\n" not in resumed and resumed.startswith("id: 2\nevent: message\n")
    by_query = b"".join(get(v, "/api/events?stream=claude/pm-1.log&after=2").body).decode()
    # Controller ruling R5/R7: Task 4's review made the synthesized `run_finished` frame carry no `id:` line (it
    # is not a file position), so the resumed-past-everything case starts at the bare event line, not "id: 3\n".
    assert "event: message" not in by_query and by_query.startswith("event: run_finished\n")
    junk = b"".join(get(v, "/api/events?stream=claude/pm-1.log", headers={"Last-Event-ID": "seven"}).body).decode()
    assert junk.startswith("id: 1\n")                                    # not an integer: from the start


@pytest.mark.parametrize("stream", ["", "claude/nope.log", "../.meld.toml", "ghost/pm-1.log", "claude"])
def test_api_events_answers_404_for_a_stream_that_is_not_a_log_of_a_configured_group(tmp_path, stream):
    v, ctx, _ = viewer_for(tmp_path)
    (ctx.store.home / "logs" / "ghost").mkdir(parents=True)
    (ctx.store.home / "logs" / "ghost" / "pm-1.log").write_text("x\n")   # exists on disk, but `ghost` is no group
    resp = get(v, f"/api/events?stream={stream}")
    assert resp.status == 404 and "error" in body(resp)


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
        assert "http://" not in text and "https://" not in text and "//cdn" not in text and "@import" not in text
    assert "innerHTML" not in js and "textContent" in js and "EventSource" in js
    assert 'className = "badge' not in js
    assert "MAX_SOURCES" in js and "classList" in js
    for route in ("/api/state", "/api/streams", "/api/events"):
        assert route in js
    assert 'name="referrer" content="no-referrer"' in html


def test_serve_binds_an_os_assigned_loopback_port_and_answers_over_a_socket(tmp_path):
    """The one socket test: the wiring from bytes on a connection to `Viewer.handle` and back, including a
    streaming body and the silenced request log."""
    v, ctx, _ = viewer_for(tmp_path, alive=lambda pid: False)     # the writer is confirmed gone: the stream ends
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
        assert data.startswith(b"id: 1\nevent: message\n") and data.endswith(b"event: end\ndata: {}\n\n")
        resp, data = fetch(f"/api/streams?t={TOKEN}", headers={"Host": f"evil.example:{port}"})
        assert (resp.status, data) == (403, b"")
    finally:
        httpd.shutdown()
        httpd.server_close()
