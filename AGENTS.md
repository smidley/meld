# Conventions for agents working in this repository

Start with `docs/HANDOFF.md` (project state and how to continue), then `docs/superpowers/specs/2026-09-03-meld-design.md` (the binding design), then the plan for the phase you are working on under `docs/superpowers/plans/`.

## Rules that are not negotiable

- **The spec wins.** Behaviour changes start in the spec; plans argue from it; code implements it. Do not re-litigate the decisions in spec section 3 or the plan headers.
- **`rules.py` is the only legality authority.** No new `if actor...` legality checks outside it; `meld status` must be able to list every legal verb.
- **`github.py` is the only GitHub path and `gitops.py` the only git runner.** Adapters are the only code that knows how a CLI is launched.
- **Workers never hold credentials.** Anything a worker writes that reaches GitHub passes through `neutralise_closers`; framework-only verbs stay framework-only.
- **Tests are hermetic and real.** Real git repos and real subprocesses over `FakeGh`; no network, no real CLI, no host `~/.claude`/gitconfig/wall-clock dependence; inject `now`; pass `adapter_for=` to every `load_context` in tests. `uv run pytest -q` must be green with no warnings before every commit.
- **Never set `MELD_SMOKE=1`** unless the owner asked for a smoke run; it launches the real AI CLIs and spends money.
- **Commits** are plain conventional messages (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`), authored with the repo-local git identity already configured; no attribution trailers of any kind; one logical change per commit.
- **Pull requests** go from a feature branch to `main` with a body in the style of PRs #1–#3 (what lands, verification, decisions to confirm, known follow-ups). Rewrite `docs/HANDOFF.md` for the new state before opening a milestone PR.
- **Standard library plus click only** in `src/meld/`; Python 3.13.

## How to run things

```bash
uv sync
uv run pytest -q
uv run meld --help
task --list
```

## Where things live

`src/meld/` modules are described in `docs/HANDOFF.md`; prompts for roles and workers are Markdown under `src/meld/prompts/`; the packaged model catalog is `src/meld/data/models.toml`; local runtime state lives under `$MELD_HOME` (default `~/.meld`) and is disposable.
