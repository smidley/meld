# Handoff: where Meld stands and how to continue

This file is rewritten at every milestone that ends in a pull request, so that any AI coding agent (or person) can pick the project up cold. Read it first, then `AGENTS.md`, then the spec.

**Milestone:** plan 3 merged (PR #3, `aa6ca09`) on 2026-09-04. Plans 1, 2, and 3 of the design are on `main`; plan 4 is next and unwritten.

## What Meld is

Meld lets N independent AI coding systems ("groups", each with its own bot GitHub account, a long-lived orchestrator, a long-lived PM, and short-lived headless workers in git worktrees) build one GitHub-hosted product together. GitHub is the only shared state: issues are the work queue, assignment is the claim lock, a per-milestone coordination issue is the conversation, and every comment there starts with a `[<party>] <VERB> [<ref>]` header. "LLM decides, Python enforces": the `meld` CLI computes the legal actions for the calling party and refuses anything else, posting `PROTOCOL-VIOLATION` where every group and the human can see it.

## Authoritative documents, in order

1. `docs/superpowers/specs/2026-09-03-meld-design.md` — the binding design. Section 3 records the decisions already made (do not re-litigate them); section 21 records borrowed ideas, what landed where, and **"Carried into plan 4"**, the exact list of obligations the next plan inherits.
2. `docs/superpowers/plans/` — one implementation plan per phase. Each plan's header lists the decisions taken while writing it and the global constraints every task obeys. Plans 1–3 are done; write plan 4 in the same format (every task carries its own tests, steps, and commit).
3. `README.md` — the command table (18 verbs), exit-code contract, product standards, and the macOS hidden-`.pth` note.
4. `AGENTS.md` — conventions for any agent editing this repo.

## Repository map

- `src/meld/` — `config.py` (`.meld.toml`, parties), `github.py` (the only GitHub path, over `gh api`), `grammar.py` (header grammar, agreement protocol), `board.py` (typed board, derivations: leases, challenges, audits), `assemble.py` (board from GitHub), `rules.py` (**the only legality authority**), `actions.py` (claim/release), `posting.py` (`meld post`), `state.py` (run records, digests under `$MELD_HOME`), `results.py` (review/audit verdict files), `waiting.py`, `status.py`, `context.py`, `gitops.py` (the only git runner), `brief.py` (worker briefs, product standards, skills), `adapters/` (Claude Code and Codex launchers), `dispatch.py` (dispatch/tick/collect/reap), `review.py` (review/merge), `audit.py` (audit/reopen), `challenge.py`, `tagging.py`, `roster.py`, `prompts.py` + `prompts/*.md` (role and worker prompts), `runners.py` (`meld run`), `cli.py`.
- `tests/` — 335 hermetic tests: real git repos and real subprocesses, GitHub through `tests/fakegh.py`, fixtures under `tests/fixtures/board1/`, helpers in `conftest.py`, `factories.py`, `productfixtures.py`, `gitfixtures.py`, `fakeadapter.py`. `tests/smoke/` runs the real CLIs and is opt-in (`task test:smoke`; it spends tokens).
- `Taskfile.yml` — `task test`, `task test:smoke`, `task status`, `task run GROUP=…`.

## Working on the repo

```bash
uv sync
uv run pytest -q          # expect all green, no warnings
uv run pytest tests/smoke -q -rs   # 2 skipped unless MELD_SMOKE=1
```

Python is pinned to 3.13 (`.python-version`); if `uv run meld` fails with `ModuleNotFoundError`, see the README's hidden-`.pth` note. Never run the smoke tests to "check" something — they launch real AI CLIs.

## How the previous phases were built (repeat this)

1. **Spec first.** Changes to behaviour start in the spec; the spec wins over any plan.
2. **Plan, then execute task by task.** Each task: write the failing tests, implement, run the focused tests then the whole suite, commit with a plain conventional message. Every task got an independent review; every finding got a recorded ruling; a whole-branch review ran before the PR.
3. **Hermeticity is a hard rule.** No network, no real CLI, no dependence on the host's `~/.claude`, gitconfig, or wall clock; inject `now`; every `load_context` in tests passes `adapter_for=`.
4. **Security invariants.** Workers never hold GitHub credentials; only PM-side code pushes/merges/reviews with the group's token; anything worker-authored that reaches GitHub goes through `neutralise_closers`; `[joint]`, `LEASE`, `EXPIRED`, `IGNORED-COMMENT` are framework-posted only; challenge verbs go through `meld challenge`.
5. **Reconcile docs at the end of each plan** (spec sections touched, README table, this file).

## Decisions taken in plan 3 that the owner should know

- Commit statuses (`meld/review/<group>`) stand in for the spec's neutral check runs, because personal tokens cannot create check runs. Bot tokens therefore need **Commit statuses: read and write**; a token without it degrades to an empty status map plus a `state_errors` note.
- A PR author's account cannot approve or request changes on its own PR on GitHub, so after another group push-fixes, the author group's verdict is a comment review and its `success` status counts toward the quorum.
- The Claude adapter loads user-level skills (`--setting-sources user,project`) and blanks every scrubbed credential name in the session settings; the Codex adapter is unchanged (its `--ignore-user-config` does not touch skills).
- Every brief and role prompt carries `## Product standards` (`.meld/STANDARDS.md` in the product repo, else the framework default: visual design, performance, and security are acceptance criteria; decide with best practice and research; surface only approval-worthy decisions) and `## Skills available` (installed skills with paths, capped at 30).

## What is next (plan 4)

Everything in spec section 21 "Carried into plan 4": decision memory (`decide`, `remember`, `recall`), `meld chat`, the plan gate (`meld plan`, the approve flip, PLAN-ADD ACK), `meld setup`, `meld doctor`, the notifier, CI-red-as-request-changes, off-protocol change detection, `IGNORED-COMMENT` posting, digest gaps, `pr_for_issue` with several PRs, and the fuller skills story (relevance-matched skills, a shared `.meld/skills/`, `skills_used`, `skill:<name>` hints, a Codex path). Keep the same process, and end the plan with a task that rewrites this file for the next handoff.
