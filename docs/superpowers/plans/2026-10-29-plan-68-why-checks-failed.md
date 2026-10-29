# Plan 68 — red checks say why (2026-10-29)

**Goal.** A red PR tells the agents what failed, and a failure that is not the code's never costs a rebuild.

**From the spec.** Section 9 (the build brief, retries), section 13 (checks and merge), section 20 (the journey).

**What the maze run showed.** PR #20 went red on every push. Claude's PM rebuilt #4 three times; its orchestrator reproduced both CI jobs locally, found them green, and asked a person for "the name of the failing job and the output of its first failing step", because `meld status` said only `checks: red`. The check runs' annotations said all along: "The job was not started because recent account payments have failed or your spending limit needs to be increased."

## Decisions (each reversible)

1. **`assemble.check_failures`** reads each failed check run's annotations through `GitHub.check_run_annotations` (Checks: read, which every App has) into `PullRequest.failures`: name, link, conclusion, up to five failure or warning messages, and `started`, false when a message says the job was not started. An unreadable annotations endpoint leaves the notes empty.
2. **Everyone sees it.** `meld status` carries `pulls[].failures`; a retry's `## Review feedback to address` ends with `### Failing checks on <head>` and, when no job ran, "These jobs never ran, so nothing in the code caused this"; the journey's "checks are red" names the first note.
3. **No rebuild for a job that never ran.** `rules._dispatch` refuses a build while the issue's open PR is red and every failed run never started, quoting the note and telling the PM to ask a person once.
4. **The PM prompt** says the notes are what there is, and never to ask a person for a CI log.

## Tasks

1. `github.py`, `assemble.py`, `board.py`, `status.py`, `brief.py`, `rules.py`, `chat.py`, `prompts/pm.md`; tests in `test_assemble.py`, `test_rules_issues.py`, `test_brief.py`.
2. Spec sections 9 and 13, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green.

## Carried

- Job logs (the failing step's output) need Actions: read, which the manifest does not ask for; annotations carry the exit code and any `::error::` lines, which was enough here.
