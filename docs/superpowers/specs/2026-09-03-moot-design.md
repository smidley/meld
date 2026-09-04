# Moot: multi-AI coordination framework — design

Date: 2026-09-03
Status: draft for review
Working name: **Moot** (an Anglo-Saxon assembly that debated and decided). Package and command are both `moot`.

## 1. Purpose

Moot lets N independent AI coding systems (day one: Claude Code and OpenAI Codex CLI) work on one GitHub-hosted product together, with a human able to steer, contribute, and intervene. Each AI system is a **group** with an orchestrator, a project manager (PM), and many short-lived worker agents. Groups negotiate plans, split work by free claim, review and modify each other's code, and challenge each other's claims. The bet is that independent models checking each other produce a better product than either alone.

Everything durable lives in GitHub (issues, PRs, milestones, files in the product repo). Everything the models are not allowed to get wrong is enforced by Python, not prompts.

## 2. Goals and non-goals

Goals

- Mostly autonomous execution inside a human-approved plan.
- N groups, each a different AI CLI, added by writing one adapter.
- Massive parallelism: many workers of the same speciality at once.
- Cross-group review with the power to modify, and a merge quorum that no group can satisfy alone.
- Hallucination protection: evidence requirements, challenges, independent audit, drift detection.
- Shared, versioned memory of decisions and lessons that orchestrators consult.
- One human console to chat with all orchestrators and receive one agreed answer.
- Every rule that matters is code with tests.

Non-goals for v1

- Webhooks or any hosted service. Polling only.
- Running groups on separate machines. Designed for portability; tested on one machine.
- Fine-grained token scoping per branch or per label. Mitigated by giving workers no GitHub credentials at all (section 12).
- Judging model quality automatically. That is done by humans watching the first milestones and turning bad decisions into prompt edits or new invariants.

## 3. Decisions made during design

| Topic | Decision |
|---|---|
| Autonomy | Human approves the plan per milestone; agents run autonomously through merge within it |
| Framework location | Its own repo (`~/Documents/moot`), reusable across products |
| Orchestrator channel | GitHub coordination issue, one per milestone |
| Identity | One GitHub machine account per group; assignee is the claim lock |
| Topology | Orchestrator and PM are long-lived headless processes per group; each worker is a fresh headless run in its own worktree |
| Cross-modify | Reviewers may push to the PR branch; last committer's group cannot approve |
| Work split | Free claim, first assignment wins |
| Hosting | This Mac first, portable by design (single entry point, env-var secrets) |
| Language | Python 3.12, `uv`-managed, `gh` under the hood |
| Framework shape | LLM decides, Python enforces: `moot status` returns legal actions, verbs refuse anything else |
| Groups | N, configured in `.moot.toml`; two shipped adapters |
| Name | Moot |

## 4. Architecture

Three layers in one Python package.

1. **`moot` CLI.** The only way any agent touches GitHub. Verbs: `status`, `wait`, `claim`, `release`, `dispatch`, `collect`, `post`, `review`, `merge`, `plan`, `decide`, `remember`, `recall`, `roster`, `models`, `reap`, `reopen`. Every mutating verb recomputes the legal-action set for the calling party and refuses anything outside it, naming the violated rule. The calling party is `MOOT_GROUP` (a group name) plus `MOOT_ROLE` (`orchestrator` or `pm`), or `human` when neither is set and `gh` is on a listed human account. State lives under `MOOT_HOME`, default `~/.moot`.
2. **Role prompts.** Markdown, one per role, rendered identically for every adapter: `orchestrator.md`, `pm.md`, `workers/<speciality>.md`. Shared preamble carries the cardinal rules, the `moot status` first habit, the header grammar, and a pointer to the product repo's own agent instructions, which win over framework defaults.
3. **Runners and adapters.** `moot run <group>` starts a group's orchestrator and PM processes with the right prompts and environment, restarting them on a schedule. `moot setup` prepares a product repo. `moot chat` is the human console. `moot doctor` checks the machine. Each supported CLI is an adapter module that knows how to render prompts, launch a headless run with a model and effort, and which models and efforts are valid.

### Repo layout

```
moot/
  pyproject.toml            # uv, Python 3.12, console_script "moot"
  src/moot/
    cli.py                  # argument parsing, dispatches to verbs
    board.py                # typed board model built from GitHub JSON
    github.py               # gh wrapper: conditional requests, cursor, rate-limit backoff
    grammar.py              # coordination header parser and round counters
    rules.py                # legal-action computation; every invariant lives here
    dispatch.py             # worktree + brief + headless run + run records
    review.py               # quorum math, push-fix flow, ping-pong cap
    memory.py               # decisions, lessons, recall
    roster.py               # worker type discovery
    chat.py                 # human console
    runners.py              # long-lived process supervision and restart
    adapters/
      base.py               # Adapter protocol
      claude.py
      codex.py
  src/moot/data/models.toml   # per adapter: models, efforts, context class, tier
  src/moot/prompts/           # packaged with the module, not a top-level directory
    _preamble.md  orchestrator.md  pm.md  coordination-issue.md
    workers/  backend.md frontend.md security.md qa.md reviewer.md auditor.md docs.md
  tests/
    fixtures/               # recorded, redacted gh JSON
    test_rules.py test_grammar.py test_dispatch.py test_review.py test_memory.py
    smoke/                  # opt-in, real CLIs, scratch repo
  Taskfile.yml              # moot:setup moot:run:<group> moot:chat moot:doctor moot:test
  README.md
```

The product repo holds only `.moot.toml`, `.moot/workers/` (product-specific worker prompts), `.moot/decisions/`, `.moot/memory/`, and a CODEOWNERS entry for `.moot/workers/`.

## 5. Configuration

### `.moot.toml` (product repo)

```toml
[repo]
name = "owner/name"
default_branch = "main"
private = true                     # setup refuses to proceed on a public repo without --allow-public

[humans]
accounts = ["empowerScott"]         # only these GitHub users may speak as [human]

[groups.claude]
cli = "claude"
bot = "moot-claude-bot"
token_env = "MOOT_TOKEN_CLAUDE"
concurrency = 8                    # max concurrent building workers
review_concurrency = 4             # max concurrent reviewer/auditor workers
[groups.claude.orchestrator]
model = "claude-opus-5"
effort = "high"
[groups.claude.pm]
model = "claude-sonnet-5"
effort = "medium"

[groups.codex]
cli = "codex"
bot = "moot-codex-bot"
token_env = "MOOT_TOKEN_CODEX"
concurrency = 8
review_concurrency = 4
[groups.codex.orchestrator]
model = "<populated from `moot models` at setup>"
effort = "high"
[groups.codex.pm]
model = "<populated from `moot models` at setup>"
effort = "medium"

[review]
quorum = 2                         # distinct non-last-committer groups; effective value is min(quorum, N-1)
pingpong_cap = 3
security_paths = ["auth/**", "**/secrets*", "infra/**"]

[agreement]
amend_cap = 3                      # draft/amend rounds before majority-or-dissent

[polling]
interval_seconds = 30
wait_timeout_seconds = 300

[runners]
restart_hours = 4
roster_refresh_minutes = 60
silence_grace_minutes = 30         # before a group continues SOLO
summary_cadence_minutes = 60       # PM summary comment

[timeouts]                         # worker wall clock by size label
S = "20m"
M = "45m"
L = "90m"

[budget]                           # per milestone, in worker runs by model tier
small = 200
medium = 80
large = 20
wallclock_hours = 72

[notify]
slack_dm_webhook_env = "MOOT_SLACK_DM_WEBHOOK"   # optional
repeat_after_minutes = 120
```

Model identifiers shown for Claude are examples. Values written as `<populated ...>` are filled by `moot setup` from live adapter output, not hand-typed; `moot doctor` re-verifies every configured model against `models.toml`.

### `models.toml` (framework)

Per adapter, per model: `context_class` (`small` | `medium` | `large`), `efforts` (ordered list), `tier` (`small` | `medium` | `large`, used for budgets), and `default_for` (`orchestrator` | `pm` | `worker`, optional). Model identifiers are populated at implementation time from each CLI's live model list and re-verified by `moot doctor`; this spec does not pin them because they change faster than the framework.

`size` to `context_class` compatibility: `S` runs on any class, `M` requires `medium` or `large`, `L` requires `large`.

## 6. GitHub data model

- **Milestone.** One per approved unit of work. Description holds the approved plan. Exactly one coordination issue.
- **Coordination issue.** Label `moot:coordination`, title `Coordination: <milestone>`. All cross-group and human-to-group conversation happens here. Every comment begins with a header line (section 7).
- **Work issue.** One per task, in the milestone. Labels:

| Label | Meaning |
|---|---|
| `moot:planned` | Created by the plan, not yet approved |
| `moot:ready` | Approved, unclaimed |
| `moot:in-progress` | Claimed, worker running or pending |
| `moot:in-review` | PR open, awaiting review quorum |
| `moot:blocked` | Needs orchestrator resolution |
| `moot:needs-human` | Needs a human before retry |
| `moot:challenged` | A CHALLENGE is open against it |
| `worker:<speciality>` | Role that owns it |
| `model:<id>` `effort:<level>` `size:S|M|L` | Set by the orchestrator, read by dispatch |
| `area:<x>` | Optional, product-defined |

- **Assignee is the claim lock.** `moot claim` reads the issue, refuses if any assignee exists, assigns the caller's bot, re-reads, and releases if it lost a race. A human assigning themself is an equally valid claim.
- **Pull request.** Branch `moot/<issue>-<slug>`. Body contains `Closes #<issue>` and a `verified-by:` line. Branch protection: require 1 approval (GitHub backstop; the real quorum is enforced by `moot merge`), dismiss stale approvals on push, require the product's CI check, restrict pushes to the bots and listed humans. Squash merge only.
- **Decision and memory files.** `.moot/decisions/NNNN-<slug>.md`, `.moot/memory/lessons.md` in the product repo (section 15).
- **Worker comments.** The framework posts exactly two comments per worker run on its issue: start (worktree, model, effort, size) and finish (commit range, outcome, `verified-by:`). Workers themselves post nothing.

## 7. Coordination grammar

Every coordination comment starts with one header line:

```
[<party>] <VERB> [<ref>] [<qualifier>]
```

- `<party>` is a group name, `<group>:pm`, `human`, or `joint`. `joint` may only be written by `moot` after quorum is verified.
- Comments from accounts that are not a configured bot or an allow-listed human are ignored by the parser and flagged once as `IGNORED-COMMENT` in the next PM summary. This is the prompt-injection defense for the channel.
- Malformed headers are ignored and logged, never fatal.

Verbs:

| Verb | Written by | Purpose |
|---|---|---|
| `ROSTER`, `ROSTER-CHANGE` | group | Inventory of worker types available to that group |
| `CONFIG` | group | Running models and efforts for orchestrator and PM |
| `PROPOSE`, `AMEND`, `AGREE`, `DISSENT` | group | Agreement protocol rounds on a referenced item |
| `PLAN`, `PLAN-ADD` | joint | Agreed decomposition; additions mid-milestone |
| `ASK`, `DIRECTIVE`, `AMEND`, `APPROVE`, `ACK`, `PROVIDED`, `RESOLVED`, `HALT`, `RESUME`, `REVERT`, `RELEASE` | human | Human inputs |
| `DRAFT`, `AGREE`, `AMEND` | group | Chat deliberation on an `ASK <id>` |
| `ANSWER` | joint | Agreed reply to an `ASK`; qualifier `DISAGREE` when no majority |
| `REVIEW-REQUEST #<pr>` | group:pm | Ask other groups to review |
| `STATUS` | group:pm | Periodic summary; leads with needs-human and budget |
| `NEEDS-HUMAN #<issue>`, `BUDGET` | group or group:pm | Blocking asks to the human |
| `CHALLENGE <ref>`, `EVIDENCE <ref>`, `RETRACT <ref>`, `UPHELD <ref>`, `DISMISSED <ref>` | group or human | Hallucination protection (section 14) |
| `AUDIT <ref>` | group or group:pm | Independent audit findings |
| `DRIFT` | group | Plan drift |
| `DECISION <id>` | joint | Agreed decision, mirrored to a file |
| `PROPOSE-WORKER <name>` | group or group:pm | New worker type proposal with draft prompt |
| `WORKER <name>` | joint | Agreed new worker type, closing a `PROPOSE-WORKER` round |
| `PING`, `SOLO`, `RESUMED`, `PROTOCOL-VIOLATION` | group | Liveness and enforcement notices |

Required fields by verb are enforced by the parser: `PLAN`, `DECISION`, `ANSWER`, `EVIDENCE`, and every worker finish comment must carry a `verified-by:` line naming a command run, test executed, or URL read. Missing it makes the comment invalid.

### Agreement protocol

Used for `PLAN`, `PLAN-ADD`, `DECISION`, `ANSWER`, and `PROPOSE-WORKER`. Each round closes with the
matching joint verb; a `PROPOSE-WORKER <name>` round closes with `WORKER <name>`, not with a joint
`PROPOSE-WORKER` (there is no such joint verb).

1. One group posts `PROPOSE <id>` (or `DRAFT <id>` for chat) with full text.
2. Every other group posts `AGREE <id>` or `AMEND <id>` with edited full text. An amend restarts the round with the amended text as the proposal.
3. When every group has agreed to the same text, the proposing group runs `moot post --joint <VERB> <id>`, which verifies the agreement on GitHub and writes the `[joint]` comment. Nothing else may write `[joint]`.
4. A `DISSENT` counts as a cast vote rather than leaving the round open, so the outcome is decided as soon as every group has either agreed or dissented — not when a round cap expires. With a strict majority, the majority text is posted as `[joint]` with a `DISSENT` section listing minority positions; with no majority (including any 2-group tie) the item is posted as `[joint] <VERB> <id> DISAGREE` containing all positions, and the human decides.
5. `agreement.amend_cap` bounds only the number of `AMEND` and re-proposal rounds. Once it is reached, a group must post `AGREE` or `DISSENT` instead of amending again; it does not by itself decide the outcome. A group that dissented is not locked in: a later `AGREE` from that group supersedes its dissent, as an `AMEND` by any group restarts the round.
6. The `[joint]` comment carries the agreed text verbatim. A caller may add its own note, which is appended under an `## Addendum` heading; it never replaces the agreed text. A ref that already has a `[joint]` comment cannot get a second one.

## 8. Issue lifecycle and legal actions

```
planned ──human APPROVE──▶ ready ──claim──▶ in-progress ──PR opened──▶ in-review ──quorum + CI──▶ merged
                             ▲                 │                          │
                             └────release──────┘                          └─changes requested / CI red─▶ in-progress
                                               │
                                               └─worker fail / timeout / pingpong cap─▶ blocked ─▶ ready | needs-human | closed
merged ──AUDIT reopen──▶ ready
```

Two milestone-level states sit above this: `planning` until `moot:plan-approved` is on the coordination issue, then `executing`. `HALT` overlays either and freezes `claim` and `dispatch` for every group until `RESUME`.

`rules.py` computes, for a board snapshot and a calling party, the set of legal verbs per issue and per PR. Every rule below is a test.

- **claim**: caller is a PM or a human; milestone is `executing`; issue is `ready`; no assignee; caller's roster contains the issue's `worker:` label; caller's count of `in-progress` issues below `concurrency`; caller's budget tier for the issue's model not exhausted; no `HALT`.
- **dispatch**: caller is a PM; caller's bot is assignee; no live run record for the issue; `model`, `effort`, `size` labels present and valid for the caller's adapter; `size` compatible with the model's context class; issue not `needs-human` or `challenged`; no `HALT`.
- **release**: caller is assignee's group or human; issue in `in-progress` or `blocked`. `--force` is human-only and posts a note.
- **review approve**: caller's group did not author the last commit on the PR head; PR not `challenged`; the head committer is known. Request-changes and push-fix are legal for any non-author-of-head group, including when the head committer is unknown, because neither approves anything. Push-fix by group X makes X the last committer. A last commit that maps to no GitHub account leaves the head committer unknown and refuses approve, rather than falling back to the PR author.
- **merge**: approvals on the current head from at least `min(review.quorum, N-1)` distinct groups, none of which authored the head, with human approvals counting as a group; required checks green; no open `CHALLENGE` on the PR; the head committer is known, since the non-committer rule cannot be evaluated without it; no `HALT`. Either group's PM may call it.
- **reopen**: an `AUDIT` comment with a `fail` finding naming the issue exists and was posted by the caller's group; the issue is closed; the caller's group did not author the PR that closed it. Returns the issue to `ready` with the findings attached.
- **collect**: caller's bot is assignee (or requested the review or audit); a run record exists whose process has exited and has not been collected.
- **post**: always legal for any party, except `--joint`, which requires verified agreement. A `PROPOSE` or `DRAFT` on a ref that already has proposal text is refused; a re-proposal after that point is an `AMEND` and counts a round. Refusals here come in two kinds. A **rule-level** refusal — no agreement yet, caller is not the proposer, wrong milestone phase, a ref that already has a `[joint]` comment, a re-proposal on an active ref, the amend cap reached — posts `PROTOCOL-VIOLATION` like any other illegal call. A **usage error** — a verb the party may not post, a human writing `[joint]`, `--joint` without a ref, a missing ref or `verified-by:` line, `DISAGREE` without the qualifier — is the caller's own mistake, reaches no other party, and posts nothing.
- **plan**: legal only in `planning`, or for `PLAN-ADD` in `executing`.

Illegal calls return a refusal naming the rule and post `PROTOCOL-VIOLATION` on the coordination issue so other groups and the human see it. Two cases are refusals without a violation: a **lost claim race**, which is first-assignment-wins working as designed rather than an illegal call, and any refusal of a human, who answers to nobody on the board. The refusal JSON therefore reports `violation_posted` as `true`, `false` (posting it was attempted and failed), or `null` (none was attempted).

## 9. Roles

All roles are headless runs. Per group, two are long-lived and one is short-lived.

**Orchestrator** (long-lived, restarted every `runners.restart_hours`). Loop: `moot wait` filtered to coordination comments, chat, blocked and needs-human issues, roster changes, and drift; act; repeat. Responsibilities: decompose the goal into issues; tag each with `worker`, `model`, `effort`, `size` using the rubric; negotiate with other orchestrators; answer human chat via the agreement protocol; resolve blocked issues; re-tag on retries; propose worker types when the roster has a gap; post `ROSTER`, `CONFIG`, `DRIFT`. It never writes product code and never dispatches.

**PM** (long-lived, same restart schedule, sibling process). Loop: `moot wait`; act; repeat. Responsibilities in `executing` only: claim `ready` issues up to the cap; dispatch with the tagged model and effort; `collect` finished workers; reap merged ones; move labels; post `REVIEW-REQUEST`; dispatch reviewers for other groups' requests; dispatch auditors after merged batches; call `merge` when legal; post `STATUS` on cadence, leading with needs-human and budget; auto-approve decision and lesson PRs whose content matches the agreed comment. It never chooses models. If it disagrees with a tag it posts to the coordination issue and the orchestrator re-tags.

**Workers** (fresh run per task). Day-one roster: `backend`, `frontend`, `security`, `qa`, `reviewer`, `auditor`, `docs`. A worker prompt is a role brief plus the fixed contract: read `.moot-brief.md` first; work only in this worktree; commit small; run the product's tests; write `.moot-result.json` (outcome, summary, `verified-by`, for reviewers a verdict) and exit. Workers have no GitHub credentials and never push, open PRs, comment, label, or merge. The framework does all of that from the result file.

Orchestrator and PM keep no state that is not also on GitHub. Restarting them is always safe, and it never touches running workers, which are independent processes tracked by run records.

### Model and effort selection rubric

The orchestrator prompt carries the rubric as a table with three worked examples. Inputs: task type, blast radius (files and systems touched), ambiguity, first attempt or retry. Outputs: `model`, `effort`, `size`. Rules the rubric states plainly: read `moot models` before tagging; there is no `XL`, split instead; on a retry after failure or changes-requested, step effort up one level or move to a stronger model before trying the same approach again; prefer the cheapest tier whose context class fits the size. The rubric is a prompt and tunable without code. Validity and budget are code.

## 10. Roster

`moot roster` returns worker types available to the calling group, with a one-line description each, from three sources: framework built-ins, the product repo's `.moot/workers/` (a same-named file overrides a built-in), and the skills and plugins installed for the group's CLI on the machine (adapter-provided).

- At start, each orchestrator posts `ROSTER` and `CONFIG` in its opening comment.
- On every restart and every `roster_refresh_minutes`, it re-runs `moot roster`, diffs against its last posted inventory, and posts `ROSTER-CHANGE` on any difference.
- `claim` checks the issue's `worker:` label against the caller's roster.
- The PM reads `moot roster` before each dispatch, never from cache. When a new type better matches a `blocked` or `ready` issue, it suggests re-tagging.
- Gap detection during decomposition triggers `PROPOSE-WORKER`, which follows the agreement protocol, then a `docs` worker is dispatched to write `.moot/workers/<name>.md` and `collect` opens the PR. That PR requires a human approval via CODEOWNERS on `.moot/workers/` in addition to the review quorum. Proposals made during `planning` ride with plan approval instead.

## 11. Dispatch and worker runtime

`moot dispatch <issue>` (or `--pr <n>` for reviewer and auditor runs):

1. Re-check legality (section 8).
2. Create a worktree from the default branch at `$MOOT_HOME/worktrees/<group>/<issue>-<slug>` on branch `moot/<issue>-<slug>`. On retry, check out the existing remote branch instead. Reviewer runs check out the PR head.
3. Render `.moot-brief.md`: role prompt, the fixed contract, issue body, the plan excerpt for this issue, file hints from the orchestrator, relevant decisions from `moot recall` (section 15), and the product's agent-instruction file path. Not the whole plan, not the coordination thread.
4. Spawn the headless run via the adapter with the tagged model and effort, non-interactive, cwd set to the worktree, filesystem permissions scoped to it, no GitHub token in the environment, wall-clock timeout from `size`.
5. Write the run record `$MOOT_HOME/runs/<group>/<issue>.json` (PID, start, worktree, log path, model, effort) and post the start comment.
6. Return immediately.

**Post-run**: `moot status` reports a run as `finished` once its process has exited. The PM then calls `moot collect <issue>`, which does all side effects: read `.moot-result.json`; if commits exist, push the branch and open (or update) the PR with `Closes #<issue>` and the `verified-by:` line; post the finish comment; move the issue to `in-review`. For reviewer runs, submit the verdict via `moot review` (approve, request-changes with the worker's comments, or push-fix which pushes the reviewer's commits and requests re-review from the author group). For auditor runs, post `AUDIT` and, when findings are `fail`, call `reopen`.

**Failures**: exit without a result file, or a result with outcome `failed`, moves the issue to `blocked` with the last 50 log lines posted. A timeout kills the process, keeps the branch, and does the same. `moot reap` removes the worktree and archives the record after merge or close.

## 12. Polling

`moot wait [--timeout S] [--filter ...]` blocks until the board changes or the timeout elapses, then returns the `moot status` JSON plus a `changes` list. Inside, every `polling.interval_seconds`:

- Fetch coordination comments newer than the stored cursor (`since`).
- Fetch open issues in the active milestone with labels and assignees, open PRs on `moot/` branches with reviews and check status.
- Read this group's run records and check whether each PID is alive.
- Diff against `$MOOT_HOME/state/<group>/<role>/snapshot.json`; return on any change. The snapshot is **per party** — a group's orchestrator, that group's PM, and the human each keep their own — because both roles of a group poll the same board and a shared snapshot would let whichever polls second consume the other's changes. The conditional-request ETag cache is **per group**, at `$MOOT_HOME/cache/<group>`, since both roles authenticate with the same token; it is written atomically and a corrupt entry is treated as no cache.

Three or four conditional API calls per cycle, roughly 400 requests an hour idle against a 5,000 per hour per-token limit. Rate-limit responses trigger exponential backoff and are reported in the returned JSON. Handoff latency is bounded by the interval, about 30 seconds; the timeout only bounds idle re-reads. The wait timeout stays below the calling CLI's command timeout by construction (adapter reports its ceiling; `moot doctor` verifies). `moot claim` re-reads the issue with the ETag cache bypassed before deciding the race.

## 13. Cross-review and merge

For a PR opened on behalf of group A's worker:

1. A's PM moves the issue to `in-review` and posts `REVIEW-REQUEST #<pr>`.
2. Every other group's PM dispatches a `reviewer` (under `review_concurrency`, exempt from the build cap). When the PR touches `review.security_paths`, a `security` reviewer is dispatched as well.
3. Each reviewer ends in approve, request-changes, or push-fix. Push-fix by group B makes B the last committer; A's PM then dispatches its own reviewer on the new head. Quality rules in the reviewer prompt: correctness, security, tests, product conventions, in that order; no style-only change requests; verify claims in the PR body by running them.
4. `moot merge` is legal when approvals on the current head come from at least `min(review.quorum, N-1)` distinct groups that did not author the head (human approvals count), CI is green, and no `CHALLENGE` is open. Whichever PM sees it first merges. Squash, PR title as subject.
5. After `review.pingpong_cap` rounds of changes on one PR, the issue moves to `blocked` and orchestrators must post a resolution.

With two groups the effective quorum is one non-author approval, which is ordinary cross-review. With three or more, it is two, and the first two responders count so extra groups add speed, not rounds.

## 14. Human participation and hallucination protection

### Plan gate

`moot setup --goal "..."` creates the milestone and coordination issue. Orchestrators plan in `planning`: decompose, reconcile via the agreement protocol, post `[joint] PLAN`, write it into the milestone description, and create the work issues as `moot:planned` with all tags. The human reviews in GitHub and either adds `moot:plan-approved` (flipping every issue to `ready`) or posts `[human] AMEND` with notes. Mid-milestone additions are `[joint] PLAN-ADD` and need `[human] ACK` before becoming `ready`.

### Humans as participants

A listed human is a third party in the rules. Self-assignment is a valid claim. A human PR on a `moot/` branch is reviewed by every group, since none authored it. A human approval counts toward the quorum. Human-only powers: `APPROVE` the plan, `ACK` plan additions, `release --force`, `HALT` and `RESUME`, `REVERT #<pr>` (makes a revert PR the top priority), `PROVIDED` and `RESOLVED` for needs-human items, and the CODEOWNERS approval on worker-type PRs.

### Chat

`moot chat` is a REPL. It shows an inbox of open `NEEDS-HUMAN`, `BUDGET`, `DISAGREE`, and `PLAN-ADD` items with ages, and lets you answer inline with the correct header. A typed message posts `[human] ASK <id>`; the orchestrators run the agreement protocol with `DRAFT`, `AGREE`, `AMEND`; the console shows `[joint] ANSWER <id>` by default and streams deliberation with `--verbose`. `DIRECTIVE` requires `ACK` from every group rather than agreed wording, and orchestrators treat it as overriding their plan.

### Needs-human

When a retry depends on a person (credential, external account, product decision, budget, permission), the requesting orchestrator labels the issue `moot:needs-human` and posts `NEEDS-HUMAN #<issue>` with a structured body: what is needed, why the agent cannot do it, what happens once provided. `claim` and `dispatch` are illegal on that issue until a listed human posts `PROVIDED` or `RESOLVED` after the request. Surfaced in `moot status` (top-level `needs_human`), the PM `STATUS` comment, the `moot chat` inbox, and an optional Slack DM that repeats after `notify.repeat_after_minutes`. Secrets never travel through GitHub: `PROVIDED` names where the value was placed (an env var or 1Password item) and the runner injects it.

### Hallucination protection

No group grades its own claims; a claim without evidence is not a fact.

- **Evidence.** `verified-by:` is mandatory on `PLAN`, `DECISION`, `ANSWER`, `EVIDENCE`, and worker finish comments. Prompts forbid asserting test results without pasted output.
- **Challenge.** Any group or human posts `CHALLENGE <ref>` (a comment, plan item, issue, or PR) stating the doubted claim. The target gains `moot:challenged`; `dispatch` and `merge` are blocked on it. The author posts `EVIDENCE` or `RETRACT`; a group other than author and challenger posts `UPHELD` or `DISMISSED`. With two groups, the human adjudicates. Resolution clears the label.
- **Independent audit.** Every merged PR is audited by a group that did not author it. Audits are batched: every 5 merges or 4 hours, whichever comes first, and at milestone end, each group dispatches one `auditor` run over the pending merged PRs it did not author. The auditor checks acceptance criteria against the code at the merge commit, re-runs tests, and posts `AUDIT` per PR. `fail` findings trigger `reopen`.
- **Drift check.** On every restart, each orchestrator compares the board to the approved plan and the decision log and posts `DRIFT` when scope, approach, or assumptions moved without a `DECISION`.
- **Human overrides.** `HALT`, `RESUME`, `REVERT`, and `CHALLENGE` are all available to the human from `moot chat`.

## 15. Shared memory

Canonical memory is versioned files in the product repo.

- `.moot/decisions/NNNN-<slug>.md`: ADR-shaped, one per `DECISION`: context, decision, alternatives, evidence, agreeing groups, date, coordination comment link. `moot decide <id>` renders it from the `[joint] DECISION` comment and opens a PR.
- `.moot/memory/lessons.md`: appended by `moot remember` when a blocked, challenged, or reverted item resolves: what went wrong, what fixed it, what to check next time.
- **CI requirement.** Branch protection requires the product's CI check, so that check must report on every PR including docs-only ones; `moot setup` warns when the workflow uses `paths` filters that would leave decision or lesson PRs unmergeable.
- **Auto-approval path.** Because the text was already jointly agreed, other groups' PMs approve a decision or lesson PR automatically when `rules.py` confirms the file content hash matches the agreed comment. These PRs still satisfy the normal quorum; they just never wait on a reviewer worker or a human.
- `moot recall <query>` searches decisions, lessons, and all past coordination issues in the repo, returning matches with links. Orchestrators run it at start and before planning. Dispatch renders relevant hits into worker briefs.
- The coordination issue is per milestone; cross-milestone continuity comes only from these files.

## 16. Adapters (N groups)

An adapter implements: `render_prompts(role, brief) -> files`, `launch(role, model, effort, cwd, timeout, env) -> process`, `valid_models() -> from models.toml`, `installed_skills() -> roster entries`, `command_timeout_ceiling()`. Claude and Codex ship in v1. Adding a system is one adapter module, one `models.toml` section, one bot account, and one `[groups.<name>]` block. Nothing in `rules.py`, `grammar.py`, or the prompts refers to a specific CLI.

Both v1 adapters run roles as single headless invocations (`claude -p`, `codex exec`) with the model and effort passed in the form each CLI accepts, output as JSON, permissions scoped to the worktree, and no interactive approval prompts.

## 17. Error handling and budgets

| Failure | Detected by | Response |
|---|---|---|
| Worker exits without result | PM via run record | `blocked`, log tail posted, orchestrator re-tags and re-dispatches or splits |
| Worker timeout | PM via run record | Kill, branch kept, same as above |
| Review ping-pong cap | rules | `blocked`; orchestrators must post a resolution |
| Illegal verb | rules | Refused; `PROTOCOL-VIOLATION` posted |
| CI red | PM via checks | Treated as request-changes; author group re-dispatches |
| Orchestrator or PM dies | runner | Restart, rebuild from `moot status`, post `RESUMED` |
| Other group silent past `silence_grace_minutes` | orchestrator | `PING`, then continue `SOLO` with a note |
| GitHub rate limit | `moot wait` | Backoff, no action |
| Off-protocol GitHub change (label or assignee changed outside `moot`) | `moot status` diff | `PROTOCOL-VIOLATION` naming the actor; no automatic revert |

Budgets are per milestone in worker runs by model tier plus a wall-clock cap. `claim` refuses when the issue's model tier is exhausted for the caller; the orchestrator re-tags to a cheaper tier or posts `BUDGET`. Spend to date appears in `moot status` and the PM `STATUS` comment.

## 18. Security

- Bot tokens are fine-grained PATs scoped to the product repo (contents, issues, pull requests), injected from 1Password via `op run` into the orchestrator and PM processes only. Workers receive no token.
- Only configured bots and allow-listed humans can speak in the grammar. All other comments are ignored.
- `moot setup` refuses public repos without `--allow-public` and prints the injection risk when overridden.
- Secrets never appear in issues, PRs, briefs, or `.moot/` files. `PROVIDED` comments name a location, not a value.
- Worker filesystem access is scoped to the worktree by each adapter's sandbox settings.
- Everything under `$MOOT_HOME` is disposable state.

## 19. Testing

- **Rules** (`test_rules.py`): every state, verb, party, and N in {2, 3}, from recorded board fixtures. This is the highest-coverage module.
- **Grammar** (`test_grammar.py`): every header verb, required fields, round counters, amend cap, majority and tie handling, malformed and unauthorized comments.
- **Review** (`test_review.py`): quorum math including human approvals, last-committer rule after push-fix, ping-pong cap, challenge blocking.
- **Dispatch** (`test_dispatch.py`): a fake adapter binary records arguments and writes fake results; tests worktree creation, brief rendering, timeout kill, post-run PR opening, reap.
- **Memory** (`test_memory.py`): decision rendering, hash match for auto-approval, recall ranking.
- **Smoke** (`tests/smoke/`, opt-in): one tiny real task per adapter against a scratch repo.

No test claims to measure decision quality. That is done by watching the first milestones and converting each bad decision into a prompt edit or a new invariant.

## 20. Bootstrap

1. Human: create one GitHub machine account per group, add each as a collaborator on the product repo, mint one fine-grained token each, store in 1Password.
2. `git clone` the framework; `uv sync`; `task moot:doctor` verifies both CLIs installed and logged in, tokens resolve, `gh` is on the human's own account, and command-timeout ceilings exceed `wait_timeout_seconds`.
3. `task moot:setup REPO=owner/name GOAL="..."` creates labels, milestone, coordination issue, CODEOWNERS entry, branch protection, and a starter `.moot.toml` with adapter-populated model fields, committed via a PR for the human to approve.
4. `task moot:run GROUP=claude` and `task moot:run GROUP=codex`, in terminals or as launchd agents. Each posts `ROSTER` and `CONFIG`.
5. `moot chat`. Approve the plan by label when it appears.

## 21. Future work (explicitly out of v1)

### Borrowed from prior art (approved 2026-09-03)

A survey of existing multi-agent coding systems (Symphony and its forks, Gas Town/Beads, Code Conductor, claude-code-review-council, ai-pair, Claude Code Review) found no project combining Moot's mechanisms, but five of their ideas are adopted for the later plans:

1. **Lease-plus-heartbeat claims** (from Beads). A claim carries a TTL that the PM's loop renews while the worker runs; an expired claim auto-reverts the issue to `ready` and posts a note. Replaces bare first-assignment-wins as the crash-recovery path. Lands in plan 2 with dispatch and collect.
2. **Citation verification before a finding is shown** (from claude-code-review-council). The reviewer worker's contract requires every cited file and line to be opened and checked against the checkout before it appears in the review; uncheckable citations are dropped and noted. Lands in plan 2's reviewer prompt.
3. **Role-matched review** (from ai-pair). When more than one group reviews a PR, the review request assigns each a different primary concern (correctness and security, tests and conventions, architecture) so reviewers surface distinct issues. Lands in plan 3 with `review`.
4. **Neutral check runs** (from Claude Code Review). Cross-group review results are published as a GitHub check run with a neutral conclusion, visible but never blocking on its own; merge remains gated by the quorum rule in `rules.py` and the human. Lands in plan 3 with `review` and `merge`.
5. **Repo-local verification bar** (from Claude Code Review's `REVIEW.md`). A product may add `.moot/REVIEW.md` stating what counts as evidence for that repo; it is rendered into reviewer and auditor briefs. Lands in plan 2 with brief rendering.


- Webhooks when running on an always-on box.
- Per-group machines and a shared `$MOOT_HOME` alternative.
- A third adapter to prove the N-group claims end to end.
- A read-only GitHub token for workers that need to read issues beyond their brief.

### Carried into plan 3 from the plan-1 final review

1. **A `[joint]` comment is only as trustworthy as its author.** `is_authorized` accepts a `[joint]` header from *any* configured bot, so a hand-written `[joint]` comment parses as `ok` and reads as the close of an agreement round. Plan 3's `decide`, `plan`, and auto-approval paths must require that the joint comment's author is the proposer's bot and that the agreement had reached `agreed`, `majority`, or `disagree` at the time it was written. An `Agreement.joint_verified` field is the suggested seam.
2. **Challenge closers are unrestricted.** `derive_open_challenges` lets any authorized party close a challenge with `UPHELD`, `DISMISSED`, or `RETRACT`. Plan 3's challenge verb must restrict who may close one per section 14: the challenger may `RETRACT`, and a party other than the challenged one decides `UPHELD` or `DISMISSED`.
3. **`moot doctor` checklist.** It must verify that each group's token identity equals the bot login configured for that group; that a git identity is configured per bot for worktree commits; that hidden `.pth` files are detected (see the README); and that every model named in `.moot.toml` exists in `models.toml` for that group's adapter.
4. **Git identity per bot (plan 2).** Plan 2 must set each bot's git `user.name` and `user.email` in its worktrees so a PR's head committer resolves to that bot's account. Without it the head committer is unknown and `approve` and `merge` are refused (section 8).

## 22. Glossary

- **Group**: one AI system (CLI + bot account + config) with an orchestrator, PM, and workers.
- **Party**: who is speaking in the grammar: a group, a group's PM, `human`, or `joint`.
- **Quorum**: the number of distinct non-author groups whose approval a PR needs; `min(review.quorum, N-1)`.
- **Board**: the typed snapshot of the milestone's issues, PRs, coordination comments, and run records that `rules.py` evaluates.
- **Brief**: the rendered `.moot-brief.md` a worker reads first.
