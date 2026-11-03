# Plan 78 — a stopped group is named (2026-11-03)

**Goal.** When a group is stopped and work waits on it, the page says so and says what to run.

**From the spec.** Section 22 (the status strip, plan 54); section 2's non-goal that the page never starts or stops a supervisor.

**What the linkcheck run showed.** The owner ran `meld stop codex` to pick up plan 75, then approved the plan. The plan tagged #2 for codex's model and made #3 to #6 wait on it, so nothing could move: claude's PM reported "codex owns #2; continuing to wait" every launch. The strip showed "codex's orchestrator has stopped (restarted 11 times). Open its transcript" with the PM line as detail, which reads as a crash to investigate, and the headline said nothing waited on the owner. An hour passed.

## Decisions (each reversible)

1. **The supervisor comes first.** For each group, a dead supervisor that is not frozen yields one `bad` line naming the group and the exact command, and the group's role lines are skipped: the roles are dead because the supervisor is. A frozen group keeps the quota line, since the freeze explains the exit.
2. **A command, not a button.** The page refuses to start or stop a supervisor (section 2), so the line carries the command to run on the machine.
3. **Not "which issues wait on it".** Mapping an issue's tagged model to a group needs the model registry, which `chat` does not hold; "nothing it owns moves" is true without it.

## Tasks

1. `chat.status_strip`; `tests/test_chat.py`.
2. Spec section 22, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green; the linkcheck page (once its viewer runs this code) shows the red line while codex is stopped.

## Carried

- `headline()` still says "nothing waits on you" with a group stopped; `meld chat` readers get the strip's line only on the page.
