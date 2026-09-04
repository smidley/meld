"""Dispatch, tick, collect, reap: the only module that spawns workers or touches worktrees."""
from __future__ import annotations

import os
import signal
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from meld.actions import IN_PROGRESS, refuse
from meld.adapters import Adapter, LaunchSpec, get_adapter, scrub_env
from meld.board import Board, RunRecord
from meld.brief import BRIEF_FILE, RESULT_FILE, render_brief
from meld.config import GroupConfig, Party
from meld.gitops import (
    branch_name,
    checkout_pr_head,
    create_worktree,
    exclude_paths,
    noreply_email,
    remove_worktree,
    set_identity,
    slugify,
    worktree_exists,
)
from meld.grammar import Header, compose
from meld.roster import WorkerType, roster
from meld.rules import Actor, Refusal, Verb, issue_refusal, party_of_login, pr_refusal
from meld.state import pid_alive
from meld.status import build_status

WORKER_PROMPT = ("Read .meld-brief.md in the current directory and carry it out exactly. "
                 "When you finish, write .meld-result.json as the brief instructs, then stop.")
KINDS = ("build", "review", "audit")
_ISO = "%Y-%m-%dT%H:%M:%SZ"


class DispatchError(RuntimeError):
    """A usage or environment problem, not a rule refusal."""


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime(_ISO)


def _from_iso(text: str) -> datetime:
    return datetime.strptime(text, _ISO).replace(tzinfo=timezone.utc)


def git_identity(ctx, group: GroupConfig) -> tuple[str, str]:
    name = group.git_name or group.bot
    if group.git_email:
        return name, group.git_email
    user = ctx.gh.get(f"users/{group.bot}")
    return name, noreply_email(int(user["id"]), group.bot)


def tail_lines(path: Path, n: int) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])


def kill_run(rec: RunRecord, *, grace_seconds: float = 5.0, sleep=time.sleep) -> str:
    """Terminate a worker's process group, escalating to SIGKILL after the grace period."""
    if not pid_alive(rec.pid):
        return "gone"
    try:
        os.killpg(rec.pid, signal.SIGTERM)
    except ProcessLookupError:
        return "gone"
    except PermissionError:
        pass
    waited = 0.0
    while pid_alive(rec.pid) and waited < grace_seconds:
        sleep(0.1)
        waited += 0.1
    if not pid_alive(rec.pid):
        return "terminated"
    try:
        os.killpg(rec.pid, signal.SIGKILL)
    except ProcessLookupError:
        return "terminated"
    return "killed"


def _adapter(ctx, group: GroupConfig, adapter: Adapter | None) -> Adapter:
    return adapter if adapter is not None else get_adapter(group.cli)


def _worker_type(ctx, adapter: Adapter, name: str | None) -> WorkerType | None:
    if not name:
        return None
    return next((w for w in roster(ctx.repo_root, adapter.installed_skills()) if w.name == name), None)


def _worktree(ctx, group: GroupConfig, key: str) -> Path:
    return ctx.store.home / "worktrees" / group.name / key


def _log_path(ctx, group: GroupConfig, kind: str, number: int, when: datetime) -> Path:
    return ctx.store.home / "logs" / group.name / f"{kind}-{number}-{when.strftime('%Y%m%dT%H%M%SZ')}.log"


def lease_body(group: str, number: int, until: str, kind: str) -> str:
    return f"until: {until}\nrun: {group}/#{number}\nkind: {kind}"


def _launch(ctx, group: GroupConfig, adapter: Adapter, *, worktree: Path, brief: str, model: str, effort: str,
            timeout: int, kind: str, number: int, when: datetime, env: dict | None) -> tuple[int, Path]:
    (worktree / BRIEF_FILE).write_text(brief)
    (worktree / RESULT_FILE).unlink(missing_ok=True)
    log_path = _log_path(ctx, group, kind, number, when)
    spec = LaunchSpec(role="worker", model=model, effort=effort, cwd=worktree, prompt=WORKER_PROMPT, log_path=log_path,
                      timeout_seconds=timeout, env=scrub_env(os.environ if env is None else env), max_turns=ctx.cfg.worker_max_turns)
    proc = adapter.launch(spec)
    return proc.pid, log_path


def dispatch(ctx, number: int, *, kind: str = "build", adapter: Adapter | None = None, now: datetime | None = None,
             env: dict | None = None) -> dict:
    if kind not in KINDS:
        raise DispatchError(f"kind must be one of {', '.join(KINDS)}, got {kind!r}")
    _, board = build_status(ctx)
    actor = ctx.actor()
    when = _now(now)
    if kind == "build":
        return _dispatch_build(ctx, board, actor, number, adapter, when, env)
    return _dispatch_pr(ctx, board, actor, number, kind, adapter, when, env)


def _dispatch_build(ctx, board: Board, actor: Actor, number: int, adapter: Adapter | None, when: datetime, env: dict | None) -> dict:
    issue = board.issues.get(number)
    if issue is None:
        refuse(ctx, board, Refusal(Verb.DISPATCH, f"#{number}", "issue is not in the active milestone"))
    refusal = issue_refusal(board, ctx.cfg, actor, Verb.DISPATCH, issue, ctx.models)
    if refusal:
        refuse(ctx, board, refusal)
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    adp = _adapter(ctx, group, adapter)
    worker = _worker_type(ctx, adp, issue.worker)
    if worker is None:
        refuse(ctx, board, Refusal(Verb.DISPATCH, f"#{number}", f"worker type '{issue.worker}' is not in {group.name}'s roster"))
    branch = branch_name(number, issue.title)
    wt = _worktree(ctx, group, f"{number}-{slugify(issue.title)}")
    mode = "reused" if worktree_exists(ctx.repo_root, wt) else create_worktree(ctx.repo_root, wt, branch, ctx.cfg.default_branch)
    name, email = git_identity(ctx, group)
    set_identity(wt, name, email)
    exclude_paths(wt)
    brief = render_brief(role=worker, kind="build", group=group.name, model=issue.model or "", effort=issue.effort or "", issue=issue,
                         pr=None, plan_text=board.plan_text, repo_root=ctx.repo_root, worktree=wt, base_branch=ctx.cfg.default_branch)
    timeout = ctx.cfg.timeout_seconds(issue.size or "M")
    deadline = when + timedelta(seconds=timeout)
    pid, log_path = _launch(ctx, group, adp, worktree=wt, brief=brief, model=issue.model or "", effort=issue.effort or "",
                            timeout=timeout, kind="build", number=number, when=when, env=env)
    record = RunRecord(group=group.name, issue=number, kind="build", pr=None, pid=pid, started_at=_iso(when), worktree=str(wt),
                       log_path=str(log_path), model=issue.model or "", effort=issue.effort or "",
                       tier=ctx.models.tier_of(group.cli, issue.model or "") or "medium", milestone=board.milestone, alive=True,
                       collected=False, deadline=_iso(deadline), branch=branch)
    ctx.store.write_run(record)
    until = _iso(deadline + timedelta(minutes=ctx.cfg.lease_grace_minutes))
    lease = ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "LEASE", f"#{number}", None),
                                                                    lease_body(group.name, number, until, "build")))
    ctx.store.write_run(replace(record, lease_comment=int(lease["id"])))
    ctx.gh.create_comment(number, f"Meld: `{group.name}` dispatched a `{worker.name}` worker on `{issue.model}` at effort {issue.effort} "
                                  f"(size {issue.size}, {mode} worktree). Branch `{branch}`, deadline {_iso(deadline)}, lease until {until}.")
    return {"dispatched": number, "kind": "build", "pid": pid, "worktree": str(wt), "branch": branch, "deadline": _iso(deadline),
            "lease_until": until, "worktree_mode": mode}


def _dispatch_pr(ctx, board: Board, actor: Actor, number: int, kind: str, adapter: Adapter | None, when: datetime, env: dict | None) -> dict:
    target = f"#{number}"
    if not actor.is_pm:
        refuse(ctx, board, Refusal(Verb.DISPATCH, target, "only the PM may dispatch"))
    pr = board.pulls.get(number)
    if pr is None:
        refuse(ctx, board, Refusal(Verb.DISPATCH, target, "PR is not on a meld/ branch of the active repo"))
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    if kind == "review":
        blocker = pr_refusal(board, ctx.cfg, actor, Verb.APPROVE, pr)
        if blocker:
            refuse(ctx, board, Refusal(Verb.DISPATCH, target, f"cannot review: {blocker.rule}"))
    else:
        if not pr.merged:
            refuse(ctx, board, Refusal(Verb.DISPATCH, target, "audit targets a merged PR"))
        if party_of_login(ctx.cfg, pr.author) == group.name:
            refuse(ctx, board, Refusal(Verb.DISPATCH, target, "cannot audit a PR authored by own group"))
    live = [r for r in board.runs.get(group.name, {}).values() if r.alive and r.kind in ("review", "audit")]
    if len(live) >= group.review_concurrency:
        refuse(ctx, board, Refusal(Verb.DISPATCH, target, f"{group.name} is at its review concurrency cap ({len(live)}/{group.review_concurrency})"))
    existing = board.run_for(group.name, number)
    if existing is not None and (existing.alive or not existing.collected):
        refuse(ctx, board, Refusal(Verb.DISPATCH, target, "a run already exists for this PR (running or uncollected)"))
    adp = _adapter(ctx, group, adapter)
    role_name = "reviewer" if kind == "review" else "auditor"
    worker = _worker_type(ctx, adp, role_name)
    if worker is None:
        refuse(ctx, board, Refusal(Verb.DISPATCH, target, f"worker type '{role_name}' is not in {group.name}'s roster"))
    wt = _worktree(ctx, group, f"{kind}-{number}")
    if worktree_exists(ctx.repo_root, wt):
        remove_worktree(ctx.repo_root, wt)
    checkout_pr_head(ctx.repo_root, wt, pr.head_ref)
    name, email = git_identity(ctx, group)
    set_identity(wt, name, email)
    exclude_paths(wt)
    model, effort = group.orchestrator.model, group.orchestrator.effort
    brief = render_brief(role=worker, kind=kind, group=group.name, model=model, effort=effort, issue=None, pr=pr,
                         plan_text=board.plan_text, repo_root=ctx.repo_root, worktree=wt, base_branch=ctx.cfg.default_branch)
    timeout = ctx.cfg.timeout_seconds("M")
    deadline = when + timedelta(seconds=timeout)
    pid, log_path = _launch(ctx, group, adp, worktree=wt, brief=brief, model=model, effort=effort, timeout=timeout, kind=kind,
                            number=number, when=when, env=env)
    record = RunRecord(group=group.name, issue=number, kind=kind, pr=number, pid=pid, started_at=_iso(when), worktree=str(wt),
                       log_path=str(log_path), model=model, effort=effort, tier=ctx.models.tier_of(group.cli, model) or "medium",
                       milestone=board.milestone, alive=True, collected=False, deadline=_iso(deadline), branch=pr.head_ref)
    ctx.store.write_run(record)
    ctx.gh.create_comment(number, f"Meld: `{group.name}` dispatched a `{worker.name}` worker on `{model}` at effort {effort} "
                                  f"against head `{pr.head_sha[:7]}`. Deadline {_iso(deadline)}.")
    return {"dispatched": number, "kind": kind, "pid": pid, "worktree": str(wt), "branch": pr.head_ref, "deadline": _iso(deadline),
            "lease_until": None, "worktree_mode": "fresh"}
