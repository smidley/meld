# Plan 40 — a conflicted pull request has a way back (2026-10-27)

**Goal.** A pull request that GitHub refuses to merge for conflicts goes back to build on its own, and a build after a merged PR never produces one.

**From the spec.** Section 9 (worktrees and retries), section 13 step 4 (`meld merge`), section 17 (the CI-red row this copies).

**What happened.** On the scratch product PR #7 squash-merged, an audit failed it and sent issue #2 back to build, and the retry continued the old branch: PR #9 carried the three pre-squash commits plus the fix and conflicted with the squash of itself. Both PMs got 405 from `meld merge` for hours, posted NEEDS-HUMAN nine times, and asked the owner to run git by hand. A memory PR (#17, `lessons.md`) sat in the same state.

## Decisions (each reversible)

1. **A build after a merged PR starts over.** When a PR from the issue's branch has merged, `dispatch` discards the worktree and local branch and creates the branch fresh from the default branch; `collect`'s push rewrites the stale remote branch with `--force-with-lease`. *Not taken:* a new branch name per attempt (every record, PR body and brief names `meld/<issue>-<slug>`).
2. **A conflict is a request for changes nobody posted.** `meld merge` catches the 405, confirms `mergeable_state` is `dirty` on a fresh read, comments on the issue, moves it from in-review to in-progress once, and replies `conflict` plus `next` instead of raising. The same shape as CI red (spec 17). *Not taken:* a new verb; the PM already calls `merge`, and a verb the PM has to discover is one more thing to get wrong.
3. **The brief names the files.** A worker holds no credentials and cannot read the conflict on GitHub, so `dispatch` runs a local `git merge-tree` against the freshly fetched default branch and the brief's `## Merge conflicts to resolve first` section lists the paths. *Not taken:* carrying `mergeable_state` on the board, which costs one GitHub read per open PR per status call and is null until GitHub computes it.
4. **A conflicted memory PR is closed.** It has no issue and no worker; `remember`/`decide` rewrites it from the current default branch.

## Tasks

1. `gitops.py` (`create_worktree(fresh=)`, `conflicts_with`), `github.py` (`close_pull`), `dispatch.py` (the fresh start, the probe, the force-with-lease condition, `conflicts` in the result), `brief.py` (`merge_conflicts`, `render_brief(conflicts=)`), `review.py` (`_conflicted`), `prompts/pm.md`; `tests/test_gitops.py`, `tests/test_dispatch.py`, `tests/test_merge.py`, `tests/test_brief.py`.
2. Spec sections 9, 13 and 17; `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings.

## Carried

- A merged-but-wrong PR still has no post-merge request-changes path of its own: the audit's FAIL sends the issue back to build, and this plan makes that retry land cleanly, which is what the scratch run needed.
- The repeated NEEDS-HUMAN posts are the PM's choice; the prompt now says never to post one for a conflict, but nothing refuses a repeat.
- Memory PR churn (four "Lesson from #4" PRs) is untouched.
