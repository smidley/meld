# Review: claude-vibe-squad, and what Meld borrows from it

Date: 2026-09-09. Source: https://github.com/mtarcure/claude-vibe-squad ("Vibe Squad" v1.1.6, MIT, Copyright (c) 2026
Matthew A.; a few files and all `plugins/` are AGPL and listed in its `THIRD_PARTY.md`). Reviewed from a shallow clone;
Scott agreed the same day to borrow the ideas below, not the code. Companion to `2026-09-03-prior-art-survey.md`.

## What it is, in Meld terms

One local coordinator ("Chrono", a Claude Code session in tmux) turns a goal into Markdown task packets and dispatches
each to a fresh native CLI process (`codex`, `claude`, Antigravity's `agy` for Gemini, `grok`, `kimi`) in its own git
worktree; a supervisor validates the worker's result artifact and completion envelope and publishes them; a registry
reconciler settles the task. 71 specialist role briefs and 65 skills are Markdown; routing is a TSV of specialist →
primary/backup/escalation/review lane with author-family anti-affinity. State lives in files inside the repo, not on
GitHub. About 150k lines of Python plus shell, launchd agents, and an optional daemon. Its own changelog describes
v1.1.6 as a "release-hardening" release about gates that reported success while broken.

Meld differs in kind: N peer groups coordinating through GitHub (issues as claim locks, a coordination issue with the
`[party] VERB` grammar, a review quorum with author and last-committer rules, `CHALLENGE`/`AUDIT`/`DRIFT`, a human
console, GitHub App identities). Nothing in Vibe Squad's runtime transplants. The rules below cover gaps Meld has.

## Borrow as spec §21 items (record during plan 5b Task 13; write each as deferred work, not shipped behaviour)

1. **Write scope enforced at merge, plus a deletion gate.** A task declares the paths it may change; `merge` refuses a
   PR whose diff touches paths outside that list, and refuses file deletions unless an exact pre-approved list allows
   them. No rule in `rules.py` reads a PR's changed files today (grep, 2026-09-09). Vibe Squad enforces `write_scope`
   when changes are integrated, not while the worker runs, and says so plainly. Natural Meld shape: the orchestrator
   writes `scope:` paths into the issue (or `meld tag --scope`), `board` exposes the PR file list, `_merge` in
   `rules.py` enforces, `meld review` warns early.
2. **Held categories denied at dispatch.** A fixed list of consequential action categories (Vibe Squad: `cleanup`,
   `credential_change`, `delete`, `live_outreach`, `paid_media`, `production_mutation`, `public_release`,
   `offensive_execution`, `malware_detonation`) that ordinary workers never receive authority for; the human grants it
   per task. Meld has `NEEDS-HUMAN` after the fact but nothing that withholds authority up front. Natural shape:
   `meld:held:<category>` labels the orchestrator applies; `_dispatch` refuses until a human `PROVIDED`/`APPROVE` on
   that issue; the worker brief names the categories as forbidden.
3. **Memory lifecycle instead of edits.** Every note carries a date and a source; a wrong or stale note is never edited
   or deleted — a corrected note is written and the old one marked `superseded` or `invalidated` with a link to its
   replacement; recall shows the status. Meld's lessons are append-only and decisions have no supersession
   (`memory.py`, 2026-09-09). Also adopt the recall prompt line: "memory primes hypotheses; it does not prove current
   state — read the file, grep the symbol, run the command before acting on it."
4. **Three-outcome probes in `meld doctor`.** PASS / FAIL / NOT MEASURED, where "could not check" is never reported as
   ok, and a probe proves its own oracle works (a positive control) before it may pass. Vibe Squad's `bin/canary.sh`
   header is the reference: five capabilities that were green in tests and doctor and dead in practice.
5. **Worker contract details.** (a) a `needs_tool` report field in `.meld-result.json` when a tool the brief declared
   fails (tool name as declared, literal invocation, verbatim error), separate from `outcome`; (b) the two-blocker stop
   rule: two consecutive failures on one objective mean stop and report with both verbatim errors, never a third blind
   variant; (c) take-over-resume for the retry path that reuses a worktree as-is: diff from the recorded anchor AND read
   untracked (`??`) files before planning; a human edit is intent, never revert it to restore the plan.
6. **Candid security statement** in README and spec §18: workers run on the host with the user's permissions inside a
   worktree; scopes, the scrubbed environment, and claim locks are an autonomy tool, not a containment boundary.

Items 1–3 are the candidate scope of a small plan 7.

## Borrow as content (optional, plan 7 or later)

7. **Starter skills.** Vibe Squad's skills use exactly Meld's plan-5b layout (`<name>/SKILL.md` with `name` and
   `description` frontmatter, mirrored into `.claude/skills` and `.agents/skills`). Candidates to copy under MIT with a
   Meld `THIRD_PARTY.md` notice, after removing Chrono-specific references: `code-review-loop`, `differential-review`,
   `claim-verification`, `interface-ambiguity-check`, `scope-decomposition`, `take-over-resume`,
   `skill-description-trigger-authoring`, `vibecheck`. Do not copy `defensive-pattern-discovery` (AGPL) or anything under
   `plugins/` (AGPL). Adopt their description convention for `.meld/skills` authors: a description is an activation
   trigger ("Use when about to X"), not a summary.

## Reference only (no action planned)

- Headless launch shapes seen in `bin/board-supervisor.sh`, for a possible third adapter later. Gemini via Antigravity:
  `agy <lane args> --add-dir <worktree> --output-format text --print <prompt>`. Kimi: `kimi --agent-file <yaml>
  --add-dir <worktree> --skills-dir <worktree>/.agents/skills --print --output-format stream-json -p <prompt>`. Grok:
  `grok --agent <yaml> --output-format streaming-json -p <prompt>` (Grok needs an xAI API key; the others use logins).
- Their review-trigger rubric (blast radius, adversarial claim, deciding measurement, architecture) decides when a second,
  cross-family review is mandatory. Meld already reviews every PR with a quorum; at most this informs when the
  orchestrator tags the extra `security` reviewer.
- Their "one fact, one home" rule (a copied fact either replaces the original or names which copy wins and what keeps
  them in agreement) is already how `AGENTS.md`/`CLAUDE.md`/`docs/HANDOFF.md` are meant to relate; keep it that way.
