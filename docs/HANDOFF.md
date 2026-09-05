# Handoff: where Meld stands and how to continue

This file is rewritten at every milestone that ends in a pull request, so that any AI coding agent (or person) can pick the project up cold. Read it first, then `AGENTS.md`, then the spec.

**Milestone:** plan 4 (decision memory, the plan gate, `meld chat`, and PM housekeeping) complete on branch `plan-4-memory-human-loop` — all 11 tasks landed, 414 tests passing, no warnings. This commit is plan 4's own last task (spec/README/handoff reconciliation); the branch has not been merged to `main` yet. Plans 1–4 are done; plan 5 is next and unwritten.

## What Meld is

Meld lets N independent AI coding systems ("groups", each with its own bot GitHub account, a long-lived orchestrator, a long-lived PM, and short-lived headless workers in git worktrees) build one GitHub-hosted product together. GitHub is the only shared state: issues are the work queue, assignment is the claim lock, a per-milestone coordination issue is the conversation, and every comment there starts with a `[<party>] <VERB> [<ref>]` header. "LLM decides, Python enforces": the `meld` CLI computes the legal actions for the calling party and refuses anything else, posting `PROTOCOL-VIOLATION` where every group and the human can see it. As of plan 4, agreed decisions and lessons persist as versioned files that auto-approved memory PRs carry into the repo (`meld decide`/`meld remember`/`meld recall`), a human runs the plan gate and talks to every group from one console (`meld plan`, `meld inbox`, `meld chat`), and each PM's `meld tick` handles red CI, off-protocol GitHub changes, stray comments, and Slack nudges without being asked.

## Authoritative documents, in order

1. `docs/superpowers/specs/2026-09-03-meld-design.md` — the binding design. Section 3 records the decisions already made (do not re-litigate them); section 21 records borrowed ideas, what landed where, and **"Carried into plan 5"**, the exact list of obligations the next plan inherits.
2. `docs/superpowers/plans/` — one implementation plan per phase. Each plan's header lists the decisions taken while writing it and the global constraints every task obeys. Plans 1–4 are done; write plan 5 in the same format (every task carries its own tests, steps, and commit).
3. `README.md` — the command table (25 verbs), exit-code contract, product standards, and the macOS hidden-`.pth` note.
4. `AGENTS.md` — conventions for any agent editing this repo.

## Repository map

- `src/meld/` — `config.py` (`.meld.toml`, parties), `github.py` (the only GitHub path, over `gh api`; plan 4 added `create_issue`, `update_milestone`, `get_contents`, `issue_events`, `list_labels`, `create_label`, `search_issues`), `grammar.py` (header grammar, agreement protocol — `Agreement` now carries `joint_verified`/`joint_verb`/`joint_author`, so nothing trusts a hand-written `[joint]` comment), `board.py` (typed board, derivations: leases, challenges, audits; `pr_for_issue` now prefers the merged PR once its issue is closed; `PullRequest.memory` reads the `meld-memory:` marker), `assemble.py` (board from GitHub), `rules.py` (**the only legality authority** for `claim`/`dispatch`/`release`/`review`/`merge`/`reopen`/`collect`/`tag`/`reap`/`challenge` — `plan`/`decide`/`remember` are new in plan 4 and still refuse inline in their own modules rather than through a `rules.py` predicate; see "Decisions for the owner" below), `actions.py` (claim/release), `posting.py` (`meld post`), `state.py` (run records; `board_digest` now carries `challenges`, `audits`, and each PR's commit `statuses`, and `CHANGE_KINDS` gained `audits`), `results.py` (review/audit verdict files), `waiting.py`, `status.py` (`coordination.agreements` now shows `status` and `joint_verified` per ref), `context.py`, `gitops.py` (the only git runner), `brief.py` (worker briefs, product standards, skills, `WorkerResult.skills_used`; also owns `_local_hits`/`recall_snippets`, re-exported by `memory.py` so `dispatch.py` never has to import `memory.py` — see "Decisions for the owner"), `roster.py`, `runners.py`, `models.py`.
  - **New this plan:** `memory.py` (`.meld/decisions/`, `.meld/memory/lessons.md`, content hashing, `meld decide`/`remember`/`recall`), `plan.py` (the plan gate: `parse_plan_items`, `meld plan issues|approve|ack`), `chat.py` (`meld inbox`, the `meld chat` REPL and its slash commands), `notifier.py` (`meld notify`, the Slack webhook payload and per-party throttle state), `housekeeping.py` (`ci_red_sweep`, `flag_ignored_comments`, `off_protocol_sweep`, all called from `dispatch.tick`).
  - `review.py` (`meld review`/`meld merge`; plan 4 added `verify_memory_pr` and the memory-PR auto-approve/request-changes path — no reviewer worker involved), `audit.py` (audit/reopen), `challenge.py`, `tagging.py`, `adapters/` (Claude Code and Codex launchers), `dispatch.py` (dispatch/tick/collect/reap; `tick` now also runs housekeeping and the notifier; `collect`'s finish comment carries a worker's `skills used:` line; briefs render `## Relevant memory`), `prompts.py` + `prompts/*.md` (role and worker prompts — `orchestrator.md`/`pm.md`/`_preamble.md` now cover planning in the item format, memory, and the human console), `cli.py` (adds `decide`, `remember`, `recall`, the `plan` command group, `inbox`, `chat`, `notify`).
- `tests/` — 414 hermetic tests: real git repos and real subprocesses, GitHub through `tests/fakegh.py`, fixtures under `tests/fixtures/board1/`, helpers in `conftest.py`, `factories.py`, `productfixtures.py`, `gitfixtures.py`, `fakeadapter.py`. New this plan: `test_memory.py`, `test_plan.py`, `test_chat.py`, `test_notifier.py`, `test_housekeeping.py`, plus extensions to `test_github.py`, `test_grammar.py`/`test_agreement.py`, `test_board.py`, `test_state.py`, `test_rules_prs.py`, `test_review.py`, `test_brief.py`, `test_collect.py`, `test_role_prompts.py`, `test_prompts.py`, `test_status.py`. `tests/smoke/` runs the real CLIs and is opt-in (`task test:smoke`; it spends tokens).
- `Taskfile.yml` — unchanged this plan: `task test`, `task test:smoke`, `task status`, `task run GROUP=…`. There is no `task meld:decide`/`meld:plan`/etc.; those verbs are invoked directly as `uv run meld ...` (or through a running orchestrator/PM).

## Working on the repo

```bash
uv sync
uv run pytest -q          # 414 passed, no warnings
uv run pytest tests/smoke -q -rs   # 2 skipped unless MELD_SMOKE=1
```

Python is pinned to 3.13 (`.python-version`); if `uv run meld` fails with `ModuleNotFoundError`, see the README's hidden-`.pth` note. Never run the smoke tests to "check" something — they launch real AI CLIs.

## The SDD process used for this plan (repeat this)

1. **Spec first.** Changes to behaviour start in the spec; the spec wins over any plan. Where plan 4's shipped code differed from what its own header proposed, the spec was written to describe what shipped, and the difference is noted inline (see "Decisions for the owner" and spec section 21).
2. **Plan, then execute task by task.** `docs/superpowers/plans/2026-09-04-plan-4-memory-human-loop.md` has 11 tasks; each got its own failing tests, implementation, a focused-then-whole-suite test run, and a plain conventional commit. Every task got an independent review; every finding produced a recorded ruling (fix now, or an explicit deferral with a cost-if-wrong note) — the ledger lives in `.superpowers/sdd/2026-09-04-plan-4-memory-human-loop/` for this plan (rulings, task briefs, and this task's own report). A whole-branch review runs before a milestone PR is opened.
3. **Hermeticity is a hard rule.** No network (plan 4's Slack webhook takes an injected `post` callable in tests), no real CLI, no dependence on the host's `~/.claude`, gitconfig, or wall clock; inject `now`; every `load_context` in tests passes `adapter_for=`.
4. **Security invariants.** Workers never hold GitHub credentials; only PM-side code pushes/merges/reviews/commits with the group's token and bot identity (memory PRs included — `meld decide`/`meld remember` commit and push PM-side, never from a worker); anything worker- or stranger-authored that reaches GitHub goes through `neutralise_closers`; `[joint]`, `LEASE`, `EXPIRED`, `IGNORED-COMMENT` are framework-posted only; challenge verbs go through `meld challenge`.
5. **Reconcile docs at the end of each plan** (spec sections touched, README table, this file) — this commit is that step for plan 4.

## Decisions for the owner (plan 4; each is reversible)

- **Memory PRs are auto-approved from board provenance, not human or worker review.** `meld review N` on a decision PR checks the file's content hash against a *verified* `[joint] DECISION` comment (written by the proposer's own bot after the agreement round actually closed — `Agreement.joint_verified`); on a lesson PR it checks the hash against the appended entry and confirms the referenced issue/PR is no longer `blocked`/`needs_human` and isn't challenged. Either way `meld dispatch --kind review` refuses to spawn a reviewer worker on it — the check is code, not a model's judgment call.
- **Lessons are auto-approved on evidence of resolution, not on agreement**, unlike decisions (which require a verified `[joint] DECISION`). There is no `PROPOSE`/`AGREE` round for a lesson; `meld remember` records what already happened, and `meld review` trusts the board's own state (the item resolved) instead.
- **The plan gate is a label, not a merged file.** `meld plan approve` adds `meld:plan-approved` to the coordination issue and flips every `meld:planned` issue to `ready`; nothing about the milestone description or the issues themselves requires a PR or a human diff review beyond reading the `[joint] PLAN` comment in GitHub. `plan`'s own legality (who may call `plan issues`/`approve`/`ack`, and when) lives as inline checks in `plan.py`, not as `rules.py` predicates — so unlike `claim`/`review`/`merge`/etc., `meld status`'s `legal` arrays never list `plan`, `decide`, or `remember`. This is tracked (spec section 21, "Carried into plan 5") rather than accidental; it does not change what is actually legal, only whether `meld status` advertises it.
- **The notifier is per party.** Each PM's own `meld tick` reads `[notify] slack_dm_webhook_env` and throttles independently in its own `$MELD_HOME/state/<group>/pm/notify.json`. With N configured groups, a needs-human issue can generate up to N separate Slack messages (one per group's PM) rather than one for the milestone. This is deliberate — a group that has gone quiet must never be able to silence the human's only channel — but it means duplicate nudges are expected, not a bug. A shared dedup marker is the plan-5 option if that turns out to be too noisy in practice.
- **Housekeeping posts (`IGNORED-COMMENT`, off-protocol `PROTOCOL-VIOLATION`) come from the alphabetically first configured group's PM only**, so a stranger's comment or an out-of-band label change is flagged once on the coordination issue rather than once per group — every PM's own cursor still advances locally, so none of them re-processes it, but only the first group's PM actually posts.
- **CI red is handled by the author group's own `tick`, as a comment plus a label swap — not a GitHub `REQUEST_CHANGES` review.** `ci_red_sweep` posts a comment naming the red PR (durable evidence, posted before anything else can fail) and moves the linked issue back to `in_progress`, once per head. `merge` was already refused while checks are red regardless; this just gets the issue back into a worker's hands instead of sitting in `in_review` doing nothing.

## What is next (plan 5)

Everything in spec section 21 "Carried into plan 5":

- `meld setup` and `meld doctor` — neither exists yet. `setup` needs labels with colors, the milestone, the coordination issue, a CODEOWNERS entry, branch protection (the `meld/review/*` statuses informational, not required), a starter `.meld.toml` with adapter-populated model fields, a public-repo refusal without `--allow-public`, and a CI `paths`-filter warning. `doctor`'s checklist is long — both CLIs installed/logged in, token identity vs. configured bot login, `gh`'s own login, per-bot git identity, hidden `.pth` detection, models present in `models.toml`, each adapter's command-timeout ceiling, `Commit statuses: write` on every bot token, `settings.json` `env`-block leaks, and a scratch sweep of `results/superseded`, `results/submitted`, `runs/<n>-gh`, `runs/<n>-gitconfig`, and stale `worktrees/<group>/memory-*` directories.
- The fuller skills story: relevance-matched skills per brief (not just the whole roster by title), a shared `.meld/skills/` in the product repo synced into each adapter's own skill directory, `skill:<name>` dispatch hints, and a Codex-specific invocation path.
- Leases tied to run records (today's same-group re-claim fix is a comment-level patch, not structural).
- Worker-authored commit messages can still close issues on squash-merge (`neutralise_closers` doesn't reach git commit messages).
- `ResourceWarning`s under `-W error` from detached worker/memory-PR `Popen` objects.
- The author group's own request-changes verdict (posted as a `COMMENT` review, like its approve) never counts toward the review ping-pong cap.
- `meld status`'s `legal` array can say `approve` is illegal for a PR's author group at the exact moment `meld review` would actually accept one from it (see "Decisions for the owner" above).
- `Board.pr_for_issue` when several PRs are open for one issue at once (the closed-issue case is fixed).
- A milestone-end audit trigger (today `audit.due` only fires from batch size/age).
- `status_error` on an already-approved memory-PR review is never retried.
- Orphaned `ci-red.json` records for PRs closed mid-handling are never pruned.
- `_local_hits` scans every decision and lesson file on every dispatch; wants an index or cache as `.meld/decisions/`/`lessons.md` grow.
- `MARKER_RE` is duplicated between `memory.py` and `board.py`; move it to a leaf module.
- Two cosmetic Task-10 prompt-wording notes (`pm.md`'s clause ordering in the `tick` sentence; `_preamble.md`'s phrasing of what `/approve` touches) and a short list of minor, behaviour-neutral review nits (see spec section 21 for the full text of each) round out the list.

## Git and identity conventions (from `AGENTS.md`)

- The spec wins; don't re-litigate spec section 3 or any plan header's decisions.
- `rules.py` is meant to be the only legality authority — see the caveat above for `plan`/`decide`/`remember`, tracked as plan-5 work rather than silently ignored.
- `github.py` is the only GitHub path, `gitops.py` the only git runner; adapters are the only code that knows how a CLI is launched.
- Workers never hold credentials; anything worker-authored that reaches GitHub passes through `neutralise_closers`.
- `uv run pytest -q` must be green with no warnings before every commit; real git repos and `FakeGh` over real subprocesses, no network, no real CLI, no host `~/.claude`/gitconfig/wall-clock dependence.
- Never set `MELD_SMOKE=1` unless the owner asked for a smoke run.
- Commits are plain conventional messages (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`), no attribution trailers, one logical change per commit, authored with the repo-local git identity already configured.
- Pull requests go from a feature branch to `main` with a body in the style of PRs #1–#3; rewrite `docs/HANDOFF.md` (this file) for the new state before opening one.
- Standard library plus `click` only in `src/meld/`; Python 3.13.
