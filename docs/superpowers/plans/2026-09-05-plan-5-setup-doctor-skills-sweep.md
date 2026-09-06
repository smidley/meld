# Plan 5: `meld setup`, `meld doctor`, the fuller skills story, and the section-21 sweep

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a fresh product bootstrappable and self-diagnosing (`meld setup`, `meld doctor`), let workers actually use skills (relevance-matched, product-shared, materialised into each CLI's project skill directory), and close every item the spec's section 21 carried into plan 5.

**Architecture:** Two new leaf-ish modules (`setup.py`, `doctor.py`) compose existing pieces (`GitHub`, `Config`, adapters, `ModelRegistry`, `StateStore`) behind two new CLI commands; the skills story extends `roster.py`/`brief.py`/`dispatch.py`/the adapters with one new Protocol method; the sweep is a series of small, test-first edits to `rules.py`, `grammar.py`, `board.py`, `memory.py`, `review.py`, `housekeeping.py`, `notifier.py`, `chat.py`, `github.py`, `adapters/base.py`. `rules.py` remains the only legality authority: plan 5 moves `plan`/`decide`/`remember` legality into it.

**Tech Stack:** Python 3.13, click, stdlib only (tomllib, urllib, json, subprocess); `uv`; pytest with the hermetic fixtures in `tests/` (FakeGh, make_product, FakeAdapter). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-03-meld-design.md` — sections 5 (config), 10 (roster), 11 (leases), 13 (statuses), 14–15 (console, memory), 16 (adapters), 18 (security), 20 (bootstrap), and **21 "Carried into plan 5" (items 1–28)**, which is this plan's checklist.

## Global Constraints

- Python `>=3.12` (pinned 3.13 locally); runtime dependency `click>=8.1` only; tests hermetic: no network, no real `gh`, no real stdin, `GIT_CONFIG_GLOBAL=/dev/null`, the autouse env scrub stays in place.
- `rules.py` is the only legality authority for orchestrator/PM verbs; every refusal goes through `actions.refuse` with an exact `Refusal` string; existing refusal strings are preserved verbatim (tests assert them).
- Secrets never reach argv, logs, state files, result dicts, error messages, worker environments, or the repo; `meld doctor` prints permission *names*, never token values.
- Framework-only verbs (`LEASE`, `EXPIRED`, `IGNORED-COMMENT`, and plan 5's `RELEASED`) are written only by `dispatch`/`tick`/`claim`/`release`, never by `meld post`.
- Every state file under `$MELD_HOME` tolerates missing, corrupt, or wrong-shaped JSON (reads as empty) and its writes are contained (an `OSError` is reported, never raised out of `tick`).
- `tick` never raises because of a sweep, a webhook, or a state file.
- Conventional commit messages, no attribution trailers. Repo-local git identity is `Scott Brant <421573+smidley@users.noreply.github.com>`.
- Baseline at the plan's base commit 966a000: `uv run pytest -q` → 439 passed, no warnings. Each task leaves the suite green.

## Decisions (rulings made while writing the plan; recorded so executors do not re-litigate)

1. **Verified joint sticks (§21 item 27).** Once a ref has a verified `[joint]`, later `[joint]` comments on it are ignored by `agreement_for` and refused by `meld post --joint`. An unverified joint can still be superseded by the proposer's verified one.
2. **Leases (§21 item 4) are structurally tied to claims by a framework-only `RELEASED #n` post**: `release` posts it, `derive_leases` drops the lease on `RELEASED`, so a same-group re-claim can never revive an old lease. (The plan-2 note allowed "tie to run records *or* post on release"; run records are per machine and invisible to other groups, so posting is the structural fix.)
3. **Worker commit messages (§21 item 5)**: `merge` passes the PR body as the squash commit message, so GitHub never folds worker commit messages into the merge commit.
4. **ResourceWarnings (§21 item 6)**: `popen_detached` returns a `DetachedPopen` whose `__del__` is a no-op; the process is reaped by `pid_alive`'s `waitpid`, not by the object.
5. **Author-group request-changes (§21 item 7)** counts toward the ping-pong cap: the COMMENT review's first line carries a machine-readable marker (`meld-verdict: request-changes`), `assemble` reads it into `Review.state == "CHANGES_REQUESTED"` with `as_author=True`.
6. **Author-group verdicts in `legal` (§21 item 8)**: `legal_pr_verbs` reports `approve`/`request-changes` through `review_verdict_rule` (the path `meld review` actually takes), while `pr_refusal` stays strict for the memory-PR path.
7. **Open-PR ties (§21 item 9)**: `pr_for_issue` prefers, among open PRs, one authored by a current assignee of the issue, then the highest number.
8. **Milestone-end audit (§21 item 10)**: `audit_summary.due` is also true when every non-coordination issue in the milestone is closed and audits are pending.
9. **Notifications (§21 items 15, 24)**: the throttle becomes shared — a PM that nudges adds the label `meld:nudged` to the issue, and the last nudge time is read from the issue's `labeled` events by any configured bot, so one nudge per repeat window regardless of how many PMs (or humans) run `notify`. The per-party `notify.json` is removed.
10. **BUDGET items (§21 item 20)** carry a ref (`budget-<n>`); a human clears one with any non-ASK comment on that ref; a BUDGET without a ref keeps the old any-later-human behaviour.
11. **`_local_hits` cost (§21 item 13)** is closed by measurement, not by an index: `meld doctor` warns when `.meld/decisions` plus `lessons.md` exceed 2 MB; an index is not built until that fires in practice.
12. **`get_contents` over 1 MB (§21 item 23)** falls back to the blob endpoint when GitHub answers `encoding: "none"`.
13. **Lessons with `## ` lines (§21 item 28)**: `remember` escapes any line starting with `#` as `\#`, so the entry boundary is always the rendered `\n## <date>` heading.
14. **Skills (§21 item 3)**: a product may ship skills under `.meld/skills/<name>/SKILL.md` (roster source `product-skill`); briefs list only skills matched to the task (name/description term overlap) plus any the orchestrator pinned with `skill:<name>` labels (`meld tag --skill`); at dispatch, product skills are materialised into the worktree under each adapter's *project* skill directory (`.claude/skills/`, `.agents/skills/`), excluded from git through the worktree's `info/exclude`, so both CLIs discover them natively and nothing is written to the host's home directory. Codex workers are still told to open `SKILL.md` directly.
15. **`meld doctor` is read-only** except `--sweep`, which deletes only what it lists (and `--sweep --dry-run` lists without deleting). A write-permission probe for commit statuses is opt-in (`--probe-status`) because it leaves a `meld/doctor` status on the default branch head.
16. **`meld setup` is idempotent** (existing labels/milestone/coordination issue are reused) and writes repo files on a local branch; `--pr` pushes with the human's own git credentials and opens the PR. It refuses a public repo without `--allow-public`.
17. **§21 item 22 (housekeeping latency while the first group is down)** stays documented, not changed.

## File structure

| File | Responsibility (new or changed in plan 5) |
|---|---|
| `src/meld/rules.py` | + `decide_rule`, `plan_rule`, `remember_rule`, `legal_agreement_verbs`; `REMEMBER` in `ISSUE_VERBS`/`PR_VERBS`; `legal_pr_verbs` via `review_verdict_rule` |
| `src/meld/status.py` | agreements gain `legal`; `audit_summary` milestone-end trigger |
| `src/meld/grammar.py` | verified joint sticks |
| `src/meld/posting.py` | `RELEASED` framework-only; second-joint rule |
| `src/meld/board.py` | `MEMORY_MARKER_RE` (moved here), `Review.as_author`, `derive_leases` handles `RELEASED`, `pr_for_issue` tie-break, `NUDGED_LABEL` |
| `src/meld/assemble.py` | author-verdict COMMENT reviews → `CHANGES_REQUESTED` |
| `src/meld/actions.py` | `release` posts `RELEASED` |
| `src/meld/memory.py` | imports `MEMORY_MARKER_RE`; `recall` reads `origin/<default>`; heading escape; REVIEW-REQUEST re-post; uses `rules.decide_rule`/`remember_rule` |
| `src/meld/review.py` | `AUTHOR_VERDICT_MARKER`; `status_error` retry for memory PRs; `merge` passes the commit message |
| `src/meld/github.py` | blob fallback in `get_contents`; `merge_pull(..., message)`; `get_repo`, `branch_protection`, `viewer_scopes`-free permission probes |
| `src/meld/housekeeping.py` | ci-red pruning |
| `src/meld/notifier.py` | shared `meld:nudged` throttle |
| `src/meld/chat.py` | BUDGET refs |
| `src/meld/adapters/base.py` | `DetachedPopen`; `Adapter.project_skills_dir()`; `Adapter.doctor_checks()` |
| `src/meld/adapters/claude.py`, `codex.py` | the two new methods |
| `src/meld/roster.py` | product skills source |
| `src/meld/brief.py` | `select_skills`; `_local_hits(..., ref=)` |
| `src/meld/dispatch.py` | skill hints, materialisation, `recall` at `origin/<default>` |
| `src/meld/tagging.py`, `src/meld/plan.py` | `--skill`; rules delegation |
| `src/meld/doctor.py` (new) | checks + sweep |
| `src/meld/setup.py` (new) | bootstrap |
| `src/meld/cli.py`, `src/meld/prompts.py`, `src/meld/prompts/*.md`, `Taskfile.yml`, `README.md` | wiring and docs |
| `docs/superpowers/specs/2026-09-03-meld-design.md`, `docs/HANDOFF.md` | reconciliation |

---

### Task 1: `plan`/`decide`/`remember` legality lives in `rules.py`; `legal` arrays tell the truth for the author group

**Files:**
- Modify: `src/meld/rules.py`, `src/meld/status.py`, `src/meld/memory.py`, `src/meld/plan.py`
- Test: `tests/test_rules_agreements.py` (new), `tests/test_status.py`, `tests/test_rules_prs.py`

**Interfaces:**
- Consumes: `grammar.agreement_for`, `board.PullRequest.memory`, `rules.review_verdict_rule`, `rules.memory_review_rule`, `rules.pr_refusal`.
- Produces: `rules.decide_rule(board, cfg, actor, ref) -> str | None`; `rules.plan_rule(board, cfg, actor, action, ref) -> str | None` with `action in ("issues", "approve", "ack")`; `rules.remember_rule(board, cfg, actor, ref) -> str | None`; `rules.legal_agreement_verbs(board, cfg, actor, ref) -> list[str]` (subset of `["decide", "plan-issues", "plan-approve", "plan-ack"]`); `Verb.REMEMBER` in `ISSUE_VERBS` and `PR_VERBS`; `status.render_status` agreements gain `"legal"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_rules_agreements.py`:

```python
from meld.config import Party
from meld.rules import Actor, Verb, decide_rule, legal_agreement_verbs, legal_pr_verbs, plan_rule, remember_rule
from tests.factories import CFG2, mk_board, mk_comment, mk_issue, mk_pr

CLAUDE = Actor(Party("group", "claude"), "meld-claude-bot")
CLAUDE_PM = Actor(Party("pm", "claude"), "meld-claude-bot")
CODEX_PM = Actor(Party("pm", "codex"), "meld-codex-bot")
HUMAN = Actor(Party("human"), "empowerScott")


def decision_thread(joint_author="meld-claude-bot", verb="DECISION", qualifier=None):
    return [
        mk_comment(101, "meld-claude-bot", "[claude] PROPOSE dec-1\n\nUse Postgres.", "2026-09-03T10:00:00Z"),
        mk_comment(102, "meld-codex-bot", "[codex] AGREE dec-1\n\nyes", "2026-09-03T10:01:00Z"),
        mk_comment(103, joint_author, f"[joint] {verb} dec-1{' ' + qualifier if qualifier else ''}\n\nUse Postgres.\n\nverified-by: agreed", "2026-09-03T10:02:00Z"),
    ]


def test_decide_rule_admits_only_the_verified_proposer():
    board = mk_board(comments=decision_thread())
    assert decide_rule(board, CFG2, CLAUDE, "dec-1") is None
    assert decide_rule(board, CFG2, CLAUDE_PM, "dec-1") is None
    assert decide_rule(board, CFG2, CODEX_PM, "dec-1") == "only the proposer (claude) may record dec-1"
    assert decide_rule(board, CFG2, HUMAN, "dec-1") == "only the proposer group's orchestrator or PM may record a decision"
    forged = mk_board(comments=decision_thread(joint_author="meld-codex-bot"))
    assert decide_rule(forged, CFG2, CLAUDE, "dec-1") == "the [joint] DECISION dec-1 comment is not verified (wrong author or posted before the round closed)"
    assert decide_rule(mk_board(comments=[]), CFG2, CLAUDE, "dec-1") == "no verified [joint] DECISION dec-1 on the coordination issue"


def test_decide_rule_refuses_an_open_memory_pr_for_the_ref():
    pr = mk_pr(40, 0, head_sha="mem0001", author="meld-claude-bot")
    pr = pr.__class__(**{**pr.__dict__, "head_ref": "meld/memory-dec-1", "body": "meld-memory: decision dec-1 " + "a" * 64, "issue_number": None})
    board = mk_board(comments=decision_thread(), pulls={40: pr})
    assert decide_rule(board, CFG2, CLAUDE, "dec-1") == "dec-1 is already recorded or has an open memory PR"


def test_legal_agreement_verbs_lists_decide_for_the_proposer_only():
    board = mk_board(comments=decision_thread())
    assert legal_agreement_verbs(board, CFG2, CLAUDE, "dec-1") == ["decide"]
    assert legal_agreement_verbs(board, CFG2, CODEX_PM, "dec-1") == []


def test_plan_rule_matches_plan_py_refusals():
    thread = [
        mk_comment(201, "meld-claude-bot", "[claude] PROPOSE plan-3\n\n- id: a | title: A | worker: backend | model: claude-sonnet-5 | effort: medium | size: S", "2026-09-03T10:00:00Z"),
        mk_comment(202, "meld-codex-bot", "[codex] AGREE plan-3\n\nyes", "2026-09-03T10:01:00Z"),
        mk_comment(203, "meld-claude-bot", "[joint] PLAN plan-3\n\n- id: a | title: A | worker: backend | model: claude-sonnet-5 | effort: medium | size: S\n\nverified-by: agreed", "2026-09-03T10:02:00Z"),
    ]
    planning = mk_board(comments=thread, phase="planning")
    assert plan_rule(planning, CFG2, CLAUDE, "issues", "plan-3") is None
    assert plan_rule(planning, CFG2, CLAUDE_PM, "issues", "plan-3") == "only the proposer group's orchestrator may create plan issues"
    assert plan_rule(planning, CFG2, HUMAN, "approve", "plan-3") is None
    assert plan_rule(planning, CFG2, CLAUDE, "approve", "plan-3") == "only a human may approve the plan"
    executing = mk_board(comments=thread, phase="executing")
    assert plan_rule(executing, CFG2, CLAUDE, "issues", "plan-3") == "plan-3 was already approved; its issues can no longer be created"
    assert plan_rule(executing, CFG2, HUMAN, "approve", "plan-3") == "the plan is already approved (milestone is executing)"
    assert plan_rule(executing, CFG2, HUMAN, "ack", "plan-add-1") == "no verified [joint] PLAN-ADD plan-add-1 on the coordination issue"
    assert legal_agreement_verbs(planning, CFG2, HUMAN, "plan-3") == ["plan-approve"]
    assert legal_agreement_verbs(planning, CFG2, CLAUDE, "plan-3") == ["plan-issues"]


def test_remember_rule_needs_a_resolved_board_item():
    board = mk_board(issues={11: mk_issue(11, "ready"), 14: mk_issue(14, "needs_human")})
    assert remember_rule(board, CFG2, CLAUDE_PM, "#11") is None
    assert remember_rule(board, CFG2, CLAUDE_PM, "#14") == "#14 is still needs_human; a lesson is recorded once the item resolves"
    assert remember_rule(board, CFG2, CLAUDE_PM, "#99") == "#99 is not an issue or PR on the board"
    assert remember_rule(board, CFG2, HUMAN, "#11") == "only an orchestrator or PM may record a lesson"


def test_legal_pr_verbs_reports_the_author_groups_comment_review_path():
    pr = mk_pr(20, 13, committer="meld-codex-bot", author="meld-claude-bot")
    board = mk_board(pulls={20: pr}, issues={13: mk_issue(13, "in_review")})
    legal = legal_pr_verbs(board, CFG2, CLAUDE_PM, pr)
    assert "approve" in legal and "request-changes" in legal and "remember" in legal
    own_head = mk_pr(21, 12, committer="meld-claude-bot", author="meld-claude-bot")
    board2 = mk_board(pulls={21: own_head}, issues={12: mk_issue(12, "in_review")})
    assert "approve" not in legal_pr_verbs(board2, CFG2, CLAUDE_PM, own_head)
```

Append to `tests/test_status.py`:

```python
def test_status_agreements_carry_legal_actions(board1_gh, tmp_path):
    from meld.status import render_status
    from meld.rules import Actor
    from meld.config import Party
    from tests.factories import CFG2, REG, mk_board, mk_comment
    comments = [
        mk_comment(101, "meld-claude-bot", "[claude] PROPOSE dec-1\n\nUse Postgres.", "2026-09-03T10:00:00Z"),
        mk_comment(102, "meld-codex-bot", "[codex] AGREE dec-1\n\nyes", "2026-09-03T10:01:00Z"),
        mk_comment(103, "meld-claude-bot", "[joint] DECISION dec-1\n\nUse Postgres.\n\nverified-by: agreed", "2026-09-03T10:02:00Z"),
    ]
    data = render_status(mk_board(comments=comments), CFG2, Actor(Party("pm", "claude"), "meld-claude-bot"), REG)
    assert data["agreements"]["dec-1"]["legal"] == ["decide"]
```

Check `tests/factories.py` first: `mk_comment(id, author, body, created_at)` and `mk_board(**fields)` must exist with those shapes (they are used by `tests/test_rules_prs.py`); if `mk_board` lacks a `phase` or `pulls` keyword, add it there (it builds a `Board`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_rules_agreements.py tests/test_status.py -q`
Expected: FAIL with `ImportError: cannot import name 'decide_rule'`.

- [ ] **Step 3: Add the predicates to `rules.py`**

After `challenge_rule` (end of file), add:

```python
# ---- Agreement-ref verbs (plan 5, spec 21 item 18): the legality plan.py and memory.py used to compute inline ----------

def decide_rule(board: Board, cfg: Config, actor: Actor, ref: str) -> str | None:
    """Why `meld decide REF` is refused for `actor`, or None. Everything here is board-derived; memory.decide adds the
    one check that needs git (the decision file already on origin's default branch) with the same message."""
    if actor.party.kind not in ("group", "pm"):
        return "only the proposer group's orchestrator or PM may record a decision"
    agreement = agreement_for(board.comments, ref, cfg)
    if not agreement.joint_posted or agreement.joint_verb != "DECISION":
        return f"no verified [joint] DECISION {ref} on the coordination issue"
    if not agreement.joint_verified:
        return f"the [joint] DECISION {ref} comment is not verified (wrong author or posted before the round closed)"
    if agreement.status not in ("agreed", "majority"):
        return f"{ref} ended in DISAGREE; the human decides"
    if agreement.proposer != actor.group:
        return f"only the proposer ({agreement.proposer}) may record {ref}"
    if any(p.memory is not None and p.memory[:2] == ("decision", ref) for p in board.pulls.values() if p.state == "open"):
        return f"{ref} is already recorded or has an open memory PR"
    return None


def _verified_plan_joint(board: Board, cfg: Config, ref: str, verbs: tuple[str, ...]) -> str | None:
    agreement = agreement_for(board.comments, ref, cfg)
    if not agreement.joint_posted or agreement.joint_verb not in verbs:
        return f"no verified [joint] {'/'.join(verbs)} {ref} on the coordination issue"
    if not agreement.joint_verified:
        return f"the [joint] {agreement.joint_verb} {ref} comment is not verified (wrong author or posted before the round closed)"
    return None


def plan_rule(board: Board, cfg: Config, actor: Actor, action: str, ref: str) -> str | None:
    """Why `meld plan <action> REF` is refused, or None. `action` is issues, approve, or ack."""
    if action == "issues":
        if actor.party.kind != "group":
            return "only the proposer group's orchestrator may create plan issues"
        rule = _verified_plan_joint(board, cfg, ref, ("PLAN", "PLAN-ADD"))
        if rule:
            return rule
        agreement = agreement_for(board.comments, ref, cfg)
        if agreement.joint_verb == "PLAN" and board.phase != "planning":
            return f"{ref} was already approved; its issues can no longer be created"
        if agreement.proposer != actor.group:
            return f"only the proposer ({agreement.proposer}) may create the issues for {ref}"
        return None
    if action == "approve":
        if not actor.is_human:
            return "only a human may approve the plan"
        if board.phase != "planning":
            return "the plan is already approved (milestone is executing)"
        return _verified_plan_joint(board, cfg, ref, ("PLAN",))
    if action == "ack":
        if not actor.is_human:
            return "only a human may ack a plan addition"
        return _verified_plan_joint(board, cfg, ref, ("PLAN-ADD",))
    return f"{action} is not a plan action"


def remember_rule(board: Board, cfg: Config, actor: Actor, ref: str) -> str | None:
    if actor.party.kind not in ("group", "pm"):
        return "only an orchestrator or PM may record a lesson"
    n = int(ref[1:]) if ref.startswith("#") and ref[1:].isdigit() else None
    issue = board.issues.get(n) if n is not None else None
    pr = board.pulls.get(n) if n is not None else None
    if issue is None and pr is None:
        return f"{ref} is not an issue or PR on the board"
    if issue is not None and issue.status in ("blocked", "needs_human"):
        return f"{ref} is still {issue.status}; a lesson is recorded once the item resolves"
    if board.is_challenged(ref):
        return f"{ref} has an open CHALLENGE; resolve it first"
    return None


def legal_agreement_verbs(board: Board, cfg: Config, actor: Actor, ref: str) -> list[str]:
    """The agreement-ref actions `actor` may take on `ref` right now, in the CLI's spelling."""
    out = []
    if decide_rule(board, cfg, actor, ref) is None:
        out.append("decide")
    if plan_rule(board, cfg, actor, "issues", ref) is None:
        out.append("plan-issues")
    if ref == f"plan-{board.milestone}" and plan_rule(board, cfg, actor, "approve", ref) is None:
        out.append("plan-approve")
    if plan_rule(board, cfg, actor, "ack", ref) is None:
        out.append("plan-ack")
    return out
```

Then wire `REMEMBER` into the tables: change `ISSUE_VERBS`/`PR_VERBS` to end with `Verb.REAP, Verb.REMEMBER`; in `issue_refusal`'s dispatch add `elif verb == Verb.REMEMBER: rule = remember_rule(board, cfg, actor, f"#{issue.number}")` (before the default branch); in `pr_refusal` add, **before** the `elif pr.state != "open"` branch, `elif verb == Verb.REMEMBER: rule = remember_rule(board, cfg, actor, f"#{pr.number}")` (a lesson about a merged PR is legal). Replace `legal_pr_verbs`:

```python
def legal_pr_verbs(board: Board, cfg: Config, actor: Actor, pr: PullRequest) -> list[str]:
    """What `actor` may do to `pr` now. approve/request-changes are reported through the path `meld review` really
    takes: the author group's verdict is legal as a COMMENT review (review_verdict_rule), and a memory PR's verdict
    follows memory_review_rule -- so the array never says approve is illegal at the moment `meld review` would post one."""
    out = []
    for v in PR_VERBS:
        if v in (Verb.APPROVE, Verb.REQUEST_CHANGES):
            verdict = "approve" if v == Verb.APPROVE else "request-changes"
            rule = memory_review_rule(board, cfg, actor, pr) if pr.memory else review_verdict_rule(board, cfg, actor, pr, verdict)
        else:
            refusal = pr_refusal(board, cfg, actor, v, pr)
            rule = None if refusal is None else refusal.rule
        if rule is None:
            out.append(v.value)
    return out
```

`review_verdict_rule` and `memory_review_rule` are defined later in the file than `legal_pr_verbs`; Python resolves them at call time, so no reordering is needed.

- [ ] **Step 4: Make `status.render_status` expose `legal` on agreements**

In `src/meld/status.py`, find the dict comprehension that builds `agreements` (`{ref: {"status": ..., "joint_verified": ...}}`) and add `"legal": legal_agreement_verbs(board, cfg, actor, ref)` to each entry; import `legal_agreement_verbs` from `meld.rules`.

- [ ] **Step 5: Delegate `memory.decide`, `memory.remember`, and `plan.*` to the predicates**

In `memory.decide`, replace the five inline checks (party kind, joint posted/verb, verified, status, proposer) and the `_memory_pr_open` half of `_already_recorded` with:

```python
    rule = decide_rule(board, ctx.cfg, actor, ref)
    if rule:
        refuse(ctx, board, Refusal(Verb.DECIDE, ref, rule))
    joint = verified_joint(board.comments, ref, ctx.cfg)
    assert joint is not None  # decide_rule verified it
    agreement = agreement_for(board.comments, ref, ctx.cfg)
    slug = slugify(ref)
    if any(p.endswith(f"-{slug}.md") for p in _default_branch_files(ctx, DECISIONS_DIR)):
        refuse(ctx, board, Refusal(Verb.DECIDE, ref, f"{ref} is already recorded or has an open memory PR"))
```

and delete `_already_recorded` (keep `_memory_pr_open` only if something else uses it; otherwise delete it too). In `memory.remember`, replace the four inline checks after the blank-text `DispatchError` with `rule = remember_rule(board, ctx.cfg, actor, ref); if rule: refuse(ctx, board, Refusal(Verb.REMEMBER, ref, rule))`, keeping the `n`/`issue`/`pr` lookups that the rendering below needs. In `plan.py`, replace `_verified_joint` and the inline actor/phase/proposer checks in `plan_issues`, `plan_approve`, `plan_ack` with `rule = plan_rule(board, ctx.cfg, actor, "<action>", ref); if rule: refuse(ctx, board, Refusal(Verb.PLAN, ref, rule))` (for `plan_approve` the ref is `f"plan-{board.milestone}"`, which is what `_verified_joint` was called with — read the current code to confirm and keep it). Existing tests in `tests/test_plan.py` and `tests/test_memory.py` assert the refusal strings; they must keep passing unchanged.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: all green (439 + the new tests); `tests/test_rules_prs.py` may need one assertion updated if it asserted that `remember` is absent from a `legal` list — check the diff of failures, and only adjust assertions that encoded the old omission.

- [ ] **Step 7: Commit**

```bash
git add src/meld/rules.py src/meld/status.py src/meld/memory.py src/meld/plan.py tests/test_rules_agreements.py tests/test_status.py tests/test_rules_prs.py
git commit -m "feat: plan, decide, and remember legality live in rules.py and show up in meld status"
```

### Task 2: A verified `[joint]` sticks; `RELEASED` ends a lease; open-PR ties; the author group's request-changes counts

**Files:**
- Modify: `src/meld/grammar.py`, `src/meld/posting.py`, `src/meld/board.py`, `src/meld/assemble.py`, `src/meld/actions.py`, `src/meld/review.py`, `src/meld/rules.py`
- Test: `tests/test_agreement.py`, `tests/test_posting.py`, `tests/test_board.py`, `tests/test_review.py`, `tests/test_actions.py` (or wherever `release` is tested — grep `def test_release`)

**Interfaces:**
- Consumes: `grammar.agreement_for`, `board.derive_leases`, `board.Review`, `assemble._pull`, `actions.release`, `review.review`'s `as_author` body.
- Produces: `board.Review.as_author: bool = False`; `review.AUTHOR_VERDICT_MARKER = "meld-verdict:"`; `posting.FRAMEWORK_ONLY_VERBS` gains `"RELEASED"`; `grammar.PM_VERBS`/`ORCHESTRATOR_VERBS` accept `RELEASED` (framework-only at post time); `Board.pr_for_issue` tie-break.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agreement.py` (uses the existing helpers in that file; if it builds comments with a local helper, reuse it, else use `tests.factories.mk_comment`):

```python
def test_a_later_unverified_joint_does_not_displace_a_verified_one():
    from tests.factories import CFG2, mk_comment
    from meld.grammar import agreement_for
    comments = [
        mk_comment(1, "meld-claude-bot", "[claude] PROPOSE dec-1\n\nUse Postgres.", "2026-09-03T10:00:00Z"),
        mk_comment(2, "meld-codex-bot", "[codex] AGREE dec-1\n\nyes", "2026-09-03T10:01:00Z"),
        mk_comment(3, "meld-claude-bot", "[joint] DECISION dec-1\n\nUse Postgres.\n\nverified-by: agreed", "2026-09-03T10:02:00Z"),
        mk_comment(4, "meld-codex-bot", "[joint] DECISION dec-1\n\nUse MySQL.\n\nverified-by: forged", "2026-09-03T10:03:00Z"),
    ]
    a = agreement_for(comments, "dec-1", CFG2)
    assert a.joint_verified and a.joint_author == "meld-claude-bot" and a.joint_comment_id == 3
```

Append to `tests/test_board.py`:

```python
def test_released_ends_a_lease_and_a_reclaim_does_not_revive_it():
    from tests.factories import mk_comment
    from meld.board import derive_leases
    comments = [
        mk_comment(1, "meld-claude-bot", "[claude:pm] LEASE #11\n\nuntil: 2026-09-03T12:00:00Z\nrun: claude/#11\nkind: build", "2026-09-03T10:00:00Z"),
        mk_comment(2, "meld-claude-bot", "[claude] RELEASED #11\n\nReleased #11.", "2026-09-03T10:30:00Z"),
    ]
    assert derive_leases(comments) == {}


def test_pr_for_issue_prefers_the_current_assignees_pr_among_open_ones():
    from tests.factories import mk_board, mk_issue, mk_pr
    board = mk_board(issues={11: mk_issue(11, "in_progress", assignees=("meld-codex-bot",))},
                     pulls={30: mk_pr(30, 11, author="meld-claude-bot"), 31: mk_pr(31, 11, author="meld-codex-bot"), 32: mk_pr(32, 11, author="meld-claude-bot")})
    assert board.pr_for_issue(11).number == 31
```

Append to `tests/test_posting.py`:

```python
def test_released_is_framework_only(tmp_path):
    import pytest
    from meld.posting import PostError, post
    from tests.productfixtures import make_product
    fake, ctx, repo, origin = make_product(tmp_path, group="claude", role="group")
    with pytest.raises(PostError, match="RELEASED"):
        post(ctx, "RELEASED", ref="#11", text="x")
```

(`make_product(..., role="group")` — check `tests/conftest.env_for`: the orchestrator role is `MELD_ROLE=orchestrator`; use whatever value the fixture expects for an orchestrator context, e.g. `role="orchestrator"`.)

Append to `tests/test_review.py` (reuse the file's existing author-group COMMENT-review test setup; the test asserts the marker and that the board counts it):

```python
def test_author_groups_request_changes_counts_toward_the_pingpong_cap():
    from meld.assemble import _pull
    from meld.review import AUTHOR_VERDICT_MARKER
    from tests.fakegh import FakeGh
    from meld.github import GitHub
    fake = FakeGh()
    raw = {"number": 20, "title": "t", "body": "Closes #13", "head": {"ref": "meld/13-x", "sha": "abc123"}, "user": {"login": "meld-claude-bot"}, "state": "open", "labels": []}
    fake.route("GET", "repos/acme/widgets/pulls/20/reviews", [
        {"user": {"login": "meld-claude-bot"}, "state": "COMMENTED", "commit_id": "abc123", "submitted_at": "2026-09-03T10:00:00Z",
         "body": f"{AUTHOR_VERDICT_MARKER} request-changes\n\nVerdict: request-changes — posted as a comment review ..."}])
    fake.route("GET", "repos/acme/widgets/pulls/20/commits", [{"author": {"login": "meld-codex-bot"}, "committer": {"login": "meld-codex-bot"}}])
    fake.route("GET", "repos/acme/widgets/commits/abc123/check-runs", {"check_runs": []})
    fake.route("GET", "repos/acme/widgets/pulls/20/files", [])
    fake.route("GET", "repos/acme/widgets/commits/abc123/status", {"state": "pending", "statuses": []})
    gh = GitHub("acme/widgets", token="t", run=fake)
    pr = _pull(gh, raw)
    assert pr.reviews[0].state == "CHANGES_REQUESTED" and pr.reviews[0].as_author
    assert pr.changes_requested_total() == 1
```

Find the test that exercises `release` (grep `release(` in `tests/`) and add an assertion that the coordination issue received a `[claude] RELEASED #11` (or `[claude:pm] RELEASED #11` for a PM caller) comment after a successful release.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agreement.py tests/test_board.py tests/test_posting.py tests/test_review.py -q`
Expected: the four new tests fail (`joint_comment_id == 4`, a lease survives `RELEASED`, PR 32 wins, `ImportError` for `AUTHOR_VERDICT_MARKER`).

- [ ] **Step 3: Verified joint sticks (`grammar.agreement_for`)**

In the joint branch, replace the unconditional assignment with:

```python
        if h.party.kind == "joint":
            if joint_verified:
                # Decision 1 (plan 5): once the proposer's own bot has closed the round, a later [joint] -- forged or
                # merely redundant -- must not flip the ref back to unverified (spec 21 item 27).
                continue
            joint_posted, joint_verb, joint_author, joint_id = True, h.verb, cmt.author, cmt.id
            closed = text is not None and ((agreed >= set(cfg.group_names)) or ((agreed | set(dissent)) >= set(cfg.group_names)))
            joint_verified = closed and proposer is not None and cmt.author == cfg.groups[proposer].bot
            continue
```

`posting.post`'s existing `if agreement.joint_verified: _refuse(... "already has a [joint] comment")` already refuses a second verified-author post; keep it.

- [ ] **Step 4: `RELEASED` (Decision 2)**

- `grammar.py`: add `"RELEASED"` to `ORCHESTRATOR_VERBS` and `PM_VERBS` (it needs a ref: add to `REQUIRES_REF`).
- `posting.py`: `FRAMEWORK_ONLY_VERBS = frozenset({"LEASE", "EXPIRED", "IGNORED-COMMENT", "RELEASED"})` and extend the refusal text to name `release`.
- `board.derive_leases`: extend the closing condition to `(c.header.verb in ("EXPIRED", "RELEASED") and c.header.party.kind in ("pm", "group")) or (c.header.verb == "RELEASE" and c.header.party.kind == "human")`.
- `actions.release`: after the label changes, for a non-human actor post the framework comment:

```python
    if not actor.is_human:
        note = compose(Header(actor.party, "RELEASED", f"#{number}", None), f"Released #{number}; any earlier LEASE on it is void.")
        ctx.gh.create_comment(board.coordination_issue, note)
```

(`Header`, `Party`, `compose` are already imported in `actions.py` for the force-release note.)

- [ ] **Step 5: Open-PR tie-break (Decision 7)**

Replace `Board.pr_for_issue`:

```python
    def pr_for_issue(self, issue: int) -> PullRequest | None:
        candidates = [p for p in self.pulls.values() if p.issue_number == issue]
        iss = self.issues.get(issue)
        issue_closed = iss is not None and iss.state == "closed"
        assignees = set(iss.assignees) if iss is not None else set()
        # Open issue: the open PR is live, preferring one authored by a current assignee (a stale PR from an earlier
        # claimant loses); closed issue: the PR that merged is the record.
        if issue_closed:
            candidates.sort(key=lambda p: (not p.merged, p.state != "open", -p.number))
        else:
            candidates.sort(key=lambda p: (p.state != "open", p.author not in assignees, -p.number))
        return candidates[0] if candidates else None
```

- [ ] **Step 6: The author group's request-changes counts (Decision 5)**

- `board.Review` gains `as_author: bool = False`.
- `review.py`: add `AUTHOR_VERDICT_MARKER = "meld-verdict:"` next to `_AUTHOR_VERB`, and make the `as_author` body start with the marker line: `body = f"{AUTHOR_VERDICT_MARKER} {verdict}\n\nVerdict: {verdict} — posted as a comment review because ..."` (keep the rest of the sentence).
- `assemble._pull`: when building `reviews`, read `r.get("body") or ""`; if `state == "COMMENTED"` and the body's first line is `f"{AUTHOR_VERDICT_MARKER} request-changes"`, store `Review(author, "CHANGES_REQUESTED", commit_id, submitted_at, as_author=True)`; an `approve` marker stays `COMMENTED` (the status carries approvals, `approving_parties` is unchanged). Import the marker from `meld.review`? No — that would import review into assemble (cycle risk); define `AUTHOR_VERDICT_MARKER` in `board.py` and import it in both `assemble.py` and `review.py`.
- `rules.py`: in `review()`'s ping-pong branch (`review.py`), the `extra` computation already adds 1 for `as_author`; now that the board counts the author's own COMMENT reviews, change `extra = 0 if already_submitted and not as_author else 1` to `extra = 0 if already_submitted else 1` and update the comment above it.

- [ ] **Step 7: Run the suite**

Run: `uv run pytest -q`
Expected: green. If `tests/test_review.py`'s ping-pong test for the author path asserted the old `extra` arithmetic, update its expectation to the new count (one request-changes = one toward the cap, counted once).

- [ ] **Step 8: Commit**

```bash
git add src/meld/grammar.py src/meld/posting.py src/meld/board.py src/meld/assemble.py src/meld/actions.py src/meld/review.py tests/
git commit -m "fix: a verified joint sticks, RELEASED ends a lease, open-PR ties prefer the assignee, author request-changes count"
```

> **Ordering note.** Tasks 3–7 are the bootstrap path (`meld setup`, `meld doctor`, prompts/README) and are executed before the rest of the sweep so the owner can try the app end to end as early as possible; a milestone PR is opened when Task 7 passes review. Tasks 8–13 finish section 21.

### Task 3: `meld setup` part 1 — labels, milestone, coordination issue, starter `.meld.toml`

**Files:**
- Create: `src/meld/setup.py`
- Modify: `src/meld/github.py` (`get_repo`, `create_milestone`), `src/meld/cli.py`
- Test: `tests/test_setup.py` (new)

**Interfaces:**
- Consumes: `GitHub.list_labels/create_label/list_open_milestones/list_issues/create_issue`, `ModelRegistry.load/for_adapter`, `adapters.ADAPTER_NAMES`, `adapters.get_adapter(name).binary`, `board.STATUS_LABELS/COORDINATION_LABEL/PLAN_APPROVED_LABEL/CHALLENGED_LABEL`.
- Produces: `setup.LABELS: dict[str, tuple[str, str]]`; `setup.NUDGED_LABEL = "meld:nudged"` (moved to `board.py` in Task 9 as `board.NUDGED_LABEL`; define it in `board.py` now and import it); `setup.ensure_labels(gh) -> dict`; `setup.ensure_milestone(gh, title) -> tuple[int, str]`; `setup.ensure_coordination_issue(gh, milestone, milestone_title, goal) -> tuple[int, str]`; `setup.starter_toml(repo, default_branch, human, installed: list[str], registry) -> str`; `setup.installed_adapters(which=shutil.which) -> list[str]`; `setup.setup(gh, *, repo, goal, milestone_title, human, target_dir, registry, allow_public=False, which=shutil.which, force=False) -> dict`; `GitHub.get_repo() -> dict`; `GitHub.create_milestone(title, description="") -> dict`; CLI `meld setup REPO --goal TEXT [--milestone TITLE] [--dir PATH] [--allow-public] [--force]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_setup.py`:

```python
import tomllib
from pathlib import Path

import pytest

from meld.github import GitHub
from meld.models import ModelRegistry
from meld.setup import LABELS, SetupError, ensure_coordination_issue, ensure_labels, ensure_milestone, installed_adapters, setup, starter_toml
from tests.fakegh import FakeGh

R = "repos/acme/widgets/"


def gh_for(fake):
    return GitHub("acme/widgets", token=None, run=fake)


def route_repo(fake, private=True, default_branch="main"):
    fake.route("GET", "repos/acme/widgets", {"private": private, "default_branch": default_branch, "full_name": "acme/widgets"})
    fake.route("GET", "user", {"login": "empowerScott"})


def test_ensure_labels_creates_only_the_missing_ones():
    fake = FakeGh()
    fake.route("GET", R + "labels", [{"name": "meld:ready", "color": "0e8a16"}])
    fake.route("POST", R + "labels", {"name": "x"}, status=201)
    out = ensure_labels(gh_for(fake))
    posted = [c.body["name"] for c in fake.calls if c.method == "POST" and c.endpoint.endswith("labels")]
    assert "meld:ready" not in posted and set(posted) == set(LABELS) - {"meld:ready"}
    assert out == {"created": sorted(set(LABELS) - {"meld:ready"}), "existing": ["meld:ready"]}


def test_ensure_milestone_and_coordination_issue_are_idempotent():
    fake = FakeGh()
    fake.route("GET", R + "milestones", [{"number": 3, "title": "M1: Skeleton", "state": "open"}])
    fake.route("GET", R + "issues", [{"number": 10, "title": "Coordination: M1", "labels": [{"name": "meld:coordination"}], "milestone": {"number": 3}}])
    gh = gh_for(fake)
    assert ensure_milestone(gh, "M1: Skeleton") == (3, "existing")
    assert ensure_coordination_issue(gh, 3, "M1: Skeleton", "Build the skeleton") == (10, "existing")
    assert not [c for c in fake.calls if c.method == "POST"]
    fake2 = FakeGh()
    fake2.route("GET", R + "milestones", [])
    fake2.route("POST", R + "milestones", {"number": 4, "title": "M1: Skeleton"}, status=201)
    fake2.route("GET", R + "issues", [])
    fake2.route("POST", R + "issues", {"number": 12}, status=201)
    gh2 = gh_for(fake2)
    assert ensure_milestone(gh2, "M1: Skeleton") == (4, "created")
    number, how = ensure_coordination_issue(gh2, 4, "M1: Skeleton", "Build the skeleton")
    assert (number, how) == (12, "created")
    body = [c.body for c in fake2.calls if c.method == "POST" and c.endpoint.endswith("issues")][0]
    assert body["labels"] == ["meld:coordination"] and body["milestone"] == 4 and "Build the skeleton" in body["body"]


def test_starter_toml_names_installed_adapters_with_registry_models():
    registry = ModelRegistry.load()
    text = starter_toml("acme/widgets", "main", "empowerScott", ["claude", "codex"], registry)
    data = tomllib.loads(text)
    assert data["repo"] == {"name": "acme/widgets", "default_branch": "main", "private": True}
    assert data["humans"]["accounts"] == ["empowerScott"]
    for name in ("claude", "codex"):
        g = data["groups"][name]
        assert g["bot"] == f"meld-{name}-bot" and g["token_env"] == f"MELD_TOKEN_{name.upper()}"
        for role in ("orchestrator", "pm"):
            spec = registry.get(name, g[role]["model"])
            assert spec is not None and g[role]["effort"] in spec.efforts
        assert registry.tier_of(name, g["orchestrator"]["model"]) == "large"
    assert data["notify"]["slack_dm_webhook_env"] == "MELD_SLACK_DM_WEBHOOK"


def test_installed_adapters_uses_which():
    assert installed_adapters(which=lambda b: "/usr/bin/" + b if b == "claude" else None) == ["claude"]


def test_setup_refuses_a_public_repo_without_allow_public(tmp_path):
    fake = FakeGh()
    route_repo(fake, private=False)
    with pytest.raises(SetupError, match="public"):
        setup(gh_for(fake), repo="acme/widgets", goal="g", milestone_title="M1", human="empowerScott", target_dir=tmp_path,
              registry=ModelRegistry.load(), which=lambda b: "/bin/" + b)
    assert not (tmp_path / ".meld.toml").exists()


def test_setup_writes_the_starter_and_never_overwrites_without_force(tmp_path):
    fake = FakeGh()
    route_repo(fake)
    fake.route("GET", R + "labels", [])
    fake.route("POST", R + "labels", {"name": "x"}, status=201)
    fake.route("GET", R + "milestones", [])
    fake.route("POST", R + "milestones", {"number": 1, "title": "M1"}, status=201)
    fake.route("GET", R + "issues", [])
    fake.route("POST", R + "issues", {"number": 2}, status=201)
    out = setup(gh_for(fake), repo="acme/widgets", goal="Ship a widget catalogue", milestone_title="M1", human="empowerScott",
                target_dir=tmp_path, registry=ModelRegistry.load(), which=lambda b: "/bin/" + b)
    assert out["milestone"] == 1 and out["coordination_issue"] == 2 and out["config"] == str(tmp_path / ".meld.toml")
    assert out["labels"]["created"] and out["groups"] == ["claude", "codex"]
    text = (tmp_path / ".meld.toml").read_text()
    assert 'name = "acme/widgets"' in text
    with pytest.raises(SetupError, match="exists"):
        setup(gh_for(fake), repo="acme/widgets", goal="g", milestone_title="M1", human="empowerScott", target_dir=tmp_path,
              registry=ModelRegistry.load(), which=lambda b: "/bin/" + b)
    out2 = setup(gh_for(fake), repo="acme/widgets", goal="g", milestone_title="M1", human="empowerScott", target_dir=tmp_path,
                 registry=ModelRegistry.load(), which=lambda b: "/bin/" + b, force=True)
    assert out2["config_written"] is True


def test_setup_with_no_installed_cli_writes_commented_groups(tmp_path):
    fake = FakeGh()
    route_repo(fake)
    fake.route("GET", R + "labels", [])
    fake.route("POST", R + "labels", {"name": "x"}, status=201)
    fake.route("GET", R + "milestones", [{"number": 1, "title": "M1", "state": "open"}])
    fake.route("GET", R + "issues", [{"number": 2, "title": "c", "labels": [{"name": "meld:coordination"}], "milestone": {"number": 1}}])
    out = setup(gh_for(fake), repo="acme/widgets", goal="g", milestone_title="M1", human="empowerScott", target_dir=tmp_path,
                registry=ModelRegistry.load(), which=lambda b: None)
    assert out["groups"] == [] and "no CLI found" in " ".join(out["warnings"])
    text = (tmp_path / ".meld.toml").read_text()
    assert "# [groups.claude]" in text and tomllib.loads(text)["repo"]["name"] == "acme/widgets"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_setup.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.setup'`.

- [ ] **Step 3: GitHub methods**

In `src/meld/github.py` add, next to `get_milestone`:

```python
    def get_repo(self) -> dict:
        return self.get(f"repos/{self.repo}", use_cache=False)

    def create_milestone(self, title: str, description: str = "") -> dict:
        return self.post(self._r("milestones"), {"title": title, "description": description})
```

In `src/meld/board.py` add `NUDGED_LABEL = "meld:nudged"` next to `PLAN_APPROVED_LABEL`.

- [ ] **Step 4: Write `src/meld/setup.py`**

```python
"""meld setup: bootstrap a product repository -- labels, milestone, coordination issue, a starter .meld.toml.

Idempotent: every GitHub object that already exists is reused, and the config file is never overwritten without
--force. Runs as the human (gh's own login), before any .meld.toml exists, so it does not go through load_context.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from meld.adapters import ADAPTER_NAMES, get_adapter
from meld.board import CHALLENGED_LABEL, COORDINATION_LABEL, NUDGED_LABEL, PLAN_APPROVED_LABEL, STATUS_LABELS
from meld.github import GitHub
from meld.models import ModelRegistry, ModelSpec


class SetupError(RuntimeError):
    pass


# name -> (color, description). Colors are GitHub's 6-hex without '#'.
LABELS: dict[str, tuple[str, str]] = {
    COORDINATION_LABEL: ("5319e7", "The milestone's coordination issue: orchestrators talk here"),
    PLAN_APPROVED_LABEL: ("0e8a16", "The human approved the plan; the milestone is executing"),
    "meld:planned": ("c2e0c6", "Created from the agreed plan; waits for plan approval"),
    "meld:ready": ("0e8a16", "Ready to be claimed by a group"),
    "meld:in-progress": ("fbca04", "Claimed; a worker is building it"),
    "meld:in-review": ("1d76db", "A PR is open and under cross-review"),
    "meld:blocked": ("b60205", "Blocked: a worker failed, or reviews reached the ping-pong cap"),
    "meld:needs-human": ("d93f0b", "A person must do something before work continues"),
    CHALLENGED_LABEL: ("e99695", "A CHALLENGE is open against this item"),
    NUDGED_LABEL: ("ededed", "The human was nudged about this needs-human item"),
}
assert set(STATUS_LABELS) <= set(LABELS)

_EFFORT_PREFERENCE = {"orchestrator": ("high", "xhigh", "medium"), "pm": ("medium", "high", "low")}


def installed_adapters(which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """Adapter names whose CLI binary is on PATH."""
    return [name for name in ADAPTER_NAMES if which(get_adapter(name).binary)]


def ensure_labels(gh: GitHub) -> dict:
    existing = {label["name"] for label in gh.list_labels()}
    created = []
    for name, (color, description) in LABELS.items():
        if name in existing:
            continue
        gh.create_label(name, color, description)
        created.append(name)
    return {"created": sorted(created), "existing": sorted(existing & set(LABELS))}


def ensure_milestone(gh: GitHub, title: str) -> tuple[int, str]:
    for m in gh.list_open_milestones():
        if m.get("title") == title:
            return int(m["number"]), "existing"
    created = gh.create_milestone(title)
    return int(created["number"]), "created"


def ensure_coordination_issue(gh: GitHub, milestone: int, milestone_title: str, goal: str) -> tuple[int, str]:
    for issue in gh.list_issues(milestone):
        if COORDINATION_LABEL in {lab.get("name") for lab in issue.get("labels", [])}:
            return int(issue["number"]), "existing"
    body = (f"# Coordination: {milestone_title}\n\n## Goal\n\n{goal.strip()}\n\n"
            "Orchestrators post here in the `[party] VERB` grammar; humans speak as `[human]`. "
            "Approve the plan by adding the `meld:plan-approved` label to this issue.\n")
    created = gh.create_issue(f"Coordination: {milestone_title}", body, [COORDINATION_LABEL], milestone)
    return int(created["number"]), "created"


def _pick(specs: list[ModelSpec], tier: str) -> ModelSpec | None:
    for spec in specs:
        if spec.tier == tier:
            return spec
    return specs[0] if specs else None


def _effort(spec: ModelSpec, role: str) -> str:
    for candidate in _EFFORT_PREFERENCE[role]:
        if candidate in spec.efforts:
            return candidate
    return spec.efforts[-1]


def _group_block(name: str, registry: ModelRegistry, commented: bool) -> str:
    specs = registry.for_adapter(name)
    orch = _pick(specs, "large")
    pm = _pick(specs, "medium") or orch
    prefix = "# " if commented else ""
    lines = [f"{prefix}[groups.{name}]", f'{prefix}cli = "{name}"', f'{prefix}bot = "meld-{name}-bot"',
             f'{prefix}token_env = "MELD_TOKEN_{name.upper()}"', f"{prefix}concurrency = 8", f"{prefix}review_concurrency = 4"]
    for role, spec in (("orchestrator", orch), ("pm", pm)):
        model = spec.id if spec else "<model id from meld models>"
        effort = _effort(spec, role) if spec else "medium"
        lines += [f"{prefix}[groups.{name}.{role}]", f'{prefix}model = "{model}"', f'{prefix}effort = "{effort}"']
    return "\n".join(lines) + "\n"


def starter_toml(repo: str, default_branch: str, human: str, installed: list[str], registry: ModelRegistry) -> str:
    parts = [f'[repo]\nname = "{repo}"\ndefault_branch = "{default_branch}"\nprivate = true\n',
             f'[humans]\naccounts = ["{human}"]\n']
    for name in ADAPTER_NAMES:
        parts.append(_group_block(name, registry, commented=name not in installed))
    parts.append('[review]\nquorum = 2\npingpong_cap = 3\nsecurity_paths = ["auth/**", "**/secrets*", "infra/**"]\n')
    parts.append('[notify]\nslack_dm_webhook_env = "MELD_SLACK_DM_WEBHOOK"\nrepeat_after_minutes = 120\n')
    return "\n".join(parts)


def setup(gh: GitHub, *, repo: str, goal: str, milestone_title: str, human: str, target_dir: Path, registry: ModelRegistry,
          allow_public: bool = False, which: Callable[[str], str | None] = shutil.which, force: bool = False) -> dict:
    info = gh.get_repo()
    if not info.get("private", False) and not allow_public:
        raise SetupError(f"{repo} is public; meld setup refuses without --allow-public "
                         "(anyone who can comment could try to speak in the grammar; only configured bots and listed humans are heard, "
                         "but every comment is still read)")
    warnings: list[str] = []
    if not info.get("private", False):
        warnings.append(f"{repo} is public: comments from unknown accounts are ignored but visible to every worker's brief")
    config_path = target_dir / ".meld.toml"
    if config_path.exists() and not force:
        raise SetupError(f"{config_path} exists; pass --force to overwrite it")
    labels = ensure_labels(gh)
    milestone, milestone_how = ensure_milestone(gh, milestone_title)
    coordination, issue_how = ensure_coordination_issue(gh, milestone, milestone_title, goal)
    installed = installed_adapters(which)
    if not installed:
        warnings.append("no CLI found on PATH for any adapter (claude, codex); every [groups.*] block is commented out -- install a CLI and uncomment its block")
    target_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(starter_toml(repo, info.get("default_branch", "main"), human, installed, registry))
    return {"repo": repo, "labels": labels, "milestone": milestone, "milestone_how": milestone_how,
            "coordination_issue": coordination, "coordination_how": issue_how, "config": str(config_path), "config_written": True,
            "groups": installed, "warnings": warnings,
            "next": [f"create one GitHub machine account per group named in {config_path.name} and add each as a collaborator on {repo}",
                     "mint one fine-grained token per bot (Contents, Issues, Pull requests, Commit statuses: read and write) and export MELD_TOKEN_<GROUP>",
                     "run `meld doctor` to verify tokens, CLIs, and models", "run `meld run <group>` for each group, then `meld chat`"]}
```

- [ ] **Step 5: CLI**

In `src/meld/cli.py` add (imports: `from meld.setup import SetupError, setup as do_setup`; add `SetupError` to `_guard`'s tuple):

```python
@main.command()
@click.argument("repo")
@click.option("--goal", required=True, help="One paragraph: what the product is for. Goes into the coordination issue.")
@click.option("--milestone", "milestone_title", default="M1", show_default=True)
@click.option("--dir", "target_dir", type=click.Path(path_type=Path), default=Path("."), show_default=True, help="Where to write .meld.toml (the product clone).")
@click.option("--allow-public", is_flag=True, help="Proceed on a public repository despite the injection risk.")
@click.option("--force", is_flag=True, help="Overwrite an existing .meld.toml.")
@click.pass_context
def setup(ctx: click.Context, repo: str, goal: str, milestone_title: str, target_dir: Path, allow_public: bool, force: bool) -> None:
    """Bootstrap a product repo: labels, milestone, coordination issue, and a starter .meld.toml (runs as you, via gh)."""
    gh = GitHub(repo, token=None, run=RUN)
    human = _guard(gh.viewer_login)
    emit(_guard(lambda: do_setup(gh, repo=repo, goal=goal, milestone_title=milestone_title, human=human, target_dir=target_dir,
                                 registry=ModelRegistry.load(), allow_public=allow_public, force=force)), ctx.obj["pretty"])
```

(`GitHub` and `ModelRegistry` are already imported in `cli.py`; check and add if not.) Add a CliRunner test in `tests/test_setup.py` that monkeypatches `meld.cli.RUN` with a FakeGh carrying the routes from `test_setup_writes_the_starter...` plus `GET user`, runs `["setup", "acme/widgets", "--goal", "g", "--dir", str(tmp_path)]`, and asserts exit code 0 and `.meld.toml` exists.

- [ ] **Step 6: Run the suite**

Run: `uv run pytest tests/test_setup.py -q && uv run pytest -q`
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add src/meld/setup.py src/meld/github.py src/meld/board.py src/meld/cli.py tests/test_setup.py
git commit -m "feat: meld setup creates labels, milestone, coordination issue, and a starter .meld.toml"
```

### Task 4: `meld setup` part 2 — repo files, CODEOWNERS, branch protection, CI-paths warning, `--pr`

**Files:**
- Modify: `src/meld/setup.py`, `src/meld/cli.py`
- Test: `tests/test_setup.py`

**Interfaces:**
- Consumes: `brief.DEFAULT_STANDARDS`, `gitops.git`, `GitHub.put/create_pull`, `tests.gitfixtures.make_origin_and_clone`.
- Produces: `setup.repo_files(target_dir, human) -> list[str]` (relative paths written or extended); `setup.ci_paths_warnings(target_dir) -> list[str]`; `setup.protect_branch(gh, branch) -> dict`; `setup.open_setup_pr(gh, target_dir, default_branch, files, run=subprocess.run) -> dict`; `setup.setup(..., protect=True, pr=False, run=subprocess.run)` returns `files`, `protection`, `ci_warnings`, `pr`; CLI flags `--no-protection`, `--pr`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_setup.py`)

```python
def test_repo_files_write_codeowners_and_standards_once(tmp_path):
    from meld.setup import repo_files
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "CODEOWNERS").write_text("* @someone\n")
    written = repo_files(tmp_path, "empowerScott")
    assert set(written) == {".github/CODEOWNERS", ".meld/STANDARDS.md"}
    assert (tmp_path / ".github" / "CODEOWNERS").read_text() == "* @someone\n/.meld/workers/ @empowerScott\n"
    assert "visual design" in (tmp_path / ".meld" / "STANDARDS.md").read_text().lower()
    assert repo_files(tmp_path, "empowerScott") == []   # second run changes nothing


def test_ci_paths_warning_fires_when_workflows_filter_out_meld(tmp_path):
    from meld.setup import ci_paths_warnings
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("on:\n  pull_request:\n    paths:\n      - 'src/**'\n      - 'tests/**'\njobs: {}\n")
    (wf / "docs.yml").write_text("on:\n  pull_request:\n    paths:\n      - '.meld/**'\njobs: {}\n")
    warnings = ci_paths_warnings(tmp_path)
    assert len(warnings) == 1 and "ci.yml" in warnings[0] and ".meld" in warnings[0]
    (wf / "ci.yml").write_text("on: [pull_request]\njobs: {}\n")
    assert ci_paths_warnings(tmp_path) == []


def test_protect_branch_lists_review_statuses_as_informational_and_tolerates_403():
    from meld.setup import protect_branch
    fake = FakeGh()
    fake.route("PUT", R + "branches/main/protection", {"url": "x"})
    out = protect_branch(gh_for(fake), "main")
    call = [c for c in fake.calls if c.method == "PUT"][0]
    assert call.body["required_status_checks"] is None and call.body["required_pull_request_reviews"]["required_approving_review_count"] == 1
    assert out == {"protected": True, "branch": "main", "note": "meld/review/<group> statuses are informational; add them to required checks only if you want them blocking"}
    fake2 = FakeGh()
    fake2.route("PUT", R + "branches/main/protection", {"message": "Upgrade to GitHub Pro"}, status=403)
    out2 = protect_branch(gh_for(fake2), "main")
    assert out2["protected"] is False and "403" in out2["error"]


def test_open_setup_pr_commits_pushes_and_opens(tmp_path):
    from meld.setup import open_setup_pr, repo_files
    from tests.gitfixtures import make_origin_and_clone, sh
    origin, repo = make_origin_and_clone(tmp_path)
    (repo / ".meld.toml").write_text('[repo]\nname = "acme/widgets"\n')
    files = repo_files(repo, "empowerScott") + [".meld.toml"]
    fake = FakeGh()
    fake.route("POST", R + "pulls", {"number": 7, "html_url": "u7"}, status=201)
    out = open_setup_pr(gh_for(fake), repo, "main", files)
    assert out == {"pr": 7, "url": "u7", "branch": "meld/setup"}
    assert sh(["ls-remote", "--heads", "origin", "meld/setup"], repo)
    assert sh(["branch", "--show-current"], repo) == "meld/setup"
    body = [c.body for c in fake.calls if c.method == "POST"][0]
    assert body["head"] == "meld/setup" and body["base"] == "main" and ".meld.toml" in body["body"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_setup.py -q`
Expected: the four new tests fail with `ImportError`.

- [ ] **Step 3: Implement**

Append to `src/meld/setup.py` (imports: `import re`, `import subprocess`, `from meld.brief import DEFAULT_STANDARDS`, `from meld.github import GitHubError`, `from meld.gitops import git`):

```python
_CODEOWNERS_LINE = "/.meld/workers/ @{human}"


def repo_files(target_dir: Path, human: str) -> list[str]:
    """Write the repo-side files the framework relies on; return the relative paths written or extended (idempotent)."""
    written: list[str] = []
    codeowners = target_dir / ".github" / "CODEOWNERS"
    line = _CODEOWNERS_LINE.format(human=human)
    existing = codeowners.read_text() if codeowners.exists() else ""
    if line not in existing.splitlines():
        codeowners.parent.mkdir(parents=True, exist_ok=True)
        codeowners.write_text((existing if existing.endswith("\n") or not existing else existing + "\n") + line + "\n")
        written.append(".github/CODEOWNERS")
    standards = target_dir / ".meld" / "STANDARDS.md"
    if not standards.exists():
        standards.parent.mkdir(parents=True, exist_ok=True)
        standards.write_text("# Product standards\n\n" + DEFAULT_STANDARDS.strip() + "\n")
        written.append(".meld/STANDARDS.md")
    return written


_PATHS_RE = re.compile(r"^(\s*)paths:\s*$", re.MULTILINE)


def ci_paths_warnings(target_dir: Path) -> list[str]:
    """Workflows whose `paths:` filter never matches `.meld/**` cannot run on a memory PR (which touches only .meld/); if such a
    check is ever made required, decision and lesson PRs become unmergeable (spec 15)."""
    out: list[str] = []
    for wf in sorted((target_dir / ".github" / "workflows").glob("*.y*ml")):
        text = wf.read_text()
        for m in _PATHS_RE.finditer(text):
            indent = len(m.group(1))
            items: list[str] = []
            for line in text[m.end():].splitlines()[1:]:
                stripped = line.strip()
                if not stripped:
                    continue
                if len(line) - len(line.lstrip()) <= indent:
                    break
                if stripped.startswith("- "):
                    items.append(stripped[2:].strip("'\""))
            if items and not any(item == "**" or item.startswith(".meld") for item in items):
                out.append(f".github/workflows/{wf.name}: its `paths:` filter excludes .meld/**, so memory PRs trigger no run from it; "
                           "do not make its checks required, or add '.meld/**' to the filter")
                break
    return out


def protect_branch(gh: GitHub, branch: str) -> dict:
    """Require one approving review; require no status checks (the meld/review/<group> statuses stay informational, spec 21 item 4)."""
    body = {"required_status_checks": None, "enforce_admins": False,
            "required_pull_request_reviews": {"required_approving_review_count": 1}, "restrictions": None}
    try:
        gh.put(f"repos/{gh.repo}/branches/{branch}/protection", body)
    except GitHubError as e:
        return {"protected": False, "branch": branch, "error": f"branch protection not applied ({e.status}): {e}"}
    return {"protected": True, "branch": branch,
            "note": "meld/review/<group> statuses are informational; add them to required checks only if you want them blocking"}


def open_setup_pr(gh: GitHub, target_dir: Path, default_branch: str, files: list[str], run=subprocess.run) -> dict:
    """Commit `files` on branch meld/setup with the human's own git identity and credentials, push, open the PR."""
    branch = "meld/setup"
    git(["checkout", "-q", "-B", branch], target_dir, run)
    git(["add", "--", *files], target_dir, run)
    git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "chore: meld setup (labels, coordination issue, starter config)"], target_dir, run)
    git(["push", "-q", "--set-upstream", "origin", f"HEAD:refs/heads/{branch}"], target_dir, run)
    body = "Files written by `meld setup`:\n\n" + "\n".join(f"- `{f}`" for f in files) + \
           "\n\nReview the starter `.meld.toml` (bot names, token env vars, models) before merging; `meld doctor` verifies it."
    pr = gh.create_pull("chore: meld setup", branch, default_branch, body)
    return {"pr": int(pr["number"]), "url": str(pr.get("html_url", "")), "branch": branch}
```

Then extend `setup(...)`: add keyword parameters `protect: bool = True, pr: bool = False, run=subprocess.run`; after writing the config call `files = repo_files(target_dir, human) + [".meld.toml"]`, `ci = ci_paths_warnings(target_dir)`, `protection = protect_branch(gh, info.get("default_branch", "main")) if protect else {"protected": False, "skipped": True}`, and `pr_info = open_setup_pr(gh, target_dir, info.get("default_branch", "main"), files, run) if pr else None`; add `"files": files, "ci_warnings": ci, "protection": protection, "pr": pr_info` to the result and extend `warnings` with `ci`. In the CLI add `--no-protection` (`protect=not no_protection`) and `--pr` flags and pass `run=RUN`... no: git must run for real — pass the default `subprocess.run` for git (only `gh` calls go through `RUN`); document that in a comment.

- [ ] **Step 4: Run the suite**

Run: `uv run pytest tests/test_setup.py -q && uv run pytest -q`
Expected: green (the `--pr` test pushes to a local bare origin only).

- [ ] **Step 5: Commit**

```bash
git add src/meld/setup.py src/meld/cli.py tests/test_setup.py
git commit -m "feat: meld setup writes CODEOWNERS and standards, protects the branch, warns on CI path filters, and can open the PR"
```

### Task 5: `meld doctor` — the checklist

**Files:**
- Create: `src/meld/doctor.py`
- Modify: `src/meld/cli.py`, `src/meld/adapters/base.py`, `src/meld/adapters/claude.py`, `src/meld/adapters/codex.py`
- Test: `tests/test_doctor.py` (new)

**Interfaces:**
- Consumes: `Config`, `GitHub` (`viewer_login`, `get`, `combined_status`, `create_status`), `ModelRegistry.validate`, `adapters.get_adapter`, `adapters.base.SCRUBBED_EXACT/SCRUBBED_PREFIXES`, `gitops.git`, `state.meld_home`.
- Produces: `doctor.Check(name, ok: bool | None, detail: str, fix: str = "")` (`ok=None` is a warning or skipped check); `doctor.run_checks(cfg, repo_root, *, env, gh_factory, registry, adapter_for, which, home, probe_status=False, user_home=Path.home()) -> list[Check]`; `doctor.summary(checks) -> dict`; `Adapter.version_argv() -> list[str]` on the Protocol and both adapters (`["claude", "--version"]`, `["codex", "--version"]`); CLI `meld doctor [--probe-status]` (exit 1 when any check failed).

- [ ] **Step 1: Write the failing tests**

`tests/test_doctor.py`:

```python
import json
from pathlib import Path

from meld.config import Config
from meld.doctor import run_checks, summary
from meld.github import GitHub
from meld.models import ModelRegistry
from tests.conftest import PROJECT_TOML
from tests.fakeadapter import FakeAdapter
from tests.fakegh import FakeGh

import tomllib

R = "repos/acme/widgets/"


def cfg():
    return Config.from_dict(tomllib.loads(PROJECT_TOML))


def fakes(*, claude_login="meld-claude-bot", codex_login="meld-codex-bot", human="empowerScott", statuses_ok=True):
    by_token: dict[str | None, FakeGh] = {}
    for token, login in ((None, human), ("tok-c", claude_login), ("tok-x", codex_login)):
        f = FakeGh()
        f.route("GET", "user", {"login": login, "id": 7})
        f.route("GET", "users/meld-claude-bot", {"login": "meld-claude-bot", "id": 11})
        f.route("GET", "users/meld-codex-bot", {"login": "meld-codex-bot", "id": 12})
        f.route("GET", R + "branches/main", {"commit": {"sha": "abc123"}})
        f.route("GET", R + "issues", [])
        f.route("GET", R + "pulls", [])
        if statuses_ok:
            f.route("GET", R + "commits/abc123/status", {"state": "pending", "statuses": []})
        else:
            f.route("GET", R + "commits/abc123/status", {"message": "Resource not accessible"}, status=403)
        f.route("POST", R + "statuses/abc123", {"id": 1}, status=201)
        by_token[token] = f
    return by_token


def run(tmp_path, by_token, *, env=None, which=lambda b: "/bin/" + b, registry=None, probe_status=False, adapters=None, user_home=None):
    env = {"MELD_TOKEN_CLAUDE": "tok-c", "MELD_TOKEN_CODEX": "tok-x", **(env or {})}
    adapters = adapters or {"claude": FakeAdapter(ceiling=600, name="claude"), "codex": FakeAdapter(ceiling=600, name="codex")}
    return run_checks(cfg(), tmp_path, env=env, gh_factory=lambda token: GitHub("acme/widgets", token=token, run=by_token[token]),
                      registry=registry or ModelRegistry.load(Path("tests/fixtures/models_test.toml")) if registry else _registry(),
                      adapter_for=lambda name: adapters[name], which=which, home=tmp_path / "home", probe_status=probe_status,
                      user_home=user_home or tmp_path / "userhome")


def _registry():
    from tests.factories import REG
    return REG


def by_name(checks):
    return {c.name: c for c in checks}


def test_doctor_passes_a_healthy_setup(tmp_path):
    checks = by_name(run(tmp_path, fakes()))
    assert checks["token:claude"].ok and "meld-claude-bot" in checks["token:claude"].detail
    assert checks["gh-login"].ok and checks["cli:claude"].ok and checks["git-identity:codex"].ok
    assert checks["timeout-ceiling:claude"].ok and checks["token-permissions:claude"].ok
    assert checks["models:claude"].ok
    assert summary(run(tmp_path, fakes()))["failed"] == []


def test_doctor_fails_when_a_token_belongs_to_the_wrong_account(tmp_path):
    checks = by_name(run(tmp_path, fakes(codex_login="someone-else")))
    assert checks["token:codex"].ok is False and "someone-else" in checks["token:codex"].detail and "meld-codex-bot" in checks["token:codex"].detail


def test_doctor_flags_missing_tokens_binaries_and_unknown_models(tmp_path):
    checks = by_name(run(tmp_path, fakes(), env={"MELD_TOKEN_CODEX": ""}, which=lambda b: None))
    assert checks["token:codex"].ok is False and "MELD_TOKEN_CODEX" in checks["token:codex"].detail
    assert checks["cli:claude"].ok is False
    assert checks["models:codex"].ok is False and "gpt-x" in checks["models:codex"].detail   # PROJECT_TOML's codex model is not in REG


def test_doctor_warns_on_settings_env_and_permissions_and_ceilings(tmp_path):
    home = tmp_path / "userhome"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(json.dumps({"env": {"MELD_TOKEN_CLAUDE": "leak", "OTHER": "x"}}))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"env": {"GH_TOKEN": "leak"}}))
    slow = {"claude": FakeAdapter(ceiling=100, name="claude"), "codex": FakeAdapter(ceiling=600, name="codex")}
    checks = by_name(run(tmp_path, fakes(statuses_ok=False), adapters=slow, user_home=home))
    assert checks["settings-env"].ok is None and "MELD_TOKEN_CLAUDE" in checks["settings-env"].detail and "GH_TOKEN" in checks["settings-env"].detail
    assert checks["token-permissions:claude"].ok is False and "Commit statuses" in checks["token-permissions:claude"].detail
    assert checks["timeout-ceiling:claude"].ok is False and "300" in checks["timeout-ceiling:claude"].detail


def test_doctor_probe_status_posts_once_per_group_only_when_asked(tmp_path):
    by_token = fakes()
    run(tmp_path, by_token)
    assert not [c for c in by_token["tok-c"].calls if c.method == "POST"]
    run(tmp_path, by_token, probe_status=True)
    posts = [c for c in by_token["tok-c"].calls if c.method == "POST" and "statuses/abc123" in c.endpoint]
    assert len(posts) == 1 and posts[0].body["context"] == "meld/doctor" and posts[0].body["state"] == "success"


def test_doctor_warns_when_memory_is_large_and_origin_is_not_https(tmp_path):
    (tmp_path / ".meld" / "memory").mkdir(parents=True)
    (tmp_path / ".meld" / "memory" / "lessons.md").write_text("x" * (2 * 1024 * 1024 + 1))
    checks = by_name(run(tmp_path, fakes()))
    assert checks["memory-size"].ok is None and "2 MB" in checks["memory-size"].detail
    assert checks["origin-https"].ok is None   # tmp_path is not a git repo: reported as a warning, never a crash
```

Simplify the `run` helper's `registry` line before committing: `registry=registry or _registry()`. (`tests/factories.REG` knows `claude-opus-5`/`claude-sonnet-5`/`claude-haiku-4-5-20251001`/`gpt-test`, so PROJECT_TOML's `gpt-x` fails the model check as the test expects.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_doctor.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.doctor'`.

- [ ] **Step 3: Adapter `version_argv`**

`adapters/base.py`: add `def version_argv(self) -> list[str]: ...` to the `Adapter` Protocol. `claude.py`: `def version_argv(self) -> list[str]: return [self.binary, "--version"]`; same in `codex.py`. `tests/fakeadapter.py`: `def version_argv(self): return [sys.executable, "--version"]`.

- [ ] **Step 4: Write `src/meld/doctor.py`**

```python
"""meld doctor: verify the machine, the tokens, the config, and the product repo before running a group (spec 20 step 2, 21 item 2).

Read-only, apart from the opt-in status probe. Every check is a Check(ok=True|False|None): None is a warning or a check that
could not run. Nothing here prints or stores a token value -- only variable names and account logins.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping

from meld.adapters.base import SCRUBBED_EXACT, SCRUBBED_PREFIXES
from meld.config import Config
from meld.github import GitHub, GitHubError
from meld.gitops import GitError, git, noreply_email
from meld.models import ModelRegistry

MEMORY_WARN_BYTES = 2 * 1024 * 1024
PROBE_CONTEXT = "meld/doctor"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool | None
    detail: str
    fix: str = ""


def _version(argv: list[str], run=subprocess.run) -> str | None:
    try:
        proc = run(argv, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or proc.stderr or "").strip().splitlines()[0] if (proc.stdout or proc.stderr) else "ok"


def _settings_env_names(paths: list[Path]) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for p in paths:
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        env = data.get("env") if isinstance(data, dict) else None
        if not isinstance(env, dict):
            continue
        bad = sorted(k for k in env if k in SCRUBBED_EXACT or any(k.startswith(pre) for pre in SCRUBBED_PREFIXES))
        if bad:
            hits[str(p)] = bad
    return hits


def _hidden_pth(repo_root: Path) -> list[Path]:
    out = []
    for p in (repo_root / ".venv" / "lib").glob("python*/site-packages/*.pth"):
        try:
            flags = getattr(os.stat(p), "st_flags", 0)
        except OSError:
            continue
        if flags & getattr(stat, "UF_HIDDEN", 0):
            out.append(p)
    return out


def _memory_bytes(repo_root: Path) -> int:
    total = 0
    for p in list((repo_root / ".meld" / "decisions").glob("*.md")) + [repo_root / ".meld" / "memory" / "lessons.md"]:
        if p.is_file():
            total += p.stat().st_size
    return total


def run_checks(cfg: Config, repo_root: Path, *, env: Mapping[str, str], gh_factory: Callable[[str | None], GitHub], registry: ModelRegistry,
               adapter_for, which: Callable[[str], str | None] = shutil.which, home: Path, probe_status: bool = False,
               user_home: Path = Path.home(), run=subprocess.run) -> list[Check]:
    checks: list[Check] = []
    # 1. gh's own login is a listed human
    try:
        me = gh_factory(None).viewer_login()
        checks.append(Check("gh-login", me in cfg.humans, f"gh is logged in as {me}; humans: {', '.join(cfg.humans)}",
                            "" if me in cfg.humans else "add the login to [humans].accounts or `gh auth switch`"))
    except GitHubError as e:
        checks.append(Check("gh-login", False, f"gh api user failed: {e}", "run `gh auth login`"))
    head_sha: str | None = None
    try:
        head_sha = gh_factory(None).get(f"repos/{cfg.repo_name}/branches/{cfg.default_branch}", use_cache=False)["commit"]["sha"]
    except (GitHubError, KeyError, TypeError):
        pass
    for group in cfg.groups.values():
        adapter = adapter_for(group.cli)
        # 2. CLI installed
        binary = adapter.version_argv()[0]
        found = which(binary)
        version = _version(adapter.version_argv(), run) if found else None
        checks.append(Check(f"cli:{group.name}", bool(found and version), f"{binary}: {version or 'not found on PATH'}",
                            "" if found and version else f"install the {group.cli} CLI and log in"))
        # 3. token present and owned by the configured bot
        token = env.get(group.token_env)
        if not token:
            checks.append(Check(f"token:{group.name}", False, f"{group.token_env} is not set", f"export {group.token_env} (from 1Password) for the orchestrator and PM processes"))
            login = None
        else:
            try:
                login = gh_factory(token).viewer_login()
                checks.append(Check(f"token:{group.name}", login == group.bot,
                                    f"{group.token_env} belongs to {login}; configured bot is {group.bot}",
                                    "" if login == group.bot else "mint the token from the bot account, or fix [groups.%s].bot" % group.name))
            except GitHubError as e:
                login = None
                checks.append(Check(f"token:{group.name}", False, f"{group.token_env}: gh api user failed: {e}", "check the token is valid and not expired"))
        # 4. git identity for worktree commits
        if group.git_email:
            checks.append(Check(f"git-identity:{group.name}", True, f"{group.git_name or group.bot} <{group.git_email}> (configured)"))
        else:
            try:
                user = gh_factory(None).get(f"users/{group.bot}", use_cache=False)
                checks.append(Check(f"git-identity:{group.name}", True, f"{group.bot} <{noreply_email(int(user['id']), group.bot)}> (derived)"))
            except (GitHubError, KeyError, TypeError, ValueError) as e:
                checks.append(Check(f"git-identity:{group.name}", False, f"cannot resolve {group.bot}: {e}", "set git_name/git_email under [groups.%s]" % group.name))
        # 5. models
        problems = []
        for role_name, role in (("orchestrator", group.orchestrator), ("pm", group.pm)):
            problems += [f"{role_name}: {p}" for p in registry.validate(group.cli, role.model, role.effort)]
        checks.append(Check(f"models:{group.name}", not problems, "; ".join(problems) or f"{group.orchestrator.model}/{group.orchestrator.effort}, {group.pm.model}/{group.pm.effort}",
                            "" if not problems else "pick models from `meld models`"))
        # 6. command ceiling vs wait timeout
        ceiling = adapter.command_timeout_ceiling_seconds()
        ok = ceiling > cfg.wait_timeout_seconds
        checks.append(Check(f"timeout-ceiling:{group.name}", ok, f"command ceiling {ceiling}s vs polling.wait_timeout_seconds {cfg.wait_timeout_seconds}s",
                            "" if ok else "lower polling.wait_timeout_seconds below the CLI's command ceiling"))
        # 7. token permissions (read probes; write only with --probe-status)
        if token and login == group.bot and head_sha:
            gh = gh_factory(token)
            missing = []
            for perm, call in (("Issues", lambda: gh.get(f"repos/{cfg.repo_name}/issues", {"per_page": 1}, use_cache=False)),
                               ("Pull requests", lambda: gh.get(f"repos/{cfg.repo_name}/pulls", {"per_page": 1}, use_cache=False)),
                               ("Commit statuses", lambda: gh.combined_status(head_sha))):
                try:
                    call()
                except GitHubError:
                    missing.append(perm)
            detail = "read: Issues, Pull requests, Commit statuses" if not missing else f"cannot read: {', '.join(missing)}"
            if probe_status and "Commit statuses" not in missing:
                try:
                    gh.create_status(head_sha, "success", PROBE_CONTEXT, f"meld doctor probe by {group.name}")
                    detail += "; write: Commit statuses (probed)"
                except GitHubError:
                    missing.append("Commit statuses (write)")
                    detail += "; cannot write Commit statuses"
            elif not missing:
                detail += "; write not probed (pass --probe-status)"
            checks.append(Check(f"token-permissions:{group.name}", not missing, detail,
                                "" if not missing else "re-mint the token with Contents, Issues, Pull requests, Commit statuses: read and write"))
        else:
            checks.append(Check(f"token-permissions:{group.name}", None, "skipped (token or default-branch head unavailable)"))
    # 8. settings.json env blocks that would re-inject a token or party into a worker
    hits = _settings_env_names([user_home / ".claude" / "settings.json", repo_root / ".claude" / "settings.json", repo_root / ".claude" / "settings.local.json"])
    checks.append(Check("settings-env", None if hits else True,
                        "; ".join(f"{p} env names {', '.join(names)}" for p, names in hits.items()) or "no scrubbed names in any settings.json env block",
                        "remove those names from the env block; workers must not receive tokens or party identity" if hits else ""))
    # 9. hidden .pth files (README)
    hidden = _hidden_pth(repo_root)
    checks.append(Check("pth-hidden", None if hidden else True, "; ".join(str(p) for p in hidden) or "no hidden .pth files under .venv",
                        "run `chflags nohidden <file>` (see README)" if hidden else ""))
    # 10. memory store size (decision 11)
    size = _memory_bytes(repo_root)
    big = size > MEMORY_WARN_BYTES
    checks.append(Check("memory-size", None if big else True, f"{size} bytes of decisions and lessons" + (" exceeds the 2 MB mark: recall scans grow with it (spec 21 item 13)" if big else ""),
                        "prune or split .meld/memory/lessons.md, or build the recall index" if big else ""))
    # 11. webhook
    if cfg.notify_webhook_env:
        set_ = bool(env.get(cfg.notify_webhook_env))
        checks.append(Check("webhook", None if not set_ else True, f"{cfg.notify_webhook_env} is {'set' if set_ else 'not set'}", "" if set_ else "export it, or remove [notify] to silence this"))
    # 12. origin over HTTPS (spec 18: host SSH keys are not blocked for workers)
    try:
        url = git(["remote", "get-url", "origin"], repo_root)
        checks.append(Check("origin-https", True if url.startswith("https://") else None, url, "" if url.startswith("https://") else "use an HTTPS origin so workers cannot push with the host's SSH keys"))
    except GitError as e:
        checks.append(Check("origin-https", None, f"cannot read origin: {e}"))
    return checks


def summary(checks: list[Check]) -> dict:
    return {"checks": [asdict(c) for c in checks], "failed": [c.name for c in checks if c.ok is False],
            "warnings": [c.name for c in checks if c.ok is None], "ok": all(c.ok is not False for c in checks)}
```

- [ ] **Step 5: CLI**

```python
@main.command()
@click.option("--probe-status", is_flag=True, help="Also verify Commit statuses WRITE by posting a `meld/doctor` status on the default branch head (one per group).")
@click.pass_context
def doctor(ctx: click.Context, probe_status: bool) -> None:
    """Verify CLIs, tokens, identities, models, permissions, and the product repo. Exit 1 when a check fails."""
    path = ctx.obj["config_path"] or _guard(lambda: find_config(Path.cwd()))
    cfg = _guard(lambda: load_config(path))
    checks = _guard(lambda: run_checks(cfg, path.parent, env=os.environ, gh_factory=lambda token: GitHub(cfg.repo_name, token=token, run=RUN),
                                       registry=ModelRegistry.load(), adapter_for=ADAPTER_FOR, home=meld_home(os.environ), probe_status=probe_status))
    result = summary(checks)
    emit(result, ctx.obj["pretty"])
    if not result["ok"]:
        raise SystemExit(1)
```

Imports needed in `cli.py`: `find_config` (from `meld.context`), `load_config` (from `meld.config`), `meld_home` (from `meld.state`), `run_checks, summary` (from `meld.doctor`). A CliRunner test in `tests/test_doctor.py` monkeypatches `meld.cli.RUN` with the `None`-token FakeGh (all calls in the CLI path go through one `RUN`, so route both `GET user` answers to the human login and expect `token:*` checks to fail; assert exit code 1 and that the JSON names them).

- [ ] **Step 6: Run the suite**

Run: `uv run pytest tests/test_doctor.py -q && uv run pytest -q`
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add src/meld/doctor.py src/meld/cli.py src/meld/adapters/ tests/fakeadapter.py tests/test_doctor.py
git commit -m "feat: meld doctor verifies CLIs, tokens, identities, models, permissions, settings, and the repo"
```

### Task 6: `meld doctor --sweep` — scratch cleanup under `$MELD_HOME`

**Files:**
- Modify: `src/meld/doctor.py`, `src/meld/cli.py`
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `results.py` layout (`results/<group>/{superseded,submitted}/`), `dispatch._worker_scratch` layout (`runs/<group>/<n>-gh/`, `<n>-gitconfig`), `memory._open_memory_pr` worktree path (`worktrees/<group>/memory-*`), `housekeeping.CI_RED_STATE`, `gitops.remove_worktree`.
- Produces: `doctor.sweep(home, cfg, *, repo_root, pull_states: Mapping[int, str] | None = None, older_than_days=7, apply=False, now=None) -> dict` with `candidates` (`[{"kind", "path", "reason"}]`), `removed` (paths), `dry_run`; `pull_states` maps PR number → `"open" | "merged" | "closed"`; CLI `meld doctor --sweep [--dry-run] [--older-than-days N]`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_doctor.py`)

```python
import os
import time

from meld.doctor import sweep


def _touch(path: Path, age_days: float = 0.0, text: str = "{}"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(path, (old, old))


def test_sweep_lists_then_removes_only_what_it_listed(tmp_path):
    home = tmp_path / "home"
    _touch(home / "results" / "claude" / "superseded" / "review-20-1.json", age_days=10)
    _touch(home / "results" / "claude" / "submitted" / "review-21-abc-1.json", age_days=1)      # too young
    _touch(home / "results" / "claude" / "review-22.json", text='{"head_sha": "x", "event": "APPROVE"}')  # memory-PR record, PR merged
    _touch(home / "results" / "claude" / "review-23.json", text='{"head_sha": "x", "event": "APPROVE"}')  # PR still open
    _touch(home / "runs" / "claude" / "11.json", text='{"issue": 11}')
    _touch(home / "runs" / "claude" / "11-gitconfig", text="")
    (home / "runs" / "claude" / "11-gh").mkdir(parents=True)
    _touch(home / "runs" / "claude" / "12-gitconfig", text="")                                    # no 12.json: orphan
    (home / "runs" / "claude" / "12-gh").mkdir(parents=True)
    (home / "worktrees" / "claude" / "memory-dec-9").mkdir(parents=True)                          # crash debris
    _touch(home / "state" / "claude" / "pm" / "ci-red.json", text='{"20": {"head": "h", "commented": true, "swapped": true}, "23": {"head": "h", "commented": true, "swapped": false}}')
    states = {20: "closed", 22: "merged", 23: "open"}
    out = sweep(home, cfg(), repo_root=tmp_path, pull_states=states, older_than_days=7)
    kinds = {(c["kind"], Path(c["path"]).name) for c in out["candidates"]}
    assert ("superseded", "review-20-1.json") in kinds and ("submitted", "review-21-abc-1.json") not in kinds
    assert ("memory-review-record", "review-22.json") in kinds and ("memory-review-record", "review-23.json") not in kinds
    assert ("run-scratch", "12-gitconfig") in kinds and ("run-scratch", "12-gh") in kinds and ("run-scratch", "11-gh") not in kinds
    assert ("memory-worktree", "memory-dec-9") in kinds
    assert ("ci-red-record", "20") in kinds and ("ci-red-record", "23") not in kinds
    assert out["dry_run"] is True and out["removed"] == []
    assert (home / "results" / "claude" / "superseded" / "review-20-1.json").exists()
    applied = sweep(home, cfg(), repo_root=tmp_path, pull_states=states, older_than_days=7, apply=True)
    assert sorted(applied["removed"]) == sorted(c["path"] for c in out["candidates"])
    assert not (home / "results" / "claude" / "superseded" / "review-20-1.json").exists()
    assert not (home / "runs" / "claude" / "12-gh").exists() and (home / "runs" / "claude" / "11-gh").exists()
    assert not (home / "worktrees" / "claude" / "memory-dec-9").exists()
    remaining = json.loads((home / "state" / "claude" / "pm" / "ci-red.json").read_text())
    assert set(remaining) == {"23"}


def test_sweep_without_board_state_skips_pr_dependent_kinds(tmp_path):
    home = tmp_path / "home"
    _touch(home / "results" / "claude" / "review-22.json", text='{"head_sha": "x", "event": "APPROVE"}')
    out = sweep(home, cfg(), repo_root=tmp_path, pull_states=None)
    assert out["candidates"] == [] and "board" in " ".join(out["skipped"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_doctor.py -q -k sweep`
Expected: FAIL with `ImportError: cannot import name 'sweep'`.

- [ ] **Step 3: Implement `sweep`** (append to `src/meld/doctor.py`; add `import shutil, time` and `from meld.gitops import remove_worktree`, `from meld.housekeeping import CI_RED_STATE`, `from meld.atomic import write_atomic`)

```python
def sweep(home: Path, cfg: Config, *, repo_root: Path, pull_states: Mapping[int, str] | None = None, older_than_days: int = 7,
          apply: bool = False, now: float | None = None) -> dict:
    """Scratch that meld leaves behind on purpose (verdict archives, per-run gh/git config, crashed memory worktrees, stale
    per-PR records). Lists every candidate with its reason; deletes only when `apply` is true, and only what it listed."""
    now = time.time() if now is None else now
    cutoff = now - older_than_days * 86400
    candidates: list[dict] = []
    skipped: list[str] = []
    if pull_states is None:
        skipped.append("memory-review-record and ci-red-record need the board (pull states); run from a product clone with gh logged in")
    for group in cfg.groups.values():
        results = home / "results" / group.name
        for kind in ("superseded", "submitted"):
            for p in sorted((results / kind).glob("*.json")):
                if p.stat().st_mtime < cutoff:
                    candidates.append({"kind": kind, "path": str(p), "reason": f"consumed verdict older than {older_than_days} days"})
        if pull_states is not None:
            for p in sorted(results.glob("review-*.json")):
                number = p.stem.removeprefix("review-")
                if number.isdigit() and pull_states.get(int(number)) in ("merged", "closed"):
                    candidates.append({"kind": "memory-review-record", "path": str(p), "reason": f"PR #{number} is {pull_states[int(number)]}; the idempotency record is no longer needed"})
        runs = home / "runs" / group.name
        if runs.is_dir():
            for p in sorted(runs.iterdir()):
                stem = p.name.removesuffix("-gh").removesuffix("-gitconfig")
                if p.name in (f"{stem}-gh", f"{stem}-gitconfig") and stem.isdigit() and not (runs / f"{stem}.json").exists():
                    candidates.append({"kind": "run-scratch", "path": str(p), "reason": f"no live run record {stem}.json"})
        for p in sorted((home / "worktrees" / group.name).glob("memory-*")):
            if p.is_dir():
                candidates.append({"kind": "memory-worktree", "path": str(p), "reason": "memory-PR scratch worktree left by a crash"})
        if pull_states is not None:
            for state_file in sorted((home / "state" / group.name).glob(f"*/{CI_RED_STATE}")):
                try:
                    data = json.loads(state_file.read_text())
                except (OSError, ValueError):
                    continue
                if not isinstance(data, dict):
                    continue
                for key in sorted(data):
                    if key.isdigit() and pull_states.get(int(key)) in ("merged", "closed"):
                        candidates.append({"kind": "ci-red-record", "path": f"{state_file}#{key}", "reason": f"PR #{key} is {pull_states[int(key)]}"})
    removed: list[str] = []
    if apply:
        for c in candidates:
            target = c["path"]
            try:
                if c["kind"] == "ci-red-record":
                    file, key = target.rsplit("#", 1)
                    data = json.loads(Path(file).read_text())
                    data.pop(key, None)
                    write_atomic(Path(file), json.dumps(data, indent=2, sort_keys=True))
                elif c["kind"] == "memory-worktree":
                    try:
                        remove_worktree(repo_root, Path(target))
                    except Exception:  # not registered as a worktree of this clone: plain directory debris
                        shutil.rmtree(target, ignore_errors=True)
                elif Path(target).is_dir():
                    shutil.rmtree(target)
                else:
                    Path(target).unlink()
            except OSError as e:
                skipped.append(f"{target}: {type(e).__name__}")
                continue
            removed.append(target)
    return {"candidates": candidates, "removed": removed, "dry_run": not apply, "skipped": skipped}
```

(`Path(c["path"]).name` for a `ci-red-record` candidate is the key after `#`, which is what the test compares.)

- [ ] **Step 4: CLI**

Extend the `doctor` command: options `--sweep` (flag), `--dry-run` (flag), `--older-than-days` (int, default 7). When `--sweep` is set, do not run the checks; build `pull_states` from the board when possible:

```python
    if do_sweep:
        states: dict[int, str] | None = None
        try:
            c = load_context(path, run=RUN, adapter_for=ADAPTER_FOR)
            _, board = build_status(c)
            states = {n: ("merged" if p.merged else p.state) for n, p in board.pulls.items()}
        except (BoardError, GitHubError, ConfigError):
            states = None
        emit(sweep(meld_home(os.environ), cfg, repo_root=path.parent, pull_states=states, older_than_days=older_than_days, apply=not dry_run), ctx.obj["pretty"])
        return
```

Note: closed-unmerged PRs are filtered off the board by `assemble.build_board`, so a record for one stays until `--older-than-days` would apply — document this in the option help ("records for closed-unmerged PRs are not detected; they age out with the archives").

- [ ] **Step 5: Run the suite; commit**

Run: `uv run pytest tests/test_doctor.py -q && uv run pytest -q` → green.

```bash
git add src/meld/doctor.py src/meld/cli.py tests/test_doctor.py
git commit -m "feat: meld doctor --sweep lists and removes meld's scratch under MELD_HOME"
```

### Task 7: The try-out milestone — commands in prompts, Taskfile tasks, README quick start, handoff

**Files:**
- Modify: `src/meld/prompts.py` (`COMMANDS`), `src/meld/prompts/pm.md`, `src/meld/prompts/_preamble.md`, `Taskfile.yml`, `README.md`, `docs/HANDOFF.md`
- Test: `tests/test_role_prompts.py`, `tests/test_prompts.py`

**Interfaces:** prompt/doc text only; `COMMANDS` gains `"meld setup REPO --goal TEXT [--milestone TITLE] [--pr]"` and `"meld doctor [--probe-status] [--sweep --dry-run]"`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_role_prompts.py`)

```python
def test_role_prompts_name_setup_and_doctor(tmp_path):
    pm = role_prompt("pm", CFG2, CFG2.groups["claude"], tmp_path)
    for needle in ("meld doctor [--probe-status]", "meld setup REPO --goal TEXT"):
        assert needle in pm, needle
    assert "meld doctor" in pm.split("## When something is off")[1]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_role_prompts.py -q` → FAIL on the first needle.

- [ ] **Step 3: Edit**

- `prompts.py`: append the two command strings to `COMMANDS`.
- `pm.md`, under `## When something is off`, add: "- A refusal about a token, a bot login, or a model is an environment problem, not a rule: post `[<group>:pm] NEEDS-HUMAN` asking the human to run `meld doctor` and fix what it reports, then stop retrying."
- `_preamble.md`: in the bullet describing the CLI, add "`meld setup` and `meld doctor` are the human's bootstrap commands; you never run them."
- `Taskfile.yml`: add tasks `doctor` (`uv run meld doctor {{.CLI_ARGS}}`) and `setup` (`uv run meld setup {{.REPO}} --goal "{{.GOAL}}" {{.CLI_ARGS}}`, requiring `REPO` and `GOAL` vars), keeping `PYTHONPATH: src`.
- `README.md`: add a **Quick start** section before "Known issue": (1) `uv sync`; (2) create the bot accounts and tokens (spec 18: Contents, Issues, Pull requests, Commit statuses read/write) and export `MELD_TOKEN_<GROUP>` via `op run`; (3) `cd <product clone> && meld setup owner/name --goal "..." --pr`; (4) merge the setup PR, uncomment or adjust `[groups.*]`; (5) `meld doctor` until green; (6) `task run GROUP=claude` and `task run GROUP=codex`; (7) `meld chat`, approve the plan with the `meld:plan-approved` label when it appears. Keep the hidden-`.pth` section.
- `docs/HANDOFF.md`: update the milestone line to "plan 5 in progress: Tasks 1–7 landed (rules consolidation, verified-joint/lease fixes, `meld setup`, `meld doctor`); Tasks 8–13 (skills story, memory hardening, housekeeping/notifier, merge/audit/process nits, spec reconciliation) still to come", add `setup.py`/`doctor.py` to the repo map, and add "try the app: README Quick start" to "What is next".

- [ ] **Step 4: Run the suite; commit**

Run: `uv run pytest -q` → green.

```bash
git add src/meld/prompts.py src/meld/prompts/pm.md src/meld/prompts/_preamble.md Taskfile.yml README.md docs/HANDOFF.md tests/test_role_prompts.py
git commit -m "docs: quick start, Taskfile tasks, and prompt commands for meld setup and meld doctor"
```

> **Milestone PR.** After Task 7's review is clean, the executor opens the plan-5a PR (`plan-5-setup-doctor-skills` → `main`, title "Plan 5a: meld setup, meld doctor, rules consolidation") so the owner can try the app, then continues with Task 8 on the same branch (a second PR, or the same one updated, at the end).

### Task 8: Skills story part 1 — product skills, relevance matching, `skill:<name>` hints

**Files:**
- Modify: `src/meld/roster.py`, `src/meld/brief.py`, `src/meld/tagging.py`, `src/meld/dispatch.py`, `src/meld/cli.py`
- Test: `tests/test_roster.py`, `tests/test_brief.py`, `tests/test_tagging.py` (or wherever `tag` is tested), `tests/test_dispatch.py`

**Interfaces:**
- Consumes: `adapters.base.scan_skills(dirs, source)`, `roster.roster`, `brief.render_brief(skills=...)`, `brief._terms`, `Issue.labels`, `tagging.tag`.
- Produces: `roster.product_skills(repo_root) -> list[WorkerType]` (source `"product-skill"`, from `.meld/skills/<name>/SKILL.md`); `roster.roster(repo_root, extra)` merges them after product workers and before adapter skills; `Issue.skills -> tuple[str, ...]` (values of `skill:` labels, sorted); `brief.select_skills(text, skills, pinned=(), limit=MAX_SKILLS) -> list[WorkerType]`; `tagging.tag(..., skills: Sequence[str] | None = None)` adds `skill:<name>` labels (validated against the roster's skill names); CLI `meld tag N --skill NAME` (repeatable); `_dispatch_build`/`_dispatch_pr` pass `skills=select_skills(...)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_roster.py`:

```python
def test_product_skills_join_the_roster_as_product_skill(tmp_path):
    from meld.roster import roster, WorkerType
    d = tmp_path / ".meld" / "skills" / "deploy-checklist"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: deploy-checklist\ndescription: Pre-deploy steps for this product\n---\n# Deploy\n")
    entries = {w.name: w for w in roster(tmp_path, [WorkerType("deploy-checklist", "host version", "skill", None)])}
    assert entries["deploy-checklist"].source == "product-skill" and entries["deploy-checklist"].path == d
```

Append to `tests/test_brief.py`:

```python
def test_select_skills_matches_the_task_and_keeps_pinned_first():
    from meld.brief import select_skills
    from meld.roster import WorkerType
    skills = [WorkerType("pdf", "Read and write PDF files", "skill", None), WorkerType("stripe-billing", "Stripe payments and invoices", "skill", None),
              WorkerType("frontend-design", "Distinctive UI design", "skill", None)]
    chosen = select_skills("Add invoice download as PDF", skills, pinned=("frontend-design",))
    assert [s.name for s in chosen] == ["frontend-design", "pdf", "stripe-billing"]
    assert select_skills("Fix the CI cache key", skills) == []
    assert [s.name for s in select_skills("anything", skills, pinned=("nope", "pdf"))] == ["pdf"]


def test_brief_lists_only_selected_skills(tmp_path):
    from meld.brief import render_brief
    from meld.roster import WorkerType
    from tests.factories import mk_issue
    issue = mk_issue(11, title="Add invoice download as PDF")
    text = render_brief(role=BACKEND, kind="build", group="claude", model="m", effort="e", issue=issue, pr=None, plan_text="",
                        repo_root=tmp_path, worktree=tmp_path, base_branch="main",
                        skills=[WorkerType("pdf", "Read and write PDF files", "skill", None)])
    assert "`pdf`" in text and "## Skills available" in text
```

Append to the tagging tests:

```python
def test_tag_skill_adds_labels_from_the_roster(tmp_path):
    from meld.tagging import tag
    from tests.productfixtures import make_product
    from tests.fakeadapter import FakeAdapter
    from meld.roster import WorkerType
    fake, ctx, repo, origin = make_product(tmp_path, group="claude", role="orchestrator")
    ctx.adapter_for = lambda name: FakeAdapter(skills=[WorkerType("pdf", "PDF files", "skill", None)])
    ctx.refresh_roster()
    fake.route("POST", "repos/acme/widgets/issues/11/labels", [], status=200)
    out = tag(ctx, 11, skills=["pdf"])
    assert out["labels"]["skills"] == ["pdf"]
    posted = [c.body for c in fake.calls if c.method == "POST" and c.endpoint.endswith("issues/11/labels")][0]
    assert "skill:pdf" in posted["labels"]
    import pytest
    from meld.actions import Refused
    with pytest.raises(Refused, match="skill 'nope'"):
        tag(ctx, 11, skills=["nope"])
```

(Check how the existing tag tests route the labels endpoint and whether `make_product` accepts `role="orchestrator"`; mirror them.)

Append to `tests/test_dispatch.py`: a test that an issue labelled `skill:pdf` plus a title unrelated to PDFs still gets `pdf` in its brief's `## Skills available`, and an unrelated installed skill does not appear (use `FakeAdapter(skills=[...])` via `adapter=` on `dispatch`, read the brief written to the worktree).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_roster.py tests/test_brief.py -q` → FAIL (`ImportError: select_skills`, product skill missing).

- [ ] **Step 3: Implement**

`roster.py`:

```python
def product_skills(repo_root: Path) -> list[WorkerType]:
    """Skills the product ships under .meld/skills/<name>/SKILL.md (frontmatter name/description), shared by every group."""
    from meld.adapters.base import scan_skills   # local import: adapters.base imports roster
    d = repo_root / ".meld" / "skills"
    return scan_skills([d], source="product-skill") if d.is_dir() else []
```

and in `roster()` after product workers: `for w in product_skills(repo_root): by_name[w.name] = w` (a product skill overrides a same-named host skill; the `extra` loop's `setdefault` then keeps it).

`board.Issue`: add

```python
    @property
    def skills(self) -> tuple[str, ...]:
        return tuple(sorted(label[len("skill:"):] for label in self.labels if label.startswith("skill:")))
```

`brief.py`:

```python
def select_skills(text: str, skills: Sequence[WorkerType], pinned: Sequence[str] = (), limit: int = MAX_SKILLS) -> list[WorkerType]:
    """Skills worth naming in a brief: the orchestrator's pins first (skill:<name> labels), then every skill whose name or
    description shares a term with the task text, by score. Unmatched skills stay out: a 30-item list nobody reads is noise."""
    by_name = {s.name: s for s in skills}
    chosen = [by_name[n] for n in pinned if n in by_name]
    terms = set(_terms(text))
    scored = []
    for s in skills:
        if s.name in pinned:
            continue
        hay = set(_terms(f"{s.name.replace('-', ' ')} {s.description}"))
        score = len(terms & hay)
        if score:
            scored.append((-score, s.name, s))
    chosen += [s for _, _, s in sorted(scored)]
    return chosen[:limit]
```

`dispatch._dispatch_build`: replace `skills=[w for w in ctx.roster_entries() if w.source == "skill"]` with
`skills=select_skills(f"{issue.title}\n{issue.body}", [w for w in ctx.roster_entries() if w.source in ("skill", "product-skill")], pinned=issue.skills)`;
`_dispatch_pr`: `skills=select_skills(f"{pr.title}\n{pr.body}", [w for w in ctx.roster_entries() if w.source in ("skill", "product-skill")])`. Import `select_skills` from `meld.brief`.

`tagging.tag`: add `skills: Sequence[str] | None = None`; count it in the "provide at least one" check; for a non-human actor validate each name against `{w.name for w in ctx.roster_entries() if w.source in ("skill", "product-skill")}` with the refusal `f"skill '{name}' is not an installed or product skill for {group.name}"`; add `skill:<name>` labels (never removing existing `skill:` labels — pins accumulate; `--skill` with an empty string is a usage error); return `"skills": sorted(set(issue.skills) | set(skills or ()))` inside `labels`. CLI: `@click.option("--skill", "skills", multiple=True)`.

- [ ] **Step 4: Run the suite; commit**

Run: `uv run pytest -q` → green (existing brief tests that passed the whole roster as `skills=` still render).

```bash
git add src/meld/roster.py src/meld/board.py src/meld/brief.py src/meld/tagging.py src/meld/dispatch.py src/meld/cli.py tests/
git commit -m "feat: product skills join the roster, briefs list task-matched and pinned skills, meld tag --skill pins one"
```

### Task 9: Skills story part 2 — product skills materialised into each CLI's project skill directory

**Files:**
- Modify: `src/meld/adapters/base.py`, `src/meld/adapters/claude.py`, `src/meld/adapters/codex.py`, `src/meld/dispatch.py`, `src/meld/gitops.py`, `src/meld/prompts/_worker_contract.md`, `tests/fakeadapter.py`
- Test: `tests/test_dispatch.py`, `tests/test_adapters.py` (or the existing adapter test file)

**Interfaces:**
- Consumes: `roster.product_skills`, `gitops.exclude_paths(path, patterns)`, `dispatch._launch`.
- Produces: `Adapter.project_skills_dir() -> str | None` (`".claude/skills"` for Claude, `".agents/skills"` for Codex, `None` for an adapter with no project discovery; `FakeAdapter` returns `".fake/skills"`); `dispatch.materialise_skills(repo_root, worktree, adapter) -> list[str]` (relative dirs created, e.g. `[".claude/skills/deploy-checklist"]`); `gitops.MELD_SCRATCH_FILES` unchanged, `exclude_paths` called with the project dir added.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dispatch.py`:

```python
def test_dispatch_materialises_product_skills_into_the_adapters_project_dir(tmp_path):
    from meld.dispatch import dispatch
    from tests.fakeadapter import FakeAdapter
    from tests.gitfixtures import sh
    from tests.productfixtures import claim_issue_11, make_product
    fake, ctx, repo, origin = make_product(tmp_path)
    d = repo / ".meld" / "skills" / "deploy-checklist"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: deploy-checklist\ndescription: Pre-deploy steps\n---\n# Deploy\n")
    sh(["add", "."], repo); sh(["commit", "-q", "-m", "skills"], repo); sh(["push", "-q", "origin", "main"], repo)
    claim_issue_11(fake)
    out = dispatch(ctx, 11, adapter=FakeAdapter())
    wt = Path(out["worktree"])
    assert (wt / ".fake" / "skills" / "deploy-checklist" / "SKILL.md").read_text().startswith("---")
    assert ".fake/" in (wt / ".git").read_text() or True   # linked worktree: .git is a file; the exclude lives in the common dir
    exclude = sh(["rev-parse", "--git-path", "info/exclude"], wt)
    assert ".fake/skills/" in Path(exclude if exclude.startswith("/") else str(wt / exclude)).read_text()
    assert "deploy-checklist" in (wt / ".meld-brief.md").read_text()
```

Append to the adapter tests:

```python
def test_project_skills_dirs():
    from meld.adapters.claude import ADAPTER as claude
    from meld.adapters.codex import ADAPTER as codex
    assert claude.project_skills_dir() == ".claude/skills" and codex.project_skills_dir() == ".agents/skills"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_dispatch.py -q -k materialises` → FAIL (`AttributeError: project_skills_dir` or the directory is missing).

- [ ] **Step 3: Implement**

`adapters/base.py`: add to the Protocol `def project_skills_dir(self) -> str | None: ...`. `claude.py`: `return ".claude/skills"` (Claude Code loads project skills from `.claude/skills/<name>/SKILL.md`). `codex.py`: `return ".agents/skills"` (Codex discovers repo-level skills under `.agents/skills/`). `tests/fakeadapter.py`: `return ".fake/skills"`.

`dispatch.py`:

```python
def materialise_skills(repo_root: Path, worktree: Path, adapter: Adapter) -> list[str]:
    """Copy the product's .meld/skills/<name>/ into the worktree under the adapter's project skill directory, so the CLI
    discovers them natively (decision 14). Worktree-local and excluded from git via info/exclude: nothing is written under
    the host's home, and a worker cannot accidentally commit the copies."""
    target_dir = adapter.project_skills_dir()
    if target_dir is None:
        return []
    made: list[str] = []
    for skill in product_skills(repo_root):
        if skill.path is None:
            continue
        dest = worktree / target_dir / skill.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(skill.path, dest)
        made.append(f"{target_dir}/{skill.name}")
    if made:
        exclude_paths(worktree, (f"{target_dir}/",))
    return made
```

(`skill.path` from `scan_skills` is the skill's directory; confirm in `adapters/base.py` and adjust if it points at `SKILL.md` — copy the parent then.) Call it in `_dispatch_build` and `_dispatch_pr` right after `exclude_paths(wt)`, and add `"skills_materialised": made` to both result dicts. Import `product_skills` from `meld.roster`.

`_worker_contract.md` item 10: append the sentence "Product skills are also copied into this worktree under your CLI's project skill directory (`.claude/skills/` or `.agents/skills/`), so invoking them by name works; Codex workers open the `SKILL.md` at the path shown when invocation is unavailable." (Keep the substrings `tests/test_role_prompts.py` and `tests/test_brief.py` assert.)

- [ ] **Step 4: Run the suite; commit**

Run: `uv run pytest -q` → green.

```bash
git add src/meld/adapters/ src/meld/dispatch.py src/meld/prompts/_worker_contract.md tests/
git commit -m "feat: product skills are materialised into each CLI's project skill directory at dispatch"
```

### Task 10: Memory hardening — one marker regex, recall at `origin/<default>`, big files, lesson headings, REVIEW-REQUEST and status retries

**Files:**
- Modify: `src/meld/board.py`, `src/meld/memory.py`, `src/meld/brief.py`, `src/meld/dispatch.py`, `src/meld/github.py`, `src/meld/review.py`
- Test: `tests/test_memory.py`, `tests/test_brief.py`, `tests/test_github.py`, `tests/test_review.py`

**Interfaces:**
- Produces: `board.MEMORY_MARKER_RE` (the one regex; `memory.MARKER_RE` becomes an alias import); `brief._local_hits(repo_root, terms, ref=None)` and `brief.recall_snippets(repo_root, text, limit=3, ref=None)` — with `ref`, files are listed with `git ls-tree -r --name-only <ref> -- .meld/decisions .meld/memory` and read with `git show <ref>:<path>`; `memory.recall` fetches then passes `ref=f"origin/{cfg.default_branch}"`; `dispatch._dispatch_build/_dispatch_pr` pass the same ref (the worktree creation already fetched); `GitHub.get_contents` blob fallback; `memory.escape_headings(text) -> str`; `memory.decide` re-posts a missing `REVIEW-REQUEST` instead of refusing; `review.review` retries a stored `status_error` on a memory PR.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_github.py`:

```python
def test_get_contents_falls_back_to_the_blob_endpoint_for_large_files(board1_gh):
    import base64
    fake, gh = board1_gh
    fake.route("GET", "repos/acme/widgets/contents/.meld%2Fmemory%2Flessons.md", {"encoding": "none", "sha": "blob1", "size": 2_000_000}, query={"ref": "main"})
    fake.route("GET", "repos/acme/widgets/git/blobs/blob1", {"encoding": "base64", "content": base64.b64encode(b"# Lessons\n").decode()})
    assert gh.get_contents(".meld/memory/lessons.md", "main") == "# Lessons\n"
```

(Match the path encoding the existing `get_contents` test uses — it quotes each segment, so the routed path is `contents/.meld%2Fmemory%2Flessons.md`; copy the exact form from the existing test.)

Append to `tests/test_memory.py`:

```python
def test_remember_escapes_lesson_lines_that_start_with_a_heading_marker(tmp_path):
    from meld.memory import escape_headings, render_lesson
    text = "Cause\n## Not a heading\n# Neither\nFix"
    assert escape_headings(text) == "Cause\n\\## Not a heading\n\\# Neither\nFix"
    entry = render_lesson("#13", escape_headings(text), "claude", "2026-09-05")
    assert entry.count("\n## ") == 1


def test_recall_reads_origin_not_the_working_copy(tmp_path):
    from meld.brief import recall_snippets
    from tests.gitfixtures import make_origin_and_clone, sh
    origin, repo = make_origin_and_clone(tmp_path)
    other = tmp_path / "other"
    sh(["clone", "-q", str(origin), str(other)], tmp_path)
    d = other / ".meld" / "decisions"
    d.mkdir(parents=True)
    (d / "0001-dec-1.md").write_text("# Decision dec-1\n\n## Decision\n\nUse Postgres for the ledger.\n")
    sh(["add", "."], other); sh(["-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "-m", "d"], other); sh(["push", "-q", "origin", "main"], other)
    sh(["fetch", "-q", "origin"], repo)
    assert recall_snippets(repo, "postgres ledger") == []                      # working copy has no .meld yet
    hits = recall_snippets(repo, "postgres ledger", ref="origin/main")
    assert hits and "Postgres" in hits[0]


def test_decide_reposts_a_missing_review_request_instead_of_refusing(tmp_path):
    from meld.grammar import agreement_for
    from meld.memory import comment_url, decide, render_decision
    from tests.test_review import memory_pr_on_board
    fake, ctx, repo, origin = make_product(tmp_path)
    with_thread(fake, DECISION_THREAD)
    _, board = build_status(ctx)
    agreement = agreement_for(board.comments, "dec-1", ctx.cfg)
    joint = [c for c in board.comments if c.ok and c.header.party.kind == "joint" and c.header.ref == "dec-1"][-1]
    text = render_decision("dec-1", agreement, comment_url(board, joint.id), "2026-09-05")
    memory_pr_on_board(fake, repo, ".meld/decisions/0001-dec-1.md", text)   # open memory PR #40; no REVIEW-REQUEST on the thread
    out = decide(ctx, "dec-1")
    assert out == {"decided": "dec-1", "pr": 40, "review_requested": True}
    posts = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint.endswith("issues/10/comments")]
    assert len(posts) == 1 and posts[0].startswith("[claude:pm] REVIEW-REQUEST #40")
    with_thread(fake, [{"id": 999, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-05T12:00:00Z", "body": posts[0]}])
    with pytest.raises(Refused, match="already recorded"):
        decide(ctx, "dec-1")
```

(`make_product`, `build_status`, `with_thread`, `DECISION_THREAD`, `Refused`, and `pytest` are already imported in `tests/test_memory.py`; the raw comment dicts follow GitHub's shape as `DECISION_THREAD` does.)

Append to `tests/test_review.py`:

```python
def test_memory_review_retries_a_failed_status_post_on_the_next_call(tmp_path):
    from meld.grammar import agreement_for
    from meld.memory import comment_url, render_decision
    from meld.results import load_result
    from tests.test_memory import DECISION_THREAD, with_thread
    fake, ctx, repo, origin = make_product(tmp_path, group="codex")          # the other group's PM reviews claude's memory PR
    with_thread(fake, DECISION_THREAD)
    _, board = build_status(ctx)
    agreement = agreement_for(board.comments, "dec-1", ctx.cfg)
    joint = [c for c in board.comments if c.ok and c.header.party.kind == "joint" and c.header.ref == "dec-1"][-1]
    text = render_decision("dec-1", agreement, comment_url(board, joint.id), "2026-09-05")
    memory_pr_on_board(fake, repo, ".meld/decisions/0001-dec-1.md", text)
    fake.route("POST", "repos/acme/widgets/statuses/mem0001", {"message": "boom"}, status=500)
    first = review(ctx, 40, now=NOW)
    assert first["event"] == "APPROVE" and first["status_error"]
    assert load_result(ctx.store.home, "codex", "review", 40)["status_error"]
    fake.route("POST", "repos/acme/widgets/statuses/mem0001", {"id": 1}, status=201)
    second = review(ctx, 40, now=NOW)
    assert second["event"] == "APPROVE" and second.get("status_retried") is True and "status_error" not in second
    assert len(calls(fake, "POST", "pulls/40/reviews")) == 1 and len(calls(fake, "POST", "statuses/mem0001")) == 2
    assert not load_result(ctx.store.home, "codex", "review", 40).get("status_error")
```

(`memory_pr_on_board`, `calls`, `NOW`, `review`, `make_product`, `build_status` are the helpers `tests/test_review.py` already defines or imports.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_github.py tests/test_memory.py tests/test_review.py -q` → the new tests fail.

- [ ] **Step 3: Implement**

- `board.py`: `MEMORY_MARKER_RE = re.compile(r"^meld-memory: (decision|lesson) (\S+) ([0-9a-f]{64})$", re.MULTILINE)` next to `MEMORY_BRANCH_PREFIX`; `PullRequest.memory` uses it; `memory.py`: `from meld.board import MEMORY_MARKER_RE as MARKER_RE` (delete the local definition).
- `github.get_contents`: after the base64 branch add

```python
        if isinstance(resp, dict) and resp.get("encoding") == "none" and resp.get("sha"):
            # Files over 1 MB come back without content; the blob endpoint serves up to 100 MB (spec 21 item 23).
            blob = self.get(self._r(f"git/blobs/{resp['sha']}"), use_cache=False)
            if isinstance(blob, dict) and blob.get("encoding") == "base64":
                return base64.b64decode(blob.get("content", "")).decode("utf-8")
```

- `brief._local_hits(repo_root, terms, ref=None)`: when `ref` is given, list `git(["ls-tree", "-r", "--name-only", ref, "--", DECISIONS_DIR, ".meld/memory"], repo_root)` (catch `GitError` → `[]`) and read each with `git(["show", f"{ref}:{path}"], repo_root)`; otherwise keep the working-copy read. `recall_snippets(..., ref=None)` passes it through. (`brief.py` already imports from `gitops`; `DECISIONS_DIR`/`LESSONS_FILE` literals are duplicated there — reuse whatever names `_local_hits` already uses.)
- `memory.recall`: `git(["fetch", "--quiet", "origin"], ctx.repo_root)` (catch `GitError` and fall back to the working copy with a `"note"` in the result) then `_local_hits(ctx.repo_root, terms, ref=f"origin/{ctx.cfg.default_branch}")`; update the docstring (it no longer lags). `dispatch`: pass `ref=f"origin/{ctx.cfg.default_branch}"` to both `recall_snippets` calls (the worktree creation fetched already).
- `memory.escape_headings(text)`: `return "\n".join(("\\" + line) if line.startswith("#") else line for line in text.splitlines())`; `remember` applies it after `neutralise_closers`.
- `memory.decide`: when `decide_rule` returns the "already recorded or has an open memory PR" rule, check whether the open memory PR for `("decision", ref)` has a `[<group>:pm] REVIEW-REQUEST #<pr>` on the coordination issue (`any(c.ok and c.header.verb == "REVIEW-REQUEST" and c.header.ref == f"#{pr.number}" for c in board.comments)`); if not, call `_request_review(...)` and return `{"decided": ref, "pr": pr.number, "review_requested": True}`; otherwise refuse as today.
- `review.review` memory branch: when `cached` matches head and event `APPROVE` but `cached.get("status_error")`, retry `create_status` once, clear the field with `update_result(..., {"status_error": None})` on success, and return with `"status_retried": True`; store `status_error` in the record on failure (it is currently only returned).

- [ ] **Step 4: Run the suite; commit**

Run: `uv run pytest -q` → green.

```bash
git add src/meld/board.py src/meld/memory.py src/meld/brief.py src/meld/dispatch.py src/meld/github.py src/meld/review.py tests/
git commit -m "fix: memory reads origin, survives large lesson files and heading lines, and retries its own follow-ups"
```

### Task 11: Housekeeping prunes, one nudge per window, BUDGET refs

**Files:**
- Modify: `src/meld/housekeeping.py`, `src/meld/notifier.py`, `src/meld/chat.py`, `src/meld/prompts/pm.md`, `src/meld/grammar.py`
- Test: `tests/test_housekeeping.py`, `tests/test_notifier.py`, `tests/test_tick.py`, `tests/test_chat.py`

**Interfaces:**
- Consumes: `board.NUDGED_LABEL`, `GitHub.issue_events/add_labels/remove_label`, `Board.pulls`, `chat.inbox`.
- Produces: `housekeeping.ci_red_sweep` drops records for PRs no longer open (returned as `"pruned": [pr numbers]` on a trailing summary item? No — keep the list of dicts and add pruning as a side effect plus a `pruned` key on each pruned record's absence; simplest: the function returns the list as today and prunes silently, logged in the returned items as `{"pr": n, "pruned": True}`); `notifier.last_nudge(events, bots) -> datetime | None`; `notifier.notify(ctx, board, now, *, post=None, env=None, dry_run=False, events=None) -> dict` — `events(number) -> list[dict]` defaults to `ctx.gh.issue_events`; the label `meld:nudged` is added after a successful send and removed by `notify` for issues no longer `needs_human`; `NOTIFY_STATE` and the per-party file are deleted; `chat.inbox` budget items carry `"ref"` and clear only on a later human non-ASK comment with the same ref (a ref-less BUDGET keeps the old rule); `grammar.REQUIRES_REF` unchanged (`BUDGET` ref stays optional).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_housekeeping.py`:

```python
def test_ci_red_sweep_prunes_records_for_prs_that_left_the_board(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    no_events(fake)
    path = ctx.store.state_dir / CI_RED_STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"20": {"head": "abc123", "commented": True, "swapped": True},
                                "99": {"head": "old", "commented": True, "swapped": True}}))
    _, board = build_status(ctx)
    out = ci_red_sweep(ctx, board, ctx.cfg.groups["claude"])
    assert {"pr": 99, "pruned": True} in out
    remaining = json.loads(path.read_text())
    assert "20" in remaining and "99" not in remaining
```

Replace the throttle tests in `tests/test_notifier.py` (the ones that read `notify.json`) with:

```python
def events_for(fake, number, stamps):
    fake.route("GET", f"repos/acme/widgets/issues/{number}/events",
               [{"id": 500 + i, "event": "labeled", "label": {"name": "meld:nudged"}, "actor": {"login": "meld-codex-bot"}, "created_at": s} for i, s in enumerate(stamps)])


def test_notify_sends_once_and_repeats_after_the_configured_delay_using_the_shared_label(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    events_for(fake, 14, [])
    fake.route("POST", "repos/acme/widgets/issues/14/labels", [], status=200)
    sent = []
    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    _, board = build_status(ctx)
    out = notify(ctx, board, now, post=lambda url, payload: sent.append(payload), env={"MELD_SLACK_DM_WEBHOOK": "https://hooks.example/x"})
    assert out["notified"] == [14] and len(sent) == 1
    labels = [c.body["labels"] for c in fake.calls if c.method == "POST" and c.endpoint.endswith("issues/14/labels")]
    assert labels == [["meld:nudged"]]
    events_for(fake, 14, ["2026-09-05T12:00:00Z"])            # what GitHub now reports, for ANY party
    other_party_ctx = make_product(tmp_path / "codex", group="codex")[1]   # a different PM, different state dir
    out2 = notify(other_party_ctx, board, now + timedelta(minutes=30), post=lambda u, p: sent.append(p), env={"MELD_SLACK_DM_WEBHOOK": "https://hooks.example/x"})
    assert out2["notified"] == [] and len(sent) == 1
    out3 = notify(ctx, board, now + timedelta(minutes=121), post=lambda u, p: sent.append(p), env={"MELD_SLACK_DM_WEBHOOK": "https://hooks.example/x"})
    assert out3["notified"] == [14] and len(sent) == 2


def test_notify_removes_the_label_when_the_item_no_longer_needs_a_human(tmp_path):
    from tests.productfixtures import fixture
    fake, ctx, repo, origin = make_product(tmp_path)
    issues = fixture("issues.json")
    for issue in issues:
        if issue["number"] == 11:                                   # ready, but still carries the nudge label
            issue["labels"].append({"name": "meld:nudged"})
    fake.route("GET", "repos/acme/widgets/issues", issues)
    events_for(fake, 14, [])
    fake.route("POST", "repos/acme/widgets/issues/14/labels", [], status=200)
    fake.route("DELETE", "repos/acme/widgets/issues/11/labels/meld%3Anudged", {}, status=200)
    fake.route("DELETE", "repos/acme/widgets/issues/11/labels/meld:nudged", {}, status=200)   # whichever encoding remove_label uses
    _, board = build_status(ctx)
    out = notify(ctx, board, datetime(2026, 9, 5, 12, tzinfo=timezone.utc), post=lambda u, p: None, env={"MELD_SLACK_DM_WEBHOOK": "https://hooks.example/x"})
    assert out["cleared"] == [11] and out["notified"] == [14]
    assert any(c.method == "DELETE" and "issues/11/labels/" in c.endpoint and "nudged" in c.endpoint for c in fake.calls)
```

(The second ctx must point at the same FakeGh: construct it with `make_product` then replace `ctx.gh` with the first context's `gh`, or route the same responses into the second fake — the assertion is that the *label events*, not a local file, gate the repeat.)

Append to `tests/test_chat.py`:

```python
def test_budget_items_clear_only_on_a_human_answer_to_their_ref():
    from tests.factories import CFG2, mk_board, mk_comment
    from meld.chat import inbox
    comments = [
        mk_comment(1, "meld-claude-bot", "[claude:pm] BUDGET budget-1\n\nmedium tier at 90%", "2026-09-05T10:00:00Z"),
        mk_comment(2, "meld-codex-bot", "[codex:pm] BUDGET budget-2\n\nlarge tier at 95%", "2026-09-05T10:01:00Z"),
        mk_comment(3, "empowerScott", "[human] DIRECTIVE budget-1\n\nraise medium to 120", "2026-09-05T10:02:00Z"),
    ]
    box = inbox(mk_board(comments=comments), CFG2, datetime(2026, 9, 5, 11, tzinfo=timezone.utc))
    assert [b["ref"] for b in box["budget"]] == ["budget-2"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_housekeeping.py tests/test_notifier.py tests/test_chat.py -q` → the new tests fail.

- [ ] **Step 3: Implement**

`housekeeping.ci_red_sweep`, at the end before `return out`:

```python
    open_prs = {str(n) for n, p in board.pulls.items() if p.state == "open"}
    stale = [k for k in raw if k not in open_prs]
    if stale:
        for k in stale:
            raw.pop(k, None)
            out.append({"pr": int(k) if k.isdigit() else k, "pruned": True})
        try:
            _save(path, raw)
        except OSError:
            pass
```

`notifier.py` — replace the state-file throttle:

```python
def last_nudge(events: list[dict], bots: set[str]) -> datetime | None:
    """The newest `labeled meld:nudged` event by a configured bot (a human adding the label by hand does not count as a nudge)."""
    stamps = [_stamp(e.get("created_at")) for e in events
              if e.get("event") == "labeled" and (e.get("label") or {}).get("name") == NUDGED_LABEL and (e.get("actor") or {}).get("login") in bots]
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None
```

In `notify`: compute `bots = {g.bot for g in ctx.cfg.groups.values()}`; for each `needs_human` issue call `events = (events_fn or ctx.gh.issue_events)(number)` inside `try/except GitHubError` (a failed fetch makes that item *not due* this call and adds an error — never a duplicate nudge); `due` when `last_nudge(...) is None` or older than the window; after a successful send, `ctx.gh.add_labels(n, [NUDGED_LABEL])` for each notified issue (contained: a failed label add is reported in `errors` — the next call may re-nudge once, which is the documented cost); for issues carrying `NUDGED_LABEL` whose status is not `needs_human`, `ctx.gh.remove_label(n, NUDGED_LABEL)` and report `"cleared": [n]`. Delete `NOTIFY_STATE`, `_load_state`, `_persist`, and the pruning code; keep `_stamp`, `slack_text`, `_post_webhook`, the containment, and the `dry_run` early return (dry run still reads events, sends nothing, labels nothing). `tick` keeps passing `env`/`post` through.

`chat.inbox` budget block:

```python
    budget = []
    for c in comments:
        if c.header.verb != "BUDGET":
            continue
        ref = c.header.ref
        answered = (any(h.id > c.id and h.header.party.kind == "human" and h.header.verb != "ASK" and h.header.ref == ref for h in comments) if ref
                    else any(h.id > c.id and h.header.party.kind == "human" and h.header.verb != "ASK" for h in comments))
        if not answered:
            budget.append({"group": c.header.party.group, "ref": ref, "comment_id": c.id, "age_minutes": _age_minutes(c.created_at, now), "text": c.text})
```

`pm.md`: where the PM is told to post `BUDGET`, say "post `[<group>:pm] BUDGET budget-<n>` (next free number) so the human can answer that ref". `_render_inbox`: show the ref.

- [ ] **Step 4: Run the suite; commit**

Run: `uv run pytest -q` → green (`tests/test_tick.py`'s notifier assertions move from the state file to the label POST).

```bash
git add src/meld/housekeeping.py src/meld/notifier.py src/meld/chat.py src/meld/prompts/pm.md tests/
git commit -m "fix: ci-red records prune, nudges throttle through a shared label, BUDGET items answer to a ref"
```

### Task 12: Merge commit messages, detached processes, the milestone-end audit, and the plan-4 nits

**Files:**
- Modify: `src/meld/github.py`, `src/meld/review.py`, `src/meld/adapters/base.py`, `src/meld/status.py`, `src/meld/board.py`, `src/meld/state.py`, `src/meld/memory.py`
- Test: `tests/test_review.py`, `tests/test_adapters.py` (or `tests/test_runners.py`), `tests/test_status.py`, `tests/test_github.py`

**Interfaces:**
- Produces: `GitHub.merge_pull(number, sha, title, method="squash", message=None)` sends `commit_message` when given; `review.merge` passes `pr.body`; `adapters.base.DetachedPopen(subprocess.Popen)` with a no-op `__del__`, returned by `popen_detached`; `status.audit_summary` sets `due` when `milestone_complete` (no open non-coordination issue) and audits are pending, and reports `"milestone_complete": bool`; `GitHub.create_status`/`create_review` keep positional compatibility but gain keyword-only `*` after `number`/`sha`? No — keep signatures; the item-17 nit is closed by documenting the parameter with `Literal` types instead: `state: Literal["success", "failure", "pending", "error"]`, `event: Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_review.py`:

```python
def test_merge_sends_the_pr_body_as_the_squash_message(tmp_path):
    # Arrange exactly as the existing merge happy-path test in this file does (same fixture routing, same reviewing group),
    # then assert the PUT body carries the PR body as the squash message (spec 21 item 5).
    fake, ctx = _merge_ready(tmp_path)          # extract the existing test's arrangement into this helper if it is inline
    _, board = build_status(ctx)
    out = merge(ctx, 20)
    assert out["merged"] == 20
    put = [c for c in fake.calls if c.method == "PUT" and c.endpoint.endswith("pulls/20/merge")][0]
    assert put.body["commit_message"] == board.pulls[20].body and put.body["merge_method"] == "squash"
```

Append to the adapter/runners tests:

```python
def test_popen_detached_never_warns_when_collected_before_the_child_exits(tmp_path):
    import gc, subprocess, sys, warnings
    from meld.adapters.base import DetachedPopen, LaunchSpec, open_log, popen_detached
    spec = LaunchSpec(role="worker", model="m", effort="e", cwd=tmp_path, prompt="", log_path=tmp_path / "log", timeout_seconds=5, env={}, max_turns=1)
    with open_log(spec.log_path) as log:
        proc = popen_detached([sys.executable, "-c", "import time; time.sleep(0.5)"], spec, log)
    assert isinstance(proc, DetachedPopen)
    pid = proc.pid
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        del proc
        gc.collect()
    import os, time
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
```

Append to `tests/test_status.py`:

```python
def test_audit_is_due_when_the_milestone_is_complete():
    from datetime import datetime, timezone
    from meld.config import Party
    from meld.rules import Actor
    from meld.status import audit_summary
    from tests.factories import CFG2, mk_board, mk_issue, mk_pr
    merged = mk_pr(19, 9, author="meld-codex-bot", state="closed", merged=True)
    board = mk_board(issues={9: mk_issue(9, "ready", state="closed")}, pulls={19: merged})
    out = audit_summary(board, CFG2, Actor(Party("pm", "claude"), "meld-claude-bot"), datetime(2026, 9, 5, tzinfo=timezone.utc))
    assert out["pending"] == [19] and out["milestone_complete"] is True and out["due"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_review.py tests/test_status.py -q -k "squash or complete"` → FAIL.

- [ ] **Step 3: Implement**

- `github.merge_pull(self, number, sha, title, method="squash", message: str | None = None)`: add `"commit_message": message` to the body when `message is not None`. `review.merge`: `ctx.gh.merge_pull(number, pr.head_sha, pr.title, message=pr.body)` with the comment "the PR body (already closer-neutralised by collect, except the framework's own `Closes #N` line) becomes the squash message, so a worker's commit messages never reach the default branch (spec 21 item 5)".
- `adapters/base.py`:

```python
class DetachedPopen(subprocess.Popen):
    """A child we deliberately never wait on (workers outlive the meld process that launched them). Popen.__del__ would
    warn 'subprocess N is still running' and register the object for later reaping; meld reaps by pid through
    state.pid_alive's waitpid instead, so the destructor is a no-op here (spec 21 item 6)."""

    def __del__(self) -> None:  # noqa: D401
        pass


def popen_detached(argv: list[str], spec: LaunchSpec, log: IO[bytes]) -> subprocess.Popen:
    """Start the CLI in its own session so a timeout can kill the whole process group."""
    return DetachedPopen(argv, cwd=str(spec.cwd), env=spec.env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
```

- `status.audit_summary`: compute `complete = all(i.state == "closed" for i in board.issues.values())` (the coordination issue is not in `board.issues`) and `due = bool(pending) and (len(pending) >= cfg.audit_batch_merges or (hours is not None and hours >= cfg.audit_batch_hours) or complete)`; add `"milestone_complete": complete` to the result. Update the docstring (spec 21 item 10).
- Nits (spec 21 item 17): `github.create_status`/`create_review` type `state`/`event` as `Literal[...]` (import from `typing`); `tests/test_github.py`: move the mid-file `import base64` to the top; `github.get_contents`'s final error names the payload: `f"unexpected payload {type(resp).__name__}: {str(resp)[:200]}"`; `board.py` closed-issue disjunct: in `grammar.agreement_for`'s `closed = ...` keep only `(agreed | set(dissent)) >= set(cfg.group_names)` (the first disjunct is implied); `state.diff_changes`: the challenges diff text prints `{}` for a missing challenge like audits do; `memory._open_memory_pr`: wrap the `finally`'s `remove_worktree` in `try/except GitError` that logs to `ctx.store.errors` so it cannot mask the original exception.

- [ ] **Step 4: Run the suite (also under `-W error` for the adapter tests); commit**

Run: `uv run pytest -q && uv run pytest -q -W error tests/test_adapters.py tests/test_runners.py tests/test_dispatch.py tests/test_collect.py tests/test_review.py` → green; the `ResourceWarning` class is gone from the worker-spawning tests.

```bash
git add src/meld/github.py src/meld/review.py src/meld/adapters/base.py src/meld/status.py src/meld/grammar.py src/meld/state.py src/meld/memory.py tests/
git commit -m "fix: squash messages exclude worker commits, detached children never warn, audits are due at milestone end"
```

### Task 13: Spec, README, and handoff reconciliation

**Files:**
- Modify: `docs/superpowers/specs/2026-09-03-meld-design.md`, `README.md`, `docs/HANDOFF.md`, `AGENTS.md` (only if a convention changed)

**Interfaces:** documentation only.

- [ ] **Step 1: Reconcile the spec with what shipped**

For every "Carried into plan 5" item 1–28 in §21, append a `**Landed in plan 5:**` (or `**Closed in plan 5 by measurement/decision:**`, or `**Stays open:**`) sentence naming the module and behaviour, exactly as §21 did for plan 4:
- 1 `meld setup` (Tasks 3–4) — labels, milestone, coordination issue, starter `.meld.toml`, CODEOWNERS, `.meld/STANDARDS.md`, branch protection with statuses informational, CI-paths warning, `--pr`, public-repo refusal.
- 2 `meld doctor` (Tasks 5–6) — the checklist as implemented, the opt-in status probe, `--sweep`.
- 3 skills (Tasks 8–9). 4 `RELEASED` (Task 2). 5 squash message (Task 12). 6 `DetachedPopen` (Task 12). 7, 8 (Task 2, Task 1). 9 tie-break (Task 2). 10 milestone-end audit (Task 12). 11 status retry (Task 10). 12 ci-red pruning (Task 11). 13 closed by the 2 MB doctor warning (decision 11). 14 `MEMORY_MARKER_RE` (Task 10). 15 and 24 shared `meld:nudged` throttle (Task 11). 16 landed in plan 4. 17 nits (Task 12). 18 rules consolidation (Task 1). 19 recall at origin (Task 10). 20 BUDGET refs (Task 11). 21 memory-review records swept (Task 6). 22 stays documented (decision 17). 23 blob fallback (Task 10). 25 REVIEW-REQUEST re-post (Task 10). 26 landed in plan 4's fix wave. 27 verified joint sticks (Task 2). 28 heading escape (Task 10).
Then update the sections those items touched: §5 (`meld setup` fills the model fields — now true), §7 (`RELEASED`, BUDGET refs, verified joint sticks), §8 (`plan`/`decide`/`remember` in `legal`; `remember` on issues and PRs), §10 (product skills, `skill:` labels, project skill directories), §11 (`RELEASED` ends a lease), §13 (the author group's request-changes counts; squash message), §14 (shared nudge label; milestone-end audit), §15 (recall reads origin; heading escape; blob fallback), §16 (`project_skills_dir`, `version_argv`), §18 (doctor's settings-env and origin checks), §20 (bootstrap steps now match the commands). Add a new **"Carried into plan 6"** list with whatever the plan-5 reviews defer (start it with: login-status checks per CLI in `meld doctor`; a memory index if the 2 MB warning ever fires; §21 item 22).

- [ ] **Step 2: README and handoff**

- README: the Quick start from Task 7 is verified against the real commands; add a short "Skills" subsection (`.meld/skills/`, `meld tag --skill`) and a "Doctor" subsection (`meld doctor`, `--probe-status`, `--sweep --dry-run`).
- `docs/HANDOFF.md`: rewrite for the plan-5 milestone: state (plan 5 complete on the branch, test count), authoritative docs in order, repo map with `setup.py`/`doctor.py`, how to run and test (Quick start), the SDD process used, "Decisions for the owner" (plan 5's decisions 1–17 above, each with the not-taken alternative and cost if wrong), what is next (plan 6 = the "Carried into plan 6" list plus the third adapter and webhooks from §21's v1 exclusions), git/identity conventions. `AGENTS.md`: add `meld setup`/`meld doctor` to the command inventory only if it lists commands.

- [ ] **Step 3: Verify and commit**

Run: `uv run pytest -q` (docs cannot break it; confirm the count for HANDOFF) and `grep -n "Carried into plan 5" -A 60 docs/superpowers/specs/2026-09-03-meld-design.md | grep -c "Landed in plan 5\|Closed in plan 5\|Stays open"` → 28 items accounted for.

```bash
git add docs/superpowers/specs/2026-09-03-meld-design.md README.md docs/HANDOFF.md AGENTS.md
git commit -m "docs: reconcile the spec, README, and handoff with plan 5; list plan-6 obligations"
```

---

## Self-review (run before dispatching Task 1)

- **Spec coverage:** §21 items 1–28 each map to a task above (see Task 13's list); §20's bootstrap steps 2–3 map to Tasks 3–6; §10's roster sources to Task 8; §16's adapter interface additions to Tasks 5 and 9.
- **Type consistency:** `Check(name, ok, detail, fix)`; `run_checks(cfg, repo_root, *, env, gh_factory, registry, adapter_for, which, home, probe_status, user_home, run)`; `sweep(home, cfg, *, repo_root, pull_states, older_than_days, apply, now)`; `setup(gh, *, repo, goal, milestone_title, human, target_dir, registry, allow_public, which, force, protect, pr, run)`; `select_skills(text, skills, pinned, limit)`; `materialise_skills(repo_root, worktree, adapter)`; `notify(ctx, board, now, *, post, env, dry_run, events)`; `merge_pull(number, sha, title, method, message)`; `_local_hits(repo_root, terms, ref)`; `recall_snippets(repo_root, text, limit, ref)` — these names are used identically wherever they appear.
- **Placeholders:** the tests marked `...` in Tasks 10–12 name the existing test they mirror and the exact assertions to make; the implementer writes them in full (the review checks that they exist and assert what the plan says).
