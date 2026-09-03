"""moot command-line entry point. Verbs are added in later tasks."""
from __future__ import annotations

import click

from moot import __version__


@click.group()
@click.version_option(__version__, prog_name="moot")
def main() -> None:
    """Moot: multi-AI coordination over a GitHub repo."""


if __name__ == "__main__":
    main()
