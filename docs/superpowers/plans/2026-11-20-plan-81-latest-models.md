# Plan 81 — a new product starts on the newest models (2026-11-20)

**Goal.** A product the wizard or `meld setup` creates today starts its roles on the newest models each CLI offers.

**From the spec.** Section 5 (`.meld.toml` and `models.toml`), section 20 (bootstrap).

**What the livedrop start showed.** The owner made a new product (`smidley/livedrop`) and asked that it use the latest Claude and Codex models. The wizard has no model field: `setup._group_block` takes the first large-tier entry of `models.toml` for the orchestrator and the first medium-tier one for the PM, in file order. Plan 22 had added the Claude 5.5 family below the older entries, so Claude would have started on `claude-opus-5` and `claude-sonnet-5`; Codex's PM would have started on `gpt-6-astra` while its CLI lists a newer `gpt-6.1-terra`.

## Decisions (each reversible)

1. **The catalogue is ordered, newest first, per adapter.** The pick rule stays "first of the tier"; the file's order is what says which model is current, and a comment at the top says so. No code changes.
2. **Opus 5.5 leads the large tier, not Fable 5.2.** Anthropic's list names Opus 5.5 the default for most work and prices Fable 5.2 at twice it; Fable is in the catalogue for a product to choose.
3. **`gpt-6.1-terra` is medium tier,** by the 5.6 family's sol/terra/luna pattern.
4. **Older entries stay,** because existing configurations name them and `meld doctor` checks a configured model against this file.

## Tasks

1. `src/meld/data/models.toml` (order, `claude-fable-5-2`, `gpt-6.1-terra`); `tests/test_models.py`.
2. Spec sections 5 and 20, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green; the wizard's facts for `~/code/livedrop` report `claude-opus-5-5` and `gpt-6.1-sol` as the defaults, and `_group_block` renders `claude-sonnet-5-5` and `gpt-6.1-terra` for the PMs.

## Carried

- `meld doctor` checks a model against the catalogue, not against the installed CLI's version: `claude-fable-5-2` on Claude Code below 2.1.290 passes doctor and fails at launch.
- The wizard offers no model choice, so a product that wants Fable edits `.meld.toml` after setup.
