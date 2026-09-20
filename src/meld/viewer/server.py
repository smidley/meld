"""The loopback viewer's server: the gate in front of every request, and the routes behind it (spec section 22).

Every route is a method taking a plain `Request` and returning a plain `Response`, so the whole surface is driven
in process by the tests; `_Handler` and `serve` (at the end of this module) are the only code that touches a socket.
"""
from __future__ import annotations

import hmac
import json
import secrets
import shutil
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from meld.actions import Refused
from meld.adapters import UnknownAdapter
from meld.appauth import AppAuthError
from meld.assemble import BoardError
from meld.config import ConfigError
from meld.dispatch import DispatchError
from meld.github import GitHubError
from meld.gitops import GitError
from meld.posting import PostError
from meld.prompts import ROLES
from meld.results import ResultsError
from meld.runners import freeze_path, read_record, supervisor_path
from meld.state import liveness, pid_alive, proc_start_of
from meld.status import build_status
from meld.transcript import total_bytes
from meld.viewer import actions as va
from meld.viewer import streams as st

#: The only hosts a request may name (with the served port): what stops a page in the browser reaching the API
#: through a DNS name rebound to 127.0.0.1. The server itself binds 127.0.0.1 alone.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
#: How long one `/api/state` answer is reused: `build_status` is several paginated `gh` calls, and a page polling
#: from two tabs would otherwise spend a real share of the token's rate limit on the same answer.
STATE_TTL_SECONDS = 5.0
#: The most a request body may be. A page's action is a few hundred bytes; this is the transport saying no to
#: anything that is not one, before the gate and before anything is read.
MAX_BODY_BYTES = 1 << 20
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"))
_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime(_ISO_FMT)


def new_token() -> str:
    """A 32-byte URL-safe token, minted once per `meld watch`; it lives in the process and in the printed URL."""
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]          # names lower-cased
    body: bytes = b""

    @classmethod
    def parse(cls, method: str, target: str, headers: Mapping[str, str], body: bytes = b"") -> Request:
        parts = urlsplit(target)
        return cls(method.upper(), parts.path or "/", parse_qs(parts.query, keep_blank_values=True),
                   {k.lower(): v for k, v in headers.items()}, body)

    def first(self, name: str) -> str:
        return (self.query.get(name) or [""])[0]


@dataclass
class Response:
    status: int
    body: bytes | Iterator[bytes] = b""
    content_type: str = "application/json; charset=utf-8"
    headers: tuple[tuple[str, str], ...] = SECURITY_HEADERS

    @property
    def streaming(self) -> bool:
        return not isinstance(self.body, (bytes, bytearray))


def _json(status: int, payload: object) -> Response:
    return Response(status, json.dumps(payload, ensure_ascii=False).encode())


def _empty(status: int) -> Response:
    return Response(status, b"", "text/plain; charset=utf-8")


def _static(name: str) -> str:
    return resources.files("meld.viewer").joinpath("static", name).read_text(encoding="utf-8")


class Viewer:
    """One `meld watch`: its token, its port, and the routes. Reads, and posts the one human verb `/api/action`
    carries; holds no legality logic."""

    ROUTES: dict[str, str] = {"/": "page", "/static/app.css": "asset_css", "/static/app.js": "asset_js",
                              "/api/state": "api_state", "/api/streams": "api_streams", "/api/events": "api_events",
                              "/api/action": "api_action"}
    #: The one route that writes is the one route that is not a GET.
    METHODS: dict[str, str] = {"/api/action": "POST"}

    def __init__(self, ctx, *, token: str, port: int, now: Callable[[], str] = _iso_now, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, alive: Callable[[int], bool] = pid_alive,
                 start_of: Callable[[int], str] = proc_start_of, disk_usage: Callable[[Path], object] = shutil.disk_usage,
                 state_ttl: float = STATE_TTL_SECONDS, interval: float = 1.0, check_every: int = st.CHECK_EVERY,
                 max_polls: int | None = None) -> None:
        self.ctx = ctx
        self.token = token
        self.port = port
        self.now, self.clock, self.sleep = now, clock, sleep
        self.alive, self.start_of, self.disk_usage = alive, start_of, disk_usage
        self.state_ttl, self.interval, self.check_every, self.max_polls = state_ttl, interval, check_every, max_polls
        self._state_cache: tuple[float, Response] | None = None
        self._lock = threading.Lock()

    # ---- the gate ---------------------------------------------------------------------------------------

    def admitted(self, req: Request) -> Response | None:
        """403 with an empty body for a request that is not ours: no token, a wrong one, or a Host that is not
        this loopback server at its port. The token compare is constant-time; nothing else about the refusal
        distinguishes the cases, on purpose."""
        given = req.first("t")
        if not given or not hmac.compare_digest(given.encode(), self.token.encode()):
            return _empty(403)
        if req.headers.get("host", "") not in {f"{h}:{self.port}" for h in LOOPBACK_HOSTS}:
            return _empty(403)
        return None

    def handle(self, req: Request) -> Response:
        refused = self.admitted(req)
        if refused is not None:
            return refused
        name = self.ROUTES.get(req.path)
        if name is None:
            return _json(404, {"error": f"no route {req.path}"})
        if req.method != self.METHODS.get(req.path, "GET"):
            return _json(405, {"error": f"{req.method} is not allowed on {req.path}"})
        return getattr(self, name)(req)

    # ---- routes -----------------------------------------------------------------------------------------

    def page(self, req: Request) -> Response:
        # The stylesheet and script are fetched through the same gate as everything else, so the page carries the
        # token in their URLs; it is already in the URL the person pasted, so this discloses nothing new.
        return Response(200, _static("app.html").replace("__TOKEN__", self.token).encode(), "text/html; charset=utf-8")

    def asset_css(self, req: Request) -> Response:
        return Response(200, _static("app.css").encode(), "text/css; charset=utf-8")

    def asset_js(self, req: Request) -> Response:
        return Response(200, _static("app.js").encode(), "text/javascript; charset=utf-8")

    def api_state(self, req: Request) -> Response:
        with self._lock:                                  # single-flight: one build at a time, shared by every tab
            cached = self._state_cache
            if cached is not None and self.clock() - cached[0] < self.state_ttl:
                return cached[1]
            resp = self._build_state()
            if resp.status == 200:
                self._state_cache = (self.clock(), resp)
            return resp

    def _build_state(self) -> Response:
        home, cfg = self.ctx.store.home, self.ctx.cfg
        now = datetime.strptime(self.now(), _ISO_FMT).replace(tzinfo=timezone.utc)
        try:
            status, _ = build_status(self.ctx, now=now)
        except (GitHubError, BoardError, OSError) as e:
            # OSError is here for `gh` itself: a binary that is missing or mid-upgrade raises FileNotFoundError
            # out of `subprocess`, which `github._request` does not convert, and unhandled it escapes `handle`,
            # drops the connection and prints a traceback every ten seconds. 502 like the others, never cached.
            return _json(502, {"error": str(e)})
        supervisors = {g: liveness(read_record(supervisor_path(home, g)), alive=self.alive, start_of=self.start_of) for g in cfg.groups}
        frozen = {g: {r: read_record(freeze_path(home, g, r)) for r in ROLES} for g in cfg.groups}
        try:
            free_mb: int | None = self.disk_usage(home).free // (1024 * 1024)  # type: ignore[attr-defined]
        except OSError:
            free_mb = None
        return _json(200, {"status": status, "supervisors": supervisors, "frozen": frozen,
                           "disk": {"free_mb": free_mb, "warn_free_mb": cfg.logs_warn_free_mb},
                           "logs": {"total_mb": total_bytes(home / "logs") // (1024 * 1024), "warn_total_mb": cfg.logs_warn_total_mb},
                           "generated_at": self.now()})

    def api_streams(self, req: Request) -> Response:
        return _json(200, {"streams": st.discover(self.ctx.cfg, self.ctx.store.home, alive=self.alive, start_of=self.start_of)})

    def _source(self, stream: str) -> st.Source | None:
        """One transcript's reader, or None when `stream` names no log of a configured group. An adapter the
        configuration names but this build does not have raises `UnknownAdapter`, which the caller decides about:
        a stream the person asked for by name is a 502, one that merely turned up in `discover` is skipped."""
        home, cfg = self.ctx.store.home, self.ctx.cfg
        path = st.stream_path(home / "logs", stream)
        group_cfg = cfg.groups.get(stream.partition("/")[0])
        if path is None or group_cfg is None:
            return None
        parse = self.ctx.adapter_for(group_cfg.cli).parse_events
        record = st.record_for(cfg, home, path)
        return st.Source(stream=stream, path=path, parse=parse,
                         writer=lambda: liveness(record, alive=self.alive, start_of=self.start_of))

    def api_events(self, req: Request) -> Response:
        """Every transcript, or the ones `stream=` names, as one event stream (spec 22)."""
        wanted = [s for s in req.query.get("stream", []) if s]
        for stream in wanted:
            try:
                if self._source(stream) is None:
                    return _json(404, {"error": f"no transcript {stream!r} of a configured group"})
            except UnknownAdapter as e:
                return _json(502, {"error": str(e)})

        def sources() -> list[st.Source]:
            # Re-read on every refresh, so a role that relaunched into a new log joins the connection and the
            # record behind each writer check is the current one.
            names = wanted or [s["id"] for s in st.discover(self.ctx.cfg, self.ctx.store.home,
                                                            alive=self.alive, start_of=self.start_of)]
            out = []
            for stream in names:
                try:
                    source = self._source(stream)
                except UnknownAdapter:
                    continue                  # a group whose CLI this build cannot translate contributes nothing
                if source is not None:
                    out.append(source)
            return out

        body = st.events(sources, now=self.now, sleep=self.sleep, interval=self.interval,
                         cursor=req.headers.get("last-event-id") or req.first("after"),
                         check_every=self.check_every, max_polls=self.max_polls)
        return Response(200, body, "text/event-stream; charset=utf-8", SECURITY_HEADERS + (("Connection", "close"),))

    def api_action(self, req: Request) -> Response:
        """One human verb, run by the same function `meld` runs (spec 22).

        The viewer decides nothing here: it coerces arguments, calls, and reports. A `Refused` comes back whole
        -- the verb, the target, and the rule that named it -- because a person reading a refusal needs the rule,
        not a summary of it. A human's refusal posts no PROTOCOL-VIOLATION (`actions.refuse` skips that for a
        human), so a button pressed in error leaves nothing on the board.
        """
        try:
            body = json.loads(req.body or b"")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return _json(400, {"error": f"the request body is not JSON: {e}"})
        if not isinstance(body, dict):
            return _json(400, {"error": "the request body must be a JSON object"})
        if body.get("party") != "human":
            # Spec 22: the page may post as no other party. This is only the page's own JSON saying so, though --
            # the check right below is what holds it to that against the party this process actually is.
            return _json(400, {"error": f"the viewer acts as the human party only, not {body.get('party')!r}"})
        if not self.ctx.actor().is_human:
            # Defence in depth behind `meld watch`'s own refusal to start as anything but the human (cli.py):
            # the body's "party": "human" above is the page's claim, and this is the only place that can check
            # it against the party this process actually is. Without this, a page served from a `MELD_GROUP`
            # environment would post -- and be refused -- as that group, PROTOCOL-VIOLATION included.
            return _json(400, {"error": f"this viewer runs as {self.ctx.actor().party.label}, not the human party"})
        verb = body.get("verb")
        run = va.VERBS.get(verb) if isinstance(verb, str) else None
        if run is None:
            return _json(400, {"error": f"unknown verb {verb!r}; the page offers: {', '.join(sorted(va.VERBS))}"})
        try:
            result = run(self.ctx, body)
        except va.BadRequest as e:
            return _json(400, {"error": str(e)})
        except Refused as e:
            return _json(409, {"refused": str(e), "verb": e.refusal.verb.value, "target": e.refusal.target,
                               "rule": e.refusal.rule, "violation_posted": e.violation_posted})
        except PostError as e:
            # A usage error, exactly as its own docstring says, so 400 -- the same answer as `BadRequest` above:
            # a verb the party may not post, a framework-only or command verb, a missing ref, a body the grammar
            # rejects. Every board-state case in `posting.post` goes through `_refuse` and arrives as `Refused`
            # instead, so 409 stays the one status carrying a refusal (spec 22).
            return _json(400, {"error": str(e)})
        except (GitHubError, BoardError, DispatchError, GitError, ResultsError, ConfigError, AppAuthError,
                UnknownAdapter, OSError) as e:
            return _json(502, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            # A long-lived local tool: an unhandled exception here drops the connection and prints a traceback in
            # the person's terminal every time they press a button. A rendered 500 is strictly better, and
            # `_StopRequested`/`KeyboardInterrupt` are BaseException, so a stop still propagates.
            return _json(500, {"error": f"{type(e).__name__}: {e}"})
        with self._lock:
            self._state_cache = None      # the board has just moved; the next /api/state rebuilds rather than lying
        return _json(200, {"ok": True, "verb": verb, "result": result})


class _Handler(BaseHTTPRequestHandler):
    """Bytes on a connection in, `Viewer.handle` out. HTTP/1.0 on purpose: every response closes its connection,
    so a streaming body needs no chunked encoding and an SSE client simply reconnects when the stream ends."""

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            # Before the gate, like a malformed request line: nothing is read, so a hostile Content-Length
            # cannot make this process wait on a body it will never accept.
            self.send_error(413, "request body too large")
            return
        resp = self.server.viewer.handle(  # type: ignore[attr-defined]
            Request.parse(self.command, self.path, self.headers, self.rfile.read(length) if length else b""))
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.content_type)
        for name, value in resp.headers:
            self.send_header(name, value)
        if not resp.streaming:
            self.send_header("Content-Length", str(len(resp.body)))
        self.end_headers()
        try:
            if resp.streaming:
                for chunk in resp.body:
                    self.wfile.write(chunk)
                    self.wfile.flush()
            else:
                self.wfile.write(resp.body)
        except (BrokenPipeError, ConnectionResetError):
            pass                                        # the tab was closed; nothing to do

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Silenced: the default prints the request line, token included, to stderr."""


def serve(viewer: Viewer, *, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Bind the viewer to loopback -- `port` 0 lets the OS choose -- and tell it the port it got, which its
    `Host` check needs. The caller runs `serve_forever`."""
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.daemon_threads = True
    httpd.viewer = viewer  # type: ignore[attr-defined]
    viewer.port = httpd.server_address[1]
    return httpd


def url(httpd: ThreadingHTTPServer, token: str) -> str:
    return f"http://127.0.0.1:{httpd.server_address[1]}/?t={token}"
