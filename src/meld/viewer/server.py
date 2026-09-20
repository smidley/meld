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

from meld.adapters import UnknownAdapter
from meld.assemble import BoardError
from meld.github import GitHubError
from meld.prompts import ROLES
from meld.runners import freeze_path, read_record, supervisor_path
from meld.state import liveness, pid_alive, proc_start_of
from meld.status import build_status
from meld.transcript import total_bytes
from meld.viewer import streams as st

#: The only hosts a request may name (with the served port): what stops a page in the browser reaching the API
#: through a DNS name rebound to 127.0.0.1. The server itself binds 127.0.0.1 alone.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
#: How long one `/api/state` answer is reused: `build_status` is several paginated `gh` calls, and a page polling
#: from two tabs would otherwise spend a real share of the token's rate limit on the same answer.
STATE_TTL_SECONDS = 5.0
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

    @classmethod
    def parse(cls, method: str, target: str, headers: Mapping[str, str]) -> Request:
        parts = urlsplit(target)
        return cls(method.upper(), parts.path or "/", parse_qs(parts.query, keep_blank_values=True),
                   {k.lower(): v for k, v in headers.items()})

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


def _int(text: str) -> int:
    try:
        return max(0, int(text))
    except (TypeError, ValueError):
        return 0


def _static(name: str) -> str:
    return resources.files("meld.viewer").joinpath("static", name).read_text(encoding="utf-8")


class Viewer:
    """One `meld watch`: its token, its port, and the routes. Reads only; posts nothing; holds no legality logic."""

    ROUTES: dict[str, str] = {"/": "page", "/static/app.css": "asset_css", "/static/app.js": "asset_js",
                              "/api/state": "api_state", "/api/streams": "api_streams", "/api/events": "api_events"}

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
        if req.method != "GET":
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

    def api_events(self, req: Request) -> Response:
        home, cfg = self.ctx.store.home, self.ctx.cfg
        stream = req.first("stream")
        path = st.stream_path(home / "logs", stream)
        group_cfg = cfg.groups.get(stream.partition("/")[0])
        if path is None or group_cfg is None:
            return _json(404, {"error": f"no transcript {stream!r} of a configured group"})
        try:
            parse = self.ctx.adapter_for(group_cfg.cli).parse_events
        except UnknownAdapter as e:
            return _json(502, {"error": str(e)})
        record = st.record_for(cfg, home, path)
        after = _int(req.headers.get("last-event-id") or req.first("after"))
        body = st.events(path, parse, after=after, now=self.now, sleep=self.sleep, interval=self.interval,
                         check_every=self.check_every, max_polls=self.max_polls,
                         writer=lambda: liveness(record, alive=self.alive, start_of=self.start_of))
        return Response(200, body, "text/event-stream; charset=utf-8", SECURITY_HEADERS + (("Connection", "close"),))


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
        resp = self.server.viewer.handle(Request.parse(self.command, self.path, self.headers))  # type: ignore[attr-defined]
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
