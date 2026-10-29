# Plan 63 — waiting on a person is quiet (2026-10-29)

**Goal.** A group whose ask only a person can answer spends nothing until the person answers.

**From the spec.** Section 8 (one open ask per ref, plan 41), section 12 (`meld wait --filter`), section 14 (the violation throttle).

**What the maze run showed.** With `NEEDS-HUMAN #4` open on the App permission, claude's PM re-posted the ask on every launch and was refused; each refusal posted a `PROTOCOL-VIOLATION`, which woke codex's PM, which posted `PING #4`, which woke claude's PM. The throttle let the second violation through because the PM wrote `4` once and `#4` the next time. Claude's PM reached launch 102 and codex's 126 in an evening in which nothing could move.

## Decisions (each reversible)

1. **A repeat of an open ask is refused quietly.** `posting._refuse(..., quiet=True)` for `ask_rule`; `actions.refuse` takes `quiet` and posts nothing. The refusal text is the feedback.
2. **`4` and `#4` are one target** in `actions._already_said`.
3. **A `human` change kind.** `board_digest.last_human_comment_id` is the newest post by a listed human; `diff_changes` emits `human` when it moves, and a snapshot from before the key never fires it.
4. **The prompts.** The PM: while your own ask is open and none of your runs is live, `meld wait --filter human,issue,pr,run`; never re-post an open ask. The orchestrator: never `PING` a group about its open `NEEDS-HUMAN`.

## Tasks

1. `state.py`, `actions.py`, `posting.py`, `prompts/pm.md`, `prompts/orchestrator.md`; tests in `test_state.py`, `test_actions.py`, `test_posting.py`.
2. Spec sections 8, 12, 14; `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green.

## Carried

- A running role keeps its prompt until relaunch; the maze's PMs pick the new wait up on their next launch.
