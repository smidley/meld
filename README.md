# Moot

Multi-AI coordination: N independent AI coding systems (Claude Code, Codex CLI, ...) work on one GitHub repo as separate groups, negotiate on a coordination issue, claim work via issue assignment, review and modify each other's PRs, and answer to one human.

Design: `docs/superpowers/specs/2026-09-03-moot-design.md`.

## Develop

    uv sync
    uv run pytest
    uv run moot --help
