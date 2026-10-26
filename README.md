# Meld

Multi-AI coordination: N independent AI coding systems (Claude Code, Codex CLI, ...) work on one GitHub repo as separate groups, negotiate on a coordination issue, claim work with a `meld:claimed-by:<group>` label, review and modify each other's PRs, and answer to one human.

Design: `docs/superpowers/specs/2026-09-03-meld-design.md`. Plans: `docs/superpowers/plans/`.

## Develop

    uv sync
    uv run pytest
    uv run meld --help

## Verbs

| Command | What it does |
|---|---|
| `meld status` | Board as JSON plus, per issue and PR, the `legal` verbs for the calling party and a `refused` map naming why each other verb is not legal right now |
| `meld wait [--timeout S] [--interval S] [--filter KINDS]` | Block until the board changes, then print status with a `changes` list; `--filter` limits which change kinds count |
| `meld claim N` | Claim a ready issue by labelling it `meld:claimed-by:<group>` (a human claims as `meld:claimed-by:human`); the first label wins, and a lost race removes only its own label |
| `meld release N [--force]` | Return an issue to ready, removing every `meld:claimed-by:*` label on it; `--force` is human-only |
| `meld post VERB [REF] [-m TEXT] [--joint]` | Post a coordination comment in the header grammar |
| `meld challenge <open|evidence|retract|uphold|dismiss> REF -m TEXT` | Open, answer, or close a `CHALLENGE`; applies or clears `meld:challenged` under the closer rules; `meld post` refuses these verbs |
| `meld models [--adapter X]` | Models and efforts an adapter accepts |
| `meld dispatch N [--kind review|audit] [--worker security]` | Spawn a headless worker in its own worktree: a claimed issue by default, a PR for `review`/`audit`; `--worker security` dispatches the sequential security reviewer once your first review is submitted |
| `meld tick` | PM housekeeping: kill overdue workers, create missing leases, release other groups' expired leases (but not one that lapsed before the human answered — that issue was waiting for a person, not stalled), swap `meld:needs-human` back once a listed human has answered the ask, move red-CI issues back to in-progress once per head, send an issue whose PR a listed human pushed to from in-progress to in-review with a `REVIEW-REQUEST` for that head (once per head, so a person's fix is reviewed rather than built over), flag strangers' comments as `IGNORED-COMMENT`, report off-protocol label/assignee changes as `PROTOCOL-VIOLATION`, and send Slack nudges for needs-human items |
| `meld collect N` | Finish a worker run: push and open the PR, or mark the issue blocked or needs-human; a run whose claim another group has since taken is recorded as superseded |
| `meld review N` | Submit a collected reviewer verdict as a GitHub review plus a `meld/review/<group>` commit status; push-fix pushes the fix and asks the author group to re-review the new head; a verdict on a moved head is marked stale. On your own group's PR (after another group's push-fix) the verdict goes out as a comment review — GitHub refuses an approve from the PR author's account — and the `success` status is what counts toward the quorum |
| `meld merge N` | Squash-merge a PR once the quorum (GitHub approvals on the head, plus the author group's `meld/review/<group>` `success` status), green checks, and no open challenge all hold, guarded by the head sha; an already-merged PR is a no-op success |
| `meld audit N` | Post a collected auditor's `AUDIT #<pr> PASS|FAIL` verdict on the coordination issue; a `FAIL` makes `meld reopen` legal for that group |
| `meld reopen N` | Return a closed issue to `ready` after your group's own `AUDIT ... FAIL`, quoting the findings; the FAIL must be newer than the issue's last close, so work closed since it was posted is audited before it can be reopened |
| `meld reap N` | Remove a finished run's worktree and archive its record |
| `meld tag N [--worker W] [--model M] [--effort E] [--size S] [--skill NAME]... [--scope GLOB]... [--may-delete PATH]... [--held CATEGORY]...` | Set the worker, model, effort, size, and `skill:` labels on an issue, and write its declarations: `--scope`/`--may-delete` replace the body's `## Scope` and `## May delete` sections (one single-line path per option), `--held` asks the human to authorize the category and then adds `meld:held:<category>` plus `meld:needs-human` |
| `meld decide REF [--supersedes FILE]` | Record a verified `[joint] DECISION` as `.meld/decisions/NNNN-<slug>.md` through a memory PR; `--supersedes NNNN-<slug>.md` retires that earlier decision in the same PR |
| `meld remember REF -m TEXT [--supersedes HEADING]` | Append a lesson about a resolved issue or PR to `.meld/memory/lessons.md` through a memory PR; `--supersedes "<heading>"` retires the earlier entry with that `## ` heading |
| `meld recall QUERY [--limit N]` | Search decisions, lessons, and past coordination issues |
| `meld plan issues REF [--dry-run]` \| `approve` \| `ack REF` | The plan gate: the proposer orchestrator creates `meld:planned` issues from a verified `[joint] PLAN`/`PLAN-ADD` (`--dry-run` reports what it would create); the human's `approve` adds `meld:plan-approved` and flips every planned issue to ready; the human's `ack REF` does the same for one `PLAN-ADD` |
| `meld inbox` | What needs a person: needs-human items, budget alerts, disagreements, plan additions, open asks |
| `meld chat [--once TEXT] [--verbose]` | A REPL that posts `[human] ASK` for plain text and the human verbs for slash commands; `--once` scripts one line and exits (non-zero when that line failed) |
| `meld notify [--dry-run]` | Send the Slack nudge for needs-human items now (a PM's `tick` also does this on its own) |
| `meld roster` | List the worker types available to the calling party |
| `meld run GROUP [--once]` | Supervise a group's orchestrator and PM as headless CLI sessions |
| `meld tail [TARGET] [--run N] [--follow/--no-follow] [--width W] [--interval S]` | Render a run's transcript as it is written; TARGET is `<group>` or `<group>/<role-or-kind>`, defaulting to the newest log — reads local files only, nothing posted |
| `meld watch [--port N] [--open/--no-open]` | Serve the local viewer on `127.0.0.1` and print its URL (a per-launch token is in it): the board, each supervisor's liveness (`alive`/`dead`/`unknown`, with the reason), each role's standing freeze, budget, free disk and log bytes, and every live transcript in its own column, all streamed over one connection — plus a configuration form editing `.meld.toml` itself: the budgets, concurrencies, models, efforts, timeouts and log caps; a group's identity is shown and not editable; saving rewrites the file canonically and drops any comments. From the page, as the human, you can also run `claim`, `release`, `post`, `challenge`, `decide`, `remember`, `plan approve`, `plan ack`, and `merge` (`decide` and `remember` are refused for a human today — `rules.decide_rule`/`remember_rule` are both a group's or the PM's — so no button for either ever renders, and the API answers the refusal itself) — `rules.py` decides each one exactly as it does on the CLI, and a refusal is shown with its rule. Reads local files and the board; posts only what you run from the page, as the human party; Ctrl-C stops it |
| `meld setup REPO --goal TEXT [--pr] [--app] [--allow-public] [--no-protection] [--force] [--milestone T] [--dir P]` | Bootstrap a product repo: labels, milestone, coordination issue, CODEOWNERS, `.meld/STANDARDS.md`, branch protection, and a starter `.meld.toml`; `--pr` opens the setup PR, `--app` writes the GitHub App form and prints the steps |
| `meld doctor [--probe-status] [--sweep [--dry-run] [--older-than-days N]]` | Verify CLIs, credentials, identities, models, ceilings, and permissions (exit 1 on a failure); `--sweep` clears meld's scratch under `$MELD_HOME` |

## Running a group

    MELD_TOKEN_CLAUDE=... uv run meld run claude

`meld run` starts the group's orchestrator and PM as headless CLI sessions with the packaged role prompts, relaunches them when they exit (at once after a clean exit, since a `-p` session ends whenever the model finishes its turn; with exponential backoff after a crash), recycles them every `runners.restart_hours`, and writes their logs under `$MELD_HOME/logs/<group>/`. Each launch tells the role its launch number and reason, so only the first launch announces `CONFIG` and `ROSTER` and only a crash or recycle posts `RESUMED`. Workers are separate processes started by the PM through `meld dispatch`; they run in `$MELD_HOME/worktrees/<group>/`, commit as the group's bot, receive no GitHub or git credentials (no token, an empty `GH_CONFIG_DIR`, credential helpers disabled), and end by writing `.meld-result.json`, which `meld collect` turns into a pushed branch and a pull request.

Real-CLI smoke tests (they spend tokens): `task test:smoke`.

**Watching a run.** `meld watch` (from the product clone, as the human) prints a `http://127.0.0.1:<port>/?t=<token>` URL; open it. Every board row's verb button fills the action bar with that verb and its ref; nothing runs until you press Run. A configuration panel edits `.meld.toml` the same way — change a value, then save; the first save rewrites the whole file canonically and does not keep its comments. A save from a panel left open while the file changed underneath it is refused rather than reverting that change, so reload the page and redo the edit; and a setting typed to 0, to a negative or to a fraction is refused by name, because most of them have a floor (the budgets, a lease's grace and the amendment cap are the ones where 0 means what it says). The token is minted per launch and required on every request, so paste the whole URL. Nothing in Meld depends on the viewer running, and `meld tail <group>/<role>` remains the terminal view for a headless box. On this Mac the active `gh` account flips between two logins, and `/api/state` answers 502 with a GitHub 404 when it is not the smidley one — run `GH_TOKEN=$(gh auth token --user smidley) meld watch` from the product clone.

## Memory

Agreed decisions become `.meld/decisions/NNNN-<id>.md` (`meld decide`), lessons append to `.meld/memory/lessons.md` (`meld remember`), both through memory PRs that the other groups' PMs approve with `meld review N` — a decision file must be byte-identical to `meld decide`'s rendering of the verified `[joint] DECISION`, a lesson PR must append exactly one entry to the existing file — no reviewer worker runs, and a merged memory PR is never audited. Once a `[joint]` comment is on a ref the round is closed; a `DISAGREE` round is never recorded. `meld recall QUERY` searches both plus past coordination issues — reading `origin`'s default branch after a fetch, so it never lags the local checkout — and dispatch renders matching snippets into every brief as `## Relevant memory`. Memory is superseded, never edited: `meld decide REF --supersedes NNNN-<slug>.md` opens a two-file PR that records the new decision (carrying a `- Supersedes:` line) and appends one `Status: superseded by ...` trailer to the old file, `meld remember REF -m TEXT --supersedes "<heading>"` names the retired lesson on the new entry's first line, and `meld recall` marks each hit `active` or `superseded` while briefs quote only what is still in force.

## Guardrails

Three things stop a worker from doing more than its task asked for. All three are declared by an orchestrator or a human and enforced by `meld`, never by the worker's own restraint.

**Write scope.** An issue body's `## Scope` section lists the globs the task may change, one `- ` bullet each (`fnmatch`, so `*` crosses `/` — `src/*.py` matches `src/a/b.py`, and `**` means nothing more than `*`; bullets inside a ``` fence are quoted, not declared). `meld merge` refuses a PR that changes anything else — `PR changes files outside #N's declared scope: ...` — and `meld status` shows `scope_violations` on the PR long before that. Scope is opt-in per issue: no `## Scope`, no check.

**Deletion gate.** A `## May delete` section lists exact paths, and the gate is always on: any other deletion by a PR that closes an issue is refused with `PR deletes files not listed under '## May delete' in #N: ...`. A rename is not a deletion. Memory PRs close no issue, so neither check reaches them.

**Held categories.** Seven consequential kinds of action — `cleanup`, `credential-change`, `delete`, `live-outreach`, `paid-media`, `production-mutation`, `public-release` — have a `meld:held:<category>` label each, created by `meld setup`. `meld tag N --held delete` posts `NEEDS-HUMAN #N` and then adds the label plus `meld:needs-human` — the ask first, and whoever runs it, a human included: the labels are the gate and the ask is the stamp an answer has to be newer than, so a hold with no ask behind it is one nobody can lift on purpose and any earlier answer lifts by accident (it is also how a person re-gates an issue they have already answered). Only a category the issue does not already carry is posted and labelled, so a re-tag never re-gates an answered issue. `meld dispatch` then refuses the issue — `held (delete): a listed human must post RESOLVED #N (or PROVIDED #N) first` — until a listed human answers, after which any PM's `meld tick` swaps `meld:needs-human` back to `meld:in-progress` or `meld:ready`. The held labels stay on the issue as the record of that authority. The worker brief lists the authorized categories under `## Authorized by a human`, and the worker contract forbids every held action that section does not name — so a worker is never handed authority a person did not grant.

Widening a scope, allowing a deletion, or authorizing a category is an edit to the issue (or a `meld tag` call) plus, for a held category, a human's answer. There is no merge override.

**What the guardrails are not.** A worker runs on this host as you, inside a git worktree, with your own permissions: it can read any file your account can read and run any command you can run. The scrubbed environment keeps GitHub credentials out of its hands, the scope and deletion checks decide what `meld merge` accepts, and the claim lock decides who may work an issue. They keep autonomous work on task; they are not a containment boundary. Run Meld on a machine, or under an account, whose contents you would hand to the model.

A fourth guardrail protects memory itself: a wrong or stale decision or lesson is superseded, never edited or deleted, so no brief can quote something already known to be wrong (see Memory above).

## The human console

`meld inbox` lists what needs a person; `meld chat` is a REPL: plain text posts `[human] ASK ask-<n>`, slash commands post the human verbs (`/halt`, `/resume`, `/directive`, `/provided`, `/resolved`, `/revert`, `/ack`, `/approve`, `/challenge`), `/wait` prints new coordination comments. With `[notify] slack_dm_webhook_env` set, each PM's `tick` sends one Slack message listing every due needs-human issue and repeats an issue only after `repeat_after_minutes`. The throttle is shared through a `meld:nudged` label on the issue, read back from GitHub's own label events, so it is one nudge per window however many PMs are running; the label is cleared once the issue stops needing a person.

## Product standards

Put the product's quality bar in `.meld/STANDARDS.md`; it is rendered into every worker brief and both role prompts. Without one, Meld's default applies: visual design, performance, and security are acceptance criteria with the same weight as correctness; agents decide with industry standards and research and surface only approval-worthy decisions; installed skills are used when they match the task.

## Skills

A product can ship its own skills as `.meld/skills/<name>/SKILL.md`, with `name` and `description` in the frontmatter. They join the roster next to the framework's built-in workers, the product's `.meld/workers/`, and whatever skills the group's CLI already has installed on the machine. A product skill overrides a same-named installed one; a `SKILL.md` whose name collides with a *worker* is dropped rather than replacing that worker's role prompt.

A brief lists only the skills that match the task: first whatever the orchestrator pinned with `meld tag N --skill <name>` (repeatable — it writes `skill:<name>` labels and refuses a name that is on neither the installed nor the product list), then every skill whose name or description shares a term with the issue's or PR's own title and body.

At dispatch, the product's skills are copied into the worktree under the directory the launching CLI looks in — `.claude/skills/` for Claude Code, `.agents/skills/` for Codex — and each copied skill is added to the clone's shared `info/exclude` by its own exact path. So each CLI finds them where it natively looks, nothing is written to your home directory, and a worker cannot commit the copies — while a project skill you or a worker write by hand in that same directory stays perfectly visible to git. A worker whose CLI cannot invoke a skill by name is told to open the `SKILL.md` at the path the brief prints.

## Doctor

    uv run meld doctor        # or: task doctor

Run it from a product clone. It checks that each configured CLI is installed and answers `--version`, that it is logged in (`cli-login:<group>` asks the CLI itself -- `claude auth status --json`, `codex login status` -- and reads its answer, because the first live run began with a logged-out CLI that the old checks could not see), and that the flags Meld's adapter passes are still listed by that CLI's `--help` (a renamed flag would otherwise make every launch exit at once; Claude Code's undocumented `--max-turns` is the one flag this cannot see); that each group's credentials resolve to the bot named in `.meld.toml` (a PAT's own login, or an App's slug plus a freshly minted installation token whose permissions and repository coverage it prints); that a git identity exists per bot; that every model named in `.meld.toml` exists in `models.toml` for that group's adapter; that each adapter's command-timeout ceiling exceeds `polling.wait_timeout_seconds`; that a PAT group can read issues, pull requests, and commit statuses; that `gh`'s own login is a listed human; and that `openssl` is on `PATH` (an App group needs it to sign its JWT). Exit code 1 when a check fails. Every check has three outcomes: it passed, it failed, or it was **not measured** -- a probe that could not run or could not read its answer -- and the summary lists the last kind under `not_measured`, apart from the warnings, so a green doctor says exactly what it has and has not verified.

Warnings do not fail the run: a `settings.json` `env` block that would hand a worker a token or a party identity, hidden `.pth` files (below), a memory store past 2 MB, a configured-but-unset Slack webhook, a non-HTTPS `origin`, an App group missing the Checks permission. Nothing in `meld doctor` depends on issue assignability any more, so a correctly configured App group has no warning it cannot fix.

`--probe-status` also proves **write** access to commit statuses, by posting one `meld/doctor` status on the default-branch head. It is opt-in because it leaves that mark.

`meld doctor --sweep` is the only thing doctor writes. It lists the scratch meld leaves under `$MELD_HOME` — consumed verdict archives, orphaned per-run `gh` and gitconfig directories, memory worktrees left behind by a crash, memory-review and CI-red records for PRs that have since merged or closed, and the dead per-party `notify.json` throttle files an older Meld wrote — and deletes exactly what it listed. `--sweep --dry-run` lists without deleting; `--older-than-days N` moves the age threshold (default 7).

## Exit codes

`0` success. `1` an error (bad config, a GitHub failure, an unusable board). `2` refused by the rules, or a usage error. A rules refusal prints its reason as JSON:

    {"refused": "claim #12 refused: issue is in_progress, not ready", "verb": "claim", "target": "#12",
     "rule": "issue is in_progress, not ready", "violation_posted": true}

`violation_posted` is `true` when the refusal was announced on the coordination issue as `PROTOCOL-VIOLATION`, `false` when that post was attempted and failed, and `null` when none was attempted — a human's refusal, or a lost claim race, which is first-label-wins working as designed rather than an illegal call.

The calling party comes from `MELD_GROUP` and `MELD_ROLE` (`orchestrator` or `pm`); unset means human, authenticated as `gh`'s current user. Group tokens come from the `token_env` named in `.meld.toml`; they are fine-grained PATs scoped to the product repo with Contents, Issues, Pull requests, and Commit statuses (read and write). Local state lives under `MELD_HOME` (default `~/.meld`) and can be deleted at any time.

## Quick start

Bootstrapping a fresh product repo, start to finish:

1. `uv sync`.
2. Create one GitHub machine account per group, add each as a collaborator on the product repo with push access (its bot pushes the `meld/` PR branches — spec 20 step 1, and the next steps `meld setup` prints say the same), and mint a fine-grained token for each (spec 18: Contents, Issues, Pull requests, Commit statuses — read and write); export `MELD_TOKEN_<GROUP>` (e.g. `MELD_TOKEN_CLAUDE`), injected from 1Password via `op run` rather than left in plaintext. Or, to avoid extra GitHub accounts, add `--app` to step 3's `meld setup owner/name --goal "..."` and follow its printed steps to create one GitHub App per group (App names are global on GitHub, so `meld-<group>` may be taken; `meld doctor` prints the bot login the App actually got). An App group claims and builds exactly like a PAT group: the claim lock is the `meld:claimed-by:<group>` label, not the issue assignee, so any bot user can hold it.
3. `cd <product clone> && meld setup owner/name --goal "..." --pr` — writes labels, the milestone, the coordination issue, `.github/CODEOWNERS`, branch protection, and a starter `.meld.toml`, then commits `.github/CODEOWNERS`, `.meld/STANDARDS.md`, and `.meld.toml` on a PR (labels, milestone, coordination issue, and branch protection are applied directly, not through the PR).
4. Merge the setup PR, then uncomment or adjust the `[groups.*]` blocks it wrote (bot name, models, efforts) for the CLIs you're running. A group's name becomes its claim label and a worktree directory, so it must match `[a-z0-9][a-z0-9_-]{0,31}` — lowercase, at most 32 characters — and cannot be `human` or `joint`.
5. `meld doctor` until it reports green.
6. `task run GROUP=claude` and `task run GROUP=codex`, one per group.
7. `meld chat`; when the plan appears, approve it with `/approve` (or `uv run meld plan approve`) — that adds `meld:plan-approved` *and* flips every planned issue to ready. Adding the label by hand does neither for the issues.
8. `meld watch`, and open the URL it prints, to watch the roles and workers as they run.

## Known issue: macOS hidden `.pth` files

uv marks its `.pth` files hidden on macOS, and Homebrew Python 3.13/3.14 skip hidden `.pth` files, so `import meld` can fail under `uv run` even though `uv sync` succeeded. Tests are unaffected (pytest is pointed at `src/` directly), and `task` sets `PYTHONPATH=src`. For a direct `uv run meld ...`, run:

    chflags nohidden .venv/lib/python3.13/site-packages/_editable_impl_meld.pth
