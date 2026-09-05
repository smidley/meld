# Plan 3: Review, Merge, Challenge, Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the loop that plan 2 left open: a collected reviewer verdict becomes a real GitHub review (approve, request changes, or a pushed fix), `meld merge` lands PRs under the quorum rule, challenges are opened and closed through one command with the closer restrictions the spec demands, audits of merged PRs are posted and can reopen issues, every legality predicate lives in `rules.py` so `meld status` lists it, and every prompt carries the product's quality bar and the skills available to the worker.

**Architecture:** Same shape as plans 1 and 2: `github.py` gains the review, merge, status, files, and issue-state endpoints (the only GitHub path); `board.py`/`assemble.py` derive challenges, audits, PR files, and merge commits from GitHub data; `rules.py` gains `review`, `audit`, `reap`, `tag`, and challenge predicates and stays the only authority; new action modules `results.py` (verdict files), `review.py` (`review`, `merge`), `audit.py` (`audit`, `reopen`), and `challenge.py` mirror `actions.py`/`dispatch.py`; prompts and briefs gain a `## Product standards` section fed by `.meld/STANDARDS.md` with a framework default, plus a `## Skills available` list. Plan 4 (decision memory, chat, plan gate, setup, doctor, notifier) is written after this plan merges; the obligations it inherits are listed in Task 13.

**Tech Stack:** Python 3.13 (`.python-version`), standard library + click, pytest with `pythonpath = ["src"]`, `uv run`. Real git repos (bare origin + clone) and real detached subprocesses in tests; `FakeGh` for GitHub.

**Spec:** `docs/superpowers/specs/2026-09-03-meld-design.md` (sections 6, 7, 8, 13, 14, 17, 19, 21 bind this plan). Where the spec and this plan differ, the spec wins unless a decision below says otherwise.

## Decisions made while writing this plan (surface to Scott; each is reversible)

1. **Neutral check runs become commit statuses.** Spec §21 item 4 asks for a GitHub check run with a neutral conclusion. Check runs can only be created by GitHub Apps; the groups authenticate with fine-grained personal tokens, so `meld review` posts a **commit status** per group instead (`context: meld/review/<group>`, `success` for approve, `failure` for request-changes, `pending` on the new head after a push-fix). A status is visible on the PR and never blocks unless a human adds it to the required checks, which is the spec's intent. §21 is reconciled in Task 13.
2. **Role-matched review needs three or more groups.** With two groups there is one reviewer per PR, so no primary concern is assigned; with N ≥ 3 the non-author groups are sorted by name and take `correctness and security`, `tests and product conventions`, `architecture, performance, and visual design` in turn.
3. **Security reviewer runs sequentially.** A PR touching `review.security_paths` gets a second review run from the same PM with `--worker security` after the first review is submitted (run records are keyed by PR number, so two live runs on one PR are impossible without a state redesign). Each submitted verdict is one GitHub review; a later `REQUEST_CHANGES` from the same bot supersedes its earlier `APPROVE`, which is what we want.
4. **Verdict files are never overwritten.** `results/<group>/<kind>-<n>.json` stays the path plan 2 documented; a second collect on the same PR moves an unconsumed file to `superseded/`, and `meld review`/`meld audit` move a consumed file to `submitted/`. Run records gain `head_sha` (GitHub's view of the PR head at dispatch) so a verdict on a superseded head is detected as stale without comparing local git objects to API strings.
5. **Who may close a challenge.** Spec §14 says the author posts `EVIDENCE` or `RETRACT`; §21 item 2 says the challenger may `RETRACT`. Both readings are useful: the challenged party may `RETRACT` (concede the claim) and the challenger may `RETRACT` (withdraw the doubt). `UPHELD`/`DISMISSED` are for a party that is neither, which with two groups means the human. `EVIDENCE` is the challenged party's (or the human's). Task 13 reconciles §14/§21 wording.
6. **Audit checks out the merge commit.** The auditor's worktree is a detached checkout of `merge_commit_sha` (present in the pulls list payload); plan 2 checked out the head branch, which may be deleted after a squash merge.
7. **Product standards and skills in prompts** (from Scott's 2026-09-04 direction: "ready for the masses", visual design/performance/security paramount, decide with best practice and research, surface only approval-worthy decisions, use available skills). Framework default text lives in `_worker_contract.md`/`_preamble.md`; a product overrides or extends it with `.meld/STANDARDS.md`, rendered into every worker brief and both role prompts. Reviewer priority becomes correctness, security, performance, visual design and accessibility (for anything a user sees), tests, conventions.
8. **User-level skills reach workers.** The Claude adapter changes `--setting-sources project` to `--setting-sources user,project` so skills installed under `~/.claude/skills` and plugin caches (already inventoried into the roster) are actually loadable inside a worker session; hooks stay disabled and the MCP config stays empty, so the isolation regression is limited to the user's `settings.json` permissions/env, which `bypassPermissions` already makes moot. **Scott should confirm this trade-off.** The Codex adapter keeps `--ignore-user-config` unless the implementer verifies it hides `~/.codex/skills`; the finding is reported either way.

## Global Constraints

- Python 3.13 pinned by `.python-version`; runtime dependency is click only; tests use pytest with `pythonpath = ["src"]`; run everything with `uv run` from the repo root. Baseline: `uv run pytest -q` → 250 passed, no warnings; `uv run pytest tests/smoke -q -rs` → 2 skipped (never set `MELD_SMOKE=1`).
- `meld` is the only GitHub path; `github.py` is the only module that talks to GitHub; `gitops.py` the only one that runs git; `rules.py` the only legality authority ("LLM decides, Python enforces"). Every rule is a test.
- Refusals: rule-level → `actions.refuse(ctx, board, Refusal(...))` (posts `PROTOCOL-VIOLATION` for non-human callers, exit 2 via `_run_action`); usage errors → `DispatchError`/`PostError` (exit 1 or 2 as the existing commands do). Every new CLI command uses `_load` + `_run_action`, and `_guard`'s tuple must cover every exception the command can raise.
- Workers never receive GitHub credentials (`scrub_env`, `worker_env`); only PM-side code pushes (`push_branch` with the group's token through the inline credential helper). Worker text that reaches GitHub goes through `neutralise_closers`; log tails through `fenced`.
- `[joint]` is written only by `meld post --joint`; `LEASE`, `EXPIRED`, and (after Task 4) `IGNORED-COMMENT` are framework-posted; challenge verbs go through `meld challenge`, never `meld post`.
- Tests are hermetic: no network, no real CLI, no dependence on the host's `~/.claude`, gitconfig, or wall clock (inject `now`); real git repos and real subprocesses are fine and preferred over mocks. Every `load_context(...)` in tests passes `adapter_for=` (`tests.fakeadapter.no_skills` or an explicit factory).
- Commits: plain conventional messages, no attribution trailers of any kind. New resources keep plan 2's naming.
- Labels: `meld:planned|ready|in-progress|in-review|blocked|needs-human|challenged|coordination|plan-approved`, `worker:`/`model:`/`effort:`/`size:` prefixes.

## File structure

| File | Responsibility (this plan) |
|---|---|
| `src/meld/github.py` | + `get_pull`, `pull_files`, `create_review`, `merge_pull`, `create_status`, `update_issue`, `put`; `GitHubError.status` |
| `src/meld/board.py` | + `Challenge`, `derive_challenges`, `derive_audits`; `PullRequest.files/merge_commit_sha/merged_at/touches()`; `RunRecord.head_sha`; `Board.challenges/audits` |
| `src/meld/assemble.py` | fetch PR files for open PRs; carry `merge_commit_sha`/`merged_at`; derive challenges and audits |
| `src/meld/rules.py` | + `Verb.REVIEW/AUDIT/CHALLENGE`, `REAP`/`TAG` predicates, `reap_rule`, `review_concern`, `challenge_rule`, `challenged_party`; extended `ISSUE_VERBS`/`PR_VERBS` |
| `src/meld/results.py` (new) | verdict files: `store_result`, `load_result`, `mark_submitted`, `result_path` |
| `src/meld/review.py` (new) | `review(ctx, n)` and `merge(ctx, n)` |
| `src/meld/audit.py` (new) | `audit(ctx, n)`, `reopen(ctx, n)`, `audit_summary(...)` |
| `src/meld/challenge.py` (new) | `challenge(ctx, action, ref, text)` |
| `src/meld/dispatch.py` | `_dispatch_pr` uses rules verbs, `--worker`, concern, merge-commit checkout, `head_sha`; `reap`/`tag` use rules; `_collect_verdict` uses `results.py` |
| `src/meld/gitops.py` | + `checkout_sha` |
| `src/meld/brief.py` | + `product_standards`, `DEFAULT_STANDARDS`, `render_brief(..., concern, skills)` sections |
| `src/meld/prompts.py`, `prompts/*.md` | commands list, product standards section, loops for review/merge/audit/challenge, quality bar, skills |
| `src/meld/adapters/claude.py` | `--setting-sources user,project` |
| `src/meld/posting.py`, `actions.py` | `IGNORED-COMMENT` framework-only; challenge verbs redirected to `meld challenge`; claim clears a stale own lease |
| `src/meld/status.py` | pulls: `merged`, `merged_at`, `touches_security_paths`, `audited_by`; `coordination.challenges`; top-level `audit` |
| `src/meld/config.py` | `[audit] batch_merges`, `batch_hours` |
| `src/meld/cli.py` | `review`, `merge`, `audit`, `reopen`, `challenge`; `dispatch --worker` |
| tests | `test_github.py`, `test_board.py`, `test_assemble.py`, `test_results.py` (new), `test_rules_prs.py`, `test_rules_issues.py`, `test_rules_challenges.py` (new), `test_posting.py`, `test_actions.py`, `test_dispatch.py`, `test_review.py` (new), `test_merge.py` (new), `test_audit.py` (new), `test_challenge.py` (new), `test_status.py`, `test_brief.py`, `test_prompts.py`, `test_role_prompts.py`, `test_adapter_claude.py`; fixture `tests/fixtures/board1/files_20.json` |

---

### Task 1: GitHub endpoints for review, merge, statuses, files, and issue state

**Files:**
- Modify: `src/meld/github.py`
- Test: `tests/test_github.py`

**Interfaces:**
- Consumes: `GitHub._mutate(method, endpoint, body)`, `GitHub.get`, `GitHub.get_paginated`, `GitHub._r`, `tests.conftest.board1_gh` fixture (`(fake, gh)` for `acme/widgets`), `FakeGh.route(method, endpoint, body, status=200)`.
- Produces: `GitHub.put(endpoint, body)`, `get_pull(number, fresh=False) -> dict`, `pull_files(number) -> list[str]`, `create_review(number, event, body, commit_id, comments=None) -> dict` (each comment sent as `{"path", "line", "side": "RIGHT", "body"}`), `merge_pull(number, sha, title, method="squash") -> dict`, `create_status(sha, state, context, description, target_url=None) -> dict`, `update_issue(number, **fields) -> dict`; `GitHubError.status: int | None` set whenever an HTTP status caused the error.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_github.py` (keep its existing imports; add `import pytest` and `from meld.github import GitHubError` if absent):

```python
def test_review_merge_status_and_issue_endpoints(board1_gh):
    fake, gh = board1_gh
    fake.route("GET", "repos/acme/widgets/pulls/20/files", [{"filename": "ci.yml"}, {"filename": ".github/workflows/ci.yml"}])
    fake.route("POST", "repos/acme/widgets/pulls/20/reviews", {"id": 501, "state": "APPROVED"})
    fake.route("PUT", "repos/acme/widgets/pulls/20/merge", {"sha": "m123", "merged": True})
    fake.route("POST", "repos/acme/widgets/statuses/abc123", {"id": 77})
    fake.route("PATCH", "repos/acme/widgets/issues/13", {"number": 13, "state": "open"})
    fake.route("GET", "repos/acme/widgets/pulls/20", {"number": 20, "merge_commit_sha": "mmm"})
    assert gh.pull_files(20) == ["ci.yml", ".github/workflows/ci.yml"]
    assert gh.create_review(20, "APPROVE", "looks right\n\nverified-by: pytest", "abc123",
                            [{"path": "README.md", "line": 1, "body": "nit"}])["id"] == 501
    assert gh.merge_pull(20, "abc123", "CI pipeline")["sha"] == "m123"
    assert gh.create_status("abc123", "success", "meld/review/claude", "claude review: approve")["id"] == 77
    assert gh.update_issue(13, state="open")["state"] == "open"
    assert gh.get_pull(20)["merge_commit_sha"] == "mmm"
    bodies = {(c.method, c.endpoint): c.body for c in fake.calls if c.method != "GET"}
    assert bodies[("POST", "repos/acme/widgets/pulls/20/reviews")] == {
        "event": "APPROVE", "body": "looks right\n\nverified-by: pytest", "commit_id": "abc123",
        "comments": [{"path": "README.md", "line": 1, "side": "RIGHT", "body": "nit"}]}
    assert bodies[("PUT", "repos/acme/widgets/pulls/20/merge")] == {"sha": "abc123", "merge_method": "squash", "commit_title": "CI pipeline"}
    assert bodies[("POST", "repos/acme/widgets/statuses/abc123")] == {"state": "success", "context": "meld/review/claude",
                                                                      "description": "claude review: approve"}
    assert bodies[("PATCH", "repos/acme/widgets/issues/13")] == {"state": "open"}


def test_create_review_without_comments_omits_the_key(board1_gh):
    fake, gh = board1_gh
    fake.route("POST", "repos/acme/widgets/pulls/20/reviews", {"id": 502})
    gh.create_review(20, "REQUEST_CHANGES", "please fix", "abc123")
    body = [c for c in fake.calls if c.method == "POST"][-1].body
    assert "comments" not in body and body["event"] == "REQUEST_CHANGES"


def test_github_error_carries_the_http_status(board1_gh):
    fake, gh = board1_gh
    fake.route("POST", "repos/acme/widgets/pulls/20/reviews", {"message": "Validation Failed"}, status=422)
    with pytest.raises(GitHubError) as ei:
        gh.create_review(20, "APPROVE", "x", "abc123")
    assert ei.value.status == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_github.py -v`
Expected: FAIL with `AttributeError: 'GitHub' object has no attribute 'pull_files'` (and the status test fails on `.status`).

- [ ] **Step 3: Implement**

In `src/meld/github.py`: give `GitHubError` an optional status:

```python
class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status
```

Find every `raise GitHubError(f"... HTTP {status} ...")` in `_call`, `get`, `_mutate`, and `remove_label` and pass `status` as the second argument (`RateLimited` keeps its own constructor and passes `status=None` or the 403/429 it saw). Add `put` next to `post`/`patch`/`delete`:

```python
    def put(self, endpoint: str, body: dict) -> Any:
        return self._mutate("PUT", endpoint, body)
```

Append the helpers after `update_pull`:

```python
    def get_pull(self, number: int, fresh: bool = False) -> dict:
        return self.get(self._r(f"pulls/{number}"), use_cache=not fresh)

    def pull_files(self, number: int) -> list[str]:
        return [f["filename"] for f in self.get_paginated(self._r(f"pulls/{number}/files"))]

    def create_review(self, number: int, event: str, body: str, commit_id: str, comments: list[dict] | None = None) -> dict:
        """Submit one PR review. `comments` are diff comments ({path, line, body}); GitHub rejects lines outside the diff with 422."""
        payload: dict[str, Any] = {"event": event, "body": body, "commit_id": commit_id}
        if comments:
            payload["comments"] = [{"path": c["path"], "line": int(c["line"]), "side": "RIGHT", "body": c["body"]} for c in comments]
        return self.post(self._r(f"pulls/{number}/reviews"), payload)

    def merge_pull(self, number: int, sha: str, title: str, method: str = "squash") -> dict:
        """Squash-merge; `sha` makes GitHub refuse (409) if the head moved since we looked."""
        return self.put(self._r(f"pulls/{number}/merge"), {"sha": sha, "merge_method": method, "commit_title": title})

    def create_status(self, sha: str, state: str, context: str, description: str, target_url: str | None = None) -> dict:
        body: dict[str, Any] = {"state": state, "context": context, "description": description[:140]}
        if target_url:
            body["target_url"] = target_url
        return self.post(self._r(f"statuses/{sha}"), body)

    def update_issue(self, number: int, **fields: Any) -> dict:
        return self.patch(self._r(f"issues/{number}"), fields)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_github.py -v` then `uv run pytest -q`
Expected: PASS (250 + 3).

- [ ] **Step 5: Commit**

```bash
git add src/meld/github.py tests/test_github.py
git commit -m "feat: GitHub review, merge, status, files, and issue-state endpoints"
```

---

### Task 2: Board derivations for challenges, audits, PR files, and merge commits

**Files:**
- Modify: `src/meld/board.py`, `src/meld/assemble.py`, `tests/conftest.py` (route the new fixture)
- Create: `tests/fixtures/board1/files_20.json`
- Test: `tests/test_board.py`, `tests/test_assemble.py`

**Interfaces:**
- Consumes: `ParsedComment` (`.ok`, `.header.verb/ref/qualifier/party`, `.author`, `.id`, `.created_at`), `_ordered`, `GitHub.pull_files` (Task 1), `tests.factories.mk_comment/mk_pr`, `CFG2`.
- Produces: `Challenge(ref, challenger, comment_id, opened_at)` where `challenger` is a group name or `human:<login>`; `derive_challenges(comments) -> dict[str, Challenge]`; `derive_audits(comments) -> dict[int, dict[str, str]]` (PR number → group → `"pass"|"fail"`, latest wins, from `AUDIT #<pr> PASS|FAIL` by a group or its PM); `PullRequest.files: tuple[str, ...] = ()`, `merge_commit_sha: str | None = None`, `merged_at: str = ""`, `touches(patterns: Iterable[str]) -> bool` (`fnmatch` per file per pattern); `RunRecord.head_sha: str = ""`; `Board.challenges: dict[str, Challenge]` and `Board.audits: dict[int, dict[str, str]]` (both `field(default_factory=dict)`); `Board.open_challenges` is kept and equals `frozenset(board.challenges)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/fixtures/board1/files_20.json`:

```json
[{"filename": "ci.yml"}, {"filename": ".github/workflows/ci.yml"}]
```

In `tests/conftest.py`, inside `load_board1_routes`, add next to the other PR routes:

```python
    fake.route("GET", f"repos/{repo}/pulls/20/files", _fixture("files_20.json"))
```

(use whatever helper the function already uses to load fixture files; keep its style.)

Append to `tests/test_board.py`:

```python
from dataclasses import replace

from meld.board import Challenge, derive_audits, derive_challenges
from tests.factories import CFG2, mk_comment, mk_pr


def test_derive_challenges_records_challenger_and_closers():
    comments = [mk_comment(CFG2, 1, "meld-codex-bot", "[codex] CHALLENGE #12\n\nthe test claim is false"),
                mk_comment(CFG2, 2, "meld-claude-bot", "[claude] CHALLENGE plan-1\n\nscope moved"),
                mk_comment(CFG2, 3, "empowerScott", "[human] DISMISSED #12\n\nverified myself")]
    ch = derive_challenges(comments)
    assert set(ch) == {"plan-1"} and ch["plan-1"] == Challenge("plan-1", "claude", 2, comments[1].created_at)
    human = derive_challenges([mk_comment(CFG2, 4, "empowerScott", "[human] CHALLENGE #13\n\nno evidence")])
    assert human["#13"].challenger == "human:empowerScott"
    reopened = derive_challenges(comments + [mk_comment(CFG2, 5, "meld-claude-bot", "[claude] CHALLENGE #12\n\nagain")])
    assert reopened["#12"].challenger == "claude" and reopened["#12"].comment_id == 5


def test_derive_audits_keeps_the_latest_verdict_per_group():
    comments = [mk_comment(CFG2, 1, "meld-codex-bot", "[codex:pm] AUDIT #19 FAIL\n\ncriterion 2 unmet"),
                mk_comment(CFG2, 2, "meld-codex-bot", "[codex:pm] AUDIT #19 PASS\n\nre-checked after the fix"),
                mk_comment(CFG2, 3, "meld-claude-bot", "[claude] AUDIT #19 PASS\n\nok"),
                mk_comment(CFG2, 4, "meld-claude-bot", "[claude] AUDIT plan-1 PASS\n\nnot a PR ref; ignored")]
    assert derive_audits(comments) == {19: {"codex": "pass", "claude": "pass"}}


def test_pull_request_touches_security_paths():
    pr = replace(mk_pr(20, 13), files=("src/auth/login.py", "README.md"))
    assert pr.touches(("src/auth/*",)) and pr.touches(("*.md",)) and not pr.touches(("db/*",)) and not pr.touches(())
```

Append to `tests/test_assemble.py` (it already builds a board from `board1_gh`; mirror its existing test's setup):

```python
def test_board_carries_files_challenges_audits_and_merge_metadata(board1_gh):
    fake, gh = board1_gh
    board = build_board(gh, CFG, {}, {}, milestone=3, now="2026-09-03T12:00:00Z")
    assert board.pulls[20].files == ("ci.yml", ".github/workflows/ci.yml")
    assert board.pulls[19].merged and board.pulls[19].merged_at and board.pulls[19].files == ()   # merged PRs skip the files fetch
    assert board.challenges["#12"].challenger == "codex" and board.open_challenges == frozenset({"#12"})
    assert board.audits == {}
```

(`CFG` is whatever config object `tests/test_assemble.py` already uses for `build_board`; read the file and reuse it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_board.py tests/test_assemble.py -v`
Expected: FAIL with `ImportError: cannot import name 'Challenge'`.

- [ ] **Step 3: Implement `board.py`**

Add `from fnmatch import fnmatch` to the imports. Extend `PullRequest` (after `merged: bool`):

```python
    files: tuple[str, ...] = ()
    merge_commit_sha: str | None = None
    merged_at: str = ""

    def touches(self, patterns: Iterable[str]) -> bool:
        """True when any changed file matches any glob (fnmatch: `*` also crosses `/`)."""
        return any(fnmatch(f, p) for f in self.files for p in patterns)
```

Extend `RunRecord` with `head_sha: str = ""` after `timed_out`. Add after `Lease`:

```python
@dataclass(frozen=True)
class Challenge:
    ref: str
    challenger: str        # a group name, or "human:<login>"
    comment_id: int
    opened_at: str
```

Add to `Board` (after `now: str = ""`):

```python
    challenges: dict[str, Challenge] = field(default_factory=dict)
    audits: dict[int, dict[str, str]] = field(default_factory=dict)   # PR number -> group -> "pass" | "fail"
```

Replace `derive_open_challenges` with the pair below (keep the old name as a thin wrapper so existing callers and tests keep working):

```python
def _challenger(c: ParsedComment) -> str:
    return c.header.party.group if c.header is not None and c.header.party.group else f"human:{c.author}"


def derive_challenges(comments: Sequence[ParsedComment]) -> dict[str, Challenge]:
    """Open challenges by ref: CHALLENGE opens (a later CHALLENGE on the same ref replaces it), UPHELD/DISMISSED/RETRACT close."""
    open_: dict[str, Challenge] = {}
    for c in _ordered(comments):
        if not c.ok or c.header is None or not c.header.ref:
            continue
        if c.header.verb == "CHALLENGE":
            open_[c.header.ref] = Challenge(c.header.ref, _challenger(c), c.id, c.created_at)
        elif c.header.verb in ("UPHELD", "DISMISSED", "RETRACT"):
            open_.pop(c.header.ref, None)
    return open_


def derive_open_challenges(comments: Sequence[ParsedComment]) -> frozenset[str]:
    return frozenset(derive_challenges(comments))


def derive_audits(comments: Sequence[ParsedComment]) -> dict[int, dict[str, str]]:
    """AUDIT #<pr> PASS|FAIL by a group (orchestrator or PM): the latest verdict per group per PR."""
    audits: dict[int, dict[str, str]] = {}
    for c in _ordered(comments):
        h = c.header
        if not c.ok or h is None or h.verb != "AUDIT" or not h.party.group or not h.ref or not h.ref.startswith("#") or not h.ref[1:].isdigit():
            continue
        if h.qualifier in ("PASS", "FAIL"):
            audits.setdefault(int(h.ref[1:]), {})[h.party.group] = h.qualifier.lower()
    return audits
```

- [ ] **Step 4: Implement `assemble.py`**

In `_pull`, fetch files only for open PRs and carry the merge metadata:

```python
    files: tuple[str, ...] = ()
    if state == "open":
        ...  # existing reviews / commits / checks block
        files = tuple(gh.pull_files(number))
    return PullRequest(
        ..., state=state, merged=merged,
        files=files, merge_commit_sha=raw.get("merge_commit_sha"), merged_at=raw.get("merged_at") or "",
    )
```

In `build_board`, compute `challenges = derive_challenges(comments)` once and pass `open_challenges=frozenset(challenges), challenges=challenges, audits=derive_audits(comments)` to `Board(...)`. Import `derive_audits, derive_challenges` from `meld.board`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_board.py tests/test_assemble.py -v` then `uv run pytest -q`
Expected: PASS. Any existing test that constructs `PullRequest(...)` positionally must still pass (all new fields have defaults).

- [ ] **Step 6: Commit**

```bash
git add src/meld/board.py src/meld/assemble.py tests/conftest.py tests/fixtures/board1/files_20.json tests/test_board.py tests/test_assemble.py
git commit -m "feat: board derives challenges, audits, PR files, and merge commits"
```

---

### Task 3: Verdict files that are never overwritten (`results.py`)

**Files:**
- Create: `src/meld/results.py`
- Modify: `src/meld/dispatch.py` (`_collect_verdict`, `_dispatch_pr` records `head_sha`)
- Test: `tests/test_results.py` (new), `tests/test_collect.py`

**Interfaces:**
- Consumes: `meld.atomic.write_atomic(path, text)`, `RunRecord.head_sha` (Task 2), `PullRequest.head_sha`.
- Produces: `results.ResultsError(ValueError)`; `results_dir(home, group) -> Path` (`home/results/<group>`); `result_path(home, group, kind, number) -> Path` (`<kind>-<number>.json`); `store_result(home, group, kind, number, payload, now_iso) -> Path` (an existing unconsumed file is moved to `superseded/<kind>-<n>-<stamp>[-k].json` first); `load_result(home, group, kind, number) -> dict | None` (raises `ResultsError` on malformed JSON); `mark_submitted(home, group, kind, number, extra, now_iso) -> Path` (merges `extra` plus `submitted_at`, writes `submitted/<kind>-<n>-<head7>-<stamp>[-k].json`, removes the original; `head7` is the first seven characters of the payload's `head_sha` or `nohead`). `_collect_verdict`'s payload gains `"worktree_sha"` (the local checkout's HEAD) while `"head_sha"` becomes the run record's GitHub head sha (falling back to the worktree sha when the record has none).

- [ ] **Step 1: Write the failing tests**

`tests/test_results.py`:

```python
import json

import pytest

from meld.results import ResultsError, load_result, mark_submitted, result_path, store_result


def test_store_result_never_overwrites_an_unconsumed_verdict(tmp_path):
    p1 = store_result(tmp_path, "claude", "review", 20, {"result": {"verdict": "approve"}}, "2026-09-04T10:00:00Z")
    p2 = store_result(tmp_path, "claude", "review", 20, {"result": {"verdict": "request-changes"}}, "2026-09-04T11:00:00Z")
    assert p1 == p2 == tmp_path / "results" / "claude" / "review-20.json"
    assert load_result(tmp_path, "claude", "review", 20)["result"]["verdict"] == "request-changes"
    aside = sorted((tmp_path / "results" / "claude" / "superseded").glob("review-20-*.json"))
    assert [a.name for a in aside] == ["review-20-20260904T110000Z.json"]
    assert json.loads(aside[0].read_text())["result"]["verdict"] == "approve"
    store_result(tmp_path, "claude", "review", 20, {"result": {"verdict": "approve"}}, "2026-09-04T11:00:00Z")
    assert sorted(a.name for a in (tmp_path / "results" / "claude" / "superseded").iterdir()) == \
        ["review-20-20260904T110000Z-1.json", "review-20-20260904T110000Z.json"]


def test_mark_submitted_moves_the_file_and_records_what_happened(tmp_path):
    store_result(tmp_path, "claude", "review", 20, {"head_sha": "abc1234567", "result": {"verdict": "approve"}}, "2026-09-04T10:00:00Z")
    moved = mark_submitted(tmp_path, "claude", "review", 20, {"event": "APPROVE", "review_id": 501}, "2026-09-04T10:05:00Z")
    assert moved == tmp_path / "results" / "claude" / "submitted" / "review-20-abc1234-20260904T100500Z.json"
    assert load_result(tmp_path, "claude", "review", 20) is None
    data = json.loads(moved.read_text())
    assert data["event"] == "APPROVE" and data["review_id"] == 501 and data["submitted_at"] == "2026-09-04T10:05:00Z"
    assert data["result"]["verdict"] == "approve"
    with pytest.raises(ResultsError, match="no collected review result"):
        mark_submitted(tmp_path, "claude", "review", 20, {}, "2026-09-04T10:06:00Z")


def test_load_result_rejects_malformed_json(tmp_path):
    p = result_path(tmp_path, "claude", "audit", 19)
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    with pytest.raises(ResultsError):
        load_result(tmp_path, "claude", "audit", 19)
    assert load_result(tmp_path, "claude", "audit", 18) is None
```

In `tests/test_collect.py::test_collect_review_run_stores_the_verdict`, extend the assertions:

```python
    assert data["head_sha"] == "abc123" and len(data["worktree_sha"]) == 40      # GitHub's head at dispatch; the checkout's HEAD
    rec = json.loads((tmp_path / "home" / "runs" / "claude" / "20.json").read_text())
    assert rec["head_sha"] == "abc123"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_results.py tests/test_collect.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.results'`.

- [ ] **Step 3: Write `src/meld/results.py`**

```python
"""Verdict files: what a collected review or audit run produced, kept until `meld review`/`meld audit` consumes it.

Layout under $MELD_HOME/results/<group>/:
  <kind>-<n>.json                      the unconsumed verdict for PR n (at most one)
  superseded/<kind>-<n>-<stamp>.json   an unconsumed verdict displaced by a newer collect
  submitted/<kind>-<n>-<head7>-<stamp>.json   a consumed verdict plus what meld did with it
"""
from __future__ import annotations

import json
from pathlib import Path

from meld.atomic import write_atomic


class ResultsError(ValueError):
    pass


def results_dir(home: Path, group: str) -> Path:
    return home / "results" / group


def result_path(home: Path, group: str, kind: str, number: int) -> Path:
    return results_dir(home, group) / f"{kind}-{number}.json"


def _stamp(now_iso: str) -> str:
    return now_iso.replace("-", "").replace(":", "")


def _unique(path: Path) -> Path:
    candidate, k = path, 0
    while candidate.exists():
        k += 1
        candidate = path.with_name(f"{path.stem}-{k}{path.suffix}")
    return candidate


def store_result(home: Path, group: str, kind: str, number: int, payload: dict, now_iso: str) -> Path:
    path = result_path(home, group, kind, number)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():  # never overwrite a verdict nobody has acted on
        aside = _unique(path.parent / "superseded" / f"{kind}-{number}-{_stamp(now_iso)}.json")
        aside.parent.mkdir(parents=True, exist_ok=True)
        path.rename(aside)
    write_atomic(path, json.dumps(payload, indent=2))
    return path


def load_result(home: Path, group: str, kind: str, number: int) -> dict | None:
    path = result_path(home, group, kind, number)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ResultsError(f"{path}: malformed verdict file: {e}") from e
    if not isinstance(data, dict):
        raise ResultsError(f"{path}: verdict file must hold a JSON object")
    return data


def mark_submitted(home: Path, group: str, kind: str, number: int, extra: dict, now_iso: str) -> Path:
    data = load_result(home, group, kind, number)
    if data is None:
        raise ResultsError(f"no collected {kind} result for #{number} in {group}")
    data = {**data, **extra, "submitted_at": now_iso}
    head7 = (data.get("head_sha") or "nohead")[:7]
    target = _unique(results_dir(home, group) / "submitted" / f"{kind}-{number}-{head7}-{_stamp(now_iso)}.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(target, json.dumps(data, indent=2))
    result_path(home, group, kind, number).unlink()
    return target
```

- [ ] **Step 4: Wire `dispatch.py`**

Import `from meld.results import store_result`. In `_dispatch_pr`, add `head_sha=pr.head_sha` to the `RunRecord(...)` constructor. Replace the body of `_collect_verdict`:

```python
def _collect_verdict(ctx, group: GroupConfig, rec: RunRecord, when: datetime) -> dict:
    result, reason = _read_outcome(rec)
    wt = Path(rec.worktree)
    worktree_sha = head_sha(wt) if wt.exists() else ""
    payload = {"kind": rec.kind, "pr": rec.pr, "head_sha": rec.head_sha or worktree_sha, "worktree_sha": worktree_sha,
               "collected_at": _iso(when), "timed_out": rec.timed_out, "reason": reason,
               "result": asdict(result) if result else None,
               "log_tail": tail_lines(Path(rec.log_path), ctx.cfg.worker_log_tail_lines)}
    path = store_result(ctx.store.home, group.name, rec.kind, rec.issue, payload, _iso(when))
    ctx.store.write_run(replace(rec, collected=True))
    return {"collected": rec.issue, "kind": rec.kind, "stored": str(path), "verdict": result.verdict if result else None}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_results.py tests/test_collect.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/meld/results.py src/meld/dispatch.py tests/test_results.py tests/test_collect.py
git commit -m "feat: verdict files are stored, superseded, and submitted without ever being overwritten"
```

---

### Task 4: Every legality predicate lives in `rules.py`

**Files:**
- Modify: `src/meld/rules.py`, `src/meld/posting.py`, `src/meld/actions.py`
- Test: `tests/test_rules_prs.py`, `tests/test_rules_issues.py`, `tests/test_rules_challenges.py` (new), `tests/test_posting.py`, `tests/test_actions.py`

**Interfaces:**
- Consumes: `Board.challenges/audits/runs/leases/comments`, `Challenge`, `grammar.agreement_for`, `board.derive_leases`, `grammar.compose/Header`, `config.Party`.
- Produces in `rules.py`: `Verb.REVIEW = "review"`, `Verb.AUDIT = "audit"`, `Verb.CHALLENGE = "challenge"`; `ISSUE_VERBS = (CLAIM, RELEASE, DISPATCH, COLLECT, REOPEN, TAG, REAP)`; `PR_VERBS = (APPROVE, REQUEST_CHANGES, PUSH_FIX, MERGE, REVIEW, AUDIT, REAP)`; `reap_rule(board, cfg, actor, number, issue, pr) -> str | None`; `review_concern(cfg, pr, group) -> str | None` with `CONCERNS`; `CHALLENGE_ACTIONS = ("open", "evidence", "retract", "uphold", "dismiss")`; `challenged_party(board, cfg, ref) -> str | None`; `challenge_rule(board, cfg, actor, action, ref) -> str | None`. `pr_refusal` handles `REAP` and `AUDIT` before the "PR is not open" gate. In `posting.py`: `FRAMEWORK_ONLY_VERBS` gains `IGNORED-COMMENT`; `COMMAND_VERBS = {"CHALLENGE", "EVIDENCE", "RETRACT", "UPHELD", "DISMISSED"}` raise `PostError("... use meld challenge ...")`. In `actions.py`: a successful PM `claim` posts `[<g>:pm] EXPIRED #n` when a stale lease by the same group still stands on the thread.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_rules_prs.py` (it defines `prule`/actors like `tests/test_rules_issues.py`; if the helper names differ, use the file's own):

```python
from dataclasses import replace

from meld.rules import CONCERNS, review_concern
from tests.factories import CFG3, mk_run


def test_review_dispatch_legality():
    pr = mk_pr(20, 13, committer="meld-codex-bot")
    b = mk_board(CFG2, pulls=[pr])
    assert prule(b, CLAUDE_PM, Verb.REVIEW, pr) is None
    assert "only other parties may review" in prule(b, CODEX_PM, Verb.REVIEW, pr)
    assert prule(b, CLAUDE_ORCH, Verb.REVIEW, pr) == "only the PM may dispatch a review"
    assert prule(mk_board(CFG2, pulls=[pr], halted=True), CLAUDE_PM, Verb.REVIEW, pr) == "HALT is in effect"
    assert prule(mk_board(CFG2, pulls=[pr], open_challenges=frozenset({"#20"})), CLAUDE_PM, Verb.REVIEW, pr) == "PR has an open CHALLENGE"
    unknown = mk_pr(21, 14, committer=None)   # unknown head committer: a review may be dispatched; approve is refused later
    assert prule(mk_board(CFG2, pulls=[unknown]), CLAUDE_PM, Verb.REVIEW, unknown) is None
    busy = mk_board(CFG2, pulls=[pr], runs={"claude": {n: mk_run("claude", n, kind="review", pr=n) for n in range(30, 34)}})
    assert prule(busy, CLAUDE_PM, Verb.REVIEW, pr) == "claude is at its review concurrency cap (4/4)"
    waiting = mk_board(CFG2, pulls=[pr], runs={"claude": {20: mk_run("claude", 20, alive=False, collected=False, kind="review", pr=20)}})
    assert prule(waiting, CLAUDE_PM, Verb.REVIEW, pr) == "a run already exists for this PR (running or uncollected)"
    assert "review" in legal_pr_verbs(b, CFG2, CLAUDE_PM, pr) and "review" not in legal_pr_verbs(b, CFG2, CODEX_PM, pr)


def test_audit_dispatch_legality():
    merged = mk_pr(19, 9, committer="meld-claude-bot", state="closed", merged=True)
    b = mk_board(CFG2, pulls=[merged])
    assert prule(b, CODEX_PM, Verb.AUDIT, merged) is None
    assert prule(b, CLAUDE_PM, Verb.AUDIT, merged) == "cannot audit a PR authored by own group"
    assert prule(b, CODEX_ORCH, Verb.AUDIT, merged) == "only the PM may dispatch an audit"
    open_pr = mk_pr(20, 13)
    assert prule(mk_board(CFG2, pulls=[open_pr]), CODEX_PM, Verb.AUDIT, open_pr) == "audit targets a merged PR"
    assert prule(replace(b, audits={19: {"codex": "pass"}}), CODEX_PM, Verb.AUDIT, merged) == "codex already audited #19"
    assert prule(mk_board(CFG2, pulls=[merged], halted=True), CODEX_PM, Verb.AUDIT, merged) == "HALT is in effect"
    assert legal_pr_verbs(b, CFG2, CODEX_PM, merged) == ["audit"]     # a merged PR accepts nothing else


def test_reap_legality_for_pr_runs():
    pr = mk_pr(20, 13)
    collected = mk_board(CFG2, pulls=[pr], runs={"claude": {20: mk_run("claude", 20, alive=False, collected=True, kind="review", pr=20)}})
    assert prule(collected, CLAUDE_PM, Verb.REAP, pr) is None          # a collected review run is reapable while the PR is open
    assert prule(mk_board(CFG2, pulls=[pr]), CLAUDE_PM, Verb.REAP, pr) == "no run record for #20 in claude"
    assert prule(collected, CLAUDE_ORCH, Verb.REAP, pr) == "only the PM may reap"


def test_review_concern_is_assigned_only_with_three_or_more_groups():
    pr = mk_pr(20, 13, committer="meld-codex-bot")
    assert review_concern(CFG2, pr, "claude") is None
    assert review_concern(CFG3, pr, "claude") == CONCERNS[0] and review_concern(CFG3, pr, "gemini") == CONCERNS[1]
    assert review_concern(CFG3, pr, "codex") is None                    # the head committer's group does not review
```

`CODEX_ORCH` may not exist in that file yet; define it beside the other actors as `Actor(Party("group", "codex"), "meld-codex-bot", ROSTER)`.

Append to `tests/test_rules_issues.py`:

```python
def test_reap_legality_for_build_runs():
    rec = mk_run("claude", 11, alive=False, collected=True)
    open_issue = mk_issue(11, status="in_progress", assignees=("meld-claude-bot",))
    assert rule(mk_board(CFG2, issues=[open_issue], runs={"claude": {11: rec}}), CLAUDE_PM, Verb.REAP, open_issue) == \
        "issue is still open and its PR is not merged"
    merged = mk_pr(21, 11, committer="meld-claude-bot", state="closed", merged=True)
    assert rule(mk_board(CFG2, issues=[open_issue], pulls=[merged], runs={"claude": {11: rec}}), CLAUDE_PM, Verb.REAP, open_issue) is None
    closed = mk_issue(11, state="closed", assignees=("meld-claude-bot",))
    b = mk_board(CFG2, issues=[closed], runs={"claude": {11: rec}})
    assert rule(b, CLAUDE_PM, Verb.REAP, closed) is None
    assert rule(mk_board(CFG2, issues=[closed]), CLAUDE_PM, Verb.REAP, closed) == "no run record for #11 in claude"
    uncollected = mk_board(CFG2, issues=[closed], runs={"claude": {11: mk_run("claude", 11, alive=False, collected=False)}})
    assert rule(uncollected, CLAUDE_PM, Verb.REAP, closed) == "run has not been collected; collect first"
    assert rule(b, CLAUDE_ORCH, Verb.REAP, closed) == "only the PM may reap"
    assert "reap" in legal_issue_verbs(b, CFG2, CLAUDE_PM, closed, REG)


def test_tag_legality():
    i = mk_issue(11)
    b = mk_board(CFG2, issues=[i])
    assert rule(b, CLAUDE_ORCH, Verb.TAG, i) is None and rule(b, HUMAN, Verb.TAG, i) is None
    assert rule(b, CLAUDE_PM, Verb.TAG, i) == "only the orchestrator or a human may tag"
    closed = mk_issue(12, state="closed")
    assert rule(mk_board(CFG2, issues=[closed]), CLAUDE_ORCH, Verb.TAG, closed) == "issue is closed"
    assert "tag" in legal_issue_verbs(b, CFG2, CLAUDE_ORCH, i, REG) and "tag" not in legal_issue_verbs(b, CFG2, CLAUDE_PM, i, REG)
```

Create `tests/test_rules_challenges.py`:

```python
from dataclasses import replace

from meld.board import Challenge
from meld.config import Party
from meld.rules import Actor, challenge_rule, challenged_party
from tests.factories import CFG2, CFG3, mk_board, mk_comment, mk_issue, mk_pr

ROSTER = frozenset({"backend", "reviewer", "auditor"})
CLAUDE_ORCH = Actor(Party("group", "claude"), "meld-claude-bot", ROSTER)
CODEX_ORCH = Actor(Party("group", "codex"), "meld-codex-bot", ROSTER)
GEMINI_ORCH = Actor(Party("group", "gemini"), "meld-gemini-bot", ROSTER)
CLAUDE_PM = Actor(Party("pm", "claude"), "meld-claude-bot", ROSTER)
HUMAN = Actor(Party("human"), "empowerScott")
OPEN = Challenge("#20", "claude", 500, "2026-09-03T10:00:00Z")


def with_challenge(board, ch=OPEN):
    return replace(board, challenges={ch.ref: ch}, open_challenges=frozenset({ch.ref}))


def test_challenged_party_resolution():
    pr = mk_pr(20, 13, committer="meld-codex-bot", author="meld-claude-bot")
    issue = mk_issue(12, status="in_progress", assignees=("meld-claude-bot",))
    proposal = mk_comment(CFG2, 1, "meld-codex-bot", "[codex] PROPOSE plan-1\n\nsplit the work")
    b = mk_board(CFG2, issues=[issue], pulls=[pr], comments=[proposal])
    assert challenged_party(b, CFG2, "#20") == "codex"       # the head committer's party answers for a PR
    assert challenged_party(b, CFG2, "#12") == "claude"      # the assignee's group answers for an issue
    assert challenged_party(b, CFG2, "plan-1") == "codex"    # the proposer answers for an agreement ref
    assert challenged_party(b, CFG2, "ask-9") is None
    unknown = mk_pr(21, 14, committer=None, author="meld-claude-bot")
    assert challenged_party(mk_board(CFG2, pulls=[unknown]), CFG2, "#21") == "claude"   # falls back to the PR author


def test_challenge_open_rules():
    pr = mk_pr(20, 13, committer="meld-codex-bot")
    b = mk_board(CFG2, pulls=[pr])
    assert challenge_rule(b, CFG2, CLAUDE_ORCH, "open", "#20") is None
    assert challenge_rule(b, CFG2, HUMAN, "open", "#20") is None
    assert challenge_rule(b, CFG2, CODEX_ORCH, "open", "#20") == "a party cannot challenge its own claim"
    assert challenge_rule(b, CFG2, CLAUDE_PM, "open", "#20") == "only an orchestrator or a human may act on challenges"
    assert challenge_rule(with_challenge(b), CFG2, HUMAN, "open", "#20") == "#20 already has an open CHALLENGE by claude"
    assert challenge_rule(b, CFG2, HUMAN, "bogus", "#20") == "unknown challenge action 'bogus'"


def test_challenge_closers_are_restricted():
    pr = mk_pr(20, 13, committer="meld-codex-bot")
    b = with_challenge(mk_board(CFG2, pulls=[pr]))
    assert challenge_rule(b, CFG2, CODEX_ORCH, "evidence", "#20") is None                     # the challenged party answers
    assert challenge_rule(b, CFG2, CLAUDE_ORCH, "evidence", "#20") == "only the challenged party (codex) or a human may post EVIDENCE"
    assert challenge_rule(b, CFG2, CLAUDE_ORCH, "retract", "#20") is None                     # the challenger withdraws
    assert challenge_rule(b, CFG2, CODEX_ORCH, "retract", "#20") is None                      # or the challenged party concedes
    assert challenge_rule(b, CFG2, CLAUDE_ORCH, "uphold", "#20") == "the challenger cannot decide its own challenge"
    assert challenge_rule(b, CFG2, CODEX_ORCH, "dismiss", "#20") == "the challenged party cannot decide the challenge against it"
    assert challenge_rule(b, CFG2, HUMAN, "uphold", "#20") is None                            # with two groups the human adjudicates
    b3 = with_challenge(mk_board(CFG3, pulls=[pr]))
    assert challenge_rule(b3, CFG3, GEMINI_ORCH, "dismiss", "#20") is None                    # a third group may decide
    assert challenge_rule(b3, CFG3, GEMINI_ORCH, "retract", "#20") == "only the challenger (claude) or the challenged party (codex) or a human may RETRACT"
    assert challenge_rule(b, CFG2, HUMAN, "uphold", "#21") == "#21 has no open CHALLENGE"
```

Append to `tests/test_posting.py` (reuse its existing `make`/context helper):

```python
def test_framework_and_command_verbs_are_refused_by_post(tmp_path):
    fake, ctx = make(tmp_path, role="pm")
    with pytest.raises(PostError, match="written by meld itself"):
        post(ctx, "IGNORED-COMMENT", text="x")
    fake, orch = make(tmp_path / "o", role="orchestrator")
    with pytest.raises(PostError, match="meld challenge"):
        post(orch, "CHALLENGE", ref="#12", text="doubt")
    with pytest.raises(PostError, match="meld challenge"):
        post(orch, "UPHELD", ref="#12", text="yes")
```

Append to `tests/test_actions.py` (reuse its context helper; route `POST repos/acme/widgets/issues/10/comments` → `{"id": 900}` and the assignee/label routes the existing claim test uses):

```python
def test_pm_claim_clears_its_own_stale_lease(tmp_path):
    fake, ctx = make(tmp_path)   # claude PM
    stale = {"id": 105, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:00:00Z",
             "body": "[claude:pm] LEASE #11\n\nuntil: 2020-01-01T00:00:00Z\nrun: claude/#11\nkind: build"}
    fake.route("GET", R + "issues/10/comments", fixture_comments() + [stale])
    # Copy, verbatim, the assignee/label routes and the fresh-issue re-read route from the existing successful-claim test
    # in this file so that claim(ctx, 11) succeeds here exactly as it does there.
    claim(ctx, 11)
    posted = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]
    assert posted and posted[-1].startswith("[claude:pm] EXPIRED #11\n")
```

(`fixture_comments()` stands for however `test_actions.py` loads `comments_10.json`; the routes the successful-claim test already uses must be copied so the claim succeeds. A claim by a human or with no stale own lease must post nothing — add that assertion to the existing successful-claim test.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rules_prs.py tests/test_rules_issues.py tests/test_rules_challenges.py tests/test_posting.py tests/test_actions.py -v`
Expected: FAIL (`Verb` has no attribute `REVIEW`, `cannot import name 'challenge_rule'`).

- [ ] **Step 3: Implement `rules.py`**

Add the imports `from meld.board import Board, Issue, PullRequest` (existing) plus `from meld.grammar import agreement_for`. Extend the enum and verb tuples:

```python
class Verb(str, Enum):
    ...existing members...
    REVIEW = "review"        # dispatch a review run on a PR
    AUDIT = "audit"          # dispatch an audit run on a merged PR
    CHALLENGE = "challenge"  # open or act on a CHALLENGE (see challenge_rule)


ISSUE_VERBS = (Verb.CLAIM, Verb.RELEASE, Verb.DISPATCH, Verb.COLLECT, Verb.REOPEN, Verb.TAG, Verb.REAP)
PR_VERBS = (Verb.APPROVE, Verb.REQUEST_CHANGES, Verb.PUSH_FIX, Verb.MERGE, Verb.REVIEW, Verb.AUDIT, Verb.REAP)
```

Add the issue predicates after `_reopen`:

```python
def _tag(board: Board, cfg: Config, actor: Actor, issue: Issue) -> str | None:
    if not (actor.is_human or actor.party.kind == "group"):
        return "only the orchestrator or a human may tag"
    if issue.state != "open":
        return "issue is closed"
    return None


def reap_rule(board: Board, cfg: Config, actor: Actor, number: int, issue: Issue | None, pr: PullRequest | None) -> str | None:
    """A collected, dead run may be reaped once its work is over: a build once its issue is closed or its PR merged/closed;
    a review or audit run as soon as it is collected."""
    if not actor.is_pm:
        return "only the PM may reap"
    run = board.run_for(actor.group or "", number)
    if run is None:
        return f"no run record for #{number} in {actor.group}"
    if run.alive:
        return "worker is still running"
    if not run.collected:
        return "run has not been collected; collect first"
    if run.kind != "build":
        return None
    linked = board.pr_for_issue(number) if issue is not None else pr
    done = (issue is not None and issue.state == "closed") or (linked is not None and (linked.merged or linked.state == "closed"))
    return None if done else "issue is still open and its PR is not merged"
```

Extend `issue_refusal`:

```python
    elif verb == Verb.TAG:
        rule = _tag(board, cfg, actor, issue)
    elif verb == Verb.REAP:
        rule = reap_rule(board, cfg, actor, issue.number, issue, None)
```

Add the PR predicates after `_merge`:

```python
def _run_slot(board: Board, cfg: Config, actor: Actor, number: int) -> str | None:
    group = cfg.groups[actor.group]  # type: ignore[index]
    live = [r for r in board.runs.get(group.name, {}).values() if r.alive and r.kind in ("review", "audit")]
    if len(live) >= group.review_concurrency:
        return f"{group.name} is at its review concurrency cap ({len(live)}/{group.review_concurrency})"
    existing = board.run_for(group.name, number)
    if existing is not None and (existing.alive or not existing.collected):
        return "a run already exists for this PR (running or uncollected)"
    return None


def _review(board: Board, cfg: Config, actor: Actor, pr: PullRequest) -> str | None:
    if board.halted:
        return "HALT is in effect"
    if not actor.is_pm:
        return "only the PM may dispatch a review"
    blocker = _other_party_only(cfg, actor, pr, Verb.REVIEW)   # the head committer's group never reviews its own head
    if blocker:
        return blocker
    if board.is_challenged(f"#{pr.number}"):
        return "PR has an open CHALLENGE"
    return _run_slot(board, cfg, actor, pr.number)


def _audit(board: Board, cfg: Config, actor: Actor, pr: PullRequest) -> str | None:
    if board.halted:
        return "HALT is in effect"
    if not actor.is_pm:
        return "only the PM may dispatch an audit"
    if not pr.merged:
        return "audit targets a merged PR"
    if party_of_login(cfg, pr.author) == actor.group:
        return "cannot audit a PR authored by own group"
    if actor.group in board.audits.get(pr.number, {}):
        return f"{actor.group} already audited #{pr.number}"
    return _run_slot(board, cfg, actor, pr.number)


CONCERNS = ("correctness and security", "tests and product conventions", "architecture, performance, and visual design")


def review_concern(cfg: Config, pr: PullRequest, group: str) -> str | None:
    """Role-matched review (spec 21.3): with three or more groups, each reviewing group takes a different primary concern."""
    excluded = {committer_party(cfg, pr), party_of_login(cfg, pr.author)}
    reviewers = sorted(g.name for g in cfg.groups.values() if g.name not in excluded)
    if len(reviewers) < 2 or group not in reviewers:
        return None
    return CONCERNS[reviewers.index(group) % len(CONCERNS)]
```

Rewrite `pr_refusal` so merged PRs accept `audit` and `reap` while everything else still requires an open PR:

```python
def pr_refusal(board: Board, cfg: Config, actor: Actor, verb: Verb, pr: PullRequest) -> Refusal | None:
    if verb == Verb.REAP:
        rule: str | None = reap_rule(board, cfg, actor, pr.number, None, pr)
    elif verb == Verb.AUDIT:
        rule = _audit(board, cfg, actor, pr)
    elif pr.state != "open":
        rule = "PR is not open"
    elif verb == Verb.APPROVE:
        rule = _approve(board, cfg, actor, pr)
    elif verb in (Verb.REQUEST_CHANGES, Verb.PUSH_FIX):
        rule = _other_party_only(cfg, actor, pr, verb)
    elif verb == Verb.MERGE:
        rule = _merge(board, cfg, actor, pr)
    elif verb == Verb.REVIEW:
        rule = _review(board, cfg, actor, pr)
    else:
        rule = f"{verb.value} is not a PR verb"
    return Refusal(verb, f"#{pr.number}", rule) if rule else None
```

Append the challenge rules:

```python
# ---- challenges ------------------------------------------------------------------

CHALLENGE_ACTIONS = ("open", "evidence", "retract", "uphold", "dismiss")


def challenged_party(board: Board, cfg: Config, ref: str) -> str | None:
    """Who must answer a challenge on `ref`: a PR's head committer's party (else its author's), an issue's first
    configured assignee's party, or an agreement ref's proposer. None when nobody owns the claim."""
    if ref.startswith("#") and ref[1:].isdigit():
        n = int(ref[1:])
        if n in board.pulls:
            pr = board.pulls[n]
            return committer_party(cfg, pr) or party_of_login(cfg, pr.author)
        if n in board.issues:
            for login in board.issues[n].assignees:
                party = party_of_login(cfg, login)
                if party:
                    return party
            return None
    return agreement_for(board.comments, ref, cfg).proposer


def challenge_rule(board: Board, cfg: Config, actor: Actor, action: str, ref: str) -> str | None:
    if action not in CHALLENGE_ACTIONS:
        return f"unknown challenge action '{action}'"
    if not (actor.is_human or actor.party.kind == "group"):
        return "only an orchestrator or a human may act on challenges"
    current = board.challenges.get(ref)
    target = challenged_party(board, cfg, ref)
    if action == "open":
        if current is not None:
            return f"{ref} already has an open CHALLENGE by {current.challenger}"
        if target is not None and target == actor.name:
            return "a party cannot challenge its own claim"
        return None
    if current is None:
        return f"{ref} has no open CHALLENGE"
    if action == "evidence":
        if actor.is_human or target is None or actor.name == target:
            return None
        return f"only the challenged party ({target}) or a human may post EVIDENCE"
    if action == "retract":
        if actor.is_human or actor.name == current.challenger or (target is not None and actor.name == target):
            return None
        return f"only the challenger ({current.challenger}) or the challenged party ({target}) or a human may RETRACT"
    if actor.name == current.challenger:
        return "the challenger cannot decide its own challenge"
    if target is not None and actor.name == target:
        return "the challenged party cannot decide the challenge against it"
    return None
```

- [ ] **Step 4: `posting.py` and `actions.py`**

In `posting.py`:

```python
FRAMEWORK_ONLY_VERBS = frozenset({"LEASE", "EXPIRED", "IGNORED-COMMENT"})
# Challenge verbs carry a label change and closer rules; `meld challenge` applies them, `meld post` refuses them.
COMMAND_VERBS = frozenset({"CHALLENGE", "EVIDENCE", "RETRACT", "UPHELD", "DISMISSED"})
```

and, right after the `FRAMEWORK_ONLY_VERBS` check in `post()`:

```python
    if verb in COMMAND_VERBS:
        raise PostError(f"{verb} is posted through `meld challenge <open|evidence|retract|uphold|dismiss> REF -m ...`, not meld post")
```

Update the `FRAMEWORK_ONLY_VERBS` error text to "written by meld itself (dispatch, tick, claim, and the PM summary), never through meld post".

In `actions.claim`, after the claim has succeeded (labels swapped), before the return:

```python
    if actor.is_pm:
        stale = derive_leases(board.comments).get(number)   # the raw thread, not board.leases: a lease we no longer hold is filtered there
        if stale is not None and stale.group == actor.group:
            ctx.gh.create_comment(board.coordination_issue, compose(
                Header(Party("pm", actor.group), "EXPIRED", f"#{number}", None),
                f"stale lease superseded by a new claim; the next `meld dispatch {number}` posts a fresh LEASE"))
```

with the imports `from meld.board import derive_leases`, `from meld.config import Party`, `from meld.grammar import Header, compose`. Add a comment above explaining why (a lease from an earlier run of ours would otherwise revive on re-claim and let another PM release the fresh claim once it expired).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_rules_prs.py tests/test_rules_issues.py tests/test_rules_challenges.py tests/test_posting.py tests/test_actions.py -v` then `uv run pytest -q`
Expected: PASS. `tests/test_status.py` may need its expected `legal` lists extended (`reap`/`tag` on issues, `review`/`audit`/`reap` on PRs) — update those assertions to the new truth, nothing else.

- [ ] **Step 6: Commit**

```bash
git add src/meld/rules.py src/meld/posting.py src/meld/actions.py tests/test_rules_prs.py tests/test_rules_issues.py tests/test_rules_challenges.py tests/test_posting.py tests/test_actions.py tests/test_status.py
git commit -m "feat: review, audit, reap, tag, and challenge legality live in rules.py"
```

---

### Task 5: Dispatch, reap, and tag act through the rules; security reviewers; audit at the merge commit

**Files:**
- Modify: `src/meld/dispatch.py`, `src/meld/gitops.py`, `src/meld/brief.py`, `src/meld/tagging.py`, `src/meld/cli.py`
- Test: `tests/test_dispatch.py`, `tests/test_brief.py`, `tests/test_gitops.py`

**Interfaces:**
- Consumes: `rules.pr_refusal(..., Verb.REVIEW | Verb.AUDIT, pr)`, `rules.reap_rule`, `rules.review_concern`, `issue_refusal(..., Verb.TAG, ...)` (Task 4); `PullRequest.merge_commit_sha` (Task 2).
- Produces: `dispatch(ctx, number, *, kind="build", worker=None, adapter=None, now=None, env=None)` (`worker` names the review worker type: `reviewer` (default) or `security`; audits always use `auditor`; a `--worker` on a build raises `DispatchError`); `REVIEW_WORKERS = {"review": ("reviewer", "security"), "audit": ("auditor",)}`; `gitops.checkout_sha(repo_root, path, sha)`; `render_brief(..., concern: str | None = None)` adds `## Your primary concern`, and the PR task text says whether the checkout is the PR head or the merge commit; CLI `meld dispatch N --kind review --worker security`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dispatch.py` (it already imports `dispatch`, `FakeAdapter`, `make_product`, `publish_pr_branch`, `mark_collected`, `posted`, `head_sha`, `BRIEF_FILE`, `sh`, `fixture`, `R`, `NOW`, `Refused`, `DispatchError`, `wait_for_exit`; add any that are missing):

```python
def test_dispatch_security_reviewer_and_worker_validation(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    publish_pr_branch(repo)
    adapter = FakeAdapter()
    out = dispatch(ctx, 20, kind="review", worker="security", adapter=adapter, now=NOW)
    assert (Path(out["worktree"]) / BRIEF_FILE).read_text().startswith("# Meld brief: security for PR #20")
    assert posted(fake, 20)[-1].startswith("Meld: `claude` dispatched a `security` worker")
    wait_for_exit(out["pid"])
    mark_collected(tmp_path, 20)
    with pytest.raises(Refused, match="worker type 'backend' cannot run a review"):
        dispatch(ctx, 20, kind="review", worker="backend", adapter=FakeAdapter(), now=NOW)
    with pytest.raises(DispatchError, match="--worker applies to review runs only"):
        dispatch(ctx, 11, worker="security", adapter=FakeAdapter(), now=NOW)


def test_dispatch_audit_checks_out_the_merge_commit(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)          # claude audits codex's merged PR #20
    publish_pr_branch(repo)
    sh(["git", "merge", "-q", "--no-ff", "-m", "CI pipeline (#20)", "meld/13-ci-pipeline"], repo)
    merge_sha = sh(["git", "rev-parse", "HEAD"], repo)
    sh(["git", "push", "-q", "origin", "main"], repo)
    pulls = fixture("pulls.json")
    for p in pulls:
        if p["number"] == 20:
            p.update({"state": "closed", "merged_at": "2026-09-03T11:00:00Z", "merge_commit_sha": merge_sha})
    fake.route("GET", R + "pulls", pulls)
    out = dispatch(ctx, 20, kind="audit", adapter=FakeAdapter(), now=NOW)
    wt = Path(out["worktree"])
    assert head_sha(wt) == merge_sha and out["branch"] == "meld/13-ci-pipeline"
    assert f"The checkout is the merge commit `{merge_sha[:7]}`" in (wt / BRIEF_FILE).read_text()
    assert posted(fake, 20)[-1].startswith("Meld: `claude` dispatched an `auditor` worker") and f"merge commit `{merge_sha[:7]}`" in posted(fake, 20)[-1]
    wait_for_exit(out["pid"])


def test_dispatch_audit_refusals_come_from_the_rules(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    with pytest.raises(Refused, match="cannot audit: audit targets a merged PR"):
        dispatch(ctx, 20, kind="audit", adapter=FakeAdapter(), now=NOW)          # PR #20 is open in the fixture
    with pytest.raises(Refused, match="cannot audit: cannot audit a PR authored by own group"):
        dispatch(ctx, 19, kind="audit", adapter=FakeAdapter(), now=NOW)          # PR #19 was merged by claude
```

Append to `tests/test_brief.py`:

```python
def test_pr_brief_names_the_primary_concern_and_the_checkout(tmp_path):
    pr = replace(mk_pr(20, 13), merge_commit_sha="feedfacefeedfacefeedfacefeedfacefeedface", state="closed", merged=True)
    text = render_brief(role=REVIEWER, kind="review", group="claude", model="m", effort="high", issue=None, pr=pr, plan_text="",
                        repo_root=tmp_path, worktree=tmp_path, base_branch="main", concern="tests and product conventions")
    assert "## Your primary concern" in text and "**tests and product conventions**" in text and "The checkout is the PR head." in text
    audit = render_brief(role=AUDITOR, kind="audit", group="claude", model="m", effort="high", issue=None, pr=pr, plan_text="",
                         repo_root=tmp_path, worktree=tmp_path, base_branch="main")
    assert "## Your primary concern" not in audit and "The checkout is the merge commit `feedfac` that landed this PR on `main`." in audit
```

(`REVIEWER`/`AUDITOR` are `WorkerType` objects; build them the way `tests/test_brief.py` already builds roles, e.g. from `roster.builtin_workers()`.)

Append to `tests/test_gitops.py`:

```python
def test_checkout_sha_detaches_at_an_exact_commit(tmp_path):
    origin, repo = make_origin_and_clone(tmp_path)
    (repo / "b.txt").write_text("b\n")
    sh(["git", "add", "b.txt"], repo)
    sh(["git", "commit", "-q", "-m", "second"], repo)
    sh(["git", "push", "-q", "origin", "main"], repo)
    target = sh(["git", "rev-parse", "HEAD~1"], repo)
    wt = tmp_path / "wt"
    checkout_sha(repo, wt, target)
    assert head_sha(wt) == target and not (wt / "b.txt").exists()
    with pytest.raises(GitError):
        checkout_sha(repo, tmp_path / "wt2", "0000000000000000000000000000000000000000")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dispatch.py tests/test_brief.py tests/test_gitops.py -v`
Expected: FAIL (`dispatch() got an unexpected keyword argument 'worker'`, `cannot import name 'checkout_sha'`, `render_brief() got an unexpected keyword argument 'concern'`).

- [ ] **Step 3: Implement**

`src/meld/gitops.py`, after `checkout_pr_head`:

```python
def checkout_sha(repo_root: Path, path: Path, sha: str, run: RunFn = subprocess.run) -> None:
    """Detached worktree at an exact commit (an audit's merge commit). Fetches first so a commit that landed on the
    default branch since our last fetch is present; a sha the remote no longer reaches raises GitError."""
    git(["fetch", "--quiet", "origin"], repo_root, run)
    git(["worktree", "prune"], repo_root, run)
    path.parent.mkdir(parents=True, exist_ok=True)
    git(["worktree", "add", "--detach", str(path), sha], repo_root, run)
```

`src/meld/brief.py`: add `concern: str | None = None` to `render_brief`'s keyword parameters; in the PR branch build the checkout sentence and the concern section:

```python
        if kind == "audit" and pr.merge_commit_sha:
            where = f"The checkout is the merge commit `{pr.merge_commit_sha[:7]}` that landed this PR on `{base_branch}`."
        else:
            where = "The checkout is the PR head."
        task = (f"## Your task\n\n**PR #{pr.number}: {pr.title}** (branch `{pr.head_ref}`, head `{pr.head_sha[:7]}`, "
                f"authored by `{pr.author}`, last commit by `{pr.head_committer or 'unknown'}`).\n\n{pr.body.strip() or '(no description)'}\n"
                f"\n{where} Your verdict goes in the `verdict` field of the result file.\n")
```

and, right after `parts` is built and before the plan excerpt:

```python
    if concern:
        parts += ["## Your primary concern", "",
                  f"Other groups review this PR too; yours is **{concern}**. Report anything else you notice, but spend your effort there first.", ""]
```

`src/meld/dispatch.py`: import `checkout_sha` from `meld.gitops` and `reap_rule, review_concern` from `meld.rules`; add the constant

```python
REVIEW_WORKERS = {"review": ("reviewer", "security"), "audit": ("auditor",)}
```

`dispatch()` gains `worker: str | None = None`; before dispatching: `if kind == "build" and worker: raise DispatchError("--worker applies to review runs only")`; pass `worker` to `_dispatch_pr`. Replace `_dispatch_pr`'s check block (everything from `if board.halted` through the `role_name`/`worker` lookup) with:

```python
    pr = board.pulls.get(number)
    if pr is None:
        refuse(ctx, board, Refusal(Verb.DISPATCH, target, "PR is not on a meld/ branch of the active repo"))
    blocker = pr_refusal(board, ctx.cfg, actor, Verb.REVIEW if kind == "review" else Verb.AUDIT, pr)
    if blocker:
        refuse(ctx, board, Refusal(Verb.DISPATCH, target, f"cannot {kind}: {blocker.rule}"))
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    role_name = worker_name or REVIEW_WORKERS[kind][0]
    if role_name not in REVIEW_WORKERS[kind]:
        refuse(ctx, board, Refusal(Verb.DISPATCH, target, f"worker type '{role_name}' cannot run a {kind}; use one of: {', '.join(REVIEW_WORKERS[kind])}"))
    adp = _adapter(ctx, group, adapter)
    worker = _worker_type(ctx, adp, role_name)
    if worker is None:
        refuse(ctx, board, Refusal(Verb.DISPATCH, target, f"worker type '{role_name}' is not in {group.name}'s roster"))
```

(`_dispatch_pr` gains the parameter `worker_name: str | None`.) Replace the checkout and brief lines:

```python
    at_merge = kind == "audit" and bool(pr.merge_commit_sha)
    if at_merge:
        checkout_sha(ctx.repo_root, wt, pr.merge_commit_sha)  # type: ignore[arg-type]
    else:
        checkout_pr_head(ctx.repo_root, wt, pr.head_ref)
    exclude_paths(wt)
    model, effort = group.orchestrator.model, group.orchestrator.effort
    concern = review_concern(ctx.cfg, pr, group.name) if kind == "review" and role_name == "reviewer" else None
    brief = render_brief(role=worker, kind=kind, group=group.name, model=model, effort=effort, issue=None, pr=pr,
                         plan_text=board.plan_text, repo_root=ctx.repo_root, worktree=wt, base_branch=ctx.cfg.default_branch, concern=concern)
```

and the announcement comment:

```python
    where = f"merge commit `{pr.merge_commit_sha[:7]}`" if at_merge else f"head `{pr.head_sha[:7]}`"   # type: ignore[index]
    article = "an" if worker.name[0] in "aeiou" else "a"
    ctx.gh.create_comment(number, f"Meld: `{group.name}` dispatched {article} `{worker.name}` worker on `{model}` at effort {effort} "
                                  f"against {where}. Deadline {_iso(deadline)}.")
```

Fix the existing test that asserted `dispatched a \`reviewer\`` only if the article changed it (it did not: "a `reviewer`"). Rewrite `reap`'s checks:

```python
    issue = board.issues.get(number)
    pr = board.pulls.get(number)
    rule = reap_rule(board, ctx.cfg, actor, number, issue, pr)
    if rule:
        refuse(ctx, board, Refusal(Verb.REAP, target, rule))
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    rec = board.run_for(group.name, number)
    assert rec is not None  # reap_rule guarantees it
```

(then the existing worktree/branch/archive body follows unchanged; delete the old inline checks and the `finished` computation.)

`src/meld/tagging.py`: replace the party check with the rule:

```python
    issue = board.issues.get(number)
    if issue is None:
        refuse(ctx, board, Refusal(Verb.TAG, target, "issue is not in the active milestone"))
    refusal = issue_refusal(board, ctx.cfg, actor, Verb.TAG, issue, ctx.models)
    if refusal:
        refuse(ctx, board, refusal)
```

(import `issue_refusal` from `meld.rules`; the remaining value validation stays.)

`src/meld/cli.py`: add `@click.option("--worker", default=None, help="Review worker type: reviewer (default) or security.")` to `dispatch` and pass `worker=worker` to `do_dispatch`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_dispatch.py tests/test_brief.py tests/test_gitops.py tests/test_reap.py tests/test_tagging.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/meld/dispatch.py src/meld/gitops.py src/meld/brief.py src/meld/tagging.py src/meld/cli.py tests/test_dispatch.py tests/test_brief.py tests/test_gitops.py
git commit -m "feat: review and audit dispatch obey the rules, accept a security reviewer, and audit the merge commit"
```

---

### Task 6: `meld review` — a collected verdict becomes a GitHub review

**Files:**
- Create: `src/meld/review.py`
- Modify: `src/meld/results.py` (add `format_findings`), `src/meld/cli.py`
- Test: `tests/test_review.py` (new)

**Interfaces:**
- Consumes: `results.load_result/mark_submitted` (Task 3), `GitHub.create_review/create_status` (Task 1), `rules.pr_refusal` with `APPROVE`/`REQUEST_CHANGES`/`PUSH_FIX`, `dispatch._swap_labels/IN_REVIEW/neutralise_closers/_now/_iso`, `actions.BLOCKED/IN_PROGRESS`, `gitops.commits_ahead/push_branch/head_sha`.
- Produces: `results.format_findings(comments: list[dict]) -> str` (one `- \`path:line\` body` line per comment); `review.EVENTS`, `review.STATUS_STATE`, `review.status_context(group) -> str` (`meld/review/<group>`), `review.submit_review(gh, number, event, body, commit_id, comments) -> dict` (422 → findings folded into the body), `review.review(ctx, number, *, now=None) -> dict` returning `{"reviewed", "verdict", "event", "review_id", "issue", "issue_status", "head"}` (or `{"reviewed", "verdict", "event": None, "reason"}` for no-verdict/stale); CLI `meld review N`.

- [ ] **Step 1: Write the failing tests**

`tests/test_review.py`:

```python
from datetime import datetime, timezone
from pathlib import Path

import pytest

from meld.actions import Refused
from meld.dispatch import DispatchError, collect, dispatch
from meld.results import load_result, store_result
from meld.review import review
from tests.fakeadapter import FakeAdapter
from tests.gitfixtures import sh
from tests.productfixtures import NO_RESULT_SCRIPT, R, REVIEW_SCRIPT, fixture, make_product, wait_for_exit
from tests.test_collect import routes_for_labels
from tests.test_dispatch import publish_pr_branch

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
CHANGES_SCRIPT = """
import json, pathlib
pathlib.Path(".meld-result.json").write_text(json.dumps({"outcome": "done", "summary": "the handler swallows errors", "verified_by": "pytest -q -> 1 failed",
    "verdict": "request-changes", "comments": [{"path": "ci.yml", "line": 1, "body": "this step never fails the build"}]}))
"""
PUSH_FIX_SCRIPT = """
import json, pathlib, subprocess
pathlib.Path("fix.py").write_text("FIXED = True\\n")
subprocess.run(["git", "add", "fix.py"], check=True)
subprocess.run(["git", "commit", "-q", "-m", "fix: guard the null case"], check=True)
pathlib.Path(".meld-result.json").write_text(json.dumps({"outcome": "done", "summary": "added the missing guard", "verified_by": "pytest -q -> ok",
    "verdict": "push-fix", "comments": []}))
"""


def reviewed_pr(tmp_path, script):
    fake, ctx, repo, origin = make_product(tmp_path)   # claude PM reviews codex's PR #20 (issue #13)
    publish_pr_branch(repo)
    routes_for_labels(fake, 13)
    fake.route("DELETE", R + "issues/13/labels/meld%3Ain-review", [])
    fake.route("POST", R + "pulls/20/reviews", {"id": 501, "html_url": "https://github.com/acme/widgets/pull/20#pullrequestreview-501"})
    fake.route("POST", R + "statuses/abc123", {"id": 77})
    out = dispatch(ctx, 20, kind="review", adapter=FakeAdapter(script=script), now=NOW)
    wait_for_exit(out["pid"])
    collect(ctx, 20, now=NOW)
    return fake, ctx, repo, Path(out["worktree"])


def calls(fake, method, endpoint):
    return [c.body for c in fake.calls if c.method == method and c.endpoint == R + endpoint]


def test_review_approve_submits_a_github_review_and_a_status(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, REVIEW_SCRIPT)
    out = review(ctx, 20, now=NOW)
    assert out == {"reviewed": 20, "verdict": "approve", "event": "APPROVE", "review_id": 501, "issue": 13, "issue_status": None, "head": "abc123"}
    (body,) = calls(fake, "POST", "pulls/20/reviews")
    assert body["event"] == "APPROVE" and body["commit_id"] == "abc123" and body["body"].startswith("looks right\n\nverified-by: pytest -q -> ok")
    assert body["comments"] == [{"path": "README.md", "line": 1, "side": "RIGHT", "body": "nit: title"}]
    (status,) = calls(fake, "POST", "statuses/abc123")
    assert status == {"state": "success", "context": "meld/review/claude", "description": "claude review: approve",
                      "target_url": "https://github.com/acme/widgets/pull/20#pullrequestreview-501"}
    assert not calls(fake, "POST", "issues/13/labels")                               # approve moves no labels
    assert load_result(tmp_path / "home", "claude", "review", 20) is None
    assert list((tmp_path / "home" / "results" / "claude" / "submitted").glob("review-20-abc123-*.json"))
    with pytest.raises(DispatchError, match="no collected review result"):
        review(ctx, 20, now=NOW)


def test_review_request_changes_returns_the_issue_to_in_progress(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, CHANGES_SCRIPT)
    out = review(ctx, 20, now=NOW)
    assert out["event"] == "REQUEST_CHANGES" and out["issue_status"] == "in_progress"
    assert calls(fake, "POST", "issues/13/labels") == [{"labels": ["meld:in-progress"]}]
    assert any(c.method == "DELETE" and c.endpoint.endswith("issues/13/labels/meld%3Ain-review") for c in fake.calls)
    assert calls(fake, "POST", "statuses/abc123")[0]["state"] == "failure"
    (body,) = calls(fake, "POST", "pulls/20/reviews")
    assert body["comments"][0]["path"] == "ci.yml"


def test_review_request_changes_at_the_pingpong_cap_blocks_the_issue(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, CHANGES_SCRIPT)
    older = [{"id": 1, "user": {"login": "meld-claude-bot"}, "state": "CHANGES_REQUESTED", "commit_id": "old000", "submitted_at": "2026-09-03T09:00:00Z"},
             {"id": 2, "user": {"login": "meld-claude-bot"}, "state": "CHANGES_REQUESTED", "commit_id": "old001", "submitted_at": "2026-09-03T09:30:00Z"}]
    fake.route("GET", R + "pulls/20/reviews", older)     # two rounds already happened; the cap is 3
    out = review(ctx, 20, now=NOW)
    assert out["issue_status"] == "blocked" and calls(fake, "POST", "issues/13/labels") == [{"labels": ["meld:blocked"]}]
    assert any("ping-pong cap (3) reached" in c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/comments")


def test_review_push_fix_pushes_and_asks_the_author_group_to_re_review(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, PUSH_FIX_SCRIPT)
    new_head = sh(["git", "rev-parse", "HEAD"], wt)
    fake.route("POST", R + f"statuses/{new_head}", {"id": 78})
    out = review(ctx, 20, now=NOW)
    assert out["event"] == "COMMENT" and out["head"] == new_head
    assert sh(["git", "rev-parse", "origin/meld/13-ci-pipeline"], repo) == new_head     # pushed from the detached review worktree
    (body,) = calls(fake, "POST", "pulls/20/reviews")
    assert body["event"] == "COMMENT" and body["body"].startswith("Pushed 1 fix commit(s)") and "may not approve this head" in body["body"]
    assert calls(fake, "POST", f"statuses/{new_head}")[0]["state"] == "pending"
    coord = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]
    assert coord[-1].startswith("[claude:pm] REVIEW-REQUEST #20\n") and new_head[:7] in coord[-1]


def test_review_of_a_moved_head_is_stale(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, REVIEW_SCRIPT)
    pulls = fixture("pulls.json")
    for p in pulls:
        if p["number"] == 20:
            p["head"]["sha"] = "def456"
    fake.route("GET", R + "pulls", pulls)
    fake.route("GET", R + "commits/def456/check-runs", fixture("checkruns_abc123.json"))
    out = review(ctx, 20, now=NOW)
    assert out["event"] is None and out["reason"] == "stale head"
    assert not calls(fake, "POST", "pulls/20/reviews")
    assert any("is stale" in c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/20/comments")
    assert load_result(tmp_path / "home", "claude", "review", 20) is None


def test_review_without_a_verdict_asks_for_a_redispatch(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, NO_RESULT_SCRIPT)
    out = review(ctx, 20, now=NOW)
    assert out["event"] is None and "exited without writing" in out["reason"]
    assert any("no usable verdict" in c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/20/comments")
    assert load_result(tmp_path / "home", "claude", "review", 20) is None


def test_review_refuses_to_approve_own_head(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, group="codex")   # codex made PR #20's last commit
    store_result(tmp_path / "home", "codex", "review", 20,
                 {"head_sha": "abc123", "result": {"verdict": "approve", "summary": "x", "verified_by": "y", "comments": []}}, "2026-09-03T12:00:00Z")
    with pytest.raises(Refused, match="cannot approve its own head"):
        review(ctx, 20, now=NOW)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_review.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.review'`.

- [ ] **Step 3: Implement**

Add to `src/meld/results.py`:

```python
def format_findings(comments: list[dict]) -> str:
    """One bullet per reviewer/auditor comment, for PR bodies and coordination posts."""
    return "\n".join(f"- `{c['path']}:{c['line']}` {c['body']}" for c in comments)
```

`src/meld/review.py`:

```python
"""meld review and meld merge: a collected reviewer verdict becomes a GitHub review; a PR lands under the quorum rule."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from meld.actions import BLOCKED, IN_PROGRESS, refuse
from meld.config import Party
from meld.dispatch import IN_REVIEW, DispatchError, _iso, _now, _swap_labels, neutralise_closers
from meld.github import GitHubError
from meld.gitops import commits_ahead, head_sha, push_branch
from meld.grammar import Header, compose
from meld.results import format_findings, load_result, mark_submitted
from meld.rules import Refusal, Verb, approving_parties, pr_refusal
from meld.status import build_status

EVENTS = {"approve": "APPROVE", "request-changes": "REQUEST_CHANGES", "push-fix": "COMMENT"}
VERBS = {"approve": Verb.APPROVE, "request-changes": Verb.REQUEST_CHANGES, "push-fix": Verb.PUSH_FIX}
# Spec 21.4 asked for a neutral check run; personal tokens cannot create check runs, so each group's review is a
# commit status instead: visible on the PR, never blocking unless a human lists it as a required check.
STATUS_STATE = {"approve": "success", "request-changes": "failure", "push-fix": "pending"}


def status_context(group: str) -> str:
    return f"meld/review/{group}"


def submit_review(gh, number: int, event: str, body: str, commit_id: str, comments: list[dict]) -> dict:
    """Post the review with diff comments; when GitHub rejects a comment position (422), fold the findings into the body."""
    try:
        return gh.create_review(number, event, body, commit_id, comments or None)
    except GitHubError as e:
        if e.status != 422 or not comments:
            raise
        return gh.create_review(number, event, f"{body}\n\n## Findings\n{format_findings(comments)}", commit_id, None)


def _no_action(ctx, group, number: int, verdict, reason: str, outcome: str, when: str, note: str, extra: dict | None = None) -> dict:
    ctx.gh.create_comment(number, note)
    mark_submitted(ctx.store.home, group.name, "review", number, {"outcome": outcome, "event": None, **(extra or {})}, when)
    return {"reviewed": number, "verdict": verdict, "event": None, "reason": reason}


def review(ctx, number: int, *, now: datetime | None = None) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    target = f"#{number}"
    if not actor.is_pm:
        refuse(ctx, board, Refusal(Verb.REVIEW, target, "only the PM may submit a review"))
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    pr = board.pulls.get(number)
    if pr is None:
        refuse(ctx, board, Refusal(Verb.REVIEW, target, "PR is not on a meld/ branch of the active repo"))
    stored = load_result(ctx.store.home, group.name, "review", number)
    if stored is None:
        raise DispatchError(f"no collected review result for {target}; run `meld collect {number}` first")
    when = _iso(_now(now))
    result = stored.get("result") or {}
    verdict = result.get("verdict")
    if verdict not in EVENTS:
        reason = stored.get("reason") or (f"verdict {verdict!r} is not a review verdict" if verdict else "the reviewer wrote no verdict")
        return _no_action(ctx, group, number, verdict, reason, "no-verdict", when,
                          f"Meld: `{group.name}` reviewer produced no usable verdict: {reason}. Re-dispatch a review.")
    if stored.get("head_sha") and stored["head_sha"] != pr.head_sha:
        return _no_action(ctx, group, number, verdict, "stale head", "stale", when,
                          f"Meld: `{group.name}` review of `{stored['head_sha'][:7]}` is stale; the head is now `{pr.head_sha[:7]}`. Re-dispatch a review.",
                          {"current_head": pr.head_sha})
    refusal = pr_refusal(board, ctx.cfg, actor, VERBS[verdict], pr)
    if refusal:
        refuse(ctx, board, refusal)
    comments = [c for c in result.get("comments") or [] if isinstance(c, dict) and {"path", "line", "body"} <= set(c)]
    body = f"{neutralise_closers(result.get('summary', ''))}\n\nverified-by: {neutralise_closers(result.get('verified_by', ''))}"
    sha = pr.head_sha
    if verdict == "push-fix":
        rec = board.run_for(group.name, number)
        wt = Path(rec.worktree) if rec else None
        pushed = commits_ahead(wt, f"origin/{pr.head_ref}") if wt is not None and wt.is_dir() else []
        if not pushed:
            raise DispatchError(f"push-fix verdict on {target} but the reviewer made no commits in its worktree")
        push_branch(wt, pr.head_ref, ctx.gh.token)  # type: ignore[arg-type]
        sha = head_sha(wt)  # type: ignore[arg-type]
        body = (f"Pushed {len(pushed)} fix commit(s) ({pushed[0][:7]}..{pushed[-1][:7]}); `{group.name}` is now the last committer "
                f"and may not approve this head.\n\n{body}")
    resp = submit_review(ctx.gh, number, EVENTS[verdict], body, pr.head_sha, comments)
    description = f"{group.name} review: {verdict}" + (" (awaiting the author group's review of the new head)" if verdict == "push-fix" else "")
    ctx.gh.create_status(sha, STATUS_STATE[verdict], status_context(group.name), description, resp.get("html_url"))
    issue_status = None
    if verdict == "request-changes" and pr.issue_number is not None and pr.issue_number in board.issues:
        if pr.changes_requested_total() + 1 >= ctx.cfg.pingpong_cap:
            _swap_labels(ctx, pr.issue_number, BLOCKED, (IN_REVIEW, IN_PROGRESS))
            ctx.gh.create_comment(pr.issue_number, f"Meld: review ping-pong cap ({ctx.cfg.pingpong_cap}) reached on PR #{number}; "
                                                    "the orchestrators must post a resolution before work resumes.")
            issue_status = "blocked"
        else:
            _swap_labels(ctx, pr.issue_number, IN_PROGRESS, (IN_REVIEW,))
            issue_status = "in_progress"
    if verdict == "push-fix":
        ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "REVIEW-REQUEST", target, None),
                              f"Head moved to {sha[:7]} by a push-fix from {group.name}; the author group must review the new head."))
    mark_submitted(ctx.store.home, group.name, "review", number,
                   {"outcome": "submitted", "event": EVENTS[verdict], "review_id": resp.get("id"), "new_head": sha if verdict == "push-fix" else None}, when)
    return {"reviewed": number, "verdict": verdict, "event": EVENTS[verdict], "review_id": resp.get("id"), "issue": pr.issue_number,
            "issue_status": issue_status, "head": sha}


def merge(ctx, number: int) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    target = f"#{number}"
    pr = board.pulls.get(number)
    if pr is None:
        refuse(ctx, board, Refusal(Verb.MERGE, target, "PR is not on a meld/ branch of the active repo"))
    refusal = pr_refusal(board, ctx.cfg, actor, Verb.MERGE, pr)
    if refusal:
        refuse(ctx, board, refusal)
    resp = ctx.gh.merge_pull(number, pr.head_sha, pr.title)   # the sha guard makes GitHub refuse (409) if the head moved since this board
    return {"merged": number, "sha": resp.get("sha"), "issue": pr.issue_number, "approvals": sorted(approving_parties(ctx.cfg, pr))}
```

`src/meld/cli.py`: import `from meld.results import ResultsError` and `from meld.review import merge as do_merge, review as do_review`; add `ResultsError` to `_guard`'s tuple; add:

```python
@main.command()
@click.argument("number", type=int)
@click.pass_context
def review(ctx: click.Context, number: int) -> None:
    """Submit the collected reviewer verdict on a PR as a GitHub review: approve, request changes, or push the fix."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_review(c, number))


@main.command()
@click.argument("number", type=int)
@click.pass_context
def merge(ctx: click.Context, number: int) -> None:
    """Squash-merge a PR once the quorum rule, required checks, and open challenges allow it."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_merge(c, number))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_review.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/meld/review.py src/meld/results.py src/meld/cli.py tests/test_review.py
git commit -m "feat: meld review submits verdicts as GitHub reviews with a commit status; meld merge lands PRs under the quorum rule"
```

---

### Task 7: `meld merge` tests and CLI coverage

**Files:**
- Test: `tests/test_merge.py` (new), `tests/test_cli.py`

**Interfaces:**
- Consumes: `review.merge` (Task 6), `GitHub.merge_pull` (Task 1), `tests.productfixtures.make_product`, `fixture`.
- Produces: nothing new; this task is the test cycle the merge action owes the plan (spec §19 "Review": quorum math including human approvals, challenge blocking).

- [ ] **Step 1: Write the failing tests**

`tests/test_merge.py`:

```python
import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.actions import Refused
from meld.review import merge
from tests.conftest import env_for
from tests.productfixtures import R, fixture, make_product


def test_merge_squashes_with_the_head_sha_guard(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)   # claude PM; PR #20's head is codex's and claude approved it on abc123
    fake.route("PUT", R + "pulls/20/merge", {"sha": "m123", "merged": True})
    assert merge(ctx, 20) == {"merged": 20, "sha": "m123", "issue": 13, "approvals": ["claude"]}
    (body,) = [c.body for c in fake.calls if c.method == "PUT"]
    assert body == {"sha": "abc123", "merge_method": "squash", "commit_title": "CI pipeline"}


def test_human_approval_counts_toward_the_quorum(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, group="codex")   # codex PM merges its own group's PR on a human approval
    fake.route("GET", R + "pulls/20/reviews", [{"id": 9, "user": {"login": "empowerScott"}, "state": "APPROVED", "commit_id": "abc123",
                                                "submitted_at": "2026-09-03T11:00:00Z"}])
    fake.route("PUT", R + "pulls/20/merge", {"sha": "m124", "merged": True})
    assert merge(ctx, 20)["approvals"] == ["human:empowerScott"]


def test_merge_refusals(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    fake.route("GET", R + "pulls/20/reviews", [])
    with pytest.raises(Refused, match="0/1 approvals from non-committer parties"):
        merge(ctx, 20)
    fake2, orch, *_ = make_product(tmp_path / "o", role="orchestrator")
    with pytest.raises(Refused, match="only a PM or a human may merge"):
        merge(orch, 20)
    fake3, challenged, *_ = make_product(tmp_path / "c")
    fake3.route("GET", R + "issues/10/comments", fixture("comments_10.json") + [
        {"id": 131, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:50:00Z", "body": "[claude] CHALLENGE #20\n\nthe CI claim is unverified"}])
    with pytest.raises(Refused, match="PR has an open CHALLENGE"):
        merge(challenged, 20)
    fake4, red, *_ = make_product(tmp_path / "r")
    fake4.route("GET", R + "commits/abc123/check-runs", {"total_count": 1, "check_runs": [{"name": "ci", "status": "completed", "conclusion": "failure"}]})
    with pytest.raises(Refused, match="required checks are red"):
        merge(red, 20)
    with pytest.raises(Refused, match="PR is not on a meld/ branch"):
        merge(ctx, 18)


def test_cli_merge(tmp_path, monkeypatch):
    fake, ctx, repo, origin = make_product(tmp_path)
    fake.route("PUT", R + "pulls/20/merge", {"sha": "m123", "merged": True})
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(repo / ".meld.toml"), "--compact", "merge", "20"], env=env_for(tmp_path))
    assert result.exit_code == 0, result.output
    assert '"merged": 20' in result.output
```

(The check-runs shape must match what `assemble.checks_state` reads; copy the field names from `tests/fixtures/board1/checkruns_abc123.json`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_merge.py -v`
Expected: FAIL if Task 6 is not yet on the branch (import error); otherwise these tests should pass immediately — in that case confirm each refusal message matches `rules.py` verbatim and move on.

- [ ] **Step 3: Fix anything the tests expose**

Adjust `review.merge` or the refusal texts only if a test fails for a real reason (never weaken an assertion to pass).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_merge.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_merge.py
git commit -m "test: merge quorum math, human approvals, challenge and check blocking, CLI"
```

---

### Task 8: `meld audit`, `meld reopen`, and the audit schedule in `meld status`

**Files:**
- Create: `src/meld/audit.py`
- Modify: `src/meld/config.py`, `src/meld/status.py`, `src/meld/cli.py`
- Test: `tests/test_audit.py` (new), `tests/test_config.py`, `tests/test_status.py`

**Interfaces:**
- Consumes: `results.load_result/mark_submitted/format_findings`, `rules.issue_refusal(..., Verb.REOPEN, ...)`, `Board.audits`, `PullRequest.merged_at`, `GitHub.update_issue/remove_assignees/add_labels/remove_label`, `status._age_minutes`.
- Produces: `Config.audit_batch_merges: int = 5`, `Config.audit_batch_hours: int = 4` (from `[audit] batch_merges`, `batch_hours`); `audit.audit(ctx, number, *, now=None) -> dict` (`{"audited", "verdict", "issue", "comment_id"}` or `{"audited", "verdict": None, "reason"}`), `audit.reopen(ctx, number) -> dict` (`{"reopened", "by"}`); `status.audit_summary(board, cfg, actor, now) -> dict` (`{"pending": [pr...], "merges_pending", "oldest_pending_hours", "due"}`) rendered as top-level `"audit"` in `render_status`; CLI `meld audit N`, `meld reopen N`.

- [ ] **Step 1: Write the failing tests**

`tests/test_audit.py`:

```python
from datetime import datetime, timezone

import pytest

from meld.actions import Refused
from meld.audit import audit, reopen
from meld.dispatch import collect, dispatch
from tests.fakeadapter import FakeAdapter
from tests.gitfixtures import sh
from tests.productfixtures import R, fixture, make_product, wait_for_exit
from tests.test_collect import routes_for_labels
from tests.test_dispatch import publish_pr_branch

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
AUDIT_FAIL_SCRIPT = """
import json, pathlib
pathlib.Path(".meld-result.json").write_text(json.dumps({"outcome": "done", "summary": "criterion 2 is not implemented", "verified_by": "pytest -q -> 3 failed",
    "verdict": "fail", "comments": [{"path": "ci.yml", "line": 1, "body": "no cache step, criterion 2 unmet"}]}))
"""


def merged_pr_20(fake, repo):
    publish_pr_branch(repo)
    sh(["git", "merge", "-q", "--no-ff", "-m", "CI pipeline (#20)", "meld/13-ci-pipeline"], repo)
    merge_sha = sh(["git", "rev-parse", "HEAD"], repo)
    sh(["git", "push", "-q", "origin", "main"], repo)
    pulls = fixture("pulls.json")
    for p in pulls:
        if p["number"] == 20:
            p.update({"state": "closed", "merged_at": "2026-09-03T11:00:00Z", "merge_commit_sha": merge_sha})
    fake.route("GET", R + "pulls", pulls)
    return merge_sha


def test_audit_posts_the_verdict_and_reopen_returns_the_issue_to_ready(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)   # claude PM audits codex's merged PR #20 (issue #13)
    merged_pr_20(fake, repo)
    routes_for_labels(fake, 13)
    fake.route("DELETE", R + "issues/13/labels/meld%3Ain-review", [])
    fake.route("PATCH", R + "issues/13", {"number": 13, "state": "open"})
    fake.route("DELETE", R + "issues/13/assignees", {})
    out = dispatch(ctx, 20, kind="audit", adapter=FakeAdapter(script=AUDIT_FAIL_SCRIPT), now=NOW)
    wait_for_exit(out["pid"])
    collect(ctx, 20, now=NOW)
    assert audit(ctx, 20, now=NOW) == {"audited": 20, "verdict": "fail", "issue": 13, "comment_id": 310}
    coord = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"][-1]
    assert coord.startswith("[claude:pm] AUDIT #20 FAIL\n") and "- `ci.yml:1` no cache step" in coord and "verified-by: pytest -q -> 3 failed" in coord
    assert any("meld reopen 13" in c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/comments")
    with pytest.raises(Refused, match="issue is not closed"):
        reopen(ctx, 13)
    fake.route("GET", R + "issues/10/comments", fixture("comments_10.json") + [
        {"id": 140, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T12:30:00Z", "body": coord}])
    issues = fixture("issues.json")
    for i in issues:
        if i["number"] == 13:
            i["state"] = "closed"
    fake.route("GET", R + "issues", issues)
    assert reopen(ctx, 13) == {"reopened": 13, "by": "claude"}
    assert [c.body for c in fake.calls if c.method == "PATCH" and c.endpoint == R + "issues/13"] == [{"state": "open"}]
    assert {"labels": ["meld:ready"]} in [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/labels"]
    assert any(c.method == "DELETE" and c.endpoint == R + "issues/13/assignees" for c in fake.calls)
    assert any("reopened by `claude` after a failed audit" in c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/comments")


def test_audit_pass_posts_no_reopen_hint_and_refusals(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    merged_pr_20(fake, repo)
    script = AUDIT_FAIL_SCRIPT.replace('"verdict": "fail"', '"verdict": "pass"')
    out = dispatch(ctx, 20, kind="audit", adapter=FakeAdapter(script=script), now=NOW)
    wait_for_exit(out["pid"])
    collect(ctx, 20, now=NOW)
    assert audit(ctx, 20, now=NOW)["verdict"] == "pass"
    coord = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"][-1]
    assert coord.startswith("[claude:pm] AUDIT #20 PASS\n")
    assert not [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/comments"]
    fake2, orch, *_ = make_product(tmp_path / "o", role="orchestrator")
    with pytest.raises(Refused, match="only the PM may post an audit"):
        audit(orch, 20, now=NOW)
```

Append to `tests/test_config.py` (mirror its existing `Config.from_dict` tests):

```python
def test_audit_batch_defaults_and_overrides():
    base = {"repo": {"name": "acme/widgets"}, "groups": {"claude": {"cli": "claude", "bot": "b", "token_env": "T",
            "orchestrator": {"model": "m", "effort": "high"}, "pm": {"model": "m", "effort": "medium"}}}}
    cfg = Config.from_dict(base)
    assert (cfg.audit_batch_merges, cfg.audit_batch_hours) == (5, 4)
    cfg2 = Config.from_dict({**base, "audit": {"batch_merges": 3, "batch_hours": 1}})
    assert (cfg2.audit_batch_merges, cfg2.audit_batch_hours) == (3, 1)
```

Append to `tests/test_status.py` (it already imports `render_status`, `mk_board`, `mk_pr`, `CFG2`, `REG`, actors):

```python
def test_status_reports_the_audit_schedule_for_the_calling_group():
    merged = replace(mk_pr(19, 9, committer="meld-claude-bot", state="closed", merged=True), merged_at="2026-09-03T06:00:00Z")
    board = mk_board(CFG2, pulls=[merged])
    now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    codex = render_status(board, CFG2, CODEX_PM, REG, now)["audit"]
    assert codex == {"pending": [19], "merges_pending": 1, "oldest_pending_hours": 6.0, "due": True}
    claude = render_status(board, CFG2, CLAUDE_PM, REG, now)["audit"]
    assert claude == {"pending": [], "merges_pending": 0, "oldest_pending_hours": None, "due": False}
    fresh = replace(merged, merged_at="2026-09-03T11:30:00Z")
    assert render_status(mk_board(CFG2, pulls=[fresh]), CFG2, CODEX_PM, REG, now)["audit"]["due"] is False
    done = replace(board, audits={19: {"codex": "pass"}})
    assert render_status(done, CFG2, CODEX_PM, REG, now)["audit"]["pending"] == []
    assert render_status(board, CFG2, HUMAN, REG, now)["audit"]["pending"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_audit.py tests/test_config.py tests/test_status.py -v`
Expected: FAIL (`No module named 'meld.audit'`, `Config` has no attribute `audit_batch_merges`, `KeyError: 'audit'`).

- [ ] **Step 3: Implement**

`src/meld/config.py`: add `audit_batch_merges: int = 5` and `audit_batch_hours: int = 4` after `lease_grace_minutes` in `Config`; in `from_dict` read `audit = data.get("audit", {})` and pass `audit_batch_merges=int(audit.get("batch_merges", 5)), audit_batch_hours=int(audit.get("batch_hours", 4))`.

Verify `GitHub.list_issues` requests closed issues too (`state=all`): if it does not, add `"state": "all"` to its params and route accordingly — `reopen` needs closed issues on the board, and `_reopen` already assumes them. Report what you found.

`src/meld/audit.py`:

```python
"""meld audit and meld reopen: post an auditor's verdict on a merged PR; reopen an issue after a failed audit."""
from __future__ import annotations

from datetime import datetime

from meld.actions import BLOCKED, IN_PROGRESS, READY, refuse
from meld.config import Party
from meld.dispatch import IN_REVIEW, DispatchError, _iso, _now, neutralise_closers
from meld.grammar import Header, compose
from meld.results import format_findings, load_result, mark_submitted
from meld.rules import Refusal, Verb, issue_refusal
from meld.status import build_status

AUDIT_VERDICTS = ("pass", "fail")


def audit(ctx, number: int, *, now: datetime | None = None) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    target = f"#{number}"
    if not actor.is_pm:
        refuse(ctx, board, Refusal(Verb.AUDIT, target, "only the PM may post an audit"))
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    pr = board.pulls.get(number)
    if pr is None:
        refuse(ctx, board, Refusal(Verb.AUDIT, target, "PR is not on a meld/ branch of the active repo"))
    stored = load_result(ctx.store.home, group.name, "audit", number)
    if stored is None:
        raise DispatchError(f"no collected audit result for {target}; run `meld collect {number}` first")
    when = _iso(_now(now))
    result = stored.get("result") or {}
    verdict = result.get("verdict")
    if verdict not in AUDIT_VERDICTS:
        reason = stored.get("reason") or (f"verdict {verdict!r} is not an audit verdict" if verdict else "the auditor wrote no verdict")
        ctx.gh.create_comment(number, f"Meld: `{group.name}` auditor produced no usable verdict: {reason}. Re-dispatch the audit.")
        mark_submitted(ctx.store.home, group.name, "audit", number, {"outcome": "no-verdict"}, when)
        return {"audited": number, "verdict": None, "reason": reason}
    comments = [c for c in result.get("comments") or [] if isinstance(c, dict) and {"path", "line", "body"} <= set(c)]
    text = (f"{neutralise_closers(result.get('summary', ''))}\n\n## Findings\n{format_findings(comments) or '- none'}\n\n"
            f"verified-by: {neutralise_closers(result.get('verified_by', ''))}")
    resp = ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "AUDIT", target, verdict.upper()), text))
    if verdict == "fail" and pr.issue_number is not None:
        ctx.gh.create_comment(pr.issue_number, f"Meld: `{group.name}` audited PR #{number} and found it does not meet its acceptance criteria; "
                                               f"see the AUDIT comment on the coordination issue. `meld reopen {pr.issue_number}` is now legal for {group.name}.")
    mark_submitted(ctx.store.home, group.name, "audit", number, {"outcome": "posted", "comment_id": resp.get("id"), "verdict": verdict}, when)
    return {"audited": number, "verdict": verdict, "issue": pr.issue_number, "comment_id": resp.get("id")}


def reopen(ctx, number: int) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    target = f"#{number}"
    issue = board.issues.get(number)
    if issue is None:
        refuse(ctx, board, Refusal(Verb.REOPEN, target, "issue is not in the active milestone"))
    refusal = issue_refusal(board, ctx.cfg, actor, Verb.REOPEN, issue, ctx.models)
    if refusal:
        refuse(ctx, board, refusal)
    fails = [c for c in board.comments if c.ok and c.header is not None and c.header.verb == "AUDIT" and c.header.ref == target and c.header.qualifier == "FAIL"]
    findings = fails[-1].text if fails else "(see the coordination issue)"
    ctx.gh.update_issue(number, state="open")
    if issue.assignees:
        ctx.gh.remove_assignees(number, list(issue.assignees))
    ctx.gh.add_labels(number, [READY])
    for label in (IN_REVIEW, IN_PROGRESS, BLOCKED):
        ctx.gh.remove_label(number, label)
    ctx.gh.create_comment(number, f"Meld: reopened by `{actor.name}` after a failed audit.\n\n{findings}")
    return {"reopened": number, "by": actor.name}
```

`src/meld/status.py`: add

```python
def audit_summary(board: Board, cfg: Config, actor: Actor, now: datetime) -> dict:
    """What the calling group owes the audit rule (spec 14): merged PRs it did not author and has not yet audited,
    and whether the batch is due (audit.batch_merges pending, or the oldest pending merge older than audit.batch_hours)."""
    if actor.group is None:
        return {"pending": [], "merges_pending": 0, "oldest_pending_hours": None, "due": False}
    pending = sorted((p for p in board.pulls.values()
                      if p.merged and party_of_login(cfg, p.author) != actor.group and actor.group not in board.audits.get(p.number, {})),
                     key=lambda p: p.number)
    ages = [_age_minutes(p.merged_at, now) for p in pending if p.merged_at]
    hours = round(max(ages) / 60, 1) if ages else None
    due = bool(pending) and (len(pending) >= cfg.audit_batch_merges or (hours is not None and hours >= cfg.audit_batch_hours))
    return {"pending": [p.number for p in pending], "merges_pending": len(pending), "oldest_pending_hours": hours, "due": due}
```

and `"audit": audit_summary(board, cfg, actor, now),` in the dict `render_status` returns (import `Board`, `Config`, `Actor` as needed).

`src/meld/cli.py`: import `from meld.audit import audit as do_audit, reopen as do_reopen` and add:

```python
@main.command()
@click.argument("number", type=int)
@click.pass_context
def audit(ctx: click.Context, number: int) -> None:
    """Post the collected auditor verdict on a merged PR as an AUDIT comment (PASS or FAIL)."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_audit(c, number))


@main.command()
@click.argument("number", type=int)
@click.pass_context
def reopen(ctx: click.Context, number: int) -> None:
    """Reopen a closed issue after a failed audit and return it to ready."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_reopen(c, number))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_audit.py tests/test_config.py tests/test_status.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/meld/audit.py src/meld/config.py src/meld/status.py src/meld/cli.py tests/test_audit.py tests/test_config.py tests/test_status.py
git commit -m "feat: meld audit posts auditor verdicts, meld reopen returns failed work to ready, status shows the audit schedule"
```

---

### Task 9: `meld challenge` — open, answer, and close challenges with the label following

**Files:**
- Create: `src/meld/challenge.py`
- Modify: `src/meld/cli.py`
- Test: `tests/test_challenge.py` (new)

**Interfaces:**
- Consumes: `rules.challenge_rule/challenged_party/CHALLENGE_ACTIONS` (Task 4), `board.CHALLENGED_LABEL`, `grammar.Header/compose/validate`, `posting.PostError`, `GitHub.add_labels/remove_label/create_comment`.
- Produces: `challenge.ACTION_VERB = {"open": "CHALLENGE", "evidence": "EVIDENCE", "retract": "RETRACT", "uphold": "UPHELD", "dismiss": "DISMISSED"}`; `challenge.challenge(ctx, action, ref, text) -> dict` returning `{"challenge", "ref", "verb", "comment_id", "label": "added"|"removed"|None, "challenged_party"}`; CLI `meld challenge ACTION REF -m TEXT | --body-file F`.

- [ ] **Step 1: Write the failing tests**

`tests/test_challenge.py`:

```python
import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.actions import Refused
from meld.challenge import challenge
from meld.posting import PostError
from tests.conftest import env_for
from tests.productfixtures import R, fixture, make_product

OPENED = fixture("comments_10.json") + [{"id": 150, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T11:00:00Z",
                                          "body": "[claude] CHALLENGE #13\n\nthe CI claim names no command"}]


def coord_posts(fake):
    return [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]


def test_open_challenge_posts_and_labels(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")   # claude doubts codex's in-review #13
    fake.route("POST", R + "issues/13/labels", [])
    out = challenge(ctx, "open", "#13", "the CI claim names no command; show the run")
    assert out == {"challenge": "open", "ref": "#13", "verb": "CHALLENGE", "comment_id": 310, "label": "added", "challenged_party": "codex"}
    assert coord_posts(fake)[-1].startswith("[claude] CHALLENGE #13\n") and "show the run" in coord_posts(fake)[-1]
    assert [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/labels"] == [{"labels": ["meld:challenged"]}]
    with pytest.raises(PostError, match="say what is doubted"):
        challenge(ctx, "open", "#11", "   ")
    fake_pm, pm, *_ = make_product(tmp_path / "pm")
    with pytest.raises(Refused, match="only an orchestrator or a human"):
        challenge(pm, "open", "#11", "doubt")


def test_challenge_closers_follow_the_rules(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    fake.route("GET", R + "issues/10/comments", OPENED)
    with pytest.raises(Refused, match="challenger cannot decide"):
        challenge(ctx, "uphold", "#13", "I say so")
    fake_c, codex, *_ = make_product(tmp_path / "c", group="codex", role="orchestrator")
    fake_c.route("GET", R + "issues/10/comments", OPENED)
    with pytest.raises(PostError, match="verified-by"):
        challenge(codex, "evidence", "#13", "trust me")
    out = challenge(codex, "evidence", "#13", "act -j ci passed locally\n\nverified-by: act -j ci -> passed")
    assert out["verb"] == "EVIDENCE" and out["label"] is None and coord_posts(fake_c)[-1].startswith("[codex] EVIDENCE #13\n")
    with pytest.raises(Refused, match="challenged party cannot decide"):
        challenge(codex, "dismiss", "#13", "wrong")
    fake_h, human, *_ = make_product(tmp_path / "h", group=None)
    fake_h.route("GET", R + "issues/10/comments", OPENED)
    fake_h.route("DELETE", R + "issues/13/labels/meld%3Achallenged", [])
    out = challenge(human, "dismiss", "#13", "the evidence holds")
    assert out["verb"] == "DISMISSED" and out["label"] == "removed" and coord_posts(fake_h)[-1].startswith("[human] DISMISSED #13\n")
    fake_r, claude2, *_ = make_product(tmp_path / "r", role="orchestrator")
    fake_r.route("GET", R + "issues/10/comments", OPENED)
    fake_r.route("DELETE", R + "issues/13/labels/meld%3Achallenged", [])
    assert challenge(claude2, "retract", "#13", "my mistake")["label"] == "removed"


def test_challenge_on_an_agreement_ref_labels_nothing(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    fake.route("GET", R + "issues/10/comments", fixture("comments_10.json") + [
        {"id": 151, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T11:00:00Z", "body": "[codex] PROPOSE plan-3\n\nsplit the login work"}])
    out = challenge(ctx, "open", "plan-3", "the split ignores the shared session store")
    assert out["label"] is None and out["challenged_party"] == "codex"
    assert not [c for c in fake.calls if c.method == "POST" and "labels" in c.endpoint]


def test_cli_challenge(tmp_path, monkeypatch):
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    fake.route("POST", R + "issues/13/labels", [])
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(repo / ".meld.toml"), "--compact", "challenge", "open", "#13", "-m", "show the run"],
                                env=env_for(tmp_path, role="orchestrator"))
    assert result.exit_code == 0, result.output
    assert '"verb": "CHALLENGE"' in result.output
    bad = CliRunner().invoke(cli.main, ["--config", str(repo / ".meld.toml"), "--compact", "challenge", "uphold", "#13", "-m", "x"],
                             env=env_for(tmp_path, role="orchestrator"))
    assert bad.exit_code == 2 and '"refused"' in bad.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_challenge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.challenge'`.

- [ ] **Step 3: Implement**

`src/meld/challenge.py`:

```python
"""meld challenge: open a CHALLENGE on a claim, answer it with EVIDENCE, or close it (RETRACT, UPHELD, DISMISSED)."""
from __future__ import annotations

from meld.actions import refuse
from meld.board import CHALLENGED_LABEL
from meld.grammar import Header, compose, validate
from meld.posting import PostError
from meld.rules import CHALLENGE_ACTIONS, Refusal, Verb, challenge_rule, challenged_party
from meld.status import build_status

ACTION_VERB = {"open": "CHALLENGE", "evidence": "EVIDENCE", "retract": "RETRACT", "uphold": "UPHELD", "dismiss": "DISMISSED"}
CLOSERS = ("retract", "uphold", "dismiss")


def _number(ref: str) -> int | None:
    return int(ref[1:]) if ref.startswith("#") and ref[1:].isdigit() else None


def challenge(ctx, action: str, ref: str, text: str) -> dict:
    if action not in CHALLENGE_ACTIONS:
        raise PostError(f"action must be one of {', '.join(CHALLENGE_ACTIONS)}")
    if not text.strip():
        raise PostError("say what is doubted, shown, or decided in -m")
    _, board = build_status(ctx)
    actor = ctx.actor()
    header = Header(actor.party, ACTION_VERB[action], ref, None)
    errors = validate(header, text)          # EVIDENCE needs a verified-by: line; every challenge verb needs its ref
    if errors:
        raise PostError("; ".join(errors))
    rule = challenge_rule(board, ctx.cfg, actor, action, ref)
    if rule:
        refuse(ctx, board, Refusal(Verb.CHALLENGE, ref, rule))
    resp = ctx.gh.create_comment(board.coordination_issue, compose(header, text))
    n = _number(ref)
    label = None
    if n is not None and (n in board.issues or n in board.pulls):   # the label rides on the challenged issue or PR (spec 14)
        if action == "open":
            ctx.gh.add_labels(n, [CHALLENGED_LABEL])
            label = "added"
        elif action in CLOSERS:
            ctx.gh.remove_label(n, CHALLENGED_LABEL)
            label = "removed"
    return {"challenge": action, "ref": ref, "verb": ACTION_VERB[action], "comment_id": resp.get("id"), "label": label,
            "challenged_party": challenged_party(board, ctx.cfg, ref)}
```

`src/meld/cli.py`: import `from meld.challenge import challenge as do_challenge` and add:

```python
@main.command()
@click.argument("action", type=click.Choice(["open", "evidence", "retract", "uphold", "dismiss"]))
@click.argument("ref")
@click.option("-m", "--message", "message", default=None, help="The doubted claim, the evidence (with a verified-by: line), or the decision.")
@click.option("--body-file", type=click.Path(path_type=Path, exists=True), default=None, help="Read the text from a file.")
@click.pass_context
def challenge(ctx: click.Context, action: str, ref: str, message: str | None, body_file: Path | None) -> None:
    """Open a CHALLENGE on a claim, answer it with EVIDENCE, or close it; the meld:challenged label follows."""
    if message is not None and body_file is not None:
        raise click.UsageError("-m/--message and --body-file are mutually exclusive")
    c = _load(ctx)
    text = body_file.read_text() if body_file else (message or "")
    try:
        emit(_guard(lambda: do_challenge(c, action, ref, text)), ctx.obj["pretty"])
    except Refused as e:
        emit(_refusal_json(e), ctx.obj["pretty"])
        ctx.exit(2)
    except PostError as e:
        emit({"error": str(e)}, ctx.obj["pretty"])
        ctx.exit(2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_challenge.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/meld/challenge.py src/meld/cli.py tests/test_challenge.py
git commit -m "feat: meld challenge opens, answers, and closes challenges with the closer rules and the label"
```

---

### Task 10: `meld status` shows what the review, audit, and challenge loops need

**Files:**
- Modify: `src/meld/status.py`
- Test: `tests/test_status.py`

**Interfaces:**
- Consumes: `PullRequest.files/merged/merged_at/touches`, `Board.challenges/audits`, `Config.security_paths`.
- Produces: each entry of `status["pulls"]` gains `"merged": bool`, `"merged_at": str`, `"files": int`, `"touches_security_paths": bool`, `"audited_by": {group: verdict}`; `status["coordination"]["challenges"]` is a list of `{"ref", "challenger", "comment_id", "opened_at"}` sorted by ref (alongside the existing `open_challenges`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_status.py`:

```python
def test_status_pull_fields_and_challenge_details():
    cfg = replace(CFG2, security_paths=("src/auth/*",))
    pr = replace(mk_pr(20, 13), files=("src/auth/login.py",))
    ch = Challenge("#20", "codex", 104, "2026-09-03T10:04:00Z")
    board = replace(mk_board(cfg, pulls=[pr]), challenges={"#20": ch}, open_challenges=frozenset({"#20"}), audits={20: {"codex": "pass"}})
    data = render_status(board, cfg, CLAUDE_PM, REG, datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc))
    (p,) = data["pulls"]
    assert p["touches_security_paths"] is True and p["files"] == 1 and p["merged"] is False and p["merged_at"] == "" and p["audited_by"] == {"codex": "pass"}
    assert data["coordination"]["challenges"] == [{"ref": "#20", "challenger": "codex", "comment_id": 104, "opened_at": "2026-09-03T10:04:00Z"}]
    assert data["coordination"]["open_challenges"] == ["#20"]
```

(imports: `from dataclasses import replace`, `from meld.board import Challenge`.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_status.py -v`
Expected: FAIL with `KeyError: 'touches_security_paths'`.

- [ ] **Step 3: Implement**

In `render_status`, extend each pulls entry:

```python
         "merged": p.merged, "merged_at": p.merged_at, "files": len(p.files),
         "touches_security_paths": p.touches(cfg.security_paths), "audited_by": dict(board.audits.get(p.number, {})),
```

and the coordination block:

```python
                         "challenges": [{"ref": c.ref, "challenger": c.challenger, "comment_id": c.comment_id, "opened_at": c.opened_at}
                                        for c in sorted(board.challenges.values(), key=lambda c: c.ref)],
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_status.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/meld/status.py tests/test_status.py
git commit -m "feat: status lists security-path PRs, audits per group, and open challenge details"
```

---

### Task 11: Product standards and skills in every brief and role prompt

**Files:**
- Modify: `src/meld/brief.py`, `src/meld/prompts.py`, `src/meld/dispatch.py`, `src/meld/adapters/claude.py`, `src/meld/adapters/codex.py` (only if the verification below says so), `src/meld/prompts/_worker_contract.md`, `src/meld/prompts/_preamble.md`, `src/meld/prompts/workers/reviewer.md`
- Test: `tests/test_brief.py`, `tests/test_prompts.py`, `tests/test_role_prompts.py`, `tests/test_adapter_claude.py`, `tests/test_dispatch.py`

**Interfaces:**
- Consumes: `roster.WorkerType` (`source == "skill"` marks adapter-installed skills), `Context.roster_entries()`.
- Produces: `brief.STANDARDS_FILE = ".meld/STANDARDS.md"`, `brief.DEFAULT_STANDARDS: str`, `brief.product_standards(repo_root) -> str` (file text stripped, else the default); `render_brief(..., skills: Sequence[WorkerType] = ())` renders `## Product standards` (always) and `## Skills available` (when any); `prompts.role_prompt` renders `## Product standards` before `## Your roster`; `prompts.COMMANDS` lists the plan-3 commands; the Claude adapter passes `--setting-sources user,project`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_brief.py`:

```python
from meld.brief import DEFAULT_STANDARDS, product_standards
from meld.roster import WorkerType


def test_brief_carries_product_standards_and_skills(tmp_path):
    issue = mk_issue(11)
    skills = [WorkerType("brand", "House brand voice and visual identity.", "skill", None, ""),
              WorkerType("security-review", "OWASP-style review of a change.", "skill", None, "")]
    text = render_brief(role=BACKEND, kind="build", group="claude", model="m", effort="medium", issue=issue, pr=None, plan_text="",
                        repo_root=tmp_path, worktree=tmp_path, base_branch="main", skills=skills)
    assert "## Product standards" in text and DEFAULT_STANDARDS.splitlines()[0] in text
    assert "## Skills available" in text and "- `brand`: House brand voice and visual identity." in text and "Invoke a skill by name" in text
    (tmp_path / ".meld").mkdir()
    (tmp_path / ".meld" / "STANDARDS.md").write_text("# Widgets standards\n\nEvery screen ships with a Lighthouse score above 90.\n")
    custom = render_brief(role=BACKEND, kind="build", group="claude", model="m", effort="medium", issue=issue, pr=None, plan_text="",
                          repo_root=tmp_path, worktree=tmp_path, base_branch="main")
    assert "Lighthouse score above 90" in custom and DEFAULT_STANDARDS.splitlines()[0] not in custom and "## Skills available" not in custom
    assert product_standards(tmp_path).startswith("# Widgets standards")
```

(`BACKEND` is a `WorkerType` the file already builds, or `next(w for w in builtin_workers() if w.name == "backend")`.)

Append to `tests/test_role_prompts.py`:

```python
def test_role_prompt_has_product_standards_and_the_plan_3_commands(tmp_path):
    text = role_prompt("pm", CFG2, CFG2.groups["claude"], tmp_path)
    assert "## Product standards" in text and "## Your roster" in text and text.index("## Product standards") < text.index("## Your roster")
    for cmd in ("meld review N", "meld merge N", "meld audit N", "meld reopen N", "meld challenge <open|evidence|retract|uphold|dismiss> REF -m TEXT",
                "meld dispatch N [--kind review|audit] [--worker security]"):
        assert cmd in text, cmd
    (tmp_path / ".meld").mkdir()
    (tmp_path / ".meld" / "STANDARDS.md").write_text("Ship dark mode everywhere.\n")
    assert "Ship dark mode everywhere." in role_prompt("orchestrator", CFG2, CFG2.groups["claude"], tmp_path)
```

Append to `tests/test_prompts.py` (the worker-prompt tests):

```python
def test_worker_contract_and_preamble_carry_the_quality_bar():
    contract = worker_contract()
    assert "Meet the product standards" in contract and "Skills available" in contract
    reviewer = next(w for w in builtin_workers() if w.name == "reviewer").text
    assert "correctness, security, performance, visual design and accessibility" in reviewer and "primary concern" in reviewer
    preamble = resources.files("meld").joinpath("prompts", "_preamble.md").read_text()
    assert "Ship for real users" in preamble and "meld challenge" in preamble
```

In `tests/test_adapter_claude.py`, change the argv assertion for setting sources to `"--setting-sources", "user,project"`.

In `tests/test_dispatch.py`, add to an existing build-dispatch test (e.g. the one that inspects `adapter.launches[0]`): dispatch with `FakeAdapter(skills=[WorkerType("brand", "Brand voice.", "skill", None, "")])` and assert the written brief contains `"## Skills available"` and `` "- `brand`: Brand voice." `` (a FakeAdapter with no skills must yield no such section).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_brief.py tests/test_role_prompts.py tests/test_prompts.py tests/test_adapter_claude.py -v`
Expected: FAIL (`cannot import name 'DEFAULT_STANDARDS'`, missing sections, old `--setting-sources project`).

- [ ] **Step 3: Implement**

`src/meld/brief.py`:

```python
STANDARDS_FILE = ".meld/STANDARDS.md"
DEFAULT_STANDARDS = """Build for real users, not for a demo. Visual design, performance, and security are acceptance criteria with the same weight as correctness: anything a user sees must look intentional, consistent, and accessible and respond quickly; every input, secret, and dependency is handled as if the product were already public.
Decide with industry standards, current documentation, and your own research rather than asking. Surface only decisions that need a human's approval (money, credentials, product direction, irreversible changes) through the framework's needs-human path.
Where a skill is available for the task at hand (design, security review, performance, a framework's conventions), use it before improvising, and say which one you used."""


def product_standards(repo_root: Path) -> str:
    """The product's own quality bar (.meld/STANDARDS.md) when it has one, else the framework default."""
    f = repo_root / STANDARDS_FILE
    return f.read_text().strip() if f.is_file() else DEFAULT_STANDARDS
```

`render_brief` gains `skills: Sequence[WorkerType] = ()` (import `Sequence` from `typing`); after the verification-bar block:

```python
    parts += ["## Product standards", "", product_standards(repo_root), ""]
    if skills:
        parts += ["## Skills available", ""] + [f"- `{s.name}`: {s.description}" for s in skills] + \
                 ["", "Invoke a skill by name when it matches the task; do not reinvent what a skill already does well.", ""]
```

`src/meld/dispatch.py`: in both `_dispatch_build` and `_dispatch_pr`, pass `skills=[w for w in ctx.roster_entries() if w.source == "skill"]` to `render_brief`.

`src/meld/prompts.py`: import `product_standards` from `meld.brief`; update `COMMANDS`:

```python
COMMANDS = ("meld status", "meld wait --filter <kinds> [--timeout S]", "meld post VERB [REF] -m TEXT [--joint]", "meld claim N",
            "meld release N", "meld dispatch N [--kind review|audit] [--worker security]", "meld tick", "meld collect N",
            "meld review N", "meld merge N", "meld audit N", "meld reopen N", "meld reap N",
            "meld challenge <open|evidence|retract|uphold|dismiss> REF -m TEXT",
            "meld tag N [--worker W] [--model M] [--effort E] [--size S]", "meld roster", "meld models")
```

and in `role_prompt`, insert `"", "## Product standards", "", product_standards(repo_root),` immediately before `"", "## Your roster", "",`.

`src/meld/prompts/_worker_contract.md`: append two items:

```markdown
9. **Meet the product standards** in the `## Product standards` section above. Visual design, performance, and security are part of done, not polish for later; decide with best practice and research, and reserve `needs_human` for decisions that genuinely need a person (money, credentials, product direction, irreversible changes).
10. **Use the skills listed under `## Skills available`** when one matches your task, and name the skill you used in your summary. A skill that exists for the job beats improvising the job.
```

`src/meld/prompts/_preamble.md`: add cardinal rule 8 after rule 7:

```markdown
8. **Ship for real users.** Visual design, performance, and security are acceptance criteria you plan for, tag for, review for, and audit for; they are never polish for later. Decide with industry standards and research; surface to the human only what needs approval (money, credentials, product direction, irreversible changes).
```

and in "The command surface", replace the `meld claim N, ...` bullet with:

```markdown
- `meld claim N`, `meld release N`, `meld dispatch N [--kind review|audit] [--worker security]`, `meld tick`, `meld collect N`, `meld review N`, `meld merge N`, `meld audit N`, `meld reopen N`, `meld reap N`, `meld tag N ...`, `meld roster`, `meld models`.
- `meld challenge <open|evidence|retract|uphold|dismiss> REF -m "..."` — the only way to open, answer, or close a `CHALLENGE`; the `meld:challenged` label follows automatically. `meld post` refuses the challenge verbs.
```

`src/meld/prompts/workers/reviewer.md`: replace the `## Focus` paragraph with:

```markdown
Another group built this. Your job is to find what they missed, in this order of priority: correctness, security, performance, visual design and accessibility (for anything a user sees), tests, then repository conventions. If your brief names a **primary concern**, spend your effort there first; other groups cover the rest, and you still report anything serious you notice. You are not here to restyle their code, but a user-facing change that looks unfinished or performs badly is a real finding, not a style nit. Use the skills listed in your brief (design, security, performance) when they match.
```

`src/meld/adapters/claude.py`: change `"--setting-sources", "project"` to `"--setting-sources", "user,project"` and add a comment: `# user: installed skills (~/.claude/skills, plugin caches) load; hooks stay disabled and MCP stays empty via _SETTINGS/_MCP_CONFIG`.

`src/meld/adapters/codex.py`: run `codex exec --help` (read-only) if the binary is present and read the description of `--ignore-user-config`. If it states that user config includes the skills directory (or you can show with `codex exec --help` / the docs that skills are only discovered through user config), remove the flag and add `"-c", "mcp_servers={}"` to keep MCP servers off; otherwise leave the adapter unchanged. Record the exact help text you saw in your report and adjust `tests/test_adapter_codex.py` only if the argv changed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_brief.py tests/test_role_prompts.py tests/test_prompts.py tests/test_adapter_claude.py tests/test_adapter_codex.py tests/test_dispatch.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/meld/brief.py src/meld/prompts.py src/meld/dispatch.py src/meld/adapters/claude.py src/meld/adapters/codex.py src/meld/prompts tests/test_brief.py tests/test_role_prompts.py tests/test_prompts.py tests/test_adapter_claude.py tests/test_adapter_codex.py tests/test_dispatch.py
git commit -m "feat: product standards and available skills in every brief and role prompt; user skills reach Claude workers"
```

---

### Task 12: Orchestrator and PM loops for review, merge, audit, and challenges

**Files:**
- Modify: `src/meld/prompts/orchestrator.md`, `src/meld/prompts/pm.md`
- Test: `tests/test_role_prompts.py`

**Interfaces:**
- Consumes: the `meld status` fields from Tasks 8 and 10 (`audit.pending/due`, `pulls[].touches_security_paths/audited_by/legal`, `coordination.challenges`) and the commands from Tasks 5–9.
- Produces: prompt text only (no code), asserted by substring tests.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_role_prompts.py`:

```python
def test_role_prompts_describe_the_review_merge_audit_and_challenge_loops(tmp_path):
    pm = role_prompt("pm", CFG2, CFG2.groups["claude"], tmp_path)
    for needle in ("`meld review N` right after `meld collect N` finishes a review run", "`meld merge N` for every PR whose `legal` includes `merge`",
                   "`--worker security`", "`audit.due`", "`meld audit N`", "`meld reopen", "ping-pong"):
        assert needle in pm, needle
    orch = role_prompt("orchestrator", CFG2, CFG2.groups["claude"], tmp_path)
    for needle in ("meld challenge open", "meld challenge uphold", "the human adjudicates", "AUDIT #<pr> FAIL", "visual design, performance, and security",
                   "security_paths"):
        assert needle in orch, needle
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_role_prompts.py -v`
Expected: FAIL on the first missing needle.

- [ ] **Step 3: Edit `pm.md`**

Replace step 3 of `## Your loop` with:

```markdown
3. In the `executing` phase, in this order:
   - `meld collect N` for every run whose state is `finished`. Collect never waits on you: a finished run is either pushed and reviewed or marked blocked; leaving it uncollected wastes the concurrency slot.
   - `meld review N` right after `meld collect N` finishes a review run, and `meld audit N` right after it finishes an audit run. The verdict file waits for you but nobody else reads it; a review that is not submitted is a review that never happened.
   - `meld merge N` for every PR whose `legal` includes `merge`. Whichever PM sees it first merges; a 409 means the head moved, so run `meld status` again.
   - `meld dispatch N --kind review` for every `REVIEW-REQUEST` from another group that `meld status` shows as legal (you cannot review your own group's head). When `pulls[].touches_security_paths` is true, dispatch a second review with `--worker security` after your first review is submitted.
   - `meld dispatch N --kind audit` for each PR in `audit.pending` when `audit.due` is true (one at a time, within `review_concurrency`), then `meld audit N` after collecting. After your `AUDIT #<pr> FAIL`, run `meld reopen <issue>` so the work returns to `ready`, and tell the orchestrator.
   - `meld dispatch N` for every `in_progress` issue assigned to your bot with no live run. An issue that came back from `in-review` carries a request-changes review on its PR: read it before re-dispatching so the brief's plan excerpt is not the only context the worker gets.
   - `meld claim N` for `ready` issues while `claim` is legal; the caps and budgets are enforced for you, so claim until refused.
```

Replace the `## Review and audit runs` section with:

```markdown
## Review, merge, and audit

You dispatch reviewers and auditors, collect them, and turn their verdicts into GitHub state: `meld review N` submits an approve, a request-changes (the issue goes back to `in_progress` for the author group), or a push-fix (your fix is pushed, your group becomes the last committer, and a `REVIEW-REQUEST` asks the author group to review the new head). A review whose head has moved is marked stale and must be re-dispatched. After `review.pingpong_cap` rounds of request-changes on one PR (review ping-pong) the issue is `blocked` and the orchestrators must post a resolution. Never review your own group's head, never dispatch beyond `review_concurrency`, and re-dispatch a reviewer when the other group pushes a fix onto a PR you already reviewed. `meld audit N` posts `AUDIT #<pr> PASS|FAIL` on the coordination issue with the auditor's findings; audits run at the merge commit, batched by `audit.due`.
```

- [ ] **Step 4: Edit `orchestrator.md`**

In `## Planning`, extend the first bullet's acceptance-criteria clause: after "acceptance criteria," add "(for anything a user sees, criteria for visual design, performance, and security are mandatory, not optional)". In the rubric table add a row:

```markdown
| Change under `review.security_paths`, or anything handling credentials, payments, or personal data | large | high | M | and the PM adds a `security` review |
```

Replace the first bullet of `## Keeping the record honest` with:

```markdown
- If another group asserts something you doubt, run `meld challenge open <ref> -m "<the doubted claim>"`; the target gains `meld:challenged` and cannot be dispatched or merged until it is resolved. The challenged party answers with `meld challenge evidence <ref> -m "..."` (with a `verified-by:` line) or concedes with `meld challenge retract <ref>`; you may withdraw with `retract` too. A party that is neither challenger nor challenged decides with `meld challenge uphold|dismiss <ref> -m "..."`; with two groups that means the human adjudicates, so ask in your `STATUS`-time summary if a challenge has waited a full cycle. `meld post` refuses the challenge verbs.
- Audits: when your PM posts `AUDIT #<pr> FAIL`, the reopened issue needs a new plan line or a re-tag; when another group's audit fails one of your PRs, answer with `EVIDENCE` or accept the reopen. Every merged PR is audited by a group that did not author it (`audit.pending` in `meld status`).
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_role_prompts.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/meld/prompts/orchestrator.md src/meld/prompts/pm.md tests/test_role_prompts.py
git commit -m "docs: role prompts run the review, merge, audit, and challenge loops"
```

---

### Task 13: Spec and README reconciliation; plan-4 obligations

**Files:**
- Modify: `docs/superpowers/specs/2026-09-03-meld-design.md`, `README.md`

**Interfaces:** documentation only; every statement must match the code on this branch (read the module before writing the sentence).

- [ ] **Step 1: Spec edits**

- §7 verb table: the `CHALLENGE … DISMISSED` row gains "posted only through `meld challenge`, which applies or clears `meld:challenged`; `meld post` refuses them"; the `IGNORED-COMMENT` row says "framework-posted; `meld post` refuses it". Note that `RETRACT` may come from the challenger (withdrawing) or the challenged party (conceding).
- §8: "review approve" bullet unchanged; add bullets "**review** (dispatch a review run): caller is a PM; PR open; caller's group is not the head committer's; PR not challenged; no HALT; within `review_concurrency`; no live or uncollected run on the PR", "**audit** (dispatch an audit run): caller is a PM; PR merged; caller's group did not author it; caller's group has not audited it; no HALT; within `review_concurrency`", "**reap**: caller is the PM whose collected, dead run it is; a build once its issue is closed or its PR merged or closed; a review or audit run once collected", "**tag**: caller is the orchestrator or a human; issue open", and "**challenge**: open by anyone but the claim's own party; EVIDENCE by the challenged party (or a human); RETRACT by challenger or challenged (or a human); UPHELD/DISMISSED by a party that is neither (with two groups, the human)". State that `meld status` lists all of these under `legal`.
- §11 (Post-run): a review or audit verdict is stored under `$MELD_HOME/results/<group>/<kind>-<n>.json` with GitHub's `head_sha` at dispatch; an unconsumed file displaced by a later collect moves to `superseded/`, a consumed one to `submitted/`; `meld review`/`meld audit` consume it. A verdict whose head moved is stale and must be re-dispatched.
- §13: rewrite steps 2–4 to the implemented flow: PMs dispatch `reviewer` (and `security` when `touches_security_paths`, sequentially); with three or more groups each reviewer gets a primary concern; `meld review` submits the GitHub review plus a `meld/review/<group>` commit status (success/failure/pending); request-changes returns the issue to `in-progress`, the cap to `blocked`; push-fix pushes from the review worktree and posts `REVIEW-REQUEST` for the new head; `meld merge` squash-merges with the head sha as a guard. Keep the quorum sentence.
- §14 Hallucination protection, Challenge bullet: replace with the closer rules above and the `meld challenge` command; Independent audit bullet: `meld audit` posts `AUDIT #<pr> PASS|FAIL`; `meld reopen` on FAIL; batching via `[audit] batch_merges`/`batch_hours` surfaced as `audit.due`; the auditor works at the merge commit.
- §5 `.meld.toml`: document `[audit] batch_merges = 5`, `batch_hours = 4`.
- §16: the Claude adapter's `--setting-sources user,project` and why; the Codex finding from Task 11.
- §17: add the row "CI red | PM via `pulls[].checks` | `merge` is refused until green; treating red as request-changes is a plan-4 item". Add "Review verdict on a moved head | `meld review` | marked stale; re-dispatch".
- §18: `.meld/STANDARDS.md` is rendered into every brief and role prompt; the framework default text lives in `brief.DEFAULT_STANDARDS`.
- §19: add `test_review.py`, `test_merge.py`, `test_audit.py`, `test_challenge.py`, `test_rules_challenges.py`, `test_results.py` to the list.
- §21: mark borrowed items 3 and 4 landed (item 4 with the commit-status variation and its reason); mark plan-3 obligations 2, 5, 6 landed; keep 1 and 3 for plan 4. Add a new subsection "Carried into plan 4" listing: decide/remember/recall (§15), chat (§14), plan gate (`meld plan`, the approve flip to `ready`, PLAN-ADD ACK), setup (§20 step 3, branch protection incl. the `meld/review/*` statuses being optional), doctor (§21 item 3 plus command-timeout ceilings and a `results/`/`runs/<n>-gh` scratch sweep), notifier (`[notify]`), CI red as request-changes, off-protocol change detection (§17 last row), `IGNORED-COMMENT` posting in the PM `STATUS`, the same-group lease re-claim window, worker-authored commit messages closing issues on merge, ResourceWarnings under `-W error`, the joint-comment author check (`Agreement.joint_verified`).

- [ ] **Step 2: README**

Extend the verbs table with `meld review N`, `meld merge N`, `meld audit N`, `meld reopen N`, `meld challenge ACTION REF -m TEXT`, and `--worker security` on dispatch. Add a section:

```markdown
## Product standards

Put the product's quality bar in `.meld/STANDARDS.md`; it is rendered into every worker brief and both role prompts. Without one, Meld's default applies: visual design, performance, and security are acceptance criteria with the same weight as correctness; agents decide with industry standards and research and surface only approval-worthy decisions; installed skills are used when they match the task.
```

- [ ] **Step 3: Verify**

Run: `uv run pytest -q` (all green, no warnings) and `uv run pytest tests/smoke -q -rs` (2 skipped).

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-09-03-meld-design.md README.md
git commit -m "docs: reconcile the spec and README with plan 3; list plan-4 obligations"
```

---

## Plan 3 exit criteria

- `uv run pytest -q` passes with no network, no real CLI, and no warnings; `tests/smoke` is opt-in and unchanged.
- Against a real repository prepared as in plan 1: after group A's `collect` opens a PR, group B's PM can `meld dispatch N --kind review`, `meld collect N`, `meld review N` (a GitHub review and a `meld/review/<B>` status appear), and either PM can `meld merge N` once the quorum rule holds; `meld dispatch N --kind audit` after the merge checks out the merge commit, and `meld audit N` posts `AUDIT #N PASS|FAIL`; `meld reopen` returns a failed issue to `ready`.
- `meld challenge open #N` labels the target and blocks its dispatch and merge; the closer rules refuse the wrong parties; `meld post CHALLENGE` is refused.
- `meld status` lists `review`, `audit`, `reap`, and `tag` under `legal`, shows `audit.pending/due`, `touches_security_paths`, and challenge details.
- Every worker brief and role prompt carries `## Product standards` (from `.meld/STANDARDS.md` or the default) and workers see `## Skills available`; Claude workers can load user-level skills.
- Plan 4 (decision memory, chat, plan gate, setup, doctor, notifier) is written against this plan's module names and the obligations list in spec §21.
