# Plan 70 — a spend cap is a limit, not a crash (2026-10-29)

**Goal.** A role that its provider refuses to serve freezes and says so, whatever words the refusal uses.

**From the spec.** Section 14, the usage-limit row; section 22 (`usage_limit`).

**What the maze run showed.** From about 15:00 UTC both codex roles printed "You hit your spend cap set by the owner of your workspace. Ask an owner to increase your spend cap to continue." and exited within three seconds. `is_limit` knew "spend limit" but not "spend cap", so the supervisor called each exit a crash and relaunched both roles every ten minutes for four hours: forty launches, no `LIMITED` post, the page saying "partly running", and claude pinging codex for silence.

## Decisions (each reversible)

1. **`spend cap` and `spending limit` are limit phrases** in `adapters.base._LIMIT_RE`. The codex adapter's `turn.failed` path already turns a limit message into `usage_limit`, so the freeze, the single `LIMITED` post and the retry cadence follow.

## Tasks

1. `adapters/base.py`; tests in `test_adapters.py`.
2. Spec section 14, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green.

## Carried

- The maze's codex roles stay frozen until the owner raises the ChatGPT workspace spend cap; a re-probe every `runners.limit_retry_minutes` (doubling) notices when it lifts.
