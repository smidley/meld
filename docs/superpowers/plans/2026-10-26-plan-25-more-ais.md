# Plan 25 — more AIs (2026-10-26)

**Goal.** Adding another AI to a product is a form, not a code change: Gemini CLI and Grok Build join Claude Code and Codex as adapters, the catalogue knows their models, and the configuration page adds a group for any adapter the build knows.

**From the spec.** Section 10 (adapters), section 18 (isolation), section 22 (the configuration form).

**What the owner said.** "The config UI needs to be cleaned up a bit. We need to be able to easily add things like additional AI's (grok, gemini, etc)."

## Decisions (each reversible)

1. **One module per CLI, like the two before.** `adapters/gemini.py` and `adapters/grok.py` follow `claude.py` and `codex.py`: an argv, an environment, a `parse_events`, the probes `meld doctor` runs. *Not taken:* a generic adapter driven by a TOML spec -- two implementations do not earn an abstraction, and each CLI's event stream is its own.
2. **Built from the published references, proved by doctor.** Neither CLI is installed on the owner's machine. The flags are the documented ones (`gemini -p -m --output-format stream-json --approval-mode yolo`; `grok --no-auto-update -p -m --effort --output-format streaming-json --always-approve --cwd --max-turns`) and `cli-flags:<g>` checks them against the installed `--help` before any run; the event vocabularies are the documented (Gemini) and the reported (Grok) ones, read defensively, and an unrecognised line is a `raw` event so the transcript stays complete where a guess is wrong. *Not taken:* waiting for an install to write them.
3. **Gemini has no effort.** The CLI has no per-run effort setting, so `supports_effort` is false, the catalogue lists one effort, `default`, and the adapter passes none.
4. **Neither has a login probe.** `login_argv` is empty, so `cli-login:<g>` is NOT MEASURED rather than guessed; a missing key surfaces as an `error` event on the first run.
5. **The catalogue carries the documented ids.** Gemini: `gemini-3-pro-preview`, `gemini-2.5-pro` (large), `gemini-2.5-flash` (medium), `gemini-2.5-flash-lite` (small). Grok: `grok-build-0.1` (large) with low/medium/high as the assumed effort levels. Each is one line to correct from the installed CLI.
6. **The form speaks plain words and adds groups.** A heading and a line of why per table, a label per key, one card per AI group with Remove; "Add a group" takes a name and an adapter the server names and builds the block from that CLI's catalogue defaults, editable until saved. Every save still passes the four gates.

## Tasks

1. `adapters/gemini.py`, `adapters/grok.py`, `adapters/__init__.py`, `data/models.toml`; `tests/test_adapter_gemini.py`, `tests/test_adapter_grok.py`, fixtures, `tests/test_adapters.py`, `tests/test_setup.py`.
2. `viewer/server.py` (`adapters`), `app.js` (`WORDS`, `GROUP_WORDS`, `renderGroup`, `addGroupRow`, `newGroupTemplate`), `app.css`; `tests/test_viewer.py`.
3. Spec sections 10, 18 and 22, README, `setup.py`'s warning, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings. The form checked in the browser against the playground: the sections, the two cards, "Add a group" offering all four adapters, a `gemini` card built with `gemini-3-pro-preview/default` (not saved: no Gemini CLI on this machine).

## Carried

- A first run of either new adapter against its real CLI, which would correct any flag, event name or model id the references got wrong.
- Grok Build's plugins are not scanned as skills.
