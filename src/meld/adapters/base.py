"""Adapter protocol: the only layer that knows how a particular CLI is launched."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO, Iterable, Mapping, Protocol

from meld.roster import WorkerType
from meld.transcript import Event, cap

SCRUBBED_EXACT = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "MELD_GROUP", "MELD_ROLE", "MELD_PUSH_TOKEN")
SCRUBBED_PREFIXES = ("MELD_TOKEN_", "MELD_APP_KEY_")
# A skill's name becomes a single path component (materialise_skills copies into `target_dir/<name>`), but
# frontmatter is free text a product author writes -- never let it carry a path separator, a leading dot, or
# anything else that could turn `<name>` into `../../src` and escape the skill directory it belongs in.
SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class LaunchSpec:
    role: str
    model: str
    effort: str
    cwd: Path
    prompt: str
    log_path: Path
    timeout_seconds: int
    env: dict[str, str]
    max_turns: int


class Adapter(Protocol):
    name: str

    def launch(self, spec: LaunchSpec) -> subprocess.Popen: ...

    def installed_skills(self) -> list[WorkerType]: ...

    def project_skills_dir(self) -> str | None: ...

    def command_timeout_ceiling_seconds(self) -> int: ...

    def supports_effort(self) -> bool: ...

    def version_argv(self) -> list[str]: ...

    def help_argv(self) -> list[str]: ...          # the command whose --help lists the flags `argv` passes

    def required_flags(self) -> tuple[str, ...]: ...  # flags `meld doctor` checks against that help text

    def parse_events(self, line: str, now: str) -> list[Event]: ...


def scrub_env(env: Mapping[str, str]) -> dict[str, str]:
    """Environment for a worker: no GitHub tokens or App private keys, no party identity, a marker that it is a worker."""
    out = {k: v for k, v in env.items() if k not in SCRUBBED_EXACT and not k.startswith(SCRUBBED_PREFIXES)}
    out["MELD_WORKER"] = "1"
    return out


def parse_doc(line: str) -> tuple[dict | None, str]:
    """`(the JSON object, the stripped line)`, or `(None, stripped)` when the line is not a JSON object.

    Every adapter's `parse_events` starts here, because every adapter faces the same two non-events: a
    blank line, and a line the CLI wrote to stderr that was never JSON at all (a Rust panic, a node
    warning, a cache error). Neither is a parse failure worth raising over.
    """
    s = line.strip()
    if not s or not s.startswith("{"):
        return None, s
    try:
        doc = json.loads(s)
    except json.JSONDecodeError:
        return None, s
    return (doc, s) if isinstance(doc, dict) else (None, s)


def raw_event(now: str, stripped: str) -> list[Event]:
    """The `raw` fallback: one event per unrecognized line, or nothing at all for a blank one."""
    return [Event(seq=0, at=now, kind="raw", text=cap(stripped))] if stripped else []


# A CLI's answer when the subscription's quota is gone. Best-effort by nature: there is no captured
# transcript of either CLI hitting a limit, so these come from prose observed in the first live run plus
# the tested set in Maurdekye/orgtree's limits.py (MIT). Getting it wrong in one direction leaves today's
# behaviour (the role is treated as a crash and backed off); wrong in the other freezes a healthy role.
# So every alternative below requires a possessive limit phrase, an explicit rate-limit phrase, or a 429 --
# never the bare word "limit", and never the max-turns ceiling, which is an ordinary exit.
_LIMIT_RE = re.compile(
    r"(?:\byou(?:'ve|\s+have)\s+(?:hit|reached|used)\s+(?:your|the)\b[^.]{0,60}?\blimit\b"
    r"|\b(?:your|the)\b[^.]{0,40}?\b(?:usage|rate|spend|session|weekly|daily|monthly)\s+limit\b"
    r"|\busage\s+limit\s+reached\b"
    r"|\brate[-\s]?limit(?:ed|ing)?\b"
    r"|\bquota\s+exceeded\b"
    r"|\b429\b)",
    re.IGNORECASE)
# The max-turns ceiling says "limit" and is not one: a role that used its turns exited, it was not throttled.
_NOT_LIMIT_RE = re.compile(r"\bmax[-_\s]?turns\b", re.IGNORECASE)
_ISO_RESET_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})Z?\b")
_CLOCK_RESET_RE = re.compile(r"\b(?:reset(?:s|ting)?|available|try\s+again|back)\b[^.]{0,20}?\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
                             re.IGNORECASE)
_DURATION_RESET_RE = re.compile(r"\bin\s+(?:about\s+)?(\d{1,3})\s*(second|minute|hour)s?\b", re.IGNORECASE)
_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def is_limit(text: object) -> bool:
    """Does this text say the subscription's quota is gone, rather than merely containing the word "limit"?"""
    s = text if isinstance(text, str) else ("" if text is None else str(text))
    if not s or _NOT_LIMIT_RE.search(s):
        return False
    return bool(_LIMIT_RE.search(s))


def _parse_now(now: str) -> datetime | None:
    try:
        return datetime.strptime(now, _ISO_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def reset_at(text: str, now: str) -> str | None:
    """When the quota comes back, as an ISO instant, or None when the text named no time.

    None is a real answer, not a failure: the supervisor re-probes on a fixed cadence rather than guessing a
    window length, because guessing long strands a whole group for hours (spec section 17).
    """
    m = _ISO_RESET_RE.search(text or "")
    if m:
        return m.group(1).replace(" ", "T") + "Z"
    base = _parse_now(now)
    if base is None:
        return None
    m = _DURATION_RESET_RE.search(text or "")
    if m:
        unit = {"second": "seconds", "minute": "minutes", "hour": "hours"}[m.group(2).lower()]
        return (base + timedelta(**{unit: int(m.group(1))})).strftime(_ISO_FMT)
    m = _CLOCK_RESET_RE.search(text or "")
    if m:
        hour, minute, meridiem = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            return None
        when = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when <= base:                      # the named clock time already passed today
            when += timedelta(days=1)
        return when.strftime(_ISO_FMT)
    return None


def limit_event(now: str, text: str) -> list[Event]:
    """The one event a supervisor acts on. `ok` is False: a quota that ran out is not a success."""
    when = reset_at(text, now)
    meta = {"resets_at": when} if when else {}
    return [Event(seq=0, at=now, kind="usage_limit", ok=False, text=cap(text), meta=meta)]


def parse_frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    found_close = False
    for line in lines[1:]:
        if line.strip() == "---":
            found_close = True
            break
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out if found_close else {}


def scan_skills(dirs: Iterable[Path], source: str = "skill") -> list[WorkerType]:
    found: dict[str, WorkerType] = {}
    for d in dirs:
        if not d.is_dir():
            continue
        # A symlinked skill directory is skipped for the same reason a symlinked SKILL.md is (below): it points
        # wherever its author aimed it, and everything under it would be read, briefed, and copied into worktrees.
        for skill_dir in sorted(p for p in d.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")):
            f = skill_dir / "SKILL.md"
            # is_symlink before is_file: a link points wherever its author aimed it, and reading it would put a
            # file from outside the skills tree into the roster, a brief, and (via materialise_skills) a worktree.
            if f.is_symlink() or not f.is_file():
                continue
            try:
                text = f.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            meta = parse_frontmatter(text)
            frontmatter_name = meta.get("name", "")
            if frontmatter_name and SKILL_NAME_RE.match(frontmatter_name):
                name = frontmatter_name
            elif SKILL_NAME_RE.match(skill_dir.name):
                # An unsafe or absent frontmatter name falls back to the directory name, itself validated --
                # `name: ../../src` must never reach a consumer that turns a skill name into a path component.
                name = skill_dir.name
            else:
                continue
            found.setdefault(name, WorkerType(name=name, description=meta.get("description", ""), source=source, path=f, text=text))
    return sorted(found.values(), key=lambda w: w.name)


def open_log(path: Path) -> IO[bytes]:
    path.parent.mkdir(parents=True, exist_ok=True)
    return open(path, "ab")


class DetachedPopen(subprocess.Popen):
    """A child the launching process may never wait on: dispatch fires a worker and forgets it (the worker outlives the
    meld process), while runners poll and wait on role processes explicitly. Popen.__del__ would warn 'subprocess N is
    still running' for a fire-and-forget child and register it for later reaping; meld reaps those by pid through
    state.pid_alive's waitpid instead, so the destructor is a no-op here (spec 21 item 6)."""

    def __del__(self) -> None:  # noqa: D401
        pass


def popen_detached(argv: list[str], spec: LaunchSpec, log: IO[bytes]) -> subprocess.Popen:
    """Start the CLI in its own session so a timeout can kill the whole process group."""
    return DetachedPopen(argv, cwd=str(spec.cwd), env=spec.env, stdin=subprocess.DEVNULL, stdout=log,
                         stderr=subprocess.STDOUT, start_new_session=True)
