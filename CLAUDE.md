# Meld

Read `docs/HANDOFF.md` first, then follow the conventions in `AGENTS.md` (imported below). The design spec at `docs/superpowers/specs/2026-09-03-meld-design.md` is the binding authority; `rules.py` is the only legality authority; tests must stay hermetic and green (`uv run pytest -q`, no warnings) before every commit; never set `MELD_SMOKE=1` without being asked; plain conventional commits with no attribution trailers.

@AGENTS.md
