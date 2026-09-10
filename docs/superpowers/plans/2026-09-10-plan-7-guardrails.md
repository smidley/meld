# Plan 7: guardrails — write scope at merge, held categories at dispatch, memory supersession

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the human three Python-enforced guardrails that "LLM decides, Python enforces" was missing: a task's declared write scope and deletion allowlist are enforced when its PR merges; consequential action categories are withheld from workers until a listed human authorizes them; and a wrong decision or lesson is superseded, never edited, with recall and briefs showing only what is current.

**Architecture:** Declarations live where the orchestrator and the human already write and workers cannot: the issue body (`## Scope`, `## May delete`) and issue labels (`meld:held:<category>`). `board.py` derives `Issue.scope`/`may_delete`/`held`, `PullRequest.removed`, `Board.scope_violations`/`blocked_deletions`, and `Board.needs_human_answered`; `rules.py` decides (`_merge` refuses out-of-scope changes and unlisted deletions; `_dispatch` refuses a held issue until a human answered); `dispatch.tick` flips `meld:needs-human` off once a human answered (closing a pre-existing gap where the label had to be removed by hand); `meld tag` gains `--scope`, `--may-delete`, `--held`; briefs show the declared scope and the human-authorized categories. Memory gains supersession: `meld decide --supersedes` writes the new decision and appends one status trailer to the old file (a two-file memory PR the review verifies byte for byte), `meld remember --supersedes` names the older lesson's heading, and `recall`/briefs mark or drop superseded notes.

**Tech Stack:** Python 3.13, standard library plus click; `fnmatch` for globs; `FakeGh` in tests; real git repos and subprocesses in tests.

**Spec:** `docs/superpowers/specs/2026-09-03-meld-design.md` — §6 (the new "Issue bodies carry the declarations Python enforces" bullet), §8 (dispatch and merge bullets), §14 (needs-human unlock through `tick`), §15 (supersession bullet), §21 `### Carried into plan 6` items 11–13 (marked *Landing in plan 7*; Task 5 marks them landed).

## Global Constraints

- Python `>=3.12` (3.13 locally); runtime dependency `click>=8.1` only.
- `rules.py` is the only legality authority: no new `if actor...` checks outside it; `meld status` must list every legal verb.
- `github.py` is the only GitHub path (plus `appauth.py`'s two App-JWT calls); `gitops.py` the only git runner.
- Workers never hold credentials; anything a worker writes that reaches GitHub passes through `neutralise_closers`. A worker can never write a declaration (issue body or label) — only orchestrators and humans can.
- Tests are hermetic and real: real git repos and real subprocesses over `FakeGh`; no network, no real CLI, no host `~/.claude`/gitconfig/wall-clock dependence; inject `now`; pass `adapter_for=` to every `load_context` in tests. `uv run pytest -q -p no:cacheprovider` must be green with no warnings before every commit.
- Never set `MELD_SMOKE=1`.
- Conventional commit subjects, no attribution trailers, one logical change per commit; repo-local identity `Scott Brant <421573+smidley@users.noreply.github.com>`; commit with `git -C /Users/scottbrant/Documents/meld ...`.
- Baseline at the plan's base (branch `plan-6b-label-claim-lock` at 5247282, PR #10): `uv run pytest -q -p no:cacheprovider` → 589 passed, no warnings. Each task leaves the suite green.

## Decisions (rulings made while writing the plan; recorded so executors do not re-litigate)

1. **Declarations live in the issue body and labels, not in a new file or a comment.** The body is orchestrator/human-written and workers cannot touch it; labels arrive with the issue payload the board already fetches. `## Scope` holds globs (`fnmatch`, `*` crosses `/`, exactly `PullRequest.touches`' semantics) and `## May delete` holds exact paths, both as `- ` bullets; backticks around an item are stripped.
2. **Scope is opt-in per issue; the deletion gate is always on.** No `## Scope` means no scope check (a product adopts scopes gradually and the orchestrator prompt tells it to declare one). A deleted file is refused unless listed under `## May delete`, whether or not a scope is declared — deletions are rare and consequential. Renames are not deletions (GitHub reports them as `renamed`). Memory PRs are exempt (they never delete and close no issue).
3. **Enforcement is at merge; the warning is everywhere earlier.** `rules._merge` refuses; `meld status` shows `scope_violations` and `blocked_deletions` per PR; the build brief tells the worker its scope and allowlist; the review brief tells the reviewer what merge will refuse. Humans widen a scope by editing the body (or `meld tag`), never by a merge override.
4. **Held categories are a fixed vocabulary**: `delete`, `credential-change`, `production-mutation`, `public-release`, `paid-media`, `live-outreach`, `cleanup` (`board.HELD_CATEGORIES`); labels `meld:held:<category>`, created by `meld setup` (a static list, unlike claim labels). Offensive-security categories from the source note are out of Meld's domain.
5. **Authorization reuses the needs-human flow.** `meld tag N --held CATEGORY` adds the held labels and `meld:needs-human` and posts `NEEDS-HUMAN #N`; the human answers `RESOLVED #N` (or `PROVIDED #N`) exactly as for any needs-human item; `_dispatch` refuses a held issue until `Board.needs_human_answered(N)` — a listed human's answer newer than the newest `NEEDS-HUMAN #N` (or any answer when no such comment exists). The held labels stay as the record; the brief lists them as human-authorized. No new verb.
6. **`tick` closes the pre-existing needs-human gap.** Today nothing removes `meld:needs-human` after a human answers (the spec promised it; a hand edit did it). Any PM's `tick` now swaps `meld:needs-human` → `meld:in-progress` (claimed issue) or `meld:ready` (unclaimed) once `needs_human_answered`, posting nothing (the human's answer is the record); idempotent across PMs.
7. **Workers learn their limits in the brief and the contract.** `_worker_contract.md` gains a rule naming the held categories as forbidden unless the brief's `## Authorized by a human` section lists them; the build brief carries `## Scope` (globs, allowlist, "merge will refuse anything else") and `## Authorized by a human` (the issue's held categories — present only because dispatch was legal, i.e. a human answered).
8. **Supersession is a two-file memory PR for decisions, a first-line reference for lessons.** `meld decide <ref> --supersedes NNNN-<slug>.md` renders the new file with `- Supersedes: NNNN-<slug>.md` and appends `\n---\nStatus: superseded by MMMM-<slug>.md on YYYY-MM-DD\n` to the old file; `verify_memory_pr` accepts exactly that pair (new file byte-identical to the rendering; old file byte-identical to origin's content plus that one trailer). `meld remember ... --supersedes "<heading>"` puts `Supersedes: <heading>` as the entry's first line; lessons stay append-only and the existing verification is unchanged.
9. **Recall shows status; briefs drop superseded notes.** `memory.recall` hits carry `status` and `superseded_by`; `brief.recall_snippets` and `brief.decision_titles` skip superseded entries so `## Relevant memory` and `## Decisions in force` quote only current memory.
10. **The prompts carry the discipline, the code carries the marks.** `orchestrator.md`'s Memory section gains: "Memory primes hypotheses; it does not prove current state — read the file, grep the symbol, run the command before acting on a recalled note", plus the `--supersedes` usage; the Planning/tagging guidance gains "declare a `--scope` for every issue you tag; flag held categories with `--held`".

## File structure

| File | Responsibility |
|---|---|
| `src/meld/board.py` | `body_section`; `Issue.scope`/`may_delete`/`held`; `HELD_CATEGORIES`, `HELD_LABEL_PREFIX`, `held_label`; `PullRequest.removed`; `Board.scope_violations`, `blocked_deletions`, `human_answer`, `needs_human_answered` |
| `src/meld/github.py` | `pull_files` returns file entries (name, status, previous name) |
| `src/meld/assemble.py` | derives `files` and `removed` |
| `src/meld/status.py` | PR objects carry `scope_violations`, `blocked_deletions` |
| `src/meld/rules.py` | `_merge` scope and deletion refusals; `_dispatch` held gate |
| `src/meld/dispatch.py` | `tick` flips `meld:needs-human` once answered; build brief receives scope and authorized categories |
| `src/meld/brief.py` | `## Scope`, `## Authorized by a human`, review brief's scope; `recall_snippets`/`decision_titles` skip superseded |
| `src/meld/prompts/_worker_contract.md`, `orchestrator.md` | held rule; scope/held/supersedes/discipline guidance |
| `src/meld/tagging.py`, `src/meld/cli.py` | `--scope`, `--may-delete`, `--held`; `--supersedes` on `decide`/`remember` |
| `src/meld/setup.py` | held labels in `LABELS` |
| `src/meld/memory.py`, `src/meld/review.py` | supersession rendering, two-file verification, recall status |
| docs: spec §6/§8/§13/§14/§15/§21, `README.md`, `docs/HANDOFF.md` | landed items; plan-7 handoff |

---

### Task 1: Declarations and derivations on the board

**Files:**
- Modify: `src/meld/board.py` (`Issue` properties near `label_value` ~line 66; `PullRequest` fields ~133–150; `Board` methods), `src/meld/github.py:245-246` (`pull_files`), `src/meld/assemble.py:120,138-143`, `src/meld/status.py:55-60`
- Test: `tests/test_board.py`, `tests/test_assemble.py`, `tests/test_github.py:212`, `tests/test_status.py`

**Interfaces:**
- Produces: `board.body_section(body: str, heading: str) -> tuple[str, ...]`; `Issue.scope -> tuple[str, ...]`; `Issue.may_delete -> tuple[str, ...]`; `board.HELD_CATEGORIES: tuple[str, ...]`; `board.HELD_LABEL_PREFIX = "meld:held:"`; `board.held_label(category) -> str`; `Issue.held -> tuple[str, ...]`; `PullRequest.removed: tuple[str, ...] = ()`; `Board.scope_violations(pr) -> tuple[str, ...]`; `Board.blocked_deletions(pr) -> tuple[str, ...]`; `Board.human_answer(issue: int) -> ParsedComment | None`; `Board.needs_human_answered(issue: int) -> bool`; `GitHub.pull_files(number) -> list[dict]` (each `{"filename", "status", "previous_filename"}`).
- Consumes: `fnmatch` (already imported in `board.py`), `ParsedComment` (header `.verb`, `.ref`, `.party.kind`; `.ok`; `.id`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_board.py`:

```python
def test_issue_declarations_come_from_body_sections_and_held_labels():
    from meld.board import HELD_CATEGORIES, HELD_LABEL_PREFIX, body_section, held_label
    from tests.factories import mk_issue
    body = ("plan-item: p1\n\n## Acceptance criteria\n- works\n\n## Scope\n- src/meld/board.py\n- `tests/test_board*.py`\n\n"
            "## May delete\n- src/meld/legacy.py\n\n## Notes\n- not a scope line\n")
    assert body_section(body, "Scope") == ("src/meld/board.py", "tests/test_board*.py")
    assert body_section(body, "May delete") == ("src/meld/legacy.py",)
    assert body_section(body, "Missing") == () and body_section("", "Scope") == ()
    issue = mk_issue(12, status="in_progress", claimed_by="claude", body=body, extra_labels=("meld:held:delete", "meld:held:public-release"))
    assert issue.scope == ("src/meld/board.py", "tests/test_board*.py") and issue.may_delete == ("src/meld/legacy.py",)
    assert issue.held == ("delete", "public-release")
    assert held_label("delete") == HELD_LABEL_PREFIX + "delete" and "credential-change" in HELD_CATEGORIES and len(HELD_CATEGORIES) == 7
    assert mk_issue(13).scope == () and mk_issue(13).held == ()


def test_board_reports_scope_violations_and_blocked_deletions():
    from tests.factories import mk_board, mk_issue, mk_pr
    body = "## Scope\n- src/meld/*.py\n- tests/**\n\n## May delete\n- src/meld/legacy.py\n"
    scoped = mk_issue(12, status="in_review", claimed_by="claude", body=body)
    free = mk_issue(13, status="in_review", claimed_by="codex")
    pr_ok = mk_pr(20, 12, files=("src/meld/board.py", "tests/test_board.py"))
    pr_out = mk_pr(21, 12, files=("src/meld/board.py", "README.md", "docs/x.md"))
    pr_del = mk_pr(22, 12, files=("src/meld/board.py", "src/meld/legacy.py", "src/meld/old.py"), removed=("src/meld/legacy.py", "src/meld/old.py"))
    pr_free = mk_pr(23, 13, files=("anything/at/all.py", "gone.py"), removed=("gone.py",))
    board = mk_board(issues={12: scoped, 13: free}, pulls={20: pr_ok, 21: pr_out, 22: pr_del, 23: pr_free})
    assert board.scope_violations(pr_ok) == () and board.blocked_deletions(pr_ok) == ()
    assert board.scope_violations(pr_out) == ("README.md", "docs/x.md")
    assert board.blocked_deletions(pr_del) == ("src/meld/old.py",)          # legacy.py is listed, old.py is not
    assert board.scope_violations(pr_free) == ()                              # no scope declared: no scope check
    assert board.blocked_deletions(pr_free) == ("gone.py",)                   # the deletion gate is always on


def test_needs_human_answered_needs_a_listed_humans_answer_after_the_ask():
    from tests.factories import mk_board, mk_comment, mk_issue
    issue = mk_issue(14, status="needs_human", claimed_by="codex")
    ask = mk_comment(50, "meld-codex-bot", "[codex] NEEDS-HUMAN #14\n\nheld: delete", "2026-09-05T10:00:00Z")
    stranger = mk_comment(51, "someone", "[human] RESOLVED #14\n\ndone", "2026-09-05T10:05:00Z")
    early = mk_comment(49, "empowerScott", "[human] RESOLVED #14\n\nold answer", "2026-09-05T09:00:00Z")
    answer = mk_comment(52, "empowerScott", "[human] PROVIDED #14\n\nauthorized", "2026-09-05T10:10:00Z")
    other = mk_comment(53, "empowerScott", "[human] RESOLVED #15\n\nwrong issue", "2026-09-05T10:11:00Z")
    assert mk_board(issues={14: issue}, comments=[ask]).needs_human_answered(14) is False
    assert mk_board(issues={14: issue}, comments=[ask, stranger]).needs_human_answered(14) is False   # not a listed human
    assert mk_board(issues={14: issue}, comments=[early, ask]).needs_human_answered(14) is False      # answered before the ask
    assert mk_board(issues={14: issue}, comments=[ask, answer, other]).needs_human_answered(14) is True
    assert mk_board(issues={14: issue}, comments=[ask, answer]).human_answer(14).id == 52
    assert mk_board(issues={14: issue}, comments=[answer]).needs_human_answered(14) is True           # label without an ask: any answer counts
```

`mk_issue` gains `body: str = ""`; `mk_pr` gains `files: tuple[str, ...] = ()` and `removed: tuple[str, ...] = ()` (check its current signature and pass them through to `PullRequest`). `mk_comment(id, author, body, created_at)` already exists; `mk_board(comments=[...])` parses them (check how the factory builds `ParsedComment`s — a stranger's `[human]` comment must parse but not count as a listed human: `party_of_login`/`cfg.is_human` decides).

Replace `tests/test_github.py:212`'s assertion with `assert [f["filename"] for f in gh.pull_files(20)] == ["ci.yml", ".github/workflows/ci.yml"]` and add, in `tests/test_assemble.py`, a test that routes `pulls/20/files` with one `{"filename": "old.py", "status": "removed"}`, one `{"filename": "new.py", "status": "renamed", "previous_filename": "was.py"}` and one `{"filename": "k.py", "status": "modified"}`, then asserts `board.pulls[20].files == ("old.py", "new.py", "k.py")` and `board.pulls[20].removed == ("old.py",)`. In `tests/test_status.py`, assert a PR object carries `scope_violations` and `blocked_deletions` lists (empty for the fixture PR).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q -p no:cacheprovider tests/test_board.py tests/test_assemble.py tests/test_github.py tests/test_status.py -k "declarations or violations or needs_human_answered or files or scope"` → AttributeError/TypeError.

- [ ] **Step 3: Implement**

`src/meld/board.py` — constants next to `HUMAN_CLAIMANT`:

```python
HELD_LABEL_PREFIX = "meld:held:"
HELD_CATEGORIES = ("cleanup", "credential-change", "delete", "live-outreach", "paid-media", "production-mutation", "public-release")


def held_label(category: str) -> str:
    return HELD_LABEL_PREFIX + category


def body_section(body: str, heading: str) -> tuple[str, ...]:
    """The `- ` bullets under `## <heading>` in an issue body, up to the next heading; backticks stripped. Declarations
    live in the body because only orchestrators and humans can write it (spec 6): `## Scope`, `## May delete`."""
    out: list[str] = []
    active = False
    for line in (body or "").splitlines():
        if line.startswith("## "):
            active = line[3:].strip().lower() == heading.lower()
            continue
        if active and line.lstrip().startswith("- "):
            item = line.lstrip()[2:].strip().strip("`").strip()
            if item:
                out.append(item)
    return tuple(out)
```

`Issue` properties (beside `claimants`):

```python
    @property
    def scope(self) -> tuple[str, ...]:
        return body_section(self.body, "Scope")

    @property
    def may_delete(self) -> tuple[str, ...]:
        return body_section(self.body, "May delete")

    @property
    def held(self) -> tuple[str, ...]:
        """Held categories on this issue (spec 6): dispatch needs a listed human's answer while any is present."""
        return tuple(sorted(l[len(HELD_LABEL_PREFIX):] for l in self.labels if l.startswith(HELD_LABEL_PREFIX) and len(l) > len(HELD_LABEL_PREFIX)))
```

`PullRequest`: add `removed: tuple[str, ...] = ()   # files the PR deletes (GitHub status "removed"; a rename is not a deletion)` after `files`.

`Board` methods:

```python
    def scope_violations(self, pr: PullRequest) -> tuple[str, ...]:
        """Changed files outside the linked issue's declared `## Scope`; empty when no scope is declared or no issue is linked."""
        issue = self.issues.get(pr.issue_number) if pr.issue_number is not None else None
        if issue is None or not issue.scope:
            return ()
        return tuple(f for f in pr.files if not any(fnmatch(f, g) for g in issue.scope))

    def blocked_deletions(self, pr: PullRequest) -> tuple[str, ...]:
        """Deleted files the linked issue's `## May delete` does not list; the gate is always on for a PR that closes an issue."""
        issue = self.issues.get(pr.issue_number) if pr.issue_number is not None else None
        if issue is None:
            return ()
        return tuple(f for f in pr.removed if f not in issue.may_delete)

    def human_answer(self, issue: int) -> ParsedComment | None:
        """The newest `PROVIDED #issue` / `RESOLVED #issue` from a listed human (party kind human; strangers do not parse as one)."""
        answers = [c for c in self.comments if c.ok and c.header is not None and c.header.party.kind == "human"
                   and c.header.verb in ("PROVIDED", "RESOLVED") and c.header.ref == f"#{issue}"]
        return max(answers, key=lambda c: c.id) if answers else None

    def needs_human_answered(self, issue: int) -> bool:
        """A listed human answered after the newest `NEEDS-HUMAN #issue` (or at all, when no ask was posted) — the condition
        that unlocks a needs-human or held issue (spec 8, 14)."""
        answer = self.human_answer(issue)
        if answer is None:
            return False
        asks = [c.id for c in self.comments if c.ok and c.header is not None and c.header.verb == "NEEDS-HUMAN" and c.header.ref == f"#{issue}"]
        return not asks or answer.id > max(asks)
```

Check how comments from a login that is not a configured bot or listed human parse today (`grammar`/`assemble`): if such a `[human] RESOLVED` parses with `party.kind == "human"`, `human_answer` must additionally require the author to be a listed human — the board has no config, so `assemble` must already reject or mark it (look for `ok=False` on impostor parties; `party_of_login`). Mirror whatever the existing rule for `[human]` comments by strangers is, and state it in the docstring.

`src/meld/github.py`:

```python
    def pull_files(self, number: int) -> list[dict]:
        """Each changed file as {"filename", "status", "previous_filename"}; status is GitHub's (added, modified, removed, renamed...)."""
        return [{"filename": f["filename"], "status": f.get("status", ""), "previous_filename": f.get("previous_filename")}
                for f in self.get_paginated(self._r(f"pulls/{number}/files"))]
```

`src/meld/assemble.py:120`: `entries = gh.pull_files(number); files = tuple(e["filename"] for e in entries); removed = tuple(e["filename"] for e in entries if e["status"] == "removed")` and pass `removed=removed` to `PullRequest(...)` (keep the existing error containment around the fetch, if any).

`src/meld/status.py:55-60`: add `"scope_violations": list(board.scope_violations(p)), "blocked_deletions": list(board.blocked_deletions(p)),` to the PR object.

- [ ] **Step 4: Run the suite; commit**

Run: `uv run pytest -q -p no:cacheprovider` → green.

```bash
git -C /Users/scottbrant/Documents/meld add src/meld/board.py src/meld/github.py src/meld/assemble.py src/meld/status.py tests/
git -C /Users/scottbrant/Documents/meld commit -m "feat: the board derives scope, deletion allowlist, held categories, and human answers"
```

---

### Task 2: Merge and dispatch enforce; tick unlocks; briefs and the contract inform

**Files:**
- Modify: `src/meld/rules.py` (`_dispatch` ~117–146, `_merge` ~327–342), `src/meld/dispatch.py` (`tick` ~503–546; the build-brief call ~420), `src/meld/brief.py` (`render_brief` ~230–290), `src/meld/prompts/_worker_contract.md`
- Test: `tests/test_rules_issues.py`, `tests/test_rules_prs.py` (or wherever `_merge` is tested — `grep -rn "def test_merge" tests`), `tests/test_tick.py`, `tests/test_brief.py`

**Interfaces:**
- Consumes: Task 1's derivations.
- Produces: refusal texts `PR changes files outside #N's declared scope: a, b`, `PR deletes files not listed under '## May delete' in #N: a`, `held (delete, public-release): a listed human must post RESOLVED #N (or PROVIDED #N) first`; `tick` result key `"resumed": [issue numbers]`; `render_brief(..., scope: Sequence[str] = (), may_delete: Sequence[str] = (), authorized: Sequence[str] = ())`.

- [ ] **Step 1: Write the failing tests**

Merge (in the file that tests `_merge`; mirror its arrangement for a mergeable PR):

```python
def test_merge_refuses_out_of_scope_changes_and_unlisted_deletions():
    from tests.factories import mk_board, mk_issue, mk_pr
    body = "## Scope\n- src/meld/*.py\n\n## May delete\n- src/meld/legacy.py\n"
    issue = mk_issue(12, status="in_review", claimed_by="claude", body=body)
    ok = mk_pr(20, 12, files=("src/meld/board.py",), approvals=("meld-codex-bot",))          # whatever the file's mergeable arrangement is
    out = mk_pr(21, 12, files=("src/meld/board.py", "README.md"), approvals=("meld-codex-bot",))
    dele = mk_pr(22, 12, files=("src/meld/board.py", "src/meld/legacy.py", "src/meld/old.py"), removed=("src/meld/legacy.py", "src/meld/old.py"), approvals=("meld-codex-bot",))
    board = mk_board(CFG2, issues=[issue], pulls=[ok, out, dele], phase="executing")
    assert rule(board, CLAUDE_PM, Verb.MERGE, ok) is None
    assert rule(board, CLAUDE_PM, Verb.MERGE, out) == "PR changes files outside #12's declared scope: README.md"
    assert rule(board, CLAUDE_PM, Verb.MERGE, dele) == "PR deletes files not listed under '## May delete' in #12: src/meld/old.py"
```

Dispatch (in `tests/test_rules_issues.py`, in its `rule(...)` idiom):

```python
def test_dispatch_refuses_a_held_issue_until_a_listed_human_answers():
    from tests.factories import mk_board, mk_comment, mk_issue
    held = mk_issue(12, status="in_progress", claimed_by="claude", extra_labels=("meld:held:delete", "meld:held:public-release"))
    ask = mk_comment(50, "meld-claude-bot", "[claude] NEEDS-HUMAN #12\n\nheld: delete, public-release", "2026-09-05T10:00:00Z")
    answer = mk_comment(51, "empowerScott", "[human] RESOLVED #12\n\ngo ahead", "2026-09-05T10:05:00Z")
    assert rule(mk_board(CFG2, issues=[held], comments=[ask]), CLAUDE_PM, Verb.DISPATCH, held) == \
        "held (delete, public-release): a listed human must post RESOLVED #12 (or PROVIDED #12) first"
    assert rule(mk_board(CFG2, issues=[held], comments=[ask, answer]), CLAUDE_PM, Verb.DISPATCH, held) is None
```

Tick (in `tests/test_tick.py`, mirroring its fixture routing): issue 14 is `needs_human` in the fixture and claimed by codex; route `GET issues` with a human `RESOLVED #14` comment newer than the fixture's `NEEDS-HUMAN #14` (add it to the comments fixture the test loads), route `POST issues/14/labels` and `DELETE issues/14/labels/meld%3Aneeds-human`, run `tick`, and assert `out["resumed"] == [14]`, that `meld:in-progress` was added and `meld:needs-human` removed; a second scenario with the answer missing asserts `out["resumed"] == []` and no label calls for 14; a third with an unclaimed needs-human issue asserts `meld:ready` is added instead.

Brief (`tests/test_brief.py`): `render_brief(...)` for a build with `scope=("src/meld/*.py",)`, `may_delete=("src/meld/legacy.py",)`, `authorized=("delete",)` contains a `## Scope` section listing both, the sentence `merge will refuse changes to any other file`, and a `## Authorized by a human` section naming `delete`; with all three empty neither section appears. For a review brief (`kind="review"`, `pr=` set) with `scope=` given, a `## Scope declared by the issue` section appears.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q -p no:cacheprovider tests/test_rules_issues.py tests/test_rules_prs.py tests/test_tick.py tests/test_brief.py -k "scope or held or resumed or Authorized"` → fail.

- [ ] **Step 3: Implement**

`src/meld/rules.py` — in `_merge`, after the quorum check and before `return None`:

```python
    outside = board.scope_violations(pr)
    if outside:
        return f"PR changes files outside #{pr.issue_number}'s declared scope: {', '.join(outside[:5])}"
    blocked = board.blocked_deletions(pr)
    if blocked:
        return f"PR deletes files not listed under '## May delete' in #{pr.issue_number}: {', '.join(blocked[:5])}"
```

(`board.scope_violations` returns `()` when no issue is linked, so memory PRs are untouched.) In `_dispatch`, right after the claim check:

```python
    if issue.held and not board.needs_human_answered(issue.number):
        return f"held ({', '.join(issue.held)}): a listed human must post RESOLVED #{issue.number} (or PROVIDED #{issue.number}) first"
```

`src/meld/dispatch.py::tick` — before the `notify(...)` call:

```python
    out["resumed"] = []
    for issue in sorted(board.issues.values(), key=lambda i: i.number):
        if issue.status != "needs_human" or not board.needs_human_answered(issue.number):
            continue
        # Spec 14: the human's PROVIDED/RESOLVED is the record; the label just has to follow it. Any PM's tick does this,
        # idempotently (the second one finds no label), and posts nothing.
        _swap_labels(ctx, issue.number, IN_PROGRESS if issue.claimed_by else READY, (NEEDS_HUMAN,))
        out["resumed"].append(issue.number)
```

(`IN_PROGRESS`/`READY`/`NEEDS_HUMAN` constants exist in `dispatch.py` — check their names at the top of the module.) In the build-brief call (~420) pass `scope=issue.scope, may_delete=issue.may_delete, authorized=issue.held`; in the review/audit call (~478) pass `scope=` and `may_delete=` from the linked issue when `pr.issue_number` is on the board.

`src/meld/brief.py::render_brief` — add the parameters and, after the `## Files to start from` block:

```python
    if scope or may_delete:
        heading = "## Scope" if issue is not None else "## Scope declared by the issue"
        parts += [heading, ""]
        if scope:
            parts += ["You may change only files matching these globs; merge will refuse changes to any other file:", ""] + [f"- `{g}`" for g in scope] + [""]
        parts += (["Files you may delete (merge refuses any other deletion):", ""] + [f"- `{p}`" for p in may_delete] + [""]) if may_delete
                 else ["No deletions are allowed: merge refuses a PR that deletes a file this issue does not list under `## May delete`.", ""]
    if authorized:
        parts += ["## Authorized by a human", "", "A listed human has authorized these held categories for this task and no others:", ""] + [f"- {c}" for c in authorized] + [""]
```

`src/meld/prompts/_worker_contract.md` — a new numbered rule after rule 3 ("You have no GitHub credentials"): "**Held actions are forbidden unless authorized.** Deleting files, changing credentials or secrets, mutating a production system, publishing or releasing, spending money, contacting anyone outside this repository, and bulk cleanup are *held categories*: do none of them unless your brief's `## Authorized by a human` section names that category. If the task needs one that is not listed, stop and write the result file with `blocked` and `needs_human` naming the category." Renumber the following rules.

- [ ] **Step 4: Run the suite; commit**

Run: `uv run pytest -q -p no:cacheprovider` → green.

```bash
git -C /Users/scottbrant/Documents/meld add src/meld/rules.py src/meld/dispatch.py src/meld/brief.py src/meld/prompts/_worker_contract.md tests/
git -C /Users/scottbrant/Documents/meld commit -m "feat: merge enforces scope and the deletion gate, dispatch withholds held categories, tick unlocks answered needs-human issues"
```

---

### Task 3: `meld tag --scope / --may-delete / --held`, held labels in setup, orchestrator guidance

**Files:**
- Modify: `src/meld/tagging.py`, `src/meld/cli.py:306-316`, `src/meld/setup.py:28-39` (`LABELS`), `src/meld/prompts/orchestrator.md`
- Test: `tests/test_tagging.py` (or wherever `tag` is tested — `grep -rln "do_tag\|from meld.tagging" tests`), `tests/test_setup.py`

**Interfaces:**
- Produces: `board.replace_section(body, heading, items) -> str` (put it in `board.py` beside `body_section`); `tagging.tag(..., scope: Sequence[str] | None = None, may_delete: Sequence[str] | None = None, held: Sequence[str] | None = None)`; `tag`'s result gains `"scope"`, `"may_delete"`, `"held"`; CLI options `--scope GLOB` (repeatable), `--may-delete PATH` (repeatable), `--held CATEGORY` (repeatable, `click.Choice(HELD_CATEGORIES)`).
- Consumes: `GitHub.update_issue(number, body=...)`, `GitHub.add_labels`, `GitHub.create_comment`, `grammar.compose`/`Header`, `board.HELD_CATEGORIES`/`held_label`/`body_section`.

- [ ] **Step 1: Write the failing tests**

In the tagging tests (mirror the file's `make`/route idiom):

```python
def test_tag_writes_scope_and_may_delete_sections_into_the_body(tmp_path):
    fake, ctx = make(tmp_path, role="orchestrator")
    fake.route("PATCH", R + "issues/11", {"number": 11})
    out = tag(ctx, 11, scope=["src/meld/*.py", "tests/test_x.py"], may_delete=["src/meld/legacy.py"])
    patch = [c for c in fake.calls if c.method == "PATCH" and c.endpoint == R + "issues/11"][0]
    body = patch.body["body"]
    assert "## Scope\n- src/meld/*.py\n- tests/test_x.py\n" in body and "## May delete\n- src/meld/legacy.py\n" in body
    assert body.startswith(fixture_issue(11)["body"].rstrip("\n"))                 # the existing body text is kept
    assert out["scope"] == ["src/meld/*.py", "tests/test_x.py"] and out["may_delete"] == ["src/meld/legacy.py"]
    fake.route("GET", R + "issues", [{**i, "body": body} if i["number"] == 11 else i for i in fixture("issues.json")])
    out2 = tag(ctx, 11, scope=["docs/**"])                                         # a second call replaces the section, keeps the other
    body2 = [c for c in fake.calls if c.method == "PATCH"][-1].body["body"]
    assert "## Scope\n- docs/**\n" in body2 and "src/meld/*.py" not in body2 and "## May delete\n- src/meld/legacy.py\n" in body2


def test_tag_held_labels_the_issue_needs_human_and_posts_the_ask(tmp_path):
    fake, ctx = make(tmp_path, role="orchestrator")
    fake.route("POST", R + "issues/11/labels", [])
    fake.route("POST", R + "issues/10/comments", {"id": 900})
    out = tag(ctx, 11, held=["delete", "public-release"])
    labels = [c.body["labels"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/11/labels"]
    assert labels == [["meld:held:delete", "meld:held:public-release", "meld:needs-human"]]
    (ask,) = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]
    assert ask.startswith("[claude] NEEDS-HUMAN #11") and "delete, public-release" in ask and "RESOLVED #11" in ask
    assert out["held"] == ["delete", "public-release"]
    with pytest.raises(Refused, match="held category"):
        tag(ctx, 11, held=["nuke"])
```

`tests/test_setup.py`: the labels-created assertion includes all seven `meld:held:<category>` names.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q -p no:cacheprovider tests/test_tagging.py tests/test_setup.py -k "scope or held or labels"` → TypeError (unexpected keyword) / missing labels.

- [ ] **Step 3: Implement**

`src/meld/board.py`:

```python
def replace_section(body: str, heading: str, items: Sequence[str]) -> str:
    """Return `body` with its `## <heading>` section replaced by `items` as `- ` bullets (appended at the end when absent;
    removed when `items` is empty). Everything else in the body is kept byte for byte."""
    lines = (body or "").rstrip("\n").splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("## ") and l[3:].strip().lower() == heading.lower()), None)
    if start is not None:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        del lines[start:end]
        while lines and start < len(lines) and lines[start:start + 1] == [""] and (start == 0 or lines[start - 1] == ""):
            del lines[start]
    if items:
        if lines and lines[-1] != "":
            lines.append("")
        lines += [f"## {heading}"] + [f"- {i}" for i in items]
    return "\n".join(lines) + "\n"
```

`src/meld/tagging.py`: extend the signature with `scope`, `may_delete`, `held` (each `Sequence[str] | None`); accept a call with only those (`if not any(wanted.values()) and not skills and scope is None and may_delete is None and not held: refuse(... "provide at least one of --worker, --model, --effort, --size, --skill, --scope, --may-delete, --held")`); validate `held` against `HELD_CATEGORIES` (`refuse(... f"unknown held category '{c}'; one of {', '.join(HELD_CATEGORIES)}")`); after the existing label writes:

```python
    if scope is not None or may_delete is not None:
        body = issue.body
        if scope is not None:
            body = replace_section(body, "Scope", scope)
        if may_delete is not None:
            body = replace_section(body, "May delete", may_delete)
        ctx.gh.update_issue(number, body=body)
    if held:
        ctx.gh.add_labels(number, [held_label(c) for c in held] + [NEEDS_HUMAN])
        if not actor.is_human:
            ctx.gh.create_comment(board.coordination_issue, compose(
                Header(actor.party, "NEEDS-HUMAN", target, None),
                f"Held categories on #{number}: {', '.join(held)}. A listed human authorizes the worker with `RESOLVED #{number}` "
                f"(or `PROVIDED #{number}`); until then #{number} cannot be dispatched."))
```

(`NEEDS_HUMAN = "meld:needs-human"`; import `compose`, `Header` from `meld.grammar`, `held_label`, `replace_section`, `HELD_CATEGORIES` from `meld.board`.) The result dict gains `"scope": list(scope) if scope is not None else list(issue.scope)`, likewise `may_delete`, and `"held": sorted(set(issue.held) | set(held or ()))`.

`src/meld/cli.py` `tag`: add `@click.option("--scope", "scope", multiple=True, help="Glob the task may change; repeatable; replaces the issue's ## Scope.")`, `@click.option("--may-delete", "may_delete", multiple=True, help="Exact path the task may delete; repeatable.")`, `@click.option("--held", "held", multiple=True, type=click.Choice(HELD_CATEGORIES), help="Held category needing a human's RESOLVED before dispatch; repeatable.")`, passing `scope=scope or None, may_delete=may_delete or None, held=held` (a click tuple; empty means not given).

`src/meld/setup.py` `LABELS`: add one entry per category, `held_label(c): ("f9d0c4", f"Held: {c} — a listed human must answer RESOLVED before dispatch")`, built with a dict comprehension merged into `LABELS` (import `HELD_CATEGORIES`, `held_label`).

`src/meld/prompts/orchestrator.md`: in the tagging/rubric section add: "Declare each issue's write scope when you tag it — `meld tag N --scope 'src/area/**' --scope 'tests/test_area*.py'` — and list any file the task may delete with `--may-delete PATH`; merge refuses anything else. If the task must delete files, touch credentials, mutate production, publish, spend money, contact people outside the repo, or do bulk cleanup, flag it `--held <category>`: dispatch waits for the human's `RESOLVED #N`."

- [ ] **Step 4: Run the suite; commit**

Run: `uv run pytest -q -p no:cacheprovider` → green.

```bash
git -C /Users/scottbrant/Documents/meld add src/meld/board.py src/meld/tagging.py src/meld/cli.py src/meld/setup.py src/meld/prompts/orchestrator.md tests/
git -C /Users/scottbrant/Documents/meld commit -m "feat: meld tag declares scope, deletion allowlist, and held categories; setup creates the held labels"
```

---

### Task 4: Memory supersession

**Files:**
- Modify: `src/meld/memory.py` (`render_decision` ~48, `render_lesson` ~60, `decide` ~115, `remember` ~155, `recall` ~178), `src/meld/review.py::verify_memory_pr` (~61–113), `src/meld/brief.py` (`decision_titles` ~85, `_local_hits` ~159, `recall_snippets` ~210), `src/meld/cli.py` (`decide`, `remember` commands), `src/meld/prompts/orchestrator.md` (Memory section)
- Test: `tests/test_memory.py`, `tests/test_review.py`, `tests/test_brief.py`

**Interfaces:**
- Produces: `memory.render_decision(ref, agreement, url, date, supersedes: str | None = None)` (adds `- Supersedes: <file>` after `- Source:`); `memory.superseded_trailer(new_file, date) -> str` = `f"\n---\nStatus: superseded by {new_file} on {date}\n"`; `memory.render_lesson(ref, text, group, date, supersedes: str | None = None)` (first body line `Supersedes: <heading>`); `decide(ctx, ref, *, now, supersedes=None)`; `remember(ctx, ref, text, *, now, supersedes=None)`; `memory.STATUS_RE = re.compile(r"^Status: superseded by (\S+) on (\d{4}-\d{2}-\d{2})$", re.M)`; `memory.SUPERSEDES_RE = re.compile(r"^Supersedes: (.+)$", re.M)`; recall hits gain `"status"` and `"superseded_by"`; CLI `meld decide REF --supersedes FILE`, `meld remember REF -m TEXT --supersedes HEADING`.
- Consumes: `_open_memory_pr` (extend to write two files: a `paths: dict[str, tuple[str, str]]`-style second write, or two writes on the same branch — read its body and keep one commit), `ctx.gh.get_contents`, `_default_branch_files`.

- [ ] **Step 1: Write the failing tests**

`tests/test_memory.py` (mirror the file's `decide`/`remember` arrangements that route the memory-PR calls):

```python
def test_decide_supersedes_writes_the_new_file_and_one_trailer_on_the_old(tmp_path):
    # arrange as the existing decide happy-path test does, with `.meld/decisions/0001-dec-0.md` present on origin's default branch
    ...
    out = decide(ctx, "dec-1", now=NOW, supersedes="0001-dec-0.md")
    new_text = <the file written for dec-1>
    assert "- Supersedes: 0001-dec-0.md\n" in new_text and new_text.index("- Supersedes:") > new_text.index("- Source:")
    old_text = <the file written for 0001-dec-0.md on the memory branch>
    assert old_text == ORIGINAL_0001 + "\n---\nStatus: superseded by 0002-dec-1.md on 2026-09-05\n"
    assert out["supersedes"] == "0001-dec-0.md"
    with pytest.raises(Refused, match="not a decision file on"):
        decide(ctx, "dec-2", now=NOW, supersedes="0099-missing.md")


def test_remember_supersedes_names_the_older_entry_first(tmp_path):
    ...
    out = remember(ctx, "#13", "the new lesson", now=NOW, supersedes="2026-09-01 — #9 (codex)")
    entry = <the appended entry>
    assert entry.startswith("\n## 2026-09-05 — #13 (claude)\n\nSupersedes: 2026-09-01 — #9 (codex)\n\nthe new lesson\n")


def test_recall_marks_superseded_notes_and_briefs_skip_them(tmp_path):
    # two decision files on origin's default branch: 0001 carries the superseded trailer naming 0002; lessons.md has an old
    # entry and a newer one whose first line is `Supersedes: <old heading>`
    hits = recall(ctx, "widgets")
    by_title = {h["title"]: h for h in hits if h["kind"] in ("decision", "lesson")}
    assert by_title["Decision dec-0"]["status"] == "superseded" and by_title["Decision dec-0"]["superseded_by"] == "0002-dec-1.md"
    assert by_title["Decision dec-1"]["status"] == "active" and by_title["Decision dec-1"]["superseded_by"] is None
    assert by_title[<old lesson title>]["status"] == "superseded"
    assert "dec-0" not in " ".join(decision_titles(ctx.repo_root)) and "dec-1" in " ".join(decision_titles(ctx.repo_root))
    assert all("dec-0" not in s and <old lesson text> not in s for s in recall_snippets(ctx.repo_root, "widgets"))
```

(The `...` marks are the file's existing fixture arrangement — reuse it verbatim; the implementer names the helper used in the report.) `tests/test_review.py`: a decision memory PR whose `files` are the new file plus the old file with exactly the trailer verifies (`True`); the same PR with the old file changed anywhere else, or with a third file, or whose trailer names a different new file, fails with a reason naming the old file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory.py tests/test_review.py tests/test_brief.py -k "supersed or recall_marks"` → TypeError (unexpected keyword `supersedes`).

- [ ] **Step 3: Implement**

`src/meld/memory.py`:

```python
STATUS_RE = re.compile(r"^Status: superseded by (\S+) on (\d{4}-\d{2}-\d{2})$", re.M)
SUPERSEDES_RE = re.compile(r"^Supersedes: (.+)$", re.M)


def superseded_trailer(new_file: str, date: str) -> str:
    return f"\n---\nStatus: superseded by {new_file} on {date}\n"


def render_decision(ref: str, agreement: Agreement, url: str, date: str, supersedes: str | None = None) -> str:
    agreed = ", ".join(sorted(agreement.agreed))
    sup = f"- Supersedes: {supersedes}\n" if supersedes else ""
    return f"# Decision {ref}\n\n- Date: {date}\n- Agreed by: {agreed}\n- Source: {url}\n{sup}\n## Decision\n\n{(agreement.text or '').strip()}\n"


def render_lesson(ref: str, text: str, group: str, date: str, supersedes: str | None = None) -> str:
    sup = f"Supersedes: {supersedes}\n\n" if supersedes else ""
    return f"\n## {date} — {ref} ({group})\n\n{sup}{text.strip()}\n"
```

`decide(..., supersedes: str | None = None)`: when given, `supersedes` must be a bare filename present in `_default_branch_files(ctx, DECISIONS_DIR)` (else `refuse(... f"{supersedes} is not a decision file on {ctx.cfg.default_branch}")`) and must not already carry a status trailer (read it with `ctx.gh.get_contents(f"{DECISIONS_DIR}/{supersedes}", ctx.cfg.default_branch)`; refuse "already superseded by …" when `STATUS_RE` matches); render with `supersedes`; open the memory PR writing two files — the new file (mode `w`) and the old file (its origin content plus `superseded_trailer(new_name, date)`) — in one commit (extend `_open_memory_pr` to take a list of `(path, text, mode)` writes; keep the single-file callers working); the PR body keeps `meld-memory: decision <ref> <digest>` for the new file and adds a line `supersedes: <old file>`; the result gains `"supersedes"`.

`remember(..., supersedes: str | None = None)`: pass through to `render_lesson` after `escape_headings(neutralise_closers(text))` (the `Supersedes:` line is rendered from the flag, not from user text, so a `Supersedes:` line inside `text` is just text).

`review.verify_memory_pr`, decision branch: allow `len(paths) == 2` when the new file's text carries `- Supersedes: <old>` and `paths` contains `f"{DECISIONS_DIR}/{old}"`; then `old_head = ctx.gh.get_contents(old_path, pr.head_sha)`, `old_base = ctx.gh.get_contents(old_path, ctx.cfg.default_branch)`, and require `old_head == old_base + superseded_trailer(new_name, date)` where `new_name` is the new file's basename and `date` its `- Date:` (else `False, f"{old_path} must be the {default_branch} file plus exactly the superseded trailer naming {new_name}"`); everything else about the new file is checked exactly as today. A two-file PR without a `- Supersedes:` line, or with the wrong second file, fails as today ("a memory PR changes exactly one file…", reworded to "one file, or two for a supersession").

`brief.py`: `decision_titles` skips files whose text matches `STATUS_RE`; `_local_hits` adds to each hit `"status": "superseded" if <decision text matches STATUS_RE or a later lesson entry's SUPERSEDES_RE names this entry's heading> else "active"` and `"superseded_by": <the file or heading>`, and `recall_snippets` drops hits whose status is `superseded`. `memory.recall` passes the new keys through (they come from `_local_hits`).

`src/meld/cli.py`: `decide` gains `@click.option("--supersedes", default=None, help="Decision file this one supersedes, e.g. 0007-old.md")`; `remember` gains `@click.option("--supersedes", default=None, help="Heading of the lesson this one supersedes, e.g. '2026-09-01 — #9 (codex)'")`.

`src/meld/prompts/orchestrator.md` Memory section: add the supersedes usage for both verbs and the discipline line from decision 10.

- [ ] **Step 4: Run the suite; commit**

Run: `uv run pytest -q -p no:cacheprovider` → green.

```bash
git -C /Users/scottbrant/Documents/meld add src/meld/memory.py src/meld/review.py src/meld/brief.py src/meld/cli.py src/meld/prompts/orchestrator.md tests/
git -C /Users/scottbrant/Documents/meld commit -m "feat: decisions and lessons are superseded, never edited; recall and briefs show only current memory"
```

---

### Task 5: Documentation for plan 7

**Files:**
- Modify: `docs/superpowers/specs/2026-09-03-meld-design.md` (§13: one sentence that merge also enforces the scope and deletion gate; §21 items 11–13: `**Landing in plan 7**` → `**Landed in plan 7:**` naming the module and behaviour; the §6/§8/§14/§15 text from the plan commit re-read against the code and corrected where a detail differs), `README.md` (verbs table rows for `meld tag`, `meld decide`, `meld remember` with the new flags; a short "Guardrails" paragraph: scope, deletion gate, held categories, supersession), `docs/HANDOFF.md` (milestone line for plan 7 with the test count; repository map lines for `board.py`, `rules.py`, `tagging.py`, `memory.py`; `## Decisions for the owner (plan 7; each is reversible)` with this plan's ten decisions, each with *Not taken* and *Cost if wrong*; "What is next" from the remaining `### Carried into plan 6` items).

**Interfaces:** documentation only.

- [ ] **Step 1: Make the edits**, verifying every sentence against the code (grep before writing).
- [ ] **Step 2: Verify**: `uv run pytest -q -p no:cacheprovider` → green; put the count into HANDOFF; `grep -n "Landing in plan 7" docs/superpowers/specs/2026-09-03-meld-design.md` → nothing.
- [ ] **Step 3: Commit**

```bash
git -C /Users/scottbrant/Documents/meld add docs/superpowers/specs/2026-09-03-meld-design.md README.md docs/HANDOFF.md
git -C /Users/scottbrant/Documents/meld commit -m "docs: guardrails landed — scope at merge, held categories at dispatch, memory supersession; handoff for plan 7"
```

---

## Self-review

- **Spec coverage:** §6 declarations bullet → Tasks 1–3; §8 dispatch/merge bullets → Task 2; §14 needs-human unlock → Task 2 (`tick`); §15 supersession → Task 4; §21 items 11–13 → Task 5.
- **Type consistency:** `body_section(body, heading)`, `replace_section(body, heading, items)`, `Issue.scope`/`may_delete`/`held`, `PullRequest.removed`, `Board.scope_violations(pr)`/`blocked_deletions(pr)`/`human_answer(issue)`/`needs_human_answered(issue)`, `pull_files -> list[dict]`, `render_brief(..., scope, may_delete, authorized)`, `tag(..., scope, may_delete, held)`, `render_decision(..., supersedes)`, `render_lesson(..., supersedes)`, `superseded_trailer(new_file, date)`, `STATUS_RE`, `SUPERSEDES_RE` — used identically wherever they appear.
- **Placeholders:** Task 4's `...` and `<...>` marks stand for the existing fixture arrangement in `tests/test_memory.py` (the implementer reuses it and names it in the report); every other step carries its code.
