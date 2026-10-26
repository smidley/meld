# Plan 11 — live-run readiness (2026-10-26)

**Goal.** Clear the failures from the first live run (2026-09-11) that would stop or embarrass a second one, plus two carried items a fresh setup would trip over. Everything here is bounded, hermetically tested, and spends nothing.

**From the spec and the handoff.** `docs/HANDOFF.md`'s live-run residuals 2, 6 and 16; spec section 21 "Carried into plan 6" items 16 (the candid security statement) and 20 (case-insensitive logins).

## Decisions (each reversible)

1. **`collect` rewrites a rebased branch under a lease, and only then.** The PR's branch is force-pushed only when the new head is not a descendant of `origin/<branch>` (the worker rewrote history) *and* is a descendant of `origin/<default branch>` (the rewrite is a rebase onto main, as decision drift-1 asks), and `--force-with-lease` refuses the rewrite when `origin/<branch>` moved since this worktree last saw it. *Not taken:* a plain `--force`, which would have overwritten a reviewer's push-fix in exactly the race residual 1 describes. *Cost if wrong:* a worker that amends without rebasing onto a moved main still fails the plain push, as before.
2. **One login comparison.** `config.same_login` lowercases both sides; `is_party` replaces the three hand-built "known logins" sets. *Not taken:* lowercasing the configuration at load time, which would still miss a GitHub login whose canonical case differs from the entry's.
3. **Residual 2 is fixed in the auditor's prompt, not in `meld reopen`.** The auditor passes a criterion a later commit on the default branch already meets, citing that commit. *Not taken:* teaching `reopen` to judge remediation, which needs a second worker to read code. *Cost if wrong:* a prompt rule is obeyed by a model, not enforced by `rules.py`; a reopen of an already-fixed issue is still possible and still a human `close` away.
4. **Residual 6 is a prompt sentence.** The PM is told that `legal` is the only list of verbs to run and why; refusals are unchanged (one announcement per identical body, plan 9).
5. **The security statement says the guardrails are not a containment boundary**, in the README's Guardrails section and spec section 18.

## Tasks

1. `gitops.is_ancestor`, `push_branch(force_with_lease=)`, `dispatch._collect_build`; `tests/test_collect.py` drives a real rebase and a real lease refusal against a bare origin.
2. `config.same_login`/`is_party`; `grammar`, `assemble`, `housekeeping`, `notifier`, `board`, `doctor`; tests in `test_config.py`, `test_grammar.py`, `test_doctor.py`.
3. `prompts/pm.md` and `prompts/workers/auditor.md`.
4. README Guardrails, spec section 18, section 21 items 16 and 20; `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings. No live run: the collect fix is proven against a bare git remote, the lease against a second clone pushing behind the worktree's back.

## Carried

- Residual 1 (build/review mutual exclusion on one branch) is narrowed, not closed: a reviewer's push-fix can no longer be lost to a retry's push, but both can still run at once.
- Residual 15 (`legal` advertising verbs the rules then refuse) stands; the PM prompt now leans on `legal` harder, which makes that item more worth fixing.
- Spec section 21 items 15, 17, 18, 19 and the plan-7 residuals are untouched.
