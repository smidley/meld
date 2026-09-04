"""meld command-line entry point."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import click

from meld import __version__
from meld.actions import Refused, claim as do_claim, release as do_release
from meld.adapters import get_adapter
from meld.assemble import BoardError
from meld.config import ConfigError
from meld.context import Context, load_context
from meld.dispatch import DispatchError, collect as do_collect, dispatch as do_dispatch, reap as do_reap, tick as do_tick
from meld.github import GitHubError
from meld.gitops import GitError
from meld.posting import PostError, post as do_post
from meld.state import CHANGE_KINDS
from meld.status import build_status
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
    except (BoardError, GitHubError, ConfigError, DispatchError, GitError) as e:
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
@click.argument("number", type=int)
@click.option("--kind", type=click.Choice(["build", "review", "audit"]), default="build", show_default=True,
              help="build: a claimed issue; review/audit: a pull request.")
@click.pass_context
def dispatch(ctx: click.Context, number: int, kind: str) -> None:
    """Spawn a headless worker in its own worktree for an issue (build) or a PR (review, audit)."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_dispatch(c, number, kind=kind))


@main.command()
@click.pass_context
def tick(ctx: click.Context) -> None:
    """PM housekeeping: kill overdue workers, create missing leases, release other groups' expired leases."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_tick(c))


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
@click.pass_context
def roster(ctx: click.Context) -> None:
    """List the worker types available to the calling party."""
    c = _load(ctx)
    emit([{"name": w.name, "description": w.description, "source": w.source} for w in _guard(c.roster_entries)], ctx.obj["pretty"])


if __name__ == "__main__":
    main()
