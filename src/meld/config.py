"""Load .meld.toml into a typed Config, and identify the calling Party."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

# A group name becomes a party label (must not collide with a reserved party, and must parse as one in a
# coordination header), a meld:claimed-by:<group> GitHub label (labels are case-insensitive for uniqueness, and
# GitHub caps a label name at 50 characters, and the 16-character "meld:claimed-by:" prefix leaves 34 of them, so the
# 32 the rule below allows is comfortably inside that limit), and a worktree path
# component -- so it is lowercase, path-safe, and length-capped.
RESERVED_PARTY_NAMES = ("human", "joint")
GROUP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class ConfigError(ValueError):
    """Raised for a missing or invalid configuration value."""


@dataclass(frozen=True)
class RoleConfig:
    model: str
    effort: str


@dataclass(frozen=True)
class GroupConfig:
    name: str
    cli: str
    bot: str
    token_env: str | None
    concurrency: int
    review_concurrency: int
    orchestrator: RoleConfig
    pm: RoleConfig
    git_name: str | None = None
    git_email: str | None = None
    auth: str = "pat"                       # "pat" (token_env) or "app" (GitHub App installation)
    app_id: int | None = None
    installation_id: int | None = None
    private_key_env: str | None = None
    private_key_file: str | None = None

    @property
    def is_app(self) -> bool:
        return self.auth == "app"


def _require(data: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in data:
        raise ConfigError(f"missing required setting {path}.{key}")
    return data[key]


def _positive_int(value: Any, path: str) -> int:
    """A real TOML integer, greater than zero. `int()` would quietly accept "12", 12.5 and True (bool is an int
    subclass); and 0 is the placeholder `meld setup --app` writes, which otherwise reaches GitHub and comes back as
    an opaque 401 or 404 instead of a config error the human can act on."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{path} must be a positive integer (0 is the starter placeholder), got {value!r}")
    return value


def _loose_int(value: Any) -> int | None:
    """`value` as an int if it is cleanly one, else `None` -- never raises. Used to read a group's App
    settings when auth is not "app": the field is inert there (nothing reads it unless `is_app`), so a
    stray non-numeric leftover from a half-finished migration must not crash the parser. An App group's
    own read (`_positive_int` on the raw value, not on this) is what actually enforces the shape.

    `OverflowError` is caught alongside `TypeError`/`ValueError`: `inf`/`-inf` is a legal TOML float, and
    `int(float("inf"))` raises `OverflowError`, not `ValueError` -- without this, that error would escape
    both auth paths, pre-empting the clean `ConfigError` `_positive_int` would otherwise raise for an App
    group's `app_id`/`installation_id`."""
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _port(value: Any, path: str) -> int:
    """A TCP port for `meld watch` to bind: a real TOML integer in 0..65535, where 0 lets the OS choose. `int()` would
    quietly accept "8080", 1.5 and True (bool is an int subclass)."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 65535:
        raise ConfigError(f"{path} must be an integer from 0 to 65535 (0 lets the OS choose), got {value!r}")
    return value


def _role(data: Mapping[str, Any], path: str) -> RoleConfig:
    return RoleConfig(model=str(_require(data, "model", path)), effort=str(_require(data, "effort", path)))


#: Floors for the `[logs]` table, whose own case is the starkest: a `max_bytes_per_run` of 0 or a negative
#: makes every tick trim every log -- writing an empty `.1` over the previous one and zeroing the original
#: each cycle -- which is silent, total log destruction from one typo.
#:
#: Every other numeric setting `from_dict` reads carries a floor too, written inline at its own read below
#: rather than as a constant here, because each one is a plain 1 or 0 whose interesting part is *which* and
#: *why*. The form `meld watch` serves (spec section 22) is why they exist at all: a field a person can type
#: a 0 into is a field the parser has to have an opinion about.
#:
#: **The rule is whether `0` is a lie.** Refuse it where it cannot mean what it says -- where the code
#: coerces it into something else, or acts on it in a way the word does not describe. `review.quorum = 0`
#: is clamped to 1 by `effective_quorum` and always was. `audit.batch_merges = 0` cannot mean a batch of
#: nothing, because `audit.due` already requires at least one pending merge, so it can only mean 1.
#: `polling.interval_seconds = 0` does not poll quickly, it deletes the backoff, because `waiting.py`
#: doubles it from zero forever. `worker.log_tail_lines = 0` posts the *whole* transcript, since
#: `lines[-0:]` is every line. Keep `0` where it means something, and there only a negative is nonsense:
#: no budget of that tier, no grace on a lease, no amendment round, no waiting on age before an audit.
#:
#: That first clause is about lying rather than about harm, deliberately. Silently not doing what a field
#: says is the failure this whole surface exists to prevent, so an honest `0` stays even when it is
#: drastic, and a dishonest one goes even when -- as with `review.quorum` -- the clamp meant nothing was
#: misbehaving.
#:
#: **There is a second clause, and it is narrower: floor a `0` that is a runaway, even where it is
#: honest.** A zero poll interval reads as exactly what it does and still never stops.
#: `notify.repeat_after_minutes = 0` is the cleanest case of the clause standing on its own: by the
#: letter it is honest -- it means "repeat every pass", and `notifier.py` does that -- but what it
#: repeats is a nudge posted to a Slack workspace the person running meld may not even own, so the loop
#: runs away at something outside meld and at other people's attention. The test is not cost; an honest
#: `0` that is merely expensive stays. It is the absence of a stopping condition, pointed outward.
#: `notify.repeat_after_minutes` is a marginal call and is recorded as one in `docs/HANDOFF.md`'s
#: decisions for the owner, with the one character that reverses it.
#:
#: `_at_least` refuses a bool and a float as well as a number below the floor, so a form's
#: `review_concurrency = 2.5` is named rather than silently truncated to 2 by a bare `int()`. A group's
#: inert App settings are the one lenient numeric read left in the file; see `_loose_int` for why.
LOGS_MIN_BYTES_PER_RUN = 64 * 1024
LOGS_MIN_WARN_TOTAL_MB = 1
RUNNERS_MIN_LIMIT_RETRY_MINUTES = 1
LOGS_MIN_WARN_FREE_MB = 1


def _at_least(value: Any, minimum: int, path: str, unit: str) -> int:
    """A real TOML integer no smaller than `minimum`. `bool` is an `int` subclass, so it is refused by name."""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{path} must be an integer of at least {minimum} {unit}, got {value!r}")
    return value


_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smh]?)\s*$")
_DURATION_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600}


def parse_duration(text: str) -> int:
    """Parse '90s', '20m', '2h', or a bare number of seconds into seconds.

    Zero parses, because zero is a well-formed duration; whether zero is a *usable* one is the caller's
    question, and `from_dict` answers it for `[timeouts]` at its own read. A negative does not parse at
    all -- the pattern matches digits and nothing else, so no sign ever reaches here and no caller has to
    refuse one separately.
    """
    m = _DURATION_RE.match(str(text))
    if not m:
        raise ConfigError(f"invalid duration {text!r}; use e.g. 90s, 20m, 2h")
    return int(m.group(1)) * _DURATION_UNITS[m.group(2)]


#: The header a rendered file carries. Re-rendered from this template on every write: the form owns the file it
#: writes, and a comment a person left in it does not survive a save (spec section 22).
HEADER = """# .meld.toml -- written by meld. Every field the schema defines is written, defaults included, so
# what you read here is what runs. `meld doctor` verifies it; `meld watch`'s form edits it.
"""

#: The known schema, table by table: the keys `Config.from_dict` reads. `unknown_keys` walks a parsed file
#: against this, because `from_dict` reads with `.get` and ignores what it does not know -- right for a parser
#: reading a newer file, wrong for a form that must not swallow a field somebody typed (spec section 22).
KNOWN: dict[str, tuple[str, ...]] = {
    "repo": ("name", "default_branch", "private"),
    "humans": ("accounts",),
    "review": ("quorum", "pingpong_cap", "security_paths"),
    "agreement": ("amend_cap",),
    "polling": ("interval_seconds", "wait_timeout_seconds"),
    "runners": ("restart_hours", "roster_refresh_minutes", "silence_grace_minutes", "summary_cadence_minutes",
                "limit_retry_minutes"),
    "timeouts": ("S", "M", "L"),
    "budget": ("small", "medium", "large", "wallclock_hours"),
    "notify": ("slack_dm_webhook_env", "repeat_after_minutes"),
    "worker": ("log_tail_lines", "max_turns"),
    "lease": ("grace_minutes",),
    "audit": ("batch_merges", "batch_hours"),
    "logs": ("max_bytes_per_run", "warn_total_mb", "warn_free_mb"),
    "viewer": ("port", "open_browser"),
}

#: A group's own keys, and a role's. `groups` is keyed by a name the product chooses, so only the fields are fixed.
KNOWN_GROUP = ("cli", "bot", "token_env", "concurrency", "review_concurrency", "git_name", "git_email",
               "auth", "app_id", "installation_id", "private_key_env", "private_key_file")
KNOWN_ROLE = ("model", "effort")


def dotted_key(path: tuple[str, ...]) -> str:
    """One `unknown_keys` path as the dotted string a message shows. **For display only.** TOML lets a quoted
    key carry a literal `.` (`[review]` with `"a.b" = 1`), so the dotted form is ambiguous and splitting it
    back apart is not the inverse of building it -- which is exactly how the viewer's `GET` came to answer
    500 `KeyError: 'a'` on such a file, making the whole form unavailable for it. Callers that need to walk
    to the key walk the tuple."""
    return ".".join(path)


def unknown_keys(data: Mapping[str, Any]) -> list[tuple[str, ...]]:
    """One path tuple per key in a parsed `.meld.toml` the schema does not define, sorted.

    Tuples rather than dotted strings because a caller that walks to the key must not have to re-split a
    name that may itself contain a `.`; `dotted_key` renders one for a message. See its own docstring.

    A group's *name* is the product's to choose, so `groups.<anything>` is legal and only its fields are checked.
    """
    out: list[tuple[str, ...]] = []
    for table, value in data.items():
        if table == "groups":
            if not isinstance(value, Mapping):
                # A shape problem, not a name problem: "groups" is the one table the schema is surest of, so
                # reporting it as an *unknown key* would tell a person "unknown key: groups" about a table
                # they spelled correctly. `Config.from_dict` is what actually surfaces this -- not as a clean
                # ConfigError, but as the AttributeError/TypeError calling .items()/.get() on the wrong shape
                # raises uncaught (load_config does not wrap it either); the viewer's PUT turns that into a
                # 400 (Task 4), and at the CLI it still reaches the caller as-is.
                continue
            for name, group in value.items():
                if not isinstance(group, Mapping):
                    continue    # ditto, one level down: a malformed group is not an unknown key either
                for key, sub in group.items():
                    if key in ("orchestrator", "pm"):
                        if isinstance(sub, Mapping):
                            out += [("groups", name, key, k) for k in sub if k not in KNOWN_ROLE]
                        continue
                    if key not in KNOWN_GROUP:
                        out.append(("groups", name, key))
            continue
        if table not in KNOWN:
            out.append((table,))
            continue
        if isinstance(value, Mapping):
            out += [(table, k) for k in value if k not in KNOWN[table]]
    return sorted(out)


#: The named escapes TOML basic strings allow, checked before the generic control-character fallback in `_s`
#: so a backslash, a quote, a tab, a newline, a carriage return, a backspace and a form feed each render as
#: their short form rather than a `\u00XX`.
_NAMED_ESCAPES = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


def _s(value: str) -> str:
    """One TOML basic string. Escaped rather than interpolated: a git_name with a quote, a backslash, a newline
    or any other control character must come back as itself instead of breaking out of its own literal --
    or, worse, producing a file `tomllib` refuses to read back at all. TOML 1.0 requires every C0 control
    character other than tab, plus DEL (U+007F), to be escaped inside a basic string; a name pasted from a
    coloured terminal can carry one of these (e.g. ESC, U+001B) even though it looks like plain text."""
    out = []
    for ch in value:
        if ch in _NAMED_ESCAPES:
            out.append(_NAMED_ESCAPES[ch])
        elif ch < "\x20" or ch == "\x7f":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _list(values: list | tuple) -> str:
    return "[" + ", ".join(_s(str(v)) for v in values) + "]"


def _lines(pairs: list[tuple[str, Any]]) -> list[str]:
    """`key = value` for each pair whose value is not None; None means the field is absent from this file."""
    out = []
    for key, value in pairs:
        if value is None:
            continue
        if isinstance(value, bool):
            out.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, int):
            out.append(f"{key} = {value}")
        elif isinstance(value, (list, tuple)):
            out.append(f"{key} = {_list(value)}")
        else:
            out.append(f"{key} = {_s(str(value))}")
    return out


def _group_lines(g: GroupConfig) -> list[str]:
    """One group, in the order section 5 writes it: what it is, how it authenticates, what it may run."""
    out = [f"[groups.{g.name}]"] + _lines([
        ("cli", g.cli), ("bot", g.bot), ("token_env", g.token_env),
        ("auth", g.auth),      # written even when it is the default "pat": every field, or the promise is a lie
        ("app_id", g.app_id), ("installation_id", g.installation_id),
        ("private_key_env", g.private_key_env), ("private_key_file", g.private_key_file),
        ("concurrency", g.concurrency), ("review_concurrency", g.review_concurrency),
        ("git_name", g.git_name), ("git_email", g.git_email),
    ])
    for role_name, role in (("orchestrator", g.orchestrator), ("pm", g.pm)):
        out += ["", f"[groups.{g.name}.{role_name}]"] + _lines([("model", role.model), ("effort", role.effort)])
    return out


def dumps(cfg: Config) -> str:
    """`cfg` as canonical TOML: every field the schema defines, in a fixed order, under the header template.

    The standard library reads TOML and does not write it. Writing only the known schema is what makes this safe
    to point at a person's file: a table this does not understand cannot be mangled, because it is not emitted at
    all -- and `unknown_keys` is what stops such a table reaching here unnoticed in the first place.
    """
    blocks: list[list[str]] = [
        ["[repo]"] + _lines([("name", cfg.repo_name), ("default_branch", cfg.default_branch), ("private", cfg.private)]),
        ["[humans]"] + _lines([("accounts", cfg.humans)]),
    ]
    for name in sorted(cfg.groups):
        blocks.append(_group_lines(cfg.groups[name]))
    blocks += [
        ["[review]"] + _lines([("quorum", cfg.review_quorum), ("pingpong_cap", cfg.pingpong_cap),
                               ("security_paths", cfg.security_paths)]),
        ["[agreement]"] + _lines([("amend_cap", cfg.amend_cap)]),
        ["[polling]"] + _lines([("interval_seconds", cfg.poll_interval_seconds),
                                ("wait_timeout_seconds", cfg.wait_timeout_seconds)]),
        ["[runners]"] + _lines([("restart_hours", cfg.restart_hours),
                                ("roster_refresh_minutes", cfg.roster_refresh_minutes),
                                ("silence_grace_minutes", cfg.silence_grace_minutes),
                                ("summary_cadence_minutes", cfg.summary_cadence_minutes),
                                ("limit_retry_minutes", cfg.runners_limit_retry_minutes)]),
        ["[timeouts]"] + _lines([(size, cfg.timeouts[size]) for size in ("S", "M", "L")]),
        ["[budget]"] + _lines([("small", cfg.budget["small"]), ("medium", cfg.budget["medium"]),
                               ("large", cfg.budget["large"]), ("wallclock_hours", cfg.budget_wallclock_hours)]),
        ["[notify]"] + _lines([("slack_dm_webhook_env", cfg.notify_webhook_env),
                               ("repeat_after_minutes", cfg.notify_repeat_after_minutes)]),
        ["[worker]"] + _lines([("log_tail_lines", cfg.worker_log_tail_lines), ("max_turns", cfg.worker_max_turns)]),
        ["[lease]"] + _lines([("grace_minutes", cfg.lease_grace_minutes)]),
        ["[audit]"] + _lines([("batch_merges", cfg.audit_batch_merges), ("batch_hours", cfg.audit_batch_hours)]),
        ["[logs]"] + _lines([("max_bytes_per_run", cfg.logs_max_bytes_per_run),
                             ("warn_total_mb", cfg.logs_warn_total_mb), ("warn_free_mb", cfg.logs_warn_free_mb)]),
        ["[viewer]"] + _lines([("port", cfg.viewer_port), ("open_browser", cfg.viewer_open_browser)]),
    ]
    return HEADER + "\n" + "\n\n".join("\n".join(b) for b in blocks) + "\n"


@dataclass(frozen=True)
class Config:
    repo_name: str
    default_branch: str
    private: bool
    humans: tuple[str, ...]
    groups: dict[str, GroupConfig]
    review_quorum: int
    pingpong_cap: int
    security_paths: tuple[str, ...]
    amend_cap: int
    poll_interval_seconds: int
    wait_timeout_seconds: int
    restart_hours: int
    roster_refresh_minutes: int
    silence_grace_minutes: int
    summary_cadence_minutes: int
    timeouts: dict[str, str]
    budget: dict[str, int]
    budget_wallclock_hours: int
    notify_webhook_env: str | None
    notify_repeat_after_minutes: int
    # Since plan 8a this bounds rendered EVENTS in the failure comment `collect` posts (`dispatch.render_tail`)
    # and raw LINES in the stored verdict payload (`dispatch.tail_lines`); the key keeps its name.
    worker_log_tail_lines: int = 50
    worker_max_turns: int = 200
    lease_grace_minutes: int = 10
    audit_batch_merges: int = 5
    audit_batch_hours: int = 4
    logs_max_bytes_per_run: int = 8 * 1024 * 1024
    logs_warn_total_mb: int = 2048
    runners_limit_retry_minutes: int = 30
    logs_warn_free_mb: int = 2048
    viewer_port: int = 0
    viewer_open_browser: bool = False

    def timeout_seconds(self, size: str) -> int:
        if size not in self.timeouts:
            raise ConfigError(f"unknown size {size!r}; expected one of {', '.join(self.timeouts)}")
        return parse_duration(self.timeouts[size])

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Config":
        repo = data.get("repo", {})
        humans = data.get("humans", {})
        groups_raw = data.get("groups", {})
        if not groups_raw:
            raise ConfigError("missing required table groups (at least one group)")
        groups: dict[str, GroupConfig] = {}
        for name, g in groups_raw.items():
            path = f"groups.{name}"
            if name in RESERVED_PARTY_NAMES:
                raise ConfigError(f"{path}: {name!r} is a reserved party name and cannot name a group")
            if not GROUP_NAME_RE.match(name):
                raise ConfigError(f"{path}: group names must match {GROUP_NAME_RE.pattern} -- lowercase, at most 32 "
                                  "characters: a name becomes the meld:claimed-by:<group> label and a worktree path component")
            auth = str(g.get("auth", "pat"))
            if auth not in ("pat", "app"):
                raise ConfigError(f"{path}.auth must be 'pat' or 'app', got {auth!r}")
            token_env = str(g["token_env"]) if g.get("token_env") else None
            if auth == "pat" and not token_env:
                raise ConfigError(f"missing required setting {path}.token_env (or set auth = \"app\")")
            # Read whenever present, not only when auth == "app": a PAT group carrying leftover App settings
            # from a half-finished migration must round-trip them unchanged, not lose them on the next save.
            app_id = _loose_int(g.get("app_id"))
            installation_id = _loose_int(g.get("installation_id"))
            key_env = str(g["private_key_env"]) if g.get("private_key_env") else None
            key_file = str(g["private_key_file"]) if g.get("private_key_file") else None
            bot = str(_require(g, "bot", path))
            if auth == "app":
                # _require's own ConfigError (missing key) must propagate as-is, not be relabeled
                # "must be a positive integer" -- so the lookup happens outside _positive_int. This re-read
                # replaces the loose one above with the strict one App auth actually needs.
                app_id = _positive_int(_require(g, "app_id", path), f"{path}.app_id")
                installation_id = _positive_int(_require(g, "installation_id", path), f"{path}.installation_id")
                if bool(key_env) == bool(key_file):
                    raise ConfigError(f"{path}: an App group needs exactly one of private_key_env or private_key_file")
                if not bot.endswith("[bot]"):
                    raise ConfigError(f"{path}.bot must be the App's bot login, '<slug>[bot]', got {bot!r}")
            groups[name] = GroupConfig(
                name=name,
                cli=str(_require(g, "cli", path)),
                bot=bot,
                token_env=token_env,
                # 0 is not "no dispatch" but "no dispatch ever": `rules.py`'s `n >= group.concurrency` and
                # `len(live) >= group.review_concurrency` are both true before anything has started.
                concurrency=_at_least(g.get("concurrency", 8), 1, f"{path}.concurrency", "concurrent dispatches"),
                review_concurrency=_at_least(g.get("review_concurrency", 4), 1, f"{path}.review_concurrency",
                                             "concurrent reviews"),
                orchestrator=_role(_require(g, "orchestrator", path), f"{path}.orchestrator"),
                pm=_role(_require(g, "pm", path), f"{path}.pm"),
                git_name=(str(g["git_name"]) if g.get("git_name") else None),
                git_email=(str(g["git_email"]) if g.get("git_email") else None),
                auth=auth,
                app_id=app_id,
                installation_id=installation_id,
                private_key_env=key_env,
                private_key_file=key_file,
            )
        review = data.get("review", {})
        agreement = data.get("agreement", {})
        polling = data.get("polling", {})
        runners = data.get("runners", {})
        timeouts = data.get("timeouts", {})
        budget = data.get("budget", {})
        notify = data.get("notify", {})
        worker = data.get("worker", {})
        lease = data.get("lease", {})
        audit = data.get("audit", {})
        logs = data.get("logs", {})
        viewer = data.get("viewer", {})
        timeouts_cfg = {"S": str(timeouts.get("S", "20m")), "M": str(timeouts.get("M", "45m")), "L": str(timeouts.get("L", "90m"))}
        for size, value in timeouts_cfg.items():
            try:
                seconds = parse_duration(value)
            except ConfigError as e:
                raise ConfigError(f"timeouts.{size}: {e}") from e
            # The same floor the numeric settings carry, in the shape a duration takes. `dispatch` sets a
            # worker's deadline to `when + timedelta(seconds=timeout_seconds(size))`, so a zero here launches
            # a worker that is already past its deadline -- one paid CLI session per launch, which is
            # `runners.restart_hours = 0` wearing different clothes. Enforced at this read rather than inside
            # `parse_duration`, whose contract ("a bare number of seconds" included) belongs to its other
            # callers and is not this plan's to change. A negative never reaches here: `_DURATION_RE` matches
            # `\d+`, so `-5m` is already an invalid duration above.
            if seconds <= 0:
                raise ConfigError(f"timeouts.{size} must be a duration greater than zero, got {value!r}: a "
                                  "worker launched with it is past its deadline the instant it starts")
        return cls(
            repo_name=str(_require(repo, "name", "repo")),
            default_branch=str(repo.get("default_branch", "main")),
            private=bool(repo.get("private", True)),
            humans=tuple(str(h) for h in humans.get("accounts", [])),
            groups=groups,
            # `effective_quorum` already clamps with `max(1, ...)`, so a 0 here never meant "no approvals
            # needed" -- it meant 1, silently. A `pingpong_cap` of 0 is worse than silent: `pingpong_exceeded`
            # is `changes_requested_total() >= cap`, true on a PR nobody has reviewed yet.
            review_quorum=_at_least(review.get("quorum", 2), 1, "review.quorum", "approving groups"),
            pingpong_cap=_at_least(review.get("pingpong_cap", 3), 1, "review.pingpong_cap", "reviews"),
            security_paths=tuple(str(p) for p in review.get("security_paths", [])),
            # 0 is meaningful here and kept: `can_amend` is `rounds < cap`, so a 0 makes AMEND illegal from the
            # start -- a coherent policy ("propose, then AGREE or DISSENT"), with `posting.py`'s own refusal
            # saying exactly that. Nothing loops, stalls or relaunches; only a negative is nonsense.
            amend_cap=_at_least(agreement.get("amend_cap", 3), 0, "agreement.amend_cap", "amendments"),
            # `waiting.wait_for_change` starts its backoff at `interval` and doubles it: 0 is a zero-sleep loop
            # against a rate-limited board that never backs off. A 0 timeout returns `meld wait` instantly, so
            # a PM's poll loop becomes a spin of paid CLI launches.
            poll_interval_seconds=_at_least(polling.get("interval_seconds", 30), 1, "polling.interval_seconds", "seconds"),
            wait_timeout_seconds=_at_least(polling.get("wait_timeout_seconds", 300), 1, "polling.wait_timeout_seconds",
                                           "seconds"),
            # `restart_hours` is both a role's launch timeout (`restart_hours * 3600`) and the recycle test
            # `clock() - started >= restart_hours * 3600`: 0 terminates and relaunches every cycle, and every
            # relaunch is a paid CLI session.
            restart_hours=_at_least(runners.get("restart_hours", 4), 1, "runners.restart_hours", "hours"),
            roster_refresh_minutes=_at_least(runners.get("roster_refresh_minutes", 60), 1,
                                             "runners.roster_refresh_minutes", "minutes"),
            # Section 13's "silent past silence_grace_minutes -> PING, then continue SOLO": a 0 tells every
            # orchestrator the other group has always been silent long enough to leave.
            silence_grace_minutes=_at_least(runners.get("silence_grace_minutes", 30), 1,
                                            "runners.silence_grace_minutes", "minutes"),
            summary_cadence_minutes=_at_least(runners.get("summary_cadence_minutes", 60), 1,
                                              "runners.summary_cadence_minutes", "minutes"),
            timeouts=timeouts_cfg,
            # An allowance, not a rate: 0 means "no runs of this tier", which `rules.py`'s `used >= cap`
            # implements exactly. `wallclock_hours` is the same shape (and read by nothing today).
            budget={
                "small": _at_least(budget.get("small", 200), 0, "budget.small", "runs"),
                "medium": _at_least(budget.get("medium", 80), 0, "budget.medium", "runs"),
                "large": _at_least(budget.get("large", 20), 0, "budget.large", "runs"),
            },
            budget_wallclock_hours=_at_least(budget.get("wallclock_hours", 72), 0, "budget.wallclock_hours", "hours"),
            notify_webhook_env=notify.get("slack_dm_webhook_env"),
            # Honest but a runaway -- the rule's second clause above, and its marginal case. A repeat
            # window of 0 makes every pass due again, so `notifier` re-posts the same nudge through
            # the Slack webhook each tick: an external service, and other people's attention. (The
            # `meld:nudged` label on GitHub is what gates a repeat, not where the nudge lands.)
            notify_repeat_after_minutes=_at_least(notify.get("repeat_after_minutes", 120), 1,
                                                  "notify.repeat_after_minutes", "minutes"),
            # `tail_lines` is `lines[-n:]`, and `lines[-0:]` is the *whole* file -- so a 0 here does the exact
            # opposite of what its author meant, posting an entire streamed transcript into a GitHub comment.
            worker_log_tail_lines=_at_least(worker.get("log_tail_lines", 50), 1, "worker.log_tail_lines", "lines"),
            worker_max_turns=_at_least(worker.get("max_turns", 200), 1, "worker.max_turns", "turns"),
            # 0 is meaningful here and kept: the lease's `until` is `deadline + grace`, so a 0 grace is a lease
            # that expires exactly at the deadline. Nothing degenerates; only a negative is nonsense.
            lease_grace_minutes=_at_least(lease.get("grace_minutes", 10), 0, "lease.grace_minutes", "minutes"),
            # The two halves of `audit.due` fall on opposite sides of the rule. `bool(pending)` guards both
            # comparisons, so `batch_merges = 0` cannot mean a batch of nothing -- it can only mean 1, which
            # is `review.quorum`'s case exactly. `batch_hours = 0` says something real and distinct: do not
            # wait on age at all, audit as soon as anything is pending. Nothing loops, nothing relaunches,
            # and refusing it would take away a usable policy for no gain.
            audit_batch_merges=_at_least(audit.get("batch_merges", 5), 1, "audit.batch_merges", "merges"),
            audit_batch_hours=_at_least(audit.get("batch_hours", 4), 0, "audit.batch_hours", "hours"),
            logs_max_bytes_per_run=_at_least(logs.get("max_bytes_per_run", 8 * 1024 * 1024), LOGS_MIN_BYTES_PER_RUN,
                                             "logs.max_bytes_per_run", "bytes"),
            logs_warn_total_mb=_at_least(logs.get("warn_total_mb", 2048), LOGS_MIN_WARN_TOTAL_MB,
                                         "logs.warn_total_mb", "megabytes"),
            runners_limit_retry_minutes=_at_least(runners.get("limit_retry_minutes", 30), RUNNERS_MIN_LIMIT_RETRY_MINUTES,
                                                  "runners.limit_retry_minutes", "minutes"),
            logs_warn_free_mb=_at_least(logs.get("warn_free_mb", 2048), LOGS_MIN_WARN_FREE_MB, "logs.warn_free_mb", "MB"),
            viewer_port=_port(viewer.get("port", 0), "viewer.port"),
            viewer_open_browser=bool(viewer.get("open_browser", False)),
        )

    @property
    def group_names(self) -> tuple[str, ...]:
        return tuple(self.groups.keys())

    @property
    def effective_quorum(self) -> int:
        return max(1, min(self.review_quorum, len(self.groups) - 1))

    def group_for_bot(self, login: str) -> GroupConfig | None:
        for g in self.groups.values():
            if same_login(g.bot, login):
                return g
        return None

    def is_human(self, login: str) -> bool:
        return any(same_login(h, login) for h in self.humans)

    def is_party(self, login: str) -> bool:
        """A configured bot or a listed human: the only logins that may speak in the grammar."""
        return self.group_for_bot(login) is not None or self.is_human(login)


def same_login(a: str, b: str) -> bool:
    """GitHub logins are case-insensitive, so every comparison of one against `.meld.toml` goes through here (spec 21
    item 20): an entry typed `Smidley` must still match the `smidley` GitHub reports."""
    return a.lower() == b.lower()


def load_config(path: Path) -> Config:
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError as e:
        raise ConfigError(f"config file not found: {path}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}") from e
    return Config.from_dict(data)


PartyKind = Literal["group", "pm", "human", "joint"]


@dataclass(frozen=True)
class Party:
    kind: PartyKind
    group: str | None = None

    @property
    def label(self) -> str:
        if self.kind == "group":
            return f"[{self.group}]"
        if self.kind == "pm":
            return f"[{self.group}:pm]"
        return f"[{self.kind}]"

    @classmethod
    def parse(cls, text: str, cfg: Config) -> "Party | None":
        if text == "human":
            return cls("human")
        if text == "joint":
            return cls("joint")
        name, _, role = text.partition(":")
        if name not in cfg.groups:
            return None
        if role == "":
            return cls("group", name)
        if role == "pm":
            return cls("pm", name)
        return None


def party_from_env(cfg: Config, env: Mapping[str, str]) -> Party:
    group = env.get("MELD_GROUP")
    if not group:
        return Party("human")
    if group not in cfg.groups:
        raise ConfigError(f"MELD_GROUP={group!r} is not a configured group")
    role = env.get("MELD_ROLE", "orchestrator")
    if role == "orchestrator":
        return Party("group", group)
    if role == "pm":
        return Party("pm", group)
    raise ConfigError(f"MELD_ROLE={role!r} must be 'orchestrator' or 'pm'")
