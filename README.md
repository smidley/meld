# Meld

Multi-AI coordination: N independent AI coding systems (Claude Code, Codex CLI, ...) work on one GitHub repo as separate groups, negotiate on a coordination issue, claim work via issue assignment, review and modify each other's PRs, and answer to one human.

Design: `docs/superpowers/specs/2026-09-03-meld-design.md`. Plans: `docs/superpowers/plans/`.

## Develop

    uv sync
    uv run pytest
    uv run meld --help

## Verbs

| Command | What it does |
|---|---|
| `meld status` | Board as JSON plus the legal actions for the calling party |
| `meld wait [--timeout S] [--interval S] [--filter KINDS]` | Block until the board changes, then print status with a `changes` list; `--filter` limits which change kinds count |
| `meld claim N` | Assign this party to a ready issue (first assignment wins) |
| `meld release N [--force]` | Return an issue to ready; `--force` is human-only |
| `meld post VERB [REF] [-m TEXT] [--joint]` | Post a coordination comment in the header grammar |
| `meld models [--adapter X]` | Models and efforts an adapter accepts |
| `meld dispatch N [--kind review|audit]` | Spawn a headless worker in its own worktree: a claimed issue by default, or a PR for `review`/`audit` |
| `meld tick` | PM housekeeping: kill overdue workers, create missing leases, release other groups' expired leases |
| `meld collect N` | Finish a worker run: push and open the PR, or mark the issue blocked or needs-human |
| `meld reap N` | Remove a finished run's worktree and archive its record |
| `meld tag N [--worker W] [--model M] [--effort E] [--size S]` | Set the worker, model, effort, and size labels on an issue |
| `meld roster` | List the worker types available to the calling party |
| `meld run GROUP [--once]` | Supervise a group's orchestrator and PM as headless CLI sessions |

## Running a group

    MELD_TOKEN_CLAUDE=... uv run meld run claude

`meld run` starts the group's orchestrator and PM as headless CLI sessions with the packaged role prompts, restarts them when they exit or every `runners.restart_hours`, and writes their logs under `$MELD_HOME/logs/<group>/`. Workers are separate processes started by the PM through `meld dispatch`; they run in `$MELD_HOME/worktrees/<group>/`, receive no GitHub credentials, and end by writing `.meld-result.json`, which `meld collect` turns into a pushed branch and a pull request.

Real-CLI smoke tests (they spend tokens): `task test:smoke`.

## Exit codes

`0` success. `1` an error (bad config, a GitHub failure, an unusable board). `2` refused by the rules, or a usage error. A rules refusal prints its reason as JSON:

    {"refused": "claim #12 refused: issue is in_progress, not ready", "verb": "claim", "target": "#12",
     "rule": "issue is in_progress, not ready", "violation_posted": true}

`violation_posted` is `true` when the refusal was announced on the coordination issue as `PROTOCOL-VIOLATION`, `false` when that post was attempted and failed, and `null` when none was attempted — a human's refusal, or a lost claim race, which is first-assignment-wins working as designed rather than an illegal call.

The calling party comes from `MELD_GROUP` and `MELD_ROLE` (`orchestrator` or `pm`); unset means human, authenticated as `gh`'s current user. Group tokens come from the `token_env` named in `.meld.toml`. Local state lives under `MELD_HOME` (default `~/.meld`) and can be deleted at any time.

## Known issue: macOS hidden `.pth` files

uv marks its `.pth` files hidden on macOS, and Homebrew Python 3.13/3.14 skip hidden `.pth` files, so `import meld` can fail under `uv run` even though `uv sync` succeeded. Tests are unaffected (pytest is pointed at `src/` directly), and `task` sets `PYTHONPATH=src`. For a direct `uv run meld ...`, run:

    chflags nohidden .venv/lib/python3.13/site-packages/_editable_impl_meld.pth
