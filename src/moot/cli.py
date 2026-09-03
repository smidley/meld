"""moot command-line entry point."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import click

from moot import __version__
from moot.assemble import BoardError
from moot.config import ConfigError
from moot.context import Context, load_context
from moot.github import GitHubError
from moot.status import build_status

RUN = subprocess.run  # tests monkeypatch this with a FakeGh


def emit(data: object, pretty: bool) -> None:
    click.echo(json.dumps(data, indent=2 if pretty else None))


@click.group()
@click.version_option(__version__, prog_name="moot")
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None,
              help="Path to .moot.toml (default: search upward from the current directory).")
@click.option("--pretty/--compact", default=True, help="Pretty-print JSON output.")
@click.pass_context
def main(ctx: click.Context, config_path: Path | None, pretty: bool) -> None:
    """Moot: multi-AI coordination over a GitHub repo."""
    ctx.obj = {"config_path": config_path, "pretty": pretty}


def _load(ctx: click.Context) -> Context:
    return _guard(lambda: load_context(ctx.obj["config_path"], run=RUN))


def _guard(fn):
    try:
        return fn()
    except (BoardError, GitHubError, ConfigError) as e:
        raise click.ClickException(str(e)) from e


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Print the board and the legal actions for the calling party."""
    c = _load(ctx)
    data, _ = _guard(lambda: build_status(c))
    emit(data, ctx.obj["pretty"])


@main.command()
@click.option("--adapter", default=None, help="Adapter to list (default: the calling group's CLI).")
@click.pass_context
def models(ctx: click.Context, adapter: str | None) -> None:
    """List the models and efforts an adapter accepts."""
    c = _load(ctx)
    adapter = adapter or (c.cfg.groups[c.party.group].cli if c.party.group else "claude")
    specs = c.models.for_adapter(adapter)
    emit({"adapter": adapter, "models": [
        {"id": s.id, "context_class": s.context_class, "efforts": list(s.efforts), "tier": s.tier} for s in specs
    ]}, ctx.obj["pretty"])


if __name__ == "__main__":
    main()
