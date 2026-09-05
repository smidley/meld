"""meld command-line entry point."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from meld import __version__
from meld.actions import Refused, claim as do_claim, release as do_release
from meld.adapters import UnknownAdapter, get_adapter
from meld.assemble import BoardError
from meld.audit import audit as do_audit, reopen as do_reopen
from meld.challenge import challenge as do_challenge
from meld.chat import inbox as build_inbox, run_chat
from meld.config import ConfigError, load_config
from meld.context import Context, find_config, load_context
from meld.dispatch import DispatchError, collect as do_collect, dispatch as do_dispatch, reap as do_reap, tick as do_tick
from meld.doctor import run_checks, summary
from meld.github import GitHub, GitHubError
from meld.gitops import GitError
from meld.memory import decide as do_decide, recall as do_recall, remember as do_remember
from meld.models import ModelRegistry
from meld.notifier import notify as do_notify
from meld.plan import plan_ack as do_plan_ack, plan_approve as do_plan_approve, plan_issues as do_plan_issues
from meld.posting import PostError, post as do_post
from meld.results import ResultsError
from meld.review import merge as do_merge, review as do_review
from meld.runners import run_group
from meld.setup import SetupError, setup as do_setup
from meld.state import CHANGE_KINDS, meld_home
from meld.status import build_status
from meld.tagging import tag as do_tag
from meld.waiting import wait_for_change

RUN = subprocess.run  # tests monkeypatch this with a FakeGh
ADAPTER_FOR = get_adapter


def emit(data: object, pretty: bool) -> None:
    click.echo(json.dumps(data, indent=2 if pretty else None))


@click.group()
@click.version_option(__version__, prog_name="meld")
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None,
              help="Path to .meld.toml (default: search upward from the current directory).")
@click.option("--pretty/--compact", default=True, help="Pretty-print JSON output.")
@click.pass_context
def main(ctx: click.Context, config_path: Path | None, pretty: bool) -> None:
    """Meld: multi-AI coordination over a GitHub repo."""
    ctx.obj = {"config_path": config_path, "pretty": pretty}


def _load(ctx: click.Context) -> Context:
    return _guard(lambda: load_context(ctx.obj["config_path"], run=RUN, adapter_for=ADAPTER_FOR))


def _guard(fn):
    try:
        return fn()
    except (BoardError, GitHubError, ConfigError, DispatchError, GitError, UnknownAdapter, ResultsError, SetupError) as e:
        raise click.ClickException(str(e)) from e


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Print the board and the legal actions for the calling party."""
    c = _load(ctx)
    data, _ = _guard(lambda: build_status(c))
    emit(data, ctx.obj["pretty"])


def _adapter_models(c: Context, adapter: str) -> dict:
    return {"adapter": adapter, "models": [
        {"id": s.id, "context_class": s.context_class, "efforts": list(s.efforts), "tier": s.tier}
        for s in c.models.for_adapter(adapter)
    ]}


@main.command()
@click.option("--adapter", default=None,
              help="Adapter to list (default: the calling group's CLI, or every adapter for a human).")
@click.pass_context
def models(ctx: click.Context, adapter: str | None) -> None:
    """List the models and efforts an adapter accepts."""
    c = _load(ctx)
    if adapter is None and c.party.group:
        adapter = c.cfg.groups[c.party.group].cli
    if adapter is None:
        # A human belongs to no group, so there is no adapter to assume: list them all.
        emit({"adapters": [_adapter_models(c, a) for a in c.models.adapters]}, ctx.obj["pretty"])
        return
    emit(_adapter_models(c, adapter), ctx.obj["pretty"])


@main.command()
@click.option("--timeout", type=float, default=None, help="Seconds to wait before returning unchanged (default: polling.wait_timeout_seconds).")
@click.option("--interval", type=float, default=None, help="Seconds between polls (default: polling.interval_seconds).")
@click.option("--filter", "kinds", default=None,
              help="Comma-separated change kinds that count as a change: " + ",".join(k for k in CHANGE_KINDS if k != "snapshot") + " (default: all).")
@click.pass_context
def wait(ctx: click.Context, timeout: float | None, interval: float | None, kinds: str | None) -> None:
    """Block until the board changes, then print status plus a changes list."""
    c = _load(ctx)
    t = timeout if timeout is not None else c.cfg.wait_timeout_seconds
    i = interval if interval is not None else c.cfg.poll_interval_seconds
    if i <= 0:
        raise click.BadParameter("must be greater than 0", param_hint="--interval")
    if t < 0:
        raise click.BadParameter("must not be negative", param_hint="--timeout")
    wanted: set[str] | None = None
    if kinds:
        wanted = {k.strip() for k in kinds.split(",") if k.strip()}
        unknown = sorted(wanted - set(CHANGE_KINDS))
        if unknown:
            raise click.BadParameter(f"unknown change kind(s): {', '.join(unknown)}", param_hint="--filter")
    data = _guard(lambda: wait_for_change(c, timeout=t, interval=i, kinds=wanted))
    emit(data, ctx.obj["pretty"])


def _refusal_json(e: Refused) -> dict:
    return {"refused": str(e), "verb": e.refusal.verb.value, "target": e.refusal.target, "rule": e.refusal.rule,
            "violation_posted": e.violation_posted}


def _run_action(ctx: click.Context, fn) -> None:
    try:
        emit(_guard(fn), ctx.obj["pretty"])
    except Refused as e:
        emit(_refusal_json(e), ctx.obj["pretty"])
        ctx.exit(2)
    except PostError as e:
        emit({"error": str(e)}, ctx.obj["pretty"])
        ctx.exit(2)


@main.command()
@click.argument("number", type=int)
@click.pass_context
def claim(ctx: click.Context, number: int) -> None:
    """Claim a ready issue by assigning this party (first assignment wins)."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_claim(c, number))


@main.command()
@click.argument("number", type=int)
@click.option("--force", is_flag=True, help="Human-only: release another party's claim and post a note.")
@click.pass_context
def release(ctx: click.Context, number: int, force: bool) -> None:
    """Release a claim, returning the issue to ready."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_release(c, number, force=force))


@main.command()
@click.argument("verb")
@click.argument("ref", required=False, default=None)
@click.option("--qualifier", default=None, help="Optional trailing qualifier, e.g. DISAGREE or FAIL.")
@click.option("-m", "--message", "message", default=None, help="Comment text.")
@click.option("--body-file", type=click.Path(path_type=Path, exists=True), default=None, help="Read comment text from a file.")
@click.option("--joint", is_flag=True, help="Post as [joint] after verifying agreement on REF.")
@click.pass_context
def post(ctx: click.Context, verb: str, ref: str | None, qualifier: str | None, message: str | None,
         body_file: Path | None, joint: bool) -> None:
    """Post a coordination comment: VERB [REF] with the calling party's header."""
    if message is not None and body_file is not None:
        raise click.UsageError("-m/--message and --body-file are mutually exclusive")
    c = _load(ctx)
    text = body_file.read_text() if body_file else (message or "")
    try:
        emit(_guard(lambda: do_post(c, verb.upper(), ref=ref, qualifier=qualifier, text=text, joint=joint)), ctx.obj["pretty"])
    except Refused as e:
        emit(_refusal_json(e), ctx.obj["pretty"])
        ctx.exit(2)
    except PostError as e:
        emit({"error": str(e)}, ctx.obj["pretty"])
        ctx.exit(2)


@main.command()
@click.argument("action", type=click.Choice(["open", "evidence", "retract", "uphold", "dismiss"]))
@click.argument("ref")
@click.option("-m", "--message", "message", default=None, help="The doubted claim, the evidence (with a verified-by: line), or the decision.")
@click.option("--body-file", type=click.Path(path_type=Path, exists=True), default=None, help="Read the text from a file.")
@click.pass_context
def challenge(ctx: click.Context, action: str, ref: str, message: str | None, body_file: Path | None) -> None:
    """Open a CHALLENGE on a claim, answer it with EVIDENCE, or close it; the meld:challenged label follows."""
    if message is not None and body_file is not None:
        raise click.UsageError("-m/--message and --body-file are mutually exclusive")
    c = _load(ctx)
    text = body_file.read_text() if body_file else (message or "")
    try:
        emit(_guard(lambda: do_challenge(c, action, ref, text)), ctx.obj["pretty"])
    except Refused as e:
        emit(_refusal_json(e), ctx.obj["pretty"])
        ctx.exit(2)
    except PostError as e:
        emit({"error": str(e)}, ctx.obj["pretty"])
        ctx.exit(2)


@main.command()
@click.argument("number", type=int)
@click.option("--kind", type=click.Choice(["build", "review", "audit"]), default="build", show_default=True,
              help="build: a claimed issue; review/audit: a pull request.")
@click.option("--worker", default=None, help="Review worker type: reviewer (default) or security.")
@click.pass_context
def dispatch(ctx: click.Context, number: int, kind: str, worker: str | None) -> None:
    """Spawn a headless worker in its own worktree for an issue (build) or a PR (review, audit)."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_dispatch(c, number, kind=kind, worker=worker))


@main.command()
@click.pass_context
def tick(ctx: click.Context) -> None:
    """PM housekeeping: kill overdue workers, create missing leases, release other groups' expired leases."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_tick(c))


@main.command()
@click.option("--dry-run", is_flag=True, help="Show what would be sent without posting or recording.")
@click.pass_context
def notify(ctx: click.Context, dry_run: bool) -> None:
    """Send the Slack nudge for needs-human items now (a PM's tick does this on its own)."""
    c = _load(ctx)

    def run() -> dict:
        _, board = build_status(c)
        return do_notify(c, board, datetime.now(timezone.utc), dry_run=dry_run)
    emit(_guard(run), ctx.obj["pretty"])


@main.command()
@click.argument("number", type=int)
@click.pass_context
def collect(ctx: click.Context, number: int) -> None:
    """Finish a worker run: push and open the PR, or mark the issue blocked or needs-human."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_collect(c, number))


@main.command()
@click.argument("number", type=int)
@click.pass_context
def reap(ctx: click.Context, number: int) -> None:
    """Remove a finished run's worktree and archive its record."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_reap(c, number))


@main.command()
@click.argument("number", type=int)
@click.pass_context
def review(ctx: click.Context, number: int) -> None:
    """Submit the collected reviewer verdict on a PR as a GitHub review: approve, request changes, or push the fix."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_review(c, number))


@main.command()
@click.argument("number", type=int)
@click.pass_context
def merge(ctx: click.Context, number: int) -> None:
    """Squash-merge a PR once the quorum rule, required checks, and open challenges allow it."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_merge(c, number))


@main.command()
@click.argument("number", type=int)
@click.pass_context
def audit(ctx: click.Context, number: int) -> None:
    """Post the collected auditor verdict on a merged PR as an AUDIT comment (PASS or FAIL)."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_audit(c, number))


@main.command()
@click.argument("number", type=int)
@click.pass_context
def reopen(ctx: click.Context, number: int) -> None:
    """Reopen a closed issue after a failed audit and return it to ready."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_reopen(c, number))


@main.command()
@click.pass_context
def roster(ctx: click.Context) -> None:
    """List the worker types available to the calling party."""
    c = _load(ctx)
    emit([{"name": w.name, "description": w.description, "source": w.source} for w in _guard(c.roster_entries)], ctx.obj["pretty"])


@main.command()
@click.argument("number", type=int)
@click.option("--worker", default=None)
@click.option("--model", default=None)
@click.option("--effort", default=None)
@click.option("--size", default=None, type=click.Choice(["S", "M", "L"]))
@click.pass_context
def tag(ctx: click.Context, number: int, worker: str | None, model: str | None, effort: str | None, size: str | None) -> None:
    """Set the worker, model, effort, and size labels on an issue (orchestrator or human)."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_tag(c, number, worker=worker, model=model, effort=effort, size=size))


@main.command()
@click.argument("ref")
@click.pass_context
def decide(ctx: click.Context, ref: str) -> None:
    """Record a verified [joint] DECISION as .meld/decisions/NNNN-<slug>.md through a memory PR."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_decide(c, ref))


@main.command()
@click.argument("ref")
@click.option("-m", "--message", "message", required=True, help="What went wrong, what fixed it, what to check next time.")
@click.pass_context
def remember(ctx: click.Context, ref: str, message: str) -> None:
    """Append a lesson about a resolved issue or PR to .meld/memory/lessons.md through a memory PR."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_remember(c, ref, message))


@main.command()
@click.argument("query")
@click.option("--limit", type=int, default=10, show_default=True)
@click.pass_context
def recall(ctx: click.Context, query: str, limit: int) -> None:
    """Search decisions, lessons, and past coordination issues."""
    c = _load(ctx)
    emit(_guard(lambda: do_recall(c, query, limit=limit)), ctx.obj["pretty"])


@main.group()
def plan() -> None:
    """The plan gate: create planned issues from a [joint] PLAN, approve the plan, or ack an addition."""


@plan.command("issues")
@click.argument("ref")
@click.option("--dry-run", is_flag=True, help="Parse and report without creating issues.")
@click.pass_context
def plan_issues_cmd(ctx: click.Context, ref: str, dry_run: bool) -> None:
    """Create meld:planned issues from the verified [joint] PLAN or PLAN-ADD comment for REF."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_plan_issues(c, ref, dry_run=dry_run))


@plan.command("approve")
@click.pass_context
def plan_approve_cmd(ctx: click.Context) -> None:
    """Human: add meld:plan-approved and flip every planned issue to ready."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_plan_approve(c))


@plan.command("ack")
@click.argument("ref")
@click.pass_context
def plan_ack_cmd(ctx: click.Context, ref: str) -> None:
    """Human: post ACK for a [joint] PLAN-ADD and flip its planned issues to ready."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_plan_ack(c, ref))


@main.command()
@click.pass_context
def inbox(ctx: click.Context) -> None:
    """What needs a person: needs-human items, budget alerts, disagreements, plan additions, open asks."""
    c = _load(ctx)

    def run() -> dict:
        _, board = build_status(c)
        return build_inbox(board, c.cfg, datetime.now(timezone.utc))
    emit(_guard(run), ctx.obj["pretty"])


@main.command()
@click.option("--once", "once", default=None, help="Post one line (or one slash command) and exit.")
@click.option("--verbose", is_flag=True, help="/wait prints every new coordination comment, not only joint and human ones.")
@click.pass_context
def chat(ctx: click.Context, once: str | None, verbose: bool) -> None:
    """Talk to every group as [human]: plain text posts an ASK; slash commands post the human verbs."""
    c = _load(ctx)
    lines = [once, "/quit"] if once is not None else iter(sys.stdin.readline, "")
    result = _guard(lambda: run_chat(c, lines, click.echo, verbose=verbose))
    emit(result, ctx.obj["pretty"])
    if once is not None and result["errors"]:
        # A scripted line that failed (refused, malformed, GitHub down) fails the command too: the caller reads the exit
        # status, not the inline "error:" line. The REPL keeps answering inline and exits 0 when the session ends.
        ctx.exit(2)


@main.command()
@click.argument("group")
@click.option("--once", is_flag=True, help="Launch both roles once and return when they exit (no restarts).")
@click.pass_context
def run(ctx: click.Context, group: str, once: bool) -> None:
    """Supervise a group's orchestrator and PM processes."""
    env = {**os.environ, "MELD_GROUP": group, "MELD_ROLE": "orchestrator"}
    try:
        c = load_context(ctx.obj["config_path"], env=env, run=RUN, adapter_for=ADAPTER_FOR)
    except ConfigError as e:
        raise click.ClickException(str(e)) from e
    emit(_guard(lambda: run_group(c, once=once, base_env=env)), ctx.obj["pretty"])


@main.command()
@click.argument("repo")
@click.option("--goal", required=True, help="One paragraph: what the product is for. Goes into the coordination issue.")
@click.option("--milestone", "milestone_title", default="M1", show_default=True)
@click.option("--dir", "target_dir", type=click.Path(path_type=Path), default=Path("."), show_default=True, help="Where to write .meld.toml (the product clone).")
@click.option("--allow-public", is_flag=True, help="Proceed on a public repository despite the injection risk.")
@click.option("--force", is_flag=True, help="Overwrite an existing .meld.toml.")
@click.option("--no-protection", is_flag=True, help="Skip branch protection (require one approving review).")
@click.option("--pr", is_flag=True, help="Commit the written files on meld/setup and open the setup PR (uses your own git credentials).")
@click.pass_context
def setup(ctx: click.Context, repo: str, goal: str, milestone_title: str, target_dir: Path, allow_public: bool, force: bool,
          no_protection: bool, pr: bool) -> None:
    """Bootstrap a product repo: labels, milestone, coordination issue, and a starter .meld.toml (runs as you, via gh)."""
    gh = GitHub(repo, token=None, run=RUN)
    human = _guard(gh.viewer_login)
    # RUN carries gh api calls (a FakeGh in tests); git for --pr always runs through the default subprocess.run so it
    # is the human's own git identity and credentials that commit and push, never a test's or a group's fake.
    emit(_guard(lambda: do_setup(gh, repo=repo, goal=goal, milestone_title=milestone_title, human=human, target_dir=target_dir,
                                 registry=ModelRegistry.load(), allow_public=allow_public, force=force,
                                 protect=not no_protection, pr=pr)), ctx.obj["pretty"])


@main.command()
@click.option("--probe-status", is_flag=True, help="Also verify Commit statuses WRITE by posting a `meld/doctor` status on the default branch head (one per group).")
@click.pass_context
def doctor(ctx: click.Context, probe_status: bool) -> None:
    """Verify CLIs, tokens, identities, models, permissions, and the product repo. Exit 1 when a check fails."""
    path = ctx.obj["config_path"] or _guard(lambda: find_config(Path.cwd()))
    cfg = _guard(lambda: load_config(path))
    checks = _guard(lambda: run_checks(cfg, path.parent, env=os.environ, gh_factory=lambda token: GitHub(cfg.repo_name, token=token, run=RUN),
                                       registry=ModelRegistry.load(), adapter_for=ADAPTER_FOR, home=meld_home(os.environ), probe_status=probe_status))
    result = summary(checks)
    emit(result, ctx.obj["pretty"])
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
