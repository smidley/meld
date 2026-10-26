# Plan 28 — any model behind a supported CLI (2026-10-26)

**Goal.** "Codex pointed at my Ollama server" is a configuration, not a new adapter: a group carries its own environment and CLI settings, the product names its own models, and the form writes all of it from a URL.

**From the spec.** Section 5 (configuration), section 10 (adapters), section 18 (isolation), section 22 (the form).

**What the owner said.** "I have ollama on my server that I would like to be able to leverage, and there's a lot of other AI's out there too." Then: "yes, build the capability."

## Decisions (each reversible)

1. **Two free tables per group, not a provider schema.** `[groups.<g>.env]` and `[groups.<g>.cli_config]` take any key; Meld validates shape only (an env name, scalar values) and leaves the CLI to judge its own settings. *Not taken:* a `provider` block Meld understands, which would need updating for every CLI's next option.
2. **Env under identity, never over it.** `extra_env` drops `MELD_*`, `GIT_*`, `GH_*` and `GITHUB_*` names, so a group's env can reach a model server but never a worker's credentials or git identity. Added after the worker scrub, under the worker's own git and gh settings.
3. **Codex: `-c` overrides; Claude: `--settings` underneath.** An ephemeral `codex exec` reads no config file, so a custom provider has to arrive on argv; Claude's merge keeps hooks disabled, memory off and credentials blanked whatever a group sets.
4. **No secrets in the file.** `.meld.toml` is committed; the form's template writes `OPENAI_API_KEY=ollama` (a placeholder a keyless server ignores) and says to export a real key in the shell instead.
5. **The product's own catalogue.** `.meld/models.toml` lays over the packaged `models.toml`; `meld add-model` writes it (one quoted-key table per model, so `qwen3-coder:30b` stays one key). Tier and context class are the product's call.
6. **The form writes it from a URL.** "Add a group" takes an optional model server; a Codex group gets the provider overrides, a Claude group the base URL; each card shows both tables as `key=value` lines.

## Tasks

1. `config.py` (fields, parsers, `extra_env`, `_key`, writer), `adapters/base.py` (`LaunchSpec.cli_config`), `runners.py`, `dispatch.py`, `adapters/codex.py` (`override_args`), `adapters/claude.py`, `models.py` (`load(extra=)`, `write_product_model`), `context.py`, `cli.py` (`add-model`); tests in `test_config_write.py`, `test_runners.py`, `test_dispatch.py`, `test_adapter_codex.py`, `test_adapter_claude.py`, `test_models.py`.
2. `app.js` (`mapField`, `parseMap`, `endpointSettings`), `app.css`; `tests/test_viewer.py`.
3. Spec sections 5 and 22, README, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings. Against the owner's Ollama server: a Codex group added from the page with the server's URL, a model named with `meld add-model`, `meld doctor` green for it, and one headless run.

## Carried

- The Ollama run itself, pending the server's URL and model.
