"""Dispatch, tick, collect, reap: the only module that spawns workers or touches worktrees."""
from __future__ import annotations

import json
import os
import shutil
import signal
import time
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from meld.actions import BLOCKED, IN_PROGRESS, refuse, release
from meld.adapters import Adapter, LaunchSpec, scrub_env
from meld.board import Board, RunRecord
from meld.brief import BRIEF_FILE, RESULT_FILE, ResultError, WorkerResult, read_result, render_brief
from meld.config import GroupConfig, Party
from meld.gitops import (
    branch_name,
    checkout_pr_head,
    commits_ahead,
    create_worktree,
    delete_local_branch,
    exclude_paths,
    head_sha,
    noreply_email,
    push_branch,
    remove_worktree,
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
IN_REVIEW = "meld:in-review"
NEEDS_HUMAN = "meld:needs-human"


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
    if adapter is not None:
        return adapter
    adp = ctx.adapter()
    if adp is None:
        raise DispatchError("no adapter for a human party")
    return adp


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


def worker_env(base: Mapping[str, str], *, identity: tuple[str, str], gh_config_dir: Path) -> dict[str, str]:
    """The environment a worker runs in.

    Scrubbed of tokens and party identity (`scrub_env`), then: the group's bot as git author and
    committer through GIT_AUTHOR_*/GIT_COMMITTER_* -- never `git config` in the worktree, because a
    linked worktree shares the clone's .git/config, so one group would relabel every other group's
    commits and the human's own; `gh` pointed at an empty config directory so it cannot borrow the
    human's login; git's credential helpers reset (an empty `credential.helper` clears the list) and
    terminal prompts disabled so a push or fetch that needs credentials fails instead of asking.
    """
    env = scrub_env(base)
    name, email = identity
    env.update({"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email,
                "GH_CONFIG_DIR": str(gh_config_dir), "GIT_TERMINAL_PROMPT": "0"})
    try:
        n = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        n = 0
    env.update({"GIT_CONFIG_COUNT": str(n + 1), f"GIT_CONFIG_KEY_{n}": "credential.helper", f"GIT_CONFIG_VALUE_{n}": ""})
    return env


def _gh_config_dir(ctx, group: GroupConfig, number: int) -> Path:
    """A fresh, empty GH_CONFIG_DIR for this run (outside the worktree, so it is never committed)."""
    d = ctx.store.home / "runs" / group.name / f"{number}-gh"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


def _launch(ctx, group: GroupConfig, adapter: Adapter, *, worktree: Path, brief: str, model: str, effort: str,
            timeout: int, kind: str, number: int, when: datetime, env: dict | None, identity: tuple[str, str]) -> tuple[int, Path]:
    (worktree / BRIEF_FILE).write_text(brief)
    (worktree / RESULT_FILE).unlink(missing_ok=True)
    log_path = _log_path(ctx, group, kind, number, when)
    spec = LaunchSpec(role="worker", model=model, effort=effort, cwd=worktree, prompt=WORKER_PROMPT, log_path=log_path,
                      timeout_seconds=timeout, max_turns=ctx.cfg.worker_max_turns,
                      env=worker_env(os.environ if env is None else env, identity=identity, gh_config_dir=_gh_config_dir(ctx, group, number)))
    try:
        proc = adapter.launch(spec)
    except OSError as e:
        raise DispatchError(f"cannot start {adapter.name}: {e}") from e
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
    exclude_paths(wt)
    brief = render_brief(role=worker, kind="build", group=group.name, model=issue.model or "", effort=issue.effort or "", issue=issue,
                         pr=None, plan_text=board.plan_text, repo_root=ctx.repo_root, worktree=wt, base_branch=ctx.cfg.default_branch)
    timeout = ctx.cfg.timeout_seconds(issue.size or "M")
    deadline = when + timedelta(seconds=timeout)
    pid, log_path = _launch(ctx, group, adp, worktree=wt, brief=brief, model=issue.model or "", effort=issue.effort or "",
                            timeout=timeout, kind="build", number=number, when=when, env=env, identity=git_identity(ctx, group))
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
    exclude_paths(wt)
    model, effort = group.orchestrator.model, group.orchestrator.effort
    brief = render_brief(role=worker, kind=kind, group=group.name, model=model, effort=effort, issue=None, pr=pr,
                         plan_text=board.plan_text, repo_root=ctx.repo_root, worktree=wt, base_branch=ctx.cfg.default_branch)
    timeout = ctx.cfg.timeout_seconds("M")
    deadline = when + timedelta(seconds=timeout)
    pid, log_path = _launch(ctx, group, adp, worktree=wt, brief=brief, model=model, effort=effort, timeout=timeout, kind=kind,
                            number=number, when=when, env=env, identity=git_identity(ctx, group))
    record = RunRecord(group=group.name, issue=number, kind=kind, pr=number, pid=pid, started_at=_iso(when), worktree=str(wt),
                       log_path=str(log_path), model=model, effort=effort, tier=ctx.models.tier_of(group.cli, model) or "medium",
                       milestone=board.milestone, alive=True, collected=False, deadline=_iso(deadline), branch=pr.head_ref)
    ctx.store.write_run(record)
    ctx.gh.create_comment(number, f"Meld: `{group.name}` dispatched a `{worker.name}` worker on `{model}` at effort {effort} "
                                  f"against head `{pr.head_sha[:7]}`. Deadline {_iso(deadline)}.")
    return {"dispatched": number, "kind": kind, "pid": pid, "worktree": str(wt), "branch": pr.head_ref, "deadline": _iso(deadline),
            "lease_until": None, "worktree_mode": "fresh"}


def tick(ctx, *, now: datetime | None = None, sleep=time.sleep) -> dict:
    """PM housekeeping: kill overdue workers, create missing leases, release other groups' expired leases."""
    _, board = build_status(ctx)
    actor = ctx.actor()
    if not actor.is_pm:
        raise DispatchError(f"tick is a PM verb; you are {actor.party.label}")
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    when = _now(now)
    now_iso = _iso(when)
    out: dict = {"checked": 0, "killed": [], "leases_created": [], "expired": []}
    for rec in list(board.runs.get(group.name, {}).values()):
        if not rec.alive:
            continue
        out["checked"] += 1
        if rec.deadline and rec.deadline < now_iso:
            how = kill_run(rec, sleep=sleep)
            ctx.store.write_run(replace(rec, alive=False, timed_out=True))
            out["killed"].append({"issue": rec.issue, "how": how})
        elif rec.kind == "build" and rec.lease_comment is None:
            base = _from_iso(rec.deadline) if rec.deadline else when
            until = _iso(base + timedelta(minutes=ctx.cfg.lease_grace_minutes))
            lease = ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "LEASE", f"#{rec.issue}", None),
                                                                            lease_body(group.name, rec.issue, until, rec.kind)))
            ctx.store.write_run(replace(rec, lease_comment=int(lease["id"])))
            out["leases_created"].append(rec.issue)
    for n, lease in sorted(board.leases.items()):
        issue = board.issues.get(n)
        if lease.group == group.name or issue is None or issue.status != "in_progress" or not (board.now and lease.until < now_iso):
            continue
        release(ctx, n)
        ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "EXPIRED", f"#{n}", None),
                                                                f"Released #{n}: lease by {lease.group} expired at {lease.until} (now {now_iso})."))
        out["expired"].append(n)
    return out


def _swap_labels(ctx, number: int, add: str, remove: tuple[str, ...]) -> None:
    ctx.gh.add_labels(number, [add])
    for label in remove:
        ctx.gh.remove_label(number, label)


def collect(ctx, number: int, *, now: datetime | None = None) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    target = f"#{number}"
    if not actor.is_pm:
        refuse(ctx, board, Refusal(Verb.COLLECT, target, "only the PM may collect"))
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    rec = board.run_for(group.name, number)
    if rec is None:
        refuse(ctx, board, Refusal(Verb.COLLECT, target, f"no run record for {target} in {group.name}"))
    if rec.alive:
        refuse(ctx, board, Refusal(Verb.COLLECT, target, "worker is still running"))
    if rec.collected:
        refuse(ctx, board, Refusal(Verb.COLLECT, target, "run already collected"))
    when = _now(now)
    if rec.kind == "build":
        issue = board.issues.get(number)
        if issue is None:
            refuse(ctx, board, Refusal(Verb.COLLECT, target, "issue is not in the active milestone"))
        refusal = issue_refusal(board, ctx.cfg, actor, Verb.COLLECT, issue, ctx.models)
        if refusal:
            refuse(ctx, board, refusal)
        return _collect_build(ctx, board, group, rec, issue, when)
    return _collect_verdict(ctx, group, rec, when)


def _read_outcome(rec: RunRecord) -> tuple[WorkerResult | None, str | None]:
    """Return (result, reason_if_not_done)."""
    if rec.timed_out:
        return None, f"timed out at {rec.deadline}"
    try:
        result = read_result(Path(rec.worktree))
    except ResultError as e:
        return None, str(e)
    if result is None:
        return None, f"exited without writing {RESULT_FILE}"
    if result.outcome == "failed":
        return result, f"worker reported failed: {result.summary}"
    return result, None


def _collect_build(ctx, board: Board, group: GroupConfig, rec: RunRecord, issue, when: datetime) -> dict:
    number = rec.issue
    wt = Path(rec.worktree)
    result, reason = _read_outcome(rec)
    tail = tail_lines(Path(rec.log_path), ctx.cfg.worker_log_tail_lines)
    if reason is None and result is not None and result.outcome == "blocked":
        _swap_labels(ctx, number, NEEDS_HUMAN, (IN_PROGRESS,))
        ask = result.needs_human or result.summary or "The worker did not say what it needs."
        ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "NEEDS-HUMAN", f"#{number}", None),
                                                                f"{ask}\n\nWorker summary: {result.summary}\nverified-by: {result.verified_by}"))
        ctx.gh.create_comment(number, f"Meld: `{group.name}` worker is blocked and needs a human: {ask}")
        ctx.store.write_run(replace(rec, collected=True))
        return {"collected": number, "outcome": "blocked", "needs_human": ask}
    commits: list[str] = []
    if reason is None:
        commits = commits_ahead(wt, f"origin/{ctx.cfg.default_branch}")
        if not commits:
            reason = "worker reported done but made no commits"
    if reason is not None:
        _swap_labels(ctx, number, BLOCKED, (IN_PROGRESS,))
        summary = f"\n\nWorker summary: {result.summary}" if result else ""
        ctx.gh.create_comment(number, f"Meld: `{group.name}` worker did not complete #{number}: {reason}.{summary}\n\n"
                                      f"Last {ctx.cfg.worker_log_tail_lines} log lines:\n\n```\n{tail}\n```")
        ctx.store.write_run(replace(rec, collected=True))
        return {"collected": number, "outcome": "failed", "reason": reason}
    assert result is not None
    push_branch(wt, rec.branch, ctx.gh.token)
    body = f"Closes #{number}\n\n{result.summary}\n\nverified-by: {result.verified_by}"
    existing = board.pr_for_issue(number)
    if existing is not None and existing.state == "open":
        ctx.gh.update_pull(existing.number, body)
        pr_number = existing.number
    else:
        pr_number = int(ctx.gh.create_pull(issue.title, rec.branch, ctx.cfg.default_branch, body)["number"])
    _swap_labels(ctx, number, IN_REVIEW, (IN_PROGRESS,))
    sha = head_sha(wt)
    ctx.gh.create_comment(number, f"Meld: `{group.name}` worker finished: {len(commits)} commit(s) {commits[0][:7]}..{commits[-1][:7]}, PR #{pr_number}.\n\n"
                                  f"verified-by: {result.verified_by}")
    ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "REVIEW-REQUEST", f"#{pr_number}", None),
                                                            f"Issue #{number}, head {sha[:7]}. Please dispatch a reviewer."))
    ctx.store.write_run(replace(rec, collected=True, pr=pr_number))
    return {"collected": number, "outcome": "done", "pr": pr_number, "commits": len(commits)}


def _collect_verdict(ctx, group: GroupConfig, rec: RunRecord, when: datetime) -> dict:
    result, reason = _read_outcome(rec)
    wt = Path(rec.worktree)
    sha = head_sha(wt) if wt.exists() else ""
    results_dir = ctx.store.home / "results" / group.name
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{rec.kind}-{rec.issue}.json"
    payload = {"kind": rec.kind, "pr": rec.pr, "head_sha": sha, "collected_at": _iso(when), "timed_out": rec.timed_out,
               "reason": reason, "result": asdict(result) if result else None,
               "log_tail": tail_lines(Path(rec.log_path), ctx.cfg.worker_log_tail_lines)}
    path.write_text(json.dumps(payload, indent=2))
    ctx.store.write_run(replace(rec, collected=True))
    return {"collected": rec.issue, "kind": rec.kind, "stored": str(path), "verdict": result.verdict if result else None}


def reap(ctx, number: int) -> dict:
    """Remove a finished run's worktree and archive its record once its work is merged or closed."""
    _, board = build_status(ctx)
    actor = ctx.actor()
    target = f"#{number}"
    if not actor.is_pm:
        refuse(ctx, board, Refusal(Verb.REAP, target, "only the PM may reap"))
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    rec = board.run_for(group.name, number)
    if rec is None:
        refuse(ctx, board, Refusal(Verb.REAP, target, f"no run record for {target} in {group.name}"))
    if rec.alive:
        refuse(ctx, board, Refusal(Verb.REAP, target, "worker is still running"))
    if not rec.collected:
        refuse(ctx, board, Refusal(Verb.REAP, target, "run has not been collected; collect first"))
    issue = board.issues.get(number)
    pr = board.pr_for_issue(number) or board.pulls.get(number)
    finished = rec.kind != "build" or (issue is not None and issue.state == "closed") or (pr is not None and (pr.merged or pr.state == "closed"))
    if not finished:
        refuse(ctx, board, Refusal(Verb.REAP, target, "issue is still open and its PR is not merged"))
    wt = Path(rec.worktree)
    removed = False
    if worktree_exists(ctx.repo_root, wt) or wt.exists():
        remove_worktree(ctx.repo_root, wt)
        removed = True
    if rec.kind == "build" and rec.branch:
        delete_local_branch(ctx.repo_root, rec.branch)
    archived = ctx.store.archive_run(group.name, number)
    return {"reaped": number, "worktree_removed": removed, "archived": str(archived)}
