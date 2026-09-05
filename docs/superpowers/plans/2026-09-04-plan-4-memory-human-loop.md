# Plan 4: Decision Memory, Plan Gate, Chat, and PM Housekeeping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Meld its human loop and its memory: agreed decisions and lessons become versioned files through auto-approved PRs and are recalled into briefs; the human approves plans, acks additions, and talks to all groups from one console; the PM's housekeeping tick handles red CI, unauthorized comments, off-protocol label changes, and Slack nudges for items that need a person; and the change digest stops missing the events the loops depend on.

**Architecture:** Same shape as plans 1–3. New action modules mirror `actions.py`/`review.py`: `memory.py` (`decide`, `remember`, `recall`, ADR rendering, content hashes), `plan.py` (`plan issues`, `plan approve`, `plan ack`), `chat.py` (inbox and REPL over the existing `post`/`challenge` actions), `notifier.py` (Slack webhook with a per-party throttle), `housekeeping.py` (CI-red, ignored-comment, and off-protocol sweeps called from `tick`). `github.py` stays the only GitHub path (it gains issue creation, milestone update, contents, events, labels, search); `rules.py` stays the only legality authority (memory-PR auto-approval, plan-gate verbs); `grammar.Agreement` gains `joint_verified` so nothing trusts a hand-written `[joint]` comment. Bootstrap (`setup`, `doctor`), the fuller skills story, and scratch sweeps are plan 5 (Task 11 records them in spec §21).

**Tech Stack:** Python 3.13, standard library + click, pytest (`pythonpath = ["src"]`), `uv run`. Real git repos and real subprocesses in tests; `FakeGh` for GitHub; an injected `post` callable for the webhook.

**Spec:** `docs/superpowers/specs/2026-09-03-meld-design.md` (sections 7, 8, 12, 14, 15, 17, 21 bind this plan). Where the spec and this plan differ, the spec wins unless a decision below says otherwise.

## Decisions made while writing this plan (surface to Scott; each is reversible)

1. **Lessons are auto-approved on evidence, not agreement.** Spec §15 says `meld remember` appends a lesson when a blocked, challenged, or reverted item resolves, and that decision *and* lesson PRs auto-approve when the content hash matches "the agreed comment". A lesson has no agreement round, so its auto-approval checks instead that the PR body's hash matches the appended entry, that the entry names an issue or PR on the board, and that the item is no longer blocked, needs-human, or challenged. Decisions keep the strict check: the file's decision text must equal the `[joint] DECISION` text of a verified agreement.
2. **`joint_verified` is the trust seam.** `Agreement.joint_verified` is true only when the `[joint]` comment was written by the proposer's bot after the round reached `agreed`, `majority`, or `disagree`. `decide`, `plan issues`, and `plan approve` refuse on an unverified joint comment; `status` exposes both flags.
3. **Memory PRs skip the worker review.** `meld dispatch --kind review` is refused on a memory PR ("run `meld review N` directly"); `meld review N` on a memory PR verifies the hash and evidence itself and posts `APPROVE` (the author group is still excluded by the existing rule, so at N=2 the other group's PM approves and either PM merges).
4. **The PLAN text has a machine-readable item format.** Orchestrators already post the plan as bullets; `meld plan issues` parses `- id: <slug> | title: <text> | worker: <w> | model: <m> | effort: <e> | size: <S|M|L>` lines with indented `  - <criterion>` lines beneath. Items with a malformed line are reported and skipped, never guessed.
5. **Chat is a thin REPL over existing verbs.** `meld chat` reads lines; plain text posts `[human] ASK ask-<n>`; slash commands map to human verbs (`/directive`, `/halt`, `/resume`, `/revert`, `/provided`, `/resolved`, `/ack`, `/approve`, `/challenge …`); `/inbox` re-prints the inbox; `/wait` blocks until the board changes and prints new coordination comments (`--verbose` streams every comment, default only `[joint]` and `[human]`-addressed ones). Non-interactive `meld inbox` and `meld chat --once TEXT` exist for scripts and tests.
6. **Off-protocol detection is scoped to unknown actors.** Meld's own label changes are made by the same bot accounts the PMs use, so the sweep flags `labeled/unlabeled/assigned/unassigned` issue events whose actor is neither a configured bot nor a listed human, posting `PROTOCOL-VIOLATION` once per event.
7. **Ignored comments are flagged by one PM.** Every PM tracks a cursor, but only the PM of the alphabetically first configured group posts the `IGNORED-COMMENT` comment, so a stranger's comment is flagged once, not N times.
8. **CI red is handled by the author group's PM.** In `tick`, a red PR whose linked issue is `in_review` and whose author party is the caller's group moves the issue back to `in_progress` with a comment, once per head; the worker's next build run is the fix.
9. **Notifications are per party and throttled.** The Slack DM webhook (`[notify] slack_dm_webhook_env`) is read from the environment at call time; `tick` sends one message per needs-human issue and repeats after `repeat_after_minutes`; the throttle lives in `$MELD_HOME/state/<group>/pm/notify.json`. `meld notify --dry-run` prints what would be sent.
10. **The digest carries what the loops watch.** `board_digest` adds challenge details, audits, and PR statuses; `diff_changes` emits `challenges` on any challenger change, a new `audits` kind, and `pr` when statuses change; `wait --filter` accepts `audits`.
11. **`pr_for_issue` prefers the merged PR for a closed issue.** For an open issue the open PR still wins.

## Global Constraints

- Python 3.13 (`.python-version`); runtime dependency click only; `uv run pytest -q` from the repo root. Baseline: 335 passed, no warnings; `uv run pytest tests/smoke -q -rs` → 2 skipped (never set `MELD_SMOKE=1`).
- `github.py` is the only GitHub path; `gitops.py` the only git runner; `rules.py` the only legality authority (every new refusal is a `Refusal` with a `Verb`); rule-level refusals go through `actions.refuse`, usage errors are `DispatchError`/`PostError`; every new CLI command uses `_load` + `_run_action` (or the `post`-style inline handling when `PostError` is possible) and `_guard` covers every exception it can raise.
- Workers never receive credentials; memory PRs are committed and pushed PM-side with the group's bot identity through the environment (`GIT_AUTHOR_*`/`GIT_COMMITTER_*`) and `push_branch` (token via the inline credential helper). Anything worker- or stranger-authored that reaches GitHub goes through `neutralise_closers`.
- Framework-posted verbs stay framework-posted (`LEASE`, `EXPIRED`, `IGNORED-COMMENT`, `[joint]`); `PROTOCOL-VIOLATION` posts from housekeeping use `compose` directly with the PM party.
- Tests are hermetic: no network (the webhook `post` callable is injected), no real CLI, no host `~/.claude`/gitconfig/wall clock (inject `now`); every `load_context` in tests passes `adapter_for=`.
- Plain conventional commits, no attribution trailers. Existing file layouts under `$MELD_HOME` are unchanged; new per-party state files live in `ctx.store.state_dir`.

## File structure

| File | Responsibility (this plan) |
|---|---|
| `src/meld/github.py` | + `create_issue`, `update_milestone`, `get_contents`, `issue_events`, `list_labels`, `create_label`, `search_issues` |
| `src/meld/grammar.py` | `Agreement.joint_verified`, `joint_verb`, `joint_author` |
| `src/meld/board.py`, `state.py` | `pr_for_issue` closed-issue preference; digest with challenges/audits/statuses; `audits` change kind |
| `src/meld/memory.py` (new) | ADR/lesson rendering, hashes, `decide`, `remember`, `recall`, `recall_snippets` |
| `src/meld/rules.py` | `Verb.DECIDE/REMEMBER/PLAN`, `memory_pr` detection, `memory_review_rule`, `_review` refusal on memory PRs, `plan_rule` |
| `src/meld/review.py` | memory-PR auto-approval path |
| `src/meld/plan.py` (new) | `parse_plan_items`, `plan_issues`, `plan_approve`, `plan_ack` |
| `src/meld/chat.py` (new) | `inbox`, `next_ask_id`, `run_chat` REPL, slash commands |
| `src/meld/notifier.py` (new) | `notify`, throttle state, Slack payload |
| `src/meld/housekeeping.py` (new) | `ci_red_sweep`, `flag_ignored_comments`, `off_protocol_sweep` |
| `src/meld/dispatch.py` | `tick` calls housekeeping + notifier; briefs get `## Relevant memory`; collect reports `skills_used` |
| `src/meld/brief.py` | `WorkerResult.skills_used`, `recall_snippets` section |
| `src/meld/prompts/*` | orchestrator/pm/preamble/worker contract updates; `COMMANDS` |
| `src/meld/cli.py` | `decide`, `remember`, `recall`, `plan`, `inbox`, `chat`, `notify` |
| docs | spec §7/§8/§12/§14/§15/§17/§21, README, `docs/HANDOFF.md` |
| tests | `test_github.py`, `test_grammar.py`/`test_agreement.py`, `test_board.py`, `test_state.py`, `test_memory.py` (new), `test_rules_prs.py`, `test_review.py`, `test_plan.py` (new), `test_chat.py` (new), `test_notifier.py` (new), `test_housekeeping.py` (new), `test_tick.py`, `test_brief.py`, `test_collect.py`, `test_role_prompts.py`, `test_prompts.py`, `test_status.py` |

---

### Task 1: GitHub endpoints for issues, milestones, contents, events, labels, and search

**Files:**
- Modify: `src/meld/github.py`
- Test: `tests/test_github.py`

**Interfaces:**
- Consumes: `GitHub._r`, `get`, `get_paginated`, `post`, `patch`, `board1_gh` fixture, `FakeGh.route`.
- Produces: `create_issue(title, body, labels, milestone) -> dict`; `update_milestone(number, **fields) -> dict`; `get_contents(path, ref) -> str | None` (decoded UTF-8 text; `None` on 404); `issue_events(number) -> list[dict]`; `list_labels() -> list[dict]`; `create_label(name, color, description="") -> dict`; `search_issues(query) -> list[dict]` (`GET search/issues`, returns `items`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_github.py`:

```python
import base64


def test_issue_milestone_contents_events_labels_and_search_endpoints(board1_gh):
    fake, gh = board1_gh
    fake.route("POST", "repos/acme/widgets/issues", {"number": 31, "html_url": "https://github.com/acme/widgets/issues/31"})
    fake.route("PATCH", "repos/acme/widgets/milestones/3", {"number": 3, "description": "plan text"})
    fake.route("GET", "repos/acme/widgets/contents/.meld/decisions/0001-x.md",
               {"content": base64.b64encode("# Decision x\n".encode()).decode(), "encoding": "base64"})
    fake.route("GET", "repos/acme/widgets/contents/.meld/missing.md", {"message": "Not Found"}, status=404)
    fake.route("GET", "repos/acme/widgets/issues/13/events", [{"id": 9001, "event": "labeled", "actor": {"login": "randouser"}, "label": {"name": "meld:ready"}}])
    fake.route("GET", "repos/acme/widgets/labels", [{"name": "meld:ready", "color": "0e8a16"}])
    fake.route("POST", "repos/acme/widgets/labels", {"name": "meld:planned", "color": "cccccc"})
    fake.route("GET", "search/issues", {"total_count": 1, "items": [{"number": 10, "title": "Coordination: M1", "html_url": "u10"}]})
    assert gh.create_issue("Login form", "body", ["meld:planned", "worker:frontend"], 3)["number"] == 31
    assert gh.update_milestone(3, description="plan text")["description"] == "plan text"
    assert gh.get_contents(".meld/decisions/0001-x.md", "abc123") == "# Decision x\n"
    assert gh.get_contents(".meld/missing.md", "abc123") is None
    assert gh.issue_events(13)[0]["actor"]["login"] == "randouser"
    assert gh.list_labels()[0]["name"] == "meld:ready"
    assert gh.create_label("meld:planned", "cccccc", "Created by the plan, not yet approved")["name"] == "meld:planned"
    assert gh.search_issues("repo:acme/widgets label:meld:coordination oauth")[0]["number"] == 10
    bodies = {(c.method, c.endpoint): c.body for c in fake.calls if c.method != "GET"}
    assert bodies[("POST", "repos/acme/widgets/issues")] == {"title": "Login form", "body": "body", "labels": ["meld:planned", "worker:frontend"], "milestone": 3}
    assert bodies[("PATCH", "repos/acme/widgets/milestones/3")] == {"description": "plan text"}
    assert bodies[("POST", "repos/acme/widgets/labels")] == {"name": "meld:planned", "color": "cccccc", "description": "Created by the plan, not yet approved"}
    contents_call = [c for c in fake.calls if c.endpoint == "repos/acme/widgets/contents/.meld/decisions/0001-x.md"][0]
    assert contents_call.query.get("ref") == "abc123"
    search_call = [c for c in fake.calls if c.endpoint == "search/issues"][0]
    assert search_call.query.get("q") == "repo:acme/widgets label:meld:coordination oauth"
```

(`Call.query` holds the parsed query parameters in `tests/fakegh.py`; if its shape differs, adapt the two `query` assertions to the fake's real shape and say so in the report.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_github.py -v`
Expected: FAIL with `AttributeError: 'GitHub' object has no attribute 'create_issue'`.

- [ ] **Step 3: Implement**

Append to `GitHub` in `src/meld/github.py` (add `import base64` at the top):

```python
    def create_issue(self, title: str, body: str, labels: list[str], milestone: int) -> dict:
        return self.post(self._r("issues"), {"title": title, "body": body, "labels": labels, "milestone": milestone})

    def update_milestone(self, number: int, **fields: Any) -> dict:
        return self.patch(self._r(f"milestones/{number}"), fields)

    def get_contents(self, path: str, ref: str) -> str | None:
        """The decoded text of a file at `ref`, or None when GitHub has no such file."""
        status, _, resp = self._call("GET", self._r(f"contents/{path}") + "?" + urlencode({"ref": ref}))
        if status == 404:
            return None
        if status >= 400:
            raise GitHubError(f"GET contents/{path}@{ref} failed with HTTP {status}: {resp}", status)
        if isinstance(resp, dict) and resp.get("encoding") == "base64":
            return base64.b64decode(resp.get("content", "")).decode("utf-8")
        raise GitHubError(f"GET contents/{path}@{ref}: unexpected payload {type(resp).__name__}")

    def issue_events(self, number: int) -> list[dict]:
        return self.get_paginated(self._r(f"issues/{number}/events"))

    def list_labels(self) -> list[dict]:
        return self.get_paginated(self._r("labels"))

    def create_label(self, name: str, color: str, description: str = "") -> dict:
        return self.post(self._r("labels"), {"name": name, "color": color, "description": description})

    def search_issues(self, query: str) -> list[dict]:
        resp = self.get("search/issues", {"q": query}, use_cache=False)
        return list((resp or {}).get("items", []))
```

Check how `_call` builds the endpoint with query parameters elsewhere in the class (`get` appends `urlencode(params)`); reuse the same helper so the FakeGh route matches on the path and the query lands in `Call.query`. If `get` already supports `params` and exposes the status through an exception, implement `get_contents` with `get(..., params={"ref": ref})` inside a `try/except GitHubError as e: if e.status == 404: return None; raise` instead of a raw `_call` — pick whichever keeps `Call.query` populated.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_github.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/meld/github.py tests/test_github.py
git commit -m "feat: GitHub endpoints for issue creation, milestone description, file contents, issue events, labels, and search"
```

---

### Task 2: Verified joint comments, a smarter `pr_for_issue`, and a digest that carries challenges, audits, and statuses

**Files:**
- Modify: `src/meld/grammar.py`, `src/meld/board.py`, `src/meld/state.py`, `src/meld/status.py`, `src/meld/cli.py` (the `--filter` help text)
- Test: `tests/test_agreement.py`, `tests/test_board.py`, `tests/test_state.py`, `tests/test_status.py`

**Interfaces:**
- Consumes: `grammar.agreement_for`, `ParsedComment.author`, `cfg.groups[g].bot`, `Board.challenges/audits`, `PullRequest.statuses/merged`.
- Produces: `Agreement.joint_verified: bool`, `Agreement.joint_verb: str | None`, `Agreement.joint_author: str | None` (the `[joint]` comment is verified when its author is the proposer's bot and, at that point in the thread, the round's status was `agreed`, `majority`, or `disagree`); `status["coordination"]["agreements"]` becomes `{ref: {"status": ..., "joint_verified": ...}}`; `Board.pr_for_issue` prefers a merged PR when the issue is closed; `board_digest` gains `"challenges": {ref: challenger}`, `"audits": {pr: {group: verdict}}`, and `"statuses"` inside each pull entry; `CHANGE_KINDS` gains `"audits"`; `diff_changes` emits `challenges` on any change of the challenges map, `audits` on any change of the audits map, and `pr` when a PR's statuses change.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agreement.py` (it already imports `agreement_for`, `mk_comment`, `CFG2`):

```python
def test_joint_comment_is_verified_only_from_the_proposers_bot_after_the_round_closed():
    thread = [mk_comment(CFG2, 1, "meld-claude-bot", "[claude] PROPOSE plan-1\n\nsplit the work"),
              mk_comment(CFG2, 2, "meld-codex-bot", "[codex] AGREE plan-1\n\nyes")]
    good = thread + [mk_comment(CFG2, 3, "meld-claude-bot", "[joint] PLAN plan-1\n\nsplit the work\n\nverified-by: meld roster")]
    a = agreement_for(good, "plan-1", CFG2)
    assert a.joint_posted and a.joint_verified and a.joint_verb == "PLAN" and a.joint_author == "meld-claude-bot"
    forged = thread + [mk_comment(CFG2, 3, "meld-codex-bot", "[joint] PLAN plan-1\n\nsplit the work\n\nverified-by: x")]
    b = agreement_for(forged, "plan-1", CFG2)
    assert b.joint_posted and not b.joint_verified and b.joint_author == "meld-codex-bot"
    early = [thread[0], mk_comment(CFG2, 2, "meld-claude-bot", "[joint] PLAN plan-1\n\nsplit the work\n\nverified-by: x")]
    c = agreement_for(early, "plan-1", CFG2)
    assert c.joint_posted and not c.joint_verified       # posted while the round was still open
```

Append to `tests/test_board.py`:

```python
def test_pr_for_issue_prefers_the_merged_pr_once_the_issue_is_closed():
    open_pr = mk_pr(22, 11, state="open")
    merged = mk_pr(21, 11, state="closed", merged=True)
    assert mk_board(CFG2, issues=[mk_issue(11)], pulls=[open_pr, merged]).pr_for_issue(11).number == 22
    closed = mk_issue(11, state="closed")
    assert mk_board(CFG2, issues=[closed], pulls=[open_pr, merged]).pr_for_issue(11).number == 21
```

Append to `tests/test_state.py` (it imports `board_digest`, `diff_changes`, `mk_board`, `mk_pr`, `mk_comment`, `CFG2`; add `Challenge` and `replace`):

```python
def test_digest_and_diff_carry_challenges_audits_and_statuses():
    pr = replace(mk_pr(20, 13), statuses={"meld/review/codex": "pending"})
    b1 = replace(mk_board(CFG2, pulls=[pr]), challenges={"#20": Challenge("#20", "claude", 500, "t")}, open_challenges=frozenset({"#20"}))
    d1 = board_digest(b1)
    assert d1["challenges"] == {"#20": "claude"} and d1["audits"] == {} and d1["pulls"]["20"]["statuses"] == {"meld/review/codex": "pending"}
    b2 = replace(b1, challenges={"#20": Challenge("#20", "codex", 501, "t")},
                 pulls={20: replace(pr, statuses={"meld/review/codex": "success"})}, audits={19: {"codex": "pass"}})
    kinds = sorted({c.kind for c in diff_changes(d1, board_digest(b2))})
    assert kinds == ["audits", "challenges", "pr"]
    texts = [c.text for c in diff_changes(d1, board_digest(b2))]
    assert any("challenger" in t for t in texts) and any("statuses" in t for t in texts) and any("audit" in t for t in texts)
    assert "audits" in CHANGE_KINDS
```

Append to `tests/test_status.py`:

```python
def test_status_agreements_report_verification():
    thread = [mk_comment(CFG2, 1, "meld-claude-bot", "[claude] PROPOSE plan-1\n\nsplit"),
              mk_comment(CFG2, 2, "meld-codex-bot", "[codex] AGREE plan-1\n\nyes"),
              mk_comment(CFG2, 3, "meld-codex-bot", "[joint] PLAN plan-1\n\nsplit\n\nverified-by: x")]
    data = render_status(mk_board(CFG2, comments=thread), CFG2, CLAUDE_PM, REG, datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc))
    assert data["coordination"]["agreements"]["plan-1"] == {"status": "agreed", "joint_verified": False}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agreement.py tests/test_board.py tests/test_state.py tests/test_status.py -v`
Expected: FAIL (`Agreement` has no attribute `joint_verified`, digest keys missing).

- [ ] **Step 3: Implement**

`src/meld/grammar.py`: extend `Agreement` with `joint_verified: bool = False`, `joint_verb: str | None = None`, `joint_author: str | None = None` (defaults keep existing positional constructions valid). In `agreement_for`, replace the joint branch with:

```python
        if h.party.kind == "joint":
            joint_posted, joint_verb, joint_author = True, h.verb, cmt.author
            closed = text is not None and ((agreed >= set(cfg.group_names)) or ((agreed | set(dissent)) >= set(cfg.group_names)))
            joint_verified = closed and proposer is not None and cmt.author == cfg.groups[proposer].bot
            continue
```

(initialise `joint_verb = joint_author = None` and `joint_verified = False` before the loop; pass the three new fields to the `Agreement(...)` constructor). A later `AMEND` after a verified joint comment must not un-verify it; keep the flags once set unless a second joint comment arrives (the latest joint comment decides).

`src/meld/board.py::Board.pr_for_issue`:

```python
    def pr_for_issue(self, issue: int) -> PullRequest | None:
        candidates = [p for p in self.pulls.values() if p.issue_number == issue]
        issue_closed = issue in self.issues and self.issues[issue].state == "closed"
        # For an open issue the open PR is the live one; once the issue is closed, the PR that merged is the record.
        candidates.sort(key=(lambda p: (not p.merged, p.state != "open", -p.number)) if issue_closed else (lambda p: (p.state != "open", -p.number)))
        return candidates[0] if candidates else None
```

`src/meld/state.py`: in `board_digest`, add `"statuses": dict(sorted(p.statuses.items()))` to each pull entry, and top-level `"challenges": {ref: c.challenger for ref, c in sorted(board.challenges.items())}` and `"audits": {str(n): dict(sorted(v.items())) for n, v in sorted(board.audits.items())}`. `CHANGE_KINDS = ("snapshot", "phase", "halted", "issue", "pr", "run", "comments", "challenges", "audits")`. In `diff_changes`: inside the pulls loop add `if a.get("statuses", {}) != b.get("statuses", {}): out.append(Change("pr", ref, a.get("statuses"), b.get("statuses"), f"pr #{n}: statuses {a.get('statuses', {})} -> {b.get('statuses', {})}"))`; replace the existing `open_challenges` comparison with one over `challenges` that emits `Change("challenges", ref, old_challenger, new_challenger, f"challenge {ref}: challenger {old} -> {new}")` per ref whose challenger changed (None for absent) — keep emitting for opened/closed refs too; add an `audits` loop emitting `Change("audits", f"pr #{n}", a, b, f"audit pr #{n}: {a or {}} -> {b or {}}")` per PR whose map changed. Keep `open_challenges` in the digest for old snapshots' sake but derive changes from `challenges`.

`src/meld/status.py`: `agreements = {ref: {"status": ag.status, "joint_verified": ag.joint_verified} for ref, ag in ((r, agreement_for(board.comments, r, cfg)) for r in refs)}`. Update any test in `tests/test_status.py` that asserted the old string-valued agreements to the new dict shape.

`src/meld/cli.py`: the `wait --filter` help text lists `audits`. Spec §12 is updated in Task 11.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_agreement.py tests/test_board.py tests/test_state.py tests/test_status.py tests/test_wait.py tests/test_posting.py -v` then `uv run pytest -q`
Expected: PASS (fix any old-snapshot compatibility issue in `diff_changes` by treating missing keys as empty).

- [ ] **Step 5: Commit**

```bash
git add src/meld/grammar.py src/meld/board.py src/meld/state.py src/meld/status.py src/meld/cli.py tests/test_agreement.py tests/test_board.py tests/test_state.py tests/test_status.py
git commit -m "feat: verified joint comments, merged-PR preference for closed issues, and a digest that watches challenges, audits, and statuses"
```

---

### Task 3: `memory.py` — decisions and lessons as files, `meld decide`, `meld remember`, `meld recall`

**Files:**
- Create: `src/meld/memory.py`
- Modify: `src/meld/rules.py` (`Verb.DECIDE`, `Verb.REMEMBER`), `src/meld/cli.py`
- Test: `tests/test_memory.py` (new)

**Interfaces:**
- Consumes: `grammar.agreement_for` (with `joint_verified`/`joint_verb`, Task 2), `gitops.create_worktree/git/push_branch/head_sha/remove_worktree`, `dispatch.git_identity/neutralise_closers`, `GitHub.create_pull/create_comment/search_issues/get_contents`, `Board.comments/issues/pulls/coordination_issue`.
- Produces:
  - `memory.DECISIONS_DIR = ".meld/decisions"`, `LESSONS_FILE = ".meld/memory/lessons.md"`, `MARKER_RE` matching `meld-memory: (decision|lesson) (\S+) ([0-9a-f]{64})` in a PR body, `MEMORY_BRANCH_PREFIX = "meld/memory-"`.
  - `content_hash(text) -> str` (sha256 hex of the UTF-8 text).
  - `next_decision_number(repo_root) -> int` (max `NNNN` prefix under `.meld/decisions` + 1, else 1).
  - `render_decision(ref, agreement, url, date) -> str`: `# Decision <ref>\n\n- Date: <date>\n- Agreed by: <sorted agreed groups>\n- Source: <url>\n\n## Decision\n\n<agreement.text.strip()>\n`.
  - `render_lesson(ref, text, group, date) -> str`: `\n## <date> — <ref> (<group>)\n\n<text.strip()>\n`.
  - `decide(ctx, ref, *, now=None) -> dict`: orchestrator or PM of the proposer group; requires a verified `[joint] DECISION <ref>`; refuses (Verb.DECIDE) otherwise; writes `.meld/decisions/NNNN-<slug>.md` in a fresh worktree on branch `meld/memory-<slug>` from the default branch, commits as the bot, pushes, opens a PR titled `Decision <ref>` whose body is `Records the agreed decision \`<ref>\`.\n\nmeld-memory: decision <ref> <hash>\nverified-by: [joint] DECISION <ref> (<url>)`, posts `[<g>:pm] REVIEW-REQUEST #<pr>` on the coordination issue, removes the worktree, returns `{"decided": ref, "path", "pr", "hash"}`. Idempotent: if a memory PR for that ref is already open (body marker) or the file exists on the default branch, refuse with "already recorded".
  - `remember(ctx, ref, text, *, now=None) -> dict`: orchestrator or PM; `ref` must be an issue or PR on the board; refuses (Verb.REMEMBER) when the item is still `blocked`/`needs_human` or challenged ("lesson recorded only after the item resolves"); appends `render_lesson(...)` to `.meld/memory/lessons.md` (creating the file with `# Lessons\n` when absent), same branch/PR mechanics (`meld/memory-lesson-<n>-<stamp>`, title `Lesson from <ref>`, marker `meld-memory: lesson <ref> <hash of the entry>`), returns `{"remembered": ref, "pr", "hash"}`.
  - `recall(ctx, query, *, limit=10) -> list[dict]`: local hits from `.meld/decisions/*.md` and `.meld/memory/lessons.md` (score = number of query terms found, case-insensitive; each hit `{"kind": "decision"|"lesson", "path", "title", "score", "snippet"}`), plus coordination issues from `gh.search_issues(f"repo:{repo} label:meld:coordination {query}")` (`{"kind": "coordination", "number", "title", "url", "score": 1}`); sorted by score desc then title; `limit` applied.
  - `recall_snippets(repo_root, text, limit=3) -> list[str]`: the local part of `recall` without a context, used by briefs in Task 9.
  - CLI: `meld decide REF`, `meld remember REF -m TEXT`, `meld recall QUERY [--limit N]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_memory.py`:

```python
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from meld.actions import Refused
from meld.memory import (DECISIONS_DIR, LESSONS_FILE, MARKER_RE, content_hash, decide, next_decision_number, recall, recall_snippets,
                         remember, render_decision, render_lesson)
from tests.gitfixtures import sh
from tests.productfixtures import R, fixture, make_product

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
DECISION_THREAD = [
    {"id": 201, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T10:10:00Z", "body": "[claude] PROPOSE dec-1\n\nUse PostgreSQL for the ledger.\n\nverified-by: read docs/adr/0001.md"},
    {"id": 202, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T10:11:00Z", "body": "[codex] AGREE dec-1\n\nfine"},
    {"id": 203, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T10:12:00Z", "html_url": "https://github.com/acme/widgets/issues/10#issuecomment-203",
     "body": "[joint] DECISION dec-1\n\nUse PostgreSQL for the ledger.\n\nverified-by: read docs/adr/0001.md"},
]


def with_thread(fake, extra):
    fake.route("GET", R + "issues/10/comments", fixture("comments_10.json") + extra)


def test_render_and_hash_are_deterministic():
    from meld.grammar import Agreement
    a = Agreement("dec-1", "Use PostgreSQL for the ledger.\n\nverified-by: read docs/adr/0001.md", "claude", frozenset({"claude", "codex"}), {}, 0, "agreed", True, True, "DECISION", "meld-claude-bot")
    text = render_decision("dec-1", a, "https://x/issues/10#issuecomment-203", "2026-09-03")
    assert text.startswith("# Decision dec-1\n\n- Date: 2026-09-03\n- Agreed by: claude, codex\n- Source: https://x/issues/10#issuecomment-203\n\n## Decision\n\nUse PostgreSQL")
    assert content_hash(text) == content_hash(text) and len(content_hash(text)) == 64
    assert render_lesson("#13", "CI silently passed; pin the action version.", "claude", "2026-09-03").startswith("\n## 2026-09-03 — #13 (claude)\n\nCI silently passed")


def test_next_decision_number(tmp_path):
    assert next_decision_number(tmp_path) == 1
    (tmp_path / ".meld" / "decisions").mkdir(parents=True)
    (tmp_path / ".meld" / "decisions" / "0007-old.md").write_text("# Decision old\n")
    assert next_decision_number(tmp_path) == 8


def test_decide_opens_a_memory_pr_and_requests_review(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    with_thread(fake, DECISION_THREAD)
    fake.route("POST", R + "pulls", {"number": 40, "html_url": "https://github.com/acme/widgets/pull/40"})
    out = decide(ctx, "dec-1", now=NOW)
    assert out["decided"] == "dec-1" and out["pr"] == 40 and out["path"] == ".meld/decisions/0001-dec-1.md"
    pull = [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "pulls"][0].body
    assert pull["title"] == "Decision dec-1" and pull["head"] == "meld/memory-dec-1" and pull["base"] == "main"
    m = MARKER_RE.search(pull["body"])
    assert m and m.group(1) == "decision" and m.group(2) == "dec-1" and m.group(3) == out["hash"]
    assert "verified-by: [joint] DECISION dec-1" in pull["body"]
    text = sh(["git", "show", "origin/meld/memory-dec-1:.meld/decisions/0001-dec-1.md"], repo)
    assert text.startswith("# Decision dec-1") and "Use PostgreSQL for the ledger." in text and content_hash(text + "\n") == out["hash"] or content_hash(text) == out["hash"]
    assert sh(["git", "log", "-1", "--format=%an", "origin/meld/memory-dec-1"], repo) == "meld-claude-bot"
    coord = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"][-1]
    assert coord.startswith("[claude:pm] REVIEW-REQUEST #40\n")
    assert not (tmp_path / "home" / "worktrees" / "claude" / "memory-dec-1").exists()   # scratch worktree removed


def test_decide_refusals(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    with pytest.raises(Refused, match="no verified \\[joint\\] DECISION"):
        decide(ctx, "dec-9", now=NOW)
    forged = DECISION_THREAD[:2] + [{**DECISION_THREAD[2], "user": {"login": "meld-codex-bot"}}]
    with_thread(fake, forged)
    with pytest.raises(Refused, match="not verified"):
        decide(ctx, "dec-1", now=NOW)
    fake2, codex, *_ = make_product(tmp_path / "c", group="codex", role="orchestrator")
    with_thread(fake2, DECISION_THREAD)
    with pytest.raises(Refused, match="proposer"):
        decide(codex, "dec-1", now=NOW)
    fake3, human, *_ = make_product(tmp_path / "h", group=None)
    with_thread(fake3, DECISION_THREAD)
    with pytest.raises(Refused, match="orchestrator or PM"):
        decide(human, "dec-1", now=NOW)


def test_remember_appends_a_lesson_and_refuses_unresolved_items(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    fake.route("POST", R + "pulls", {"number": 41, "html_url": "https://github.com/acme/widgets/pull/41"})
    with pytest.raises(Refused, match="still needs_human"):
        remember(ctx, "#14", "we needed the key earlier", now=NOW)
    out = remember(ctx, "#13", "CI silently passed; pin the action version.", now=NOW)
    assert out["remembered"] == "#13" and out["pr"] == 41
    pull = [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "pulls"][0].body
    assert pull["title"] == "Lesson from #13" and MARKER_RE.search(pull["body"]).group(1) == "lesson"
    text = sh(["git", "show", f"origin/{pull['head']}:.meld/memory/lessons.md"], repo)
    assert text.startswith("# Lessons\n") and "pin the action version" in text


def test_recall_ranks_local_files_and_coordination_issues(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    (repo / ".meld" / "decisions").mkdir(parents=True)
    (repo / ".meld" / "decisions" / "0001-dec-1.md").write_text("# Decision dec-1\n\n## Decision\n\nUse PostgreSQL for the ledger.\n")
    (repo / ".meld" / "memory").mkdir()
    (repo / ".meld" / "memory" / "lessons.md").write_text("# Lessons\n\n## 2026-09-01 — #7 (codex)\n\nPostgreSQL migrations need a lock timeout.\n")
    fake.route("GET", "search/issues", {"items": [{"number": 5, "title": "Coordination: M0 ledger", "html_url": "u5"}]})
    hits = recall(ctx, "postgresql ledger")
    assert [h["kind"] for h in hits] == ["decision", "lesson", "coordination"]
    assert hits[0]["score"] == 2 and hits[0]["path"].endswith("0001-dec-1.md") and hits[2]["number"] == 5
    assert recall_snippets(repo, "ledger migrations") and "lock timeout" in recall_snippets(repo, "ledger migrations")[0]
    assert recall_snippets(repo, "nothing matches here") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_memory.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.memory'`.

- [ ] **Step 3: Implement**

`src/meld/rules.py`: add `DECIDE = "decide"` and `REMEMBER = "remember"` to `Verb` (not in `ISSUE_VERBS`/`PR_VERBS`; they are ref-level like `CHALLENGE`).

`src/meld/memory.py`:

```python
"""Decision memory: agreed decisions and lessons become versioned files through PRs; `recall` finds them again."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from meld.actions import refuse
from meld.config import Party
from meld.dispatch import DispatchError, _iso, _now, git_identity, neutralise_closers
from meld.gitops import create_worktree, git, push_branch, remove_worktree, slugify
from meld.grammar import Agreement, Header, agreement_for, compose
from meld.rules import Refusal, Verb
from meld.status import build_status

DECISIONS_DIR = ".meld/decisions"
LESSONS_FILE = ".meld/memory/lessons.md"
MEMORY_BRANCH_PREFIX = "meld/memory-"
MARKER_RE = re.compile(r"^meld-memory: (decision|lesson) (\S+) ([0-9a-f]{64})$", re.MULTILINE)
_NUMBER_RE = re.compile(r"^(\d{4})-")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def next_decision_number(repo_root: Path) -> int:
    d = repo_root / DECISIONS_DIR
    numbers = [int(m.group(1)) for f in d.glob("*.md") if (m := _NUMBER_RE.match(f.name))] if d.is_dir() else []
    return max(numbers, default=0) + 1


def render_decision(ref: str, agreement: Agreement, url: str, date: str) -> str:
    agreed = ", ".join(sorted(agreement.agreed))
    return f"# Decision {ref}\n\n- Date: {date}\n- Agreed by: {agreed}\n- Source: {url}\n\n## Decision\n\n{(agreement.text or '').strip()}\n"


def render_lesson(ref: str, text: str, group: str, date: str) -> str:
    return f"\n## {date} — {ref} ({group})\n\n{text.strip()}\n"


def _comment_url(board, comment_id: int) -> str:
    return f"https://github.com/{board.repo}/issues/{board.coordination_issue}#issuecomment-{comment_id}"


def _memory_pr_open(board, kind: str, ref: str) -> bool:
    return any(m and m.group(1) == kind and m.group(2) == ref for m in (MARKER_RE.search(p.body) for p in board.pulls.values() if p.state == "open"))


def _open_memory_pr(ctx, board, group, *, branch: str, path: str, text: str, mode: str, title: str, body: str) -> tuple[int, str]:
    """Write `text` at `path` (mode 'w' or 'a') on a fresh branch from the default branch, commit as the bot, push, open the PR."""
    wt = ctx.store.home / "worktrees" / group.name / branch.removeprefix("meld/")
    if wt.exists():
        remove_worktree(ctx.repo_root, wt)
    create_worktree(ctx.repo_root, wt, branch, ctx.cfg.default_branch)
    target = wt / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode == "a" and target.exists():
        target.write_text(target.read_text() + text)
    elif mode == "a":
        target.write_text("# Lessons\n" + text)
    else:
        target.write_text(text)
    name, email = git_identity(ctx, group)
    env = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}
    git(["add", path], wt)
    git(["commit", "-q", "-m", title.lower().replace("decision ", "docs: record decision ").replace("lesson from ", "docs: record lesson from ")], wt, env=env)
    push_branch(wt, branch, ctx.gh.token)
    pr = ctx.gh.create_pull(title, branch, ctx.cfg.default_branch, body)
    remove_worktree(ctx.repo_root, wt)
    return int(pr["number"]), str(pr.get("html_url", ""))


def _request_review(ctx, board, group, pr_number: int, what: str) -> None:
    ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "REVIEW-REQUEST", f"#{pr_number}", None),
                                                            f"{what}; run `meld review {pr_number}` (memory PR: hash-verified, no worker needed)."))


def decide(ctx, ref: str, *, now: datetime | None = None) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    if actor.party.kind not in ("group", "pm"):
        refuse(ctx, board, Refusal(Verb.DECIDE, ref, "only the proposer group's orchestrator or PM may record a decision"))
    agreement = agreement_for(board.comments, ref, ctx.cfg)
    if not agreement.joint_posted or agreement.joint_verb != "DECISION":
        refuse(ctx, board, Refusal(Verb.DECIDE, ref, f"no verified [joint] DECISION {ref} on the coordination issue"))
    if not agreement.joint_verified:
        refuse(ctx, board, Refusal(Verb.DECIDE, ref, f"the [joint] DECISION {ref} comment is not verified (wrong author or posted before the round closed)"))
    if agreement.proposer != actor.group:
        refuse(ctx, board, Refusal(Verb.DECIDE, ref, f"only the proposer ({agreement.proposer}) may record {ref}"))
    slug = slugify(ref)
    if _memory_pr_open(board, "decision", ref) or any(f.name.endswith(f"-{slug}.md") for f in (ctx.repo_root / DECISIONS_DIR).glob("*.md")) if (ctx.repo_root / DECISIONS_DIR).is_dir() else _memory_pr_open(board, "decision", ref):
        refuse(ctx, board, Refusal(Verb.DECIDE, ref, f"{ref} is already recorded or has an open memory PR"))
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    when = _now(now)
    joint = next(c for c in board.comments if c.ok and c.header and c.header.party.kind == "joint" and c.header.ref == ref)
    url = _comment_url(board, joint.id)
    text = render_decision(ref, agreement, url, when.strftime("%Y-%m-%d"))
    digest = content_hash(text)
    path = f"{DECISIONS_DIR}/{next_decision_number(ctx.repo_root):04d}-{slug}.md"
    body = f"Records the agreed decision `{ref}`.\n\nmeld-memory: decision {ref} {digest}\nverified-by: [joint] DECISION {ref} ({url})"
    pr_number, _ = _open_memory_pr(ctx, board, group, branch=f"{MEMORY_BRANCH_PREFIX}{slug}", path=path, text=text, mode="w",
                                   title=f"Decision {ref}", body=body)
    _request_review(ctx, board, group, pr_number, f"Decision {ref} recorded as {path}")
    return {"decided": ref, "path": path, "pr": pr_number, "hash": digest}


def remember(ctx, ref: str, text: str, *, now: datetime | None = None) -> dict:
    if not text.strip():
        raise DispatchError("a lesson needs text: what went wrong, what fixed it, what to check next time")
    _, board = build_status(ctx)
    actor = ctx.actor()
    if actor.party.kind not in ("group", "pm"):
        refuse(ctx, board, Refusal(Verb.REMEMBER, ref, "only an orchestrator or PM may record a lesson"))
    n = int(ref[1:]) if ref.startswith("#") and ref[1:].isdigit() else None
    issue = board.issues.get(n) if n is not None else None
    pr = board.pulls.get(n) if n is not None else None
    if issue is None and pr is None:
        refuse(ctx, board, Refusal(Verb.REMEMBER, ref, f"{ref} is not an issue or PR on the board"))
    if issue is not None and issue.status in ("blocked", "needs_human"):
        refuse(ctx, board, Refusal(Verb.REMEMBER, ref, f"{ref} is still {issue.status}; a lesson is recorded once the item resolves"))
    if board.is_challenged(ref):
        refuse(ctx, board, Refusal(Verb.REMEMBER, ref, f"{ref} has an open CHALLENGE; resolve it first"))
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    when = _now(now)
    entry = render_lesson(ref, neutralise_closers(text), group.name, when.strftime("%Y-%m-%d"))
    digest = content_hash(entry)
    stamp = when.strftime("%Y%m%dT%H%M%SZ")
    body = f"Records a lesson from {ref}.\n\nmeld-memory: lesson {ref} {digest}\nverified-by: {ref} resolved on the board at {_iso(when)}"
    pr_number, _ = _open_memory_pr(ctx, board, group, branch=f"{MEMORY_BRANCH_PREFIX}lesson-{n}-{stamp}", path=LESSONS_FILE, text=entry, mode="a",
                                   title=f"Lesson from {ref}", body=body)
    _request_review(ctx, board, group, pr_number, f"Lesson from {ref} appended to {LESSONS_FILE}")
    return {"remembered": ref, "pr": pr_number, "hash": digest}


def _terms(query: str) -> list[str]:
    return [t for t in re.split(r"\W+", query.lower()) if len(t) > 2]


def _local_hits(repo_root: Path, terms: list[str]) -> list[dict]:
    hits: list[dict] = []
    d = repo_root / DECISIONS_DIR
    for f in sorted(d.glob("*.md")) if d.is_dir() else []:
        text = f.read_text()
        score = sum(1 for t in terms if t in text.lower())
        if score:
            title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), f.stem)
            hits.append({"kind": "decision", "path": str(f.relative_to(repo_root)), "title": title, "score": score, "snippet": text.strip()[:300]})
    lessons = repo_root / LESSONS_FILE
    if lessons.is_file():
        for block in re.split(r"\n(?=## )", lessons.read_text()):
            if not block.startswith("## "):
                continue
            score = sum(1 for t in terms if t in block.lower())
            if score:
                hits.append({"kind": "lesson", "path": LESSONS_FILE, "title": block.splitlines()[0][3:].strip(), "score": score, "snippet": block.strip()[:300]})
    return hits


def recall_snippets(repo_root: Path, text: str, limit: int = 3) -> list[str]:
    hits = sorted(_local_hits(repo_root, _terms(text)), key=lambda h: (-h["score"], h["title"]))
    return [h["snippet"] for h in hits[:limit]]


def recall(ctx, query: str, *, limit: int = 10) -> list[dict]:
    terms = _terms(query)
    hits = _local_hits(ctx.repo_root, terms)
    for item in ctx.gh.search_issues(f"repo:{ctx.cfg.repo_name} label:meld:coordination {query}"):
        hits.append({"kind": "coordination", "number": item.get("number"), "title": item.get("title", ""), "url": item.get("html_url", ""), "score": 1})
    hits.sort(key=lambda h: (-h["score"], h["title"]))
    return hits[:limit]
```

Fix the awkward "already recorded" condition into a readable helper `_already_recorded(ctx, board, ref, slug) -> bool` (open memory PR for the ref, or a `*-<slug>.md` under the decisions dir). `gitops.git` must accept an `env` argument (it does: `git(args, cwd, run, env)`); confirm `create_worktree` returns for a brand-new branch (mode "new") and that `push_branch` works from an attached worktree.

`src/meld/cli.py`: import `from meld.memory import decide as do_decide, recall as do_recall, remember as do_remember` and add:

```python
@main.command()
@click.argument("ref")
@click.pass_context
def decide(ctx: click.Context, ref: str) -> None:
    """Record a verified [joint] DECISION as .meld/decisions/NNNN-<slug>.md through a memory PR."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_decide(c, ref))


@main.command()
@click.argument("ref")
@click.option("-m", "--message", "message", required=True, help="What went wrong, what fixed it, what to check next time.")
@click.pass_context
def remember(ctx: click.Context, ref: str, message: str) -> None:
    """Append a lesson about a resolved issue or PR to .meld/memory/lessons.md through a memory PR."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_remember(c, ref, message))


@main.command()
@click.argument("query")
@click.option("--limit", type=int, default=10, show_default=True)
@click.pass_context
def recall(ctx: click.Context, query: str, limit: int) -> None:
    """Search decisions, lessons, and past coordination issues."""
    c = _load(ctx)
    emit(_guard(lambda: do_recall(c, query, limit=limit)), ctx.obj["pretty"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_memory.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/meld/memory.py src/meld/rules.py src/meld/cli.py tests/test_memory.py
git commit -m "feat: meld decide, remember, and recall turn agreed decisions and lessons into versioned memory"
```

---

### Task 4: Memory PRs are hash-verified and auto-approved by `meld review`

**Files:**
- Modify: `src/meld/rules.py`, `src/meld/review.py`, `src/meld/dispatch.py` (nothing beyond what rules refuse), `src/meld/board.py` (`PullRequest.memory` property)
- Test: `tests/test_rules_prs.py`, `tests/test_review.py`

**Interfaces:**
- Consumes: `memory.MARKER_RE/content_hash/render_decision/DECISIONS_DIR/LESSONS_FILE`, `GitHub.get_contents`, `agreement_for`, `PullRequest.files/body/head_sha`, `rules.pr_refusal(APPROVE)`.
- Produces: `PullRequest.memory -> tuple[str, str, str] | None` (`(kind, ref, hash)` from the body marker); `rules._review` refuses dispatching a review on a memory PR ("memory PR: run `meld review N` directly, no worker needed"); `rules.memory_review_rule(board, cfg, actor, pr) -> str | None` (legality of the auto-approval = `_approve`'s rules); `review.verify_memory_pr(ctx, board, pr) -> tuple[bool, str]` (fetches the changed file at the head, checks the hash, and for decisions that the file's `## Decision` text equals the verified `[joint] DECISION` text; for lessons that the appended entry's hash matches and the ref is on the board and resolved); `review.review(ctx, n)` on a memory PR takes this path: verified → `create_review(APPROVE)` + `success` status + return `{"reviewed", "memory": kind, "event": "APPROVE"}`; not verified → `create_review(REQUEST_CHANGES)` with the reason + `failure` status.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_rules_prs.py`:

```python
def test_memory_pr_detection_and_review_dispatch_refusal():
    body = "Records the agreed decision `dec-1`.\n\nmeld-memory: decision dec-1 " + "a" * 64 + "\nverified-by: x"
    pr = replace(mk_pr(40, 0, committer="meld-claude-bot"), body=body, head_ref="meld/memory-dec-1")
    assert pr.memory == ("decision", "dec-1", "a" * 64) and mk_pr(20, 13).memory is None
    b = mk_board(CFG2, pulls=[pr])
    assert prule(b, CODEX_PM, Verb.REVIEW, pr) == "memory PR: run `meld review 40` directly, no worker needed"
    assert prule(b, CODEX_PM, Verb.APPROVE, pr) is None and "made the last commit" in prule(b, CLAUDE_PM, Verb.APPROVE, pr)
```

Append to `tests/test_review.py`:

```python
from meld.memory import content_hash, decide
from tests.test_memory import DECISION_THREAD, with_thread


def memory_pr_on_board(fake, repo, path, text, number=40, head="mem0001", kind="decision", ref="dec-1", digest=None):
    import base64
    pulls = fixture("pulls.json") + [{"number": number, "title": f"Decision {ref}", "state": "open", "merged_at": None,
                                      "user": {"login": "meld-claude-bot"}, "head": {"ref": f"meld/memory-{ref}", "sha": head}, "labels": [],
                                      "body": f"Records.\n\nmeld-memory: {kind} {ref} {digest or content_hash(text)}\nverified-by: x"}]
    fake.route("GET", R + "pulls", pulls)
    fake.route("GET", R + f"pulls/{number}/reviews", [])
    fake.route("GET", R + f"pulls/{number}/commits", [{"sha": head, "author": {"login": "meld-claude-bot"}, "committer": {"login": "meld-claude-bot"}}])
    fake.route("GET", R + f"pulls/{number}/files", [{"filename": path}])
    fake.route("GET", R + f"commits/{head}/check-runs", fixture("checkruns_abc123.json"))
    fake.route("GET", R + f"commits/{head}/status", {"state": "pending", "statuses": []})
    fake.route("GET", R + f"contents/{path}", {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()})
    fake.route("POST", R + f"pulls/{number}/reviews", {"id": 601, "html_url": "r601"})
    fake.route("POST", R + f"statuses/{head}", {"id": 79})


def test_review_auto_approves_a_verified_decision_pr(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, group="codex")          # codex reviews claude's memory PR
    with_thread(fake, DECISION_THREAD)
    from meld.grammar import agreement_for
    from meld.memory import render_decision
    board_comments = None
    text = render_decision("dec-1", agreement_for([__import__("meld.grammar", fromlist=["parse_comment"]).parse_comment(c, ctx.cfg) for c in fixture("comments_10.json") + DECISION_THREAD], "dec-1", ctx.cfg),
                           "https://github.com/acme/widgets/issues/10#issuecomment-203", "2026-09-03")
    memory_pr_on_board(fake, repo, ".meld/decisions/0001-dec-1.md", text)
    out = review(ctx, 40, now=NOW)
    assert out == {"reviewed": 40, "memory": "decision", "event": "APPROVE", "review_id": 601}
    (body,) = [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "pulls/40/reviews"]
    assert body["event"] == "APPROVE" and "hash" in body["body"]
    assert [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "statuses/mem0001"][0]["state"] == "success"


def test_review_requests_changes_on_a_tampered_memory_pr(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, group="codex")
    with_thread(fake, DECISION_THREAD)
    text = "# Decision dec-1\n\n## Decision\n\nUse MySQL instead.\n"
    memory_pr_on_board(fake, repo, ".meld/decisions/0001-dec-1.md", text)             # hash matches the file, text does not match the agreement
    out = review(ctx, 40, now=NOW)
    assert out["event"] == "REQUEST_CHANGES" and "does not match" in out["reason"]
    memory_pr_on_board(fake, repo, ".meld/decisions/0001-dec-1.md", text, digest="b" * 64)   # hash mismatch
    assert "hash" in review(ctx, 40, now=NOW)["reason"]


def test_review_auto_approves_a_lesson_pr_for_a_resolved_item(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, group="codex")
    entry = "\n## 2026-09-03 — #13 (claude)\n\nPin the action version.\n"
    memory_pr_on_board(fake, repo, ".meld/memory/lessons.md", "# Lessons\n" + entry, number=41, head="mem0002", kind="lesson", ref="#13", digest=content_hash(entry))
    fake.route("GET", R + "contents/.meld/memory/lessons.md", {"encoding": "base64", "content": __import__("base64").b64encode(b"# Lessons\n").decode()})
    # the route above serves both refs; the test only needs head == base + entry to hold, so serve the head text for the head sha
    import base64
    fake.route("GET", R + "contents/.meld/memory/lessons.md", {"encoding": "base64", "content": base64.b64encode(("# Lessons\n" + entry).encode()).decode()})
    out = review(ctx, 41, now=NOW)
    assert out["event"] == "APPROVE" and out["memory"] == "lesson"
```

The lesson test as written cannot serve two different texts for the same path (FakeGh routes by path). Implement `verify_memory_pr` for lessons as: fetch the head file, require that it ends with an entry whose hash equals the marker (search for the last `\n## ` block) — no base fetch needed — and adjust the test to route only the head text. Report that simplification.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rules_prs.py tests/test_review.py -v`
Expected: FAIL (`PullRequest` has no attribute `memory`).

- [ ] **Step 3: Implement**

`src/meld/board.py`: add to `PullRequest`:

```python
    @property
    def memory(self) -> tuple[str, str, str] | None:
        """(kind, ref, hash) when this is a memory PR opened by `meld decide`/`meld remember` (body marker), else None."""
        m = re.search(r"^meld-memory: (decision|lesson) (\S+) ([0-9a-f]{64})$", self.body or "", re.MULTILINE)
        return (m.group(1), m.group(2), m.group(3)) if m else None
```

`src/meld/rules.py`: in `_review`, before the run-slot check: `if pr.memory: return f"memory PR: run \`meld review {pr.number}\` directly, no worker needed"`. Add `memory_review_rule(board, cfg, actor, pr) -> str | None` returning `pr_refusal(board, cfg, actor, Verb.APPROVE, pr)`'s rule (PM-only: `if not actor.is_pm: return "only the PM may approve a memory PR"`).

`src/meld/review.py`: import `from meld.grammar import agreement_for` and `from meld.memory import LESSONS_FILE, content_hash` (import inside the function if a cycle appears: memory imports review? it does not; memory imports dispatch/status, fine). Add:

```python
def verify_memory_pr(ctx, board, pr) -> tuple[bool, str]:
    kind, ref, digest = pr.memory  # type: ignore[misc]
    paths = list(pr.files)
    if len(paths) != 1:
        return False, f"a memory PR changes exactly one file, this one changes {len(paths)}"
    text = ctx.gh.get_contents(paths[0], pr.head_sha)
    if text is None:
        return False, f"{paths[0]} is missing at {pr.head_sha[:7]}"
    if kind == "decision":
        if content_hash(text) != digest:
            return False, "file hash does not match the meld-memory marker"
        agreement = agreement_for(board.comments, ref, ctx.cfg)
        if not (agreement.joint_posted and agreement.joint_verified and agreement.joint_verb == "DECISION"):
            return False, f"no verified [joint] DECISION {ref}"
        recorded = text.split("## Decision\n\n", 1)[1].strip() if "## Decision\n\n" in text else ""
        if recorded != (agreement.text or "").strip():
            return False, "the recorded decision text does not match the agreed [joint] DECISION text"
        return True, "hash and agreed text verified"
    if paths[0] != LESSONS_FILE:
        return False, f"a lesson PR changes {LESSONS_FILE}, not {paths[0]}"
    last = text.rfind("\n## ")
    entry = text[last:] if last >= 0 else ""
    if content_hash(entry) != digest:
        return False, "the appended lesson entry's hash does not match the meld-memory marker"
    n = int(ref[1:]) if ref.startswith("#") and ref[1:].isdigit() else None
    issue = board.issues.get(n) if n is not None else None
    if issue is None and (n is None or n not in board.pulls):
        return False, f"{ref} is not on the board"
    if issue is not None and issue.status in ("blocked", "needs_human"):
        return False, f"{ref} is still {issue.status}"
    if board.is_challenged(ref):
        return False, f"{ref} has an open CHALLENGE"
    return True, "hash and resolution verified"
```

In `review()`, right after `pr` is resolved and before the verdict-file lookup:

```python
    if pr.memory:
        rule = memory_review_rule(board, ctx.cfg, actor, pr)
        if rule:
            refuse(ctx, board, Refusal(Verb.APPROVE, target, rule))
        ok, reason = verify_memory_pr(ctx, board, pr)
        event = "APPROVE" if ok else "REQUEST_CHANGES"
        body = (f"Memory PR check by {group.name}: {reason}.\n\nverified-by: meld review {number} (file at {pr.head_sha[:7]} fetched and hashed)")
        resp = ctx.gh.create_review(number, event, body, pr.head_sha)
        ctx.gh.create_status(pr.head_sha, "success" if ok else "failure", status_context(group.name), f"{group.name} memory check: {'ok' if ok else reason}"[:140], resp.get("html_url"))
        out = {"reviewed": number, "memory": pr.memory[0], "event": event, "review_id": resp.get("id")}
        if not ok:
            out["reason"] = reason
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rules_prs.py tests/test_review.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/meld/board.py src/meld/rules.py src/meld/review.py tests/test_rules_prs.py tests/test_review.py
git commit -m "feat: memory PRs are hash-verified against the agreed text and auto-approved by meld review"
```

---

### Task 5: The plan gate — `meld plan issues`, `meld plan approve`, `meld plan ack`

**Files:**
- Create: `src/meld/plan.py`
- Modify: `src/meld/rules.py` (`Verb.PLAN`), `src/meld/actions.py` (`PLANNED = "meld:planned"` constant next to `READY`), `src/meld/cli.py`
- Test: `tests/test_plan.py` (new)

**Interfaces:**
- Consumes: `grammar.agreement_for` (`joint_verified`, `joint_verb`), `GitHub.create_issue/update_milestone/add_labels/remove_label`, `posting.post` (for the human `ACK`), `board.PLAN_APPROVED_LABEL`, `models.SIZES`.
- Produces: `plan.PlanItem(id, title, worker, model, effort, size, criteria)`; `plan.ITEM_RE`; `parse_plan_items(text) -> tuple[list[PlanItem], list[str]]` (items in order; one error string per malformed `- id:` line); `plan_issues(ctx, ref, *, dry_run=False) -> dict` (`{"plan": ref, "created": [numbers], "skipped": [ids already present], "errors": [...]}`); `plan_approve(ctx) -> dict` (`{"approved": "plan-<m>", "ready": [numbers]}`); `plan_ack(ctx, ref) -> dict` (`{"acked": ref, "ready": [numbers]}`); issue bodies carry `plan-item: <id>` and `plan-ref: <ref>` on their first two lines; CLI group `meld plan` with `issues REF [--dry-run]`, `approve`, `ack REF`.

- [ ] **Step 1: Write the failing tests**

`tests/test_plan.py`:

```python
import pytest

from meld.actions import Refused
from meld.plan import PlanItem, parse_plan_items, plan_ack, plan_approve, plan_issues
from tests.productfixtures import R, fixture, make_product

PLAN_TEXT = """Milestone 1 decomposition.

- id: login-form | title: Login form | worker: frontend | model: claude-sonnet-5 | effort: medium | size: M
  - renders email and password fields
  - shows server errors inline
- id: health | title: Add health endpoint | worker: backend | model: claude-sonnet-5 | effort: low | size: S
  - GET /health returns 200
- id: broken line without the fields
- id: bad-size | title: X | worker: backend | model: m | effort: low | size: XL

verified-by: meld roster; meld models"""

PLAN_THREAD = [
    {"id": 301, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:00:00Z", "body": "[claude] PROPOSE plan-3\n\n" + PLAN_TEXT},
    {"id": 302, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:01:00Z", "body": "[codex] AGREE plan-3\n\nagreed"},
    {"id": 303, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:02:00Z", "body": "[joint] PLAN plan-3\n\n" + PLAN_TEXT},
]


def test_parse_plan_items():
    items, errors = parse_plan_items(PLAN_TEXT)
    assert items[0] == PlanItem("login-form", "Login form", "frontend", "claude-sonnet-5", "medium", "M",
                                ("renders email and password fields", "shows server errors inline"))
    assert items[1].id == "health" and items[1].criteria == ("GET /health returns 200",)
    assert len(items) == 2 and len(errors) == 2 and "broken line" in errors[0] and "bad-size" in errors[1]


def planning_board(fake, extra_issues=()):
    issues = fixture("issues.json")
    for i in issues:
        if i["number"] == 10:
            i["labels"] = [l for l in i["labels"] if l["name"] != "meld:plan-approved"]      # back to the planning phase
    issues += list(extra_issues)
    fake.route("GET", R + "issues", issues)
    return issues


def test_plan_issues_creates_planned_issues_once(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    fake.route("GET", R + "issues/10/comments", fixture("comments_10.json") + PLAN_THREAD)
    fake.route("POST", R + "issues", {"number": 31, "html_url": "u31"})
    fake.route("PATCH", R + "milestones/3", {"number": 3})
    out = plan_issues(ctx, "plan-3")
    assert out["created"] == [31, 31] and out["skipped"] == [] and len(out["errors"]) == 2
    bodies = [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues"]
    assert bodies[0]["title"] == "Login form" and bodies[0]["milestone"] == 3
    assert bodies[0]["labels"] == ["meld:planned", "worker:frontend", "model:claude-sonnet-5", "effort:medium", "size:M"]
    assert bodies[0]["body"].startswith("plan-item: login-form\nplan-ref: plan-3\n") and "- renders email and password fields" in bodies[0]["body"]
    assert [c.body for c in fake.calls if c.method == "PATCH" and c.endpoint == R + "milestones/3"] == [{"description": PLAN_TEXT}]
    # a second run skips items whose plan-item marker already exists on the board
    existing = fixture("issues.json") + [{"number": 31, "title": "Login form", "body": "plan-item: login-form\nplan-ref: plan-3\n", "state": "open",
                                          "labels": [{"name": "meld:planned"}], "assignees": [], "milestone": {"number": 3}}]
    fake.route("GET", R + "issues", existing)
    again = plan_issues(ctx, "plan-3")
    assert again["skipped"] == ["login-form"] and again["created"] == [31]
    with pytest.raises(Refused, match="only the proposer"):
        plan_issues(make_product(tmp_path / "c", group="codex", role="orchestrator")[1], "plan-3")


def test_plan_issues_refuses_without_a_verified_joint_plan(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    with pytest.raises(Refused, match="no verified \\[joint\\] PLAN"):
        plan_issues(ctx, "plan-3")
    fake2, pm, *_ = make_product(tmp_path / "p")
    fake2.route("GET", R + "issues/10/comments", fixture("comments_10.json") + PLAN_THREAD)
    with pytest.raises(Refused, match="orchestrator"):
        plan_issues(pm, "plan-3")


def test_plan_approve_flips_planned_issues_to_ready(tmp_path):
    fake, human, repo, origin = make_product(tmp_path, group=None)
    planned = [{"number": n, "title": f"P{n}", "body": f"plan-item: p{n}\nplan-ref: plan-3\n", "state": "open",
                "labels": [{"name": "meld:planned"}, {"name": "worker:backend"}], "assignees": [], "milestone": {"number": 3}} for n in (31, 32)]
    planning_board(fake, planned)
    fake.route("GET", R + "issues/10/comments", fixture("comments_10.json") + PLAN_THREAD)
    for n in (10, 31, 32):
        fake.route("POST", R + f"issues/{n}/labels", [])
        fake.route("DELETE", R + f"issues/{n}/labels/meld%3Aplanned", [])
    out = plan_approve(human)
    assert out == {"approved": "plan-3", "ready": [31, 32]}
    assert [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/labels"] == [{"labels": ["meld:plan-approved"]}]
    assert [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/31/labels"] == [{"labels": ["meld:ready"]}]
    assert any(c.method == "DELETE" and c.endpoint.endswith("issues/32/labels/meld%3Aplanned") for c in fake.calls)
    with pytest.raises(Refused, match="only a human"):
        plan_approve(make_product(tmp_path / "o", role="orchestrator")[1])


def test_plan_ack_posts_ack_and_readies_the_additions(tmp_path):
    fake, human, repo, origin = make_product(tmp_path, group=None)
    add_text = "- id: audit-log | title: Audit log | worker: backend | model: claude-sonnet-5 | effort: medium | size: M\n  - every write is logged\n\nverified-by: meld roster"
    thread = fixture("comments_10.json") + [
        {"id": 311, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T11:00:00Z", "body": "[codex] PROPOSE plan-add-1\n\n" + add_text},
        {"id": 312, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T11:01:00Z", "body": "[claude] AGREE plan-add-1\n\nok"},
        {"id": 313, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T11:02:00Z", "body": "[joint] PLAN-ADD plan-add-1\n\n" + add_text}]
    fake.route("GET", R + "issues/10/comments", thread)
    issues = fixture("issues.json") + [{"number": 33, "title": "Audit log", "body": "plan-item: audit-log\nplan-ref: plan-add-1\n", "state": "open",
                                        "labels": [{"name": "meld:planned"}], "assignees": [], "milestone": {"number": 3}}]
    fake.route("GET", R + "issues", issues)
    fake.route("POST", R + "issues/33/labels", [])
    fake.route("DELETE", R + "issues/33/labels/meld%3Aplanned", [])
    out = plan_ack(human, "plan-add-1")
    assert out == {"acked": "plan-add-1", "ready": [33]}
    posted = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]
    assert posted[-1].startswith("[human] ACK plan-add-1\n")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_plan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.plan'`.

- [ ] **Step 3: Implement**

`src/meld/rules.py`: add `PLAN = "plan"` to `Verb`. `src/meld/actions.py`: add `PLANNED = "meld:planned"`.

`src/meld/plan.py`:

```python
"""meld plan: a verified [joint] PLAN becomes planned issues; the human approves the plan or acks an addition."""
from __future__ import annotations

import re
from dataclasses import dataclass

from meld.actions import PLANNED, READY, refuse
from meld.board import PLAN_APPROVED_LABEL
from meld.grammar import agreement_for
from meld.models import SIZES
from meld.posting import post
from meld.rules import Refusal, Verb
from meld.status import build_status

ITEM_RE = re.compile(r"^\s*-\s*id:\s*(?P<id>[a-z0-9][a-z0-9-]*)\s*\|\s*title:\s*(?P<title>[^|]+?)\s*\|\s*worker:\s*(?P<worker>\S+)\s*\|"
                     r"\s*model:\s*(?P<model>\S+)\s*\|\s*effort:\s*(?P<effort>\S+)\s*\|\s*size:\s*(?P<size>[A-Z]+)\s*$")
CRITERION_RE = re.compile(r"^\s{2,}-\s+(?P<text>.*\S)\s*$")
MARKER_RE = re.compile(r"^plan-item: (\S+)$", re.MULTILINE)


@dataclass(frozen=True)
class PlanItem:
    id: str
    title: str
    worker: str
    model: str
    effort: str
    size: str
    criteria: tuple[str, ...]


def parse_plan_items(text: str) -> tuple[list[PlanItem], list[str]]:
    items: list[PlanItem] = []
    errors: list[str] = []
    current: dict | None = None
    criteria: list[str] = []

    def flush() -> None:
        nonlocal current, criteria
        if current is not None:
            items.append(PlanItem(**current, criteria=tuple(criteria)))
        current, criteria = None, []

    for line in (text or "").splitlines():
        m = ITEM_RE.match(line)
        if m:
            flush()
            if m.group("size") not in SIZES:
                errors.append(f"item {m.group('id')}: size {m.group('size')!r} is not one of {', '.join(SIZES)}: {line.strip()}")
                continue
            current = {k: m.group(k).strip() for k in ("id", "title", "worker", "model", "effort", "size")}
            continue
        c = CRITERION_RE.match(line)
        if c and current is not None:
            criteria.append(c.group("text"))
            continue
        if line.lstrip().startswith("- id:"):
            flush()
            errors.append(f"malformed plan item: {line.strip()}")
            continue
        if line.strip() and not line.startswith(" "):
            flush()
    flush()
    return items, errors


def _verified_joint(ctx, board, ref: str, verbs: tuple[str, ...], verb: Verb):
    agreement = agreement_for(board.comments, ref, ctx.cfg)
    if not agreement.joint_posted or agreement.joint_verb not in verbs:
        refuse(ctx, board, Refusal(verb, ref, f"no verified [joint] {'/'.join(verbs)} {ref} on the coordination issue"))
    if not agreement.joint_verified:
        refuse(ctx, board, Refusal(verb, ref, f"the [joint] {agreement.joint_verb} {ref} comment is not verified (wrong author or posted before the round closed)"))
    return agreement


def _planned_for(board, ref: str) -> list[int]:
    return sorted(n for n, i in board.issues.items() if PLANNED in i.labels and f"plan-ref: {ref}" in (i.body or ""))


def _ready(ctx, numbers: list[int]) -> None:
    for n in numbers:
        ctx.gh.add_labels(n, [READY])
        ctx.gh.remove_label(n, PLANNED)


def plan_issues(ctx, ref: str, *, dry_run: bool = False) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    if actor.party.kind != "group":
        refuse(ctx, board, Refusal(Verb.PLAN, ref, "only the proposer group's orchestrator may create plan issues"))
    agreement = _verified_joint(ctx, board, ref, ("PLAN", "PLAN-ADD"), Verb.PLAN)
    if agreement.proposer != actor.group:
        refuse(ctx, board, Refusal(Verb.PLAN, ref, f"only the proposer ({agreement.proposer}) may create the issues for {ref}"))
    items, errors = parse_plan_items(agreement.text or "")
    existing = {m.group(1) for i in board.issues.values() for m in [MARKER_RE.search(i.body or "")] if m}
    created: list[int] = []
    skipped: list[str] = []
    for item in items:
        if item.id in existing:
            skipped.append(item.id)
            continue
        body = f"plan-item: {item.id}\nplan-ref: {ref}\n\n## Acceptance criteria\n" + "\n".join(f"- {c}" for c in item.criteria)
        labels = [PLANNED, f"worker:{item.worker}", f"model:{item.model}", f"effort:{item.effort}", f"size:{item.size}"]
        if not dry_run:
            created.append(int(ctx.gh.create_issue(item.title, body, labels, board.milestone)["number"]))
    if agreement.joint_verb == "PLAN" and not dry_run:
        ctx.gh.update_milestone(board.milestone, description=agreement.text or "")
    return {"plan": ref, "created": created, "skipped": skipped, "errors": errors, "dry_run": dry_run}


def plan_approve(ctx) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    ref = f"plan-{board.milestone}"
    if not actor.is_human:
        refuse(ctx, board, Refusal(Verb.PLAN, ref, "only a human may approve the plan"))
    if board.phase != "planning":
        refuse(ctx, board, Refusal(Verb.PLAN, ref, "the plan is already approved (milestone is executing)"))
    _verified_joint(ctx, board, ref, ("PLAN",), Verb.PLAN)
    ctx.gh.add_labels(board.coordination_issue, [PLAN_APPROVED_LABEL])
    ready = sorted(n for n, i in board.issues.items() if PLANNED in i.labels)
    _ready(ctx, ready)
    return {"approved": ref, "ready": ready}


def plan_ack(ctx, ref: str) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    if not actor.is_human:
        refuse(ctx, board, Refusal(Verb.PLAN, ref, "only a human may ack a plan addition"))
    _verified_joint(ctx, board, ref, ("PLAN-ADD",), Verb.PLAN)
    post(ctx, "ACK", ref=ref, text=f"{ref} acknowledged; its issues are ready")
    ready = _planned_for(board, ref)
    _ready(ctx, ready)
    return {"acked": ref, "ready": ready}
```

(`plan_ack` posting first then labeling is fine: both are idempotent to retry.) `src/meld/cli.py`: import `from meld.plan import plan_ack as do_plan_ack, plan_approve as do_plan_approve, plan_issues as do_plan_issues` and add a group:

```python
@main.group()
def plan() -> None:
    """The plan gate: create planned issues from a [joint] PLAN, approve the plan, or ack an addition."""


@plan.command("issues")
@click.argument("ref")
@click.option("--dry-run", is_flag=True, help="Parse and report without creating issues.")
@click.pass_context
def plan_issues_cmd(ctx: click.Context, ref: str, dry_run: bool) -> None:
    """Create meld:planned issues from the verified [joint] PLAN or PLAN-ADD comment for REF."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_plan_issues(c, ref, dry_run=dry_run))


@plan.command("approve")
@click.pass_context
def plan_approve_cmd(ctx: click.Context) -> None:
    """Human: add meld:plan-approved and flip every planned issue to ready."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_plan_approve(c))


@plan.command("ack")
@click.argument("ref")
@click.pass_context
def plan_ack_cmd(ctx: click.Context, ref: str) -> None:
    """Human: post ACK for a [joint] PLAN-ADD and flip its planned issues to ready."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_plan_ack(c, ref))
```

(`_run_action` must also catch `PostError` from `plan_ack` → extend `_run_action` to emit `{"error": ...}` and exit 2 on `PostError`, matching the `post` command.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_plan.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/meld/plan.py src/meld/rules.py src/meld/actions.py src/meld/cli.py tests/test_plan.py
git commit -m "feat: meld plan turns a verified joint PLAN into planned issues and lets the human approve or ack"
```

---

### Task 6: `meld inbox` and `meld chat` — one console for the human

**Files:**
- Create: `src/meld/chat.py`
- Modify: `src/meld/cli.py`
- Test: `tests/test_chat.py` (new)

**Interfaces:**
- Consumes: `posting.post`, `challenge.challenge`, `plan.plan_approve/plan_ack`, `waiting.wait_for_change`, `status.build_status`, `board.comments/issues`, `status._age_minutes`.
- Produces: `chat.inbox(board, cfg, now) -> dict` with keys `needs_human` (`[{"issue", "title", "age_minutes", "ask"}]` — `ask` is the latest `NEEDS-HUMAN #n` text or `""`; excluded once a later `[human] PROVIDED|RESOLVED #n` exists), `budget` (`[{"group", "comment_id", "age_minutes", "text"}]` for `BUDGET` comments with no later human comment), `disagree` (`[{"ref", "verb", "comment_id"}]` for `[joint] … DISAGREE` without a later human `DIRECTIVE`/`AMEND`/`APPROVE` on the ref), `plan_add` (`[{"ref", "comment_id"}]` without a later `[human] ACK <ref>`), `asks` (`[{"ref", "comment_id", "answered": bool}]`); `chat.next_ask_id(board) -> str` (`ask-<n+1>`); `chat.run_chat(ctx, lines, out, *, verbose=False) -> dict` (`{"posted": [headers], "errors": [str]}`), where `lines` is any iterable of input lines and `out` a callable receiving output lines; slash commands: `/inbox`, `/wait`, `/halt`, `/resume`, `/directive TEXT`, `/revert #N [TEXT]`, `/provided #N TEXT`, `/resolved #N TEXT`, `/ack REF`, `/approve`, `/challenge ACTION REF TEXT`, `/quit`; any other line posts `[human] ASK <next id>`; CLI `meld inbox` and `meld chat [--once TEXT] [--verbose]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_chat.py`:

```python
from datetime import datetime, timezone

import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.chat import inbox, next_ask_id, run_chat
from meld.dispatch import DispatchError
from tests.conftest import env_for
from tests.factories import CFG2, mk_board, mk_comment, mk_issue
from tests.productfixtures import R, fixture, make_product

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def test_inbox_collects_what_needs_a_person():
    comments = [
        mk_comment(CFG2, 1, "meld-codex-bot", "[codex] NEEDS-HUMAN #14\n\nCreate a Stripe sandbox key and put it in STRIPE_KEY"),
        mk_comment(CFG2, 2, "meld-claude-bot", "[claude] BUDGET\n\nlarge tier exhausted"),
        mk_comment(CFG2, 3, "meld-claude-bot", "[claude] PROPOSE ans-1\n\nA"),
        mk_comment(CFG2, 4, "meld-codex-bot", "[codex] DISSENT ans-1\n\nB"),
        mk_comment(CFG2, 5, "meld-claude-bot", "[joint] ANSWER ans-1 DISAGREE\n\n## Positions\n- claude: A\n- codex: B\n\nverified-by: x"),
        mk_comment(CFG2, 6, "meld-codex-bot", "[joint] PLAN-ADD plan-add-1\n\n- id: x | title: X | worker: backend | model: m | effort: low | size: S\n\nverified-by: y"),
        mk_comment(CFG2, 7, "empowerScott", "[human] ASK ask-1\n\nwhy postgres?"),
        mk_comment(CFG2, 8, "empowerScott", "[human] ASK ask-2\n\nstatus?"),
        mk_comment(CFG2, 9, "meld-claude-bot", "[joint] ANSWER ask-2\n\nall good\n\nverified-by: meld status"),
    ]
    board = mk_board(CFG2, issues=[mk_issue(14, status="needs_human", assignees=("meld-codex-bot",))], comments=comments)
    box = inbox(board, CFG2, NOW)
    assert box["needs_human"][0]["issue"] == 14 and "Stripe sandbox key" in box["needs_human"][0]["ask"]
    assert box["budget"][0]["group"] == "claude" and box["disagree"] == [{"ref": "ans-1", "verb": "ANSWER", "comment_id": 5}]
    assert box["plan_add"] == [{"ref": "plan-add-1", "comment_id": 6}]
    assert [(a["ref"], a["answered"]) for a in box["asks"]] == [("ask-1", False), ("ask-2", True)]
    assert next_ask_id(board) == "ask-3"
    resolved = comments + [mk_comment(CFG2, 10, "empowerScott", "[human] PROVIDED #14\n\nkey is in 1Password: STRIPE_KEY"),
                           mk_comment(CFG2, 11, "empowerScott", "[human] ACK plan-add-1\n\nok"),
                           mk_comment(CFG2, 12, "empowerScott", "[human] DIRECTIVE dir-1\n\nuse A")]
    box2 = inbox(mk_board(CFG2, issues=[mk_issue(14, status="needs_human")], comments=resolved), CFG2, NOW)
    assert box2["needs_human"] == [] and box2["plan_add"] == [] and box2["budget"] == [] and box2["disagree"] == [{"ref": "ans-1", "verb": "ANSWER", "comment_id": 5}]


def test_run_chat_posts_asks_and_human_verbs(tmp_path):
    fake, human, repo, origin = make_product(tmp_path, group=None)
    fake.route("POST", R + "issues/13/labels", [])
    lines = ["why did we pick postgres?", "/halt", "/directive ship the login form first", "/challenge open #13 the CI claim names no command", "/inbox", "/quit"]
    out_lines: list[str] = []
    result = run_chat(human, lines, out_lines.append)
    posted = [c.body["body"].splitlines()[0] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]
    assert posted == ["[human] ASK ask-1", "[human] HALT", "[human] DIRECTIVE dir-1", "[human] CHALLENGE #13"]
    assert result["posted"] == posted and result["errors"] == []
    assert any("needs_human" in line or "Needs a human" in line for line in out_lines)


def test_run_chat_reports_errors_and_refuses_non_humans(tmp_path):
    fake, human, repo, origin = make_product(tmp_path, group=None)
    result = run_chat(human, ["/provided", "/bogus", "/quit"], lambda s: None)
    assert len(result["errors"]) == 2 and result["posted"] == []
    fake2, pm, *_ = make_product(tmp_path / "p")
    with pytest.raises(DispatchError, match="humans"):
        run_chat(pm, ["hello"], lambda s: None)


def test_cli_inbox_and_chat_once(tmp_path, monkeypatch):
    fake, human, repo, origin = make_product(tmp_path, group=None)
    monkeypatch.setattr(cli, "RUN", fake)
    env = env_for(tmp_path, group=None)
    result = CliRunner().invoke(cli.main, ["--config", str(repo / ".meld.toml"), "--compact", "inbox"], env=env)
    assert result.exit_code == 0, result.output
    assert '"needs_human"' in result.output
    once = CliRunner().invoke(cli.main, ["--config", str(repo / ".meld.toml"), "--compact", "chat", "--once", "what is next?"], env=env)
    assert once.exit_code == 0, once.output
    assert "[human] ASK ask-1" in once.output
    repl = CliRunner().invoke(cli.main, ["--config", str(repo / ".meld.toml"), "--compact", "chat"], env=env, input="/resume\n/quit\n")
    assert repl.exit_code == 0, repl.output
    assert "[human] RESUME" in repl.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_chat.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.chat'`.

- [ ] **Step 3: Implement**

`src/meld/chat.py`:

```python
"""meld inbox and meld chat: what needs a person, and one console to answer as [human]."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Callable, Iterable

from meld.actions import Refused
from meld.board import Board
from meld.challenge import challenge
from meld.config import Config
from meld.dispatch import DispatchError
from meld.plan import plan_ack, plan_approve
from meld.posting import PostError, post
from meld.status import _age_minutes, build_status
from meld.waiting import wait_for_change

_ASK_RE = re.compile(r"^ask-(\d+)$")
_DIR_RE = re.compile(r"^dir-(\d+)$")


def _ordered(board: Board):
    return sorted((c for c in board.comments if c.ok and c.header is not None), key=lambda c: (c.created_at, c.id))


def _later_human(comments, after_id: int, verbs: tuple[str, ...], ref: str | None = None) -> bool:
    return any(c.id > after_id and c.header.party.kind == "human" and c.header.verb in verbs and (ref is None or c.header.ref == ref) for c in comments)


def inbox(board: Board, cfg: Config, now: datetime) -> dict:
    comments = _ordered(board)
    needs_human = []
    for i in sorted(board.issues.values(), key=lambda i: i.number):
        if i.status != "needs_human":
            continue
        asks = [c for c in comments if c.header.verb == "NEEDS-HUMAN" and c.header.ref == f"#{i.number}"]
        last_id = asks[-1].id if asks else 0
        if asks and _later_human(comments, last_id, ("PROVIDED", "RESOLVED"), f"#{i.number}"):
            continue
        needs_human.append({"issue": i.number, "title": i.title, "age_minutes": _age_minutes(i.updated_at, now), "ask": asks[-1].text if asks else ""})
    budget = [{"group": c.header.party.group, "comment_id": c.id, "age_minutes": _age_minutes(c.created_at, now), "text": c.text}
              for c in comments if c.header.verb == "BUDGET" and not any(h.id > c.id and h.header.party.kind == "human" for h in comments)]
    disagree = [{"ref": c.header.ref, "verb": c.header.verb, "comment_id": c.id} for c in comments
                if c.header.party.kind == "joint" and c.header.qualifier == "DISAGREE"
                and not _later_human(comments, c.id, ("DIRECTIVE", "AMEND", "APPROVE"), c.header.ref)]
    plan_add = [{"ref": c.header.ref, "comment_id": c.id} for c in comments
                if c.header.party.kind == "joint" and c.header.verb == "PLAN-ADD" and not _later_human(comments, c.id, ("ACK",), c.header.ref)]
    asks = [{"ref": c.header.ref, "comment_id": c.id,
             "answered": any(a.header.party.kind == "joint" and a.header.verb == "ANSWER" and a.header.ref == c.header.ref for a in comments)}
            for c in comments if c.header.party.kind == "human" and c.header.verb == "ASK"]
    return {"needs_human": needs_human, "budget": budget, "disagree": disagree, "plan_add": plan_add, "asks": asks}


def _next_id(board: Board, pattern: re.Pattern, prefix: str) -> str:
    n = max((int(m.group(1)) for c in board.comments if c.header and c.header.ref for m in [pattern.match(c.header.ref)] if m), default=0)
    return f"{prefix}-{n + 1}"


def next_ask_id(board: Board) -> str:
    return _next_id(board, _ASK_RE, "ask")


def _render_inbox(box: dict) -> str:
    lines = ["Needs a human:"] + [f"  #{i['issue']} {i['title']} ({i['age_minutes']} min): {i['ask'].splitlines()[0] if i['ask'] else '(no ask posted)'}" for i in box["needs_human"]]
    lines += ["Budget:"] + [f"  {b['group']}: {b['text'].splitlines()[0] if b['text'] else ''}" for b in box["budget"]]
    lines += ["Disagreements awaiting you:"] + [f"  {d['verb']} {d['ref']}" for d in box["disagree"]]
    lines += ["Plan additions awaiting ACK:"] + [f"  {p['ref']}" for p in box["plan_add"]]
    lines += ["Open asks:"] + [f"  {a['ref']}{'' if a['answered'] else ' (unanswered)'}" for a in box["asks"]]
    return "\n".join(lines)


def _new_comments(board: Board, after_id: int, verbose: bool) -> list[str]:
    out = []
    for c in _ordered(board):
        if c.id <= after_id:
            continue
        if verbose or c.header.party.kind in ("joint", "human") or c.header.verb in ("NEEDS-HUMAN", "BUDGET", "DRIFT", "PROTOCOL-VIOLATION"):
            out.append(f"{c.header.party.label} {c.header.verb} {c.header.ref or ''} {c.header.qualifier or ''}".strip() + f"\n{c.text}")
    return out


def run_chat(ctx, lines: Iterable[str], out: Callable[[str], None], *, verbose: bool = False) -> dict:
    data, board = build_status(ctx)
    actor = ctx.actor()
    if not actor.is_human:
        raise DispatchError("meld chat is for humans; groups talk through meld post")
    posted: list[str] = []
    errors: list[str] = []
    last_seen = max((c.id for c in board.comments), default=0)
    out(_render_inbox(inbox(board, ctx.cfg, datetime.now(tz=__import__("datetime").timezone.utc))))

    def do_post(verb: str, ref: str | None, text: str, qualifier: str | None = None) -> None:
        nonlocal board
        resp = post(ctx, verb, ref=ref, qualifier=qualifier, text=text)
        posted.append(resp["header"])
        out(resp["header"])
        _, board = build_status(ctx)

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            if line == "/quit":
                break
            if line == "/inbox":
                out(_render_inbox(inbox(board, ctx.cfg, datetime.now(tz=__import__("datetime").timezone.utc))))
            elif line == "/wait":
                result = wait_for_change(ctx, timeout=ctx.cfg.wait_timeout_seconds, interval=ctx.cfg.poll_interval_seconds, kinds=("comments",))
                _, board = build_status(ctx)
                for text in _new_comments(board, last_seen, verbose):
                    out(text)
                last_seen = max((c.id for c in board.comments), default=last_seen)
                out("(timed out)" if result.get("timed_out") else "")
            elif line == "/halt":
                do_post("HALT", None, "halt requested from meld chat")
            elif line == "/resume":
                do_post("RESUME", None, "resume requested from meld chat")
            elif line.startswith("/directive "):
                do_post("DIRECTIVE", _next_id(board, _DIR_RE, "dir"), line[len("/directive "):])
            elif line.startswith(("/provided ", "/resolved ", "/revert ")):
                verb, _, rest = line[1:].partition(" ")
                ref, _, text = rest.partition(" ")
                if not ref.startswith("#"):
                    raise PostError(f"/{verb} needs an issue or PR ref like #13")
                do_post(verb.upper(), ref, text or f"{verb} from meld chat")
            elif line.startswith("/challenge "):
                _, action, ref, *rest = line.split(" ", 3)
                resp = challenge(ctx, action, ref, rest[0] if rest else "")
                posted.append(f"[human] {resp['verb']} {ref}")
                out(f"[human] {resp['verb']} {ref}")
                _, board = build_status(ctx)
            elif line.startswith("/ack "):
                out(json.dumps(plan_ack(ctx, line[len("/ack "):].strip())))
                _, board = build_status(ctx)
            elif line == "/approve":
                out(json.dumps(plan_approve(ctx)))
                _, board = build_status(ctx)
            elif line.startswith("/"):
                raise PostError(f"unknown command {line.split()[0]}; try /inbox /wait /halt /resume /directive /provided /resolved /revert /ack /approve /challenge /quit")
            else:
                do_post("ASK", next_ask_id(board), line)
        except (PostError, Refused, DispatchError) as e:
            errors.append(str(e))
            out(f"error: {e}")
    return {"posted": posted, "errors": errors}
```

Replace the `datetime.now(tz=__import__(...))` hack with a proper `from datetime import datetime, timezone` import and `datetime.now(timezone.utc)`. `src/meld/cli.py`: import `from meld.chat import inbox as build_inbox, run_chat` and add:

```python
@main.command()
@click.pass_context
def inbox(ctx: click.Context) -> None:
    """What needs a person: needs-human items, budget alerts, disagreements, plan additions, open asks."""
    c = _load(ctx)

    def run() -> dict:
        _, board = build_status(c)
        return build_inbox(board, c.cfg, datetime.now(timezone.utc))
    emit(_guard(run), ctx.obj["pretty"])


@main.command()
@click.option("--once", "once", default=None, help="Post one line (or one slash command) and exit.")
@click.option("--verbose", is_flag=True, help="/wait prints every new coordination comment, not only joint and human ones.")
@click.pass_context
def chat(ctx: click.Context, once: str | None, verbose: bool) -> None:
    """Talk to every group as [human]: plain text posts an ASK; slash commands post the human verbs."""
    c = _load(ctx)
    lines = [once, "/quit"] if once is not None else iter(click.get_text_stream("stdin").readline, "")
    result = _guard(lambda: run_chat(c, lines, click.echo, verbose=verbose))
    emit(result, ctx.obj["pretty"])
```

(import `datetime`, `timezone` in `cli.py`; `_guard` must include `DispatchError`, which it does.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_chat.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/meld/chat.py src/meld/cli.py tests/test_chat.py
git commit -m "feat: meld inbox and meld chat give the human one console over the coordination issue"
```

---

### Task 7: The notifier — Slack nudges for items that need a person

**Files:**
- Create: `src/meld/notifier.py`
- Modify: `src/meld/dispatch.py` (`tick` calls the notifier), `src/meld/cli.py`
- Test: `tests/test_notifier.py` (new), `tests/test_tick.py`

**Interfaces:**
- Consumes: `Config.notify_webhook_env`, `Config.notify_repeat_after_minutes`, `ctx.store.state_dir`, `atomic.write_atomic`, `status._age_minutes`, `Board.issues`.
- Produces: `notifier.NOTIFY_STATE = "notify.json"`; `notifier.slack_text(items, repo) -> str`; `notifier.notify(ctx, board, now, *, post=None, env=None, dry_run=False) -> dict` returning `{"due": [numbers], "notified": [numbers], "skipped": reason | None, "text": str | None}` — `due` = needs-human issues never notified or notified ≥ `repeat_after_minutes` ago (per-party state file `state_dir/notify.json` mapping issue → ISO time); when the webhook env var is unset or the config has none, `skipped` explains and nothing is sent; `dry_run` computes without sending or recording; `post(url, payload)` defaults to a stdlib `urllib.request` POST of JSON with a 10 s timeout; `tick` (PM) gains `env: Mapping | None = None` and returns `"notified": [...]`; CLI `meld notify [--dry-run]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_notifier.py`:

```python
import json
from datetime import datetime, timedelta, timezone

from meld.notifier import NOTIFY_STATE, notify, slack_text
from tests.productfixtures import make_product

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def test_notify_sends_once_and_repeats_after_the_configured_delay(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    _, board = __import__("meld.status", fromlist=["build_status"]).build_status(ctx)
    sent: list[tuple[str, dict]] = []
    env = {"MELD_SLACK_DM_WEBHOOK": "https://hooks.slack.test/abc"}
    cfg_env_name = ctx.cfg.notify_webhook_env or "MELD_SLACK_DM_WEBHOOK"
    env = {cfg_env_name: "https://hooks.slack.test/abc"}
    out = notify(ctx, board, NOW, post=lambda url, payload: sent.append((url, payload)), env=env)
    assert out["due"] == [14] and out["notified"] == [14] and out["skipped"] is None
    assert sent[0][0] == "https://hooks.slack.test/abc" and "#14" in sent[0][1]["text"] and "Needs API key" in sent[0][1]["text"]
    state = json.loads((ctx.store.state_dir / NOTIFY_STATE).read_text())
    assert state["14"] == "2026-09-03T12:00:00Z"
    again = notify(ctx, board, NOW + timedelta(minutes=5), post=lambda url, payload: sent.append((url, payload)), env=env)
    assert again["due"] == [] and again["notified"] == [] and len(sent) == 1
    later = notify(ctx, board, NOW + timedelta(minutes=ctx.cfg.notify_repeat_after_minutes + 1), post=lambda url, payload: sent.append((url, payload)), env=env)
    assert later["notified"] == [14] and len(sent) == 2


def test_notify_skips_without_a_webhook_and_dry_runs(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    _, board = __import__("meld.status", fromlist=["build_status"]).build_status(ctx)
    sent = []
    out = notify(ctx, board, NOW, post=lambda u, p: sent.append(p), env={})
    assert out["notified"] == [] and out["due"] == [14] and "not set" in out["skipped"] and sent == []
    env = {ctx.cfg.notify_webhook_env: "https://hooks.slack.test/abc"}
    dry = notify(ctx, board, NOW, post=lambda u, p: sent.append(p), env=env, dry_run=True)
    assert dry["notified"] == [] and dry["due"] == [14] and "#14" in dry["text"] and sent == [] and not (ctx.store.state_dir / NOTIFY_STATE).exists()


def test_slack_text_lists_each_item_with_age_and_link():
    text = slack_text([{"issue": 14, "title": "Needs API key", "age_minutes": 90}], "acme/widgets")
    assert text.startswith("Meld needs a human on acme/widgets:") and "#14 Needs API key (1h 30m)" in text and "https://github.com/acme/widgets/issues/14" in text
```

`tests/conftest.PROJECT_TOML` has no `[notify]` table, so `ctx.cfg.notify_webhook_env` may be `None`; if it is, extend `PROJECT_TOML` with `[notify]\nslack_dm_webhook_env = "MELD_SLACK_DM_WEBHOOK"\nrepeat_after_minutes = 120` (check that no existing test asserts on the absence) and simplify the two `env` lines in the first test to use that name. Append to `tests/test_tick.py`:

```python
def test_tick_notifies_needs_human_items_when_a_webhook_is_configured(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    sent = []
    import meld.dispatch as dispatch_mod
    out = tick(ctx, now=NOW, env={"MELD_SLACK_DM_WEBHOOK": "https://hooks.slack.test/abc"}, post=lambda u, p: sent.append(p))
    assert out["notified"] == [14] and len(sent) == 1
    assert tick(ctx, now=NOW, env={}, post=lambda u, p: sent.append(p))["notified"] == []
```

(`tick` gains `post=None` too, forwarded to `notify`; the fixture issue #14 is `needs_human`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_notifier.py tests/test_tick.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.notifier'`.

- [ ] **Step 3: Implement**

`src/meld/notifier.py`:

```python
"""Slack nudges for items that need a person: one message per needs-human issue, repeated after a configured delay."""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from meld.atomic import write_atomic
from meld.board import Board
from meld.status import _age_minutes

NOTIFY_STATE = "notify.json"
_ISO = "%Y-%m-%dT%H:%M:%SZ"


def _post_webhook(url: str, payload: dict) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10):  # noqa: S310 - the URL comes from the operator's own environment
        pass


def _age(minutes: int) -> str:
    return f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes}m"


def slack_text(items: list[dict], repo: str) -> str:
    lines = [f"Meld needs a human on {repo}:"]
    for i in items:
        lines.append(f"• #{i['issue']} {i['title']} ({_age(i['age_minutes'])}) https://github.com/{repo}/issues/{i['issue']}")
    return "\n".join(lines)


def _load_state(path: Path) -> dict[str, str]:
    try:
        return json.loads(path.read_text()) if path.is_file() else {}
    except json.JSONDecodeError:
        return {}


def notify(ctx, board: Board, now: datetime, *, post: Callable[[str, dict], None] | None = None, env: Mapping[str, str] | None = None,
           dry_run: bool = False) -> dict:
    env = os.environ if env is None else env
    items = sorted(({"issue": i.number, "title": i.title, "age_minutes": _age_minutes(i.updated_at, now)}
                    for i in board.issues.values() if i.status == "needs_human"), key=lambda d: d["issue"])
    state_path = ctx.store.state_dir / NOTIFY_STATE
    sent = _load_state(state_path)
    due = []
    for item in items:
        last = sent.get(str(item["issue"]))
        if last is None or (now - datetime.strptime(last, _ISO).replace(tzinfo=timezone.utc)).total_seconds() >= ctx.cfg.notify_repeat_after_minutes * 60:
            due.append(item)
    numbers = [d["issue"] for d in due]
    text = slack_text(due, ctx.cfg.repo_name) if due else None
    if not ctx.cfg.notify_webhook_env:
        return {"due": numbers, "notified": [], "skipped": "no [notify] slack_dm_webhook_env configured", "text": text}
    url = env.get(ctx.cfg.notify_webhook_env)
    if not url:
        return {"due": numbers, "notified": [], "skipped": f"{ctx.cfg.notify_webhook_env} is not set", "text": text}
    if dry_run or not due:
        return {"due": numbers, "notified": [], "skipped": None, "text": text}
    (post or _post_webhook)(url, {"text": text})
    stamp = now.strftime(_ISO)
    for n in numbers:
        sent[str(n)] = stamp
    state_path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(state_path, json.dumps(sent, indent=2))
    return {"due": numbers, "notified": numbers, "skipped": None, "text": text}
```

`src/meld/dispatch.py::tick`: add parameters `env: Mapping[str, str] | None = None, post=None`; before the return, `out["notified"] = notify(ctx, board, when, post=post, env=env)["notified"]` (import `notify` lazily inside `tick` if a cycle appears: `notifier` imports `status`, not `dispatch`, so a top-level import is fine). `src/meld/cli.py`:

```python
@main.command()
@click.option("--dry-run", is_flag=True, help="Show what would be sent without posting or recording.")
@click.pass_context
def notify(ctx: click.Context, dry_run: bool) -> None:
    """Send the Slack nudge for needs-human items now (a PM's tick does this on its own)."""
    c = _load(ctx)

    def run() -> dict:
        _, board = build_status(c)
        return do_notify(c, board, datetime.now(timezone.utc), dry_run=dry_run)
    emit(_guard(run), ctx.obj["pretty"])
```

(`from meld.notifier import notify as do_notify`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_notifier.py tests/test_tick.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/meld/notifier.py src/meld/dispatch.py src/meld/cli.py tests/test_notifier.py tests/test_tick.py tests/conftest.py
git commit -m "feat: Slack nudges for needs-human items, throttled per party and sent from the PM's tick"
```

---

### Task 8: PM housekeeping in `tick` — red CI, ignored comments, off-protocol changes

**Files:**
- Create: `src/meld/housekeeping.py`
- Modify: `src/meld/dispatch.py` (`tick`)
- Test: `tests/test_housekeeping.py` (new), `tests/test_tick.py`

**Interfaces:**
- Consumes: `Board.pulls/issues/comments`, `PullRequest.checks/head_sha/issue_number/author`, `rules.party_of_login`, `dispatch._swap_labels/IN_REVIEW`, `actions.IN_PROGRESS`, `GitHub.issue_events/create_comment`, `grammar.compose/Header`, `config.Party`, `ctx.store.state_dir`, `atomic.write_atomic`.
- Produces: `housekeeping.CI_RED_STATE = "ci-red.json"`, `IGNORED_STATE = "ignored.json"`, `OFF_PROTOCOL_STATE = "off-protocol.json"`; `ci_red_sweep(ctx, board, group) -> list[dict]` (for each open PR with `checks == "red"` authored by the caller's group whose linked issue is `in_review`: labels `in_review → in_progress`, an issue comment naming the PR and head, once per head; returns `[{"pr", "issue", "head"}]`); `flag_ignored_comments(ctx, board, group) -> dict` (`{"flagged": [comment ids], "posted": bool}`: unauthorized comments newer than the per-party cursor; only the alphabetically first configured group posts `[<g>:pm] IGNORED-COMMENT` listing `- comment <id> by \`<author>\``; every group advances its cursor); `off_protocol_sweep(ctx, board, group) -> list[dict]` (for each open issue in the milestone, `gh.issue_events(n)`; `labeled/unlabeled/assigned/unassigned` events by an actor that is neither a configured bot nor a listed human and whose id is newer than the per-issue cursor are posted once as `[<g>:pm] PROTOCOL-VIOLATION` with `off-protocol change on #<n>: <actor> <event> <label or assignee>`; only the alphabetically first group posts, all groups advance cursors; returns `[{"issue", "event_id", "actor", "event", "what"}]`); `tick` (PM) returns `"ci_red"`, `"ignored"`, `"off_protocol"` in its result.

- [ ] **Step 1: Write the failing tests**

`tests/test_housekeeping.py`:

```python
import json
from datetime import datetime, timezone

from meld.housekeeping import CI_RED_STATE, IGNORED_STATE, OFF_PROTOCOL_STATE, ci_red_sweep, flag_ignored_comments, off_protocol_sweep
from meld.status import build_status
from tests.productfixtures import R, fixture, make_product
from tests.test_collect import routes_for_labels

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
RED = {"total_count": 1, "check_runs": [{"name": "ci", "status": "completed", "conclusion": "failure"}]}


def no_events(fake):
    for n in (11, 12, 13, 14):
        fake.route("GET", R + f"issues/{n}/events", [])


def test_ci_red_returns_the_authors_issue_to_in_progress_once_per_head(tmp_path):
    fake, codex, repo, origin = make_product(tmp_path, group="codex")      # PR #20 (issue #13, in review) is codex's
    fake.route("GET", R + "commits/abc123/check-runs", RED)
    routes_for_labels(fake, 13)
    fake.route("DELETE", R + "issues/13/labels/meld%3Ain-review", [])
    _, board = build_status(codex)
    group = codex.cfg.groups["codex"]
    out = ci_red_sweep(codex, board, group)
    assert out == [{"pr": 20, "issue": 13, "head": "abc123"}]
    assert [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/labels"] == [{"labels": ["meld:in-progress"]}]
    comment = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/comments"][-1]
    assert "CI is red on PR #20" in comment and "abc123"[:7] in comment
    assert json.loads((codex.store.state_dir / CI_RED_STATE).read_text()) == {"20": "abc123"}
    assert ci_red_sweep(codex, board, group) == []                               # same head: nothing more
    fake2, claude, *_ = make_product(tmp_path / "c")                            # claude did not author #20: not its job
    fake2.route("GET", R + "commits/abc123/check-runs", RED)
    _, board2 = build_status(claude)
    assert ci_red_sweep(claude, board2, claude.cfg.groups["claude"]) == []


def test_ignored_comments_are_flagged_once_by_the_first_group(tmp_path):
    fake, claude, repo, origin = make_product(tmp_path)                      # fixture comment 103 is by randouser (unauthorized)
    _, board = build_status(claude)
    out = flag_ignored_comments(claude, board, claude.cfg.groups["claude"])
    assert out == {"flagged": [103], "posted": True}
    coord = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"][-1]
    assert coord.startswith("[claude:pm] IGNORED-COMMENT\n") and "- comment 103 by `randouser`" in coord
    assert json.loads((claude.store.state_dir / IGNORED_STATE).read_text()) == {"last_id": 103}
    assert flag_ignored_comments(claude, board, claude.cfg.groups["claude"]) == {"flagged": [], "posted": False}
    fake2, codex, *_ = make_product(tmp_path / "x", group="codex")
    _, board2 = build_status(codex)
    assert flag_ignored_comments(codex, board2, codex.cfg.groups["codex"]) == {"flagged": [103], "posted": False}   # codex only advances its cursor
    assert not [c for c in fake2.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]


def test_off_protocol_changes_by_unknown_actors_are_reported_once(tmp_path):
    fake, claude, repo, origin = make_product(tmp_path)
    no_events(fake)
    fake.route("GET", R + "issues/13/events", [
        {"id": 9001, "event": "labeled", "actor": {"login": "randouser"}, "label": {"name": "meld:ready"}},
        {"id": 9002, "event": "assigned", "actor": {"login": "meld-codex-bot"}, "assignee": {"login": "meld-codex-bot"}},
        {"id": 9003, "event": "unassigned", "actor": {"login": "someone-else"}, "assignee": {"login": "meld-codex-bot"}},
        {"id": 9004, "event": "commented", "actor": {"login": "someone-else"}}])
    _, board = build_status(claude)
    out = off_protocol_sweep(claude, board, claude.cfg.groups["claude"])
    assert [(o["event_id"], o["actor"], o["event"]) for o in out] == [(9001, "randouser", "labeled"), (9003, "someone-else", "unassigned")]
    posts = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]
    assert len(posts) == 2 and posts[0].startswith("[claude:pm] PROTOCOL-VIOLATION\n") and "off-protocol change on #13: randouser labeled meld:ready" in posts[0]
    assert json.loads((claude.store.state_dir / OFF_PROTOCOL_STATE).read_text()) == {"13": 9004}
    assert off_protocol_sweep(claude, board, claude.cfg.groups["claude"]) == []
```

Append to `tests/test_tick.py`:

```python
def test_tick_runs_the_housekeeping_sweeps(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    for n in (11, 12, 13, 14):
        fake.route("GET", R + f"issues/{n}/events", [])
    out = tick(ctx, now=NOW, env={})
    assert out["ci_red"] == [] and out["off_protocol"] == [] and out["ignored"]["flagged"] == [103] and out["notified"] == []
```

Other `tick` tests that do not route `issues/{n}/events` need it too: the FakeGh returns 404 for an unrouted GET, which `issue_events` would raise as `GitHubError` — route `[]` in `productfixtures.make_product` for the fixture's open issues (11, 12, 13, 14) so every existing test keeps passing, and note it in the report.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_housekeeping.py tests/test_tick.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.housekeeping'`.

- [ ] **Step 3: Implement**

`src/meld/housekeeping.py`:

```python
"""PM housekeeping run from `meld tick`: red CI on our PRs, comments from strangers, and label or assignee changes made outside meld."""
from __future__ import annotations

import json
from pathlib import Path

from meld.actions import IN_PROGRESS
from meld.atomic import write_atomic
from meld.board import Board
from meld.config import GroupConfig, Party
from meld.grammar import Header, compose
from meld.rules import party_of_login

CI_RED_STATE = "ci-red.json"
IGNORED_STATE = "ignored.json"
OFF_PROTOCOL_STATE = "off-protocol.json"
_EVENTS = ("labeled", "unlabeled", "assigned", "unassigned")


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text()) if path.is_file() else {}
    except json.JSONDecodeError:
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(data, indent=2))


def _first_group(cfg) -> str:
    return min(cfg.group_names)


def ci_red_sweep(ctx, board: Board, group: GroupConfig) -> list[dict]:
    """A red check on one of our open PRs is a request for changes: the linked issue goes back to in-progress once per head."""
    from meld.dispatch import IN_REVIEW, _swap_labels  # local import: dispatch imports this module
    path = ctx.store.state_dir / CI_RED_STATE
    handled = _load(path)
    out = []
    for pr in sorted(board.pulls.values(), key=lambda p: p.number):
        if pr.state != "open" or pr.checks != "red" or party_of_login(ctx.cfg, pr.author) != group.name or pr.issue_number is None:
            continue
        issue = board.issues.get(pr.issue_number)
        if issue is None or issue.status != "in_review" or handled.get(str(pr.number)) == pr.head_sha:
            continue
        _swap_labels(ctx, pr.issue_number, IN_PROGRESS, (IN_REVIEW,))
        ctx.gh.create_comment(pr.issue_number, f"Meld: CI is red on PR #{pr.number} (head `{pr.head_sha[:7]}`); treated as request-changes. "
                                               f"The next build run on this issue fixes the checks; the PR moves back to review when they pass.")
        handled[str(pr.number)] = pr.head_sha
        out.append({"pr": pr.number, "issue": pr.issue_number, "head": pr.head_sha})
    if out:
        _save(path, handled)
    return out


def flag_ignored_comments(ctx, board: Board, group: GroupConfig) -> dict:
    """Comments from accounts outside the configured bots and humans are ignored by the parser and flagged once (spec 7)."""
    path = ctx.store.state_dir / IGNORED_STATE
    state = _load(path)
    cursor = int(state.get("last_id", 0))
    new = sorted((c for c in board.comments if not c.authorized and c.id > cursor), key=lambda c: c.id)
    if not new:
        return {"flagged": [], "posted": False}
    posted = False
    if group.name == _first_group(ctx.cfg):
        body = "\n".join(f"- comment {c.id} by `{c.author}`" for c in new)
        ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "IGNORED-COMMENT", None, None),
                                                                f"Comments from accounts that are neither a configured bot nor a listed human were ignored:\n{body}"))
        posted = True
    _save(path, {"last_id": new[-1].id})
    return {"flagged": [c.id for c in new], "posted": posted}


def off_protocol_sweep(ctx, board: Board, group: GroupConfig) -> list[dict]:
    """Label or assignee changes on milestone issues by an actor that is neither a bot nor a listed human (spec 17)."""
    known = {g.bot for g in ctx.cfg.groups.values()} | set(ctx.cfg.humans)
    path = ctx.store.state_dir / OFF_PROTOCOL_STATE
    cursors = _load(path)
    out = []
    for n, issue in sorted(board.issues.items()):
        if issue.state != "open":
            continue
        last = int(cursors.get(str(n), 0))
        newest = last
        for e in ctx.gh.issue_events(n):
            eid = int(e.get("id", 0))
            newest = max(newest, eid)
            actor = (e.get("actor") or {}).get("login", "")
            if eid <= last or e.get("event") not in _EVENTS or actor in known:
                continue
            what = (e.get("label") or {}).get("name") or (e.get("assignee") or {}).get("login") or ""
            record = {"issue": n, "event_id": eid, "actor": actor, "event": e.get("event"), "what": what}
            out.append(record)
            if group.name == _first_group(ctx.cfg):
                ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "PROTOCOL-VIOLATION", None, None),
                                                                        f"off-protocol change on #{n}: {actor} {e.get('event')} {what}".rstrip()))
        if newest != last:
            cursors[str(n)] = newest
    _save(path, cursors)
    return out
```

`src/meld/dispatch.py::tick`: after the existing work and before the return (PM path), add `group = ctx.cfg.groups[actor.group]` if not already bound and:

```python
    out["ci_red"] = ci_red_sweep(ctx, board, group)
    out["ignored"] = flag_ignored_comments(ctx, board, group)
    out["off_protocol"] = off_protocol_sweep(ctx, board, group)
```

(import `from meld.housekeeping import ci_red_sweep, flag_ignored_comments, off_protocol_sweep` at the top; `housekeeping` imports `dispatch` lazily inside `ci_red_sweep`, so there is no cycle at import time). Route `issues/{n}/events → []` for 11–14 in `tests/productfixtures.make_product`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_housekeeping.py tests/test_tick.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/meld/housekeeping.py src/meld/dispatch.py tests/test_housekeeping.py tests/test_tick.py tests/productfixtures.py
git commit -m "feat: tick handles red CI on our PRs, flags comments from strangers, and reports off-protocol label changes"
```

---

### Task 9: Memory reaches the workers — recall snippets in briefs, `skills_used` in results

**Files:**
- Modify: `src/meld/brief.py`, `src/meld/dispatch.py`, `src/meld/prompts/_worker_contract.md`
- Test: `tests/test_brief.py`, `tests/test_dispatch.py`, `tests/test_collect.py`

**Interfaces:**
- Consumes: `memory.recall_snippets(repo_root, text, limit=3)` (Task 3), `render_brief` (skills/concern from plan 3), `_collect_build`'s finish comment, `_worker_contract.md` item 6 JSON shape.
- Produces: `WorkerResult.skills_used: tuple[str, ...] = ()` (read from an optional `skills_used` list of strings; anything else is a `ResultError`); `render_brief(..., recall: Sequence[str] = ())` renders `## Relevant memory` (one bullet per snippet, each truncated to 300 characters) before `## Product standards`; `_dispatch_build` passes `recall=recall_snippets(ctx.repo_root, f"{issue.title}\n{issue.body}")` and `_dispatch_pr` passes `recall=recall_snippets(ctx.repo_root, pr.title)`; `_collect_build`'s finish comment ends with `skills used: a, b` when the result lists any; the worker contract's JSON gains `"skills_used": []` and item 10 says to list the skills actually used.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_brief.py`:

```python
def test_brief_carries_relevant_memory_and_result_reads_skills_used(tmp_path):
    (tmp_path / ".meld" / "memory").mkdir(parents=True)
    (tmp_path / ".meld" / "memory" / "lessons.md").write_text("# Lessons\n\n## 2026-09-01 — #7 (codex)\n\nHealth endpoints must not touch the database.\n")
    issue = mk_issue(11, title="Add health endpoint")
    text = render_brief(role=BACKEND, kind="build", group="claude", model="m", effort="medium", issue=issue, pr=None, plan_text="",
                        repo_root=tmp_path, worktree=tmp_path, base_branch="main", recall=recall_snippets(tmp_path, "Add health endpoint"))
    assert "## Relevant memory" in text and "must not touch the database" in text and text.index("## Relevant memory") < text.index("## Product standards")
    plain = render_brief(role=BACKEND, kind="build", group="claude", model="m", effort="medium", issue=issue, pr=None, plan_text="",
                         repo_root=tmp_path, worktree=tmp_path, base_branch="main")
    assert "## Relevant memory" not in plain
    (tmp_path / ".meld-result.json").write_text(json.dumps({"outcome": "done", "summary": "s", "verified_by": "v", "skills_used": ["brand", "security-review"]}))
    assert read_result(tmp_path).skills_used == ("brand", "security-review")
    (tmp_path / ".meld-result.json").write_text(json.dumps({"outcome": "done", "summary": "s", "verified_by": "v"}))
    assert read_result(tmp_path).skills_used == ()
    (tmp_path / ".meld-result.json").write_text(json.dumps({"outcome": "done", "summary": "s", "verified_by": "v", "skills_used": "brand"}))
    with pytest.raises(ResultError, match="skills_used"):
        read_result(tmp_path)
```

(imports: `recall_snippets` from `meld.memory`, `read_result`, `ResultError`, `json`, `pytest` as needed.) Append to `tests/test_dispatch.py`:

```python
def test_dispatch_renders_relevant_memory_into_the_brief(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    (repo / ".meld" / "memory").mkdir(parents=True)
    (repo / ".meld" / "memory" / "lessons.md").write_text("# Lessons\n\n## 2026-09-01 — #7 (codex)\n\nHealth endpoints must not touch the database.\n")
    claim_issue_11(fake)
    out = dispatch(ctx, 11, adapter=FakeAdapter(), now=NOW)
    text = (Path(out["worktree"]) / BRIEF_FILE).read_text()
    assert "## Relevant memory" in text and "must not touch the database" in text
    wait_for_exit(out["pid"])
```

Append to `tests/test_collect.py`:

```python
def test_collect_reports_the_skills_a_worker_used(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    routes_for_labels(fake, 11)
    fake.route("POST", R + "pulls", {"number": 21, "html_url": "https://github.com/acme/widgets/pull/21"})
    script = DONE_SCRIPT.replace('"verified_by": "python -c \'import health\' -> ok"', '"verified_by": "python -c \'import health\' -> ok", "skills_used": ["brand"]')
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=script), now=NOW)
    wait_for_exit(out["pid"])
    collect(ctx, 11, now=NOW)
    finish = issue_posts(fake, 11)[-1]
    assert finish.startswith("Meld: `claude` worker finished") and finish.rstrip().endswith("skills used: brand")
```

(check `DONE_SCRIPT`'s exact `verified_by` literal in `tests/productfixtures.py` before writing the `replace`; if the quoting differs, target the `"outcome": "done"` key instead and insert `"skills_used": ["brand"], ` before it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_brief.py tests/test_dispatch.py tests/test_collect.py -v`
Expected: FAIL (`render_brief() got an unexpected keyword argument 'recall'`, `WorkerResult` has no attribute `skills_used`).

- [ ] **Step 3: Implement**

`src/meld/brief.py`: add `skills_used: tuple[str, ...] = ()` as the last `WorkerResult` field; in `read_result`, read `raw = data.get("skills_used", [])`, raise `ResultError(f"{RESULT_FILE}: skills_used must be a list of strings")` unless it is a list of `str`, and pass `skills_used=tuple(raw)`. `render_brief` gains `recall: Sequence[str] = ()`; before the `## Product standards` block:

```python
    if recall:
        parts += ["## Relevant memory", ""] + [f"- {s[:300].strip()}" for s in recall] + ["", "Decisions and lessons from earlier work that mention what this task touches; read the files under `.meld/` before departing from them.", ""]
```

`src/meld/dispatch.py`: import `from meld.memory import recall_snippets` (memory imports dispatch — import lazily inside the two dispatch functions to avoid the cycle, or move `recall_snippets`' local-file logic into `brief.py`; the cleaner choice is to have `memory.py` import `recall_snippets` from `brief.py`, so define `recall_snippets` and `_local_hits` in `brief.py` and re-export from `memory.py` — do that and update Task 3's import if you are the same implementer; otherwise a lazy import is acceptable). Pass `recall=` at both `render_brief` call sites as specified. In `_collect_build`, after computing the finish comment text, append `f"\nskills used: {', '.join(result.skills_used)}"` when `result.skills_used`.

`src/meld/prompts/_worker_contract.md`: add `"skills_used": []` to the JSON in item 6 and a sentence under it: "`skills_used` lists the skills you invoked or followed, by name, so the framework can see which ones helped." Extend item 10: "and list them in `skills_used`".

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_brief.py tests/test_dispatch.py tests/test_collect.py tests/test_memory.py tests/test_prompts.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/meld/brief.py src/meld/dispatch.py src/meld/memory.py src/meld/prompts/_worker_contract.md tests/test_brief.py tests/test_dispatch.py tests/test_collect.py
git commit -m "feat: briefs carry relevant decisions and lessons; workers report the skills they used"
```

---

### Task 10: Role prompts for planning, memory, the human console, and the new housekeeping

**Files:**
- Modify: `src/meld/prompts.py` (`COMMANDS`), `src/meld/prompts/orchestrator.md`, `src/meld/prompts/pm.md`, `src/meld/prompts/_preamble.md`
- Test: `tests/test_role_prompts.py`

**Interfaces:** prompt text only, asserted by substring tests; `COMMANDS` lists every plan-4 command.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_role_prompts.py`:

```python
def test_role_prompts_cover_planning_memory_and_the_human_console(tmp_path):
    orch = role_prompt("orchestrator", CFG2, CFG2.groups["claude"], tmp_path)
    for needle in ("- id: <slug> | title:", "meld plan issues plan-<milestone>", "meld plan approve", "meld recall", "meld decide <id>", "meld remember <ref>",
                   "## Memory", "verified [joint]"):
        assert needle in orch, needle
    pm = role_prompt("pm", CFG2, CFG2.groups["claude"], tmp_path)
    for needle in ("memory PR", "red CI", "IGNORED-COMMENT", "off-protocol", "Slack"):
        assert needle in pm, needle
    for cmd in ("meld decide REF", "meld remember REF -m TEXT", "meld recall QUERY", "meld plan issues REF [--dry-run]", "meld plan approve",
                "meld plan ack REF", "meld inbox", "meld chat [--once TEXT]", "meld notify [--dry-run]"):
        assert cmd in pm, cmd
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_role_prompts.py -v`
Expected: FAIL on the first missing needle.

- [ ] **Step 3: Edit the prompts**

`src/meld/prompts.py`: extend `COMMANDS` with `"meld decide REF"`, `"meld remember REF -m TEXT"`, `"meld recall QUERY"`, `"meld plan issues REF [--dry-run]"`, `"meld plan approve"`, `"meld plan ack REF"`, `"meld inbox"`, `"meld chat [--once TEXT]"`, `"meld notify [--dry-run]"`.

`src/meld/prompts/orchestrator.md`, `## Planning`, replace the first two bullets with:

```markdown
- Before proposing, run `meld recall <topic>` for the milestone's main themes and read what it returns: decisions in `.meld/decisions/` bind you, lessons in `.meld/memory/lessons.md` tell you what went wrong before.
- In the `planning` phase, read the goal on the coordination issue, then propose a decomposition with `meld post PROPOSE plan-<milestone> -m "..."`. Write every work item on its own line in the machine-readable form `- id: <slug> | title: <text> | worker: <type> | model: <id> | effort: <level> | size: <S|M|L>`, followed by indented `  - <acceptance criterion>` lines (for anything a user sees, criteria for visual design, performance, and security are mandatory, not optional). Other groups reply `AGREE`, `AMEND`, or `DISSENT`; converge, then the proposer runs `meld post --joint PLAN plan-<milestone>` with a `verified-by:` line naming what you checked (repository files read, `meld roster`, `meld models`, `meld recall`), and then `meld plan issues plan-<milestone>`, which creates one `meld:planned` issue per item (malformed lines are reported and skipped; fix them with an AMEND round). The human runs `meld plan approve`, which flips every planned issue to `ready`; only a verified [joint] PLAN (posted by the proposer's own bot after the round closed) is accepted.
- Mid-milestone additions go through `PROPOSE`/`AGREE` as `plan-add-<n>` in the same item form, then `meld post --joint PLAN-ADD plan-add-<n>` and `meld plan issues plan-add-<n>`; the issues become `ready` only after the human runs `meld plan ack plan-add-<n>`.
```

Add a new section before `## Chat with the human`:

```markdown
## Memory

- When a `PROPOSE <id>` round closes and the proposer has posted `meld post --joint DECISION <id>`, the proposer runs `meld decide <id>`: it writes `.meld/decisions/NNNN-<id>.md` from the agreed text and opens a memory PR. The other groups' PMs approve it with `meld review N` (hash-verified against the agreed text, no worker needed) and either PM merges.
- When a blocked, challenged, or reverted item resolves, the group that handled it runs `meld remember <ref> -m "what went wrong | what fixed it | what to check next time"`; that opens a memory PR appending to `.meld/memory/lessons.md`, approved the same way.
- `meld recall <query>` searches decisions, lessons, and past coordination issues. Run it at launch 1, before planning, and before proposing any decision that may already exist.
```

`src/meld/prompts/pm.md`: in `## Your loop` step 1, replace the tick sentence with: "`meld tick` — kills overdue workers, creates any missing lease, releases other groups' expired claims, moves your issues whose PR has red CI back to `in_progress` (once per head), flags comments from strangers as `IGNORED-COMMENT` (the alphabetically first group posts, everyone records), reports off-protocol label or assignee changes by unknown actors as `PROTOCOL-VIOLATION`, and sends the Slack nudge for needs-human items when `[notify]` is configured. Run it first, every cycle." In step 3, extend the `meld review N` bullet: "A `REVIEW-REQUEST` that says *memory PR* needs no worker: run `meld review N` directly; it verifies the file hash against the agreed text (decisions) or the item's resolution (lessons) and approves or requests changes." In step 4, extend the STATUS sentence: "…blocked, in-review counts, and `coordination.ignored_comments`."

`src/meld/prompts/_preamble.md`, "The command surface": add the bullets

```markdown
- `meld decide REF`, `meld remember REF -m "..."`, `meld recall QUERY` — decision memory (section 15): agreed decisions and lessons become files in the product repo through hash-verified memory PRs; recall before you plan or decide.
- `meld plan issues REF [--dry-run]` (proposer orchestrator), `meld plan approve` and `meld plan ack REF` (human) — the plan gate.
- `meld inbox`, `meld chat [--once TEXT]`, `meld notify [--dry-run]` — the human's console and Slack nudges; you will see what the human posts there as `[human]` comments.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_role_prompts.py tests/test_prompts.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/meld/prompts.py src/meld/prompts/orchestrator.md src/meld/prompts/pm.md src/meld/prompts/_preamble.md tests/test_role_prompts.py
git commit -m "docs: role prompts plan in the item format, keep memory, and use the human console"
```

---

### Task 11: Spec, README, and handoff reconciliation; plan-5 obligations

**Files:**
- Modify: `docs/superpowers/specs/2026-09-03-meld-design.md`, `README.md`, `docs/HANDOFF.md`, `AGENTS.md` (only if a convention changed)

**Interfaces:** documentation only; every statement must match the code on this branch (read the module before writing the sentence).

- [ ] **Step 1: Spec edits**

- §7: note the id conventions (`ask-<n>` for human asks, `dir-<n>` for directives, `plan-<milestone>`/`plan-add-<n>`/`dec-…` refs); the `IGNORED-COMMENT` row now says `meld tick` posts it (first group by name); the `PROTOCOL-VIOLATION` row mentions off-protocol changes by unknown actors.
- §8: add bullets **plan** (`plan issues`: proposer orchestrator with a verified `[joint] PLAN`/`PLAN-ADD`; `plan approve`: human, `planning` phase, verified `[joint] PLAN`; `plan ack`: human, verified `[joint] PLAN-ADD`), **decide** (proposer group's orchestrator or PM, verified `[joint] DECISION`), **remember** (orchestrator or PM; the item is on the board, not blocked/needs-human, not challenged); state that a `[joint]` comment is *verified* only when written by the proposer's bot after the round closed and that `meld status` shows `joint_verified`.
- §12: the change kinds now include `audits`; the digest carries challenge details, audits, and PR statuses (what fires `challenges`, `audits`, `pr`).
- §14 Plan gate: the commands and the item format; Chat: `meld inbox`/`meld chat` as built (slash commands, `--once`, `--verbose`); Needs-human: the notifier (`[notify]`, per-party throttle, `meld notify --dry-run`).
- §15: `meld decide`/`remember`/`recall` as built; the auto-approval rule for decisions (hash + agreed text) and for lessons (hash + resolved item, no agreement round — decision 1 of this plan); memory PRs skip worker review; briefs carry `## Relevant memory`; `skills_used` in the result file.
- §17: the "CI red" row now reads "author group's `tick` moves the issue back to in-progress once per head"; the off-protocol row reads as implemented (unknown actors, issue events, once per event).
- §21: mark landed: plan-3 obligations 1 (joint author check) and 3 (doctor stays plan 5); the "Carried into plan 4" items that landed (memory, chat, plan gate, notifier, CI red, off-protocol, `IGNORED-COMMENT`, digest gaps, `pr_for_issue`, `skills_used`). Add **"Carried into plan 5"**: `meld setup` (labels with colors, milestone, coordination issue, CODEOWNERS, branch protection with the `meld/review/*` statuses optional, starter `.meld.toml`, public-repo refusal, CI `paths` filter warning); `meld doctor` (the full checklist from §20 and the plan-4 list: CLIs, tokens and their permissions incl. Commit statuses, bot login matches, `gh` on the human, hidden `.pth`, models in `models.toml`, command-timeout ceilings, settings `env` leaks, stale PM clone, human login in `[humans]`, scratch sweep of `results/superseded`, `results/submitted`, `runs/<n>-gh`, `runs/<n>-gitconfig`, `worktrees/<group>/memory-*`); the fuller skills story (relevance-matched skills beyond title matching, a shared `.meld/skills/` synced into each adapter's directory, `skill:<name>` dispatch hints, a Codex path); leases tied to run records; worker commit messages closing issues; `-W error` ResourceWarnings; the author group's request-changes not counting toward ping-pong; `legal` not showing the author group's comment-review path; `Board.pr_for_issue` when several PRs are open for one issue; a milestone-end audit trigger.

- [ ] **Step 2: README**

Extend the verbs table with `meld decide REF`, `meld remember REF -m TEXT`, `meld recall QUERY`, `meld plan issues|approve|ack`, `meld inbox`, `meld chat [--once TEXT] [--verbose]`, `meld notify [--dry-run]`. Add sections:

```markdown
## Memory

Agreed decisions become `.meld/decisions/NNNN-<id>.md` (`meld decide`), lessons append to `.meld/memory/lessons.md` (`meld remember`), both through memory PRs that the other groups' PMs approve with `meld review N` after verifying the file hash — no reviewer worker runs. `meld recall QUERY` searches both plus past coordination issues, and dispatch renders matching snippets into every brief as `## Relevant memory`.

## The human console

`meld inbox` lists what needs a person; `meld chat` is a REPL: plain text posts `[human] ASK ask-<n>`, slash commands post the human verbs (`/halt`, `/resume`, `/directive`, `/provided`, `/resolved`, `/revert`, `/ack`, `/approve`, `/challenge`), `/wait` prints new coordination comments. With `[notify] slack_dm_webhook_env` set, each PM's `tick` sends a Slack nudge per needs-human issue and repeats after `repeat_after_minutes`.
```

- [ ] **Step 3: Handoff**

Rewrite `docs/HANDOFF.md` for this milestone: "plan 4 merged" state, the plan-4 decisions the owner should know (lesson auto-approval on evidence, `joint_verified`, memory PRs skipping worker review, the plan item format, the chat design, off-protocol scope, first-group flagging, CI-red handling, notifier throttle, digest additions, `pr_for_issue`), the new modules in the repository map, the new commands, and "What is next (plan 5)" mirroring §21's "Carried into plan 5". Keep the rest accurate (test count from `uv run pytest -q`).

- [ ] **Step 4: Verify**

Run: `uv run pytest -q` (all green, no warnings) and `uv run pytest tests/smoke -q -rs` (2 skipped).

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-09-03-meld-design.md README.md docs/HANDOFF.md AGENTS.md
git commit -m "docs: reconcile the spec, README, and handoff with plan 4; list plan-5 obligations"
```

---

## Plan 4 exit criteria

- `uv run pytest -q` passes with no network, no real CLI, and no warnings; `tests/smoke` is opt-in and unchanged.
- Against a real repository: after a `[joint] DECISION dec-1` posted by the proposer's bot, `meld decide dec-1` opens a memory PR, the other group's `meld review N` approves it without a worker, and either PM merges; `meld remember #N -m …` does the same for a resolved item; `meld recall postgres` finds both.
- During planning, the proposer's `meld plan issues plan-<m>` creates the planned issues from the item format; the human's `meld plan approve` flips them to `ready`; `meld plan ack plan-add-<n>` readies an addition.
- `meld inbox` shows needs-human, budget, disagreement, plan-addition, and ask items; `meld chat` posts `[human] ASK ask-<n>` for plain text and the human verbs for slash commands.
- With a webhook configured, `meld tick` sends one Slack message per needs-human issue and repeats only after `repeat_after_minutes`; `tick` also moves the author group's red-CI issues back to in-progress once per head, flags strangers' comments once, and reports off-protocol changes by unknown actors.
- `wait --filter challenges|audits|pr` fires on challenger changes, new audits, and status changes; a hand-written `[joint]` comment is reported as unverified and refused by `decide` and `plan`.
- `docs/HANDOFF.md` describes the post-plan-4 state so another AI can write plan 5 from the repo alone.
