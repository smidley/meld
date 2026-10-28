# Plan 50 — a model at capacity is a pause, not a failure (2026-10-28)

**Goal.** A busy model never costs a retry, a re-tag, a freeze or a person.

**From the spec.** Section 17 (the new row beside the limit row), section 9 (the rubric), section 22 (events).

**What the owner said.** "It looks like one of the agents is blocked due to model capacity; this needs to be handled automatically."

**What happened.** On `smidley/meld-test-maze`, Codex answered "Selected model is at capacity. Please try a different model." to the first worker on #3 and to several role sessions. The worker died without a result; collect called it `failed`; the rubric then refused the same tags; the orchestrator raised effort; the retry failed for a real reason; the issue ended `blocked`. The role sessions were relaunched on the crash backoff.

## Decisions (each reversible)

1. **Its own event.** `adapters.base.is_capacity`/`capacity_event` give every adapter the same recognition (Codex `turn.failed`/`error` messages, Claude's 529 result); the kind is `capacity`, rendered `busy`. *Not taken:* treating it as a usage limit, which freezes the role until a reset nobody named.
2. **The supervisor pauses, then relaunches as itself.** `CAPACITY_PAUSE_SECONDS` (60), reason `capacity`, restart count untouched; the prompt's launch block says nothing of yours failed.
3. **Collect calls it `capacity`, not `failed`.** The issue stays in progress, the comment says the build is retried, the run record carries `ended_at`; `rules._dispatch` neither applies the rubric nor allows a dispatch before `CAPACITY_RETRY_SECONDS` (60) have passed, and names the instant.
4. **The page and the prompts say so.** The journey reads "the model was at capacity; the build is retried after a short pause"; the PM prompt says never to re-tag or post NEEDS-HUMAN for it; the orchestrator prompt says it is not a failed attempt.

## Tasks

1. `adapters/base.py`, `adapters/codex.py`, `adapters/claude.py`, `transcript.py` (the kind, its label, `capacity_text`), `runners.py`, `prompts.py` (`LAUNCH_REASONS`, `_REASON_TEXT`), `board.py` (`RunRecord.ended_at`), `dispatch.py`, `rules.py`, `chat.py`, `viewer/static/app.js`, `prompts/pm.md`, `prompts/orchestrator.md`; tests for each.
2. Spec section 17, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings.

## Carried

- Codex's advice "try a different model" is not followed automatically: after repeated capacity stops the orchestrator may move the issue to another model by hand; a count per issue on the board would make that a rule.
