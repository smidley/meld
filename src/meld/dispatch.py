"""Dispatch, tick, collect, reap: the only module that spawns workers or touches worktrees."""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import time
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Sequence

from meld.actions import BLOCKED, IN_PROGRESS, READY, refuse, release
from meld.adapters import Adapter, LaunchSpec, scrub_env
from meld.adapters.base import SKILL_NAME_RE
from meld.board import Board, RunRecord
from meld.brief import BRIEF_FILE, FETCH_FAILED_NOTE, RESULT_FILE, ResultError, WorkerResult, read_result, recall_snippets, render_brief, select_skills
from meld.config import GroupConfig, Party
from meld.gitops import (
    GitError,
    branch_name,
    checkout_pr_head,
    checkout_sha,
    commits_ahead,
    create_worktree,
    delete_local_branch,
    detach_head,
    exclude_paths,
    git,
    head_sha,
    noreply_email,
    push_branch,
    remove_worktree,
    slugify,
    worktree_exists,
)
from meld.grammar import Header, compose
from meld.housekeeping import ci_red_sweep, flag_ignored_comments, off_protocol_sweep
from meld.notifier import notify
from meld.results import cap_body, store_result
from meld.roster import WorkerType, roster
from meld.rules import Actor, Refusal, Verb, issue_refusal, pr_refusal, reap_rule, review_concern
from meld.state import pid_alive
from meld.status import build_status

WORKER_PROMPT = ("Read .meld-brief.md in the current directory and carry it out exactly. "
                 "When you finish, write .meld-result.json as the brief instructs, then stop.")
KINDS = ("build", "review", "audit")
REVIEW_WORKERS = {"review": ("reviewer", "security"), "audit": ("auditor",)}
_ISO = "%Y-%m-%dT%H:%M:%SZ"
IN_REVIEW = "meld:in-review"
NEEDS_HUMAN = "meld:needs-human"
# Every form GitHub links: keyword, optional whitespace, optional colon, optional whitespace, then
# `#<n>` or `<owner>/<repo>#<n>`. Superset on purpose (a missing separator is neutralised too).
_CLOSER_RE = re.compile(r"(?i)\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b(\s*:?\s*)((?:[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+)?#\d+)")
_BACKTICKS_RE = re.compile(r"`+")
_MEMORY_MARKER_RE = re.compile(r"(?im)^([ \t]*meld-memory):")


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


def neutralise_closers(text: str) -> str:
    """Keep a worker's words but break GitHub's closing-keyword syntax ("fixes #7" -> "fixes `#7`", "Closes: #9",
    "resolved acme/widgets#12" -> "resolved `acme/widgets#12`"), so the only line of a PR body that can close an
    issue is the framework's own `Closes #<issue>`."""
    return _CLOSER_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}`{m.group(3)}`", text or "")


def neutralise_memory_markers(text: str) -> str:
    """Break a `meld-memory:` line in worker-provided text ("meld-memory :"), so the only marker a PR body can carry is the
    one `meld decide`/`meld remember` write themselves. Belt and braces: `PullRequest.memory` also requires a `meld/memory-*`
    branch, which no build PR is ever on."""
    return _MEMORY_MARKER_RE.sub(r"\1 :", text or "")


def _pr_body_text(text: str) -> str:
    """Worker text bound for a PR body: no closing keywords, no memory markers."""
    return neutralise_memory_markers(neutralise_closers(text))


def fenced(text: str) -> str:
    """Wrap text in a code fence longer than any run of backticks inside it, so a log line of ``` cannot end the block
    and let the rest of a worker's output render as markdown."""
    longest = max((len(m.group(0)) for m in _BACKTICKS_RE.finditer(text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text}\n{fence}"


def _leads_own_group(pid: int) -> bool:
    """Workers start in their own session, so a live worker's pid is its process-group id. A reused pid that leads
    no group is somebody else's process and must not be signalled."""
    try:
        return os.getpgid(pid) == pid
    except (ProcessLookupError, PermissionError):
        return False


def kill_run(rec: RunRecord, *, grace_seconds: float = 5.0, sleep=time.sleep) -> str:
    """Terminate a worker's process group, escalating to SIGKILL after the grace period."""
    if not pid_alive(rec.pid) or not _leads_own_group(rec.pid):
        return "gone"
    try:
        os.killpg(rec.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):  # PermissionError: the pid now belongs to another user's process
        return "gone"
    waited = 0.0
    while pid_alive(rec.pid) and waited < grace_seconds:
        sleep(0.1)
        waited += 0.1
    if not pid_alive(rec.pid):
        return "terminated"
    try:
        os.killpg(rec.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
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


# Git settings forced on every worker at the command-line level (they also override the same keys in the
# clone's own config): no credential helpers, no commit signing with the human's key. Hooks are NOT forced
# off here: the human's global hooksPath is already unreachable through the empty GIT_CONFIG_GLOBAL, and a
# product's own repo-local hooks (linters, commit checks) should run for a worker as they would for anyone.
_WORKER_GIT_CONFIG = (("credential.helper", ""), ("commit.gpgsign", "false"))


def worker_env(base: Mapping[str, str], *, identity: tuple[str, str], gh_config_dir: Path, git_config_global: Path) -> dict[str, str]:
    """The environment a worker runs in.

    Scrubbed of tokens and party identity (`scrub_env`), then: the group's bot as git author and
    committer through GIT_AUTHOR_*/GIT_COMMITTER_* -- never `git config` in the worktree, because a
    linked worktree shares the clone's .git/config, so one group would relabel every other group's
    commits and the human's own; `gh` pointed at an empty config directory so it cannot borrow the
    human's login; git pointed at an empty global config file with the system config disabled, so the
    human's `url.<base>.insteadOf` (which could route the HTTPS origin over host SSH keys),
    `commit.gpgsign`/`user.signingkey`, `core.hooksPath`, and credential helpers are never read at
    all -- the clone's own config still applies, since the worktree needs its remote; and, as
    belt-and-braces, _WORKER_GIT_CONFIG appended to any GIT_CONFIG_* list the parent already set,
    with terminal prompts disabled so a push or fetch that needs credentials fails instead of asking.
    """
    env = scrub_env(base)
    name, email = identity
    env.update({"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email,
                "GH_CONFIG_DIR": str(gh_config_dir), "GIT_CONFIG_GLOBAL": str(git_config_global), "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0"})
    try:
        n = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        n = 0
    for key, value in _WORKER_GIT_CONFIG:
        env[f"GIT_CONFIG_KEY_{n}"], env[f"GIT_CONFIG_VALUE_{n}"] = key, value
        n += 1
    env["GIT_CONFIG_COUNT"] = str(n)
    return env


def _worker_scratch(ctx, group: GroupConfig, number: int) -> tuple[Path, Path]:
    """Fresh per-run files outside the worktree (so they are never committed): an empty GH_CONFIG_DIR and an
    empty file to stand in for the global gitconfig. Both are recreated on every launch so nothing a previous
    worker wrote there carries over."""
    gh_dir = ctx.store.home / "runs" / group.name / f"{number}-gh"
    shutil.rmtree(gh_dir, ignore_errors=True)
    gh_dir.mkdir(parents=True)
    gitconfig = ctx.store.home / "runs" / group.name / f"{number}-gitconfig"
    gitconfig.write_text("")
    return gh_dir, gitconfig


def _launch(ctx, group: GroupConfig, adapter: Adapter, *, worktree: Path, brief: str, model: str, effort: str,
            timeout: int, kind: str, number: int, when: datetime, env: dict | None, identity: tuple[str, str]) -> tuple[int, Path]:
    (worktree / BRIEF_FILE).write_text(brief)
    (worktree / RESULT_FILE).unlink(missing_ok=True)
    log_path = _log_path(ctx, group, kind, number, when)
    gh_dir, gitconfig = _worker_scratch(ctx, group, number)
    spec = LaunchSpec(role="worker", model=model, effort=effort, cwd=worktree, prompt=WORKER_PROMPT, log_path=log_path,
                      timeout_seconds=timeout, max_turns=ctx.cfg.worker_max_turns,
                      env=worker_env(os.environ if env is None else env, identity=identity, gh_config_dir=gh_dir, git_config_global=gitconfig))
    try:
        proc = adapter.launch(spec)
    except OSError as e:
        raise DispatchError(f"cannot start {adapter.name}: {e}") from e
    return proc.pid, log_path


MATERIALISED_MANIFEST = ".meld-materialised.json"


def _read_materialised_manifest(path: Path) -> list[str]:
    """The skill names `materialise_skills` copied into this directory on a previous run, or `[]` when the
    manifest is missing, unreadable, or not a JSON list of strings. Corrupt or absent state must never block
    a dispatch -- it only costs a stale copy staying around one extra run."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) and all(isinstance(n, str) for n in data) else []


def _tracked_in_worktree(worktree: Path, relpath: str) -> bool:
    """True when `relpath` is tracked by git on the branch or commit this worktree has checked out: a file the
    product itself committed there, never meld's own copy to write or prune."""
    try:
        git(["ls-files", "--error-unmatch", "--", relpath], worktree)
        return True
    except GitError:
        return False


def _drop_symlinks(directory: str, names: list[str]) -> set[str]:
    """`shutil.copytree`'s `ignore` callback: never copy a symlink found inside a skill directory. By default
    copytree writes the link's *target* into the worktree -- content from outside the skill tree, which a worker
    then reads and could commit -- and a link that points back up its own tree makes the copy recurse until it
    fails, losing the whole skill. Dropping links keeps the copy to files the skill directory really holds."""
    return {n for n in names if Path(directory, n).is_symlink()}


def materialise_skills(worktree: Path, adapter: Adapter, skills: Sequence[WorkerType]) -> dict:
    """Copy the product's roster-listed product skills into the worktree under the adapter's project skill
    directory, so the CLI discovers them natively (decision 14). Worktree-local, and each copy excluded from git
    by its own exact path through the clone's shared info/exclude (see that call below): nothing is written under
    the host's home, and a worker cannot accidentally commit the copies.

    Idempotent across a reused worktree via a manifest of the names copied last time
    (`<target_dir>/.meld-materialised.json`): a since-deleted product skill's stale copy is pruned on the next
    run, but only a name this function itself wrote to that manifest is ever removed on that basis -- never
    anything else found under that directory. A destination already tracked by git on this worktree's
    checked-out branch is the product's own file, not meld's to make or prune: it is left alone entirely
    (never copied over, never pruned as stale, whether it is a currently-listed skill or a prune candidate)
    and reported in `skipped` instead of `made`.

    `skills` is the caller's already-filtered roster entries (`ctx.roster_entries()` filtered to
    `source == "product-skill"`), not a fresh `product_skills(repo_root)` scan: `roster()` is what drops a
    product skill whose name collides with a worker (see roster.py), so materialising straight off disk would
    copy a skill the roster itself excludes.

    Defence in depth against a hostile or malformed `skill.name` (frontmatter is free text; `scan_skills`
    already validates it at the source, but a `skills` entry could in principle be built any other way): every
    name -- including one already sitting in a previous manifest -- is re-checked against `SKILL_NAME_RE`
    before it is ever used to build a filesystem path, so a name like `../../src` can never rmtree or
    copytree anything outside `target_dir`. Every filesystem operation is wrapped in `try/except OSError`, and
    a plain file sitting where `target_dir` itself needs to be a directory (e.g. a product-committed file at
    the adapter's project skill path) is detected up front: a missing skill copy must never fail a dispatch.

    Returns `{"made": [...], "skipped": [...]}`. `made` is the relative dirs actually (re)written this run
    (e.g. `[".claude/skills/deploy-checklist"]`). `skipped` names anything left untouched or that failed, with
    the reason (e.g. `".claude/skills/deploy-checklist (tracked in the product repo; left alone)"`,
    `"../../x: not a valid skill name"`, `"deploy-checklist: PermissionError"`).
    """
    target_dir = adapter.project_skills_dir()
    if target_dir is None:
        return {"made": [], "skipped": []}
    base = worktree / target_dir
    # A product file sitting where a directory needs to go (e.g. a tracked `.claude` or `.fake` file) must
    # never crash a dispatch: walk each prefix of target_dir and bail out cleanly the moment one is a file.
    probe = worktree
    for part in Path(target_dir).parts:
        probe = probe / part
        if probe.exists() and not probe.is_dir():
            return {"made": [], "skipped": [f"{target_dir} is a file in this repo; skills not materialised"]}
    manifest_path = base / MATERIALISED_MANIFEST
    previous = [n for n in _read_materialised_manifest(manifest_path) if SKILL_NAME_RE.match(n)]
    made: list[str] = []
    skipped: list[str] = []
    valid_skills: list[WorkerType] = []
    for skill in skills:
        if skill.path is None:
            continue
        if not SKILL_NAME_RE.match(skill.name):
            skipped.append(f"{skill.name}: not a valid skill name")
            continue
        valid_skills.append(skill)
    current_names = {skill.name for skill in valid_skills}
    for name in previous:
        if name in current_names:
            continue
        relpath = f"{target_dir}/{name}"
        if _tracked_in_worktree(worktree, relpath):
            skipped.append(f"{relpath} (tracked in the product repo; left alone)")
            continue
        stale = base / name
        try:
            if stale.exists():
                shutil.rmtree(stale)
        except OSError as e:
            skipped.append(f"{name}: {type(e).__name__}")
    made_names: list[str] = []
    for skill in valid_skills:
        relpath = f"{target_dir}/{skill.name}"
        if _tracked_in_worktree(worktree, relpath):
            skipped.append(f"{relpath} (tracked in the product repo; left alone)")
            continue
        # `skill.path` from `scan_skills` is the skill's SKILL.md file, not its directory -- copy the parent.
        dest = base / skill.name
        try:
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(skill.path.parent, dest, ignore=_drop_symlinks)
        except OSError as e:
            skipped.append(f"{skill.name}: {type(e).__name__}")
            continue
        made.append(relpath)
        made_names.append(skill.name)
    if made_names or previous:
        try:
            base.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(made_names))
            # One line per path meld actually wrote, never `{target_dir}/` itself: `info/exclude` belongs to the
            # whole clone (a linked worktree shares `.git/info/exclude`), so a directory-wide pattern would hide
            # a worker's -- or the human's -- own new project skill from `git add -A` here and in every other
            # checkout of this clone. A per-name line left behind after a skill is removed is harmless.
            exclude_paths(worktree, (*(f"{target_dir}/{n}/" for n in made_names), f"{target_dir}/{MATERIALISED_MANIFEST}"))
        except OSError as e:
            skipped.append(f"{MATERIALISED_MANIFEST}: {type(e).__name__}")
    return {"made": made, "skipped": skipped}


def dispatch(ctx, number: int, *, kind: str = "build", worker: str | None = None, adapter: Adapter | None = None,
             now: datetime | None = None, env: dict | None = None) -> dict:
    if kind not in KINDS:
        raise DispatchError(f"kind must be one of {', '.join(KINDS)}, got {kind!r}")
    if kind == "build" and worker:
        raise DispatchError("--worker applies to review runs only")
    _, board = build_status(ctx)
    actor = ctx.actor()
    when = _now(now)
    if kind == "build":
        return _dispatch_build(ctx, board, actor, number, adapter, when, env)
    return _dispatch_pr(ctx, board, actor, number, kind, adapter, when, env, worker)


def _recall_for_brief(ctx, text: str) -> tuple[list[str], str | None]:
    """recall_snippets at origin/<default>, refreshing that ref first: the checkout reaching this call cannot be
    trusted to have it current on its own -- a reused build worktree skips create_worktree's fetch entirely (the
    `mode == "reused"` branch in _dispatch_build never calls it), and checkout_pr_head only fetches the PR's own
    branch, never the default branch. A fetch failure (e.g. offline) must never fail a dispatch: it falls back to
    the working copy and returns a note for the caller to fold into the dispatch result instead of raising."""
    try:
        git(["fetch", "--quiet", "origin", ctx.cfg.default_branch], ctx.repo_root)
    except GitError:
        return recall_snippets(ctx.repo_root, text), FETCH_FAILED_NOTE
    return recall_snippets(ctx.repo_root, text, ref=f"origin/{ctx.cfg.default_branch}"), None


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
    skills_result = materialise_skills(wt, adp, [w for w in ctx.roster_entries() if w.source == "product-skill"])
    recall_hits, recall_note = _recall_for_brief(ctx, f"{issue.title}\n{issue.body}")
    brief = render_brief(role=worker, kind="build", group=group.name, model=issue.model or "", effort=issue.effort or "", issue=issue,
                         pr=None, plan_text=board.plan_text, repo_root=ctx.repo_root, worktree=wt, base_branch=ctx.cfg.default_branch,
                         skills=select_skills(f"{issue.title}\n{issue.body}",
                                              [w for w in ctx.roster_entries() if w.source in ("skill", "product-skill")], pinned=issue.skills),
                         recall=recall_hits, project_skills_dir=adp.project_skills_dir(),
                         scope=issue.scope, may_delete=issue.may_delete, authorized=issue.held)
    timeout = ctx.cfg.timeout_seconds(issue.size or "M")
    deadline = when + timedelta(seconds=timeout)
    pid, log_path = _launch(ctx, group, adp, worktree=wt, brief=brief, model=issue.model or "", effort=issue.effort or "",
                            timeout=timeout, kind="build", number=number, when=when, env=env, identity=git_identity(ctx, group))
    record = RunRecord(group=group.name, issue=number, kind="build", pr=None, pid=pid, started_at=_iso(when), worktree=str(wt),
                       log_path=str(log_path), model=issue.model or "", effort=issue.effort or "",
                       tier=ctx.models.tier_of(group.cli, issue.model or "") or "medium", milestone=board.milestone, alive=True,
                       collected=False, deadline=_iso(deadline), branch=branch, worker=worker.name)
    ctx.store.write_run(record)
    until = _iso(deadline + timedelta(minutes=ctx.cfg.lease_grace_minutes))
    lease = ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "LEASE", f"#{number}", None),
                                                                    lease_body(group.name, number, until, "build")))
    ctx.store.write_run(replace(record, lease_comment=int(lease["id"])))
    ctx.gh.create_comment(number, f"Meld: `{group.name}` dispatched a `{worker.name}` worker on `{issue.model}` at effort {issue.effort} "
                                  f"(size {issue.size}, {mode} worktree). Branch `{branch}`, deadline {_iso(deadline)}, lease until {until}.")
    result = {"dispatched": number, "kind": "build", "pid": pid, "worktree": str(wt), "branch": branch, "deadline": _iso(deadline),
              "lease_until": until, "worktree_mode": mode, "skills_materialised": skills_result}
    if recall_note:
        result["recall_note"] = recall_note
    return result


def _dispatch_pr(ctx, board: Board, actor: Actor, number: int, kind: str, adapter: Adapter | None, when: datetime, env: dict | None,
                 worker_name: str | None) -> dict:
    target = f"#{number}"
    pr = board.pulls.get(number)
    if pr is None:
        refuse(ctx, board, Refusal(Verb.DISPATCH, target, "PR is not on a meld/ branch of the active repo"))
    blocker = pr_refusal(board, ctx.cfg, actor, Verb.REVIEW if kind == "review" else Verb.AUDIT, pr)
    if blocker:
        refuse(ctx, board, Refusal(Verb.DISPATCH, target, f"cannot {kind}: {blocker.rule}"))
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    role_name = worker_name or REVIEW_WORKERS[kind][0]
    if role_name not in REVIEW_WORKERS[kind]:
        refuse(ctx, board, Refusal(Verb.DISPATCH, target, f"worker type '{role_name}' cannot run {'an' if kind[0] in 'aeiou' else 'a'} {kind}; "
                                                          f"use one of: {', '.join(REVIEW_WORKERS[kind])}"))
    adp = _adapter(ctx, group, adapter)
    worker = _worker_type(ctx, adp, role_name)
    if worker is None:
        refuse(ctx, board, Refusal(Verb.DISPATCH, target, f"worker type '{role_name}' is not in {group.name}'s roster"))
    wt = _worktree(ctx, group, f"{kind}-{number}")
    if worktree_exists(ctx.repo_root, wt):
        remove_worktree(ctx.repo_root, wt)
    at_merge = kind == "audit" and bool(pr.merge_commit_sha)
    if at_merge:
        checkout_sha(ctx.repo_root, wt, pr.merge_commit_sha)  # type: ignore[arg-type]
    else:
        checkout_pr_head(ctx.repo_root, wt, pr.head_ref)
    exclude_paths(wt)
    skills_result = materialise_skills(wt, adp, [w for w in ctx.roster_entries() if w.source == "product-skill"])
    model, effort = group.orchestrator.model, group.orchestrator.effort
    concern = review_concern(ctx.cfg, pr, group.name) if kind == "review" and role_name == "reviewer" else None
    recall_hits, recall_note = _recall_for_brief(ctx, pr.title)
    linked = board.issues.get(pr.issue_number) if pr.issue_number is not None else None
    brief = render_brief(role=worker, kind=kind, group=group.name, model=model, effort=effort, issue=None, pr=pr,
                         plan_text=board.plan_text, repo_root=ctx.repo_root, worktree=wt, base_branch=ctx.cfg.default_branch, concern=concern,
                         skills=select_skills(f"{pr.title}\n{pr.body}",
                                              [w for w in ctx.roster_entries() if w.source in ("skill", "product-skill")]),
                         recall=recall_hits, project_skills_dir=adp.project_skills_dir(),
                         scope=linked.scope if linked else (), may_delete=linked.may_delete if linked else ())
    timeout = ctx.cfg.timeout_seconds("M")
    deadline = when + timedelta(seconds=timeout)
    pid, log_path = _launch(ctx, group, adp, worktree=wt, brief=brief, model=model, effort=effort, timeout=timeout, kind=kind,
                            number=number, when=when, env=env, identity=git_identity(ctx, group))
    record = RunRecord(group=group.name, issue=number, kind=kind, pr=number, pid=pid, started_at=_iso(when), worktree=str(wt),
                       log_path=str(log_path), model=model, effort=effort, tier=ctx.models.tier_of(group.cli, model) or "medium",
                       milestone=board.milestone, alive=True, collected=False, deadline=_iso(deadline), branch=pr.head_ref,
                       head_sha=pr.head_sha, worker=worker.name)
    ctx.store.write_run(record)
    where = f"merge commit `{pr.merge_commit_sha[:7]}`" if at_merge else f"head `{pr.head_sha[:7]}`"   # type: ignore[index]
    article = "an" if worker.name[0] in "aeiou" else "a"
    ctx.gh.create_comment(number, f"Meld: `{group.name}` dispatched {article} `{worker.name}` worker on `{model}` at effort {effort} "
                                  f"against {where}. Deadline {_iso(deadline)}.")
    result = {"dispatched": number, "kind": kind, "pid": pid, "worktree": str(wt), "branch": pr.head_ref, "deadline": _iso(deadline),
              "lease_until": None, "worktree_mode": "fresh", "skills_materialised": skills_result}
    if recall_note:
        result["recall_note"] = recall_note
    return result


def tick(ctx, *, now: datetime | None = None, sleep=time.sleep, env: Mapping[str, str] | None = None, post=None) -> dict:
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
        # release() itself would also post a framework RELEASED note; announce=False suppresses that so this one
        # event (an expired lease reclaimed by tick) writes exactly one framework comment -- the EXPIRED one below,
        # which names the lease that expired (derive_leases treats both verbs as closers, so nothing is lost).
        release(ctx, n, announce=False)
        ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "EXPIRED", f"#{n}", None),
                                                                f"Released #{n}: lease by {lease.group} expired at {lease.until} (now {now_iso})."))
        out["expired"].append(n)
    out["resumed"] = []
    for issue in sorted(board.issues.values(), key=lambda i: i.number):
        if issue.status != "needs_human" or not board.needs_human_answered(issue.number):
            continue
        # Spec 14: the human's PROVIDED/RESOLVED is the record; the label just has to follow it. Any PM's tick does this,
        # idempotently (the second one finds no label), and posts nothing.
        _swap_labels(ctx, issue.number, IN_PROGRESS if issue.claimed_by else READY, (NEEDS_HUMAN,))
        out["resumed"].append(issue.number)
    notified = notify(ctx, board, when, post=post, env=env)
    out["notified"] = notified["notified"]
    out["notify_errors"] = notified.get("errors", [])   # a failed Slack post or state write is reported here, never raised
    out["ci_red"] = ci_red_sweep(ctx, board, group)
    out["ignored"] = flag_ignored_comments(ctx, board, group)
    out["off_protocol"] = off_protocol_sweep(ctx, board, group)
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
        return _collect_build(ctx, board, group, rec, issue)
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


def _collect_build(ctx, board: Board, group: GroupConfig, rec: RunRecord, issue) -> dict:
    number = rec.issue
    if issue.status != "in_progress" or issue.claimed_by != group.name:
        # The claim moved on while the worker ran (our lease expired and another group re-claimed, a human
        # released or re-labelled, or the issue is contested): the run is over, but nothing of it may reach GitHub.
        # The worktree stays for inspection, detached so the branch name is free for whoever now holds the issue
        # (git refuses to check out a branch that another worktree of the same clone has checked out).
        holder = ", ".join(issue.claimants) or "nobody"
        wt = Path(rec.worktree)
        if wt.is_dir():
            detach_head(wt)
        ctx.store.write_run(replace(rec, collected=True))
        return {"collected": number, "outcome": "superseded",
                "reason": f"issue is {issue.status} and claimed by {holder}; {group.name} no longer holds it"}
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
                                      f"Last {ctx.cfg.worker_log_tail_lines} log lines:\n\n{fenced(tail)}")
        ctx.store.write_run(replace(rec, collected=True))
        return {"collected": number, "outcome": "failed", "reason": reason}
    assert result is not None
    push_branch(wt, rec.branch, ctx.gh.token)
    # Worker text enters the PR body: strip its power to close arbitrary issues (only our Closes line may).
    body = f"Closes #{number}\n\n{_pr_body_text(result.summary)}\n\nverified-by: {_pr_body_text(result.verified_by)}"
    existing = board.pr_for_issue(number)
    if existing is not None and existing.state == "open":
        ctx.gh.update_pull(existing.number, body)
        pr_number = existing.number
    else:
        pr_number = int(ctx.gh.create_pull(issue.title, rec.branch, ctx.cfg.default_branch, body)["number"])
    _swap_labels(ctx, number, IN_REVIEW, (IN_PROGRESS,))
    sha = head_sha(wt)
    finish = (f"Meld: `{group.name}` worker finished: {len(commits)} commit(s) {commits[0][:7]}..{commits[-1][:7]}, PR #{pr_number}.\n\n"
              f"verified-by: {result.verified_by}")
    if result.skills_used:
        finish += f"\nskills used: {', '.join(result.skills_used)}"
    ctx.gh.create_comment(number, cap_body(finish))
    ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "REVIEW-REQUEST", f"#{pr_number}", None),
                                                            f"Issue #{number}, head {sha[:7]}. Please dispatch a reviewer."))
    ctx.store.write_run(replace(rec, collected=True, pr=pr_number))
    return {"collected": number, "outcome": "done", "pr": pr_number, "commits": len(commits)}


def _collect_verdict(ctx, group: GroupConfig, rec: RunRecord, when: datetime) -> dict:
    result, reason = _read_outcome(rec)
    wt = Path(rec.worktree)
    worktree_sha = head_sha(wt) if wt.exists() else ""
    payload = {"kind": rec.kind, "pr": rec.pr, "worker": rec.worker, "head_sha": rec.head_sha or worktree_sha, "worktree_sha": worktree_sha,
               "collected_at": _iso(when), "timed_out": rec.timed_out, "reason": reason,
               "result": asdict(result) if result else None,
               "log_tail": tail_lines(Path(rec.log_path), ctx.cfg.worker_log_tail_lines)}
    path = store_result(ctx.store.home, group.name, rec.kind, rec.issue, payload, _iso(when))
    ctx.store.write_run(replace(rec, collected=True))
    return {"collected": rec.issue, "kind": rec.kind, "stored": str(path), "verdict": result.verdict if result else None}


def reap(ctx, number: int) -> dict:
    """Remove a finished run's worktree and archive its record once its work is merged or closed."""
    _, board = build_status(ctx)
    actor = ctx.actor()
    target = f"#{number}"
    issue = board.issues.get(number)
    pr = board.pulls.get(number)
    rule = reap_rule(board, ctx.cfg, actor, number, issue, pr)
    if rule:
        refuse(ctx, board, Refusal(Verb.REAP, target, rule))
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    rec = board.run_for(group.name, number)
    assert rec is not None  # reap_rule guarantees it
    wt = Path(rec.worktree)
    removed = False
    if worktree_exists(ctx.repo_root, wt) or wt.exists():
        remove_worktree(ctx.repo_root, wt)
        removed = True
    if rec.kind == "build" and rec.branch:
        delete_local_branch(ctx.repo_root, rec.branch)
    archived = ctx.store.archive_run(group.name, number)
    return {"reaped": number, "worktree_removed": removed, "archived": str(archived)}
