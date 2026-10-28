# Plan 42 — a state directory per product (2026-10-27)

**Goal.** A second product on the same machine never shares local state with the first, and nobody sets `MELD_HOME` by hand.

**From the spec.** Section 18 (`$MELD_HOME` is disposable state), section 20 (the wizard).

**What happened.** The scratch product only ran beside the playground because its launch config set `MELD_HOME=~/.meld-scratch`. Without that, `meld stop codex` in one product's folder could have stopped the other's supervisor, and run records (keyed by issue number) and worktrees would have collided.

## Decisions (each reversible)

1. **`~/.meld/products/<owner>/<name>`.** `state.meld_home(env, repo)` returns it once the repository is known; an explicit `MELD_HOME` wins regardless; bare `~/.meld` only before a repository exists. *Not taken:* keying on the folder path (a clone moved or re-cloned would lose its state) or on a hash (unreadable in `ls`).
2. **Every command passes the configured repo.** `load_context` for the roles and the viewer; `run`, `stop`, `ps`, `doctor`, `tail` from the loaded config; a detached supervisor gets `MELD_HOME` set explicitly in its environment so it never re-derives it differently.
3. **The wizard moves house when the repository is named.** `SetupSession.home_for` turns the base home into the product's at `create()`, so keys, logs and runs land where every later command looks; the launcher and doctor read `session.home` at call time.
4. **No migration.** The playground's state stays at `~/.meld`; its launch config sets `MELD_HOME` explicitly. *Not taken:* moving `~/.meld` into a product directory under the owner's feet.

## Tasks

1. `state.py`, `context.py`, `cli.py`, `firstrun.py`; `tests/test_state.py`, `tests/test_cli.py`, `tests/test_firstrun.py`.
2. Spec section 18, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings.

## Carried

- The wizard's viewer token is the base home's; the product's own token is minted on the next `meld start` in that folder.
