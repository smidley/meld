# Meld Plan 2: Execution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Meld run work: adapters that launch Claude Code and Codex CLI as sandboxed headless processes, `dispatch`/`tick`/`collect`/`reap` that turn a claimed issue into a pushed branch and an open PR, lease-plus-heartbeat claims, the real orchestrator/PM/worker prompts, and `meld run <group>` supervising a group's two long-lived roles.

**Architecture:** Plan 1's rules engine stays the only authority on legality; every new mutating verb re-checks it first. `adapters/` are the only modules that know a CLI's name or flags; `gitops.py` is the only module that runs `git`; `dispatch.py` is the only module that spawns workers or touches worktrees. Workers receive a rendered `.meld-brief.md`, a scrubbed environment with no GitHub credentials, and a fixed contract to leave `.meld-result.json`; the framework does every GitHub side effect from that file. Claims carry a lease recorded in an editable `LEASE` comment on the coordination issue that the PM renews each cycle and any PM may expire.

**Tech Stack:** Python 3.13 (pinned), `uv`, `click` (only runtime dependency), `pytest`, `git`, `gh` (system dependencies), stdlib `subprocess`, `dataclasses`, `json`, `re`, `signal`.

**Spec:** `docs/superpowers/specs/2026-09-03-meld-design.md` — sections 9, 10, 11, 12, 16, 17, and 21 (borrowed items 1, 2, 5 and carried item 4). Plan 1's plan (`docs/superpowers/plans/2026-09-03-plan-1-coordination-core.md`) documents the modules this plan builds on; the exact current signatures are in the "Interfaces from plan 1" section below.

## Global Constraints

- Python `>=3.12`, pinned to 3.13 by `.python-version`; run everything as `uv run ...`. pytest is configured with `pythonpath = ["src"]`.
- Only runtime dependency is `click>=8.1`. Dev dependency: `pytest>=8`. No others.
- Every GitHub call goes through `meld.github.GitHub`. Every `git` invocation goes through `meld.gitops.git`. Only `meld/adapters/*` may name a CLI (`claude`, `codex`) or its flags.
- Every mutating verb re-checks `meld.rules` before acting and refuses with the named rule; rule-level refusals post `PROTOCOL-VIOLATION` via `meld.actions.refuse` and exit 2.
- Workers never hold GitHub credentials: the launch environment is scrubbed of `GH_TOKEN`, `GITHUB_TOKEN`, every `MELD_TOKEN_*`, `MELD_GROUP`, and `MELD_ROLE`. Workers never push, open PRs, comment, label, or merge; `collect` does that from `.meld-result.json`.
- Coordination comments start with `[<party>] <VERB> [<ref>] [<qualifier>]`; only `meld` writes `[joint]`.
- Tests run with no network, no real `claude`/`codex`, and no real GitHub: `FakeGh` for `gh`, a `FakeAdapter` for CLIs, real `git` against temporary repositories. Only `tests/smoke/` (opt-in via `MELD_SMOKE=1`) runs the real CLIs.
- Commit messages are plain conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`). No AI attribution, no `Co-Authored-By` lines.
- Work on branch `plan-2-execution` in `~/Documents/meld` (already created from `main` at `25226a9`); never commit to `main`.
- Decisions made for this plan, to be recorded in the spec by Task 17: a lease lives in a `LEASE #<n>` comment by the owning PM on the coordination issue and is renewed by editing that comment in place; `EXPIRED #<n>` is the PM verb for releasing another group's expired claim; reviewer and auditor runs are dispatched and collected here but their verdicts are stored under `$MELD_HOME/results/` for plan 3's `review`/`audit` verbs to post; PID reuse is not detected in this plan (a run record's `pid` plus `deadline` is the liveness signal); a worker's effort is passed to the CLI where the CLI accepts it and is otherwise stated in the brief.

## Interfaces from plan 1 (exact, current)

- `meld.config`: `Config` (frozen dataclass; fields include `repo_name`, `default_branch`, `humans`, `groups: dict[str, GroupConfig]`, `timeouts: dict[str, str]` with keys `S/M/L`, `budget`, `poll_interval_seconds`, `wait_timeout_seconds`, `restart_hours`, `roster_refresh_minutes`, `silence_grace_minutes`, `summary_cadence_minutes`, `effective_quorum` property, `group_for_bot(login)`, `is_human(login)`, `Config.from_dict(data)`); `GroupConfig(name, cli, bot, token_env, concurrency, review_concurrency, orchestrator: RoleConfig, pm: RoleConfig)`; `RoleConfig(model, effort)`; `Party(kind, group)` with `.label`; `party_from_env(cfg, env)`; `ConfigError`.
- `meld.github.GitHub(repo, token=None, run=subprocess.run, cache_dir=None)`: `get(endpoint, params=None, *, use_cache=True)`, `get_paginated`, `post`, `patch`, `delete`, `get_issue(number, fresh=False)`, `list_comments(issue, since=None)`, `create_comment(number, body)`, `add_labels`, `remove_label`, `add_assignees`, `remove_assignees`, `list_pulls(state)`, `pull_reviews`, `pull_commits`, `check_runs`, `viewer_login`; `GitHubError`, `RateLimited`.
- `meld.grammar`: `Header(party, verb, ref, qualifier)`, `ParsedComment(id, author, created_at, body, header, authorized, errors)` with `.text`, `.ok`; `parse_comment(raw, cfg)`, `compose(header, text)`, `allowed_verbs(party)`, `validate(header, body)`; constants `ORCHESTRATOR_VERBS`, `PM_VERBS`, `HUMAN_VERBS`, `JOINT_VERBS`, `REQUIRES_REF`, `REQUIRES_VERIFIED_BY`, `AGREEMENT_VERBS`; `agreement_for(comments, ref, cfg) -> Agreement`.
- `meld.board`: `Issue(number, title, body, labels, assignees, state, milestone, created_at="", updated_at="")` with `.status`, `.worker`, `.model`, `.effort`, `.size`, `.challenged`; `PullRequest(..., head_committer: str | None, issue_number, reviews, checks, labels, state, merged)`; `RunRecord(group, issue, kind, pr, pid, started_at, worktree, log_path, model, effort, tier, milestone, alive, collected)` with `.finished`; `Board(repo, milestone, coordination_issue, phase, halted, issues, pulls, comments, runs, spend, open_challenges, ignored_comments)` with `.in_progress_count(bot)`, `.pr_for_issue(n)`, `.run_for(group, issue)`, `.is_challenged(ref)`; label constants `STATUS_LABELS`, `CHALLENGED_LABEL`, `COORDINATION_LABEL`, `PLAN_APPROVED_LABEL`.
- `meld.state`: `meld_home(env)`, `pid_alive(pid)`, `StateStore(home, group, role=None)` with `.cache_dir`, `.state_dir`, `.runs_root`, `.errors`, `.load_snapshot()`, `.save_snapshot(d)`, `.write_run(record) -> Path`, `.load_runs(alive=pid_alive)`, `.load_spend(milestone)`; `board_digest(board)`, `diff_digest(old, new) -> list[str]`.
- `meld.atomic.write_atomic(path, text)`.
- `meld.assemble`: `build_board(gh, cfg, runs, spend, milestone=None)`, `find_active_milestone(gh)`, `checks_state`, `issue_number_from_pr`, `BoardError`, `BRANCH_PREFIX = "meld/"`.
- `meld.rules`: `Verb` (CLAIM, RELEASE, DISPATCH, COLLECT, REOPEN, APPROVE, REQUEST_CHANGES, PUSH_FIX, MERGE, POST), `Actor(party, login, roster)`, `Refusal(verb, target, rule)`, `issue_refusal(board, cfg, actor, verb, issue, models)`, `legal_issue_verbs`, `pr_refusal`, `legal_pr_verbs`, `party_of_login`, `committer_party`, `approving_parties`, `pingpong_exceeded`.
- `meld.roster`: `WorkerType(name, description, source, path, text="")`, `parse_worker_file`, `builtin_workers()`, `product_workers(repo_root)`, `roster(repo_root, extra=())`, `roster_names(repo_root, extra=())`.
- `meld.models`: `ModelRegistry.load()`, `.get(adapter, id)`, `.validate(adapter, id, effort)`, `.tier_of`, `.for_adapter`, `.adapters`; `SIZES`, `size_fits`.
- `meld.context`: `Context(cfg, party, gh, store, models, repo_root, actor_login)` with `.actor()`; `load_context(config_path=None, env=None, run=subprocess.run, cwd=None, models=None)`.
- `meld.status`: `build_status(ctx, now=None) -> (dict, Board)` (adds `state_errors`), `render_status(...)`.
- `meld.waiting.wait_for_change(ctx, timeout, interval, *, sleep=time.sleep, clock=time.monotonic) -> dict`.
- `meld.actions`: `Refused(refusal, post_error=None, violation_posted=None)`, `refuse(ctx, board, refusal) -> NoReturn`, `claim(ctx, number)`, `release(ctx, number, force=False)`; constants `READY`, `IN_PROGRESS`, `BLOCKED`.
- `meld.posting`: `PostError`, `post(ctx, verb, ref=None, qualifier=None, text="", joint=False)`.
- `meld.cli`: `main` (click group; `--config`, `--pretty/--compact`), `RUN` (monkeypatched by tests), `emit(data, pretty)`, `_load(ctx)`, `_guard(fn)`, `_run_action(ctx, fn)`, `_refusal_json(e)`; commands `status`, `models`, `wait`, `claim`, `release`, `post`.
- Tests: `tests/conftest.py` exports `load_board1_routes(fake, repo="acme/widgets")`, fixture `board1_gh`, `PROJECT_TOML`, `env_for(tmp_path, group="claude", role="pm")`; `tests/fakegh.py` exports `FakeGh` (`.route(method, endpoint, body, status=200, etag=None)`, `.calls`, `.envs`, `.rate_limit_next`) and `Call`; `tests/factories.py` exports `CFG2`, `CFG3`, `REG`, `mk_issue`, `mk_pr`, `mk_comment`, `mk_run`, `mk_board`, `STATUS_TO_LABEL`.

## File structure

New:
- `src/meld/gitops.py` — worktrees, identities, commits-ahead, push with a token that never touches argv, removal.
- `src/meld/brief.py` — renders `.meld-brief.md`; parses and validates `.meld-result.json`.
- `src/meld/adapters/__init__.py`, `base.py`, `claude.py`, `codex.py` — adapter protocol, registry, environment scrubbing, skills scanning, and the two CLIs.
- `src/meld/dispatch.py` — `dispatch`, `tick`, `collect`, `reap`.
- `src/meld/runners.py` — `run_group` supervisor.
- `src/meld/prompts/_preamble.md`, `_worker_contract.md`, `orchestrator.md`, `pm.md`; full bodies for `prompts/workers/*.md`.
- `src/meld/prompts.py` (role prompt rendering), `src/meld/tagging.py` (`meld tag`).
- `tests/fakeadapter.py`, `tests/gitfixtures.py`, `tests/productfixtures.py`, `tests/test_gitops.py`, `test_brief.py`, `test_prompts.py`, `test_adapters.py`, `test_adapter_claude.py`, `test_adapter_codex.py`, `test_dispatch.py`, `test_tick.py`, `test_collect.py`, `test_reap.py`, `test_roster_cli.py`, `test_role_prompts.py`, `test_tagging.py`, `test_runners.py`, `tests/smoke/test_adapters_smoke.py`.

Modified:
- `src/meld/state.py` (structured `Change` records, `RunRecord` extensions via `board.py`), `src/meld/waiting.py` (`kinds` filter), `src/meld/cli.py` (`--filter`, new commands), `src/meld/config.py` (git identity, worker and lease settings, `parse_duration`), `src/meld/grammar.py` (`LEASE`, `EXPIRED`), `src/meld/board.py` (`Lease`, `Board.leases`, `Board.plan_text`, `RunRecord` fields), `src/meld/assemble.py` (leases, milestone description), `src/meld/rules.py` (expired-lease release), `src/meld/status.py` (leases in JSON), `src/meld/context.py` (adapter skills in the roster), `tests/factories.py`, `tests/conftest.py`.

---

### Task 1: Structured change records and `meld wait --filter`

**Files:**
- Modify: `src/meld/state.py`
- Modify: `src/meld/waiting.py`
- Modify: `src/meld/cli.py`
- Modify: `tests/test_state.py`, `tests/test_wait.py`

**Interfaces:**
- Consumes: `board_digest`, `diff_digest`, `wait_for_change`, the `wait` command.
- Produces (in `meld.state`): `@dataclass(frozen=True) Change(kind: str, ref: str, before: object, after: object, text: str)`; `CHANGE_KINDS = ("snapshot", "phase", "halted", "issue", "pr", "run", "comments", "challenges")`; `diff_changes(old: dict | None, new: dict) -> list[Change]`; `filter_changes(changes, kinds: Iterable[str] | None) -> list[Change]` (a `snapshot` change always passes); `diff_digest` now returns `[c.text for c in diff_changes(old, new)]` with texts unchanged. In `meld.waiting`: `wait_for_change(ctx, timeout, interval, *, kinds=None, sleep=..., clock=...)` returns the status dict plus `changes: list[str]`, `change_records: list[dict]`, `timed_out`, `rate_limited`; an irrelevant change still saves the snapshot. CLI: `meld wait --filter comments,issue`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_state.py` (add `diff_changes, filter_changes` to its `from meld.state import ...` line):

```python
def test_diff_changes_are_structured_and_filterable():
    b1 = mk_board(CFG2, issues=[mk_issue(11)], pulls=[mk_pr(20, 13)])
    b2 = mk_board(CFG2, issues=[mk_issue(11, status="in_progress", assignees=("meld-claude-bot",))],
                  pulls=[mk_pr(20, 13, approvals=("meld-codex-bot",))], halted=True)
    d1, d2 = board_digest(b1), board_digest(b2)
    changes = diff_changes(d1, d2)
    assert [c.kind for c in changes] == ["halted", "issue", "pr"]
    issue = changes[1]
    assert (issue.ref, issue.before, issue.after, issue.text) == ("#11", "ready", "in_progress", "issue #11: ready -> in_progress")
    assert [c.text for c in changes] == diff_digest(d1, d2)
    assert [c.kind for c in filter_changes(changes, {"pr"})] == ["pr"]
    assert filter_changes(changes, None) == changes
    initial = diff_changes(None, d2)
    assert [c.kind for c in initial] == ["snapshot"] and initial[0].text == "initial snapshot"
    assert filter_changes(initial, {"comments"}) == initial
```

Append to `tests/test_wait.py` (add `import json` and `from pathlib import Path` if missing, and `from click.testing import CliRunner` plus `import meld.cli as cli`):

```python
def _fixture(name):
    return json.loads((Path(__file__).parent / "fixtures" / "board1" / name).read_text())


def test_filter_ignores_other_kinds_but_still_saves_snapshot(tmp_path):
    fake, ctx = make_ctx(tmp_path)
    clock = Clock()
    wait_for_change(ctx, timeout=300, interval=30, sleep=clock.sleep, clock=clock)
    issues = _fixture("issues.json")
    for i in issues:
        if i["number"] == 11:
            i["labels"] = [l for l in i["labels"] if l["name"] != "meld:ready"] + [{"name": "meld:in-progress"}]
            i["assignees"] = [{"login": "meld-codex-bot"}]

    def sleep_then_mutate(s):
        clock.sleep(s)
        fake.route("GET", "repos/acme/widgets/issues", issues)

    out = wait_for_change(ctx, timeout=90, interval=30, kinds={"comments"}, sleep=sleep_then_mutate, clock=clock)
    assert out["timed_out"] is True and out["changes"] == [] and out["change_records"] == []
    assert ctx.store.load_snapshot()["issues"]["11"]["status"] == "in_progress"

    comments = _fixture("comments_10.json") + [{"id": 105, "user": {"login": "meld-claude-bot"},
                                                 "created_at": "2026-09-03T12:00:00Z", "body": "[claude] ROSTER-CHANGE\n\n+marketing"}]

    def sleep_then_comment(s):
        clock.sleep(s)
        fake.route("GET", "repos/acme/widgets/issues/10/comments", comments)

    out = wait_for_change(ctx, timeout=300, interval=30, kinds={"comments"}, sleep=sleep_then_comment, clock=clock)
    assert out["timed_out"] is False
    assert out["change_records"][0]["kind"] == "comments" and out["changes"] == ["comments: new up to #105"]


def test_cli_wait_filter_validates_kinds(tmp_path, monkeypatch):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    monkeypatch.setattr(cli, "RUN", fake)
    bad = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "wait", "--timeout", "0", "--filter", "issue,bogus"],
                             env=env_for(tmp_path))
    assert bad.exit_code == 2 and "bogus" in bad.output
    ok = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "wait", "--timeout", "0", "--filter", "issue,comments"],
                            env=env_for(tmp_path))
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.output)["changes"] == ["initial snapshot"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_state.py tests/test_wait.py -v`
Expected: FAIL with `ImportError: cannot import name 'diff_changes'`

- [ ] **Step 3: Restructure `diff_digest` in `src/meld/state.py`**

Change the imports at the top to `from dataclasses import asdict, dataclass` and `from typing import Callable, Iterable, Mapping`, then replace the whole `diff_digest` function with:

```python
CHANGE_KINDS = ("snapshot", "phase", "halted", "issue", "pr", "run", "comments", "challenges")


@dataclass(frozen=True)
class Change:
    """One difference between two board digests, with a machine-readable kind and a human line."""
    kind: str
    ref: str
    before: object
    after: object
    text: str


def diff_changes(old: dict | None, new: dict) -> list[Change]:
    if old is None:
        return [Change("snapshot", "", None, None, "initial snapshot")]
    out: list[Change] = []
    for key in ("phase", "halted"):
        if old.get(key) != new.get(key):
            out.append(Change(key, "", old.get(key), new.get(key), f"{key}: {old.get(key)} -> {new.get(key)}"))
    for n in sorted(set(old.get("issues", {})) | set(new.get("issues", {})), key=int):
        a, b = old.get("issues", {}).get(n), new.get("issues", {}).get(n)
        ref = f"#{n}"
        if a is None:
            out.append(Change("issue", ref, None, b["status"], f"issue #{n}: new ({b['status']})"))
        elif b is None:
            out.append(Change("issue", ref, a["status"], None, f"issue #{n}: gone"))
        elif a["status"] != b["status"]:
            out.append(Change("issue", ref, a["status"], b["status"], f"issue #{n}: {a['status']} -> {b['status']}"))
        elif a != b:
            out.append(Change("issue", ref, a, b, f"issue #{n}: labels or assignees changed"))
    for n in sorted(set(old.get("pulls", {})) | set(new.get("pulls", {})), key=int):
        a, b = old.get("pulls", {}).get(n), new.get("pulls", {}).get(n)
        ref = f"pr #{n}"
        if a is None:
            out.append(Change("pr", ref, None, b, f"pr #{n}: new"))
        elif b is None:
            out.append(Change("pr", ref, a, None, f"pr #{n}: gone"))
        else:
            if a["head"] != b["head"]:
                out.append(Change("pr", ref, a["head"], b["head"], f"pr #{n}: new head {b['head']}"))
            if a["approvals"] != b["approvals"]:
                out.append(Change("pr", ref, a["approvals"], b["approvals"], f"pr #{n}: approvals {a['approvals']} -> {b['approvals']}"))
            if a["changes_requested"] != b["changes_requested"]:
                out.append(Change("pr", ref, a["changes_requested"], b["changes_requested"],
                                  f"pr #{n}: changes_requested {a['changes_requested']} -> {b['changes_requested']}"))
            if a["checks"] != b["checks"]:
                out.append(Change("pr", ref, a["checks"], b["checks"], f"pr #{n}: checks {a['checks']} -> {b['checks']}"))
            if a["state"] != b["state"]:
                out.append(Change("pr", ref, a["state"], b["state"], f"pr #{n}: {a['state']} -> {b['state']}"))
    for k in sorted(set(old.get("runs", {})) | set(new.get("runs", {}))):
        a, b = old.get("runs", {}).get(k), new.get("runs", {}).get(k)
        if a != b:
            out.append(Change("run", k, a, b, f"run {k}: {a or 'none'} -> {b or 'none'}"))
    if old.get("last_comment_id") != new.get("last_comment_id"):
        out.append(Change("comments", "", old.get("last_comment_id"), new.get("last_comment_id"),
                          f"comments: new up to #{new.get('last_comment_id')}"))
    if old.get("open_challenges") != new.get("open_challenges"):
        out.append(Change("challenges", "", old.get("open_challenges"), new.get("open_challenges"),
                          f"open_challenges: {old.get('open_challenges')} -> {new.get('open_challenges')}"))
    return out


def filter_changes(changes: list[Change], kinds: Iterable[str] | None) -> list[Change]:
    """Keep only the requested kinds; an initial snapshot always counts."""
    if not kinds:
        return list(changes)
    wanted = set(kinds) | {"snapshot"}
    return [c for c in changes if c.kind in wanted]


def diff_digest(old: dict | None, new: dict) -> list[str]:
    return [c.text for c in diff_changes(old, new)]
```

- [ ] **Step 4: Thread `kinds` through `src/meld/waiting.py`**

Replace the file's imports and `wait_for_change` with:

```python
"""Block until the board changes or a timeout elapses."""
from __future__ import annotations

import time
from dataclasses import asdict
from typing import Iterable

from meld.github import RateLimited
from meld.state import diff_changes, filter_changes
from meld.status import build_status

_MAX_BACKOFF = 300.0


def _nap(sleep, clock, deadline: float, want: float) -> None:
    """Sleep for `want` seconds, but never past the deadline (and never negative)."""
    sleep(max(0.0, min(want, deadline - clock())))


def wait_for_change(ctx, timeout: float, interval: float, *, kinds: Iterable[str] | None = None,
                    sleep=time.sleep, clock=time.monotonic) -> dict:
    deadline = clock() + timeout
    prev = ctx.store.load_snapshot()
    backoff = float(interval)
    while True:
        try:
            data, _ = build_status(ctx)
        except RateLimited:
            if clock() >= deadline:
                return {"timed_out": True, "rate_limited": True, "changes": [], "change_records": []}
            _nap(sleep, clock, deadline, min(backoff, _MAX_BACKOFF))
            backoff = min(backoff * 2, _MAX_BACKOFF)
            continue
        digest = data["digest"]
        all_changes = diff_changes(prev, digest)
        if all_changes:
            ctx.store.save_snapshot(digest)
            prev = digest
        relevant = filter_changes(all_changes, kinds)
        if relevant:
            data.update({"changes": [c.text for c in relevant], "change_records": [asdict(c) for c in relevant],
                         "timed_out": False, "rate_limited": False})
            return data
        if clock() >= deadline:
            data.update({"changes": [], "change_records": [], "timed_out": True, "rate_limited": False})
            return data
        _nap(sleep, clock, deadline, interval)
```

- [ ] **Step 5: Add `--filter` to the `wait` command in `src/meld/cli.py`**

Add `from meld.state import CHANGE_KINDS` to the imports. Replace the `wait` command with:

```python
@main.command()
@click.option("--timeout", type=float, default=None, help="Seconds to wait before returning unchanged (default: polling.wait_timeout_seconds).")
@click.option("--interval", type=float, default=None, help="Seconds between polls (default: polling.interval_seconds).")
@click.option("--filter", "kinds", default=None,
              help="Comma-separated change kinds that count as a change: " + ",".join(k for k in CHANGE_KINDS if k != "snapshot") + " (default: all).")
@click.pass_context
def wait(ctx: click.Context, timeout: float | None, interval: float | None, kinds: str | None) -> None:
    """Block until the board changes, then print status plus a changes list."""
    c = _load(ctx)
    t = timeout if timeout is not None else c.cfg.wait_timeout_seconds
    i = interval if interval is not None else c.cfg.poll_interval_seconds
    if i <= 0:
        raise click.BadParameter("must be greater than 0", param_hint="--interval")
    if t < 0:
        raise click.BadParameter("must not be negative", param_hint="--timeout")
    wanted: set[str] | None = None
    if kinds:
        wanted = {k.strip() for k in kinds.split(",") if k.strip()}
        unknown = sorted(wanted - set(CHANGE_KINDS))
        if unknown:
            raise click.BadParameter(f"unknown change kind(s): {', '.join(unknown)}", param_hint="--filter")
    data = _guard(lambda: wait_for_change(c, timeout=t, interval=i, kinds=wanted))
    emit(data, ctx.obj["pretty"])
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_state.py tests/test_wait.py -v` then `uv run pytest -q`
Expected: PASS; full suite passes with the previous 141 plus the 3 new tests.

- [ ] **Step 7: Commit**

```bash
git add src/meld/state.py src/meld/waiting.py src/meld/cli.py tests/test_state.py tests/test_wait.py
git commit -m "feat: structured change records and meld wait --filter"
```

---

### Task 2: Config additions — git identity, worker and lease settings, durations

**Files:**
- Modify: `src/meld/config.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Produces (in `meld.config`): `GroupConfig` gains `git_name: str | None = None` and `git_email: str | None = None` (from `[groups.<g>] git_name`, `git_email`); `Config` gains `worker_log_tail_lines: int` (`[worker] log_tail_lines`, default 50), `worker_max_turns: int` (`[worker] max_turns`, default 200), `lease_grace_minutes: int` (`[lease] grace_minutes`, default 10); `parse_duration(text: str) -> int` accepting `"90s"`, `"20m"`, `"2h"` (also a bare integer meaning seconds) and raising `ConfigError` otherwise; `Config.timeout_seconds(size: str) -> int`. `from_dict` validates every `timeouts` value with `parse_duration`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py` (add `parse_duration` to the import line):

```python
def test_git_identity_and_worker_lease_settings(tmp_path):
    text = MINIMAL + """
[worker]
log_tail_lines = 80
max_turns = 50

[lease]
grace_minutes = 5
""".replace('bot = "meld-claude-bot"', 'bot = "meld-claude-bot"\ngit_name = "Meld Claude"\ngit_email = "1+meld-claude-bot@users.noreply.github.com"')
    cfg = load_config(write(tmp_path, text))
    assert cfg.groups["claude"].git_name == "Meld Claude"
    assert cfg.groups["claude"].git_email == "1+meld-claude-bot@users.noreply.github.com"
    assert cfg.groups["codex"].git_name is None and cfg.groups["codex"].git_email is None
    assert (cfg.worker_log_tail_lines, cfg.worker_max_turns, cfg.lease_grace_minutes) == (80, 50, 5)
    defaults = load_config(write(tmp_path, MINIMAL))
    assert (defaults.worker_log_tail_lines, defaults.worker_max_turns, defaults.lease_grace_minutes) == (50, 200, 10)


def test_parse_duration_and_timeout_seconds(tmp_path):
    assert parse_duration("90s") == 90 and parse_duration("20m") == 1200 and parse_duration("2h") == 7200 and parse_duration("45") == 45
    with pytest.raises(ConfigError, match="duration"):
        parse_duration("soon")
    cfg = load_config(write(tmp_path, MINIMAL))
    assert (cfg.timeout_seconds("S"), cfg.timeout_seconds("M"), cfg.timeout_seconds("L")) == (1200, 2700, 5400)
    with pytest.raises(ConfigError, match="timeouts.M"):
        load_config(write(tmp_path, MINIMAL + '\n[timeouts]\nM = "fast"\n'))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_duration'`

- [ ] **Step 3: Implement in `src/meld/config.py`**

Add `import re` to the imports. Extend `GroupConfig` with two trailing fields:

```python
    git_name: str | None = None
    git_email: str | None = None
```

Add after `_role`:

```python
_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smh]?)\s*$")
_DURATION_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600}


def parse_duration(text: str) -> int:
    """Parse '90s', '20m', '2h', or a bare number of seconds into seconds."""
    m = _DURATION_RE.match(str(text))
    if not m:
        raise ConfigError(f"invalid duration {text!r}; use e.g. 90s, 20m, 2h")
    return int(m.group(1)) * _DURATION_UNITS[m.group(2)]
```

Extend `Config` with three trailing fields and one method:

```python
    worker_log_tail_lines: int = 50
    worker_max_turns: int = 200
    lease_grace_minutes: int = 10

    def timeout_seconds(self, size: str) -> int:
        return parse_duration(self.timeouts[size])
```

In `from_dict`, when building each `GroupConfig` add `git_name=(str(g["git_name"]) if g.get("git_name") else None), git_email=(str(g["git_email"]) if g.get("git_email") else None),`. Before `return cls(...)` add:

```python
        worker = data.get("worker", {})
        lease = data.get("lease", {})
        timeouts_cfg = {"S": str(timeouts.get("S", "20m")), "M": str(timeouts.get("M", "45m")), "L": str(timeouts.get("L", "90m"))}
        for size, value in timeouts_cfg.items():
            try:
                parse_duration(value)
            except ConfigError as e:
                raise ConfigError(f"timeouts.{size}: {e}") from e
```

and use `timeouts=timeouts_cfg,` in the constructor call (replacing the inline dict), plus `worker_log_tail_lines=int(worker.get("log_tail_lines", 50)), worker_max_turns=int(worker.get("max_turns", 200)), lease_grace_minutes=int(lease.get("grace_minutes", 10)),`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v` then `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/meld/config.py tests/test_config.py
git commit -m "feat: git identity per group, worker and lease settings, duration parsing"
```

---

### Task 3: Git plumbing (`gitops.py`) tested against real temporary repositories

**Files:**
- Create: `src/meld/gitops.py`
- Create: `tests/gitfixtures.py`
- Create: `tests/test_gitops.py`

**Interfaces:**
- Produces (in `meld.gitops`): `class GitError(RuntimeError)`; `git(args: list[str], cwd: Path, run=subprocess.run, env: dict | None = None) -> str` (stdout stripped; raises `GitError` with stderr on non-zero); `slugify(title, limit=40) -> str`; `branch_name(issue: int, title: str) -> str` (`meld/<n>-<slug>`); `noreply_email(user_id: int, login: str) -> str`; `remote_branch_exists(repo_root, branch, run) -> bool`; `worktree_exists(repo_root, path, run) -> bool`; `create_worktree(repo_root, path, branch, base, run) -> str` returning `"existing"` (remote branch reused) or `"new"`; `checkout_pr_head(repo_root, path, branch, run) -> None`; `set_identity(path, name, email, run) -> None`; `head_sha(path, run) -> str`; `commits_ahead(path, base_ref, run) -> list[str]` (oldest first); `is_dirty(path, run) -> bool`; `push_branch(path, branch, token: str | None, run, base_env=None) -> None` (token travels only in the `MELD_PUSH_TOKEN` environment variable read by an inline credential helper); `remove_worktree(repo_root, path, run) -> None`; `delete_local_branch(repo_root, branch, run) -> None` (missing branch is not an error).
- `tests/gitfixtures.py` produces: `make_origin_and_clone(tmp_path) -> tuple[Path, Path]` (a bare `origin.git` with one commit on `main`, and a clone `repo` whose `origin` points at it).

- [ ] **Step 1: Write the git fixtures**

`tests/gitfixtures.py`:

```python
"""Real temporary git repositories for gitops and dispatch tests. No network."""
from __future__ import annotations

import subprocess
from pathlib import Path


def sh(args: list[str], cwd: Path) -> str:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, check=True).stdout.strip()


def make_origin_and_clone(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    sh(["git", "init", "--bare", "-b", "main", str(origin)], tmp_path)
    sh(["git", "init", "-b", "main", str(repo)], tmp_path)
    sh(["git", "config", "user.name", "Fixture"], repo)
    sh(["git", "config", "user.email", "fixture@example.com"], repo)
    (repo / "README.md").write_text("# product\n")
    sh(["git", "add", "README.md"], repo)
    sh(["git", "commit", "-q", "-m", "initial"], repo)
    sh(["git", "remote", "add", "origin", str(origin)], repo)
    sh(["git", "push", "-q", "-u", "origin", "main"], repo)
    return origin, repo
```

- [ ] **Step 2: Write the failing tests**

`tests/test_gitops.py`:

```python
import subprocess
from pathlib import Path

import pytest

from meld.gitops import (
    GitError,
    branch_name,
    checkout_pr_head,
    commits_ahead,
    create_worktree,
    delete_local_branch,
    git,
    head_sha,
    is_dirty,
    noreply_email,
    push_branch,
    remote_branch_exists,
    remove_worktree,
    set_identity,
    slugify,
    worktree_exists,
)
from tests.gitfixtures import make_origin_and_clone, sh


def test_slug_and_branch_and_noreply():
    assert slugify("Add Health Endpoint!!") == "add-health-endpoint"
    assert slugify("x" * 60, limit=10) == "xxxxxxxxxx"
    assert slugify("///") == "task"
    assert branch_name(42, "Add Health Endpoint!!") == "meld/42-add-health-endpoint"
    assert noreply_email(421573, "smidley") == "421573+smidley@users.noreply.github.com"


def test_git_raises_gerror_with_stderr(tmp_path):
    with pytest.raises(GitError, match="not a git repository"):
        git(["status"], tmp_path)


def test_worktree_lifecycle_new_push_existing(tmp_path):
    origin, repo = make_origin_and_clone(tmp_path)
    wt = tmp_path / "wt" / "claude" / "42-add-health"
    branch = "meld/42-add-health"
    assert not worktree_exists(repo, wt)
    assert create_worktree(repo, wt, branch, "main") == "new"
    assert worktree_exists(repo, wt)
    set_identity(wt, "Meld Claude", "1+meld-claude-bot@users.noreply.github.com")
    assert not is_dirty(wt)
    (wt / "health.py").write_text("OK = True\n")
    assert is_dirty(wt)
    sh(["git", "add", "health.py"], wt)
    sh(["git", "commit", "-q", "-m", "feat: health"], wt)
    assert sh(["git", "log", "-1", "--format=%an <%ae>"], wt) == "Meld Claude <1+meld-claude-bot@users.noreply.github.com>"
    ahead = commits_ahead(wt, "origin/main")
    assert ahead == [head_sha(wt)]
    assert not remote_branch_exists(repo, branch)
    push_branch(wt, branch, token=None)
    assert remote_branch_exists(repo, branch)
    remove_worktree(repo, wt)
    assert not wt.exists() and not worktree_exists(repo, wt)
    delete_local_branch(repo, branch)
    delete_local_branch(repo, branch)  # idempotent
    assert create_worktree(repo, wt, branch, "main") == "existing"
    assert head_sha(wt) == ahead[0]
    other = tmp_path / "wt" / "codex" / "review-42"
    checkout_pr_head(repo, other, branch)
    assert head_sha(other) == ahead[0]


def test_push_keeps_token_out_of_argv(tmp_path):
    origin, repo = make_origin_and_clone(tmp_path)
    wt = tmp_path / "wt" / "x"
    create_worktree(repo, wt, "meld/7-x", "main")
    (wt / "a.txt").write_text("a\n")
    sh(["git", "add", "a.txt"], wt)
    sh(["git", "commit", "-q", "-m", "a"], wt)
    seen: list[tuple[list[str], dict]] = []

    def recording_run(cmd, **kwargs):
        seen.append((list(cmd), dict(kwargs.get("env") or {})))
        return subprocess.run(cmd, **kwargs)

    push_branch(wt, "meld/7-x", token="ghp_secret", run=recording_run, base_env={"PATH": "/usr/bin:/bin"})
    cmd, env = seen[-1]
    assert "ghp_secret" not in " ".join(cmd)
    assert env["MELD_PUSH_TOKEN"] == "ghp_secret"
    assert any(a.startswith("credential.helper=!f()") for a in cmd)
    assert remote_branch_exists(repo, "meld/7-x")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_gitops.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.gitops'`

- [ ] **Step 4: Write `src/meld/gitops.py`**

```python
"""Git plumbing for worktrees, identities, and pushes. The only module that runs git."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable

RunFn = Callable[..., subprocess.CompletedProcess]
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_CREDENTIAL_HELPER = '!f() { echo username=x-access-token; echo "password=$MELD_PUSH_TOKEN"; }; f'


class GitError(RuntimeError):
    pass


def git(args: list[str], cwd: Path, run: RunFn = subprocess.run, env: dict | None = None) -> str:
    proc = run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {cwd}: {(proc.stderr or '').strip()}")
    return (proc.stdout or "").strip()


def slugify(title: str, limit: int = 40) -> str:
    slug = _SLUG_RE.sub("-", title.lower()).strip("-")
    return slug[:limit].rstrip("-") or "task"


def branch_name(issue: int, title: str) -> str:
    return f"meld/{issue}-{slugify(title)}"


def noreply_email(user_id: int, login: str) -> str:
    return f"{user_id}+{login}@users.noreply.github.com"


def remote_branch_exists(repo_root: Path, branch: str, run: RunFn = subprocess.run) -> bool:
    return bool(git(["ls-remote", "--heads", "origin", branch], repo_root, run))


def worktree_exists(repo_root: Path, path: Path, run: RunFn = subprocess.run) -> bool:
    listing = git(["worktree", "list", "--porcelain"], repo_root, run)
    target = path.resolve()
    return any(line.startswith("worktree ") and Path(line[len("worktree "):]).resolve() == target for line in listing.splitlines())


def create_worktree(repo_root: Path, path: Path, branch: str, base: str, run: RunFn = subprocess.run) -> str:
    """Create `path` on `branch`: reuse the remote branch when it exists (a retry), else branch from origin/<base>."""
    git(["fetch", "--quiet", "origin"], repo_root, run)
    path.parent.mkdir(parents=True, exist_ok=True)
    if remote_branch_exists(repo_root, branch, run):
        git(["worktree", "add", "--track", "-B", branch, str(path), f"origin/{branch}"], repo_root, run)
        return "existing"
    git(["worktree", "add", "-B", branch, str(path), f"origin/{base}"], repo_root, run)
    return "new"


def checkout_pr_head(repo_root: Path, path: Path, branch: str, run: RunFn = subprocess.run) -> None:
    git(["fetch", "--quiet", "origin", branch], repo_root, run)
    path.parent.mkdir(parents=True, exist_ok=True)
    git(["worktree", "add", "--detach", str(path), f"origin/{branch}"], repo_root, run)


def set_identity(path: Path, name: str, email: str, run: RunFn = subprocess.run) -> None:
    git(["config", "user.name", name], path, run)
    git(["config", "user.email", email], path, run)


def head_sha(path: Path, run: RunFn = subprocess.run) -> str:
    return git(["rev-parse", "HEAD"], path, run)


def commits_ahead(path: Path, base_ref: str, run: RunFn = subprocess.run) -> list[str]:
    out = git(["rev-list", "--reverse", f"{base_ref}..HEAD"], path, run)
    return out.splitlines() if out else []


def is_dirty(path: Path, run: RunFn = subprocess.run) -> bool:
    return bool(git(["status", "--porcelain"], path, run))


def push_branch(path: Path, branch: str, token: str | None, run: RunFn = subprocess.run, base_env: dict | None = None) -> None:
    """Push `branch` to origin. The token reaches git only through the MELD_PUSH_TOKEN variable read by an inline helper."""
    env = dict(os.environ if base_env is None else base_env)
    if token:
        env["MELD_PUSH_TOKEN"] = token
    git(["-c", "credential.helper=", "-c", f"credential.helper={_CREDENTIAL_HELPER}", "push", "--set-upstream", "origin", branch],
        path, run, env=env)


def remove_worktree(repo_root: Path, path: Path, run: RunFn = subprocess.run) -> None:
    git(["worktree", "remove", "--force", str(path)], repo_root, run)
    git(["worktree", "prune"], repo_root, run)


def delete_local_branch(repo_root: Path, branch: str, run: RunFn = subprocess.run) -> None:
    try:
        git(["branch", "-D", branch], repo_root, run)
    except GitError as e:
        if "not found" not in str(e):
            raise
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_gitops.py -v` then `uv run pytest -q`
Expected: PASS. If `git worktree add -B` complains that the branch is checked out elsewhere, a previous test left a worktree behind; each test uses its own `tmp_path`, so this indicates a bug in `remove_worktree`.

- [ ] **Step 6: Commit**

```bash
git add src/meld/gitops.py tests/gitfixtures.py tests/test_gitops.py
git commit -m "feat: git plumbing for worktrees, identities, and token-safe pushes"
```

---

### Task 4: Leases — `LEASE`/`EXPIRED` verbs, `Board.leases`, plan text, expired-lease release

**Files:**
- Modify: `src/meld/grammar.py`, `src/meld/board.py`, `src/meld/github.py`, `src/meld/assemble.py`, `src/meld/rules.py`, `src/meld/status.py`
- Modify: `tests/factories.py`, `tests/test_grammar.py`, `tests/test_board.py`, `tests/test_assemble.py`, `tests/test_rules_issues.py`, `tests/test_status.py`

**Interfaces:**
- `meld.grammar`: `PM_VERBS` gains `LEASE` and `EXPIRED`; both are in `REQUIRES_REF`. A `LEASE #<n>` body carries the line `until: <ISO-8601 UTC, e.g. 2026-09-03T13:00:00Z>`.
- `meld.board`: `@dataclass(frozen=True) Lease(issue: int, group: str, until: str, comment_id: int)`; `parse_lease_until(text: str) -> str | None`; `derive_leases(comments) -> dict[int, Lease]` (a later `LEASE #n` replaces an earlier one; `EXPIRED #n` by any PM, `RELEASE #n` by a human, or the issue leaving `in_progress` is handled by consumers); `utc_now_iso() -> str` (`YYYY-MM-DDTHH:MM:SSZ`); `Board` gains trailing fields `leases: dict[int, Lease] = field(default_factory=dict)`, `plan_text: str = ""`, `now: str = ""`; `Board.lease_expired(issue: int) -> bool` (`lease.until < board.now`, both ISO Z strings).
- `meld.github.GitHub.get_milestone(number) -> dict` (`GET repos/{repo}/milestones/{number}`) and `.update_comment(comment_id, body) -> dict` (`PATCH repos/{repo}/issues/comments/{id}`).
- `meld.assemble`: `_locate_active` also returns the milestone description; `build_board` fills `leases`, `plan_text` (milestone description), `now` (`utc_now_iso()` unless a `now` argument is given: `build_board(gh, cfg, runs, spend, milestone=None, now=None)`).
- `meld.rules._release`: a PM may release an issue whose lease has expired even if its bot is not the assignee (rule text otherwise unchanged); new helper `lease_rule(board, issue) -> str | None`.
- `meld.status.render_status`: each `issues[]` entry gains `"lease": {"group": ..., "until": ..., "expired": bool} | None`.
- `tests/factories.py`: `mk_board(..., leases=None, plan_text="", now="2026-09-03T12:00:00Z")`; `mk_lease(issue, group="claude", until="2026-09-03T13:00:00Z", comment_id=900) -> Lease`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_grammar.py`:

```python
def test_pm_lease_and_expired_verbs():
    assert parse_header("[codex:pm] LEASE #12", CFG) == Header(Party("pm", "codex"), "LEASE", "#12", None)
    assert parse_header("[codex:pm] EXPIRED #12", CFG) == Header(Party("pm", "codex"), "EXPIRED", "#12", None)
    assert parse_header("[codex] LEASE #12", CFG) is None
    assert validate(Header(Party("pm", "codex"), "LEASE", None, None), "until: x") == ["LEASE requires a ref"]
```

Append to `tests/test_board.py` (add `Lease, derive_leases, parse_lease_until, utc_now_iso` to the import):

```python
def test_parse_lease_until_and_derive_leases():
    assert parse_lease_until("until: 2026-09-03T13:00:00Z\nrun: claude/#12") == "2026-09-03T13:00:00Z"
    assert parse_lease_until("no lease here") is None
    cs = [mk_comment(CFG2, 1, "meld-claude-bot", "[claude:pm] LEASE #12\n\nuntil: 2026-09-03T13:00:00Z"),
          mk_comment(CFG2, 2, "meld-claude-bot", "[claude:pm] LEASE #12\n\nuntil: 2026-09-03T13:30:00Z"),
          mk_comment(CFG2, 3, "meld-codex-bot", "[codex:pm] LEASE #13\n\nuntil: 2026-09-03T14:00:00Z"),
          mk_comment(CFG2, 4, "randouser", "[codex:pm] LEASE #14\n\nuntil: 2099-01-01T00:00:00Z"),
          mk_comment(CFG2, 5, "meld-claude-bot", "[claude:pm] EXPIRED #13\n\nlease expired")]
    leases = derive_leases(cs)
    assert leases == {12: Lease(12, "claude", "2026-09-03T13:30:00Z", 2)}
    assert len(utc_now_iso()) == 20 and utc_now_iso().endswith("Z")
    b = mk_board(CFG2, issues=[mk_issue(12, status="in_progress", assignees=("meld-claude-bot",))],
                 leases={12: mk_lease(12, until="2026-09-03T11:00:00Z")}, now="2026-09-03T12:00:00Z")
    assert b.lease_expired(12) and not b.lease_expired(99)
```

Append to `tests/test_assemble.py`:

```python
def test_build_board_reads_leases_and_plan_text(board1_gh):
    fake, gh = board1_gh
    comments = json.loads((Path(__file__).parent / "fixtures" / "board1" / "comments_10.json").read_text())
    comments.append({"id": 110, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:20:00Z",
                     "body": "[claude:pm] LEASE #12\n\nuntil: 2026-09-03T10:30:00Z\nrun: claude/#12"})
    fake.route("GET", "repos/acme/widgets/issues/10/comments", comments)
    b = build_board(gh, CFG2, {"claude": {}, "codex": {}}, {"claude": {}, "codex": {}}, now="2026-09-03T11:00:00Z")
    assert b.leases[12].group == "claude" and b.leases[12].until == "2026-09-03T10:30:00Z" and b.leases[12].comment_id == 110
    assert b.lease_expired(12)
    assert b.plan_text == "approved plan text"
    assert b.now == "2026-09-03T11:00:00Z"
```

(add `import json` and `from pathlib import Path` at the top of that test file if missing).

Append to `tests/test_rules_issues.py` (add `mk_lease` to the factories import):

```python
def test_any_pm_may_release_an_expired_lease():
    claimed = mk_issue(12, status="in_progress", assignees=("meld-claude-bot",))
    live = mk_board(CFG2, issues=[claimed], leases={12: mk_lease(12, until="2026-09-03T13:00:00Z")}, now="2026-09-03T12:00:00Z")
    assert rule(live, CODEX_PM, Verb.RELEASE, claimed) == "issue is not assigned to meld-codex-bot"
    expired = mk_board(CFG2, issues=[claimed], leases={12: mk_lease(12, until="2026-09-03T11:00:00Z")}, now="2026-09-03T12:00:00Z")
    assert rule(expired, CODEX_PM, Verb.RELEASE, claimed) is None
    assert rule(expired, CLAUDE_ORCH, Verb.RELEASE, claimed) is None  # the owner may always release its own
    codex_orch = Actor(Party("group", "codex"), "meld-codex-bot", ROSTER)
    assert rule(expired, codex_orch, Verb.RELEASE, claimed) == "issue is not assigned to meld-codex-bot"
```

Append to `tests/test_status.py` inside `test_status_command_reports_board_and_legal_actions`, after the `issues` assertions: `assert issues[12]["lease"] is None`. Add a new test:

```python
def test_status_reports_leases(project: Path, monkeypatch):
    fake = FakeGh()
    load_board1_routes(fake)
    comments = json.loads((Path(__file__).parent / "fixtures" / "board1" / "comments_10.json").read_text())
    comments.append({"id": 110, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:20:00Z",
                     "body": "[claude:pm] LEASE #12\n\nuntil: 2000-01-01T00:00:00Z"})
    fake.route("GET", "repos/acme/widgets/issues/10/comments", comments)
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(project / ".meld.toml"), "--compact", "status"], env=env_for(project))
    data = json.loads(result.output)
    lease = {i["number"]: i for i in data["issues"]}[12]["lease"]
    assert lease == {"group": "claude", "until": "2000-01-01T00:00:00Z", "expired": True}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_grammar.py tests/test_board.py tests/test_assemble.py tests/test_rules_issues.py tests/test_status.py -q`
Expected: FAIL (import errors for `Lease`, `mk_lease`; `LEASE` header parses to `None`).

- [ ] **Step 3: Grammar**

In `src/meld/grammar.py` add `"LEASE", "EXPIRED"` to `PM_VERBS` and to `REQUIRES_REF`.

- [ ] **Step 4: Board**

In `src/meld/board.py` add `import re`, `from dataclasses import dataclass, field`, `from datetime import datetime, timezone`, then:

```python
_LEASE_UNTIL_RE = re.compile(r"^\s*until:\s*(\S+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Lease:
    issue: int
    group: str
    until: str
    comment_id: int


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_lease_until(text: str) -> str | None:
    m = _LEASE_UNTIL_RE.search(text or "")
    return m.group(1) if m else None


def derive_leases(comments: Sequence[ParsedComment]) -> dict[int, Lease]:
    leases: dict[int, Lease] = {}
    for c in _ordered(comments):
        if not c.ok or c.header is None or not c.header.ref or not c.header.ref.startswith("#") or not c.header.ref[1:].isdigit():
            continue
        n = int(c.header.ref[1:])
        if c.header.verb == "LEASE" and c.header.party.kind == "pm" and c.header.party.group:
            until = parse_lease_until(c.text)
            if until:
                leases[n] = Lease(n, c.header.party.group, until, c.id)
        elif (c.header.verb == "EXPIRED" and c.header.party.kind == "pm") or (c.header.verb == "RELEASE" and c.header.party.kind == "human"):
            leases.pop(n, None)
    return leases
```

Add three trailing fields and one method to `Board`:

```python
    leases: dict[int, Lease] = field(default_factory=dict)
    plan_text: str = ""
    now: str = ""

    def lease_expired(self, issue: int) -> bool:
        lease = self.leases.get(issue)
        return bool(lease and self.now and lease.until < self.now)
```

- [ ] **Step 5: GitHub helpers**

In `src/meld/github.py` add:

```python
    def get_milestone(self, number: int) -> dict:
        return self.get(self._r(f"milestones/{number}"))

    def update_comment(self, comment_id: int, body: str) -> dict:
        return self.patch(self._r(f"issues/comments/{comment_id}"), {"body": body})
```

Add to `tests/test_github.py`:

```python
def test_update_comment_and_get_milestone(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("PATCH", "repos/acme/widgets/issues/comments/110", {"id": 110})
    fake.route("GET", "repos/acme/widgets/milestones/3", {"number": 3, "description": "plan"})
    assert gh.update_comment(110, "new body")["id"] == 110
    assert fake.calls[0].method == "PATCH" and fake.calls[0].body == {"body": "new body"}
    assert gh.get_milestone(3)["description"] == "plan"
```

- [ ] **Step 6: Assemble**

In `src/meld/assemble.py`: import `derive_leases, utc_now_iso` from `meld.board`. Change `_locate_active` to return `(milestone_number, coordination_number, raw_issues, description)` where `description = m.get("description") or ""`; update `find_active_milestone` to unpack four values. Change `build_board`'s signature to `build_board(gh, cfg, runs, spend, milestone=None, now=None)`; on the explicit-milestone path fetch `description = gh.get_milestone(milestone).get("description") or ""`. Pass `leases=derive_leases(comments)`, `plan_text=description`, `now=now or utc_now_iso()` to `Board(...)`. Update `tests/conftest.py::load_board1_routes` to also route `GET repos/acme/widgets/milestones/3` to the first element of `milestones.json`.

- [ ] **Step 7: Rules**

In `src/meld/rules.py` replace `_release` with:

```python
def lease_rule(board: Board, issue: Issue) -> str | None:
    """Why a non-owner may still release: the owner's lease has expired."""
    lease = board.leases.get(issue.number)
    if lease is None:
        return None
    return f"lease by {lease.group} expired at {lease.until}" if board.lease_expired(issue.number) else None


def _release(board: Board, cfg: Config, actor: Actor, issue: Issue) -> str | None:
    if issue.status not in ("in_progress", "blocked"):
        return f"issue is {issue.status}; release is only legal from in-progress or blocked"
    if actor.is_human:
        return None
    group = cfg.groups[actor.group]  # type: ignore[index]
    if group.bot in issue.assignees:
        return None
    if actor.is_pm and lease_rule(board, issue):
        return None
    return f"issue is not assigned to {group.bot}"
```

- [ ] **Step 8: Status and factories**

In `src/meld/status.py`, inside the `issues` comprehension add `"lease": _lease_json(board, i)` and define:

```python
def _lease_json(board: Board, issue) -> dict | None:
    lease = board.leases.get(issue.number)
    if lease is None:
        return None
    return {"group": lease.group, "until": lease.until, "expired": board.lease_expired(issue.number)}
```

In `tests/factories.py` import `Lease` from `meld.board`, extend `mk_board(..., leases: dict[int, Lease] | None = None, plan_text: str = "", now: str = "2026-09-03T12:00:00Z")` passing `leases=leases or {}, plan_text=plan_text, now=now`, and add:

```python
def mk_lease(issue: int, group: str = "claude", until: str = "2026-09-03T13:00:00Z", comment_id: int = 900) -> Lease:
    return Lease(issue, group, until, comment_id)
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS, no warnings.

- [ ] **Step 10: Commit**

```bash
git add src/meld/grammar.py src/meld/board.py src/meld/github.py src/meld/assemble.py src/meld/rules.py src/meld/status.py tests
git commit -m "feat: leases as editable PM comments, plan text on the board, expired-lease release"
```

---

### Task 5: Worker contract, brief rendering, and the result file

**Files:**
- Create: `src/meld/prompts/_worker_contract.md`
- Create: `src/meld/brief.py`
- Create: `tests/test_brief.py`

**Interfaces:**
- Produces (in `meld.brief`): `BRIEF_FILE = ".meld-brief.md"`, `RESULT_FILE = ".meld-result.json"`, `OUTCOMES = ("done", "failed", "blocked")`, `VERDICTS = ("approve", "request-changes", "push-fix", "pass", "fail")`; `class ResultError(ValueError)`; `@dataclass(frozen=True) WorkerResult(outcome: str, summary: str, verified_by: str, verdict: str | None, comments: tuple[dict, ...], needs_human: str | None)`; `read_result(worktree: Path) -> WorkerResult | None` (None when the file is absent; `ResultError` when malformed or outcome/verdict invalid); `worker_contract() -> str` (packaged `_worker_contract.md`); `plan_excerpt(plan_text: str, issue_number: int) -> str` (paragraphs mentioning `#<n>`, joined by blank lines); `file_hints(issue_body: str) -> list[str]` (lines under a `Files:`/`## Files` heading until a blank line, else every backticked path-looking token); `decision_titles(repo_root: Path) -> list[str]` (first `# ` heading of each `.meld/decisions/*.md`); `review_bar(repo_root: Path) -> str` (`.meld/REVIEW.md` text or `""`); `agent_instruction_files(worktree: Path) -> list[str]` (which of `CLAUDE.md`, `AGENTS.md`, `.github/copilot-instructions.md` exist); `render_brief(*, role: WorkerType, kind: str, group: str, model: str, effort: str, issue: Issue | None, pr: PullRequest | None, plan_text: str, repo_root: Path, worktree: Path, base_branch: str) -> str`.

- [ ] **Step 1: Write the worker contract**

`src/meld/prompts/_worker_contract.md`:

```markdown
## The worker contract

You are one worker in a group of AI agents coordinated by Meld. You have exactly one task, described above, and one working directory: this git worktree. Everything below is non-negotiable.

1. **Read this brief first, then the repository's own instructions** (listed above, if any). Product conventions win over anything generic in this brief.
2. **Work only in this worktree.** Do not touch files outside it. Do not change git remotes, branches, or history; commit on the branch you were given. Commit small, with plain conventional-commit messages. Never add attribution lines.
3. **You have no GitHub credentials, on purpose.** Do not try to push, open pull requests, comment, label, or merge. The framework does that from your result file. If you find yourself wanting to talk to GitHub, stop and record it as `needs_human` or in your summary instead.
4. **Run the product's tests** for what you changed before you finish. A claim like "tests pass" without the command and its output is not evidence and will be challenged.
5. **Cite only what you opened.** Every file and line you reference in your summary or review must be something you actually read in this worktree at the current head. If you could not verify a citation, drop it and say so.
6. **When you are done, write `.meld-result.json`** in the worktree root and stop. Its shape:

```json
{
  "outcome": "done | failed | blocked",
  "summary": "What you did, what you verified, and what remains, in plain sentences.",
  "verified_by": "The exact command(s) you ran and their outcome, e.g. `uv run pytest -q` -> 141 passed",
  "verdict": null,
  "comments": [],
  "needs_human": null
}
```

- `outcome` is `done` when the task is complete and verified; `failed` when you could not complete it and another attempt with more context might; `blocked` when a person must do something first (put what they must do in `needs_human`).
- `verdict` is only for reviewer and auditor workers: reviewers use `approve`, `request-changes`, or `push-fix`; auditors use `pass` or `fail`. Builders leave it `null`.
- `comments` is only for reviewers and auditors: a list of `{"path": "...", "line": 12, "body": "..."}` objects, each citing a file and line you opened.
7. **Never stall.** If you cannot make progress, write the result file with `failed` or `blocked` and stop. A silent exit with no result file is treated as a failure.
8. **Do not modify `.meld-brief.md`** and do not write anything else outside your task's files except `.meld-result.json`.
```

- [ ] **Step 2: Write the failing tests**

`tests/test_brief.py`:

```python
import json
from pathlib import Path

import pytest

from meld.brief import (
    BRIEF_FILE,
    RESULT_FILE,
    ResultError,
    WorkerResult,
    agent_instruction_files,
    decision_titles,
    file_hints,
    plan_excerpt,
    read_result,
    render_brief,
    review_bar,
    worker_contract,
)
from meld.roster import builtin_workers
from tests.factories import mk_issue, mk_pr

PLAN = """# Plan
Intro paragraph.

## Milestone 1
- #11 Add the health endpoint at GET /healthz returning 200.
  Keep it dependency-free.

- #12 Login form with OAuth.

Closing notes mention #11 again.
"""


def test_plan_excerpt_collects_paragraphs_mentioning_the_issue():
    excerpt = plan_excerpt(PLAN, 11)
    assert "GET /healthz" in excerpt and "dependency-free" in excerpt and "mention #11 again" in excerpt
    assert "OAuth" not in excerpt
    assert plan_excerpt(PLAN, 99) == ""
    assert "#111" not in plan_excerpt("- #111 other\n\n- #11 mine", 11)


def test_file_hints_prefers_files_section_then_backticked_paths():
    body = "Do the thing.\n\nFiles:\n- src/app/health.py\n- tests/test_health.py\n\nMore prose with `src/other.py`."
    assert file_hints(body) == ["src/app/health.py", "tests/test_health.py"]
    assert file_hints("Touch `src/a.py` and `docs/b.md` but not `not-a-path`.") == ["src/a.py", "docs/b.md"]
    assert file_hints("nothing here") == []


def test_repo_local_context_files(tmp_path: Path):
    assert decision_titles(tmp_path) == [] and review_bar(tmp_path) == "" and agent_instruction_files(tmp_path) == []
    (tmp_path / ".meld" / "decisions").mkdir(parents=True)
    (tmp_path / ".meld" / "decisions" / "0001-db.md").write_text("# 0001 Use SQLite for v1\n\ncontext\n")
    (tmp_path / ".meld" / "REVIEW.md").write_text("Behavior claims need a file:line citation.\n")
    (tmp_path / "CLAUDE.md").write_text("# rules\n")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "copilot-instructions.md").write_text("x")
    assert decision_titles(tmp_path) == ["0001 Use SQLite for v1"]
    assert review_bar(tmp_path).startswith("Behavior claims")
    assert agent_instruction_files(tmp_path) == ["CLAUDE.md", ".github/copilot-instructions.md"]


def test_render_brief_for_a_builder_and_a_reviewer(tmp_path: Path):
    (tmp_path / ".meld" / "decisions").mkdir(parents=True)
    (tmp_path / ".meld" / "decisions" / "0001-db.md").write_text("# 0001 Use SQLite for v1\n")
    (tmp_path / ".meld" / "REVIEW.md").write_text("Cite file:line for every claim.\n")
    (tmp_path / "AGENTS.md").write_text("x")
    backend = next(w for w in builtin_workers() if w.name == "backend")
    issue = mk_issue(11, title="Add health endpoint", size="S")
    text = render_brief(role=backend, kind="build", group="claude", model="claude-sonnet-5", effort="medium", issue=issue, pr=None,
                        plan_text=PLAN, repo_root=tmp_path, worktree=tmp_path, base_branch="main")
    assert text.startswith("# Meld brief: backend for #11")
    for needle in ("Add health endpoint", "GET /healthz", "0001 Use SQLite for v1", "Cite file:line", "AGENTS.md",
                   "claude-sonnet-5", "effort: medium", "## The worker contract", RESULT_FILE, "branch `meld/11-"):
        assert needle in text, needle
    reviewer = next(w for w in builtin_workers() if w.name == "reviewer")
    pr = mk_pr(20, 13, committer="meld-codex-bot")
    text = render_brief(role=reviewer, kind="review", group="claude", model="claude-opus-5", effort="high", issue=None, pr=pr,
                        plan_text=PLAN, repo_root=tmp_path, worktree=tmp_path, base_branch="main")
    assert text.startswith("# Meld brief: reviewer for PR #20") and "codex" in text and "verdict" in text


def test_read_result_missing_valid_and_malformed(tmp_path: Path):
    assert read_result(tmp_path) is None
    (tmp_path / RESULT_FILE).write_text(json.dumps({"outcome": "done", "summary": "did it", "verified_by": "pytest -> 3 passed"}))
    assert read_result(tmp_path) == WorkerResult("done", "did it", "pytest -> 3 passed", None, (), None)
    (tmp_path / RESULT_FILE).write_text(json.dumps({"outcome": "done", "summary": "r", "verified_by": "x", "verdict": "approve",
                                                    "comments": [{"path": "a.py", "line": 3, "body": "nit"}]}))
    assert read_result(tmp_path).verdict == "approve" and read_result(tmp_path).comments[0]["line"] == 3
    (tmp_path / RESULT_FILE).write_text("{not json")
    with pytest.raises(ResultError, match="not valid JSON"):
        read_result(tmp_path)
    (tmp_path / RESULT_FILE).write_text(json.dumps({"outcome": "maybe", "summary": "", "verified_by": ""}))
    with pytest.raises(ResultError, match="outcome"):
        read_result(tmp_path)
    (tmp_path / RESULT_FILE).write_text(json.dumps({"outcome": "done", "summary": "s", "verified_by": "v", "verdict": "meh"}))
    with pytest.raises(ResultError, match="verdict"):
        read_result(tmp_path)
    assert BRIEF_FILE == ".meld-brief.md" and "needs_human" in worker_contract()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_brief.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.brief'`

- [ ] **Step 4: Write `src/meld/brief.py`**

```python
"""Render the worker brief and read the worker result file."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from meld.board import Issue, PullRequest
from meld.gitops import branch_name
from meld.roster import WorkerType

BRIEF_FILE = ".meld-brief.md"
RESULT_FILE = ".meld-result.json"
OUTCOMES = ("done", "failed", "blocked")
VERDICTS = ("approve", "request-changes", "push-fix", "pass", "fail")
_AGENT_FILES = ("CLAUDE.md", "AGENTS.md", ".github/copilot-instructions.md")
_PATH_TOKEN_RE = re.compile(r"`([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)`")
_FILES_HEADING_RE = re.compile(r"^\s*(?:#+\s*)?files:?\s*$", re.IGNORECASE)


class ResultError(ValueError):
    pass


@dataclass(frozen=True)
class WorkerResult:
    outcome: str
    summary: str
    verified_by: str
    verdict: str | None
    comments: tuple[dict, ...]
    needs_human: str | None


def worker_contract() -> str:
    return resources.files("meld").joinpath("prompts", "_worker_contract.md").read_text()


def plan_excerpt(plan_text: str, issue_number: int) -> str:
    pattern = re.compile(rf"#{issue_number}(?!\d)")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", plan_text or "") if p.strip()]
    return "\n\n".join(p for p in paragraphs if pattern.search(p))


def file_hints(issue_body: str) -> list[str]:
    lines = (issue_body or "").splitlines()
    for i, line in enumerate(lines):
        if _FILES_HEADING_RE.match(line):
            hints: list[str] = []
            for follow in lines[i + 1:]:
                if not follow.strip():
                    break
                hints.append(follow.strip().lstrip("-*").strip().strip("`"))
            if hints:
                return hints
    return [m for m in _PATH_TOKEN_RE.findall(issue_body or "") if "/" in m or m.endswith((".py", ".md", ".toml", ".ts", ".js"))]


def decision_titles(repo_root: Path) -> list[str]:
    d = repo_root / ".meld" / "decisions"
    titles: list[str] = []
    if d.is_dir():
        for f in sorted(d.glob("*.md")):
            for line in f.read_text().splitlines():
                if line.startswith("# "):
                    titles.append(line[2:].strip())
                    break
    return titles


def review_bar(repo_root: Path) -> str:
    f = repo_root / ".meld" / "REVIEW.md"
    return f.read_text() if f.is_file() else ""


def agent_instruction_files(worktree: Path) -> list[str]:
    return [name for name in _AGENT_FILES if (worktree / name).is_file()]


def render_brief(*, role: WorkerType, kind: str, group: str, model: str, effort: str, issue: Issue | None, pr: PullRequest | None,
                 plan_text: str, repo_root: Path, worktree: Path, base_branch: str) -> str:
    if issue is not None:
        title = f"# Meld brief: {role.name} for #{issue.number}"
        task = f"## Your task\n\n**#{issue.number}: {issue.title}**\n\n{issue.body.strip() or '(no description)'}\n"
        task += f"\nYou are on branch `{branch_name(issue.number, issue.title)}`, created from `{base_branch}`.\n"
        excerpt = plan_excerpt(plan_text, issue.number)
        hints = file_hints(issue.body)
    else:
        assert pr is not None
        title = f"# Meld brief: {role.name} for PR #{pr.number}"
        task = (f"## Your task\n\n**PR #{pr.number}: {pr.title}** (branch `{pr.head_ref}`, head `{pr.head_sha[:7]}`, "
                f"authored by `{pr.author}`, last commit by `{pr.head_committer or 'unknown'}`).\n\n{pr.body.strip() or '(no description)'}\n"
                f"\nThe checkout is the PR head. Your verdict goes in the `verdict` field of the result file.\n")
        excerpt = plan_excerpt(plan_text, pr.issue_number) if pr.issue_number else ""
        hints = []
    parts = [title, "", f"You are the **{role.name}** worker of group **{group}**, running as `{model}` at effort: {effort}.", "",
             "## Who you are", "", role.text.strip() or role.description, "", task]
    if excerpt:
        parts += ["## Plan excerpt", "", excerpt, ""]
    if hints:
        parts += ["## Files to start from", ""] + [f"- `{h}`" for h in hints] + [""]
    decisions = decision_titles(repo_root)
    if decisions:
        parts += ["## Decisions in force", ""] + [f"- {t}" for t in decisions] + ["", "Read the matching file under `.meld/decisions/` before departing from any of them.", ""]
    bar = review_bar(repo_root)
    if bar:
        parts += ["## Verification bar for this repository", "", bar.strip(), ""]
    agent_files = agent_instruction_files(worktree)
    if agent_files:
        parts += ["## Repository instructions", ""] + [f"- `{f}`" for f in agent_files] + ["", "These win over anything generic below.", ""]
    parts += [worker_contract().strip(), ""]
    return "\n".join(parts)


def read_result(worktree: Path) -> WorkerResult | None:
    f = worktree / RESULT_FILE
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ResultError(f"{RESULT_FILE} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ResultError(f"{RESULT_FILE} must be a JSON object")
    outcome = data.get("outcome")
    if outcome not in OUTCOMES:
        raise ResultError(f"{RESULT_FILE}: outcome must be one of {', '.join(OUTCOMES)}, got {outcome!r}")
    verdict = data.get("verdict")
    if verdict is not None and verdict not in VERDICTS:
        raise ResultError(f"{RESULT_FILE}: verdict must be one of {', '.join(VERDICTS)} or null, got {verdict!r}")
    comments = data.get("comments") or []
    if not isinstance(comments, list) or not all(isinstance(c, dict) for c in comments):
        raise ResultError(f"{RESULT_FILE}: comments must be a list of objects")
    needs_human = data.get("needs_human")
    return WorkerResult(outcome=outcome, summary=str(data.get("summary", "")), verified_by=str(data.get("verified_by", "")),
                        verdict=verdict, comments=tuple(comments), needs_human=str(needs_human) if needs_human else None)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_brief.py -v` then `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/meld/prompts/_worker_contract.md src/meld/brief.py tests/test_brief.py
git commit -m "feat: worker contract, brief rendering, and result-file parsing"
```

---

### Task 6: Full worker prompts

**Files:**
- Modify: `src/meld/prompts/workers/backend.md`, `frontend.md`, `security.md`, `qa.md`, `reviewer.md`, `auditor.md`, `docs.md`
- Create: `tests/test_prompts.py`

**Interfaces:**
- Each file keeps its first two lines exactly (heading and the one-line description the roster tests assert) and gains the sections `## Focus`, `## Method`, `## Definition of done`, `## Evidence you must record`. `WorkerType.text` (the whole file) is what `render_brief` embeds.

- [ ] **Step 1: Write the failing tests**

`tests/test_prompts.py`:

```python
from meld.roster import builtin_workers

REQUIRED = ("## Focus", "## Method", "## Definition of done", "## Evidence you must record")


def test_every_builtin_worker_has_the_full_brief_sections():
    workers = {w.name: w for w in builtin_workers()}
    assert set(workers) == {"auditor", "backend", "docs", "frontend", "qa", "reviewer", "security"}
    for w in workers.values():
        for section in REQUIRED:
            assert section in w.text, (w.name, section)
        assert "verified_by" in w.text or "verified-by" in w.text, w.name


def test_reviewer_and_auditor_prompts_carry_the_verification_rules():
    workers = {w.name: w for w in builtin_workers()}
    reviewer, auditor = workers["reviewer"].text, workers["auditor"].text
    for needle in ("open the file", "request-changes", "push-fix", "approve", "no style-only", "correctness", "security"):
        assert needle in reviewer, needle
    for needle in ("acceptance criteria", "pass", "fail", "re-run", "verified_by"):
        assert needle in auditor, needle
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: FAIL on the missing sections.

- [ ] **Step 3: Write the seven prompt bodies**

Replace each file's contents. The first two lines of each must stay identical to today's.

`src/meld/prompts/workers/backend.md`:

```markdown
# backend

Implements server-side code, APIs, data models, and migrations, with tests for everything it builds.

## Focus

Server-side behavior: request handling, domain logic, persistence, migrations, background jobs, and the tests that prove them. You do not touch user-facing markup or styling unless the task says so.

## Method

1. Read the task, the plan excerpt, and the repository instructions. Find the existing patterns for the layer you are changing (how a handler is registered, how a model is defined, how a migration is written) and follow them.
2. Write the failing test first when the repository has a test suite; make it pass with the smallest change that is correct, then refactor only what you touched.
3. Keep each commit to one logical change with a conventional-commit message.
4. Run the narrowest relevant tests while iterating and the full suite once before you finish.

## Definition of done

- The behavior in the task is implemented and covered by tests that fail without your change.
- The full test suite passes; output is warning-free unless the repository already had warnings.
- No unrelated files changed; no TODOs or commented-out code left behind.
- The result file's `summary` names each acceptance criterion and how you met it.

## Evidence you must record

`verified_by` must contain the exact test command(s) and the pass/fail counts you observed. Summaries that say "tests pass" without a command are challenged and the task is reopened.
```

`src/meld/prompts/workers/frontend.md`:

```markdown
# frontend

Builds user-facing components, pages, and styling; verifies rendering and accessibility with tests.

## Focus

Components, pages, routing, state handling in the client, styling, and accessibility. You call existing APIs; you do not change server behavior unless the task says so.

## Method

1. Find the repository's component conventions (framework, styling approach, test runner) before writing anything, and reuse existing components rather than adding parallel ones.
2. Build against the acceptance criteria in the task: what the user sees, what happens on interaction, what happens on error and while loading.
3. Add or extend component tests for behavior (rendered text, interactions, error states) rather than snapshots alone; check keyboard access and labels for anything interactive.
4. Run the relevant tests while iterating and the full suite once before you finish.

## Definition of done

- Every state the task names (default, loading, error, empty) renders and is tested.
- Interactive elements are reachable by keyboard and have accessible names.
- Lint and type checks the repository defines pass; the full suite passes.
- No unrelated files changed.

## Evidence you must record

`verified_by` lists the test, lint, and type-check commands you ran with their results. If a visual check was needed and you could not perform it, say so explicitly in `summary`.
```

`src/meld/prompts/workers/security.md`:

```markdown
# security

Reviews or implements authentication, authorization, secrets handling, and input validation; reports findings with evidence.

## Focus

Trust boundaries: who can call what, with which identity, on which data. Authentication flows, authorization checks, secrets and token handling, input validation and encoding, dependency and configuration exposure.

## Method

1. Map the boundary the task touches: entry points, identities involved, data read or written. Write it down in your summary.
2. For implementation tasks, prefer the repository's existing primitives (its auth middleware, validators, secret loading) over new code; add tests that prove denial as well as success.
3. For review tasks, trace each finding to a specific file and line you opened in this worktree, state the concrete attack or failure it enables, and rate it Critical, Important, or Minor. Do not report a finding you cannot cite.
4. Never print, log, or commit a secret value; reference where a secret is stored, never what it is.

## Definition of done

- Implementation: the control exists, is tested for both the allowed and the denied path, and the full suite passes.
- Review: every finding has file:line, impact, and a fix; anything you could not verify is listed separately as unverified rather than asserted.

## Evidence you must record

`verified_by` lists the commands you ran (tests, scanners, greps for secret patterns) and their results. For reviews, put the findings in `comments` with `path`, `line`, and `body`.
```

`src/meld/prompts/workers/qa.md`:

```markdown
# qa

Writes and runs end-to-end and regression tests against acceptance criteria; files precise failure reports.

## Focus

Behavior as the user or caller experiences it: end-to-end and integration tests, regression tests for fixed bugs, and precise reports when reality disagrees with the acceptance criteria.

## Method

1. Turn each acceptance criterion in the task into at least one executable check. Name tests after the behavior they prove.
2. Use the repository's existing test harness and fixtures; add fixtures only when none fit.
3. Run the new tests against the current head. When one fails because the product is wrong rather than the test, do not paper over it: record the exact failing command, output, and the criterion it violates in `summary`, and set `outcome` to `done` if the tests themselves are complete or `blocked` if the product must change first.
4. Run the full suite once before you finish.

## Definition of done

- Every acceptance criterion maps to a named test.
- The suite is deterministic: no sleeps for timing, no network, no dependence on the wall clock.
- Failures you found are reported with reproduction commands, not just described.

## Evidence you must record

`verified_by` contains the commands and pass/fail counts for the new tests and the full suite. Product failures go in `summary` with their reproduction command.
```

`src/meld/prompts/workers/reviewer.md`:

```markdown
# reviewer

Reviews another group's pull request for correctness, security, tests, and conventions; approves, requests changes, or pushes a fix.

## Focus

Another group built this. Your job is to find what they missed, in this order of priority: correctness, security, tests, then repository conventions. You are not here to restyle their code.

## Method

1. Read the linked issue and plan excerpt so you know what the PR was supposed to do, then read the diff against the base branch (`git diff origin/<base>...HEAD`).
2. **Cite only what you opened.** Before you write any file:line reference, open the file at the PR head in this worktree and confirm the line says what you claim. A citation you could not verify is dropped and mentioned as such. Reviews are challenged when citations do not hold.
3. Run the product's tests yourself. Do not trust the PR body's test claims; verify them and note when they were wrong.
4. Decide the verdict:
   - `approve` when the PR does what the issue asked, is correct as far as you can tell, and is tested.
   - `request-changes` when something must change and the fix is more than trivial, or when you are not sure how the author would want it fixed. Put each required change in `comments` with `path`, `line`, `body`.
   - `push-fix` only for small, unambiguous corrections (a typo in a message, a missing null check with an obvious fix, a broken import): make the change, add or adjust the test that covers it, commit with a conventional message that names what you fixed, and describe it in `summary`. A push-fix makes your group the last committer, so your group can no longer approve this head.
5. Make no style-only change requests. Flag Critical and Important issues; put nits in `comments` marked "nit:" and do not let them decide the verdict.

## Definition of done

- The verdict is set and every `request-changes` item cites a verified file and line.
- Tests were run and their result is recorded, including any disagreement with the PR body.
- Your summary names what the PR does well before what it gets wrong.

## Evidence you must record

`verified_by` lists the test commands you ran and their results, and states how many citations you verified and how many you dropped.
```

`src/meld/prompts/workers/auditor.md`:

```markdown
# auditor

Independently re-checks merged work against its acceptance criteria and posted evidence; reports pass or fail per PR.

## Focus

Merged pull requests your group did not author. You are the check that no group grades its own claims: does the code at the merge commit actually meet the issue's acceptance criteria, and did the posted evidence hold?

## Method

1. For each PR in your brief, read the linked issue's acceptance criteria and the PR body's `verified-by` claims.
2. Check out is already at the merge commit. Locate the code that implements each criterion and open it; note the file and line.
3. Re-run the tests the PR body claims were run, and the full suite. Record the exact commands and results.
4. Give a verdict per PR: `pass` when every criterion is met and the evidence held; `fail` when any criterion is unmet, any claimed test does not pass, or a claimed citation does not exist. A `fail` reopens the issue, so state precisely which criterion failed and how you know.

## Definition of done

- Every PR in the brief has a `pass` or `fail` with file:line evidence for each criterion.
- Every re-run command and its result is recorded.
- Nothing is asserted that you did not open or run yourself.

## Evidence you must record

`verified_by` contains the re-run commands and results. `verdict` is `pass` or `fail`; per-criterion findings go in `comments` with `path`, `line`, and `body`.
```

`src/meld/prompts/workers/docs.md`:

```markdown
# docs

Writes and updates documentation, decision records, and worker-type prompt files; keeps them consistent with the code.

## Focus

Documentation that has to match reality: READMEs, runbooks, ADR-style decision records under `.meld/decisions/`, lessons under `.meld/memory/`, and worker prompt files under `.meld/workers/`.

## Method

1. Read the code or decision you are documenting before writing; every statement about behavior must be traceable to a file you opened or a command you ran.
2. Follow the existing document structure and voice. For a worker prompt file, keep the required shape: a `# <name>` heading, one description line, then Focus, Method, Definition of done, and Evidence sections.
3. When the task is to record an agreed decision, copy the agreed text verbatim into the decision file and add only the required metadata (date, groups, link).
4. Run whatever checks the repository has for docs (link checkers, formatters, the test suite if docs are tested).

## Definition of done

- The document says what the code does, in the repository's voice, with no placeholders.
- Cross-references resolve; commands shown were actually run.
- No unrelated files changed.

## Evidence you must record

`verified_by` lists the commands you ran to confirm the documented behavior and any doc checks the repository provides.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_prompts.py tests/test_roster.py -v`
Expected: PASS (roster tests still see the same descriptions).

- [ ] **Step 5: Commit**

```bash
git add src/meld/prompts/workers tests/test_prompts.py
git commit -m "feat: full worker prompts with method, definition of done, and evidence rules"
```

---

### Task 7: Adapter protocol, environment scrubbing, skills scanning, fake adapter

**Files:**
- Create: `src/meld/adapters/__init__.py`, `src/meld/adapters/base.py`
- Create: `tests/fakeadapter.py`, `tests/test_adapters.py`

**Interfaces:**
- Produces (in `meld.adapters.base`): `@dataclass(frozen=True) LaunchSpec(role: str, model: str, effort: str, cwd: Path, prompt: str, log_path: Path, timeout_seconds: int, env: dict[str, str], max_turns: int)`; `class Adapter(Protocol)` with `name: str`, `launch(self, spec: LaunchSpec) -> subprocess.Popen`, `installed_skills(self) -> list[WorkerType]`, `command_timeout_ceiling_seconds(self) -> int`, `supports_effort(self) -> bool`; `SCRUBBED_EXACT = ("GH_TOKEN", "GITHUB_TOKEN", "MELD_GROUP", "MELD_ROLE", "MELD_PUSH_TOKEN")`, `SCRUBBED_PREFIXES = ("MELD_TOKEN_",)`; `scrub_env(env: Mapping[str, str]) -> dict[str, str]` (drops those, sets `MELD_WORKER=1`); `parse_frontmatter(text: str) -> dict[str, str]`; `scan_skills(dirs: Iterable[Path], source: str = "skill") -> list[WorkerType]` (each `<dir>/<name>/SKILL.md`, skipping dot-directories such as Codex's `.system`; name from frontmatter `name` or the directory; description from frontmatter `description`; `text` is the file contents; sorted by name; duplicates keep the first); `open_log(path: Path)` returning an appendable binary file handle after creating parent directories; `popen_detached(argv, spec, log) -> subprocess.Popen` (stdin devnull, stdout and stderr to the log, `cwd=spec.cwd`, `env=spec.env`, `start_new_session=True`).
- `meld.adapters`: `get_adapter(name: str) -> Adapter` (lazy import of `meld.adapters.claude`/`codex`), `class UnknownAdapter(KeyError)`, `ADAPTER_NAMES = ("claude", "codex")`.
- `tests/fakeadapter.py`: `class FakeAdapter` implementing the protocol: `name = "fake"`, `launches: list[LaunchSpec]`, constructor `FakeAdapter(script: str | None = None, skills: list[WorkerType] = (), ceiling: int = 600)`; `launch` spawns `sys.executable -c <script>` in `spec.cwd` (default script sleeps 0.2 s and exits 0), recording the spec; `installed_skills` returns `skills`.

- [ ] **Step 1: Write the fake adapter**

`tests/fakeadapter.py`:

```python
"""A fake CLI adapter: spawns tiny python processes instead of claude/codex."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

from meld.adapters.base import LaunchSpec, open_log, popen_detached
from meld.roster import WorkerType

DEFAULT_SCRIPT = "import time; time.sleep(0.2)"


@dataclass
class FakeAdapter:
    script: str | None = None
    skills: list[WorkerType] = field(default_factory=list)
    ceiling: int = 600
    name: str = "fake"
    launches: list[LaunchSpec] = field(default_factory=list)

    def launch(self, spec: LaunchSpec):
        self.launches.append(spec)
        with open_log(spec.log_path) as log:
            return popen_detached([sys.executable, "-c", self.script or DEFAULT_SCRIPT], spec, log)

    def installed_skills(self) -> list[WorkerType]:
        return list(self.skills)

    def command_timeout_ceiling_seconds(self) -> int:
        return self.ceiling

    def supports_effort(self) -> bool:
        return True
```

- [ ] **Step 2: Write the failing tests**

`tests/test_adapters.py`:

```python
import os
import time
from pathlib import Path

import pytest

from meld.adapters import ADAPTER_NAMES, UnknownAdapter, get_adapter
from meld.adapters.base import LaunchSpec, parse_frontmatter, scan_skills, scrub_env
from tests.fakeadapter import FakeAdapter


def test_scrub_env_drops_credentials_and_party_and_marks_worker():
    env = {"PATH": "/bin", "HOME": "/Users/x", "GH_TOKEN": "a", "GITHUB_TOKEN": "b", "MELD_TOKEN_CLAUDE": "c",
           "MELD_TOKEN_CODEX": "d", "MELD_GROUP": "claude", "MELD_ROLE": "pm", "MELD_PUSH_TOKEN": "e", "MELD_HOME": "/h"}
    out = scrub_env(env)
    assert out == {"PATH": "/bin", "HOME": "/Users/x", "MELD_HOME": "/h", "MELD_WORKER": "1"}


def test_parse_frontmatter_and_scan_skills(tmp_path: Path):
    assert parse_frontmatter("no frontmatter") == {}
    text = "---\nname: brand\ndescription: Brand voice and assets.\nother: x\n---\n# Brand\nbody"
    assert parse_frontmatter(text) == {"name": "brand", "description": "Brand voice and assets.", "other": "x"}
    a, b = tmp_path / "a", tmp_path / "b"
    (a / "brand").mkdir(parents=True)
    (a / "brand" / "SKILL.md").write_text(text)
    (a / "noskill").mkdir()
    (b / "zeta").mkdir(parents=True)
    (b / "zeta" / "SKILL.md").write_text("# zeta\n\nno frontmatter here\n")
    (b / "brand").mkdir()
    (b / "brand" / "SKILL.md").write_text("---\nname: brand\ndescription: duplicate\n---\n")
    (b / ".system" / "hidden").mkdir(parents=True)
    (b / ".system" / "hidden" / "SKILL.md").write_text("---\nname: hidden\n---\n")
    skills = scan_skills([a, b, tmp_path / "missing"])
    assert [(s.name, s.description, s.source) for s in skills] == [("brand", "Brand voice and assets.", "skill"), ("zeta", "", "skill")]
    assert skills[0].path == a / "brand" / "SKILL.md" and skills[0].text.startswith("---")


def test_registry_names_and_unknown():
    assert ADAPTER_NAMES == ("claude", "codex")
    with pytest.raises(UnknownAdapter):
        get_adapter("gemini")
    assert get_adapter("claude").name == "claude" and get_adapter("codex").name == "codex"


def test_fake_adapter_launches_detached_process_and_logs(tmp_path: Path):
    adapter = FakeAdapter(script="import sys; print('hello from worker'); sys.exit(3)")
    spec = LaunchSpec(role="worker", model="m", effort="low", cwd=tmp_path, prompt="Read .meld-brief.md",
                      log_path=tmp_path / "logs" / "w.log", timeout_seconds=60, env=scrub_env(dict(os.environ)), max_turns=5)
    proc = adapter.launch(spec)
    assert proc.pid > 0 and os.getpgid(proc.pid) == proc.pid  # its own session/process group
    assert proc.wait(timeout=10) == 3
    assert "hello from worker" in (tmp_path / "logs" / "w.log").read_text()
    assert adapter.launches == [spec]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_adapters.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.adapters'`

- [ ] **Step 4: Write `src/meld/adapters/base.py`**

```python
"""Adapter protocol: the only layer that knows how a particular CLI is launched."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterable, Mapping, Protocol

from meld.roster import WorkerType

SCRUBBED_EXACT = ("GH_TOKEN", "GITHUB_TOKEN", "MELD_GROUP", "MELD_ROLE", "MELD_PUSH_TOKEN")
SCRUBBED_PREFIXES = ("MELD_TOKEN_",)


@dataclass(frozen=True)
class LaunchSpec:
    role: str
    model: str
    effort: str
    cwd: Path
    prompt: str
    log_path: Path
    timeout_seconds: int
    env: dict[str, str]
    max_turns: int


class Adapter(Protocol):
    name: str

    def launch(self, spec: LaunchSpec) -> subprocess.Popen: ...

    def installed_skills(self) -> list[WorkerType]: ...

    def command_timeout_ceiling_seconds(self) -> int: ...

    def supports_effort(self) -> bool: ...


def scrub_env(env: Mapping[str, str]) -> dict[str, str]:
    """Environment for a worker: no GitHub credentials, no party identity, a marker that it is a worker."""
    out = {k: v for k, v in env.items() if k not in SCRUBBED_EXACT and not k.startswith(SCRUBBED_PREFIXES)}
    out["MELD_WORKER"] = "1"
    return out


def parse_frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def scan_skills(dirs: Iterable[Path], source: str = "skill") -> list[WorkerType]:
    found: dict[str, WorkerType] = {}
    for d in dirs:
        if not d.is_dir():
            continue
        for skill_dir in sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith(".")):
            f = skill_dir / "SKILL.md"
            if not f.is_file():
                continue
            text = f.read_text()
            meta = parse_frontmatter(text)
            name = meta.get("name") or skill_dir.name
            found.setdefault(name, WorkerType(name=name, description=meta.get("description", ""), source=source, path=f, text=text))
    return sorted(found.values(), key=lambda w: w.name)


def open_log(path: Path) -> IO[bytes]:
    path.parent.mkdir(parents=True, exist_ok=True)
    return open(path, "ab")


def popen_detached(argv: list[str], spec: LaunchSpec, log: IO[bytes]) -> subprocess.Popen:
    """Start the CLI in its own session so a timeout can kill the whole process group."""
    return subprocess.Popen(argv, cwd=str(spec.cwd), env=spec.env, stdin=subprocess.DEVNULL, stdout=log,
                            stderr=subprocess.STDOUT, start_new_session=True)
```

`src/meld/adapters/__init__.py`:

```python
"""Adapter registry. Import adapters lazily so a missing CLI never breaks the rest of meld."""
from __future__ import annotations

from importlib import import_module

from meld.adapters.base import Adapter, LaunchSpec, scrub_env

ADAPTER_NAMES = ("claude", "codex")


class UnknownAdapter(KeyError):
    pass


def get_adapter(name: str) -> Adapter:
    if name not in ADAPTER_NAMES:
        raise UnknownAdapter(f"unknown adapter {name!r}; known: {', '.join(ADAPTER_NAMES)}")
    module = import_module(f"meld.adapters.{name}")
    return module.ADAPTER


__all__ = ["ADAPTER_NAMES", "Adapter", "LaunchSpec", "UnknownAdapter", "get_adapter", "scrub_env"]
```

Until Tasks 8 and 9 land, `get_adapter("claude")`/`("codex")` would raise `ModuleNotFoundError`; the registry test therefore runs only after those tasks. Mark `test_registry_names_and_unknown` with `@pytest.mark.skipif(not (Path(__file__).parents[1] / "src" / "meld" / "adapters" / "codex.py").exists(), reason="adapters land in Tasks 8-9")` for now and remove the marker in Task 9.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_adapters.py -v` then `uv run pytest -q`
Expected: PASS (one skipped until Task 9).

- [ ] **Step 6: Commit**

```bash
git add src/meld/adapters tests/fakeadapter.py tests/test_adapters.py
git commit -m "feat: adapter protocol, worker environment scrubbing, skills scanning, fake adapter"
```

---

### Task 8: Claude Code adapter

**Files:**
- Create: `src/meld/adapters/claude.py`
- Create: `tests/test_adapter_claude.py`

**Interfaces:**
- Produces (in `meld.adapters.claude`): `class ClaudeAdapter` with `name = "claude"`, constructor `ClaudeAdapter(binary: str = "claude", home: Path | None = None)` (`home` defaults to `~/.claude`); `argv(spec: LaunchSpec) -> list[str]`; `env_for(spec) -> dict[str, str]`; `launch(spec)`; `installed_skills()`; `command_timeout_ceiling_seconds() -> int` (600: Claude Code's Bash tool caps a single command at 10 minutes, so `meld wait` inside an orchestrator or PM session must use a smaller timeout); `supports_effort() -> bool` (True); module-level `ADAPTER = ClaudeAdapter()`.
- Launch shape (from the current CLI reference, 2026-09-03): `claude -p <prompt> --model <model> --output-format json --permission-mode bypassPermissions --strict-mcp-config --mcp-config {"mcpServers":{}} --settings {"disableAllHooks":true,"autoMemoryEnabled":false} --setting-sources project --max-turns <n>`. Effort travels in the environment as `CLAUDE_CODE_EFFORT_LEVEL` (it overrides every other effort setting). The environment also sets `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` and `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, and removes `CLAUDECODE` and `CLAUDE_CODE_ENTRYPOINT` (a `claude` started from inside another Claude Code session otherwise refuses to run). `--bare` is deliberately not used: it skips OAuth and would require an API key, while this machine runs on a subscription login. `bypassPermissions` removes prompts; confinement comes from the worktree and the scrubbed environment, which is the documented setup for an externally sandboxed worker.
- Skills: `~/.claude/skills/*/SKILL.md` and `~/.claude/plugins/cache/*/*/*/skills/*/SKILL.md` (via `scan_skills`).

- [ ] **Step 1: Write the failing tests**

`tests/test_adapter_claude.py`:

```python
import json
from pathlib import Path

import meld.adapters.claude as claude_mod
from meld.adapters.base import LaunchSpec
from meld.adapters.claude import ClaudeAdapter


def spec(tmp_path: Path, **over) -> LaunchSpec:
    base = dict(role="worker", model="claude-sonnet-5", effort="high", cwd=tmp_path, prompt="Read .meld-brief.md and do it.",
                log_path=tmp_path / "log.txt", timeout_seconds=1200,
                env={"PATH": "/usr/bin", "HOME": "/Users/x", "CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli", "MELD_WORKER": "1"}, max_turns=200)
    base.update(over)
    return LaunchSpec(**base)


def test_argv_is_the_documented_unattended_shape(tmp_path: Path):
    argv = ClaudeAdapter().argv(spec(tmp_path))
    assert argv[:3] == ["claude", "-p", "Read .meld-brief.md and do it."]
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5"
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--strict-mcp-config" in argv and json.loads(argv[argv.index("--mcp-config") + 1]) == {"mcpServers": {}}
    assert json.loads(argv[argv.index("--settings") + 1]) == {"disableAllHooks": True, "autoMemoryEnabled": False}
    assert argv[argv.index("--setting-sources") + 1] == "project"
    assert argv[argv.index("--max-turns") + 1] == "200"
    assert "--effort" not in argv and "--bare" not in argv and "--dangerously-skip-permissions" not in argv


def test_env_sets_effort_and_strips_nested_session_guard(tmp_path: Path):
    env = ClaudeAdapter().env_for(spec(tmp_path))
    assert env["CLAUDE_CODE_EFFORT_LEVEL"] == "high" and env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert "CLAUDECODE" not in env and "CLAUDE_CODE_ENTRYPOINT" not in env
    assert env["PATH"] == "/usr/bin" and env["HOME"] == "/Users/x" and env["MELD_WORKER"] == "1"


def test_launch_uses_argv_env_and_log(tmp_path: Path, monkeypatch):
    captured = {}

    def fake_popen(argv, spec_, log):
        captured.update(argv=argv, spec=spec_, log_name=log.name)
        return "proc"

    monkeypatch.setattr(claude_mod, "popen_detached", fake_popen)
    s = spec(tmp_path)
    assert ClaudeAdapter().launch(s) == "proc"
    assert captured["argv"][0] == "claude" and captured["spec"].env["CLAUDE_CODE_EFFORT_LEVEL"] == "high"
    assert captured["spec"].cwd == tmp_path and captured["log_name"] == str(tmp_path / "log.txt")


def test_installed_skills_scans_personal_and_plugin_skills(tmp_path: Path):
    home = tmp_path / ".claude"
    (home / "skills" / "brand").mkdir(parents=True)
    (home / "skills" / "brand" / "SKILL.md").write_text("---\nname: brand\ndescription: Brand voice.\n---\n")
    plug = home / "plugins" / "cache" / "market" / "design" / "1.0.0" / "skills" / "slides"
    plug.mkdir(parents=True)
    (plug / "SKILL.md").write_text("---\nname: slides\ndescription: Decks.\n---\n")
    names = [(s.name, s.description) for s in ClaudeAdapter(home=home).installed_skills()]
    assert names == [("brand", "Brand voice."), ("slides", "Decks.")]
    assert ClaudeAdapter(home=tmp_path / "nowhere").installed_skills() == []
    assert ClaudeAdapter().command_timeout_ceiling_seconds() == 600 and ClaudeAdapter().supports_effort() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_adapter_claude.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.adapters.claude'`

- [ ] **Step 3: Write `src/meld/adapters/claude.py`**

```python
"""Claude Code adapter: one unattended `claude -p` run per launch."""
from __future__ import annotations

import json
from pathlib import Path

from meld.adapters.base import LaunchSpec, open_log, popen_detached, scan_skills
from meld.roster import WorkerType

_SETTINGS = {"disableAllHooks": True, "autoMemoryEnabled": False}
_MCP_CONFIG = {"mcpServers": {}}
_DROP_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")
BASH_TOOL_CEILING_SECONDS = 600  # Claude Code's Bash tool caps one command at 10 minutes


class ClaudeAdapter:
    name = "claude"

    def __init__(self, binary: str = "claude", home: Path | None = None):
        self.binary = binary
        self.home = home or Path.home() / ".claude"

    def argv(self, spec: LaunchSpec) -> list[str]:
        return [
            self.binary, "-p", spec.prompt,
            "--model", spec.model,
            "--output-format", "json",
            "--permission-mode", "bypassPermissions",
            "--strict-mcp-config", "--mcp-config", json.dumps(_MCP_CONFIG),
            "--settings", json.dumps(_SETTINGS),
            "--setting-sources", "project",
            "--max-turns", str(spec.max_turns),
        ]

    def env_for(self, spec: LaunchSpec) -> dict[str, str]:
        env = {k: v for k, v in spec.env.items() if k not in _DROP_ENV}
        env["CLAUDE_CODE_EFFORT_LEVEL"] = spec.effort
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        return env

    def launch(self, spec: LaunchSpec):
        prepared = LaunchSpec(**{**spec.__dict__, "env": self.env_for(spec)})
        with open_log(spec.log_path) as log:
            return popen_detached(self.argv(prepared), prepared, log)

    def installed_skills(self) -> list[WorkerType]:
        dirs = [self.home / "skills", *sorted((self.home / "plugins" / "cache").glob("*/*/*/skills"))]
        return scan_skills(dirs)

    def command_timeout_ceiling_seconds(self) -> int:
        return BASH_TOOL_CEILING_SECONDS

    def supports_effort(self) -> bool:
        return True


ADAPTER = ClaudeAdapter()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_adapter_claude.py -v` then `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/meld/adapters/claude.py tests/test_adapter_claude.py
git commit -m "feat: Claude Code adapter for unattended workers"
```

---

### Task 9: Codex CLI adapter

**Files:**
- Create: `src/meld/adapters/codex.py`
- Create: `tests/test_adapter_codex.py`
- Modify: `tests/test_adapters.py` (remove the skip marker on `test_registry_names_and_unknown`)

**Interfaces:**
- Produces (in `meld.adapters.codex`): `class CodexAdapter` with `name = "codex"`, constructor `CodexAdapter(binary: str = "codex", home: Path | None = None)` (`home` defaults to the user's home directory); `argv(spec) -> list[str]`; `env_for(spec) -> dict[str, str]` (unchanged copy); `launch(spec)`; `installed_skills()` (scans `~/.agents/skills` and `~/.codex/skills`, dot-directories such as `.system` excluded by `scan_skills`); `command_timeout_ceiling_seconds() -> int` (600; the exact Codex shell-command ceiling is unverified, so the same conservative value is used and `meld doctor` re-checks it in plan 3); `supports_effort() -> bool` (True); `ADAPTER = CodexAdapter()`.
- Launch shape (from `codex exec --help`, 0.144.6): `codex exec -m <model> -c model_reasoning_effort="<effort>" -s workspace-write -a never -C <cwd> --json -o <cwd>/.meld-last-message.txt --ignore-user-config --ephemeral <prompt>`. `-s workspace-write` must be explicit because `exec` defaults to read-only; `-a never` never prompts; `--ignore-user-config` skips the user's `config.toml` and with it their MCP servers and hooks; `--ephemeral` keeps no session files. Auth still resolves through `CODEX_HOME`, which is left untouched.

- [ ] **Step 1: Write the failing tests**

`tests/test_adapter_codex.py`:

```python
from pathlib import Path

import meld.adapters.codex as codex_mod
from meld.adapters.base import LaunchSpec
from meld.adapters.codex import CodexAdapter


def spec(tmp_path: Path, **over) -> LaunchSpec:
    base = dict(role="worker", model="gpt-5.6-terra", effort="high", cwd=tmp_path, prompt="Read .meld-brief.md and do it.",
                log_path=tmp_path / "log.txt", timeout_seconds=1200, env={"PATH": "/usr/bin", "HOME": "/Users/x", "MELD_WORKER": "1"}, max_turns=200)
    base.update(over)
    return LaunchSpec(**base)


def test_argv_is_the_documented_unattended_shape(tmp_path: Path):
    argv = CodexAdapter().argv(spec(tmp_path))
    assert argv[:2] == ["codex", "exec"]
    assert argv[argv.index("-m") + 1] == "gpt-5.6-terra"
    assert argv[argv.index("-c") + 1] == 'model_reasoning_effort="high"'
    assert argv[argv.index("-s") + 1] == "workspace-write" and argv[argv.index("-a") + 1] == "never"
    assert argv[argv.index("-C") + 1] == str(tmp_path)
    assert "--json" in argv and "--ignore-user-config" in argv and "--ephemeral" in argv
    assert argv[argv.index("-o") + 1] == str(tmp_path / ".meld-last-message.txt")
    assert argv[-1] == "Read .meld-brief.md and do it."
    assert "--full-auto" not in argv and "--dangerously-bypass-approvals-and-sandbox" not in argv


def test_env_is_passed_through_unchanged(tmp_path: Path):
    s = spec(tmp_path)
    assert CodexAdapter().env_for(s) == s.env


def test_launch_uses_argv_and_log(tmp_path: Path, monkeypatch):
    captured = {}

    def fake_popen(argv, spec_, log):
        captured.update(argv=argv, spec=spec_, log_name=log.name)
        return "proc"

    monkeypatch.setattr(codex_mod, "popen_detached", fake_popen)
    s = spec(tmp_path)
    assert CodexAdapter().launch(s) == "proc"
    assert captured["argv"][:2] == ["codex", "exec"] and captured["spec"].cwd == tmp_path and captured["log_name"] == str(tmp_path / "log.txt")


def test_installed_skills_scans_agents_and_codex_dirs_but_not_system(tmp_path: Path):
    (tmp_path / ".agents" / "skills" / "deploy").mkdir(parents=True)
    (tmp_path / ".agents" / "skills" / "deploy" / "SKILL.md").write_text("---\nname: deploy\ndescription: Ship it.\n---\n")
    (tmp_path / ".codex" / "skills" / ".system" / "skill-creator").mkdir(parents=True)
    (tmp_path / ".codex" / "skills" / ".system" / "skill-creator" / "SKILL.md").write_text("---\nname: skill-creator\n---\n")
    (tmp_path / ".codex" / "skills" / "review").mkdir(parents=True)
    (tmp_path / ".codex" / "skills" / "review" / "SKILL.md").write_text("---\nname: review\ndescription: Review.\n---\n")
    names = [s.name for s in CodexAdapter(home=tmp_path).installed_skills()]
    assert names == ["deploy", "review"]
    assert CodexAdapter().command_timeout_ceiling_seconds() == 600 and CodexAdapter().supports_effort() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_adapter_codex.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.adapters.codex'`

- [ ] **Step 3: Write `src/meld/adapters/codex.py`**

```python
"""OpenAI Codex CLI adapter: one unattended `codex exec` run per launch."""
from __future__ import annotations

from pathlib import Path

from meld.adapters.base import LaunchSpec, open_log, popen_detached, scan_skills
from meld.roster import WorkerType

LAST_MESSAGE_FILE = ".meld-last-message.txt"
COMMAND_CEILING_SECONDS = 600  # conservative; the exact Codex shell-command ceiling is verified by `meld doctor`


class CodexAdapter:
    name = "codex"

    def __init__(self, binary: str = "codex", home: Path | None = None):
        self.binary = binary
        self.home = home or Path.home()

    def argv(self, spec: LaunchSpec) -> list[str]:
        return [
            self.binary, "exec",
            "-m", spec.model,
            "-c", f'model_reasoning_effort="{spec.effort}"',
            "-s", "workspace-write",
            "-a", "never",
            "-C", str(spec.cwd),
            "--json",
            "-o", str(spec.cwd / LAST_MESSAGE_FILE),
            "--ignore-user-config",
            "--ephemeral",
            spec.prompt,
        ]

    def env_for(self, spec: LaunchSpec) -> dict[str, str]:
        return dict(spec.env)

    def launch(self, spec: LaunchSpec):
        prepared = LaunchSpec(**{**spec.__dict__, "env": self.env_for(spec)})
        with open_log(spec.log_path) as log:
            return popen_detached(self.argv(prepared), prepared, log)

    def installed_skills(self) -> list[WorkerType]:
        return scan_skills([self.home / ".agents" / "skills", self.home / ".codex" / "skills"])

    def command_timeout_ceiling_seconds(self) -> int:
        return COMMAND_CEILING_SECONDS

    def supports_effort(self) -> bool:
        return True


ADAPTER = CodexAdapter()
```

- [ ] **Step 4: Remove the skip marker**

In `tests/test_adapters.py` delete the `@pytest.mark.skipif(...)` decorator on `test_registry_names_and_unknown`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_adapter_codex.py tests/test_adapters.py -v` then `uv run pytest -q`
Expected: PASS, no skips.

- [ ] **Step 6: Commit**

```bash
git add src/meld/adapters/codex.py tests/test_adapter_codex.py tests/test_adapters.py
git commit -m "feat: Codex CLI adapter for unattended workers"
```

---

### Task 10: Run-record extensions, zombie-safe `pid_alive`, GitHub PR helpers, and `meld dispatch`

**Files:**
- Modify: `src/meld/board.py` (RunRecord fields), `src/meld/state.py` (`pid_alive`, `_read_record`, `archive_run`), `src/meld/github.py` (`create_pull`, `update_pull`), `src/meld/rules.py` (`Verb.REAP`, `Verb.TAG`), `src/meld/status.py` (run fields)
- Create: `src/meld/dispatch.py`
- Modify: `src/meld/cli.py`
- Create: `tests/productfixtures.py`, `tests/test_dispatch.py`
- Modify: `tests/test_state.py`, `tests/test_github.py`

**Interfaces:**
- `RunRecord` gains trailing fields `deadline: str = ""` (ISO Z), `branch: str = ""`, `lease_comment: int | None = None`, `timed_out: bool = False`; `write_run` persists them and `_read_record` reads them with those defaults.
- `state.pid_alive(pid)` first reaps an exited child of this process with `os.waitpid(pid, os.WNOHANG)` (ignoring `ChildProcessError`), so a finished worker spawned by this same process is reported dead rather than lingering as a zombie. `StateStore.archive_run(group: str, issue: int) -> Path` moves `runs/<g>/<n>.json` to `runs/<g>/archive/<n>-<YYYYmmddTHHMMSSZ>.json`.
- `GitHub.create_pull(title, head, base, body) -> dict` (`POST repos/{repo}/pulls`), `GitHub.update_pull(number, body) -> dict` (`PATCH repos/{repo}/pulls/{number}`).
- `rules.Verb` gains `REAP = "reap"` and `TAG = "tag"` (neither in `ISSUE_VERBS`/`PR_VERBS`).
- `status.render_status` `runs[]` entries gain `"deadline"`, `"branch"`, `"timed_out"`.
- `meld.dispatch`: `class DispatchError(RuntimeError)` (usage/environment problems, exit 1); `WORKER_PROMPT` constant; `git_identity(ctx, group) -> tuple[str, str]` (config `git_name`/`git_email`, else the bot login and `<id>+<bot>@users.noreply.github.com` via `GET users/<bot>`); `tail_lines(path, n) -> str`; `kill_run(rec, *, grace_seconds=5.0, sleep=time.sleep) -> str` (`"gone"|"terminated"|"killed"`, signals the process group); `dispatch(ctx, number, *, kind="build", adapter=None, now=None, env=None) -> dict`. Build dispatch: rules `DISPATCH` check, worker type from `roster(repo_root, adapter.installed_skills())`, worktree at `$MELD_HOME/worktrees/<group>/<n>-<slug>` (reused if present, else `create_worktree`), git identity set, brief written, stale result removed, `LaunchSpec(role="worker", ...)` launched with `scrub_env`, run record written, `[<g>:pm] LEASE #<n>` posted with `until: <deadline + lease_grace_minutes>`, start comment posted on the issue. Review/audit dispatch (`kind="review"|"audit"`): PM only; target is a PR in `board.pulls`; review requires `pr_refusal(APPROVE)` to pass (cannot review own head, challenged, unknown committer, not open); audit requires a merged PR not authored by the caller's group; both respect `review_concurrency` over live review/audit runs; worktree `review-<n>`/`audit-<n>` is a fresh detached checkout of the PR head branch; model and effort come from the group's `orchestrator` RoleConfig; no lease; start comment on the PR. Returns `{"dispatched", "kind", "pid", "worktree", "branch", "deadline", "lease_until", "worktree_mode"}`.
- CLI: `meld dispatch NUMBER [--kind build|review|audit]`; `DispatchError` becomes a `click.ClickException`; `Refused` uses `_run_action`.
- `tests/productfixtures.py`: `make_product(tmp_path, comments=None, issues=None) -> tuple[FakeGh, Context, Path, Path]` (origin, clone with `.meld.toml`, `FakeGh` with board1 routes plus `GET users/meld-claude-bot -> {"id": 7}`, `POST issues/10/comments`, `POST issues/11/comments`, and a context for the claude PM); `claim_issue_11(fake)` (mutates the issues route so #11 is `in_progress`, assigned to `meld-claude-bot`, size S); `wait_for_exit(pid, timeout=15)`; `DONE_SCRIPT`, `NO_RESULT_SCRIPT`, `BLOCKED_SCRIPT`, `SLEEP_SCRIPT`, `REVIEW_SCRIPT` worker scripts for `FakeAdapter`.

- [ ] **Step 1: Write the product fixtures**

`tests/productfixtures.py`:

```python
"""A product repository plus FakeGh and a PM context, for dispatch/tick/collect/reap tests."""
from __future__ import annotations

import json
import time
from pathlib import Path

from meld.context import load_context
from meld.state import pid_alive
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.fakegh import FakeGh
from tests.gitfixtures import make_origin_and_clone

FIXTURES = Path(__file__).parent / "fixtures" / "board1"
R = "repos/acme/widgets/"

DONE_SCRIPT = """
import json, pathlib, subprocess
pathlib.Path("health.py").write_text("OK = True\\n")
subprocess.run(["git", "add", "health.py"], check=True)
subprocess.run(["git", "commit", "-q", "-m", "feat: health endpoint"], check=True)
pathlib.Path(".meld-result.json").write_text(json.dumps({"outcome": "done", "summary": "added health.py",
    "verified_by": "python -c 'import health' -> ok"}))
"""
NO_RESULT_SCRIPT = "print('worker crashed before writing a result'); raise SystemExit(1)"
BLOCKED_SCRIPT = """
import json, pathlib
pathlib.Path(".meld-result.json").write_text(json.dumps({"outcome": "blocked", "summary": "need a key",
    "verified_by": "n/a", "needs_human": "Create a Stripe sandbox key and put it in STRIPE_KEY"}))
"""
SLEEP_SCRIPT = "import time; time.sleep(30)"
REVIEW_SCRIPT = """
import json, pathlib
pathlib.Path(".meld-result.json").write_text(json.dumps({"outcome": "done", "summary": "looks right", "verified_by": "pytest -q -> ok",
    "verdict": "approve", "comments": [{"path": "README.md", "line": 1, "body": "nit: title"}]}))
"""


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


def claim_issue_11(fake: FakeGh, size: str = "S") -> list[dict]:
    issues = fixture("issues.json")
    for i in issues:
        if i["number"] == 11:
            i["labels"] = [l for l in i["labels"] if not l["name"].startswith(("meld:ready", "size:"))]
            i["labels"] += [{"name": "meld:in-progress"}, {"name": f"size:{size}"}]
            i["assignees"] = [{"login": "meld-claude-bot"}]
    fake.route("GET", R + "issues", issues)
    return issues


def make_product(tmp_path: Path, group: str = "claude", role: str = "pm"):
    origin, repo = make_origin_and_clone(tmp_path)
    (repo / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    fake.route("GET", "users/meld-claude-bot", {"id": 7, "login": "meld-claude-bot"})
    fake.route("GET", "users/meld-codex-bot", {"id": 8, "login": "meld-codex-bot"})
    for n in (10, 11, 12, 13, 20):
        fake.route("POST", R + f"issues/{n}/comments", {"id": 300 + n, "html_url": f"u{n}"})
    ctx = load_context(repo / ".meld.toml", env=env_for(tmp_path, group=group, role=role), run=fake)
    return fake, ctx, repo, origin


def wait_for_exit(pid: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not pid_alive(pid), f"pid {pid} still alive after {timeout}s"
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_state.py`:

```python
def test_pid_alive_reaps_own_exited_child_and_archive_run(tmp_path):
    import subprocess, sys
    from meld.state import pid_alive
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()  # exited; without a waitpid in pid_alive a zombie would still answer kill(pid, 0)
    assert pid_alive(proc.pid) is False
    store = StateStore(tmp_path, "claude")
    store.write_run(mk_run("claude", 12))
    archived = store.archive_run("claude", 12)
    assert archived.parent == tmp_path / "runs" / "claude" / "archive" and archived.name.startswith("12-")
    assert not (tmp_path / "runs" / "claude" / "12.json").exists()
    assert store.load_runs(alive=lambda p: False) == {"claude": {}}
    assert store.load_spend(3) == {"claude": {"medium": 1}}


def test_run_record_extensions_round_trip(tmp_path):
    store = StateStore(tmp_path, "claude")
    rec = mk_run("claude", 12)
    from dataclasses import replace
    store.write_run(replace(rec, deadline="2026-09-03T13:00:00Z", branch="meld/12-login", lease_comment=110, timed_out=True))
    loaded = store.load_runs(alive=lambda p: False)["claude"][12]
    assert (loaded.deadline, loaded.branch, loaded.lease_comment, loaded.timed_out) == ("2026-09-03T13:00:00Z", "meld/12-login", 110, True)
```

Append to `tests/test_github.py`:

```python
def test_create_and_update_pull(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("POST", "repos/acme/widgets/pulls", {"number": 21, "html_url": "u"})
    fake.route("PATCH", "repos/acme/widgets/pulls/21", {"number": 21})
    assert gh.create_pull("t", "meld/11-x", "main", "Closes #11")["number"] == 21
    assert fake.calls[0].body == {"title": "t", "head": "meld/11-x", "base": "main", "body": "Closes #11"}
    gh.update_pull(21, "new body")
    assert fake.calls[1].method == "PATCH" and fake.calls[1].body == {"body": "new body"}
```

`tests/test_dispatch.py`:

```python
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from meld.actions import Refused
from meld.brief import BRIEF_FILE
from meld.dispatch import DispatchError, dispatch, git_identity
from meld.gitops import head_sha, worktree_exists
from tests.fakeadapter import FakeAdapter
from tests.gitfixtures import sh
from tests.productfixtures import DONE_SCRIPT, R, claim_issue_11, fixture, make_product, wait_for_exit

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def posted(fake, number):
    return [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + f"issues/{number}/comments"]


def test_dispatch_build_happy_path(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    adapter = FakeAdapter(script=DONE_SCRIPT)
    out = dispatch(ctx, 11, adapter=adapter, now=NOW)
    assert out["dispatched"] == 11 and out["kind"] == "build" and out["worktree_mode"] == "new"
    assert out["branch"] == "meld/11-add-health-endpoint" and out["deadline"] == "2026-09-03T12:20:00Z" and out["lease_until"] == "2026-09-03T12:30:00Z"
    wt = Path(out["worktree"])
    assert wt == tmp_path / "home" / "worktrees" / "claude" / "11-add-health-endpoint" and worktree_exists(repo, wt)
    assert (wt / BRIEF_FILE).read_text().startswith("# Meld brief: backend for #11")
    spec = adapter.launches[0]
    assert spec.role == "worker" and spec.model == "claude-sonnet-5" and spec.effort == "medium" and spec.cwd == wt and spec.timeout_seconds == 1200
    assert "MELD_TOKEN_CLAUDE" not in spec.env and "GH_TOKEN" not in spec.env and spec.env["MELD_WORKER"] == "1" and spec.max_turns == 200
    rec = json.loads((tmp_path / "home" / "runs" / "claude" / "11.json").read_text())
    assert rec["pid"] == out["pid"] and rec["deadline"] == "2026-09-03T12:20:00Z" and rec["branch"] == out["branch"] and rec["lease_comment"] == 310 and rec["kind"] == "build"
    lease = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"][0]
    assert lease.startswith("[claude:pm] LEASE #11\n") and "until: 2026-09-03T12:30:00Z" in lease
    assert posted(fake, 11)[0].startswith("Meld: `claude` dispatched a `backend` worker")
    wait_for_exit(out["pid"])
    assert sh(["git", "log", "-1", "--format=%an <%ae>"], wt) == "meld-claude-bot <7+meld-claude-bot@users.noreply.github.com>"
    assert (wt / ".meld-result.json").exists()


def test_dispatch_reuses_an_existing_worktree(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    first = dispatch(ctx, 11, adapter=FakeAdapter(), now=NOW)
    wait_for_exit(first["pid"])
    # pretend the first run was collected so a second dispatch is legal
    rec_path = tmp_path / "home" / "runs" / "claude" / "11.json"
    rec = json.loads(rec_path.read_text())
    rec["collected"] = True
    rec_path.write_text(json.dumps(rec))
    second = dispatch(ctx, 11, adapter=FakeAdapter(), now=NOW)
    assert second["worktree_mode"] == "reused" and second["worktree"] == first["worktree"]
    wait_for_exit(second["pid"])


def test_dispatch_refusals(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    with pytest.raises(Refused) as ei:
        dispatch(ctx, 11, adapter=FakeAdapter(), now=NOW)  # issue 11 is ready, not in-progress
    assert ei.value.refusal.rule == "issue is ready, not in-progress" and ei.value.violation_posted is True
    with pytest.raises(Refused) as ei:
        dispatch(ctx, 12, adapter=FakeAdapter(), now=NOW)  # challenged in the fixtures
    assert ei.value.refusal.rule == "issue has an open CHALLENGE"
    fake2, orch, *_ = make_product(tmp_path / "b", role="orchestrator")
    claim_issue_11(fake2)
    with pytest.raises(Refused) as ei:
        dispatch(orch, 11, adapter=FakeAdapter(), now=NOW)
    assert ei.value.refusal.rule == "only the PM may dispatch"
    with pytest.raises(DispatchError, match="kind"):
        dispatch(ctx, 11, kind="bogus", adapter=FakeAdapter(), now=NOW)


def test_dispatch_refuses_worker_type_missing_from_roster(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    issues = claim_issue_11(fake)
    for i in issues:
        if i["number"] == 11:
            i["labels"] = [l for l in i["labels"] if not l["name"].startswith("worker:")] + [{"name": "worker:marketing"}]
    fake.route("GET", R + "issues", issues)
    with pytest.raises(Refused) as ei:
        dispatch(ctx, 11, adapter=FakeAdapter(), now=NOW)
    assert "not in claude's roster" in ei.value.refusal.rule


def test_git_identity_prefers_config_then_noreply(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    assert git_identity(ctx, ctx.cfg.groups["claude"]) == ("meld-claude-bot", "7+meld-claude-bot@users.noreply.github.com")
    from dataclasses import replace
    g = replace(ctx.cfg.groups["claude"], git_name="Meld Claude", git_email="me@example.com")
    assert git_identity(ctx, g) == ("Meld Claude", "me@example.com")


def test_dispatch_review_checks_out_pr_head(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    # the fixture PR #20 lives on meld/13-ci-pipeline; publish that branch to the fake origin
    sh(["git", "checkout", "-q", "-b", "meld/13-ci-pipeline"], repo)
    (repo / "ci.yml").write_text("ci\n")
    sh(["git", "add", "ci.yml"], repo)
    sh(["git", "commit", "-q", "-m", "ci"], repo)
    sh(["git", "push", "-q", "origin", "meld/13-ci-pipeline"], repo)
    sh(["git", "checkout", "-q", "main"], repo)
    expected = sh(["git", "rev-parse", "meld/13-ci-pipeline"], repo)
    adapter = FakeAdapter()
    out = dispatch(ctx, 20, kind="review", adapter=adapter, now=NOW)
    wt = Path(out["worktree"])
    assert wt.name == "review-20" and head_sha(wt) == expected and out["lease_until"] is None
    assert (wt / BRIEF_FILE).read_text().startswith("# Meld brief: reviewer for PR #20")
    spec = adapter.launches[0]
    assert (spec.model, spec.effort) == ("claude-opus-5", "high")  # the group's orchestrator RoleConfig
    rec = json.loads((tmp_path / "home" / "runs" / "claude" / "20.json").read_text())
    assert rec["kind"] == "review" and rec["pr"] == 20 and rec["lease_comment"] is None
    assert posted(fake, 20)[0].startswith("Meld: `claude` dispatched a `reviewer` worker")
    wait_for_exit(out["pid"])
    # codex may not review its own head (PR #20's last commit is by meld-codex-bot)
    fake2, codex, *_ = make_product(tmp_path / "c", group="codex")
    with pytest.raises(Refused, match="cannot review"):
        dispatch(codex, 20, kind="review", adapter=FakeAdapter(), now=NOW)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_state.py tests/test_github.py tests/test_dispatch.py -q`
Expected: FAIL (`archive_run`, `create_pull`, `meld.dispatch` missing; `pid_alive` zombie test fails).

- [ ] **Step 4: Board, state, github, rules, status changes**

`src/meld/board.py` — append to `RunRecord`:

```python
    deadline: str = ""
    branch: str = ""
    lease_comment: int | None = None
    timed_out: bool = False
```

`src/meld/state.py` — replace `pid_alive` and extend `_read_record`, add `archive_run`:

```python
def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        reaped, _ = os.waitpid(pid, os.WNOHANG)  # reap our own exited child so it does not linger as a zombie
        if reaped == pid:
            return False
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
```

In `_read_record` pass `deadline=str(data.get("deadline", "")), branch=str(data.get("branch", "")), lease_comment=(int(data["lease_comment"]) if data.get("lease_comment") is not None else None), timed_out=bool(data.get("timed_out", False))`. Add to `StateStore`:

```python
    def archive_run(self, group: str, issue: int) -> Path:
        src = self.runs_root / group / f"{issue}.json"
        archive = self.runs_root / group / "archive"
        archive.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dst = archive / f"{issue}-{stamp}.json"
        os.replace(src, dst)
        return dst
```

(add `from datetime import datetime, timezone` to the imports).

`src/meld/github.py` — add:

```python
    def create_pull(self, title: str, head: str, base: str, body: str) -> dict:
        return self.post(self._r("pulls"), {"title": title, "head": head, "base": base, "body": body})

    def update_pull(self, number: int, body: str) -> dict:
        return self.patch(self._r(f"pulls/{number}"), {"body": body})
```

`src/meld/rules.py` — add `REAP = "reap"` and `TAG = "tag"` to `Verb`.

`src/meld/status.py` — in the `runs` comprehension add `"deadline": r.deadline, "branch": r.branch, "timed_out": r.timed_out`.

- [ ] **Step 5: Write `src/meld/dispatch.py`**

```python
"""Dispatch, tick, collect, reap: the only module that spawns workers or touches worktrees."""
from __future__ import annotations

import os
import signal
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from meld.actions import IN_PROGRESS, refuse
from meld.adapters import Adapter, LaunchSpec, get_adapter, scrub_env
from meld.board import Board, RunRecord
from meld.brief import BRIEF_FILE, RESULT_FILE, render_brief
from meld.config import GroupConfig, Party
from meld.gitops import (
    branch_name,
    checkout_pr_head,
    create_worktree,
    noreply_email,
    remove_worktree,
    set_identity,
    slugify,
    worktree_exists,
)
from meld.grammar import Header, compose
from meld.roster import WorkerType, roster
from meld.rules import Actor, Refusal, Verb, issue_refusal, party_of_login, pr_refusal
from meld.state import pid_alive
from meld.status import build_status

WORKER_PROMPT = ("Read .meld-brief.md in the current directory and carry it out exactly. "
                 "When you finish, write .meld-result.json as the brief instructs, then stop.")
KINDS = ("build", "review", "audit")
_ISO = "%Y-%m-%dT%H:%M:%SZ"


class DispatchError(RuntimeError):
    """A usage or environment problem, not a rule refusal."""


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime(_ISO)


def _from_iso(text: str) -> datetime:
    return datetime.strptime(text, _ISO).replace(tzinfo=timezone.utc)


def git_identity(ctx, group: GroupConfig) -> tuple[str, str]:
    name = group.git_name or group.bot
    if group.git_email:
        return name, group.git_email
    user = ctx.gh.get(f"users/{group.bot}")
    return name, noreply_email(int(user["id"]), group.bot)


def tail_lines(path: Path, n: int) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])


def kill_run(rec: RunRecord, *, grace_seconds: float = 5.0, sleep=time.sleep) -> str:
    """Terminate a worker's process group, escalating to SIGKILL after the grace period."""
    if not pid_alive(rec.pid):
        return "gone"
    try:
        os.killpg(rec.pid, signal.SIGTERM)
    except ProcessLookupError:
        return "gone"
    except PermissionError:
        pass
    waited = 0.0
    while pid_alive(rec.pid) and waited < grace_seconds:
        sleep(0.1)
        waited += 0.1
    if not pid_alive(rec.pid):
        return "terminated"
    try:
        os.killpg(rec.pid, signal.SIGKILL)
    except ProcessLookupError:
        return "terminated"
    return "killed"


def _adapter(ctx, group: GroupConfig, adapter: Adapter | None) -> Adapter:
    return adapter if adapter is not None else get_adapter(group.cli)


def _worker_type(ctx, adapter: Adapter, name: str | None) -> WorkerType | None:
    if not name:
        return None
    return next((w for w in roster(ctx.repo_root, adapter.installed_skills()) if w.name == name), None)


def _worktree(ctx, group: GroupConfig, key: str) -> Path:
    return ctx.store.home / "worktrees" / group.name / key


def _log_path(ctx, group: GroupConfig, kind: str, number: int, when: datetime) -> Path:
    return ctx.store.home / "logs" / group.name / f"{kind}-{number}-{when.strftime('%Y%m%dT%H%M%SZ')}.log"


def lease_body(group: str, number: int, until: str, kind: str) -> str:
    return f"until: {until}\nrun: {group}/#{number}\nkind: {kind}"


def _launch(ctx, group: GroupConfig, adapter: Adapter, *, worktree: Path, brief: str, model: str, effort: str,
            timeout: int, kind: str, number: int, when: datetime, env: dict | None) -> tuple[int, Path]:
    (worktree / BRIEF_FILE).write_text(brief)
    (worktree / RESULT_FILE).unlink(missing_ok=True)
    log_path = _log_path(ctx, group, kind, number, when)
    spec = LaunchSpec(role="worker", model=model, effort=effort, cwd=worktree, prompt=WORKER_PROMPT, log_path=log_path,
                      timeout_seconds=timeout, env=scrub_env(os.environ if env is None else env), max_turns=ctx.cfg.worker_max_turns)
    proc = adapter.launch(spec)
    return proc.pid, log_path


def dispatch(ctx, number: int, *, kind: str = "build", adapter: Adapter | None = None, now: datetime | None = None,
             env: dict | None = None) -> dict:
    if kind not in KINDS:
        raise DispatchError(f"kind must be one of {', '.join(KINDS)}, got {kind!r}")
    _, board = build_status(ctx)
    actor = ctx.actor()
    when = _now(now)
    if kind == "build":
        return _dispatch_build(ctx, board, actor, number, adapter, when, env)
    return _dispatch_pr(ctx, board, actor, number, kind, adapter, when, env)


def _dispatch_build(ctx, board: Board, actor: Actor, number: int, adapter: Adapter | None, when: datetime, env: dict | None) -> dict:
    issue = board.issues.get(number)
    if issue is None:
        refuse(ctx, board, Refusal(Verb.DISPATCH, f"#{number}", "issue is not in the active milestone"))
    refusal = issue_refusal(board, ctx.cfg, actor, Verb.DISPATCH, issue, ctx.models)
    if refusal:
        refuse(ctx, board, refusal)
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    adp = _adapter(ctx, group, adapter)
    worker = _worker_type(ctx, adp, issue.worker)
    if worker is None:
        refuse(ctx, board, Refusal(Verb.DISPATCH, f"#{number}", f"worker type '{issue.worker}' is not in {group.name}'s roster"))
    branch = branch_name(number, issue.title)
    wt = _worktree(ctx, group, f"{number}-{slugify(issue.title)}")
    mode = "reused" if worktree_exists(ctx.repo_root, wt) else create_worktree(ctx.repo_root, wt, branch, ctx.cfg.default_branch)
    name, email = git_identity(ctx, group)
    set_identity(wt, name, email)
    brief = render_brief(role=worker, kind="build", group=group.name, model=issue.model or "", effort=issue.effort or "", issue=issue,
                         pr=None, plan_text=board.plan_text, repo_root=ctx.repo_root, worktree=wt, base_branch=ctx.cfg.default_branch)
    timeout = ctx.cfg.timeout_seconds(issue.size or "M")
    deadline = when + timedelta(seconds=timeout)
    pid, log_path = _launch(ctx, group, adp, worktree=wt, brief=brief, model=issue.model or "", effort=issue.effort or "",
                            timeout=timeout, kind="build", number=number, when=when, env=env)
    record = RunRecord(group=group.name, issue=number, kind="build", pr=None, pid=pid, started_at=_iso(when), worktree=str(wt),
                       log_path=str(log_path), model=issue.model or "", effort=issue.effort or "",
                       tier=ctx.models.tier_of(group.cli, issue.model or "") or "medium", milestone=board.milestone, alive=True,
                       collected=False, deadline=_iso(deadline), branch=branch)
    ctx.store.write_run(record)
    until = _iso(deadline + timedelta(minutes=ctx.cfg.lease_grace_minutes))
    lease = ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "LEASE", f"#{number}", None),
                                                                    lease_body(group.name, number, until, "build")))
    ctx.store.write_run(replace(record, lease_comment=int(lease["id"])))
    ctx.gh.create_comment(number, f"Meld: `{group.name}` dispatched a `{worker.name}` worker on `{issue.model}` at effort {issue.effort} "
                                  f"(size {issue.size}, {mode} worktree). Branch `{branch}`, deadline {_iso(deadline)}, lease until {until}.")
    return {"dispatched": number, "kind": "build", "pid": pid, "worktree": str(wt), "branch": branch, "deadline": _iso(deadline),
            "lease_until": until, "worktree_mode": mode}


def _dispatch_pr(ctx, board: Board, actor: Actor, number: int, kind: str, adapter: Adapter | None, when: datetime, env: dict | None) -> dict:
    target = f"#{number}"
    if not actor.is_pm:
        refuse(ctx, board, Refusal(Verb.DISPATCH, target, "only the PM may dispatch"))
    pr = board.pulls.get(number)
    if pr is None:
        refuse(ctx, board, Refusal(Verb.DISPATCH, target, "PR is not on a meld/ branch of the active repo"))
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    if kind == "review":
        blocker = pr_refusal(board, ctx.cfg, actor, Verb.APPROVE, pr)
        if blocker:
            refuse(ctx, board, Refusal(Verb.DISPATCH, target, f"cannot review: {blocker.rule}"))
    else:
        if not pr.merged:
            refuse(ctx, board, Refusal(Verb.DISPATCH, target, "audit targets a merged PR"))
        if party_of_login(ctx.cfg, pr.author) == group.name:
            refuse(ctx, board, Refusal(Verb.DISPATCH, target, "cannot audit a PR authored by own group"))
    live = [r for r in board.runs.get(group.name, {}).values() if r.alive and r.kind in ("review", "audit")]
    if len(live) >= group.review_concurrency:
        refuse(ctx, board, Refusal(Verb.DISPATCH, target, f"{group.name} is at its review concurrency cap ({len(live)}/{group.review_concurrency})"))
    existing = board.run_for(group.name, number)
    if existing is not None and (existing.alive or not existing.collected):
        refuse(ctx, board, Refusal(Verb.DISPATCH, target, "a run already exists for this PR (running or uncollected)"))
    adp = _adapter(ctx, group, adapter)
    role_name = "reviewer" if kind == "review" else "auditor"
    worker = _worker_type(ctx, adp, role_name)
    if worker is None:
        refuse(ctx, board, Refusal(Verb.DISPATCH, target, f"worker type '{role_name}' is not in {group.name}'s roster"))
    wt = _worktree(ctx, group, f"{kind}-{number}")
    if worktree_exists(ctx.repo_root, wt):
        remove_worktree(ctx.repo_root, wt)
    checkout_pr_head(ctx.repo_root, wt, pr.head_ref)
    name, email = git_identity(ctx, group)
    set_identity(wt, name, email)
    model, effort = group.orchestrator.model, group.orchestrator.effort
    brief = render_brief(role=worker, kind=kind, group=group.name, model=model, effort=effort, issue=None, pr=pr,
                         plan_text=board.plan_text, repo_root=ctx.repo_root, worktree=wt, base_branch=ctx.cfg.default_branch)
    timeout = ctx.cfg.timeout_seconds("M")
    deadline = when + timedelta(seconds=timeout)
    pid, log_path = _launch(ctx, group, adp, worktree=wt, brief=brief, model=model, effort=effort, timeout=timeout, kind=kind,
                            number=number, when=when, env=env)
    record = RunRecord(group=group.name, issue=number, kind=kind, pr=number, pid=pid, started_at=_iso(when), worktree=str(wt),
                       log_path=str(log_path), model=model, effort=effort, tier=ctx.models.tier_of(group.cli, model) or "medium",
                       milestone=board.milestone, alive=True, collected=False, deadline=_iso(deadline), branch=pr.head_ref)
    ctx.store.write_run(record)
    ctx.gh.create_comment(number, f"Meld: `{group.name}` dispatched a `{worker.name}` worker on `{model}` at effort {effort} "
                                  f"against head `{pr.head_sha[:7]}`. Deadline {_iso(deadline)}.")
    return {"dispatched": number, "kind": kind, "pid": pid, "worktree": str(wt), "branch": pr.head_ref, "deadline": _iso(deadline),
            "lease_until": None, "worktree_mode": "fresh"}
```

- [ ] **Step 6: CLI**

In `src/meld/cli.py` add `from meld.dispatch import DispatchError, dispatch as do_dispatch` and extend `_guard`'s except tuple to `(BoardError, GitHubError, ConfigError, DispatchError, GitError)` (import `GitError` from `meld.gitops`). Append:

```python
@main.command()
@click.argument("number", type=int)
@click.option("--kind", type=click.Choice(["build", "review", "audit"]), default="build", show_default=True,
              help="build: a claimed issue; review/audit: a pull request.")
@click.pass_context
def dispatch(ctx: click.Context, number: int, kind: str) -> None:
    """Spawn a headless worker in its own worktree for an issue (build) or a PR (review, audit)."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_dispatch(c, number, kind=kind))
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_dispatch.py tests/test_state.py tests/test_github.py -v` then `uv run pytest -q`
Expected: PASS. The dispatch tests spawn short-lived python processes; the whole file should finish in a few seconds.

- [ ] **Step 8: Commit**

```bash
git add src/meld/board.py src/meld/state.py src/meld/github.py src/meld/rules.py src/meld/status.py src/meld/dispatch.py src/meld/cli.py tests/productfixtures.py tests/test_dispatch.py tests/test_state.py tests/test_github.py
git commit -m "feat: meld dispatch spawns sandboxed workers in worktrees with leases and run records"
```

---

### Task 11: `meld tick` — kill overdue workers, create missing leases, release other groups' expired leases

**Files:**
- Modify: `src/meld/dispatch.py`, `src/meld/cli.py`
- Create: `tests/test_tick.py`

**Interfaces:**
- `meld.dispatch.tick(ctx, *, now=None, sleep=time.sleep) -> dict` returning `{"checked": int, "killed": [{"issue", "how"}], "leases_created": [int], "expired": [int]}`. PM only (`DispatchError` otherwise). For each live run of the caller's group: past `deadline` → `kill_run`, record rewritten with `timed_out=True`; a live build run with no `lease_comment` → a `LEASE` comment is created and recorded. For each lease held by another group on an `in_progress` issue that has expired → `actions.release(ctx, n)` (legal via the expired-lease rule) then `[<g>:pm] EXPIRED #n` posted on the coordination issue.
- CLI: `meld tick`.

- [ ] **Step 1: Write the failing tests**

`tests/test_tick.py`:

```python
import json
from datetime import datetime, timezone

import pytest

from meld.dispatch import DispatchError, dispatch, tick
from meld.state import pid_alive
from tests.fakeadapter import FakeAdapter
from tests.productfixtures import R, SLEEP_SCRIPT, claim_issue_11, fixture, make_product, wait_for_exit

PAST = datetime(2020, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def coordination_posts(fake):
    return [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]


def test_tick_kills_overdue_runs_and_marks_them_timed_out(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=SLEEP_SCRIPT), now=PAST)  # deadline is in 2020
    assert pid_alive(out["pid"])
    result = tick(ctx, now=NOW)
    assert result["killed"] == [{"issue": 11, "how": "terminated"}] and result["checked"] == 1
    wait_for_exit(out["pid"], timeout=5)
    rec = json.loads((tmp_path / "home" / "runs" / "claude" / "11.json").read_text())
    assert rec["timed_out"] is True
    assert tick(ctx, now=NOW)["checked"] == 0  # dead runs are not re-checked


def test_tick_creates_a_missing_lease(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=SLEEP_SCRIPT), now=NOW)
    rec_path = tmp_path / "home" / "runs" / "claude" / "11.json"
    rec = json.loads(rec_path.read_text())
    rec["lease_comment"] = None
    rec_path.write_text(json.dumps(rec))
    before = len(coordination_posts(fake))
    result = tick(ctx, now=NOW)
    assert result["leases_created"] == [11] and result["killed"] == []
    assert coordination_posts(fake)[before].startswith("[claude:pm] LEASE #11\n") and "until: 2026-09-03T12:30:00Z" in coordination_posts(fake)[before]
    assert json.loads(rec_path.read_text())["lease_comment"] == 310
    from meld.dispatch import kill_run
    from meld.board import RunRecord
    kill_run(RunRecord(**{**rec, "alive": True}), grace_seconds=2)


def test_tick_releases_another_groups_expired_lease(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    issues = fixture("issues.json")
    for i in issues:
        if i["number"] == 13:  # in_review in the fixture; make it codex's in-progress claim
            i["labels"] = [l for l in i["labels"] if l["name"] != "meld:in-review"] + [{"name": "meld:in-progress"}]
    fake.route("GET", R + "issues", issues)
    comments = fixture("comments_10.json") + [{"id": 120, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:30:00Z",
                                               "body": "[codex:pm] LEASE #13\n\nuntil: 2026-09-03T11:00:00Z\nrun: codex/#13"}]
    fake.route("GET", R + "issues/10/comments", comments)
    fake.route("DELETE", R + "issues/13/assignees", {})
    fake.route("POST", R + "issues/13/labels", [])
    fake.route("DELETE", R + "issues/13/labels/meld%3Ain-progress", [])
    result = tick(ctx, now=NOW)
    assert result["expired"] == [13]
    mutations = [(c.method, c.endpoint) for c in fake.calls if c.method != "GET" and "issues/13" in c.endpoint]
    assert ("DELETE", R + "issues/13/assignees") in mutations and ("POST", R + "issues/13/labels") in mutations
    assert coordination_posts(fake)[-1].startswith("[claude:pm] EXPIRED #13\n") and "expired at 2026-09-03T11:00:00Z" in coordination_posts(fake)[-1]
    fresh = tick(ctx, now=datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc))  # not yet expired at 10:00
    assert fresh["expired"] == []


def test_tick_is_pm_only(tmp_path):
    fake, orch, *_ = make_product(tmp_path, role="orchestrator")
    with pytest.raises(DispatchError, match="PM"):
        tick(orch, now=NOW)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tick.py -v`
Expected: FAIL with `ImportError: cannot import name 'tick'`

- [ ] **Step 3: Implement `tick` in `src/meld/dispatch.py`**

Add `from meld.actions import IN_PROGRESS, refuse, release` (extend the existing import) and append:

```python
def tick(ctx, *, now: datetime | None = None, sleep=time.sleep) -> dict:
    """PM housekeeping: kill overdue workers, create missing leases, release other groups' expired leases."""
    _, board = build_status(ctx)
    actor = ctx.actor()
    if not actor.is_pm:
        raise DispatchError(f"tick is a PM verb; you are {actor.party.label}")
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    when = _now(now)
    now_iso = _iso(when)
    out: dict = {"checked": 0, "killed": [], "leases_created": [], "expired": []}
    for rec in list(board.runs.get(group.name, {}).values()):
        if not rec.alive:
            continue
        out["checked"] += 1
        if rec.deadline and rec.deadline < now_iso:
            how = kill_run(rec, sleep=sleep)
            ctx.store.write_run(replace(rec, alive=False, timed_out=True))
            out["killed"].append({"issue": rec.issue, "how": how})
        elif rec.kind == "build" and rec.lease_comment is None:
            base = _from_iso(rec.deadline) if rec.deadline else when
            until = _iso(base + timedelta(minutes=ctx.cfg.lease_grace_minutes))
            lease = ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "LEASE", f"#{rec.issue}", None),
                                                                            lease_body(group.name, rec.issue, until, rec.kind)))
            ctx.store.write_run(replace(rec, lease_comment=int(lease["id"])))
            out["leases_created"].append(rec.issue)
    for n, lease in sorted(board.leases.items()):
        issue = board.issues.get(n)
        if lease.group == group.name or issue is None or issue.status != "in_progress" or not (board.now and lease.until < now_iso):
            continue
        release(ctx, n)
        ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "EXPIRED", f"#{n}", None),
                                                                f"Released #{n}: lease by {lease.group} expired at {lease.until} (now {now_iso})."))
        out["expired"].append(n)
    return out
```

Note: `release` re-reads the board and applies the expired-lease rule itself; `tick` only decides *which* leases to try. If `release` refuses (for example the lease was renewed between the two reads) the `Refused` propagates and the CLI reports it.

- [ ] **Step 4: CLI**

In `src/meld/cli.py` import `tick as do_tick` and append:

```python
@main.command()
@click.pass_context
def tick(ctx: click.Context) -> None:
    """PM housekeeping: kill overdue workers, create missing leases, release other groups' expired leases."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_tick(c))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_tick.py -v` then `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/meld/dispatch.py src/meld/cli.py tests/test_tick.py
git commit -m "feat: meld tick kills overdue workers and expires stale leases"
```

---

### Task 12: `meld collect` — push, open the PR, request review; failures to blocked or needs-human; store verdicts

**Files:**
- Modify: `src/meld/dispatch.py`, `src/meld/cli.py`
- Create: `tests/test_collect.py`

**Interfaces:**
- `meld.dispatch.collect(ctx, number, *, now=None) -> dict`. PM only (rules `COLLECT` for build runs; explicit checks for review/audit runs). Build run: reads `.meld-result.json`; success (`outcome == "done"` and commits ahead of `origin/<default_branch>`) → `push_branch` with the group's token (`ctx.gh.token`), create or update the PR (`Closes #<n>`, summary, `verified-by:` line), labels `meld:in-progress` → `meld:in-review`, finish comment on the issue, `[<g>:pm] REVIEW-REQUEST #<pr>` on the coordination issue; returns `{"collected", "outcome": "done", "pr", "commits"}`. `outcome == "blocked"` → labels → `meld:needs-human`, `[<g>:pm] NEEDS-HUMAN #<n>` posted with the `needs_human` text, issue comment; returns `outcome: "blocked"`. Anything else (timed out, no result file, malformed result, `failed`, no commits) → labels → `meld:blocked`, issue comment with the reason and the last `worker_log_tail_lines` log lines in a fenced block; returns `outcome: "failed", "reason"`. Review/audit run: result (or the failure) stored as JSON at `$MELD_HOME/results/<group>/<kind>-<n>.json` with keys `kind, pr, head_sha, collected_at, timed_out, result, log_tail`; returns `{"collected", "kind", "stored", "verdict"}`. In every case the run record is rewritten with `collected=True` (and `pr` set on success).
- `meld.gitops.head_sha` and `commits_ahead` are used; nothing new in gitops.
- CLI: `meld collect NUMBER`.

- [ ] **Step 1: Write the failing tests**

`tests/test_collect.py`:

```python
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from meld.actions import Refused
from meld.dispatch import collect, dispatch, tick
from meld.gitops import remote_branch_exists
from tests.fakeadapter import FakeAdapter
from tests.gitfixtures import sh
from tests.productfixtures import BLOCKED_SCRIPT, DONE_SCRIPT, NO_RESULT_SCRIPT, R, REVIEW_SCRIPT, SLEEP_SCRIPT, claim_issue_11, make_product, wait_for_exit

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
PAST = datetime(2020, 1, 1, tzinfo=timezone.utc)


def issue_posts(fake, number):
    return [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + f"issues/{number}/comments"]


def label_calls(fake, number):
    return [(c.method, c.endpoint.split(f"issues/{number}/labels", 1)[1], c.body) for c in fake.calls
            if c.method != "GET" and f"issues/{number}/labels" in c.endpoint]


def routes_for_labels(fake, number):
    fake.route("POST", R + f"issues/{number}/labels", [])
    for label in ("meld%3Ain-progress", "meld%3Aready", "meld%3Ablocked"):
        fake.route("DELETE", R + f"issues/{number}/labels/{label}", [])


def test_collect_done_pushes_opens_pr_and_requests_review(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    routes_for_labels(fake, 11)
    fake.route("POST", R + "pulls", {"number": 21, "html_url": "https://github.com/acme/widgets/pull/21"})
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=DONE_SCRIPT), now=NOW)
    wait_for_exit(out["pid"])
    result = collect(ctx, 11, now=NOW)
    assert result == {"collected": 11, "outcome": "done", "pr": 21, "commits": 1}
    assert remote_branch_exists(repo, "meld/11-add-health-endpoint")
    pull = [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "pulls"][0].body
    assert pull["head"] == "meld/11-add-health-endpoint" and pull["base"] == "main" and pull["title"] == "Add health endpoint"
    assert pull["body"].startswith("Closes #11\n\nadded health.py\n\nverified-by: python -c 'import health' -> ok")
    assert ("POST", "", {"labels": ["meld:in-review"]}) in label_calls(fake, 11) and ("DELETE", "/meld%3Ain-progress", None) in label_calls(fake, 11)
    finish = issue_posts(fake, 11)[-1]
    assert finish.startswith("Meld: `claude` worker finished: 1 commit(s)") and "PR #21" in finish and "verified-by:" in finish
    coord = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"][-1]
    assert coord.startswith("[claude:pm] REVIEW-REQUEST #21\n") and "Issue #11" in coord
    rec = json.loads((tmp_path / "home" / "runs" / "claude" / "11.json").read_text())
    assert rec["collected"] is True and rec["pr"] == 21
    with pytest.raises(Refused, match="already collected"):
        collect(ctx, 11, now=NOW)


def test_collect_without_result_blocks_with_log_tail(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    routes_for_labels(fake, 11)
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=NO_RESULT_SCRIPT), now=NOW)
    wait_for_exit(out["pid"])
    result = collect(ctx, 11, now=NOW)
    assert result["outcome"] == "failed" and result["reason"] == "exited without writing .meld-result.json"
    assert ("POST", "", {"labels": ["meld:blocked"]}) in label_calls(fake, 11)
    comment = issue_posts(fake, 11)[-1]
    assert "exited without writing" in comment and "worker crashed before writing a result" in comment and "```" in comment
    assert not remote_branch_exists(repo, "meld/11-add-health-endpoint")


def test_collect_timed_out_run(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    routes_for_labels(fake, 11)
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=SLEEP_SCRIPT), now=PAST)
    tick(ctx, now=NOW)
    wait_for_exit(out["pid"], timeout=5)
    result = collect(ctx, 11, now=NOW)
    assert result["outcome"] == "failed" and result["reason"].startswith("timed out at 2020-01-01T00:20:00Z")


def test_collect_blocked_needs_human(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    routes_for_labels(fake, 11)
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=BLOCKED_SCRIPT), now=NOW)
    wait_for_exit(out["pid"])
    result = collect(ctx, 11, now=NOW)
    assert result["outcome"] == "blocked"
    assert ("POST", "", {"labels": ["meld:needs-human"]}) in label_calls(fake, 11)
    coord = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"][-1]
    assert coord.startswith("[claude:pm] NEEDS-HUMAN #11\n") and "Stripe sandbox key" in coord


def test_collect_review_run_stores_the_verdict(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    sh(["git", "checkout", "-q", "-b", "meld/13-ci-pipeline"], repo)
    (repo / "ci.yml").write_text("ci\n")
    sh(["git", "add", "ci.yml"], repo)
    sh(["git", "commit", "-q", "-m", "ci"], repo)
    sh(["git", "push", "-q", "origin", "meld/13-ci-pipeline"], repo)
    sh(["git", "checkout", "-q", "main"], repo)
    out = dispatch(ctx, 20, kind="review", adapter=FakeAdapter(script=REVIEW_SCRIPT), now=NOW)
    wait_for_exit(out["pid"])
    result = collect(ctx, 20, now=NOW)
    stored = Path(result["stored"])
    assert stored == tmp_path / "home" / "results" / "claude" / "review-20.json" and result["verdict"] == "approve"
    data = json.loads(stored.read_text())
    assert data["kind"] == "review" and data["pr"] == 20 and data["result"]["verdict"] == "approve" and data["result"]["comments"][0]["path"] == "README.md"
    assert not [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "pulls"]  # nothing posted for verdicts yet


def test_collect_refusals(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    with pytest.raises(Refused, match="no run record"):
        collect(ctx, 11, now=NOW)
    claim_issue_11(fake)
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=SLEEP_SCRIPT), now=NOW)
    with pytest.raises(Refused, match="still running"):
        collect(ctx, 11, now=NOW)
    from meld.dispatch import kill_run
    from meld.board import RunRecord
    rec = json.loads((tmp_path / "home" / "runs" / "claude" / "11.json").read_text())
    kill_run(RunRecord(**{**rec, "alive": True}), grace_seconds=2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_collect.py -v`
Expected: FAIL with `ImportError: cannot import name 'collect'`

- [ ] **Step 3: Implement `collect` in `src/meld/dispatch.py`**

Extend imports: `import json`, `from dataclasses import asdict, replace`, `from meld.actions import BLOCKED, IN_PROGRESS, refuse, release`, `from meld.brief import BRIEF_FILE, RESULT_FILE, ResultError, WorkerResult, read_result, render_brief`, `from meld.gitops import (..., commits_ahead, head_sha, push_branch, ...)`. Add constants `IN_REVIEW = "meld:in-review"` and `NEEDS_HUMAN = "meld:needs-human"`. Append:

```python
def _swap_labels(ctx, number: int, add: str, remove: tuple[str, ...]) -> None:
    ctx.gh.add_labels(number, [add])
    for label in remove:
        ctx.gh.remove_label(number, label)


def collect(ctx, number: int, *, now: datetime | None = None) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    target = f"#{number}"
    if not actor.is_pm:
        refuse(ctx, board, Refusal(Verb.COLLECT, target, "only the PM may collect"))
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    rec = board.run_for(group.name, number)
    if rec is None:
        refuse(ctx, board, Refusal(Verb.COLLECT, target, f"no run record for {target} in {group.name}"))
    if rec.alive:
        refuse(ctx, board, Refusal(Verb.COLLECT, target, "worker is still running"))
    if rec.collected:
        refuse(ctx, board, Refusal(Verb.COLLECT, target, "run already collected"))
    when = _now(now)
    if rec.kind == "build":
        issue = board.issues.get(number)
        if issue is None:
            refuse(ctx, board, Refusal(Verb.COLLECT, target, "issue is not in the active milestone"))
        refusal = issue_refusal(board, ctx.cfg, actor, Verb.COLLECT, issue, ctx.models)
        if refusal:
            refuse(ctx, board, refusal)
        return _collect_build(ctx, board, group, rec, issue, when)
    return _collect_verdict(ctx, group, rec, when)


def _read_outcome(rec: RunRecord) -> tuple[WorkerResult | None, str | None]:
    """Return (result, reason_if_not_done)."""
    if rec.timed_out:
        return None, f"timed out at {rec.deadline}"
    try:
        result = read_result(Path(rec.worktree))
    except ResultError as e:
        return None, str(e)
    if result is None:
        return None, f"exited without writing {RESULT_FILE}"
    if result.outcome == "failed":
        return result, f"worker reported failed: {result.summary}"
    return result, None


def _collect_build(ctx, board: Board, group: GroupConfig, rec: RunRecord, issue, when: datetime) -> dict:
    number = rec.issue
    wt = Path(rec.worktree)
    result, reason = _read_outcome(rec)
    tail = tail_lines(Path(rec.log_path), ctx.cfg.worker_log_tail_lines)
    if reason is None and result is not None and result.outcome == "blocked":
        _swap_labels(ctx, number, NEEDS_HUMAN, (IN_PROGRESS,))
        ask = result.needs_human or result.summary or "The worker did not say what it needs."
        ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "NEEDS-HUMAN", f"#{number}", None),
                                                                f"{ask}\n\nWorker summary: {result.summary}\nverified-by: {result.verified_by}"))
        ctx.gh.create_comment(number, f"Meld: `{group.name}` worker is blocked and needs a human: {ask}")
        ctx.store.write_run(replace(rec, collected=True))
        return {"collected": number, "outcome": "blocked", "needs_human": ask}
    commits: list[str] = []
    if reason is None:
        commits = commits_ahead(wt, f"origin/{ctx.cfg.default_branch}")
        if not commits:
            reason = "worker reported done but made no commits"
    if reason is not None:
        _swap_labels(ctx, number, BLOCKED, (IN_PROGRESS,))
        summary = f"\n\nWorker summary: {result.summary}" if result else ""
        ctx.gh.create_comment(number, f"Meld: `{group.name}` worker did not complete #{number}: {reason}.{summary}\n\n"
                                      f"Last {ctx.cfg.worker_log_tail_lines} log lines:\n\n```\n{tail}\n```")
        ctx.store.write_run(replace(rec, collected=True))
        return {"collected": number, "outcome": "failed", "reason": reason}
    assert result is not None
    push_branch(wt, rec.branch, ctx.gh.token)
    body = f"Closes #{number}\n\n{result.summary}\n\nverified-by: {result.verified_by}"
    existing = board.pr_for_issue(number)
    if existing is not None and existing.state == "open":
        ctx.gh.update_pull(existing.number, body)
        pr_number = existing.number
    else:
        pr_number = int(ctx.gh.create_pull(issue.title, rec.branch, ctx.cfg.default_branch, body)["number"])
    _swap_labels(ctx, number, IN_REVIEW, (IN_PROGRESS,))
    sha = head_sha(wt)
    ctx.gh.create_comment(number, f"Meld: `{group.name}` worker finished: {len(commits)} commit(s) {commits[0][:7]}..{commits[-1][:7]}, PR #{pr_number}.\n\n"
                                  f"verified-by: {result.verified_by}")
    ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "REVIEW-REQUEST", f"#{pr_number}", None),
                                                            f"Issue #{number}, head {sha[:7]}. Please dispatch a reviewer."))
    ctx.store.write_run(replace(rec, collected=True, pr=pr_number))
    return {"collected": number, "outcome": "done", "pr": pr_number, "commits": len(commits)}


def _collect_verdict(ctx, group: GroupConfig, rec: RunRecord, when: datetime) -> dict:
    result, reason = _read_outcome(rec)
    wt = Path(rec.worktree)
    sha = head_sha(wt) if wt.exists() else ""
    results_dir = ctx.store.home / "results" / group.name
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{rec.kind}-{rec.issue}.json"
    payload = {"kind": rec.kind, "pr": rec.pr, "head_sha": sha, "collected_at": _iso(when), "timed_out": rec.timed_out,
               "reason": reason, "result": asdict(result) if result else None,
               "log_tail": tail_lines(Path(rec.log_path), ctx.cfg.worker_log_tail_lines)}
    path.write_text(json.dumps(payload, indent=2))
    ctx.store.write_run(replace(rec, collected=True))
    return {"collected": rec.issue, "kind": rec.kind, "stored": str(path), "verdict": result.verdict if result else None}
```

- [ ] **Step 4: CLI**

Import `collect as do_collect` and append:

```python
@main.command()
@click.argument("number", type=int)
@click.pass_context
def collect(ctx: click.Context, number: int) -> None:
    """Finish a worker run: push and open the PR, or mark the issue blocked or needs-human."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_collect(c, number))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_collect.py -v` then `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/meld/dispatch.py src/meld/cli.py tests/test_collect.py
git commit -m "feat: meld collect pushes branches, opens PRs, requests review, and stores verdicts"
```

---

### Task 13: `meld reap`

**Files:**
- Modify: `src/meld/dispatch.py`, `src/meld/cli.py`
- Create: `tests/test_reap.py`

**Interfaces:**
- `meld.dispatch.reap(ctx, number) -> dict`. PM only. Requires a collected, non-live run record whose issue is closed, whose PR is merged or closed, or whose kind is `review`/`audit`; otherwise refuses (`Verb.REAP`). Removes the worktree if present, deletes the local branch for build runs, archives the run record (`StateStore.archive_run`), returns `{"reaped": number, "worktree_removed": bool, "archived": path}`.
- CLI: `meld reap NUMBER`.

- [ ] **Step 1: Write the failing tests**

`tests/test_reap.py`:

```python
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from meld.actions import Refused
from meld.dispatch import collect, dispatch, reap
from meld.gitops import worktree_exists
from tests.fakeadapter import FakeAdapter
from tests.productfixtures import DONE_SCRIPT, R, claim_issue_11, fixture, make_product, wait_for_exit
from tests.test_collect import routes_for_labels

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def test_reap_after_merge_removes_worktree_and_archives(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    routes_for_labels(fake, 11)
    fake.route("POST", R + "pulls", {"number": 21})
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=DONE_SCRIPT), now=NOW)
    wait_for_exit(out["pid"])
    collect(ctx, 11, now=NOW)
    with pytest.raises(Refused, match="still open"):
        reap(ctx, 11)
    issues = fixture("issues.json")
    for i in issues:
        if i["number"] == 11:
            i["state"] = "closed"
    fake.route("GET", R + "issues", issues)
    result = reap(ctx, 11)
    assert result["reaped"] == 11 and result["worktree_removed"] is True
    assert not worktree_exists(repo, Path(out["worktree"])) and not Path(out["worktree"]).exists()
    assert Path(result["archived"]).parent == tmp_path / "home" / "runs" / "claude" / "archive"
    assert not (tmp_path / "home" / "runs" / "claude" / "11.json").exists()
    assert ctx.store.load_spend(3) == {"claude": {"medium": 1}}
    with pytest.raises(Refused, match="no run record"):
        reap(ctx, 11)


def test_reap_refuses_live_or_uncollected(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    out = dispatch(ctx, 11, adapter=FakeAdapter(), now=NOW)
    with pytest.raises(Refused, match="still running|collect first"):
        reap(ctx, 11)
    wait_for_exit(out["pid"])
    with pytest.raises(Refused, match="collect first"):
        reap(ctx, 11)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_reap.py -v`
Expected: FAIL with `ImportError: cannot import name 'reap'`

- [ ] **Step 3: Implement `reap`**

Append to `src/meld/dispatch.py` (import `delete_local_branch` from `meld.gitops`):

```python
def reap(ctx, number: int) -> dict:
    """Remove a finished run's worktree and archive its record once its work is merged or closed."""
    _, board = build_status(ctx)
    actor = ctx.actor()
    target = f"#{number}"
    if not actor.is_pm:
        refuse(ctx, board, Refusal(Verb.REAP, target, "only the PM may reap"))
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    rec = board.run_for(group.name, number)
    if rec is None:
        refuse(ctx, board, Refusal(Verb.REAP, target, f"no run record for {target} in {group.name}"))
    if rec.alive:
        refuse(ctx, board, Refusal(Verb.REAP, target, "worker is still running"))
    if not rec.collected:
        refuse(ctx, board, Refusal(Verb.REAP, target, "run has not been collected; collect first"))
    issue = board.issues.get(number)
    pr = board.pr_for_issue(number) or board.pulls.get(number)
    finished = rec.kind != "build" or (issue is not None and issue.state == "closed") or (pr is not None and (pr.merged or pr.state == "closed"))
    if not finished:
        refuse(ctx, board, Refusal(Verb.REAP, target, "issue is still open and its PR is not merged"))
    wt = Path(rec.worktree)
    removed = False
    if worktree_exists(ctx.repo_root, wt) or wt.exists():
        remove_worktree(ctx.repo_root, wt)
        removed = True
    if rec.kind == "build" and rec.branch:
        delete_local_branch(ctx.repo_root, rec.branch)
    archived = ctx.store.archive_run(group.name, number)
    return {"reaped": number, "worktree_removed": removed, "archived": str(archived)}
```

- [ ] **Step 4: CLI**

Import `reap as do_reap` and append:

```python
@main.command()
@click.argument("number", type=int)
@click.pass_context
def reap(ctx: click.Context, number: int) -> None:
    """Remove a finished run's worktree and archive its record."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_reap(c, number))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_reap.py -v` then `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/meld/dispatch.py src/meld/cli.py tests/test_reap.py
git commit -m "feat: meld reap archives finished runs and removes their worktrees"
```

---

### Task 14: `meld roster` and adapter skills in the roster

**Files:**
- Modify: `src/meld/context.py`, `src/meld/dispatch.py`, `src/meld/cli.py`
- Create: `tests/test_roster_cli.py`

**Interfaces:**
- `meld.context.Context` gains a trailing field `adapter_for: Callable[[str], Adapter] = get_adapter` and methods `adapter() -> Adapter | None` (the group's adapter for group/PM parties, `None` for humans) and `roster_entries() -> list[WorkerType]` (`roster(repo_root, extra=adapter.installed_skills())`); `actor()` builds its roster names from `roster_entries()`. `load_context(..., adapter_for=get_adapter)`.
- `meld.dispatch._adapter` prefers an explicit `adapter` argument, else `ctx.adapter()`.
- `meld.cli`: module attribute `ADAPTER_FOR = get_adapter` (tests monkeypatch it) passed to `load_context`; command `meld roster` printing `[{"name", "description", "source"}]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_roster_cli.py`:

```python
import json
from pathlib import Path

from click.testing import CliRunner

import meld.cli as cli
from meld.context import load_context
from meld.roster import WorkerType
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.fakeadapter import FakeAdapter
from tests.fakegh import FakeGh

SKILL = WorkerType("marketing", "Launch copy and positioning.", "skill", None, "---\nname: marketing\n---\n")


def test_context_roster_includes_adapter_skills_for_groups_only(tmp_path: Path):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    factory = lambda cli_name: FakeAdapter(skills=[SKILL])  # noqa: E731
    ctx = load_context(tmp_path / ".meld.toml", env=env_for(tmp_path), run=fake, adapter_for=factory)
    assert ctx.adapter() is not None and "marketing" in ctx.actor().roster
    assert [w.name for w in ctx.roster_entries()][:2] == ["auditor", "backend"] and "marketing" in [w.name for w in ctx.roster_entries()]
    human = load_context(tmp_path / ".meld.toml", env=env_for(tmp_path, group=None), run=fake, adapter_for=factory)
    assert human.adapter() is None and human.actor().roster == frozenset()


def test_roster_command(tmp_path: Path, monkeypatch):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    monkeypatch.setattr(cli, "RUN", fake)
    monkeypatch.setattr(cli, "ADAPTER_FOR", lambda cli_name: FakeAdapter(skills=[SKILL]))
    result = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "roster"], env=env_for(tmp_path))
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert {"name": "marketing", "description": "Launch copy and positioning.", "source": "skill"} in data
    assert [d["name"] for d in data][:2] == ["auditor", "backend"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_roster_cli.py -v`
Expected: FAIL (`load_context` rejects `adapter_for`).

- [ ] **Step 3: Implement**

`src/meld/context.py`: import `from typing import Callable, Mapping`, `from meld.adapters import Adapter, get_adapter`, `from meld.roster import WorkerType, roster`. Add to `Context`:

```python
    adapter_for: Callable[[str], Adapter] = get_adapter

    def adapter(self) -> Adapter | None:
        if not self.party.group:
            return None
        return self.adapter_for(self.cfg.groups[self.party.group].cli)

    def roster_entries(self) -> list[WorkerType]:
        adapter = self.adapter()
        return roster(self.repo_root, adapter.installed_skills() if adapter else ())

    def actor(self) -> Actor:
        names = frozenset(w.name for w in self.roster_entries()) if self.party.group else frozenset()
        return Actor(self.party, self.actor_login, names)
```

(replace the old `actor` body; drop the now-unused `roster_names` import). Add `adapter_for: Callable[[str], Adapter] = get_adapter` to `load_context`'s signature and pass `adapter_for=adapter_for` into `Context(...)`.

`src/meld/dispatch.py`: `_adapter` becomes `return adapter if adapter is not None else ctx.adapter()`, raising `DispatchError("no adapter for a human party")` when that is `None`.

`src/meld/cli.py`: add `from meld.adapters import get_adapter`, `ADAPTER_FOR = get_adapter`, pass `adapter_for=ADAPTER_FOR` in `_load`, and append:

```python
@main.command()
@click.pass_context
def roster(ctx: click.Context) -> None:
    """List the worker types available to the calling party."""
    c = _load(ctx)
    emit([{"name": w.name, "description": w.description, "source": w.source} for w in _guard(c.roster_entries)], ctx.obj["pretty"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_roster_cli.py tests/test_dispatch.py -v` then `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/meld/context.py src/meld/dispatch.py src/meld/cli.py tests/test_roster_cli.py
git commit -m "feat: meld roster and adapter-provided skills in the party's roster"
```

---

### Task 15: Role prompts (`_preamble.md`, `orchestrator.md`, `pm.md`), `role_prompt`, and `meld tag`

**Files:**
- Create: `src/meld/prompts/_preamble.md`, `src/meld/prompts/orchestrator.md`, `src/meld/prompts/pm.md`
- Create: `src/meld/prompts.py`, `src/meld/tagging.py`
- Modify: `src/meld/cli.py`
- Create: `tests/test_role_prompts.py`, `tests/test_tagging.py`

**Interfaces:**
- `meld.prompts.role_prompt(role: str, cfg: Config, group: GroupConfig, repo_root: Path, *, roster_entries: list[WorkerType] | None = None) -> str` for `role in ("orchestrator", "pm")`: preamble + role file + a generated "## Your configuration" block (repo, group, bot, the other groups and their bots, humans, this role's model and effort, caps, cadences, timeouts, budgets, effective quorum) + "## Your roster" (name and description per entry) + a "## Commands available" list. `ROLES = ("orchestrator", "pm")`.
- `meld.tagging.tag(ctx, number, *, worker=None, model=None, effort=None, size=None) -> dict`: orchestrator (party kind `group`) or human only; the issue must be in the active milestone; at least one field; for a group actor `model`/`effort` must validate against the group's adapter (`models.validate`), `size` in `SIZES`, `worker` in the actor's roster; a human's values are checked only for `size`. Replaces any existing `worker:`/`model:`/`effort:`/`size:` label of the same prefix. Returns `{"tagged": number, "labels": {"worker": ..., "model": ..., "effort": ..., "size": ...}}` with the final values. Rule-level refusals use `Verb.TAG`.
- CLI: `meld tag NUMBER [--worker W] [--model M] [--effort E] [--size S]`.

- [ ] **Step 1: Write the prompt files**

`src/meld/prompts/_preamble.md`:

```markdown
# Meld: how every long-lived role works

You are one of several AI systems working on the same GitHub repository. Each system is a **group** with its own bot account; you speak only for your group. The rules below are enforced by the `meld` command, not by good intentions: an illegal call is refused with the rule it broke and posts `PROTOCOL-VIOLATION` where every other group and the human can see it. Work with the tool, not around it.

## Cardinal rules

1. **Always start with `meld status`** (or the `meld wait` result you just received). Act only on verbs it lists as legal for the object you are touching. Never assign, label, comment, or merge through any other tool.
2. **Everything durable lives in GitHub.** You keep no private state. If you are restarted you rebuild your picture from `meld status` and the coordination issue; nothing is lost.
3. **Claims do not conflict.** Whether you or another group work an issue is decided by GitHub assignment through `meld claim`. Losing a race is normal and not a violation; move on.
4. **No claim without evidence.** Every `PLAN`, `DECISION`, `ANSWER`, and `EVIDENCE` comment must carry a `verified-by:` line naming the command run, test executed, or document read. "Tests pass" without the command is not evidence and will be challenged.
5. **Talk on the coordination issue and nowhere else.** Cross-group and human conversation happens as comments there through `meld post`. Only `meld` writes `[joint]`, and only after verifying agreement.
6. **Secrets never appear in comments, briefs, or files.** Refer to where a secret lives (an environment variable or vault item), never to its value.
7. **HALT stops everything.** When the human posts `HALT`, claiming and dispatching stop until `RESUME`; keep watching, do nothing else.

## The command surface

- `meld status` — the board as JSON, with `legal` verbs per issue and PR for you. Read `needs_human`, `budget`, `halted`, and `coordination.agreements` first.
- `meld wait --filter <kinds>` — block until the board changes (`phase, halted, issue, pr, run, comments, challenges`). Use it instead of polling; the process spends nothing while it waits. Keep `--timeout` under your shell command ceiling.
- `meld post VERB [REF] -m "..." [--joint]` — speak in the header grammar. Usage errors exit 2 with `{"error": ...}`; rule refusals exit 2 with `{"refused": ...}` and post a violation.
- `meld claim N`, `meld release N`, `meld dispatch N [--kind review|audit]`, `meld tick`, `meld collect N`, `meld reap N`, `meld tag N ...`, `meld roster`, `meld models`.
- Exit codes: `0` success; `1` an error you should report (GitHub, config, board); `2` refused by the rules or a usage error. Always read the JSON.

## The header grammar

Every coordination comment starts with `[<party>] <VERB> [<ref>] [<qualifier>]`. Your party is `[<group>]` as orchestrator or `[<group>:pm]` as PM; the human is `[human]`; `[joint]` is written only by `meld`. Refs are `#<number>` or lowercase ids like `plan-1` or `ask-3`. Agreement runs `PROPOSE` (or `DRAFT` for chat) → `AGREE` / `AMEND` / `DISSENT` → `meld post --joint <VERB> <ref>` by the proposer once every group has agreed (or, past the amend cap, when a majority has). A re-proposal counts as an amend round; the amend cap is enforced.
```

`src/meld/prompts/orchestrator.md`:

```markdown
# You are the orchestrator of your group

You own your group's plan, its relationship with the other groups, and its answers to the human. You never write product code and never dispatch workers; your PM does the dispatching from the tags you set.

## Your loop

1. `meld status`. On your first turn after a start or restart, post `[<group>] CONFIG` (your model, effort, caps) and `[<group>] ROSTER` (the output of `meld roster`), then `RESUMED` if you were restarted.
2. `meld wait --filter comments,challenges,issue,phase,halted` with a timeout under your command ceiling.
3. For each change: read the new coordination comments in order and act on the ones addressed to all groups or to you (`ASK`, `DIRECTIVE`, `PROPOSE`, `AMEND`, `AGREE`, `DISSENT`, `CHALLENGE`, `EVIDENCE`, `NEEDS-HUMAN`, `BUDGET`, `REVIEW-REQUEST` from other groups, `PROTOCOL-VIOLATION`). Then look at `blocked` and `needs_human` issues and at the roster diff.
4. Repeat. Never idle-loop faster than the poll interval; `meld wait` does the waiting.

## Planning

- In the `planning` phase, read the goal on the coordination issue, then propose a decomposition with `meld post PROPOSE plan-<milestone> -m "..."`: one bullet per work issue with an id, a title, acceptance criteria, the worker type, and the model, effort, and size you would tag it with (see the rubric). Other groups reply `AGREE`, `AMEND`, or `DISSENT`; converge, then the proposer runs `meld post --joint PLAN plan-<milestone>` with a `verified-by:` line naming what you checked (repository files read, `meld roster`, `meld models`).
- After the human adds `meld:plan-approved`, tag every issue that lacks tags with `meld tag N --worker W --model M --effort E --size S`. The PM claims and dispatches from those tags and never chooses models itself.
- Mid-milestone additions go through `PROPOSE`/`AGREE` as `plan-add-<n>`, then `meld post --joint PLAN-ADD`, and become `ready` only after the human `ACK`s.

## The model and effort rubric

Read `meld models` before tagging. Inputs: task type, blast radius (files and systems touched), ambiguity, and whether this is a first attempt or a retry.

| Situation | Model tier | Effort | Size |
|---|---|---|---|
| Mechanical change with a precise spec, one or two files | small | low or medium | S |
| Ordinary feature or fix touching a few files, clear acceptance criteria | medium | medium | S or M |
| Cross-cutting change, ambiguous requirements, security-sensitive paths, or anything a review already sent back | large | high | M or L |
| Anything you cannot honestly call L | split it into two issues | | |

Rules: there is no `XL`; on a retry after a failure or `request-changes`, raise effort one level or move up a tier before trying the same approach again; prefer the cheapest tier whose context class fits the size (`meld status` refuses a dispatch whose size does not fit the model). Worked examples: "rename a config key everywhere" → small, low, S. "add an endpoint with tests following an existing pattern" → medium, medium, S. "introduce OAuth login across client and server" → large, high, L, and probably two issues.

## Chat with the human

An `[human] ASK <id>` deserves one agreed answer from all groups. If you see it first, post `DRAFT <id>` with your answer and a `verified-by:` line; otherwise reply `AGREE <id>` or `AMEND <id>` with better text. When every group agrees, the drafter posts `meld post --joint ANSWER <id>`. Past the amend cap with no unanimity, post the joint answer with the qualifier `DISAGREE` so the human sees each position. A `DIRECTIVE` needs `ACK` from every group and overrides the plan.

## Keeping the record honest

- If another group asserts something you doubt, post `CHALLENGE <ref>` stating the doubted claim. The author must answer with `EVIDENCE` or `RETRACT`; a third party or the human closes it. A challenged issue or PR cannot be dispatched or merged until then.
- After every restart compare the board with the approved plan and post `DRIFT` if scope, approach, or assumptions moved without a recorded decision.
- When a blocked issue needs a person (a credential, an account, a product decision), post `NEEDS-HUMAN #<n>` with what is needed, why you cannot do it, and what happens once it is provided. When a budget tier is exhausted, post `BUDGET` or re-tag remaining issues to a cheaper tier.
- If another group has been silent past the grace period, post `PING`; after the grace period passes, post `SOLO` and continue.
```

`src/meld/prompts/pm.md`:

```markdown
# You are the project manager of your group

You turn tagged issues into pushed branches and open pull requests, and you keep your group's runs healthy. You never choose models or efforts; if a tag looks wrong, say so on the coordination issue and let the orchestrator re-tag.

## Your loop

1. `meld tick` — kills overdue workers, creates any missing lease, and releases other groups' expired claims. Run it first, every cycle.
2. `meld status`. On your first turn after a start or restart post `[<group>:pm] RESUMED`.
3. In the `executing` phase, in this order:
   - `meld collect N` for every run whose state is `finished`. Collect never waits on you: a finished run is either pushed and reviewed or marked blocked; leaving it uncollected wastes the concurrency slot.
   - `meld reap N` for every collected run whose issue is closed or whose PR is merged.
   - `meld dispatch N --kind review` for every `REVIEW-REQUEST` from another group that you have not yet reviewed and that `meld status` shows as legal (you cannot review your own group's head).
   - `meld dispatch N` for every `in_progress` issue assigned to your bot with no live run.
   - `meld claim N` for `ready` issues while `claim` is legal; the caps and budgets are enforced for you, so claim until refused.
4. Every `summary_cadence_minutes`, post `[<group>:pm] STATUS`: needs-human items first, then budget by tier, then running, finished, blocked, and in-review counts.
5. `meld wait --filter run,issue,pr,comments` with a timeout under your command ceiling, then repeat.

## What collect does for you

`meld collect` reads the worker's `.meld-result.json`. A `done` result with commits is pushed, a PR is opened with `Closes #N` and the worker's `verified-by:` line, the issue moves to `in-review`, and `REVIEW-REQUEST #<pr>` is posted for the other groups. A `blocked` result moves the issue to `needs-human` and posts `NEEDS-HUMAN` with the worker's ask. Anything else (timeout, no result, `failed`, no commits) moves the issue to `blocked` with the log tail; the orchestrator decides whether to re-tag and retry.

## Review and audit runs

You dispatch reviewers and auditors, and you collect them; their verdicts are stored under `$MELD_HOME/results/<group>/` for the review and audit verbs. Never review your own group's head, never dispatch beyond `review_concurrency`, and re-dispatch a reviewer when the other group pushes a fix onto a PR you already reviewed.

## When something is off

- A dispatch, claim, or collect refusal tells you the rule. Do not retry the same call; fix the cause or report it.
- A worker that keeps failing on the same issue is the orchestrator's problem: post the failure summary on the coordination issue rather than dispatching a fourth time.
- If `meld status` shows `state_errors`, report them in your next `STATUS`; they mean a corrupt local file was skipped.
```

- [ ] **Step 2: Write the failing tests**

`tests/test_role_prompts.py`:

```python
from pathlib import Path

import pytest

from meld.prompts import ROLES, role_prompt
from tests.factories import CFG2


def test_role_prompt_composes_preamble_role_and_configuration(tmp_path: Path):
    text = role_prompt("orchestrator", CFG2, CFG2.groups["claude"], tmp_path)
    for needle in ("# Meld: how every long-lived role works", "# You are the orchestrator", "## Your configuration",
                   "acme/widgets", "meld-claude-bot", "codex (bot `meld-codex-bot`)", "empowerScott", "claude-opus-5", "effort high",
                   "concurrency 8", "quorum 1", "## Your roster", "backend", "## Commands available", "meld tag"):
        assert needle in text, needle
    pm = role_prompt("pm", CFG2, CFG2.groups["codex"], tmp_path)
    assert "# You are the project manager" in pm and "claude-sonnet-5" in pm and "meld tick" in pm
    assert ROLES == ("orchestrator", "pm")
    with pytest.raises(ValueError):
        role_prompt("worker", CFG2, CFG2.groups["claude"], tmp_path)
```

`tests/test_tagging.py`:

```python
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.actions import Refused
from meld.context import load_context
from meld.tagging import tag
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.fakegh import FakeGh

R = "repos/acme/widgets/"


def make(tmp_path, group="claude", role="orchestrator"):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    fake.route("POST", R + "issues/11/labels", [])
    for label in ("model%3Aclaude-sonnet-5", "effort%3Amedium", "size%3AS", "worker%3Abackend"):
        fake.route("DELETE", R + f"issues/11/labels/{label}", [])
    fake.route("POST", R + "issues/10/comments", {"id": 1})
    return fake, load_context(tmp_path / ".meld.toml", env=env_for(tmp_path, group=group, role=role), run=fake)


def label_calls(fake):
    return [(c.method, c.endpoint.split("issues/11/labels", 1)[1], c.body) for c in fake.calls if c.method != "GET" and "issues/11/labels" in c.endpoint]


def test_tag_replaces_labels_and_validates(tmp_path):
    fake, ctx = make(tmp_path)
    out = tag(ctx, 11, model="claude-opus-5", effort="high", size="M")
    assert out == {"tagged": 11, "labels": {"worker": "backend", "model": "claude-opus-5", "effort": "high", "size": "M"}}
    calls = label_calls(fake)
    assert ("POST", "", {"labels": ["model:claude-opus-5", "effort:high", "size:M"]}) in calls
    assert ("DELETE", "/model%3Aclaude-sonnet-5", None) in calls and ("DELETE", "/effort%3Amedium", None) in calls and ("DELETE", "/size%3AS", None) in calls
    assert not any(e.startswith("/worker") for _, e, _ in calls)


def test_tag_refusals(tmp_path):
    fake, ctx = make(tmp_path)
    with pytest.raises(Refused, match="at least one"):
        tag(ctx, 11)
    with pytest.raises(Refused, match="not valid"):
        tag(ctx, 11, effort="turbo")
    with pytest.raises(Refused, match="unknown for adapter"):
        tag(ctx, 11, model="gpt-5.6-sol")
    with pytest.raises(Refused, match="size"):
        tag(ctx, 11, size="XL")
    with pytest.raises(Refused, match="roster"):
        tag(ctx, 11, worker="marketing")
    with pytest.raises(Refused, match="not in the active milestone"):
        tag(ctx, 999, size="S")
    _, pm = make(tmp_path / "pm", role="pm")
    with pytest.raises(Refused, match="orchestrator or a human"):
        tag(pm, 11, size="S")


def test_human_may_tag_any_model(tmp_path):
    fake, human = make(tmp_path, group=None)
    out = tag(human, 11, model="gpt-5.6-sol", effort="xhigh")
    assert out["labels"]["model"] == "gpt-5.6-sol"


def test_cli_tag(tmp_path, monkeypatch):
    fake, _ = make(tmp_path)
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "tag", "11", "--size", "M"],
                                env=env_for(tmp_path, role="orchestrator"))
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["labels"]["size"] == "M"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_role_prompts.py tests/test_tagging.py -v`
Expected: FAIL with `ModuleNotFoundError` for `meld.prompts` and `meld.tagging`.

- [ ] **Step 4: Write `src/meld/prompts.py`**

```python
"""Render the long-lived role prompts from the packaged markdown plus the group's configuration."""
from __future__ import annotations

from importlib import resources
from pathlib import Path

from meld.config import Config, GroupConfig
from meld.roster import WorkerType, roster

ROLES = ("orchestrator", "pm")
COMMANDS = ("meld status", "meld wait --filter <kinds> [--timeout S]", "meld post VERB [REF] -m TEXT [--joint]", "meld claim N",
            "meld release N", "meld dispatch N [--kind review|audit]", "meld tick", "meld collect N", "meld reap N",
            "meld tag N [--worker W] [--model M] [--effort E] [--size S]", "meld roster", "meld models")


def _read(name: str) -> str:
    return resources.files("meld").joinpath("prompts", name).read_text()


def role_prompt(role: str, cfg: Config, group: GroupConfig, repo_root: Path, *, roster_entries: list[WorkerType] | None = None) -> str:
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}, got {role!r}")
    own = group.orchestrator if role == "orchestrator" else group.pm
    others = [g for g in cfg.groups.values() if g.name != group.name]
    lines = [
        "## Your configuration", "",
        f"- Repository: `{cfg.repo_name}` (default branch `{cfg.default_branch}`)",
        f"- Your group: **{group.name}**, bot `{group.bot}`; you speak as `[{group.name}]` (orchestrator) or `[{group.name}:pm]` (PM)",
        "- Other groups: " + (", ".join(f"{g.name} (bot `{g.bot}`)" for g in others) or "none"),
        "- Humans: " + (", ".join(f"`{h}`" for h in cfg.humans) or "none listed"),
        f"- You run as `{own.model}` at effort {own.effort}",
        f"- Caps: concurrency {group.concurrency}, review concurrency {group.review_concurrency}; review quorum {cfg.effective_quorum}",
        f"- Cadences: poll every {cfg.poll_interval_seconds}s, wait timeout {cfg.wait_timeout_seconds}s, restart every {cfg.restart_hours}h, "
        f"roster refresh every {cfg.roster_refresh_minutes}m, silence grace {cfg.silence_grace_minutes}m, status summary every {cfg.summary_cadence_minutes}m",
        f"- Worker timeouts: S {cfg.timeouts['S']}, M {cfg.timeouts['M']}, L {cfg.timeouts['L']}; amend cap {cfg.amend_cap}; ping-pong cap {cfg.pingpong_cap}",
        "- Budget (worker runs per tier this milestone): " + ", ".join(f"{k} {v}" for k, v in cfg.budget.items()),
        "", "## Your roster", "",
    ]
    entries = roster_entries if roster_entries is not None else roster(repo_root)
    lines += [f"- `{w.name}` ({w.source}): {w.description}" for w in entries]
    lines += ["", "## Commands available", ""] + [f"- `{c}`" for c in COMMANDS]
    return "\n".join([_read("_preamble.md").strip(), "", _read(f"{role}.md").strip(), "", *lines, ""])
```

- [ ] **Step 5: Write `src/meld/tagging.py`**

```python
"""meld tag: the orchestrator (or a human) sets worker, model, effort, and size labels on an issue."""
from __future__ import annotations

from meld.actions import refuse
from meld.models import SIZES
from meld.rules import Refusal, Verb
from meld.status import build_status

PREFIXES = ("worker", "model", "effort", "size")


def tag(ctx, number: int, *, worker: str | None = None, model: str | None = None, effort: str | None = None, size: str | None = None) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    target = f"#{number}"
    if not (actor.is_human or actor.party.kind == "group"):
        refuse(ctx, board, Refusal(Verb.TAG, target, "only the orchestrator or a human may tag"))
    issue = board.issues.get(number)
    if issue is None:
        refuse(ctx, board, Refusal(Verb.TAG, target, "issue is not in the active milestone"))
    wanted = {"worker": worker, "model": model, "effort": effort, "size": size}
    if not any(wanted.values()):
        refuse(ctx, board, Refusal(Verb.TAG, target, "provide at least one of --worker, --model, --effort, --size"))
    if size is not None and size not in SIZES:
        refuse(ctx, board, Refusal(Verb.TAG, target, f"size '{size}' is not one of {', '.join(SIZES)}"))
    if not actor.is_human:
        group = ctx.cfg.groups[actor.group]  # type: ignore[index]
        final_model = model or issue.model
        final_effort = effort or issue.effort
        if (model or effort) and final_model and final_effort:
            errors = ctx.models.validate(group.cli, final_model, final_effort)
            if errors:
                refuse(ctx, board, Refusal(Verb.TAG, target, errors[0]))
        elif model and ctx.models.get(group.cli, model) is None:
            refuse(ctx, board, Refusal(Verb.TAG, target, f"model '{model}' unknown for adapter '{group.cli}'"))
        if worker is not None and worker not in actor.roster:
            refuse(ctx, board, Refusal(Verb.TAG, target, f"worker type '{worker}' is not in {group.name}'s roster"))
    to_add = [f"{prefix}:{value}" for prefix, value in wanted.items() if value]
    for prefix, value in wanted.items():
        if value:
            for label in sorted(issue.labels):
                if label.startswith(f"{prefix}:") and label != f"{prefix}:{value}":
                    ctx.gh.remove_label(number, label)
    ctx.gh.add_labels(number, to_add)
    final = {prefix: (wanted[prefix] or getattr(issue, prefix)) for prefix in PREFIXES}
    return {"tagged": number, "labels": final}
```

- [ ] **Step 6: CLI**

Import `from meld.tagging import tag as do_tag` and append:

```python
@main.command()
@click.argument("number", type=int)
@click.option("--worker", default=None)
@click.option("--model", default=None)
@click.option("--effort", default=None)
@click.option("--size", default=None, type=click.Choice(["S", "M", "L"]))
@click.pass_context
def tag(ctx: click.Context, number: int, worker: str | None, model: str | None, effort: str | None, size: str | None) -> None:
    """Set the worker, model, effort, and size labels on an issue (orchestrator or human)."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_tag(c, number, worker=worker, model=model, effort=effort, size=size))
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_role_prompts.py tests/test_tagging.py -v` then `uv run pytest -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/meld/prompts src/meld/prompts.py src/meld/tagging.py src/meld/cli.py tests/test_role_prompts.py tests/test_tagging.py
git commit -m "feat: orchestrator and PM role prompts, role_prompt rendering, and meld tag"
```

---

### Task 16: Runners — `meld run <group>`

**Files:**
- Create: `src/meld/runners.py`
- Modify: `src/meld/cli.py`
- Create: `tests/test_runners.py`

**Interfaces:**
- `meld.runners`: `@dataclass class RoleProcess(role: str, proc: subprocess.Popen, started: float, log_path: Path, restarts: int = 0)`; `role_env(ctx, group, role) -> dict[str, str]` (a copy of the process environment plus `MELD_GROUP`, `MELD_ROLE`, `MELD_HOME`; tokens pass through untouched because these roles do talk to GitHub); `launch_role(ctx, group, adapter, role, *, clock=time.monotonic) -> RoleProcess` (`LaunchSpec(role=role, model=<role RoleConfig>.model, effort=..., cwd=repo_root, prompt=role_prompt(...), log_path=$MELD_HOME/logs/<group>/<role>-<stamp>.log, timeout_seconds=restart_hours*3600, env=role_env, max_turns=10000)`); `run_group(ctx, *, adapter=None, once=False, cycles: int | None = None, interval=5.0, sleep=time.sleep, clock=time.monotonic) -> dict` returning `{"group", "roles": {role: {"launches": n, "last_exit": code | None}}}`. Behaviour: launches both roles; each cycle, a role whose process has exited is relaunched after a backoff of `min(5 * 2**restarts, 300)` seconds (unless `once`), and a role running longer than `restart_hours` is terminated (SIGTERM to its process group, SIGKILL after 10 s) and relaunched. `once=True` returns when both roles have exited; `cycles` bounds the loop for tests. `SIGTERM`/`SIGINT` to the supervisor terminates both children before exiting.
- CLI: `meld run GROUP [--once]` (constructs the context for that group's orchestrator party from the environment).

- [ ] **Step 1: Write the failing tests**

`tests/test_runners.py`:

```python
import json
from pathlib import Path

from meld.runners import launch_role, role_env, run_group
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.fakeadapter import FakeAdapter
from tests.fakegh import FakeGh
from meld.context import load_context


def make(tmp_path: Path):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    env = env_for(tmp_path, role="orchestrator")
    ctx = load_context(tmp_path / ".meld.toml", env=env, run=fake, adapter_for=lambda name: FakeAdapter())
    return ctx, env


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_role_env_carries_identity_and_tokens(tmp_path):
    ctx, env = make(tmp_path)
    e = role_env(ctx, ctx.cfg.groups["claude"], "pm", base_env=env)
    assert e["MELD_GROUP"] == "claude" and e["MELD_ROLE"] == "pm" and e["MELD_TOKEN_CLAUDE"] == "tok-c" and e["MELD_HOME"] == str(tmp_path / "home")


def test_launch_role_builds_the_spec(tmp_path):
    ctx, env = make(tmp_path)
    adapter = FakeAdapter()
    rp = launch_role(ctx, ctx.cfg.groups["claude"], adapter, "orchestrator", base_env=env)
    spec = adapter.launches[0]
    assert spec.role == "orchestrator" and spec.model == "claude-opus-5" and spec.effort == "high" and spec.cwd == tmp_path
    assert spec.prompt.startswith("# Meld: how every long-lived role works") and "# You are the orchestrator" in spec.prompt
    assert spec.timeout_seconds == 4 * 3600 and spec.env["MELD_ROLE"] == "orchestrator" and spec.log_path.parent == tmp_path / "home" / "logs" / "claude"
    rp.proc.wait(timeout=10)


def test_run_group_once_launches_both_roles_and_returns_when_they_exit(tmp_path):
    ctx, env = make(tmp_path)
    adapter = FakeAdapter(script="import sys; sys.exit(0)")
    clock = Clock()
    out = run_group(ctx, adapter=adapter, once=True, interval=0.05, base_env=env)
    assert sorted(s.role for s in adapter.launches) == ["orchestrator", "pm"]
    assert out["group"] == "claude" and out["roles"]["pm"]["launches"] == 1 and out["roles"]["pm"]["last_exit"] == 0


def test_run_group_restarts_exited_roles_with_backoff(tmp_path):
    ctx, env = make(tmp_path)
    adapter = FakeAdapter(script="import sys; sys.exit(1)")
    clock = Clock()
    slept: list[float] = []

    def sleep(s):
        slept.append(s)
        clock.sleep(s)
        import time
        time.sleep(0.05)  # let the tiny real processes exit

    out = run_group(ctx, adapter=adapter, cycles=4, interval=0.01, sleep=sleep, clock=clock, base_env=env)
    assert out["roles"]["orchestrator"]["launches"] >= 2 and out["roles"]["orchestrator"]["last_exit"] == 1
    assert 5.0 in slept  # first backoff is 5 s (virtual)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_runners.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.runners'`

- [ ] **Step 3: Write `src/meld/runners.py`**

```python
"""Supervise a group's two long-lived roles: launch, restart on exit, recycle on schedule."""
from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from meld.adapters import Adapter, LaunchSpec
from meld.config import GroupConfig
from meld.prompts import ROLES, role_prompt
from meld.state import pid_alive

ROLE_MAX_TURNS = 10000
_MAX_BACKOFF = 300.0


@dataclass
class RoleProcess:
    role: str
    proc: object
    started: float
    log_path: Path
    restarts: int = 0
    launches: int = 1
    last_exit: int | None = None


def role_env(ctx, group: GroupConfig, role: str, *, base_env: dict | None = None) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    env.update({"MELD_GROUP": group.name, "MELD_ROLE": role, "MELD_HOME": str(ctx.store.home)})
    return env


def launch_role(ctx, group: GroupConfig, adapter: Adapter, role: str, *, base_env: dict | None = None, clock=time.monotonic) -> RoleProcess:
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    cfg_role = group.orchestrator if role == "orchestrator" else group.pm
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = ctx.store.home / "logs" / group.name / f"{role}-{stamp}.log"
    spec = LaunchSpec(role=role, model=cfg_role.model, effort=cfg_role.effort, cwd=ctx.repo_root,
                      prompt=role_prompt(role, ctx.cfg, group, ctx.repo_root, roster_entries=ctx.roster_entries()),
                      log_path=log_path, timeout_seconds=ctx.cfg.restart_hours * 3600, env=role_env(ctx, group, role, base_env=base_env),
                      max_turns=ROLE_MAX_TURNS)
    proc = adapter.launch(spec)
    return RoleProcess(role=role, proc=proc, started=clock(), log_path=log_path)


def _terminate(proc, *, grace: float = 10.0, sleep=time.sleep) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    waited = 0.0
    while pid_alive(proc.pid) and waited < grace:
        sleep(0.1)
        waited += 0.1
    if pid_alive(proc.pid):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_group(ctx, *, adapter: Adapter | None = None, once: bool = False, cycles: int | None = None, interval: float = 5.0,
              sleep=time.sleep, clock=time.monotonic, base_env: dict | None = None) -> dict:
    group = ctx.cfg.groups[ctx.party.group]  # type: ignore[index]
    adp = adapter if adapter is not None else ctx.adapter()
    if adp is None:
        raise ValueError("meld run needs a group party; set MELD_GROUP or pass the group name")
    procs = {role: launch_role(ctx, group, adp, role, base_env=base_env, clock=clock) for role in ROLES}
    stop = {"now": False}

    def on_signal(signum, frame):  # noqa: ARG001
        stop["now"] = True

    previous = {sig: signal.signal(sig, on_signal) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        cycle = 0
        while not stop["now"]:
            cycle += 1
            for role, rp in procs.items():
                code = rp.proc.poll()
                if code is not None:
                    rp.last_exit = code
                    if once:
                        continue
                    backoff = min(5.0 * (2 ** rp.restarts), _MAX_BACKOFF)
                    sleep(backoff)
                    fresh = launch_role(ctx, group, adp, role, base_env=base_env, clock=clock)
                    fresh.restarts, fresh.launches, fresh.last_exit = rp.restarts + 1, rp.launches + 1, code
                    procs[role] = fresh
                elif clock() - rp.started >= ctx.cfg.restart_hours * 3600:
                    _terminate(rp.proc, sleep=sleep)
                    fresh = launch_role(ctx, group, adp, role, base_env=base_env, clock=clock)
                    fresh.launches, fresh.last_exit = rp.launches + 1, rp.last_exit
                    procs[role] = fresh
            if once and all(rp.proc.poll() is not None for rp in procs.values()):
                break
            if cycles is not None and cycle >= cycles:
                break
            sleep(interval)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if stop["now"]:
            for rp in procs.values():
                _terminate(rp.proc, sleep=sleep)
    return {"group": group.name, "roles": {role: {"launches": rp.launches, "last_exit": rp.proc.poll() if rp.proc.poll() is not None else rp.last_exit}
                                            for role, rp in procs.items()}}
```

- [ ] **Step 4: CLI**

Add `import os` if missing, `from meld.runners import run_group`, and append:

```python
@main.command()
@click.argument("group")
@click.option("--once", is_flag=True, help="Launch both roles once and return when they exit (no restarts).")
@click.pass_context
def run(ctx: click.Context, group: str, once: bool) -> None:
    """Supervise a group's orchestrator and PM processes."""
    env = {**os.environ, "MELD_GROUP": group, "MELD_ROLE": "orchestrator"}
    try:
        c = load_context(ctx.obj["config_path"], env=env, run=RUN, adapter_for=ADAPTER_FOR)
    except ConfigError as e:
        raise click.ClickException(str(e)) from e
    emit(_guard(lambda: run_group(c, once=once, base_env=env)), ctx.obj["pretty"])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_runners.py -v` then `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/meld/runners.py src/meld/cli.py tests/test_runners.py
git commit -m "feat: meld run supervises a group's orchestrator and PM"
```

---

### Task 17: Opt-in smoke tests, Taskfile, spec and README reconciliation

**Files:**
- Create: `tests/smoke/__init__.py`, `tests/smoke/test_adapters_smoke.py`
- Modify: `Taskfile.yml`, `README.md`, `docs/superpowers/specs/2026-09-03-meld-design.md`, `pyproject.toml`

**Interfaces:**
- Smoke tests run only with `MELD_SMOKE=1` and the CLI on `PATH`; each launches the real adapter on the prompt "Create a file named hello.txt containing exactly the word hello, then stop." inside a temporary git repository and asserts the file appears within 300 seconds.
- pytest ignores `tests/smoke` by default (`addopts = "--ignore=tests/smoke"`); `task test:smoke` runs it.

- [ ] **Step 1: Write the smoke tests**

`tests/smoke/__init__.py`: empty.

`tests/smoke/test_adapters_smoke.py`:

```python
"""Real-CLI smoke tests. Opt in with MELD_SMOKE=1; each test needs the CLI on PATH and a logged-in account."""
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from meld.adapters import get_adapter
from meld.adapters.base import LaunchSpec, scrub_env

pytestmark = pytest.mark.skipif(os.environ.get("MELD_SMOKE") != "1", reason="set MELD_SMOKE=1 to run real CLI smoke tests")
PROMPT = "Create a file named hello.txt containing exactly the word hello, then stop."


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    return tmp_path


@pytest.mark.parametrize("name,model", [("claude", "claude-sonnet-5"), ("codex", "gpt-5.6-terra")])
def test_adapter_creates_a_file(tmp_path: Path, name: str, model: str):
    if shutil.which(name) is None:
        pytest.skip(f"{name} not on PATH")
    adapter = get_adapter(name)
    spec = LaunchSpec(role="worker", model=model, effort="low", cwd=_repo(tmp_path), prompt=PROMPT, log_path=tmp_path / "log.txt",
                      timeout_seconds=300, env=scrub_env(dict(os.environ)), max_turns=10)
    proc = adapter.launch(spec)
    deadline = time.monotonic() + 300
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(1)
    if proc.poll() is None:
        proc.kill()
        pytest.fail(f"{name} did not finish within 300s; log:\n{(tmp_path / 'log.txt').read_text()[-2000:]}")
    assert (tmp_path / "hello.txt").read_text().strip() == "hello", (tmp_path / "log.txt").read_text()[-2000:]
```

- [ ] **Step 2: pytest and Taskfile**

In `pyproject.toml` under `[tool.pytest.ini_options]` add `addopts = "--ignore=tests/smoke"`. In `Taskfile.yml` replace the `test:smoke` task's command with `MELD_SMOKE=1 uv run pytest tests/smoke -q -p no:cacheprovider` and add:

```yaml
  run:
    desc: Supervise a group's orchestrator and PM (GROUP=claude|codex)
    cmds:
      - uv run meld run {{.GROUP}}
```

- [ ] **Step 3: README**

Extend the verbs table in `README.md` with rows for `meld dispatch N [--kind review|audit]`, `meld tick`, `meld collect N`, `meld reap N`, `meld tag N ...`, `meld roster`, `meld run GROUP [--once]`, and `meld wait --filter`. Add a section:

```markdown
## Running a group

    MELD_TOKEN_CLAUDE=... uv run meld run claude

`meld run` starts the group's orchestrator and PM as headless CLI sessions with the packaged role prompts, restarts them when they exit or every `runners.restart_hours`, and writes their logs under `$MELD_HOME/logs/<group>/`. Workers are separate processes started by the PM through `meld dispatch`; they run in `$MELD_HOME/worktrees/<group>/`, receive no GitHub credentials, and end by writing `.meld-result.json`, which `meld collect` turns into a pushed branch and a pull request.

Real-CLI smoke tests (they spend tokens): `task test:smoke`.
```

- [ ] **Step 4: Spec reconciliation**

In `docs/superpowers/specs/2026-09-03-meld-design.md`:
- §9 PM loop: add "`meld tick` first each cycle (timeouts, leases)"; add the orchestrator's `meld tag` responsibility.
- §11: state that the lease TTL is the worker deadline plus `lease.grace_minutes`, recorded in a `[<g>:pm] LEASE #<n>` comment on the coordination issue (no per-cycle renewal is needed; `tick` creates a missing lease and any PM may release an expired one, posting `EXPIRED #<n>`); that reviewer and auditor verdicts are stored under `$MELD_HOME/results/<group>/` for the `review` and `audit` verbs (plan 3); that reviewer and auditor runs use the group's orchestrator model and effort; that a retry reuses an existing worktree.
- §12: document `meld wait --filter <kinds>` and the change kinds.
- §16: name the adapter protocol as implemented (`launch(LaunchSpec)`, `installed_skills()`, `command_timeout_ceiling_seconds()`, `supports_effort()`), the Claude launch shape and environment, and the Codex launch shape.
- §17: add the row "Worker exceeds deadline | `meld tick` | SIGTERM then SIGKILL; `collect` marks blocked with the log tail".
- §21: mark borrowed items 1, 2, and 5 as landed in plan 2 (with the TTL variation), and add "PID reuse is not detected; a run record's pid plus deadline is the liveness signal" under future work.
- Plan-3 obligations list: add "`meld review`/`meld audit` consume `$MELD_HOME/results/<group>/<kind>-<n>.json`".

- [ ] **Step 5: Run everything**

Run: `uv run pytest -q` (smoke excluded) and, if you have both CLIs logged in and want to spend tokens, `task test:smoke`.
Expected: the unit suite passes with no warnings.

- [ ] **Step 6: Commit**

```bash
git add tests/smoke pyproject.toml Taskfile.yml README.md docs/superpowers/specs/2026-09-03-meld-design.md
git commit -m "docs: reconcile the spec and README with plan 2; add opt-in adapter smoke tests"
```

---

## Plan 2 exit criteria

- `uv run pytest -q` passes with no network, no real CLI, and no warnings; `tests/smoke` is opt-in and passes for each CLI logged in on this machine.
- A PM can, against a real repository prepared as in plan 1's exit criteria: `meld claim N`, `meld dispatch N`, `meld tick`, `meld collect N` (a branch is pushed and a PR opens with `Closes #N`), and `meld reap N` after merge.
- `meld run claude --once` launches an orchestrator and a PM session that each post `CONFIG`/`ROSTER`/`RESUMED` and exit.
- Plan 3 (review, merge, challenge, audit, decide, remember, recall, chat, plan, setup, doctor, notifier) is written against this plan's module names.
