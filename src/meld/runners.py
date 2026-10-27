"""Supervise a group's two long-lived roles: launch, restart on exit, recycle on schedule."""
from __future__ import annotations

import json
import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from meld.adapters import Adapter, LaunchSpec
from meld.assemble import BoardError, find_active_milestone
from meld.atomic import write_atomic
from meld.config import GroupConfig, Party, extra_env
from meld.dispatch import DispatchError
from meld.github import GitHubError
from meld.grammar import Header, compose
from meld.prompts import ROLES, role_prompt
from meld.state import liveness, pid_alive, proc_start_of
from meld.transcript import Tail

ROLE_MAX_TURNS = 10000
_MAX_BACKOFF = 300.0
# A `claude -p` / `codex exec` session ends whenever the model returns its final message, so a role
# exiting 0 after a real stretch of work is the steady state, not a failure: it relaunches at once and
# the backoff counter resets. A non-zero exit is a crash: exponential backoff, and the role is told so
# (it posts RESUMED). A 0 within a minute of starting is throttled with the same backoff -- a session
# that never got going must not hot-loop -- but it is still an exit, not a crash: the first live run's
# codex PM finished honest 50-second turns (`meld wait` returning early on board activity) and, told
# "crash" each time, posted RESUMED on every relaunch.
CLEAN_EXIT_SECONDS = 60.0
# How long a freeze is ever believed, whatever instant the CLI's own prose named. A limit message is prose, and
# one stray timestamp in it ("...you have reached your usage limit for GitHub API requests; the milestone closes
# 2026-12-01T00:00:00Z") would otherwise freeze a role until December: the freeze is persisted, survives every
# supervisor restart, is re-probed by nothing, is invisible to `meld status` and `meld doctor`, and is recoverable
# only by a person deleting limit.json. Six hours covers a Claude five-hour session window with an hour to spare,
# and a genuine weekly limit simply re-probes four times a day -- each re-probe costing one fast-failing launch
# and one true LIMITED, which is far cheaper than a group stranded for a month. A constant, not a config knob.
MAX_FREEZE_MINUTES = 8 * 24 * 60   # a provider's named reset is believed up to eight days (plan 38: six hours re-probed a week-long quota 48 times)
MAX_RETRY_MINUTES = 24 * 60         # the re-probe cadence for a limit that named no time doubles each time it hits the limit again, up to a day
_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


class _StopRequested(Exception):
    """Raised by the supervisor's signal handler, only while it is inside its own sleep."""


@dataclass
class RoleProcess:
    role: str
    proc: object
    started: float
    log_path: Path
    restarts: int = 0
    launches: int = 1
    last_exit: int | None = None
    reason: str = "start"


def role_env(ctx, group: GroupConfig, role: str, *, base_env: dict | None = None) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    env.update(extra_env(group))   # the group's own variables (a model endpoint, say) under its identity, never over it
    env.update({"MELD_GROUP": group.name, "MELD_ROLE": role, "MELD_HOME": str(ctx.store.home)})
    return env


def launch_role(ctx, group: GroupConfig, adapter: Adapter, role: str, *, base_env: dict | None = None, clock=time.monotonic,
                launch_no: int = 1, reason: str = "start") -> RoleProcess:
    """Start one role session. `launch_no` and `reason` (start, exit, crash, recycle) go into the prompt's
    `## This launch` block so the role knows whether to announce itself."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    cfg_role = group.orchestrator if role == "orchestrator" else group.pm
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = ctx.store.home / "logs" / group.name / f"{role}-{stamp}.log"
    try:
        # Assembling the prompt is as much of the launch as spawning it, and reads as many files: the preamble,
        # the role prompt, the product's standards. A filesystem error in any of them is the same failure as a
        # log file that cannot be opened, so it is inside this `try` and becomes the same DispatchError.
        ctx.refresh_roster()  # a skill installed or a product worker file added since the last launch belongs in this prompt
        prompt = role_prompt(role, ctx.cfg, group, ctx.repo_root, roster_entries=ctx.roster_entries(), launch_no=launch_no, reason=reason)
        spec = LaunchSpec(role=role, model=cfg_role.model, effort=cfg_role.effort, cwd=ctx.repo_root, prompt=prompt,
                          log_path=log_path, timeout_seconds=ctx.cfg.restart_hours * 3600, env=role_env(ctx, group, role, base_env=base_env),
                          max_turns=ROLE_MAX_TURNS, cli_config=dict(group.cli_config))
        proc = adapter.launch(spec)
    except OSError as e:
        raise DispatchError(f"cannot start {adapter.name}: {e}") from e
    return RoleProcess(role=role, proc=proc, started=clock(), log_path=log_path, launches=launch_no, reason=reason)


def _terminate(proc, *, grace: float = 10.0, sleep=time.sleep) -> None:
    # Signal the whole group even when the leader has already exited: a background job the role started
    # is still in it. ESRCH means the group is gone; EPERM is macOS answering for an un-reaped zombie
    # leader, or a pid reused by another user's process -- nothing of ours is left to signal either way.
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    waited = 0.0
    while proc.poll() is None and waited < grace:
        sleep(0.1)
        waited += 0.1
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait()


def _iso_now() -> str:
    """The default `wall` seam (see run_group): what time it is, as the format every instant here uses."""
    return datetime.now(timezone.utc).strftime(_ISO_FMT)


def _plus_minutes(iso: str, minutes: int) -> str:
    """`iso` plus `minutes`, in the same format, or "" when `iso` is not an instant.

    The one place an instant is parsed. Every other comparison in the freeze path is a plain string compare,
    which is chronological for this format -- and "" is falsy, so a freeze file carrying no usable instant
    reads as passed rather than as a freeze with no end.
    """
    try:
        base = datetime.strptime(iso, _ISO_FMT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return ""
    return (base + timedelta(minutes=minutes)).strftime(_ISO_FMT)


def freeze_path(home: Path, group: str, role: str) -> Path:
    """Where a usage freeze is remembered. Local state, so a supervisor restart honours a live freeze."""
    return home / "state" / group / role / "limit.json"


def supervisor_path(home: Path, group: str) -> Path:
    """Where a group's supervisor records itself: its pid and the instant the kernel started it (spec 22)."""
    return home / "state" / group / "supervisor.json"


def role_run_path(home: Path, group: str, role: str) -> Path:
    """Where a role's current launch is recorded: pid, start time, the log it writes, launch number and reason."""
    return home / "state" / group / role / "current.json"


def processes(cfg, home: Path, *, alive: Callable[[int], bool] = pid_alive, start_of: Callable[[int], str] = proc_start_of) -> dict:
    """Every group's supervisor and roles as the local records describe them: spec 22's tri-state liveness with its
    reason per process, plus each role's freeze -- read from `$MELD_HOME/state` alone, no GitHub (`meld ps`)."""
    from meld.prompts import ROLES
    out = {}
    for g in cfg.groups:
        roles = {r: {**liveness(read_record(role_run_path(home, g, r)), alive=alive, start_of=start_of), "frozen": read_record(freeze_path(home, g, r))}
                 for r in ROLES}
        out[g] = {"supervisor": liveness(read_record(supervisor_path(home, g)), alive=alive, start_of=start_of), "roles": roles}
    return out


def read_record(path: Path) -> dict | None:
    """A JSON object at `path`, or None when there is none, it is unreadable, or it is not an object."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_record(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(payload, indent=2) + "\n")


# The freeze file is one such record; these names are what plan 9 and its tests call the same two functions.
read_freeze = read_record
write_freeze = write_record


def _note(path: Path, payload: dict) -> None:
    """Write a record about a process, and swallow the failure if the disk will not take it.

    A record is evidence for `meld watch`, nothing more: no decision in this module reads one back. So a state
    directory that cannot be written -- the full disk of 2026-09-12, a permission -- must not turn a launch that
    succeeded into an exception, because the process is running whether or not it was written down, and a
    `_try_launch` that raised after `adapter.launch` returned would leave it out of `procs` and launch it again.
    """
    try:
        write_record(path, payload)
    except OSError:
        pass


def limit_of(log_path: Path, adapter: Adapter, now: str) -> str | None | bool:
    """`False` when this role's transcript shows no usage limit; the reset instant when it shows one that
    named a time; `None` when it shows one that named no time.

    Three outcomes rather than two on purpose: "limited, time unknown" has to be distinguishable from "not
    limited", because the first waits `runners.limit_retry_minutes` and the second relaunches at once.

    Only the adapter knows how its CLI says this, so this asks it for events and looks for the one kind that
    means the quota is gone; no prose is matched here.
    """
    reader = Tail(log_path)
    # read then flush, exactly as dispatch.render_tail does: a role killed mid-write leaves its last line
    # unterminated, and that is the line most likely to carry the limit.
    events = reader.read(adapter.parse_events, now) + reader.flush(adapter.parse_events, now)
    for event in reversed(events):
        if event.kind == "usage_limit":
            return event.meta.get("resets_at") or None
    return False


def limit_text(log_path: Path, adapter: Adapter, now: str) -> str:
    """What the CLI said when it hit its limit -- the last `usage_limit` event's text -- for the freeze record and the page."""
    reader = Tail(log_path)
    events = reader.read(adapter.parse_events, now) + reader.flush(adapter.parse_events, now)
    for event in reversed(events):
        if event.kind == "usage_limit":
            return event.text
    return ""


def freeze_deadline(freeze: dict, retry_minutes: int) -> str:
    """The instant a freeze runs until: the earlier of the instant the CLI named and MAX_FREEZE_MINUTES after it began,
    else the record's own re-probe interval (`retry_minutes` in it, which backs off) or the configured one after it
    began; "" when the record carries no usable instant (so it reads as passed)."""
    at = freeze.get("at") or ""
    named = _plus_minutes(freeze.get("resets_at") or "", 0)   # validated: "" for absent, malformed, or not a string
    own = freeze.get("retry_minutes")
    retry = own if isinstance(own, int) and own > 0 else retry_minutes
    return min(named, _plus_minutes(at, MAX_FREEZE_MINUTES)) if named else _plus_minutes(at, retry)


def last_limit_path(home: Path, group: str, role: str) -> Path:
    """The previous freeze's record, kept after the freeze itself is cleared, so the next one can back off."""
    return home / "state" / group / role / "limit-last.json"


def next_retry_minutes(home: Path, group: str, role: str, now: str, configured: int) -> int:
    """How long to wait before re-probing a limit that named no time: the configured interval, doubled each time a
    re-probe runs straight into the limit again (the previous freeze ended within one interval of this one), up to a
    day. The second live run's Antigravity group re-probed a week-long quota every thirty minutes, and each probe sent
    the owner an over-limit email."""
    last = read_record(last_limit_path(home, group, role)) or {}
    prev = last.get("retry_minutes")
    until = _plus_minutes(last.get("until") or "", 0)
    if isinstance(prev, int) and prev > 0 and until and until <= now <= _plus_minutes(until, prev):
        return min(prev * 2, MAX_RETRY_MINUTES)
    return configured


def _announce(ctx, group: GroupConfig, role: str, *, resets_at: str | None, retry_minutes: int | None = None) -> bool:
    """Post the one LIMITED notice for a freeze. True when it reached the board.

    The role that hit the limit is already gone, so the supervisor is the only thing left that can say so
    (spec section 7) -- and it says so through `create_comment`, not `meld post`, because LIMITED is a
    framework-only verb no role may forge (`posting.FRAMEWORK_ONLY_VERBS`).
    """
    try:
        _, coordination = find_active_milestone(ctx.gh)
        party = Party("pm", group.name) if role == "pm" else Party("group", group.name)
        until = (f"resets: {resets_at}" if resets_at
                 else f"resets: unknown; re-probing in {retry_minutes or ctx.cfg.runners_limit_retry_minutes}m")
        ctx.gh.create_comment(coordination, compose(Header(party, "LIMITED", None, None),
                                                    f"{group.name}'s {role} hit its usage limit and is frozen.\n{until}"))
    except (GitHubError, BoardError):
        return False
    return True


def _freeze(ctx, group: GroupConfig, role: str, *, resets_at: str | None, now: str, text: str = "") -> None:
    """Remember the freeze, and tell the board.

    The freeze matters more than the announcement, so a notice that could not be posted -- a supervisor
    rate-limited at the very moment a role's quota ran out -- is written as `posted: False` and retried by
    `_frozen` while the freeze stands. A freeze nobody was told about is the silent role this mechanism exists
    to remove. `announce_attempted_at` is when this first attempt was made, so the retry cadence is measured
    from it rather than restarting with the next supervisor.
    """
    payload: dict = {"at": now, "posted": False, "announce_attempted_at": now,
                     "retry_minutes": next_retry_minutes(ctx.store.home, group.name, role, now, ctx.cfg.runners_limit_retry_minutes)}
    if resets_at:
        payload["resets_at"] = resets_at
    if text:
        payload["text"] = text[:500]   # the provider's own words, for the page's limits view
    payload["posted"] = _announce(ctx, group, role, resets_at=resets_at, retry_minutes=payload["retry_minutes"])
    write_freeze(freeze_path(ctx.store.home, group.name, role), payload)
    write_record(last_limit_path(ctx.store.home, group.name, role),
                 {"at": now, "retry_minutes": payload["retry_minutes"], "until": freeze_deadline(payload, payload["retry_minutes"])})


def _frozen(ctx, group: GroupConfig, role: str, *, now: str) -> str:
    """The instant this role's freeze runs until, or "" when no freeze stands -- so one call both answers the
    question and gives the report something to show. Two side effects belong to answering it: a freeze whose
    window has passed is cleared here, so the next poll relaunches; and a freeze that still stands but has
    never been announced is re-announced here, on the re-probe cadence, and recorded once it lands.

    The deadline is the earlier of the instant the CLI named and `MAX_FREEZE_MINUTES` after the freeze began:
    a named instant is believed, but only that far. Both go through `_plus_minutes`, the one place an instant
    is parsed, so a freeze file carrying no usable instant -- a hand-edited `resets_at`, a missing `at` -- reads
    as passed rather than as a freeze with no end, and the role's next limit rewrites the file with a fresh `at`.
    """
    path = freeze_path(ctx.store.home, group.name, role)
    freeze = read_freeze(path)
    if freeze is None:
        return ""
    deadline = freeze_deadline(freeze, ctx.cfg.runners_limit_retry_minutes)
    if not (deadline and now < deadline):             # ISO-8601 Z strings sort chronologically
        path.unlink(missing_ok=True)
        return ""
    if not freeze.get("posted"):
        # Retrying is not free: every attempt is `find_active_milestone`'s paginated reads plus a `create_comment`,
        # aimed at a board that is already failing, and this runs every poll interval -- 5 seconds in production.
        # So it runs on the same cadence as the re-probe, and the attempt is persisted whether or not it landed
        # (a `tried` that is not an instant reads as due, which errs towards announcing the freeze).
        tried = freeze.get("announce_attempted_at")
        if not tried or now >= _plus_minutes(tried, ctx.cfg.runners_limit_retry_minutes):
            posted = _announce(ctx, group, role, resets_at=freeze.get("resets_at"), retry_minutes=freeze.get("retry_minutes"))
            write_freeze(path, {**freeze, "posted": posted, "announce_attempted_at": now})
    return deadline


def run_group(ctx, *, adapter: Adapter | None = None, once: bool = False, cycles: int | None = None, interval: float = 5.0,
              sleep=time.sleep, clock=time.monotonic, wall: Callable[[], str] = _iso_now,
              start_of: Callable[[int], str] = proc_start_of, base_env: dict | None = None) -> dict:
    """Keep a group's roles alive. Two clocks, for two jobs: `clock` is monotonic and drives the backoff and
    the restart schedule; `wall` answers what time it is, because a freeze deadline is a wall-clock instant a
    CLI named and monotonic seconds cannot be compared with one. Both are injected so tests never wait; `start_of`
    reads a process's start time (`state.proc_start_of`) and is injected so tests never run `ps`."""
    group = ctx.cfg.groups[ctx.party.group]
    adp = adapter if adapter is not None else ctx.adapter()
    if adp is None:
        raise ValueError("meld run needs a group party; set MELD_GROUP or pass the group name")

    state = {"stop": False, "sleeping": False}

    def on_signal(signum, frame):  # noqa: ARG001
        state["stop"] = True
        if state["sleeping"]:
            raise _StopRequested()

    def _pause(seconds: float) -> None:
        # Sleeps the supervisor takes on its own behalf (backoff, poll interval) go through
        # here so on_signal can cut them short; _terminate's sleeps do not, since it is
        # already tearing a process down and an early exit there would leave that in doubt.
        state["sleeping"] = True
        try:
            sleep(seconds)
        except _StopRequested:
            pass
        finally:
            state["sleeping"] = False

    previous = {sig: signal.signal(sig, on_signal) for sig in (signal.SIGTERM, signal.SIGINT)}
    # Who this supervisor is, for `meld watch`'s liveness check (spec 22). Never removed: a dead pid in this file
    # is "confirmed gone", which is more than a missing file can say, and the next supervisor overwrites it.
    _note(supervisor_path(ctx.store.home, group.name),
          {"pid": os.getpid(), "started_at": wall(), "proc_start": start_of(os.getpid())})
    procs: dict[str, RoleProcess | None] = {}   # None once a role's launch attempt failed: nothing to poll
    errors: dict[str, int] = {}                 # how many turns this role could not take at all
    failures: dict[str, str] = {}               # and what the last one said
    pending: dict[str, dict] = {}               # the launch a role is still owed, its attempt having failed
    frozen: dict[str, str] = {}                 # the instant a role's freeze runs until, while one stands

    def _cannot(role: str, detail: str) -> None:
        """Record a step this role could not take, and back off on the crash schedule.

        A supervisor that cannot get a role through a cycle has a problem only a person can fix -- no disk, no
        binary, no permission, no `gh` -- and exiting hides it instead of reporting it: residual 4 is an
        `OSError` creating a log file, which is how both groups died mid-milestone on 2026-09-12 with
        `[Errno 28] No space left on device` as their last word.
        """
        errors[role] = errors.get(role, 0) + 1
        failures[role] = detail
        _pause(min(5.0 * (2 ** (errors[role] - 1)), _MAX_BACKOFF))

    def _try_launch(role: str, *, launch_no: int, reason: str, restarts: int = 0,
                    last_exit: int | None = None) -> RoleProcess | None:
        """Launch a role, or record the failure, back off, and answer None.

        `once` is the exception, because it is a one-shot and not a supervisor: a relaunch is the very thing
        `--once` excludes, so a role that cannot start there is the answer.
        """
        try:
            fresh = launch_role(ctx, group, adp, role, base_env=base_env, clock=clock,
                                launch_no=launch_no, reason=reason)
        except DispatchError as e:
            if once:
                raise
            # The attempt is still owed, unchanged: nothing ran, so this role's launch number, the reason it
            # was launching for and the exit that prompted it are all still true of it on the next cycle.
            pending[role] = {"launch_no": launch_no, "reason": reason, "restarts": restarts, "last_exit": last_exit}
            _cannot(role, str(e))
            return None
        pending.pop(role, None)
        _note(role_run_path(ctx.store.home, group.name, role),
              {"pid": fresh.proc.pid, "started_at": wall(), "proc_start": start_of(fresh.proc.pid),
               "log_path": str(fresh.log_path), "reason": reason, "launch_no": launch_no})
        fresh.restarts, fresh.last_exit = restarts, last_exit
        return fresh

    def _report(role: str) -> dict:
        """What this role did, for the caller. A role can have no live process -- frozen since before this
        supervisor started, or a launch that could not be made -- and then its line says what it managed
        before that, with `launch_errors` and `last_launch_error` carrying the diagnosis and `frozen_until`
        separating a frozen role (nothing wrong, waiting for a quota) from one that could not start."""
        rp, owed = procs.get(role), pending.get(role, {})
        if rp is None:
            # It has made one fewer launch than the one it is still owed: none at all, for a role that has
            # never started, and `reason` is null because it has no launch to have a reason for.
            line = {"launches": owed.get("launch_no", 1) - 1, "restarts": owed.get("restarts", 0),
                    "last_exit": owed.get("last_exit"), "reason": None}
        else:
            code = rp.proc.poll()
            line = {"launches": rp.launches, "restarts": rp.restarts,
                    "last_exit": code if code is not None else rp.last_exit, "reason": rp.reason}
        return {**line, "launch_errors": errors.get(role, 0), "last_launch_error": failures.get(role),
                "frozen_until": frozen.get(role) or None}

    def _turn(role: str) -> None:
        """One role's step of one cycle: honour its freeze, or launch it, or poll what is running. Every
        `OSError` this can raise is the caller's to catch -- see the boundary in the loop below."""
        now = wall()  # one instant for this role's whole turn, so the freeze it writes matches the one it read
        # This check must stay first -- before the poll, before any decision to launch. A role frozen
        # on a usage limit is left alone, and nothing this loop later learns to do may relaunch it --
        # including the first cycle's launches, which is why they happen here and not before the loop.
        had_freeze = read_freeze(freeze_path(ctx.store.home, group.name, role)) is not None
        frozen[role] = _frozen(ctx, group, role, now=now)
        if frozen[role]:
            return
        rp = procs.get(role)
        if rp is None:
            # Nothing to poll: this role has never started, or its last attempt failed. The launch it
            # is owed carries the reason it was owed for -- `limit` when `_frozen` has just cleared a
            # freeze, so the role posts RESUMED, and `start` on this supervisor's first cycle.
            procs[role] = _try_launch(role, **(pending.get(role)
                                               or {"launch_no": 1, "reason": "limit" if had_freeze else "start"}))
            return
        if had_freeze:
            # `_frozen` has just cleared a freeze whose window passed. Relaunch deliberately, as
            # `limit` so the role posts RESUMED and closes the LIMITED pair -- and without reading the
            # transcript again: its last event is still the usage_limit that froze it.
            if rp.proc.poll() is None:
                _terminate(rp.proc, sleep=sleep)
            procs[role] = _try_launch(role, launch_no=rp.launches + 1, reason="limit",
                                      restarts=rp.restarts, last_exit=rp.proc.poll())
            return
        code = rp.proc.poll()
        if code is not None:
            rp.last_exit = code
            if once:
                return
            # A limit outranks the exit code: an exhausted quota exits in a way no code distinguishes
            # from a clean finish, and relaunching it is the bug this branch exists to stop.
            limited = limit_of(rp.log_path, adp, now)
            if limited is not False:
                _freeze(ctx, group, role, resets_at=limited or None, now=now, text=limit_text(rp.log_path, adp, now))
                return                           # do not relaunch; the next cycle re-checks the freeze
            if code == 0 and clock() - rp.started >= CLEAN_EXIT_SECONDS:
                restarts, reason = 0, "exit"
            else:
                restarts, reason = rp.restarts + 1, ("exit" if code == 0 else "crash")
                _pause(min(5.0 * (2 ** rp.restarts), _MAX_BACKOFF))
            procs[role] = _try_launch(role, launch_no=rp.launches + 1, reason=reason,
                                      restarts=restarts, last_exit=code)
        elif clock() - rp.started >= ctx.cfg.restart_hours * 3600:
            _terminate(rp.proc, sleep=sleep)
            procs[role] = _try_launch(role, launch_no=rp.launches + 1, reason="recycle",
                                      last_exit=rp.last_exit)

    try:
        cycle = 0
        while not state["stop"]:
            cycle += 1
            for role in ROLES:  # ROLES, not the live processes: a role with no process is a role to launch
                try:
                    _turn(role)
                except OSError as e:
                    # The one boundary for everything a role's turn touches: `_announce`'s `gh` (a missing or
                    # upgraded binary is a `FileNotFoundError`, which is an `OSError`, and `_announce` catches
                    # only board errors), the freeze file's write and unlink, the prompt files a launch reads.
                    # Any of them ended the whole supervisor before -- the exact failure this plan removes. It
                    # counts as this role's cycle failing: recorded, backed off on the crash schedule, and the
                    # other role still gets its turn. `_StopRequested` and `KeyboardInterrupt` are not `OSError`,
                    # so a stop still propagates; do not widen this.
                    _cannot(role, f"{role}'s cycle failed: {e}")
            if once and not any(rp is not None and rp.proc.poll() is None for rp in procs.values()):
                break                                # --once is over once nothing of the group is running
            if cycles is not None and cycle >= cycles:
                break
            _pause(interval)
    except _StopRequested:
        # The handler fired in the sliver between sleep() returning and _pause clearing `sleeping`:
        # a stop was requested all the same, so honour it instead of surfacing a traceback.
        state["stop"] = True
    except BaseException:
        state["stop"] = True
        raise
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if state["stop"]:
            for rp in procs.values():
                if rp is not None:                   # a launch that failed left no process to stop
                    _terminate(rp.proc, sleep=sleep)
    return {"group": group.name, "roles": {role: _report(role) for role in ROLES}}
