"""Supervise a group's two long-lived roles: launch, restart on exit, recycle on schedule."""
from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from meld.adapters import Adapter, LaunchSpec
from meld.config import GroupConfig
from meld.prompts import ROLES, role_prompt
from meld.state import pid_alive

ROLE_MAX_TURNS = 10000
_MAX_BACKOFF = 300.0


@dataclass
class RoleProcess:
    role: str
    proc: object
    started: float
    log_path: Path
    restarts: int = 0
    launches: int = 1
    last_exit: int | None = None


def role_env(ctx, group: GroupConfig, role: str, *, base_env: dict | None = None) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    env.update({"MELD_GROUP": group.name, "MELD_ROLE": role, "MELD_HOME": str(ctx.store.home)})
    return env


def launch_role(ctx, group: GroupConfig, adapter: Adapter, role: str, *, base_env: dict | None = None, clock=time.monotonic) -> RoleProcess:
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    cfg_role = group.orchestrator if role == "orchestrator" else group.pm
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = ctx.store.home / "logs" / group.name / f"{role}-{stamp}.log"
    spec = LaunchSpec(role=role, model=cfg_role.model, effort=cfg_role.effort, cwd=ctx.repo_root,
                      prompt=role_prompt(role, ctx.cfg, group, ctx.repo_root, roster_entries=ctx.roster_entries()),
                      log_path=log_path, timeout_seconds=ctx.cfg.restart_hours * 3600, env=role_env(ctx, group, role, base_env=base_env),
                      max_turns=ROLE_MAX_TURNS)
    proc = adapter.launch(spec)
    return RoleProcess(role=role, proc=proc, started=clock(), log_path=log_path)


def _terminate(proc, *, grace: float = 10.0, sleep=time.sleep) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    waited = 0.0
    while pid_alive(proc.pid) and waited < grace:
        sleep(0.1)
        waited += 0.1
    if pid_alive(proc.pid):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_group(ctx, *, adapter: Adapter | None = None, once: bool = False, cycles: int | None = None, interval: float = 5.0,
              sleep=time.sleep, clock=time.monotonic, base_env: dict | None = None) -> dict:
    group = ctx.cfg.groups[ctx.party.group]
    adp = adapter if adapter is not None else ctx.adapter()
    if adp is None:
        raise ValueError("meld run needs a group party; set MELD_GROUP or pass the group name")
    procs = {role: launch_role(ctx, group, adp, role, base_env=base_env, clock=clock) for role in ROLES}
    stop = {"now": False}

    def on_signal(signum, frame):  # noqa: ARG001
        stop["now"] = True

    previous = {sig: signal.signal(sig, on_signal) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        cycle = 0
        while not stop["now"]:
            cycle += 1
            for role, rp in procs.items():
                code = rp.proc.poll()
                if code is not None:
                    rp.last_exit = code
                    if once:
                        continue
                    backoff = min(5.0 * (2 ** rp.restarts), _MAX_BACKOFF)
                    sleep(backoff)
                    fresh = launch_role(ctx, group, adp, role, base_env=base_env, clock=clock)
                    fresh.restarts, fresh.launches, fresh.last_exit = rp.restarts + 1, rp.launches + 1, code
                    procs[role] = fresh
                elif clock() - rp.started >= ctx.cfg.restart_hours * 3600:
                    _terminate(rp.proc, sleep=sleep)
                    fresh = launch_role(ctx, group, adp, role, base_env=base_env, clock=clock)
                    fresh.launches, fresh.last_exit = rp.launches + 1, rp.last_exit
                    procs[role] = fresh
            if once and all(rp.proc.poll() is not None for rp in procs.values()):
                break
            if cycles is not None and cycle >= cycles:
                break
            sleep(interval)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if stop["now"]:
            for rp in procs.values():
                _terminate(rp.proc, sleep=sleep)
    return {"group": group.name, "roles": {role: {"launches": rp.launches, "last_exit": rp.proc.poll() if rp.proc.poll() is not None else rp.last_exit}
                                            for role, rp in procs.items()}}
