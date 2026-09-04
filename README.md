# Moot

Multi-AI coordination: N independent AI coding systems (Claude Code, Codex CLI, ...) work on one GitHub repo as separate groups, negotiate on a coordination issue, claim work via issue assignment, review and modify each other's PRs, and answer to one human.

Design: `docs/superpowers/specs/2026-09-03-moot-design.md`. Plans: `docs/superpowers/plans/`.

## Develop

    uv sync
    uv run pytest
    uv run moot --help

## Verbs (Plan 1)

| Command | What it does |
|---|---|
| `moot status` | Board as JSON plus the legal actions for the calling party |
| `moot wait [--timeout S] [--interval S]` | Block until the board changes, then print status with a `changes` list |
| `moot claim N` | Assign this party to a ready issue (first assignment wins) |
| `moot release N [--force]` | Return an issue to ready; `--force` is human-only |
| `moot post VERB [REF] [-m TEXT] [--joint]` | Post a coordination comment in the header grammar |
| `moot models [--adapter X]` | Models and efforts an adapter accepts |

The calling party comes from `MOOT_GROUP` and `MOOT_ROLE` (`orchestrator` or `pm`); unset means human, authenticated as `gh`'s current user. Group tokens come from the `token_env` named in `.moot.toml`. Local state lives under `MOOT_HOME` (default `~/.moot`) and can be deleted at any time.

## Known issue: macOS hidden `.pth` files

uv marks its `.pth` files hidden on macOS, and Homebrew Python 3.13/3.14 skip hidden `.pth` files, so `import moot` can fail under `uv run` even though `uv sync` succeeded. Tests are unaffected (pytest is pointed at `src/` directly), and `task` sets `PYTHONPATH=src`. For a direct `uv run moot ...`, run:

    chflags nohidden .venv/lib/python3.13/site-packages/_editable_impl_moot.pth
