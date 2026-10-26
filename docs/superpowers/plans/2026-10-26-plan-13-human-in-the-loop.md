# Plan 13 — the human in the loop (2026-10-26)

**Goal.** Handoff residual 10 from the first live run: a person's push to a PR branch moved nothing, and the PM's next cycle built a retry over it (and undid the relabel the person made to work around that). A demonstration with a person stepping in hits this at once.

**From the spec.** Section 17's failure table (the red-CI row is the template), section 13 (review requests), and the rule that a worktree is the framework's to prepare.

## Decisions (each reversible)

1. **A human's head is a review request, raised by the author group's tick.** `housekeeping.human_head_sweep` selects our open PRs whose head committer is a listed human and whose issue is `in_progress` with no live build run; it posts `REVIEW-REQUEST #<pr>` naming the head and the person, then moves the issue to `in_review`, once per head, with the same persisted two-step record the red-CI sweep keeps. *Not taken:* treating the push as a collect (there is no result file to read) or leaving it to the person to relabel (which the next tick could undo). *Cost if wrong:* a person who pushes work in progress gets a review request they did not want; they can push again, which is a new head and a new request only after the issue returns to `in_progress`.
2. **One once-per-head sweep, two uses.** `_head_sweep` carries the machinery (selection, comment-then-swap, per-head persistence, error containment, pruning); `ci_red_sweep` and `human_head_sweep` are each a filter, a comment and a label direction. *Not taken:* a second copy of fifty lines.
3. **A reused worktree fast-forwards to `origin/<branch>` when strictly behind.** `gitops.fast_forward_to_remote` fetches the branch and fast-forwards a clean checkout; a diverged or dirty checkout is left alone and the dispatch result says which (`branch_refresh`). *Not taken:* a reset to the remote, which would drop a failed run's local commits.
4. **Red CI on a person's head still returns the issue to `in_progress`.** That is the red-CI row's meaning and it is unchanged; the retry then builds on the person's head, which decision 3 makes possible.
5. **The page's check word is `red`**, not `failure`: plan 10's row edit used a word `checks_state` never emits, so a red check never showed. Fixed here, with no test — the page's JavaScript is not executed by the suite.

## Tasks

1. `housekeeping._head_sweep`, `ci_red_sweep` on it, `human_head_sweep`, `HUMAN_HEAD_STATE`; `dispatch.tick` runs it after the red-CI sweep; `tests/test_housekeeping.py`.
2. `gitops.fast_forward_to_remote`; `dispatch._dispatch_build` calls it on a reused worktree and reports `branch_refresh`; `tests/test_dispatch.py` drives a real push from a second clone.
3. `viewer/static/app.js` check word.
4. README's `meld tick` row, spec section 17's table, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings. The sweep test drives the human head through the board fixture (a human committer on PR #20, issue #13 in progress) and asserts the request, the swap, the state file, the once-per-head silence, and that another group's PR and a bot's head are left alone. The dispatch test pushes from a second clone and asserts the worktree lands on that commit, then reads `current` on the next dispatch.

## Carried

- `meld doctor --sweep` prunes `ci-red.json` records; `human-head.json` is pruned by the sweep itself but not by doctor's sweep.
- A person's push while a build run is live is left to that run's `collect`, whose push the lease then refuses; the PM sees a `GitError` and re-dispatches, and the retry fast-forwards.
- The second half of residual 10 as written ("a human relabel is undone") is addressed by removing the need for the relabel; a relabel to `in_review` on a red head is still returned to `in_progress`, by design.
