# Meld: multi-AI coordination framework — design

Date: 2026-09-03
Status: draft for review
Name: **Meld** (to merge distinct parts into one whole; the working name until 2026-09-03 was "moot"). Package and command are both `meld`.

## 1. Purpose

Meld lets N independent AI coding systems (day one: Claude Code and OpenAI Codex CLI) work on one GitHub-hosted product together, with a human able to steer, contribute, and intervene. Each AI system is a **group** with an orchestrator, a project manager (PM), and many short-lived worker agents. Groups negotiate plans, split work by free claim, review and modify each other's code, and challenge each other's claims. The bet is that independent models checking each other produce a better product than either alone.

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
| Framework location | Its own repo (`~/Documents/meld`), reusable across products |
| Orchestrator channel | GitHub coordination issue, one per milestone |
| Identity | One GitHub machine account per group; assignee is the claim lock |
| Topology | Orchestrator and PM are long-lived headless processes per group; each worker is a fresh headless run in its own worktree |
| Cross-modify | Reviewers may push to the PR branch; last committer's group cannot approve |
| Work split | Free claim, first assignment wins |
| Hosting | This Mac first, portable by design (single entry point, env-var secrets) |
| Language | Python 3.12, `uv`-managed, `gh` under the hood |
| Framework shape | LLM decides, Python enforces: `meld status` returns legal actions, verbs refuse anything else |
| Groups | N, configured in `.meld.toml`; two shipped adapters |
| Name | Meld |

## 4. Architecture

Three layers in one Python package.

1. **`meld` CLI.** The only way any agent touches GitHub. Verbs today: `status`, `wait`, `claim`, `release`, `post`, `models`, `dispatch`, `tick`, `collect`, `review`, `merge`, `audit`, `reopen`, `reap`, `tag`, `challenge`, `roster`, `run`, `decide`, `remember`, `recall`, `plan`, `inbox`, `chat`, `notify`; `setup` and `doctor` are plan 5 (section 21). Every mutating verb recomputes the legal-action set for the calling party and refuses anything outside it, naming the violated rule. The calling party is `MELD_GROUP` (a group name) plus `MELD_ROLE` (`orchestrator` or `pm`), or `human` when neither is set and `gh` is on a listed human account. State lives under `MELD_HOME`, default `~/.meld`.
2. **Role prompts.** Markdown, one per role, rendered identically for every adapter: `orchestrator.md`, `pm.md`, `workers/<speciality>.md`. Shared preamble carries the cardinal rules, the `meld status` first habit, the header grammar, and a pointer to the product repo's own agent instructions, which win over framework defaults.
3. **Runners and adapters.** `meld run <group>` starts a group's orchestrator and PM processes with the right prompts and environment, restarting them on a schedule. `meld chat` (the human console, section 14) landed in plan 4; `meld setup` (prepares a product repo) and `meld doctor` (checks the machine) are still plan-5 work (section 21). Each supported CLI is an adapter module that knows how to render prompts, launch a headless run with a model and effort, and which models and efforts are valid.

### Repo layout

```
meld/
  pyproject.toml            # uv, Python 3.12+, console_script "meld"
  src/meld/
    cli.py                  # argument parsing, dispatches to verbs
    board.py                # typed board model and derivations (leases, challenges, audits)
    assemble.py             # builds a Board from GitHub JSON; the only reader of GitHub in the core
    github.py               # gh wrapper: conditional requests, rate-limit detection, endpoint helpers
    grammar.py              # coordination header parser and agreement rounds
    rules.py                # legal-action computation; every invariant lives here
    actions.py posting.py tagging.py challenge.py   # claim/release, post, tag, challenge
    dispatch.py             # worktree + brief + headless run + run records; tick, collect, reap
    review.py audit.py      # meld review / merge; meld audit / reopen
    results.py              # verdict files under $MELD_HOME/results
    status.py waiting.py state.py context.py config.py models.py gitops.py brief.py roster.py runners.py
    memory.py chat.py       # decisions, lessons, recall; the human console (inbox + REPL)
    plan.py notifier.py housekeeping.py   # the plan gate; Slack nudges; CI-red/ignored-comment/off-protocol sweeps run from tick
    adapters/
      base.py               # Adapter protocol, environment scrubbing, skill scanning
      claude.py
      codex.py
  src/meld/data/models.toml   # per adapter: models, efforts, context class, tier
  src/meld/prompts/           # packaged with the module, not a top-level directory
    _preamble.md  _worker_contract.md  orchestrator.md  pm.md
    workers/  backend.md frontend.md security.md qa.md reviewer.md auditor.md docs.md
  tests/
    fixtures/board1/        # recorded, redacted gh JSON
    test_rules_issues.py test_rules_prs.py test_rules_challenges.py test_grammar.py test_dispatch.py
    test_review.py test_merge.py test_audit.py test_challenge.py test_results.py test_memory.py test_plan.py test_chat.py test_notifier.py test_housekeeping.py ...
    smoke/                  # opt-in, real CLIs, scratch repo
  Taskfile.yml              # test, test:smoke, status, run (setup/doctor tasks follow those still-plan-5 verbs)
  README.md
```

The product repo holds only `.meld.toml`, `.meld/workers/` (product-specific worker prompts), `.meld/decisions/`, `.meld/memory/`, and a CODEOWNERS entry for `.meld/workers/`.

## 5. Configuration

### `.meld.toml` (product repo)

```toml
[repo]
name = "owner/name"
default_branch = "main"
private = true                     # setup refuses to proceed on a public repo without --allow-public

[humans]
accounts = ["empowerScott"]         # only these GitHub users may speak as [human]

[groups.claude]
cli = "claude"
bot = "meld-claude-bot"
token_env = "MELD_TOKEN_CLAUDE"
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
bot = "meld-codex-bot"
token_env = "MELD_TOKEN_CODEX"
concurrency = 8
review_concurrency = 4
[groups.codex.orchestrator]
model = "<populated from `meld models` at setup>"
effort = "high"
[groups.codex.pm]
model = "<populated from `meld models` at setup>"
effort = "medium"

[review]
quorum = 2                         # distinct non-last-committer groups; effective value is min(quorum, N-1)
pingpong_cap = 3
security_paths = ["auth/**", "**/secrets*", "infra/**"]

[audit]
batch_merges = 5                   # dispatch a batch once this many of a group's owed audits are pending
batch_hours = 4                    # ...or once the oldest pending one has waited this long, whichever comes first

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
slack_dm_webhook_env = "MELD_SLACK_DM_WEBHOOK"   # optional
repeat_after_minutes = 120
```

Model identifiers shown for Claude are examples. Values written as `<populated ...>` are filled by `meld setup` from live adapter output, not hand-typed; `meld doctor` re-verifies every configured model against `models.toml`.

### `models.toml` (framework)

Per adapter, per model: `context_class` (`small` | `medium` | `large`), `efforts` (ordered list), `tier` (`small` | `medium` | `large`, used for budgets), and `default_for` (`orchestrator` | `pm` | `worker`, optional). Model identifiers are populated at implementation time from each CLI's live model list and re-verified by `meld doctor`; this spec does not pin them because they change faster than the framework.

`size` to `context_class` compatibility: `S` runs on any class, `M` requires `medium` or `large`, `L` requires `large`.

## 6. GitHub data model

- **Milestone.** One per approved unit of work. Description holds the approved plan. Exactly one coordination issue.
- **Coordination issue.** Label `meld:coordination`, title `Coordination: <milestone>`. All cross-group and human-to-group conversation happens here. Every comment begins with a header line (section 7).
- **Work issue.** One per task, in the milestone. Labels:

| Label | Meaning |
|---|---|
| `meld:planned` | Created by the plan, not yet approved |
| `meld:ready` | Approved, unclaimed |
| `meld:in-progress` | Claimed, worker running or pending |
| `meld:in-review` | PR open, awaiting review quorum |
| `meld:blocked` | Needs orchestrator resolution |
| `meld:needs-human` | Needs a human before retry |
| `meld:challenged` | A CHALLENGE is open against it |
| `worker:<speciality>` | Role that owns it |
| `model:<id>` `effort:<level>` `size:S|M|L` | Set by the orchestrator, read by dispatch |
| `area:<x>` | Optional, product-defined |

- **Assignee is the claim lock.** `meld claim` reads the issue, refuses if any assignee exists, assigns the caller's bot, re-reads, and releases if it lost a race. A human assigning themself is an equally valid claim.
- **Pull request.** Branch `meld/<issue>-<slug>`. Body contains `Closes #<issue>` and a `verified-by:` line. Branch protection: require 1 approval (GitHub backstop; the real quorum is enforced by `meld merge`), dismiss stale approvals on push, require the product's CI check, restrict pushes to the bots and listed humans. Squash merge only.
- **Decision and memory files.** `.meld/decisions/NNNN-<slug>.md`, `.meld/memory/lessons.md` in the product repo (section 15).
- **Worker comments.** The framework posts exactly two comments per worker run on its issue: start (worktree, model, effort, size) and finish (commit range, outcome, `verified-by:`). Workers themselves post nothing.

## 7. Coordination grammar

Every coordination comment starts with one header line:

```
[<party>] <VERB> [<ref>] [<qualifier>]
```

- `<party>` is a group name, `<group>:pm`, `human`, or `joint`. `joint` may only be written by `meld` after quorum is verified.
- Comments from accounts that are not a configured bot or an allow-listed human are ignored by the parser and flagged once, in their own `IGNORED-COMMENT` comment posted by `meld tick`. This is the prompt-injection defense for the channel.
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
| `CHALLENGE <ref>`, `EVIDENCE <ref>`, `RETRACT <ref>`, `UPHELD <ref>`, `DISMISSED <ref>` | group or human | Hallucination protection (section 14); posted only through `meld challenge`, which applies or clears `meld:challenged`; `meld post` refuses them |
| `AUDIT <ref>` | group or group:pm | Independent audit findings |
| `DRIFT` | group | Plan drift |
| `DECISION <id>` | joint | Agreed decision, mirrored to a file |
| `PROPOSE-WORKER <name>` | group or group:pm | New worker type proposal with draft prompt |
| `WORKER <name>` | joint | Agreed new worker type, closing a `PROPOSE-WORKER` round |
| `PING`, `SOLO`, `RESUMED`, `PROTOCOL-VIOLATION` | group or group:pm | Liveness notices, and enforcement: `PROTOCOL-VIOLATION` is posted either by the party whose illegal `meld` call was refused (naming the broken rule, section 8), or by a PM's `meld tick` naming a label or assignee change on a milestone issue made by an actor that is neither a configured bot nor a listed human (section 17) |
| `LEASE #<issue>`, `EXPIRED #<issue>` | group:pm, framework-posted | A build's claim lease and its expiry (section 11), written only by `meld dispatch` and `meld tick`; `meld post` refuses both for every caller |
| `IGNORED-COMMENT` | group:pm, framework-posted | Flags comments from accounts outside the configured bots and humans, counted as `coordination.ignored_comments` in `meld status`; posted by `meld tick` — only the alphabetically first configured group's PM, so a stranger's comment is flagged once rather than once per group; framework-posted, `meld post` refuses it |

`RETRACT` may come from the challenger (withdrawing the doubt) or the challenged party (conceding the claim); `UPHELD` and `DISMISSED` are for a party that is neither.

Required fields by verb are enforced by the parser: `PLAN`, `DECISION`, `ANSWER`, `EVIDENCE`, and every worker finish comment must carry a `verified-by:` line naming a command run, test executed, or URL read. Missing it makes the comment invalid.

**Ref conventions.** `ask-<n>` and `dir-<n>` are assigned by `meld chat` itself — the next unused number for that prefix among comments already on the board — for `[human] ASK` and `DIRECTIVE`. `plan-<milestone>`, `plan-add-<n>`, and a decision id (conventionally `dec-<n>`) are not generated by any code: the proposing orchestrator picks the string when it opens the `PROPOSE`/`DRAFT` round, and that same ref is carried through `AGREE`/`AMEND`/`DISSENT` to the `[joint]` comment and on to `meld plan issues`/`meld decide`.

### Agreement protocol

Used for `PLAN`, `PLAN-ADD`, `DECISION`, `ANSWER`, and `PROPOSE-WORKER`. Each round closes with the
matching joint verb; a `PROPOSE-WORKER <name>` round closes with `WORKER <name>`, not with a joint
`PROPOSE-WORKER` (there is no such joint verb).

1. One group posts `PROPOSE <id>` (or `DRAFT <id>` for chat) with full text.
2. Every other group posts `AGREE <id>` or `AMEND <id>` with edited full text. An amend restarts the round with the amended text as the proposal.
3. When every group has agreed to the same text, the proposing group runs `meld post --joint <VERB> <id>`, which verifies the agreement on GitHub and writes the `[joint]` comment. Nothing else may write `[joint]`.
4. A `DISSENT` counts as a cast vote rather than leaving the round open, so the outcome is decided as soon as every group has either agreed or dissented — not when a round cap expires. With a strict majority, the majority text is posted as `[joint]` with a `DISSENT` section listing minority positions; with no majority (including any 2-group tie) the item is posted as `[joint] <VERB> <id> DISAGREE` containing all positions, and the human decides.
5. `agreement.amend_cap` bounds only the number of `AMEND` and re-proposal rounds. Once it is reached, a group must post `AGREE` or `DISSENT` instead of amending again; it does not by itself decide the outcome. A group that dissented is not locked in: a later `AGREE` from that group supersedes its dissent, as an `AMEND` by any group restarts the round.
6. The `[joint]` comment carries the agreed text verbatim. A caller may add its own note, which is appended under an `## Addendum` heading; it never replaces the agreed text. A ref that already has a *verified* `[joint]` comment cannot get a second one; an unverified one (written by anyone but the proposer's bot, or before the round closed) does not block the proposer's, which then supersedes it — the latest `[joint]` comment on a ref is the one that counts.
7. **Any `[joint]` comment freezes the round.** Once a `[joint]` comment exists on a ref — verified or not — later `PROPOSE`, `DRAFT`, `AMEND`, `AGREE`, and `DISSENT` comments on that ref are ignored by the agreement computation and refused by `meld post` ("`<ref>` is closed by a `[joint]` comment; open a new ref"). Without this, an `AMEND` posted after a verified `[joint] DECISION` rewrote the round's text and made the amender its proposer under a joint comment that still read as verified — and `meld decide` recorded it. A round that genuinely needs reopening gets a new ref.
8. **A `DISAGREE` round is never recorded.** `[joint] <VERB> <id> DISAGREE` lists the positions for the human; `meld decide` refuses it and the memory-PR verifier requests changes on a decision PR for it ("`<ref>` ended in DISAGREE; the human decides").

## 8. Issue lifecycle and legal actions

```
planned ──human APPROVE──▶ ready ──claim──▶ in-progress ──PR opened──▶ in-review ──quorum + CI──▶ merged
                             ▲                 │                          │
                             └────release──────┘                          └─changes requested─▶ in-progress
                                               │
                                               └─worker fail / timeout / pingpong cap─▶ blocked ─▶ ready | needs-human | closed
merged ──AUDIT reopen──▶ ready
```

Two milestone-level states sit above this: `planning` until `meld:plan-approved` is on the coordination issue, then `executing`. `HALT` overlays either and freezes `claim` and `dispatch` for every group until `RESUME`.

`rules.py` computes, for a board snapshot and a calling party, the set of legal verbs per issue and per PR. Every rule below is a test.

- **claim**: caller is a PM or a human; milestone is `executing`; issue is `ready`; no assignee; caller's roster contains the issue's `worker:` label; caller's count of `in-progress` issues below `concurrency`; caller's budget tier for the issue's model not exhausted; no `HALT`.
- **dispatch**: caller is a PM; caller's bot is assignee; no live run record for the issue; `model`, `effort`, `size` labels present and valid for the caller's adapter; `size` compatible with the model's context class; issue not `needs-human` or `challenged`; no `HALT`.
- **release**: caller is assignee's group or human; issue in `in-progress` or `blocked`. `--force` is human-only and posts a note.
- **review approve**: caller's group did not author the last commit on the PR head; the caller is not the PR's author party; PR not `challenged`; the head committer is known. The author-party clause is GitHub's rule, not ours: GitHub returns 422 when the account that opened a PR submits an `APPROVE` or `REQUEST_CHANGES` review on it, and Meld's PR author is the author group's bot. So after another group push-fixes the head (section 13 step 3) the author group's approve or request-changes is refused as a GitHub verb, and `meld review` posts it as a `COMMENT` review whose first line names the verdict, with the `meld/review/<group>` commit status on that head as the record (`rules.review_verdict_rule` is the legality `meld review` applies: PR open, the author is not the head committer, the head committer is known, and no open challenge for an approve). Request-changes is legal for any party that is neither the head committer's nor the author's; push-fix is legal for any non-author-of-head group, including when the head committer is unknown, because it approves nothing. Push-fix by group X makes X the last committer. A last commit that maps to no GitHub account leaves the head committer unknown and refuses approve, rather than falling back to the PR author.
- **merge**: approvals on the current head from at least `min(review.quorum, N-1)` distinct groups, none of which authored the head, with human approvals counting as a group; the author group counts when it is not the head committer and its `meld/review/<group>` status on the current head is `success`, since its approval can never be a GitHub review (see review approve); required checks green; no open `CHALLENGE` on the PR; the head committer is known, since the non-committer rule cannot be evaluated without it; no `HALT`. Either group's PM may call it.
- **reopen**: an `AUDIT` comment with a `fail` finding naming the issue exists and was posted by the caller's group; the issue is closed; the caller's group did not author the PR that closed it. Returns the issue to `ready` with the findings attached.
- **collect**: caller's bot is assignee (or requested the review or audit); a run record exists whose process has exited and has not been collected.
- **post**: always legal for any party, except `--joint`, which requires verified agreement. A `PROPOSE` or `DRAFT` on a ref that already has proposal text is refused; a re-proposal after that point is an `AMEND` and counts a round. Refusals here come in two kinds. A **rule-level** refusal — no agreement yet, caller is not the proposer, wrong milestone phase, a ref that already has a verified `[joint]` comment, an agreement verb on a ref that a `[joint]` comment has closed (section 7), a re-proposal on an active ref, the amend cap reached — posts `PROTOCOL-VIOLATION` like any other illegal call. A **usage error** — a verb the party may not post, a human writing `[joint]`, `--joint` without a ref, a missing ref or `verified-by:` line, `DISAGREE` without the qualifier — is the caller's own mistake, reaches no other party, and posts nothing.
- **plan**: `plan issues` is legal for the proposer group's orchestrator given a verified `[joint] PLAN` (refused once the milestone has left `planning`, since that plan's issues already exist) or `[joint] PLAN-ADD` (legal in any phase). It parses the referenced text as the item format (plan-4 decision 4: `- id: <slug> | title: <text> | worker: <w> | model: <m> | effort: <e> | size: <S|M|L>` plus indented `  - <criterion>` lines), skipping and reporting a malformed line and skipping (not recreating) an id that already backs a `meld:planned` issue; `--dry-run` parses and reports `would_create` without creating anything. `plan approve` is legal for a human only, while the milestone is still `planning`, given a verified `[joint] PLAN` on `plan-<milestone>`; it adds `meld:plan-approved` to the coordination issue and flips every `meld:planned` issue to `meld:ready`. `plan ack` is legal for a human only, given a verified `[joint] PLAN-ADD` on the ref; it posts `[human] ACK <ref>` — skipped, idempotently, if already posted — and flips that addition's own planned issues to `meld:ready`.
- **decide**: legal for the agreement's proposer (that group's orchestrator or PM) given a verified `[joint] DECISION` on the ref whose round ended `agreed` or `majority` (a `DISAGREE` round is refused: the human decides), unless the ref is already recorded — an open memory PR for it, or its file already present on origin's default branch. Writes `.meld/decisions/NNNN-<slug>.md` from the agreed text, linking the verified `[joint]` comment itself as `- Source:` (never an earlier hand-written one), opens a memory PR carrying a `meld-memory: decision <ref> <sha256>` body marker, and posts `REVIEW-REQUEST` naming it a memory PR.
- **remember**: legal for any group's orchestrator or PM once `ref` (an issue or PR number already on the board) is neither `blocked` nor `needs_human`, and carries no open challenge — there is no agreement round to verify, since a lesson records what already happened rather than proposing anything. Appends an entry to `.meld/memory/lessons.md` through the same memory-PR machinery, marker `meld-memory: lesson <ref> <sha256>`.

A `[joint]` comment is *verified* only when it was written by the proposer's own bot after the round reached `agreed`, `majority`, or `disagree` (`Agreement.joint_verified`, alongside `joint_verb`, `joint_author`, and `joint_comment_id`; `grammar.verified_joint` returns that comment, and it is the one `decide` links and the verifier re-renders from); `meld status` shows both that flag and the round `status` per ref under `coordination.agreements`. `plan`, `decide`, and `remember` refuse through the same `Refusal`/`PROTOCOL-VIOLATION` path as every verb above, but — unlike them — their legality predicates are not yet folded into `rules.py`'s per-issue and per-PR tables, so `meld status`'s `legal` arrays never list `plan`, `decide`, or `remember` (carried into plan 5, section 21).
- **review** (dispatch a review run): caller is a PM; PR open; caller's group is not the head committer's; PR not challenged; no `HALT`; within `review_concurrency`; no live or uncollected run on the PR.
- **audit** (dispatch an audit run): caller is a PM; PR merged and not a memory PR (its content was verified against the board at review, and `audit.pending` omits it too); caller's group did not author it; caller's group has not audited it; no `HALT`; within `review_concurrency`.
- **reap**: caller is the PM whose collected, dead run it is; a build once its issue is closed or its PR merged or closed; a review or audit run once collected.
- **tag**: caller is the orchestrator or a human; issue open.
- **challenge**: acted on by an orchestrator or a human only (PMs may not open, answer, or decide challenges). Open by any orchestrator or human but the claim's own party; `EVIDENCE` by the challenged party's orchestrator (or a human); `RETRACT` by the challenger or the challenged party's orchestrator (or a human); `UPHELD`/`DISMISSED` by an orchestrator of a group that is neither (with two groups, the human).

`meld status` lists `review`, `audit`, `reap`, and `tag` under each issue's or PR's `legal` array; `challenge` legality is not surfaced there — call `meld challenge` and read its refusal (or the lack of one), or watch `coordination.challenges` for what is currently open.

Illegal calls return a refusal naming the rule and post `PROTOCOL-VIOLATION` on the coordination issue so other groups and the human see it. Two cases are refusals without a violation: a **lost claim race**, which is first-assignment-wins working as designed rather than an illegal call, and any refusal of a human, who answers to nobody on the board. The refusal JSON therefore reports `violation_posted` as `true`, `false` (posting it was attempted and failed), or `null` (none was attempted).

## 9. Roles

All roles are headless runs. Per group, two are long-lived and one is short-lived.

**Orchestrator** (long-lived, restarted every `runners.restart_hours`). Loop: `meld wait` filtered to coordination comments, chat, blocked and needs-human issues, roster changes, and drift; act; repeat. Responsibilities: decompose the goal into issues; tag each with `worker`, `model`, `effort`, `size` via `meld tag` using the rubric; negotiate with other orchestrators; answer human chat via the agreement protocol; resolve blocked issues; re-tag on retries; propose worker types when the roster has a gap; post `ROSTER`, `CONFIG`, `DRIFT`. It never writes product code and never dispatches.

**PM** (long-lived, same restart schedule, sibling process). Loop: `meld tick` first each cycle (timeouts, leases); `meld wait`; act; repeat. Responsibilities in `executing` only: claim `ready` issues up to the cap; dispatch with the tagged model and effort; `collect` finished workers; reap merged ones; move labels; post `REVIEW-REQUEST`; dispatch reviewers for other groups' requests; dispatch auditors after merged batches; call `merge` when legal; post `STATUS` on cadence, leading with needs-human and budget; auto-approve decision PRs whose file is byte-identical to `meld decide`'s rendering of the verified `[joint] DECISION`, and lesson PRs that append exactly one entry about a resolved item (a lesson has no agreed comment; section 15). It never chooses models. If it disagrees with a tag it posts to the coordination issue and the orchestrator re-tags.

**Workers** (fresh run per task). Day-one roster: `backend`, `frontend`, `security`, `qa`, `reviewer`, `auditor`, `docs`. A worker prompt is a role brief plus the fixed contract: read `.meld-brief.md` first; work only in this worktree; commit small; run the product's tests; write `.meld-result.json` (outcome, summary, `verified-by`, for reviewers a verdict) and exit. Workers have no GitHub credentials and never push, open PRs, comment, label, or merge. The framework does all of that from the result file.

Orchestrator and PM keep no state that is not also on GitHub. Restarting them is always safe, and it never touches running workers, which are independent processes tracked by run records.

### Model and effort selection rubric

The orchestrator prompt carries the rubric as a table with three worked examples. Inputs: task type, blast radius (files and systems touched), ambiguity, first attempt or retry. Outputs: `model`, `effort`, `size`. Rules the rubric states plainly: read `meld models` before tagging; there is no `XL`, split instead; on a retry after failure or changes-requested, step effort up one level or move to a stronger model before trying the same approach again; prefer the cheapest tier whose context class fits the size. The rubric is a prompt and tunable without code. Validity and budget are code.

## 10. Roster

`meld roster` returns worker types available to the calling group, with a one-line description each, from three sources: framework built-ins, the product repo's `.meld/workers/` (a same-named file overrides a built-in), and the skills and plugins installed for the group's CLI on the machine (adapter-provided).

- At start, each orchestrator posts `ROSTER` and `CONFIG` in its opening comment.
- On every restart and every `roster_refresh_minutes`, it re-runs `meld roster`, diffs against its last posted inventory, and posts `ROSTER-CHANGE` on any difference.
- `claim` checks the issue's `worker:` label against the caller's roster.
- The PM reads `meld roster` before each dispatch, never from cache. When a new type better matches a `blocked` or `ready` issue, it suggests re-tagging.
- Gap detection during decomposition triggers `PROPOSE-WORKER`, which follows the agreement protocol, then a `docs` worker is dispatched to write `.meld/workers/<name>.md` and `collect` opens the PR. That PR requires a human approval via CODEOWNERS on `.meld/workers/` in addition to the review quorum. Proposals made during `planning` ride with plan approval instead.

## 11. Dispatch and worker runtime

`meld dispatch <n> [--kind review|audit]` (`<n>` is a PR number for reviewer and auditor runs):

1. Re-check legality (section 8).
2. Create a worktree from the default branch at `$MELD_HOME/worktrees/<group>/<issue>-<slug>` on branch `meld/<issue>-<slug>`; a retry whose worktree is still on disk reuses it as-is, otherwise a pushed remote branch is checked out instead of branching fresh from the default branch. A worktree registration whose directory was deleted ($MELD_HOME is disposable) is pruned and recreated. Reviewer and auditor runs check out the PR head instead.
3. Render `.meld-brief.md`: role prompt, the fixed contract, issue body, the plan excerpt for this issue, file hints from the orchestrator, relevant decisions from `meld recall` (section 15), and the product's agent-instruction file path. Not the whole plan, not the coordination thread.
4. Spawn the headless run via the adapter — a build uses the issue's tagged model and effort, a reviewer or auditor run uses the group's orchestrator model and effort — non-interactive, cwd set to the worktree, wall-clock timeout from `size`. The environment is scrubbed of tokens and party identity and carries the group's bot as git author and committer (`GIT_AUTHOR_*`/`GIT_COMMITTER_*`; never `git config` in the worktree, which would write the clone's shared `.git/config`), `GH_CONFIG_DIR` pointed at an empty per-run directory, git credential helpers reset, and `GIT_TERMINAL_PROMPT=0`. Filesystem scope is whatever the adapter provides: the Claude adapter runs `--permission-mode bypassPermissions`, so its isolation is exactly this environment; the Codex adapter runs `-s workspace-write` (section 18).
5. Write the run record `$MELD_HOME/runs/<group>/<issue>.json` (PID, start, worktree, log path, model, effort) and post the start comment.
6. Return immediately.

**Leases**: dispatching a build also posts a `[<group>:pm] LEASE #<issue>` comment on the coordination issue, whose `until` is the worker's deadline plus `lease.grace_minutes`. No per-cycle renewal is needed: `meld tick` fills in a lease that is missing for one of the calling group's own live runs, and releases any other group's lease whose `until` has passed, posting `[<group>:pm] EXPIRED #<issue>` and returning that issue to `ready`. A lease counts only while the leasing group's bot is assigned to the issue: `meld release` posts nothing, so once a group lets go and another group claims the issue, the old `LEASE` comment asserts nothing about the new claim. `LEASE` and `EXPIRED` are written only by `meld dispatch` and `meld tick`; `meld post` refuses them for every caller, so a lease cannot be forged.

**Post-run**: `meld status` reports a run as `finished` once its process has exited. The PM then calls `meld collect <issue>`, which does all side effects: read `.meld-result.json`; if commits exist, push the branch and open (or update) the PR with `Closes #<issue>` and the `verified-by:` line (GitHub closing keywords inside the worker's own text are neutralised, so only that line can close an issue); post the finish comment; move the issue to `in-review`. If the issue is no longer `in-progress` with the group's bot assigned when `collect` runs (its lease expired and another group re-claimed it, or a human released it), the run is recorded as `superseded`: nothing is pushed or posted, and the worktree is kept for inspection. A retry on the same issue archives the previous run record, so every run counts against the budget. For reviewer and auditor runs, `collect` instead writes the verdict to `$MELD_HOME/results/<group>/<kind>-<n>.json`, tagged with the PR's `head_sha` at dispatch time (head SHA, whether it timed out, the worker's result, and the log tail), and marks the run collected; a second collect that finds an unconsumed verdict there moves it aside to `superseded/` rather than overwriting it. `meld review` and `meld audit` read that file to act on it — submitting the review (approve, request-changes with the worker's comments, or push-fix which pushes the reviewer's commits and requests re-review from the author group) or posting `AUDIT` and, on a `fail` finding, calling `reopen` — then move the consumed file to `submitted/`. A review verdict whose stored `head_sha` no longer matches the PR's current head is stale: `meld review` refuses to submit it and reports so instead of acting on stale intent; re-dispatch a fresh review.

**Failures**: exit without a result file, or a result with outcome `failed`, moves the issue to `blocked` with the last 50 log lines posted. A timeout kills the process, keeps the branch, and does the same. `meld reap` removes the worktree and archives the record after merge or close.

## 12. Polling

`meld wait [--timeout S] [--interval S] [--filter <kinds>]` blocks until the board changes or the timeout elapses, then returns the `meld status` JSON plus a `changes` list. `--filter` takes a comma-separated subset of the change kinds — `phase`, `halted`, `issue`, `pr`, `run`, `comments`, `challenges`, `audits` — and only those kinds count as a change; omitted, every kind does. `challenges` fires on any change to a ref's challenger (a `CHALLENGE` opening, closing, or being replaced by a different challenger on the same ref); `audits` fires on any new or changed `AUDIT #<pr> PASS|FAIL` verdict, per group per PR; `pr` fires on a new head, a change in approvals, changes-requested, checks, or state, *or* a change in commit statuses — so the author group's `meld/review/<group>` status flipping to `success` after a push-fix (section 13 step 3) fires `pr` too, not only a fresh GitHub review. An initial snapshot (no prior state to diff against) always counts, filter or not. Inside, every `polling.interval_seconds`:

- Fetch coordination comments newer than the stored cursor (`since`).
- Fetch open issues in the active milestone with labels and assignees, open PRs on `meld/` branches with reviews and check status.
- Read this group's run records and check whether each PID is alive.
- Diff against `$MELD_HOME/state/<group>/<role>/snapshot.json`; return on any change. The snapshot is **per party** — a group's orchestrator, that group's PM, and the human each keep their own — because both roles of a group poll the same board and a shared snapshot would let whichever polls second consume the other's changes. The conditional-request ETag cache is **per group**, at `$MELD_HOME/cache/<group>`, since both roles authenticate with the same token; it is written atomically and a corrupt entry is treated as no cache.

Three or four conditional API calls per cycle, roughly 400 requests an hour idle against a 5,000 per hour per-token limit. Rate-limit responses trigger exponential backoff and are reported in the returned JSON. Handoff latency is bounded by the interval, about 30 seconds; the timeout only bounds idle re-reads. The wait timeout stays below the calling CLI's command timeout by construction (adapter reports its ceiling; `meld doctor` verifies). `meld claim` re-reads the issue with the ETag cache bypassed before deciding the race.

## 13. Cross-review and merge

For a PR opened on behalf of group A's worker:

1. A's PM moves the issue to `in-review` and posts `REVIEW-REQUEST #<pr>`.
2. Every other group's PM dispatches a `reviewer` (under `review_concurrency`, exempt from the build cap); with three or more groups, `review_concern` gives every reviewing group but the head committer's a different primary concern — correctness and security, tests and product conventions, or architecture, performance, and visual design — in rotation by group name (section 21 item 3; with two groups there is one reviewer, so no concern is assigned). When the PR touches `review.security_paths`, the same PM also dispatches a `security` reviewer, sequentially once its first review is submitted: a run record is keyed by PR number, so two live review runs cannot coexist on one PR.
3. Each reviewer ends in approve, request-changes, or push-fix, submitted by `meld review`. Approve or request-changes posts a GitHub review plus a `meld/review/<group>` commit status (`success` or `failure`) — a personal token cannot create a check run, so this is the neutral signal section 21 item 4 asked for; request-changes also returns the issue to `in-progress` for the author group, or to `blocked` once `review.pingpong_cap` request-changes reviews have accumulated on the PR. Push-fix pushes the reviewer's own commits from its review worktree, posts a `pending` status, makes the pushing group the last committer, and posts a new `REVIEW-REQUEST` for the moved head so A's PM dispatches its own reviewer on it. That reviewer is reviewing A's own PR: GitHub refuses an `APPROVE` or `REQUEST_CHANGES` review from the account that opened a PR (422), so `meld review` posts A's verdict as a `COMMENT` review whose first line names it, and A's `meld/review/<group>` status on the new head is the record the quorum reads (section 8, review approve). `meld review` is idempotent through its own verdict file, not through GitHub state: the review id and the pushed sha are written into the file the moment each lands, so a retry after a later failure skips what already happened, while a second verdict on the same head from another worker (the `security` review after the `reviewer`) is a new file and posts its own review. A push-fix verdict with no commits in the worktree (or a reaped worktree) is a no-action outcome like a missing verdict: the PM is told to re-dispatch. Quality rules in the reviewer prompt: correctness, security, performance, visual design and accessibility (for anything a user sees), tests, then repository conventions, in that order; no style-only change requests; verify claims in the PR body by running them.
4. `meld merge` is legal when approvals on the current head come from at least `min(review.quorum, N-1)` distinct groups that did not author the head (human approvals count; the author group counts through its `success` status on the current head, since GitHub's own-PR rule keeps its approval out of the review list — with two groups this is what lets a push-fixed PR land without a human), required checks are green, no `CHALLENGE` is open, and the head committer is known. Whichever PM sees it first merges, squash, PR title as subject, with the head sha as a guard so a merge attempted after the head moved is refused (409) instead of landing the wrong commit; a 405 means another PM merged first or the PR is no longer mergeable, and a PR the board already shows as merged is a no-op success rather than a refusal.
5. Once `review.pingpong_cap` request-changes reviews have accumulated on one PR, the issue moves to `blocked` and orchestrators must post a resolution. The cap counts `CHANGES_REQUESTED` reviews on the PR — from any reviewer, on any head — not rounds, so two groups each requesting changes on one head spend two of the three.

With two groups the effective quorum is one non-author approval, which is ordinary cross-review. With three or more, it is two, and the first two responders count so extra groups add speed, not rounds.

## 14. Human participation and hallucination protection

### Plan gate

`meld setup --goal "..."` creates the milestone and coordination issue (still plan 5, section 21). Orchestrators plan in `planning`: decompose, reconcile via the agreement protocol, and post `[joint] PLAN plan-<milestone>` with the decomposition written as one line per item — `- id: <slug> | title: <text> | worker: <type> | model: <id> | effort: <level> | size: <S|M|L>`, followed by indented `  - <acceptance criterion>` lines. `meld plan issues plan-<milestone>` parses that text into one `meld:planned` issue per item (a malformed line is reported and skipped, never guessed; `--dry-run` reports what it would create without creating it) and the same text is written into the milestone description. The human reviews in GitHub and runs `meld plan approve`, which adds `meld:plan-approved` and flips every `meld:planned` issue to `ready`, or posts `[human] AMEND` with notes. Mid-milestone additions are proposed and agreed the same way as `plan-add-<n>`, closed with `[joint] PLAN-ADD`, turned into issues by `meld plan issues plan-add-<n>`, and become `ready` only once the human runs `meld plan ack plan-add-<n>` (idempotent: acking twice still posts the `ACK` comment once). Both halves refuse on a `[joint]` comment that is not *verified* (section 7) — written by anyone but the proposer's own bot, or written before the round actually closed.

### Humans as participants

A listed human is a third party in the rules. Self-assignment is a valid claim. A human PR on a `meld/` branch is reviewed by every group, since none authored it. A human approval counts toward the quorum. Human-only powers: `APPROVE` the plan, `ACK` plan additions, `release --force`, `HALT` and `RESUME`, `REVERT #<pr>` (makes a revert PR the top priority), `PROVIDED` and `RESOLVED` for needs-human items, and the CODEOWNERS approval on worker-type PRs.

### Chat

`meld inbox` (non-interactive) and `meld chat` (a REPL, also scriptable one line at a time via `--once TEXT`) open on the same inbox: open `NEEDS-HUMAN` issues (skipping ones already answered by `PROVIDED`/`RESOLVED`), `BUDGET` comments, `[joint] ... DISAGREE` items still awaiting a `DIRECTIVE`/`AMEND`/`APPROVE`, `PLAN-ADD` rounds still awaiting `ACK`, and open `ASK`s (each flagged answered or not). In `chat`, plain text posts `[human] ASK ask-<n>` (the next unused `ask-` number on the board); slash commands post the human verbs directly — `/halt`, `/resume`, `/directive TEXT`, `/provided`/`/resolved`/`/revert #REF [TEXT]`, `/challenge ACTION REF TEXT`, `/ack REF`, `/approve` — `/inbox` re-prints the inbox, and `/wait` blocks until the board changes and prints the new coordination comments: by default only `[joint]` and `[human]` ones plus `NEEDS-HUMAN`/`BUDGET`/`DRIFT`/`PROTOCOL-VIOLATION`, every comment with `--verbose`. A malformed slash command (a missing ref, an unknown action) is recorded as an error and answered inline; it does not end the session. `DIRECTIVE` requires `ACK` from every group rather than agreed wording, and orchestrators treat it as overriding their plan.

### Needs-human

When a retry depends on a person (credential, external account, product decision, budget, permission), the requesting orchestrator labels the issue `meld:needs-human` and posts `NEEDS-HUMAN #<issue>` with a structured body: what is needed, why the agent cannot do it, what happens once provided. `claim` and `dispatch` are illegal on that issue until a listed human posts `PROVIDED` or `RESOLVED` after the request. Surfaced in `meld status` (top-level `needs_human`), the PM `STATUS` comment, and the `meld inbox`/`meld chat` inbox. With `[notify] slack_dm_webhook_env` set, every PM's `meld tick` posts one Slack message naming all currently-due needs-human issues (plan-4's header decision 9 said one message per issue; what shipped, and what this describes, is one message listing every due issue) and repeats only after `notify.repeat_after_minutes` per issue; the throttle is recorded independently per party in `$MELD_HOME/state/<group>/pm/notify.json`. A failed post — an `OSError`, an `http.client` exception, a bad value — or an unreadable throttle entry never raises out of `tick`: the failure comes back as `notify_errors`, the item stays due, and a malformed timestamp reads as "never notified". `meld notify --dry-run` previews the message without sending it or recording a new send (a misconfigured webhook still lets a stale, no-longer-`needs_human` entry be pruned from the throttle file even on a dry run — nothing is ever sent early, but the state file can still shrink); a missing or unset webhook is reported as `skipped`, not an error, and a failed post is retried on the next `tick` rather than marked sent. Because the throttle is per party rather than shared, N configured groups can send up to N independent copies of the same nudge — deliberate (a group that has gone quiet must not be able to silence the human's only channel), but a plan-5 candidate is a shared dedup marker (section 21). Secrets never travel through GitHub: `PROVIDED` names where the value was placed (an env var or 1Password item) and the runner injects it.

### Hallucination protection

No group grades its own claims; a claim without evidence is not a fact.

- **Evidence.** `verified-by:` is mandatory on `PLAN`, `DECISION`, `ANSWER`, `EVIDENCE`, and worker finish comments. Prompts forbid asserting test results without pasted output.
- **Challenge.** An orchestrator or a human opens one with `meld challenge open <ref> -m "..."` (a comment, plan item, issue, or PR) against a claim that is not its own party's; PMs may not act on challenges at all, and a bare number is read as `#<n>`. The target gains `meld:challenged`; `dispatch` and `merge` are blocked on it. `meld challenge evidence <ref> -m "..."` (with a `verified-by:` line) is the challenged party's orchestrator's to post, or a human's; `meld challenge retract <ref>` may come from the challenger (withdrawing the doubt) or the challenged party's orchestrator (conceding the claim). `meld challenge uphold <ref>` or `dismiss <ref>` is decided by an orchestrator of a group that is neither challenger nor challenged — with two groups, the human. Each of these five verbs applies or clears `meld:challenged` as `meld challenge` posts it (the label moves first, so a failed post leaves a clean retry rather than a stuck label); `meld post` refuses all of them.
- **Independent audit.** Every merged PR except a memory PR (whose content was verified against the board when it was reviewed, section 15, and which has no worker output to audit) is audited by a group that did not author it. `meld status`'s `audit` block tells each group which merged PRs it owes (`audit.pending`) and whether the batch is due (`audit.due`, once `[audit] batch_merges` pending audits accumulate or the oldest has waited `batch_hours`); the PM then dispatches one `auditor` run at a time, checked out at the merge commit rather than the (possibly deleted) head branch. `meld audit` posts `AUDIT #<pr> PASS|FAIL` on the coordination issue with the auditor's findings; a `FAIL` makes `meld reopen <issue>` legal for that group, returning the issue to `ready` with the findings quoted.
- **Drift check.** On every restart, each orchestrator compares the board to the approved plan and the decision log and posts `DRIFT` when scope, approach, or assumptions moved without a `DECISION`.
- **Human overrides.** `HALT`, `RESUME`, `REVERT`, and `CHALLENGE` are all available to the human from `meld chat`.

## 15. Shared memory

Canonical memory is versioned files in the product repo.

- `.meld/decisions/NNNN-<slug>.md`: one per agreed `DECISION`, numbered from one past the highest sequence already present under `.meld/decisions/` on origin's default branch: date, agreeing groups, a link to the *verified* `[joint] DECISION` comment (`grammar.verified_joint`, never an earlier hand-written one), and the agreed text verbatim under `## Decision`; a round that ended `DISAGREE` is refused. `meld decide <ref>` renders it and opens a memory PR on branch `meld/memory-<slug>`, committed and pushed with the calling group's bot identity (forcing `commit.gpgsign=false` for that one commit, so a machine-wide `commit.gpgsign=true` can never hang it); the PR body carries a `meld-memory: decision <ref> <sha256-of-the-file>` marker.
- `.meld/memory/lessons.md`: appended by `meld remember <ref> -m "..."` when a blocked, challenged, or reverted issue or PR resolves — free-form text (what went wrong, what fixed it, what to check next time), dated and attributed to the calling group — through the same memory-PR machinery on its own `meld/memory-lesson-<n>-<timestamp>` branch; the marker is `meld-memory: lesson <ref> <sha256-of-the-appended-entry>`.
- **CI requirement.** Branch protection requires the product's CI check, so that check must report on every PR including docs-only ones; `meld setup` warns when the workflow uses `paths` filters that would leave decision or lesson PRs unmergeable (still plan 5, section 21).
- **Auto-approval path, and no worker.** `meld dispatch --kind review` refuses on a memory PR ("run `meld review N` directly"); `meld review N` itself fetches the changed file at the PR's head and decides: a **decision** PR must change exactly one file under `.meld/decisions/`, whose content hash matches the marker *and* which is byte-identical to `meld decide`'s own rendering of the verified `[joint] DECISION <ref>` — the agreement's text and agreeing groups, the verified joint comment's URL as `- Source:`, and the file's own `- Date:` line, which must be a `YYYY-MM-DD` date and is the one value taken from the file itself — so no preamble or note can ride into the repo (and from there into every brief's `## Relevant memory`); a round that ended `DISAGREE` is refused. A **lesson** PR must change only `.meld/memory/lessons.md`, whose head must equal the default branch's copy (or `# Lessons\n` when there is none) plus exactly the one `## `-delimited entry that hashes to the marker — append-only by verification, so a lesson PR can neither rewrite or drop an earlier lesson nor add a second entry under one marker (a PR whose base has moved since needs a rebase) — and whose `ref` is on the board, no longer `blocked`/`needs_human`, and not challenged — a lesson has no agreement round to check against, so it is verified against the board's own state instead (plan-4 decision 1). Either way `meld review` posts `APPROVE` or `REQUEST_CHANGES` plus the same `meld/review/<group>` commit status an ordinary review uses, so a memory PR still needs the normal quorum — it just never waits on a dispatched reviewer or a human. A cached `APPROVE` is trusted only while the PR's head hasn't moved since; every other case (no cache, a cached `REQUEST_CHANGES`, or a moved head) re-verifies from scratch, so a corrected file is reflected on the next call and a second `meld review N` never posts a duplicate review of an unchanged verdict.
- `meld recall <query>` searches `.meld/decisions/`, `.meld/memory/lessons.md`, and (via the GitHub issue search API, `search/issues`, scoped to the repo and the `meld:coordination` label) past `meld:coordination` issues in the repo, ranked by term hits, returning matches with links. Orchestrators run it at launch and before planning or proposing a decision that might already exist. `meld dispatch` renders up to three locally-matching snippets (decisions and lessons only, not the coordination search) into every worker brief under `## Relevant memory`, matched against the issue's or PR's own title and body. The two halves read different state: `decide`/`remember`'s own "already recorded" check fetches and reads `origin/<default>` fresh each time, while `recall` (and so every brief) reads the local checkout, which can lag behind it — briefs are advisory, so a stale miss just means a snippet doesn't show up (a plan-5 sweep might fetch first, section 21).
- Workers report which installed skills they used: a worker's `.meld-result.json` may carry `skills_used` (a list of names, silently truncated to 30 entries of 100 characters each rather than failing an otherwise-good result over telemetry), and `meld collect` appends a `skills used: ...` line to the finish comment whenever the list is non-empty.
- The coordination issue is per milestone; cross-milestone continuity comes only from these files.

## 16. Adapters (N groups)

An adapter implements: `launch(spec: LaunchSpec) -> subprocess.Popen`, `installed_skills() -> list[WorkerType]`, `command_timeout_ceiling_seconds() -> int`, `supports_effort() -> bool`. `LaunchSpec` carries `role`, `model`, `effort`, `cwd`, `prompt`, `log_path`, `timeout_seconds`, `env`, and `max_turns`; model validity and tiers live in `models.toml`, not in the adapter. Claude and Codex ship in v1. Adding a system is one adapter module, one `models.toml` section, one bot account, and one `[groups.<name>]` block. Nothing in `rules.py`, `grammar.py`, or the prompts refers to a specific CLI.

The Claude adapter runs `claude -p <prompt> --model <model> --output-format json --permission-mode bypassPermissions --strict-mcp-config --mcp-config '{"mcpServers": {}}' --settings '{"disableAllHooks": true, "autoMemoryEnabled": false, "env": {...}}' --setting-sources user,project --max-turns <n>`. The `env` map in `--settings` sets every scrubbed variable name (`GH_TOKEN`, `GITHUB_TOKEN`, the enterprise variants, `MELD_GROUP`, `MELD_ROLE`, `MELD_PUSH_TOKEN`) to the empty string: `--settings` is merged last, so a `settings.json` `env` block loaded through `--setting-sources` cannot hand the worker a token or a party identity that `worker_env` just removed (section 18). Its environment sets `CLAUDE_CODE_EFFORT_LEVEL` to the effort, disables auto-memory and nonessential traffic, and drops the `CLAUDECODE`/`CLAUDE_CODE_ENTRYPOINT` variables a worker would otherwise inherit from meld's own process. `user` was added to `--setting-sources` in plan 3 so a worker can actually load skills installed under `~/.claude/skills` and plugin caches (section 10 already counted them into the roster); hooks stay disabled and the MCP config stays empty, so the change only reaches the user's own `settings.json` permissions and environment, which `--permission-mode bypassPermissions` already makes moot.

The Codex adapter runs `codex exec -m <model> -c 'model_reasoning_effort="<effort>"' -s workspace-write -a never -C <cwd> --json -o <cwd>/.meld-last-message.txt --ignore-user-config --ephemeral <prompt>`, with the environment passed through unchanged; this is unchanged from plan 2, since `codex exec --help` scopes `--ignore-user-config` to `config.toml` and does not mention skills, so it was never blocking one. Codex has no equivalent way to load a skill at run time, though: its `installed_skills()` scan still feeds the roster and the worker brief, so a Codex worker's brief names each skill and its path and tells the worker to open and follow the `SKILL.md` directly rather than invoke it (a fuller Codex skills story is still plan-5 work, section 21). Both adapters run in the worktree with stdin closed and stdout and stderr appended to the log file, no interactive approval prompts.

## 17. Error handling and budgets

| Failure | Detected by | Response |
|---|---|---|
| Worker exits without result | PM via run record | `blocked`, log tail posted, orchestrator re-tags and re-dispatches or splits |
| Worker exceeds deadline | `meld tick` | SIGTERM then SIGKILL; `collect` marks a build issue `blocked` with the log tail, or records the timeout and log tail in the verdict file for review/audit runs |
| Review ping-pong cap | rules | `blocked`; orchestrators must post a resolution |
| Review verdict on a moved head | `meld review` | marked stale; re-dispatch |
| Illegal verb | rules | Refused; `PROTOCOL-VIOLATION` posted |
| CI red | author group's `meld tick` via `pulls[].checks` | Comments once naming the PR (the durable evidence), then moves the linked issue back to `in_progress` — once per head; the next build run on the issue is the fix. `merge` stays refused until checks are actually green regardless |
| Orchestrator or PM dies | runner | Restart, rebuild from `meld status`, post `RESUMED` |
| Other group silent past `silence_grace_minutes` | orchestrator | `PING`, then continue `SOLO` with a note |
| GitHub rate limit | `meld wait` | Backoff, no action |
| Off-protocol GitHub change (label or assignee changed on a milestone issue by an actor that is neither a configured bot nor a listed human) | `meld tick`'s `off_protocol_sweep`, reading each open issue's own `issue_events` against a persisted per-issue cursor (not a `meld status` snapshot diff) | `PROTOCOL-VIOLATION` naming the actor and the change, posted once per event by the alphabetically first configured group's PM; no automatic revert |

Budgets are per milestone in worker runs by model tier plus a wall-clock cap. `claim` refuses when the issue's model tier is exhausted for the caller; the orchestrator re-tags to a cheaper tier or posts `BUDGET`. Spend to date appears in `meld status` and the PM `STATUS` comment.

## 18. Security

- Bot tokens are fine-grained PATs scoped to the product repo with **Contents**, **Issues**, **Pull requests**, and **Commit statuses** set to read and write — `meld review` publishes every verdict as a `meld/review/<group>` status, and without the last permission the review posts and the status is recorded as `status_error` — injected from 1Password via `op run` into the orchestrator and PM processes only. Workers receive no token.
- Only configured bots and allow-listed humans can speak in the grammar. All other comments are ignored.
- `meld setup` refuses public repos without `--allow-public` and prints the injection risk when overridden.
- Secrets never appear in issues, PRs, briefs, or `.meld/` files. `PROVIDED` comments name a location, not a value.
- Workers inherit no credentials. Their environment is scrubbed of `GH_TOKEN`, `GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN`, `GITHUB_ENTERPRISE_TOKEN`, every `MELD_TOKEN_*`, and the party variables; `GH_CONFIG_DIR` points at an empty per-run directory, so `gh` has no login; git's credential helpers are reset (`credential.helper=` through `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_n`) and `GIT_TERMINAL_PROMPT=0`, so a push or fetch that needs credentials fails instead of borrowing the human's keychain. `meld` itself refuses to run with `MELD_WORKER=1` set. The Claude adapter additionally blanks every scrubbed variable name through the `env` map of its `--settings` JSON, so a user or project `settings.json` loaded via `--setting-sources user,project` cannot re-inject one; `MELD_TOKEN_*` names are per group and unknown to the adapter, so `meld doctor` (still plan 5) is meant to warn when either settings file's `env` names one. Not blocked: SSH keys or an agent on the host, so the product clone's `origin` should be an HTTPS URL.
- Filesystem isolation is only what each adapter provides. The Claude adapter runs `--permission-mode bypassPermissions` (no sandbox: a worker can write wherever the user can) and relies on the environment scrubbing above; the Codex adapter runs `-s workspace-write`.
- `.meld/STANDARDS.md`, the product's own quality bar, is rendered into the `## Product standards` section of every worker brief and both role prompts; the framework's own default, used when the file is absent or empty, lives in `brief.DEFAULT_STANDARDS`.
- Everything under `$MELD_HOME` is disposable state.

## 19. Testing

- **Rules** (`test_rules_issues.py`, `test_rules_prs.py`, `test_rules_challenges.py`): every state, verb, party, and N in {2, 3}, from recorded board fixtures. This is the highest-coverage module.
- **Grammar** (`test_grammar.py`): every header verb, required fields, round counters, amend cap, majority and tie handling, malformed and unauthorized comments.
- **Review and merge** (`test_review.py`, `test_merge.py`): submitting a collected verdict as a GitHub review and a `meld/review/<group>` status, idempotent retries, stale-head detection, push-fix, the ping-pong cap; `merge`'s quorum math including human approvals and the head-sha guard.
- **Audit, challenge, and results** (`test_audit.py`, `test_challenge.py`, `test_results.py`): posting `AUDIT` and reopening a failed issue; opening, answering, and closing a challenge under the closer rules; verdict-file storage, superseding, and submission.
- **Dispatch** (`test_dispatch.py`): a fake adapter binary records arguments and writes fake results; tests worktree creation, brief rendering, timeout kill, post-run PR opening, reap.
- **Memory** (`test_memory.py`): decision rendering, hash match for auto-approval, recall ranking.
- **Smoke** (`tests/smoke/`, opt-in): one tiny real task per adapter against a scratch repo.

No test claims to measure decision quality. That is done by watching the first milestones and converting each bad decision into a prompt edit or a new invariant.

## 20. Bootstrap

1. Human: create one GitHub machine account per group, add each as a collaborator on the product repo, mint one fine-grained token each with repository permissions Contents, Issues, Pull requests, and Commit statuses (read and write), store in 1Password.
2. `git clone` the framework; `uv sync`; `task meld:doctor` verifies both CLIs installed and logged in, tokens resolve, `gh` is on the human's own account, and command-timeout ceilings exceed `wait_timeout_seconds`.
3. `task meld:setup REPO=owner/name GOAL="..."` creates labels, milestone, coordination issue, CODEOWNERS entry, branch protection, and a starter `.meld.toml` with adapter-populated model fields, committed via a PR for the human to approve.
4. `task meld:run GROUP=claude` and `task meld:run GROUP=codex`, in terminals or as launchd agents. Each posts `ROSTER` and `CONFIG`.
5. `meld chat`. Approve the plan by label when it appears.

## 21. Future work (explicitly out of v1)

### Borrowed from prior art (approved 2026-09-03)

A survey of existing multi-agent coding systems (Symphony and its forks, Gas Town/Beads, Code Conductor, claude-code-review-council, ai-pair, Claude Code Review) found no project combining Meld's mechanisms, but five of their ideas are adopted for the later plans:

1. **Lease-plus-heartbeat claims** (from Beads). A claim carries a TTL that the PM's loop renews while the worker runs; an expired claim auto-reverts the issue to `ready` and posts a note. Replaces bare first-assignment-wins as the crash-recovery path. Landed in plan 2 with dispatch and collect, with a TTL variation: the lease is fixed at dispatch to the worker's deadline plus `lease.grace_minutes` rather than renewed on a heartbeat while the worker runs (section 11); `tick` still fills in a lease that's missing and still releases one that has expired.
2. **Citation verification before a finding is shown** (from claude-code-review-council). The reviewer worker's contract requires every cited file and line to be opened and checked against the checkout before it appears in the review; uncheckable citations are dropped and noted. Landed in plan 2's reviewer prompt.
3. **Role-matched review** (from ai-pair). When more than one group reviews a PR, the review request assigns each a different primary concern (correctness and security, tests and conventions, architecture) so reviewers surface distinct issues. Landed in plan 3: `review_concern` assigns one once there are three or more groups (with two, there is exactly one reviewer, so nothing to rotate).
4. **Neutral check runs** (from Claude Code Review). Cross-group review results are published as a GitHub check run with a neutral conclusion, visible but never blocking on its own; merge remains gated by the quorum rule in `rules.py` and the human. Landed in plan 3 with `review` and `merge`, with one variation: a personal token cannot create a check run, so each group's review is published as a **commit status** instead (`meld/review/<group>`, `success`/`failure`/`pending`) — visible on the PR and never blocking unless a human adds it to the repository's required checks, which keeps the same intent.
5. **Repo-local verification bar** (from Claude Code Review's `REVIEW.md`). A product may add `.meld/REVIEW.md` stating what counts as evidence for that repo; it is rendered into every worker's brief, not just reviewer and auditor. Landed in plan 2 with brief rendering.


- Webhooks when running on an always-on box.
- Per-group machines and a shared `$MELD_HOME` alternative.
- A third adapter to prove the N-group claims end to end.
- A read-only GitHub token for workers that need to read issues beyond their brief.
- PID reuse is only partly detected: `kill_run` signals a pid only while it still leads its own process group, so a reused pid that is not a session leader is left alone; one that is a session leader is indistinguishable from the worker. A run record's pid plus deadline remains the liveness signal.

### Carried into plan 3 from the plan-1 final review

1. **A `[joint]` comment is only as trustworthy as its author.** `is_authorized` accepts a `[joint]` header from *any* configured bot, so a hand-written `[joint]` comment parses as `ok` and reads as the close of an agreement round. Still open at the end of plan 3: `decide`, `plan`, and auto-approval didn't exist yet to require it — the joint comment's author must be the proposer's bot and the agreement must have reached `agreed`, `majority`, or `disagree` at the time it was written. An `Agreement.joint_verified` field is the suggested seam. **Landed in plan 4:** `Agreement.joint_verified` (plus `joint_verb` and `joint_author`) is exactly that field, and `decide`, `plan issues`, and `plan approve` all refuse on an unverified `[joint]` comment (section 7, section 8).
2. **Challenge closers are unrestricted.** `derive_open_challenges` lets any authorized party close a challenge with `UPHELD`, `DISMISSED`, or `RETRACT`. Landed in plan 3: `challenge_rule` restricts `EVIDENCE` to the challenged party (or a human), `RETRACT` to the challenger or the challenged party (or a human), and `UPHELD`/`DISMISSED` to a party that is neither, per section 14.
3. **`meld doctor` checklist.** It must verify that each group's token identity equals the bot login configured for that group; that a git identity is configured per bot for worktree commits; that hidden `.pth` files are detected (see the README); and that every model named in `.meld.toml` exists in `models.toml` for that group's adapter. `meld doctor` itself does not exist yet. **Still not built at the end of plan 4 either** — it stays a plan-5 obligation; see "Carried into plan 5" below for the full, grown checklist.
4. **Git identity per bot (plan 2).** Landed in plan 2 as `GIT_AUTHOR_*`/`GIT_COMMITTER_*` in the worker environment, so a PR's head committer resolves to that bot's account (without it the head committer is unknown and `approve` and `merge` are refused, section 8). Not as `git config` in the worktree: a linked worktree shares the clone's `.git/config`, so a per-worktree write would relabel every other group's commits and the human's own.
5. **`meld review`/`meld audit` consume `$MELD_HOME/results/<group>/<kind>-<n>.json`.** Plan 2's `collect` stops at writing a reviewer or auditor run's verdict to that file; it does not submit a review or post an audit finding. Landed in plan 3: `meld review` and `meld audit` load the file with `results.load_result` and act on it, then move it to `submitted/` (or `superseded/` first, if a later collect displaced it before it was consumed).
6. **Move reap/tag/review-dispatch legality into `rules.py`** so `meld status` lists them under `legal` (today the predicates live in `dispatch.reap`, `_dispatch_pr`, and `tagging.tag`). Landed in plan 3: `reap_rule`, `_tag`, `_review`, and `_audit` all live in `rules.py`, and `meld status` lists `reap`, `tag`, `review`, and `audit` under each issue's or PR's `legal` array (challenge legality is still read only from `meld challenge`'s own refusal, section 8).

### Carried into plan 4

Recorded here so plan 4 is written against this plan's module names rather than rediscovered from scratch:

- **Decision memory** (section 15): `meld decide`, `meld remember`, `meld recall`, and `memory.py` do not exist yet. **Landed in plan 4**, as section 15 now describes.
- **Chat** (section 14): `meld chat` and the human console do not exist yet. **Landed in plan 4**: `meld inbox` and `meld chat` (including `--once` and `--verbose`), as section 14 now describes.
- **Plan gate**: `meld plan`, the human's `meld:plan-approved` label flipping every `meld:planned` issue to `ready`, and `PLAN-ADD` needing a `[human] ACK` are all still unimplemented. **Landed in plan 4**: `meld plan issues|approve|ack`, as section 8 and section 14 now describe — except its legality still isn't folded into `rules.py` (carried into plan 5 below).
- **Setup** (section 20 step 3): `meld setup` does not exist yet; when it is built, branch protection should list the `meld/review/<group>` statuses (section 13) as informational rather than required, matching plan 3's choice to keep them non-blocking. **Still not built**; carried into plan 5 below unchanged.
- **`meld doctor`** (item 3 above): plus verifying each adapter's `command_timeout_ceiling_seconds()` exceeds `wait_timeout_seconds`; that each bot token carries **Commit statuses: write** alongside contents, issues, and pull requests (section 18 — without it every `meld review` records a `status_error` and the author group's verdict never reaches the quorum); a warning when the user's or the product's Claude `settings.json` has an `env` block naming a scrubbed variable or any `MELD_TOKEN_*` (section 16); and a scratch sweep of `$MELD_HOME/results/<group>/superseded/`, `results/<group>/submitted/`, and the per-run `runs/<group>/<n>-gh` and `<n>-gitconfig` files dispatch leaves behind. **Still not built**; carried into plan 5 below with its checklist grown further.
- **Notifier**: `[notify]` is parsed into `Config`, but nothing reads `notify_webhook_env` or `notify_repeat_after_minutes` yet. **Landed in plan 4**: `notifier.py`, `meld notify`, and the per-party throttle called from `meld tick`, as section 14 now describes.
- **CI red as request-changes** (section 17): today `merge` simply refuses until checks are green; converting a red check into an automatic `request-changes` review is not implemented. **Landed in plan 4, with a variation**: `housekeeping.ci_red_sweep`, run from the author group's own `meld tick`, comments once per head and moves the issue back to `in_progress` rather than posting an actual GitHub `REQUEST_CHANGES` review (section 17).
- **Off-protocol change detection** (section 17 last row): nothing diffs a board snapshot against the previous one to catch a label or assignee changed outside `meld` and post `PROTOCOL-VIOLATION` naming the actor. **Landed in plan 4, with a variation**: `housekeeping.off_protocol_sweep` reads each open issue's own GitHub `issue_events` against a persisted per-issue cursor, rather than diffing two board snapshots (section 17).
- **`IGNORED-COMMENT` posting in the PM `STATUS`**: `board.ignored_comments` counts them and `meld status` surfaces the count, but nothing posts an `IGNORED-COMMENT` comment or folds it into a `STATUS` summary yet. **Landed in plan 4**: `housekeeping.flag_ignored_comments`, called from `meld tick` (the alphabetically first configured group's PM posts; every PM's cursor still advances); the PM prompt now folds `coordination.ignored_comments` into its `STATUS` line (section 7).
- **The same-group lease re-claim window**: `meld claim` posts `EXPIRED` to supersede its own stale `LEASE` the moment it re-claims an issue, but leases are still not tied to run records, so this closes the window with a comment-level patch, not a structural fix. **Still open**; carried into plan 5 below.
- **Worker-authored commit messages can still close issues on merge**: `neutralise_closers` scrubs closing keywords from PR bodies and review/audit comment text, but not from the git commit messages a worker writes in its worktree, which GitHub's squash-merge can fold into the merge commit's own body. **Still open**; carried into plan 5 below.
- **`ResourceWarning`s under `-W error`**: the detached `Popen` objects `popen_detached` returns are never waited on, which is correct for a fire-and-forget worker process but warns under strict warning settings. **Still open**; carried into plan 5 below.
- **The joint-comment author check** (item 1 above): `Agreement.joint_verified` is still just a suggested seam; nothing enforces it yet. **Landed in plan 4**, per item 1 above.
- **`board_digest` omits challenge, audit, and status detail**: it carries `open_challenges` as a bare set of refs, not `Board.challenges`/`Board.audits`, so a re-challenge by a different challenger on an already-open ref does not fire `wait --filter challenges` (a new `AUDIT` comment still fires `comments`); and it carries GitHub approvals but not `PullRequest.statuses`, so the author group's `meld/review/<group>` `success` status (section 13 step 3) making `merge` legal does not fire `wait --filter pr` — the author group's own PM sees `merge` in its next `meld status` and merges, so the loop completes, but the other PM learns of it one poll later. **Landed in plan 4**: `board_digest` now carries `challenges`, `audits`, and each PR's `statuses`; `diff_changes` emits `challenges` and a new `audits` kind and fires `pr` on a status change too (section 12).
- **`Board.pr_for_issue` prefers an open PR over the merged one that actually closed the issue** when more than one PR references it — pre-existing, worth revisiting once multi-PR issues are common. **Landed in plan 4 for a closed issue** (plan-4 decision 11): the merged PR now wins once the issue is closed. Still open when several PRs are open for one issue at once; carried into plan 5 below.
- **A fuller skills story**: relevance-matched skills per brief instead of the whole roster, a shared `.meld/skills/` in the product repo synced into each adapter's own skill directory, `skills_used` recorded in the result file, `skill:<name>` dispatch hints, and — since Codex has no run-time invocation to enable (section 16) — a Codex-specific path that goes beyond naming the file for the worker to read. **`skills_used` landed in plan 4** (`WorkerResult.skills_used`, the finish comment's `skills used:` line, section 15); the rest is still open and carried into plan 5 below.

### Carried into plan 5

Recorded here (superseding the still-open items marked above) so plan 5 is written against this plan's module names rather than rediscovered from scratch. Several are unchanged carryovers from "Carried into plan 4"; the rest surfaced during plan-4's own task-by-task review.

1. **`meld setup`.** Still does not exist. Labels with colors, milestone, coordination issue, CODEOWNERS entry, branch protection (the `meld/review/*` statuses listed as informational, not required, matching plan 3's own choice), a starter `.meld.toml` with adapter-populated model fields, a refusal on a public repo without `--allow-public`, and a warning when the product's CI workflow's `paths` filter would leave decision or lesson PRs unmergeable (section 15, section 20 step 3).
2. **`meld doctor`.** Still does not exist. The full checklist: both CLIs installed and logged in; each group's token identity matches its configured bot login; `gh`'s own login is a listed human; a git identity configured per bot for worktree commits; hidden `.pth` files detected (README); every model named in `.meld.toml` exists in `models.toml` for that group's adapter; each adapter's `command_timeout_ceiling_seconds()` exceeds `wait_timeout_seconds`; each bot token carries **Commit statuses: write** (without it every `meld review` records a `status_error` and that group's verdict never reaches the quorum); a warning when the user's or the product's Claude `settings.json` `env` block names a scrubbed variable or any `MELD_TOKEN_*`; and a scratch sweep of `$MELD_HOME/results/<group>/superseded/`, `results/<group>/submitted/`, the per-run `runs/<group>/<n>-gh` and `<n>-gitconfig` files, and stale `worktrees/<group>/memory-*` directories left behind by `meld decide`/`meld remember` (section 20; plan-3 item 3 above).
3. **A fuller skills story.** Relevance-matched skills per brief (today's roster is matched only by the worker's title, and is the whole thing), a shared `.meld/skills/` in the product repo synced into each adapter's own skill directory, `skill:<name>` dispatch hints, and — since Codex still has no run-time skill invocation (section 16) — a Codex-specific path beyond naming the file for the worker to read.
4. **Leases tied to run records.** `meld claim`'s `EXPIRED` re-post only patches over the same-group re-claim window (section 11); a lease is still not a structural property of the run record itself.
5. **Worker-authored commit messages can still close issues on merge.** `neutralise_closers` scrubs PR bodies and review/audit comment text, not the git commit messages a worker writes in its own worktree, which a squash-merge can fold into the merge commit's own body.
6. **`ResourceWarning`s under `-W error`.** The detached `Popen` objects worker and memory-PR dispatch both return are never waited on.
7. **The author group's own request-changes doesn't count toward the ping-pong cap.** Because GitHub refuses a `REQUEST_CHANGES` review from a PR's own author, `meld review` posts the author group's request-changes verdict as a `COMMENT` review (exactly like its approve, section 8) — so it lands as GitHub state `COMMENTED`, never `CHANGES_REQUESTED`, and never adds to `pingpong_exceeded`'s count, even though the reviewer prompt treats it as a real verdict.
8. **`legal` doesn't show the author group's comment-review path.** `legal_pr_verbs` computes `approve`/`request-changes` through `pr_refusal`, whose `_approve`/`_other_party_only` rules always refuse a PR's own author group (`AUTHOR_RULE`) — even though `meld review`, through the separate `review_verdict_rule`, does let that group submit its own verdict as a GitHub `COMMENT` review once it is not also the head committer (section 8, section 13 step 3). A PR's `legal` array can therefore say `approve` is illegal for the author group at the exact moment `meld review` would accept and post one.
9. **`Board.pr_for_issue` when several PRs are open for one issue.** Plan 4 fixed the closed-issue case (the merged PR wins, decision 11); ties among several simultaneously open PRs on one issue still just resolve to "highest number wins."
10. **A milestone-end audit trigger.** `audit.due` still only fires from `batch_merges`/`batch_hours`; nothing yet forces the last few merges of a milestone to be audited before the milestone is considered closed.
11. **`status_error` on a memory-PR review is never retried.** When `meld review`'s `create_status` call fails on an already-approved memory PR, the failure is returned in the response but never stored, so nothing re-attempts posting that status later (harmless for the *other* group's approval, which is a real GitHub review either way — but would matter were that group ever the head committer).
12. **Orphaned `ci-red.json` records.** A PR closed mid-handling (between the evidence comment and the label swap, or after) leaves its per-PR record in the PM's state directory forever; harmless — the guard only re-processes open PRs — but never pruned.
13. **`_local_hits` scans every decision and lesson file on every dispatch.** Fine at plan-4 scale; wants an index or cache once `.meld/decisions/` and `lessons.md` grow large, so brief-rendering latency stops growing with the memory store.
14. **`MARKER_RE` is duplicated**: once in `memory.py` for the memory-PR body marker, once in `board.py`'s `PullRequest.memory` (to avoid a `board.py` → `memory.py` import). Move it to a leaf module both can import from.
15. **Per-party Slack throttling means one nudge per group's PM**, not one for the milestone (section 14, "Needs-human"). Deliberate for now (a down group must not be able to silence the human); a shared dedup marker in a location every PM can see is the plan-5 option.
16. **Two Task-10 prompt-wording notes**, both cosmetic: `pm.md`'s ordering of the Slack-nudge clause within the `tick` sentence, and `_preamble.md`'s phrasing of what `/approve` touches. **Landed in the plan-4 final fix wave.**
17. **Minor plan-4 review nits**, cosmetic and behaviour-neutral: `github.py`'s `create_status`/`create_review` take their state/event as a positional string rather than a named or typed parameter; `tests/test_github.py` has one `import base64` sitting mid-file, ahead of the test that needs it, instead of joining the other imports at the top; `get_contents`'s error for an unexpected payload names only the Python type (`unexpected payload {type(resp).__name__}`), not what was actually there; a redundant boolean disjunct in `board.py`'s closed-issue computation; (the case of a second `[joint]` comment superseding the first on an already-closed ref got its test in the final fix wave); `wait`'s diff text prints `None` for a missing challenge but `{}` for a missing audit (placeholder inconsistency); a `GitError` raised by `remove_worktree` inside a `finally` block could mask an original exception (no realistic trigger found).
18. **`plan`/`decide`/`remember` still aren't in `rules.py`'s legality tables.** All three refuse through the same `Refusal`/`PROTOCOL-VIOLATION` path as every verb above (section 8), but their conditions are inline checks in `plan.py`/`memory.py`, not `rules.py` predicates alongside `claim`/`dispatch`/`review`/etc. — so `meld status`'s per-issue and per-PR `legal` arrays never mention `plan`, `decide`, or `remember`, even at the moment calling one of them would actually succeed. Extends the consolidation plan-3 item 6 above already did for `reap`/`tag`/`review`/`audit` to the three verbs plan 4 added.
19. **`recall` can lag `decide`/`remember`'s own view of what's already recorded.** `decide` and `remember`'s "already recorded" check fetches and reads `origin/<default>` fresh on every call (section 15); `meld recall` — and so every worker brief's `## Relevant memory` — reads the local checkout instead, which can be behind it. Harmless today (briefs are advisory; a stale miss just means a snippet doesn't show up), but a plan-5 sweep that fetches before reading would close the gap.

The final whole-branch review of plan 4 (its fix wave landed F1–F10, F12, and the audit exclusion below as code) left these recorded rather than fixed:

20. **`BUDGET` items in the inbox clear on any later human comment that is not an `ASK`.** `chat.inbox` drops a `BUDGET` entry once any human `DIRECTIVE`/`HALT`/`PROVIDED`/… follows it, whether or not it addressed the budget; a ref on `BUDGET` (and a human answer keyed to it) would let the human clear one deliberately.
21. **Memory-PR review records are never `mark_submitted`.** `meld review` keeps `results/<group>/review-<n>.json` for a memory PR as its idempotency record (head, event, reason) rather than moving it to `submitted/`; the `meld doctor` scratch sweep (item 2) should recognise and prune those once the PR is merged or closed.
22. **Housekeeping posts depend on the alphabetically first group being up.** `IGNORED-COMMENT` and off-protocol `PROTOCOL-VIOLATION` are posted only by the first configured group's PM, while every other PM advances its own cursor regardless — so events that arrive while the first group is down are never posted by anyone, including that group once it returns.
23. **`get_contents` fails for files over 1 MB.** GitHub's contents API omits `content` above that size; `lessons.md` grows without bound, so the verifier needs the blob endpoint or the `raw` media type before a product's lessons file gets there.
24. **`meld notify` from a non-PM party throttles separately.** The throttle file is per party (`state/<group>/<role>/notify.json`, or `state/human/`), so an orchestrator's or a human's `meld notify` never sees the PM tick's sends and can nudge again immediately.
25. **A `decide` whose `REVIEW-REQUEST` post fails cannot be retried while its memory PR is open.** The PR exists, so a retry is refused as already recorded; the other PMs still see the PR on the board and `meld review N` still works, but nobody was asked.
26. **Merged memory PRs owe no audit — landed in the fix wave as code** (`rules._audit` and `status.audit_summary` exclude PRs with a `meld-memory:` marker); recorded here because it was the one §21 gap that was a bug rather than a deferral.

## 22. Glossary

- **Group**: one AI system (CLI + bot account + config) with an orchestrator, PM, and workers.
- **Party**: who is speaking in the grammar: a group, a group's PM, `human`, or `joint`.
- **Quorum**: the number of distinct non-author groups whose approval a PR needs; `min(review.quorum, N-1)`.
- **Board**: the typed snapshot of the milestone's issues, PRs, coordination comments, and run records that `rules.py` evaluates.
- **Brief**: the rendered `.meld-brief.md` a worker reads first.
