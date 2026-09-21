"""The loopback viewer's server: the gate in front of every request, and the routes behind it (spec section 22).

Every route is a method taking a plain `Request` and returning a plain `Response`, so the whole surface is driven
in process by the tests; `_Handler` and `serve` (at the end of this module) are the only code that touches a socket.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import shutil
import threading
import time
import tomllib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from meld.actions import Refused
from meld.adapters import UnknownAdapter
from meld.appauth import AppAuthError
from meld.assemble import BoardError
from meld.atomic import write_atomic
from meld.config import Config, ConfigError, dotted_key, dumps, unknown_keys
from meld.dispatch import DispatchError
from meld.doctor import config_checks
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


def _json(status: int, payload: object, *, allow_nan: bool = True) -> Response:
    return Response(status, json.dumps(payload, ensure_ascii=False, allow_nan=allow_nan).encode())


def _empty(status: int) -> Response:
    return Response(status, b"", "text/plain; charset=utf-8")


def _static(name: str) -> str:
    return resources.files("meld.viewer").joinpath("static", name).read_text(encoding="utf-8")


def config_version(text: str) -> str:
    """A version tag for the `.meld.toml` a `GET` showed, so a `PUT` can say which file it was editing.

    A digest of the content rather than `st_mtime_ns`, for three reasons. It answers the actual question --
    "are these still the bytes I was shown?" -- so a hand edit that rewrites the file to the same content is
    not a collision, where every mtime scheme would call it one. It has no filesystem-resolution floor: two
    saves inside one timestamp tick are distinct here and indistinguishable there, and `write_atomic`'s
    `os.replace` of a fresh temp file is exactly the shape that makes near-simultaneous writes plausible.
    And it keeps the payload free of wall-clock: a test can build one from a string, with no clock to inject
    and no filesystem timestamp to depend on.

    Taken over the text as the route reads it, not the raw bytes, so both sides of the comparison decode
    the same way and the route's encoding stays the one question it already was.
    """
    return hashlib.sha256(text.encode()).hexdigest()


def _canonical_config(parsed: dict) -> tuple[dict, str | None]:
    """What `GET /api/config` shows -- every field the schema defines, at its effective (default-filled) value,
    plus any key `unknown_keys` names, carried over verbatim from the raw parse (spec 22) -- and the parser's
    own complaint when the file was too broken to canonicalise, else `None`.

    `dumps` already writes every field the schema defines, defaults included, and says why: "a form that shows
    a field and then does not write it is lying... a round trip is only exact if nothing is omitted." The same
    logic binds the read. A fresh `meld setup` file has no `[budget]`/`[polling]`/`[runners]`/`[timeouts]`/
    `[logs]`/`[viewer]` table at all -- nothing is wrong with it, `Config.from_dict` fills every one of those
    from its own defaults -- but the raw parse alone cannot show a field that was never written, so a bare
    file and "the form offers the budgets, the timeouts, the polling and runner intervals..." (spec 22)
    contradicted each other until this canonicalises before showing.

    Canonicalising alone would break the *other* half of spec 22 -- an unknown key refused by name on `PUT` --
    by silently dropping it from `GET` first, unseen. So every path `unknown_keys(parsed)` names is copied back
    in from the raw parse afterward, verbatim, creating whatever intermediate tables it needs. It is walked as
    the tuple `unknown_keys` returns, never re-split from a dotted string: a quoted TOML key may itself contain
    a `.` (`[review]` with `"a.b" = 1`), and splitting that one made this walk answer 500 `KeyError: 'a'`.

    A file too broken for `Config.from_dict` to parse at all (the same exception family the `PUT` path already
    catches around the same call) falls back to the raw parse whole, so it can still be seen -- and fixed --
    rather than turning a bad hand-edit into a `GET` that cannot answer. The complaint comes back with it:
    the fallback is otherwise invisible in the body, and a person would see only a sparser-than-usual form
    and then have every save refused by a parser they were never shown.

    Only that one call is caught. Rendering the `Config` and reading it back is ours, not the file's, and
    `tomllib.TOMLDecodeError` is a subclass of `ValueError`: catching it here would turn a `dumps` that emits
    unparseable TOML into a silent fallback to the raw parse, while `PUT` reported the very same bug honestly
    as a 500. It is left to propagate to the route's catch-all, which says so.
    """
    try:
        cfg = Config.from_dict(parsed)
    except (ValueError, AttributeError, TypeError, OverflowError) as e:
        return parsed, f"{type(e).__name__}: {e}"
    canonical = tomllib.loads(dumps(cfg))
    for parts in unknown_keys(parsed):
        src = parsed
        for part in parts[:-1]:
            src = src[part]
        dst = canonical
        for part in parts[:-1]:
            dst = dst.setdefault(part, {})
        dst[parts[-1]] = src[parts[-1]]
    return canonical, None


class Viewer:
    """One `meld watch`: its token, its port, and the routes. Reads, and posts the one human verb `/api/action`
    carries; holds no legality logic."""

    ROUTES: dict[str, str] = {"/": "page", "/static/app.css": "asset_css", "/static/app.js": "asset_js",
                              "/api/state": "api_state", "/api/streams": "api_streams", "/api/events": "api_events",
                              "/api/action": "api_action", "/api/config": "api_config"}
    #: The routes that are not plain GETs. `/api/config` is the one route that answers two methods.
    METHODS: dict[str, str | tuple[str, ...]] = {"/api/action": "POST", "/api/config": ("GET", "PUT")}

    def __init__(self, ctx, *, token: str, port: int, config_path: Path | None = None,
                 now: Callable[[], str] = _iso_now, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, alive: Callable[[int], bool] = pid_alive,
                 start_of: Callable[[int], str] = proc_start_of, disk_usage: Callable[[Path], object] = shutil.disk_usage,
                 state_ttl: float = STATE_TTL_SECONDS, interval: float = 1.0, check_every: int = st.CHECK_EVERY,
                 max_polls: int | None = None) -> None:
        self.ctx = ctx
        self.token = token
        self.port = port
        self.config_path = config_path
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
        allowed = self.METHODS.get(req.path, "GET")
        if req.method not in ({allowed} if isinstance(allowed, str) else set(allowed)):
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
            # the person's terminal every time they press a button. A rendered 500 is strictly better.
            # KeyboardInterrupt is BaseException, not Exception, so it still propagates past this; _StopRequested
            # (runners.py, raised only by the supervisor's own signal handler) is an Exception and would be
            # caught here like any other -- harmless only because meld watch never installs that handler.
            return _json(500, {"error": f"{type(e).__name__}: {e}"})
        with self._lock:
            self._state_cache = None      # the board has just moved; the next /api/state rebuilds rather than lying
        return _json(200, {"ok": True, "verb": verb, "result": result})

    #: What a write does not do. Said in the answer because it is the question a person asks next -- and
    #: phrased to presume no save, because the page paints this line the moment the panel opens as well as
    #: after a write. It began "saved to the file; ...", so the first thing a person saw on a panel they had
    #: not touched was their own path followed by a claim that something had been saved.
    CONFIG_NOTE = ("writing it reconfigures nothing already running: the supervisors and this page keep the "
                   "configuration they started with, and every new meld process reads the new one")

    def api_config(self, req: Request) -> Response:
        """The `.meld.toml` as parsed, and a validated write of it (spec 22).

        The viewer decides nothing about validity: `Config.from_dict` decides whether a candidate parses,
        `doctor.config_checks` decides whether it is sane, and this reports what they said. Every refusal happens
        before `write_atomic`, so a rejected write leaves the file exactly as it was.

        A `PUT` must also name the `version` the `GET` it came from returned, or it is refused: a form held
        open while the file changed underneath it would otherwise save the copy it is still holding, quietly
        reverting whatever the file gained meanwhile.
        """
        if self.config_path is None:
            return _json(502, {"error": "this viewer was started without a configuration file"})
        try:
            if req.method == "GET":
                return self._read_config()
            try:
                body = json.loads(req.body or b"")
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                return _json(400, {"error": f"the request body is not JSON: {e}"})
            candidate = body.get("config") if isinstance(body, dict) else None
            if not isinstance(candidate, dict) or not candidate:
                return _json(400, {"error": "the request body must be a JSON object with a non-empty 'config'"})
            # The one read-side key `PUT` does not ignore. A panel the page opened an hour ago still holds the
            # hour-old copy -- `loadConfig` re-reads only when `configState` is null -- so without this a save
            # from it answered 200 and silently reverted every hand edit made to the file since. Checked here,
            # ahead of the content ladder: if the document being edited is no longer the document on disk,
            # that is the first thing worth saying, and "reload" is the answer to all of it.
            version = body.get("version")
            if not isinstance(version, str) or not version:
                return _json(400, {"error": "the request body must carry the 'version' the GET it came from returned"})
            try:
                current = config_version(self.config_path.read_text())
            except OSError as e:
                return _json(502, {"error": f"cannot read {self.config_path}: {e}"})
            if version != current:
                return _json(409, {"error": f"{self.config_path} changed on disk since this form was loaded; "
                                            "reload it before saving, or the change already there is lost",
                                   "version": current})
            unknown = unknown_keys(candidate)
            if unknown:
                # `from_dict` would ignore these; a form must not swallow a field somebody typed.
                return _json(400, {"error": f"unknown setting(s): {', '.join(dotted_key(k) for k in unknown)}"})
            try:
                cfg = Config.from_dict(candidate)
            except (ValueError, AttributeError, TypeError, OverflowError) as e:
                # ValueError is the one that does the work: ConfigError is a subclass, and since every
                # numeric setting gained a floor there is no unguarded `int()` left in `from_dict` for
                # anything to escape from -- `test_no_numeric_setting_is_read_with_a_bare_int` pins that
                # absence. So a cleared field (""), a non-numeric string ("26x"), and the non-standard
                # Infinity/NaN literals `json.loads` accepts as floats all arrive here as the ConfigError
                # `_at_least` raises, naming the setting as well as the value. AttributeError/TypeError:
                # a "table" that is not one (`review = 5`) makes `from_dict`'s own `.get` raise rather than
                # refuse. OverflowError is unreachable today for exactly the reason above -- `_at_least`
                # rejects `inf` as the non-integer it is, before any conversion -- and is kept only so that
                # re-introducing an arithmetic read cannot turn a bad candidate into a dropped connection;
                # it costs nothing and this clause must not be the thing that needs remembering.
                # `unknown_keys` cannot see any of this either, since it only walks mappings. It is
                # still the candidate's problem, so it is a 400 naming what went wrong, never a traceback.
                return _json(400, {"error": f"{type(e).__name__}: {e}"})
            text = dumps(cfg)
            try:
                reparsed = Config.from_dict(tomllib.loads(text))
            except (ConfigError, tomllib.TOMLDecodeError) as e:
                return _json(500, {"error": f"the rendered file does not parse: {e}"})
            if reparsed != cfg:
                differs = sorted(f for f in cfg.__dataclass_fields__ if getattr(cfg, f) != getattr(reparsed, f))
                return _json(500, {"error": "the rendered file does not describe the same configuration", "differs": differs})
            checks = config_checks(cfg, self.ctx.models, self.ctx.adapter_for)
            if any(c.ok is False for c in checks):
                return _json(409, {"error": "meld doctor's configuration checks refuse this", "checks": [asdict(c) for c in checks]})
            try:
                write_atomic(self.config_path, text)
            except OSError as e:
                return _json(502, {"error": f"cannot write {self.config_path}: {e}"})
            with self._lock:
                # A provable no-op today, and kept deliberately. `/api/state`'s config-derived numbers -- the
                # budget cap, the warn thresholds -- come from `ctx.cfg` (`status.py`), the configuration this
                # process loaded at launch, which a write to the *file* does not touch: plan decision 6 says
                # a write reconfigures nothing already running, and that staleness is the decision, not a bug
                # this line fixes. It stays because every mutating route drops the cache, and because a future
                # `/api/state` that read the file rather than `ctx.cfg` would then be correct by default
                # rather than by someone remembering to add it back.
                self._state_cache = None
            return self._read_config(ok=True)
        except Exception as e:  # noqa: BLE001
            # Same reasoning as api_action: a long-lived local tool must answer even when the answer is that
            # something unforeseen broke. The case that motivated this: a `.meld.toml` carrying a bare TOML
            # date/time/datetime (`load_config` accepts one happily; `json.dumps` cannot serialize one) makes
            # `_read_config` raise, reached from GET as well as from a successful write's own re-read above --
            # which is why this wraps the whole method rather than only the PUT-parsing middle.
            # KeyboardInterrupt is BaseException, not Exception, so it still propagates past this; _StopRequested
            # (runners.py, raised only by the supervisor's own signal handler) is an Exception and would be
            # caught here like any other -- harmless only because meld watch never installs that handler.
            return _json(500, {"error": f"{type(e).__name__}: {e}"})

    def _read_config(self, *, ok: bool = False) -> Response:
        try:
            raw = self.config_path.read_text()
        except OSError as e:
            return _json(502, {"error": f"cannot read {self.config_path}: {e}"})
        try:
            parsed = tomllib.loads(raw)
        except tomllib.TOMLDecodeError as e:
            return _json(502, {"error": f"cannot read {self.config_path}: {e}"})
        models = {adapter: [{"id": s.id, "efforts": list(s.efforts), "tier": s.tier, "context_class": s.context_class}
                            for s in self.ctx.models.for_adapter(adapter)]
                  for adapter in self.ctx.models.adapters}
        config, error = _canonical_config(parsed)
        # The two states that block every save, said on the read rather than only on the person's first
        # attempt. `unknown` names each key the schema does not define -- rendered as an ordinary editable
        # field otherwise, so a person learns on Save that nothing can be written until they hand-edit the
        # file, and that blanking the box does not remove the key. `error` is the parser's own complaint
        # when `_canonical_config` fell back to the raw parse, which is otherwise invisible in the body.
        # Both are diagnosis, not settings: `PUT` ignores them exactly as it ignores `path`, `models`,
        # `note` and `version`, and `unknown_keys` is re-derived from the candidate on the way in.
        payload = {"path": str(self.config_path), "config": config, "models": models,
                   "note": self.CONFIG_NOTE, "version": config_version(raw),
                   "unknown": [dotted_key(k) for k in unknown_keys(parsed)]}
        if error is not None:
            payload["error"] = error
        # allow_nan=False, scoped to this one return: `parsed` is tomllib's raw parse of the whole file, and
        # TOML float syntax allows a bare inf/nan/+nan/-inf literal anywhere -- json.dumps's default would
        # silently emit the non-standard tokens NaN/Infinity, valid Python but not valid JSON, which a browser's
        # JSON.parse rejects. Refusing to serialize one turns it into the same clean 500 the catch-all above
        # already gives a bare TOML date -- not a global default, since the other four routes' 200s sit outside
        # any catch-all and a raise there would drop the connection instead.
        return _json(200, {**payload, "ok": True} if ok else payload, allow_nan=False)


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
