# Meld

Multi-AI coordination: N independent AI coding systems (Claude Code, Codex CLI, ...) work on one GitHub repo as separate groups, negotiate on a coordination issue, claim work via issue assignment, review and modify each other's PRs, and answer to one human.

Design: `docs/superpowers/specs/2026-09-03-meld-design.md`. Plans: `docs/superpowers/plans/`.

## Develop

    uv sync
    uv run pytest
    uv run meld --help

## Verbs

| Command | What it does |
|---|---|
| `meld status` | Board as JSON plus the legal actions for the calling party |
| `meld wait [--timeout S] [--interval S] [--filter KINDS]` | Block until the board changes, then print status with a `changes` list; `--filter` limits which change kinds count |
| `meld claim N` | Assign this party to a ready issue (first assignment wins) |
| `meld release N [--force]` | Return an issue to ready; `--force` is human-only |
| `meld post VERB [REF] [-m TEXT] [--joint]` | Post a coordination comment in the header grammar |
| `meld challenge <open|evidence|retract|uphold|dismiss> REF -m TEXT` | Open, answer, or close a `CHALLENGE`; applies or clears `meld:challenged` under the closer rules; `meld post` refuses these verbs |
| `meld models [--adapter X]` | Models and efforts an adapter accepts |
| `meld dispatch N [--kind review|audit] [--worker security]` | Spawn a headless worker in its own worktree: a claimed issue by default, a PR for `review`/`audit`; `--worker security` dispatches the sequential security reviewer once your first review is submitted |
| `meld tick` | PM housekeeping: kill overdue workers, create missing leases, release other groups' expired leases, move red-CI issues back to in-progress once per head, flag strangers' comments as `IGNORED-COMMENT`, report off-protocol label/assignee changes as `PROTOCOL-VIOLATION`, and send Slack nudges for needs-human items |
| `meld collect N` | Finish a worker run: push and open the PR, or mark the issue blocked or needs-human; a run whose claim another group has since taken is recorded as superseded |
| `meld review N` | Submit a collected reviewer verdict as a GitHub review plus a `meld/review/<group>` commit status; push-fix pushes the fix and asks the author group to re-review the new head; a verdict on a moved head is marked stale. On your own group's PR (after another group's push-fix) the verdict goes out as a comment review — GitHub refuses an approve from the PR author's account — and the `success` status is what counts toward the quorum |
| `meld merge N` | Squash-merge a PR once the quorum (GitHub approvals on the head, plus the author group's `meld/review/<group>` `success` status), green checks, and no open challenge all hold, guarded by the head sha; an already-merged PR is a no-op success |
| `meld audit N` | Post a collected auditor's `AUDIT #<pr> PASS|FAIL` verdict on the coordination issue; a `FAIL` makes `meld reopen` legal for that group |
| `meld reopen N` | Return a closed issue to `ready` after your group's own `AUDIT ... FAIL`, quoting the findings |
| `meld reap N` | Remove a finished run's worktree and archive its record |
| `meld tag N [--worker W] [--model M] [--effort E] [--size S]` | Set the worker, model, effort, and size labels on an issue |
| `meld decide REF` | Record a verified `[joint] DECISION` as `.meld/decisions/NNNN-<slug>.md` through a memory PR |
| `meld remember REF -m TEXT` | Append a lesson about a resolved issue or PR to `.meld/memory/lessons.md` through a memory PR |
| `meld recall QUERY [--limit N]` | Search decisions, lessons, and past coordination issues |
| `meld plan issues REF [--dry-run]` \| `approve` \| `ack REF` | The plan gate: the proposer orchestrator creates `meld:planned` issues from a verified `[joint] PLAN`/`PLAN-ADD` (`--dry-run` reports what it would create); the human's `approve` adds `meld:plan-approved` and flips every planned issue to ready; the human's `ack REF` does the same for one `PLAN-ADD` |
| `meld inbox` | What needs a person: needs-human items, budget alerts, disagreements, plan additions, open asks |
| `meld chat [--once TEXT] [--verbose]` | A REPL that posts `[human] ASK` for plain text and the human verbs for slash commands; `--once` scripts one line and exits (non-zero when that line failed) |
| `meld notify [--dry-run]` | Send the Slack nudge for needs-human items now (a PM's `tick` also does this on its own) |
| `meld roster` | List the worker types available to the calling party |
| `meld run GROUP [--once]` | Supervise a group's orchestrator and PM as headless CLI sessions |

## Running a group

    MELD_TOKEN_CLAUDE=... uv run meld run claude

`meld run` starts the group's orchestrator and PM as headless CLI sessions with the packaged role prompts, relaunches them when they exit (at once after a clean exit, since a `-p` session ends whenever the model finishes its turn; with exponential backoff after a crash), recycles them every `runners.restart_hours`, and writes their logs under `$MELD_HOME/logs/<group>/`. Each launch tells the role its launch number and reason, so only the first launch announces `CONFIG` and `ROSTER` and only a crash or recycle posts `RESUMED`. Workers are separate processes started by the PM through `meld dispatch`; they run in `$MELD_HOME/worktrees/<group>/`, commit as the group's bot, receive no GitHub or git credentials (no token, an empty `GH_CONFIG_DIR`, credential helpers disabled), and end by writing `.meld-result.json`, which `meld collect` turns into a pushed branch and a pull request.

Real-CLI smoke tests (they spend tokens): `task test:smoke`.

## Memory

Agreed decisions become `.meld/decisions/NNNN-<id>.md` (`meld decide`), lessons append to `.meld/memory/lessons.md` (`meld remember`), both through memory PRs that the other groups' PMs approve with `meld review N` — a decision file must be byte-identical to `meld decide`'s rendering of the verified `[joint] DECISION`, a lesson PR must append exactly one entry to the existing file — no reviewer worker runs, and a merged memory PR is never audited. Once a `[joint]` comment is on a ref the round is closed; a `DISAGREE` round is never recorded. `meld recall QUERY` searches both plus past coordination issues, and dispatch renders matching snippets into every brief as `## Relevant memory`.

## The human console

`meld inbox` lists what needs a person; `meld chat` is a REPL: plain text posts `[human] ASK ask-<n>`, slash commands post the human verbs (`/halt`, `/resume`, `/directive`, `/provided`, `/resolved`, `/revert`, `/ack`, `/approve`, `/challenge`), `/wait` prints new coordination comments. With `[notify] slack_dm_webhook_env` set, each PM's `tick` sends one Slack message listing every due needs-human issue and repeats an issue only after `repeat_after_minutes`.

## Product standards

Put the product's quality bar in `.meld/STANDARDS.md`; it is rendered into every worker brief and both role prompts. Without one, Meld's default applies: visual design, performance, and security are acceptance criteria with the same weight as correctness; agents decide with industry standards and research and surface only approval-worthy decisions; installed skills are used when they match the task.

## Exit codes

`0` success. `1` an error (bad config, a GitHub failure, an unusable board). `2` refused by the rules, or a usage error. A rules refusal prints its reason as JSON:

    {"refused": "claim #12 refused: issue is in_progress, not ready", "verb": "claim", "target": "#12",
     "rule": "issue is in_progress, not ready", "violation_posted": true}

`violation_posted` is `true` when the refusal was announced on the coordination issue as `PROTOCOL-VIOLATION`, `false` when that post was attempted and failed, and `null` when none was attempted — a human's refusal, or a lost claim race, which is first-assignment-wins working as designed rather than an illegal call.

The calling party comes from `MELD_GROUP` and `MELD_ROLE` (`orchestrator` or `pm`); unset means human, authenticated as `gh`'s current user. Group tokens come from the `token_env` named in `.meld.toml`; they are fine-grained PATs scoped to the product repo with Contents, Issues, Pull requests, and Commit statuses (read and write). Local state lives under `MELD_HOME` (default `~/.meld`) and can be deleted at any time.

## Known issue: macOS hidden `.pth` files

uv marks its `.pth` files hidden on macOS, and Homebrew Python 3.13/3.14 skip hidden `.pth` files, so `import meld` can fail under `uv run` even though `uv sync` succeeded. Tests are unaffected (pytest is pointed at `src/` directly), and `task` sets `PYTHONPATH=src`. For a direct `uv run meld ...`, run:

    chflags nohidden .venv/lib/python3.13/site-packages/_editable_impl_meld.pth
