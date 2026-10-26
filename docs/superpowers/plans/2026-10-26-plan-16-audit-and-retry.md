# Plan 16 — the audit a challenge asks for, and the retry a failure earns (2026-10-26)

**Goal.** The last two live-run residuals that change what a demo audience sees on the board: an audit requested by a challenge waited for the batch like any other (residual 7), and a PM could re-dispatch a failed run at the unchanged tag before the orchestrator re-tagged, so the same failure ran twice (residual 5).

**From the spec.** Section 8's dispatch rule, section 9's model-and-effort rubric, section 14's "Independent audit".

## Decisions (each reversible)

1. **A pending merged PR under an open CHALLENGE — or whose issue is — makes the group's audit due at once.** `status.audit_summary` adds `challenged` (the PR numbers) and `due` is true whenever it is non-empty; the PM prompt says to audit those first. *Not taken:* a DECISION as a trigger. A decision ref names no PR, so "requested by a DECISION" has no mechanical reading; a decision that wants an audit can open a challenge.
2. **`dispatch` refuses a retry after a failed run at the unchanged model and effort.** The run record gains `outcome` (`done`, `no_change`, `failed`, `blocked`, `superseded`), stamped by `collect` and read back from disk; `rules._dispatch` refuses when the group's last build run on the issue was collected `failed` and the issue's `model` and `effort` equal the record's. Both `meld dispatch` and `legal`/`refused` read the one rule. *Not taken:* refusing after `request-changes` too, as the rubric's wording allows — the review may call for a different approach rather than more effort, and that retry is the normal flow. *Cost if wrong:* a failure that was the environment's (a flaky test, a full disk) still costs a re-tag before the retry.
3. **An old record with no `outcome` dispatches as before.** The field defaults to `None`, so nothing recorded before this plan is refused.

## Tasks

1. `board.RunRecord.outcome`, `state._read_record`, `dispatch._collect_build` stamps; `rules._dispatch`; `tests/factories.mk_run`, `tests/test_rules_issues.py`, `tests/test_collect.py`.
2. `status.audit_summary`; `tests/test_status.py`.
3. `prompts/pm.md` (audit bullet), `prompts/orchestrator.md` (rubric rules); spec section 8's dispatch bullet and section 14's audit bullet; `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings. The rule test drives the refusal, the re-tag that lifts it, and every other outcome; the collect test proves the stamp survives disk and the next dispatch is refused end to end; the status test drives the challenge on the PR and on its issue, and the author group owing nothing.

## Carried

- A DECISION cannot request an audit by itself (decision 1).
- A retry after `request-changes` at the unchanged tag is still legal (decision 2).
- Review and audit runs carry no `outcome`; only builds do.
