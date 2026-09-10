# Plan 6b: the `meld:claimed-by:<group>` claim lock

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the issue-assignee claim lock with a `meld:claimed-by:<group>` label so an App group (whose bot user GitHub will not make an assignee) can claim and build exactly like a PAT group, with one mechanism for everyone.

**Architecture:** The lock moves from `issue.assignees` to one label per claiming group. `board.Issue` derives `claimants` (every `meld:claimed-by:*` group, sorted) and `claimed_by` (the single holder, or `None` when there are zero or several) from its labels; `Board` learns each group's bot login so `pr_for_issue` can still prefer the holder's PR. `rules.py` keeps every legality decision and reads `claimed_by`/`claimants` instead of assignees. `actions.claim` ensures its label exists, adds it, re-reads, and treats any claim-label set other than its own as a lost race (remove own label, refuse); `actions.release` removes every claim label. Leases are unchanged and stay valid only while their group holds the label. Assignees are no longer read or written; `meld doctor`'s `assignable:<g>` check goes away; group names are validated so they are label- and path-safe. Docs follow.

**Tech Stack:** Python 3.13, standard library plus click; `FakeGh` for GitHub in tests; real git repos and subprocesses in tests.

**Spec:** `docs/superpowers/specs/2026-09-03-meld-design.md` — sections 3 (decisions table: Identity), 6 (the claim lock paragraph, rewritten for this plan), 8 (claim/dispatch/release/collect legality), 11 (board fetch), 14 (off-protocol sweep unchanged), 20 (bootstrap: the App limitation disappears), 21 item 3 ("Carried into plan 6", marked *Landing in plan 6b*; Task 5 marks it landed).

## Global Constraints

- Python `>=3.12` (3.13 locally); runtime dependency `click>=8.1` only.
- `rules.py` is the only legality authority: no new `if actor...` checks outside it; `meld status` must list every legal verb.
- `github.py` is the only GitHub path (plus `appauth.py`'s two App-JWT calls); `gitops.py` the only git runner.
- Workers never hold credentials; anything a worker writes that reaches GitHub passes through `neutralise_closers`.
- Tests are hermetic and real: real git repos and real subprocesses over `FakeGh`; no network, no real CLI, no host `~/.claude`/gitconfig/wall-clock dependence; inject `now`; pass `adapter_for=` to every `load_context` in tests. `uv run pytest -q -p no:cacheprovider` must be green with no warnings before every commit.
- Never set `MELD_SMOKE=1`.
- Conventional commit subjects, no attribution trailers, one logical change per commit; repo-local identity `Scott Brant <421573+smidley@users.noreply.github.com>`; commit with `git -C /Users/scottbrant/Documents/meld ...`.
- One GitHub identity per group is preserved; PAT and App groups behave identically for claiming.
- Baseline at the plan's base (`main` = 5012be2, plan 5b merged): `uv run pytest -q -p no:cacheprovider` → 565 passed, no warnings. Each task leaves the suite green.

## Decisions (rulings made while writing the plan; recorded so executors do not re-litigate)

1. **A label, for every group.** `meld:claimed-by:<group>` replaces the assignee for PAT and App groups alike: one mechanism, and labels arrive with the issue payload the board already fetches (no extra API call). Scott delegated the shape on 2026-09-10 ("use your best judgement").
2. **The race rule is the assignee lock's, unchanged in spirit.** Add own label, re-read, and lose if the claim-label set is anything but exactly own: remove own label, refuse (no `PROTOCOL-VIOLATION`). Two racers can both lose and both retry; none can both win, so there is no tie-break. GitHub gives no compare-and-swap on labels, and a tie-break needs both racers to see the same set at the same moment, which GitHub does not guarantee; both-lose is the safe rule.
3. **Contested issues.** Two or more claim labels can exist only after a crash between add and re-read. Then `claimed_by` is `None`, `claimants` lists them, `claim` and `dispatch` refuse, either claimant (or a human) may release, and each claimant's lease stays valid (`held_leases` keeps a lease whose group is among the claimants) so the ordinary expiry path lets any PM release it, which removes every claim label. No special repair verb.
4. **A human claims with `meld:claimed-by:human`.** One label for all humans (humans build things themselves; no group may dispatch a human-claimed issue). `human` is therefore a reserved group name.
5. **Assignees are neither read nor written any more.** Humans may assign issues to people as a courtesy; meld ignores it. The off-protocol sweep keeps flagging assignee changes by strangers (they remain off-protocol actors). `GitHub.add_assignees`/`remove_assignees` are removed as dead code.
6. **Claim labels are created lazily** by `claim` through `GitHub.ensure_label` (a `POST labels` whose 422 means "exists"), colour `c5def5`, description `Claimed by <group>`; `meld setup` cannot know group names and `LABELS` stays static.
7. **Group names are validated**: `[a-z0-9][a-z0-9_-]{0,31}` and never `human`, because a name becomes a label (50-char limit, prefix included) and a worktree path component.
8. **`Board` learns bot logins** (`Board.bots`, group → login, filled by `assemble` from the config) so `pr_for_issue` can still prefer the current holder's PR. A small, explicit exception to "Board stays free of Config", limited to logins.
9. **`assignable:<g>` leaves `meld doctor`.** The lock no longer depends on assignability. A PAT group still needs push access for PR branches; that is a different requirement and is not checked here.
10. **No migration tooling.** Issues claimed under the assignee lock read as unclaimed after upgrading; a product mid-milestone re-claims them once. Documented in HANDOFF; no product is mid-flight today.
11. **An orphan claim is recoverable through `meld`** (added by the whole-branch review, 2026-09-10, Important 2). `actions.claim` adds its label before the `try`, so a PM killed between the add and the re-read left `meld:ready` plus a claim label that refused everyone: `claim` said "already claimed", `release` said "issue is ready", `tick` skipped it, and no lease existed to expire. Two rules in `rules.py` close it. `_release` also accepts a `ready` issue that carries a claim label (`stale = issue.status == "ready" and bool(issue.claimants)`), for a claimant or a human, and the refusal names the case; `actions.release` then sheds every claim label unchanged. `_claim` compares against the caller's own name (`HUMAN_CLAIMANT` for a human) instead of refusing on any claimant, so a group re-claims past its own stale label — the add is a no-op, the re-read shows exactly its own label, the swap proceeds, and the crashed PM's next loop self-heals. Another party's label still refuses, and a contested `ready` issue refuses everyone, naming both claimants. *Not taken:* a `meld repair` verb, or letting any PM release a stale claim it does not hold. *Cost if wrong:* a live claim released mid-flight by its own group is impossible in practice — only a claimant or a human — or, once the holder's lease has expired, any PM through the ordinary lease rule — may release it, and a claimant releasing its own in-flight claim is exactly what it could already do. *Residual:* a stale label whose holder never comes back, and an `in_progress` issue with no claimant, still need a human `meld release` (spec §21 item 21).

## File structure

| File | Responsibility |
|---|---|
| `src/meld/board.py` | `CLAIM_LABEL_PREFIX`, `HUMAN_CLAIMANT`, `claim_label`, `claim_group`; `Issue.claimants`/`claimed_by`; `Board.bots`, `Board.holder_login`; `held_leases` (was `assigned_leases`); `in_progress_count(group)`; `pr_for_issue` via the holder's login |
| `src/meld/assemble.py` | fills `Board.bots`; stops reading assignees |
| `src/meld/rules.py` | claim/dispatch/release/collect/challenged-party legality on `claimed_by`/`claimants` |
| `src/meld/actions.py` | `claim` and `release` on the label |
| `src/meld/github.py` | `ensure_label`; assignee methods removed |
| `src/meld/dispatch.py` | collect's supersession check on `claimed_by` |
| `src/meld/status.py` | reports `claimed_by`/`claimants` |
| `src/meld/config.py` | group-name validation |
| `src/meld/doctor.py`, `src/meld/setup.py` | `assignable:<g>` and the App limitation text removed |
| `tests/factories.py`, `tests/productfixtures.py`, `tests/fixtures/board1/issues.json` | claims expressed as labels |
| docs: spec §20/§21, `README.md`, `docs/HANDOFF.md` | the limitation is gone; handoff for plan 6b |

---

### Task 1: The board derives the claim from labels (additive; assignees still present)

**Files:**
- Modify: `src/meld/board.py` (`Issue`, the `assigned_leases` block at ~236–257, `Board`, `in_progress_count`, `pr_for_issue`)
- Modify: `src/meld/assemble.py:175-182` (the `Board(...)` construction)
- Modify: `src/meld/rules.py:102` (`board.in_progress_count(group.bot)` → `group.name`)
- Modify: `tests/factories.py` (`mk_issue`), `tests/productfixtures.py` (`claim_issue_11`), `tests/fixtures/board1/issues.json` (issues 12, 13, 14)
- Test: `tests/test_board.py`, `tests/test_assemble.py`

**Interfaces:**
- Produces: `board.CLAIM_LABEL_PREFIX = "meld:claimed-by:"`; `board.HUMAN_CLAIMANT = "human"`; `board.claim_label(group: str) -> str`; `board.claim_group(label: str) -> str | None`; `Issue.claimants -> tuple[str, ...]` (property, sorted); `Issue.claimed_by -> str | None` (property; the single claimant, else `None`); `Board.bots: dict[str, str]` (field, group → bot login, default empty); `Board.holder_login(issue: int) -> str | None`; `board.held_leases(leases, issues) -> dict[int, Lease]` (replaces `assigned_leases`, which is deleted; keeps a lease whose group is among the issue's claimants); `Board.in_progress_count(group: str) -> int` (takes a group name now, not a bot login).
- Consumes: nothing new.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_board.py`:

```python
def test_issue_derives_its_claim_from_the_claimed_by_labels():
    from meld.board import CLAIM_LABEL_PREFIX, claim_group, claim_label
    from tests.factories import mk_issue
    assert claim_label("codex") == "meld:claimed-by:codex" and CLAIM_LABEL_PREFIX == "meld:claimed-by:"
    assert claim_group("meld:claimed-by:codex") == "codex" and claim_group("meld:in-progress") is None and claim_group("meld:claimed-by:") is None
    free = mk_issue(11)
    assert free.claimants == () and free.claimed_by is None
    held = mk_issue(12, status="in_progress", claimed_by="claude")
    assert held.claimants == ("claude",) and held.claimed_by == "claude"
    contested = mk_issue(13, status="in_progress", extra_labels=("meld:claimed-by:codex", "meld:claimed-by:claude"))
    assert contested.claimants == ("claude", "codex") and contested.claimed_by is None


def test_held_leases_keep_only_the_lease_whose_group_holds_the_label():
    from meld.board import Lease, held_leases
    from tests.factories import mk_issue
    issues = {12: mk_issue(12, status="in_progress", claimed_by="claude"),
              13: mk_issue(13, status="in_progress", claimed_by="codex"),
              14: mk_issue(14, status="in_progress"),
              15: mk_issue(15, status="in_progress", extra_labels=("meld:claimed-by:claude", "meld:claimed-by:codex"))}
    leases = {12: Lease(12, "claude", "2026-09-03T12:00:00Z"), 13: Lease(13, "claude", "2026-09-03T12:00:00Z"),
              14: Lease(14, "codex", "2026-09-03T12:00:00Z"), 15: Lease(15, "codex", "2026-09-03T12:00:00Z"),
              99: Lease(99, "codex", "2026-09-03T12:00:00Z")}
    # 13: codex holds, lease says claude; 14: nobody holds; 15: contested, codex is a claimant so its lease survives; 99: no such issue
    assert set(held_leases(leases, issues)) == {12, 15}


def test_in_progress_count_and_holder_login_use_the_claim_label():
    from tests.factories import CFG2, mk_board, mk_issue, mk_pr
    board = mk_board(issues={12: mk_issue(12, status="in_progress", claimed_by="claude"),
                             13: mk_issue(13, status="in_progress", claimed_by="claude"),
                             14: mk_issue(14, status="in_progress", claimed_by="codex")},
                     pulls={20: mk_pr(20, 12, author="meld-codex-bot"), 21: mk_pr(21, 12, author="meld-claude-bot")})
    assert board.in_progress_count("claude") == 2 and board.in_progress_count("codex") == 1 and board.in_progress_count("human") == 0
    assert board.holder_login(12) == "meld-claude-bot" and board.holder_login(99) is None
    assert board.pr_for_issue(12).number == 21   # the holder's open PR beats a stale one from another group
```

`mk_board` must pass `bots={g.name: g.bot for g in CFG2.groups.values()}` to `Board` (see Step 3); `mk_pr(..., author=...)` already exists.

Append to `tests/test_assemble.py`:

```python
def test_build_board_fills_the_bot_logins_and_reads_claims_from_labels(tmp_path):
    from tests.productfixtures import make_product
    from meld.status import build_status
    fake, ctx, repo, origin = make_product(tmp_path)
    _, board = build_status(ctx)
    assert board.bots == {"claude": "meld-claude-bot", "codex": "meld-codex-bot"}
    assert board.issues[12].claimed_by == "claude" and board.issues[13].claimed_by == "codex" and board.issues[11].claimed_by is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q -p no:cacheprovider tests/test_board.py tests/test_assemble.py -k "claim or held_leases or holder_login or bot_logins"` → ImportError/AttributeError (`claim_label`, `held_leases`, `claimed_by`, `bots`).

- [ ] **Step 3: Implement**

`src/meld/board.py` — constants next to `NUDGED_LABEL`:

```python
CLAIM_LABEL_PREFIX = "meld:claimed-by:"
HUMAN_CLAIMANT = "human"   # the one claimant name every human shares; a reserved group name (config.GROUP_NAME_RE)


def claim_label(group: str) -> str:
    """The label that is a group's claim on an issue (spec 6): one per claiming party, PAT or App alike."""
    return CLAIM_LABEL_PREFIX + group


def claim_group(label: str) -> str | None:
    """The claiming party a label names, or None for any other label (the bare prefix names nobody)."""
    return label[len(CLAIM_LABEL_PREFIX):] or None if label.startswith(CLAIM_LABEL_PREFIX) else None
```

`Issue` gains two properties (keep the `assignees` field for now; Task 4 removes it):

```python
    @property
    def claimants(self) -> tuple[str, ...]:
        """Every party holding a claim label, sorted. One is the normal case; two or more is a contested issue."""
        return tuple(sorted(g for g in (claim_group(l) for l in self.labels) if g))

    @property
    def claimed_by(self) -> str | None:
        """The single claim holder, or None when the issue is free or contested."""
        c = self.claimants
        return c[0] if len(c) == 1 else None
```

Replace the `assigned_leases` block (the long comment plus the function, ~lines 236–257) with:

```python
# A lease is valid only while the leasing group holds the issue's claim label. The LEASE comment can outlive the
# claim it describes: a group's or PM's own `meld release` posts a framework RELEASED note and a human's `--force` a
# `[human] RELEASE`, both of which `derive_leases` drops the lease on (plan-5 decision 2) -- but a human's plain
# release posts nothing, and a claim can end without any comment at all (a label removed on GitHub). So once the
# leasing group lets go and another group claims the issue, an old LEASE could still read as a lease on the new
# claim, and once its `until` passed any PM could release the new owner (a LEASE forged by hand would do the same on
# purpose). The label, not the thread, is the claim lock (spec section 6), so it decides validity too.
def held_leases(leases: Mapping[int, Lease], issues: Mapping[int, Issue]) -> dict[int, Lease]:
    """Keep only the leases whose group currently holds a claim label on the leased issue. `claimants`, not `claimed_by`:
    on a contested issue (two labels) each claimant's lease stays valid, so the ordinary expiry path can clear it."""
    return {n: lease for n, lease in leases.items() if (issue := issues.get(n)) is not None and lease.group in issue.claimants}
```

`Board`: add the field `bots: dict[str, str] = field(default_factory=dict)   # group -> bot login, from the config (assemble)` after `audits`, and

```python
    def holder_login(self, issue: int) -> str | None:
        """The bot login of the group holding `issue`'s claim, or None (free, contested, human-held, unknown group)."""
        iss = self.issues.get(issue)
        return self.bots.get(iss.claimed_by) if iss is not None and iss.claimed_by else None

    def in_progress_count(self, group: str) -> int:
        return sum(1 for i in self.issues.values() if i.status == "in_progress" and i.claimed_by == group)
```

and in `pr_for_issue` replace the `assignees = ...` line and the open-issue sort key with:

```python
        holder = self.holder_login(issue)
        # Open issue: the open PR is live, preferring one authored by the current holder's bot (a stale PR from an
        # earlier claimant loses); closed issue: the PR that merged is the record.
        if issue_closed:
            candidates.sort(key=lambda p: (not p.merged, p.state != "open", -p.number))
        else:
            candidates.sort(key=lambda p: (p.state != "open", p.author != holder, -p.number))
```

`src/meld/assemble.py`: import `held_leases` instead of `assigned_leases`; in `Board(...)` pass `leases=held_leases(derive_leases(comments), issues)` and `bots={g.name: g.bot for g in cfg.groups.values()}`.

`src/meld/rules.py:102`: `n = board.in_progress_count(group.name)`.

`tests/factories.py`: `mk_issue` gains `claimed_by: str | None = None` (keep `assignees` for now) and adds `labels.add(claim_label(claimed_by))` when given (import `claim_label` from `meld.board`); `mk_board` passes `bots={g.name: g.bot for g in CFG2.groups.values()}` (check the `Board(...)` call in `mk_board` and add the keyword).

`tests/productfixtures.py::claim_issue_11`: after the `assignees` line add `i["labels"].append({"name": "meld:claimed-by:claude"})`.

`tests/fixtures/board1/issues.json`: add `{"name": "meld:claimed-by:claude"}` to issue 12's labels and `{"name": "meld:claimed-by:codex"}` to issues 13 and 14 (each already has the matching assignee).

Then run the whole suite: tests that assert `in_progress_count(<bot login>)` or `assigned_leases` must be updated to the new names (`grep -rn "in_progress_count\|assigned_leases" tests`); a test that expects a lease to be dropped because the bot is unassigned now expresses that as the claim label missing.

- [ ] **Step 4: Run the suite; commit**

Run: `uv run pytest -q -p no:cacheprovider` → green.

```bash
git -C /Users/scottbrant/Documents/meld add src/meld/board.py src/meld/assemble.py src/meld/rules.py tests/
git -C /Users/scottbrant/Documents/meld commit -m "feat: the board derives each issue's claim from meld:claimed-by labels"
```

---

### Task 2: Legality, collect, and status read the claim label

**Files:**
- Modify: `src/meld/rules.py` (`_claim` ~93, `_dispatch` ~125, `_release` ~174, `challenged_party` ~470–481), `src/meld/dispatch.py:597-605` (`_collect_build`), `src/meld/status.py:49`
- Test: `tests/test_rules_issues.py`, `tests/test_rules_challenges.py`, `tests/test_collect.py`, `tests/test_status.py`

**Interfaces:**
- Consumes: `Issue.claimants`, `Issue.claimed_by` (Task 1).
- Produces: refusal texts `already claimed by <a, b>`, `issue is claimed by <a, b>, not <group>` / `issue is not claimed by <group>`; `meld status` issue objects carry `claimed_by` and `claimants` instead of `assignees`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_rules_issues.py` (mirror the file's existing `Actor`/`CFG2`/`mk_board` usage; `REGISTRY` is whatever the file calls its `ModelRegistry`):

```python
def test_claim_and_dispatch_follow_the_claim_label_not_the_assignee():
    from meld.config import Party
    from meld.rules import Actor, issue_refusal, Verb
    from tests.factories import CFG2, mk_board, mk_issue
    pm = Actor(Party("pm", "codex"), "meld-codex-bot", roster=frozenset({"backend"}))
    held_by_claude = mk_issue(12, status="in_progress", claimed_by="claude", assignees=("meld-codex-bot",))   # assignee lies; the label rules
    board = mk_board(issues={12: held_by_claude}, phase="executing")
    assert issue_refusal(board, CFG2, pm, Verb.DISPATCH, held_by_claude, REGISTRY).rule == "issue is claimed by claude, not codex"
    free_but_assigned = mk_issue(11, status="ready", assignees=("meld-claude-bot",))
    board = mk_board(issues={11: free_but_assigned}, phase="executing")
    assert issue_refusal(board, CFG2, pm, Verb.CLAIM, free_but_assigned, REGISTRY) is None
    contested = mk_issue(13, status="in_progress", extra_labels=("meld:claimed-by:claude", "meld:claimed-by:codex"))
    board = mk_board(issues={13: contested}, phase="executing")
    assert issue_refusal(board, CFG2, pm, Verb.DISPATCH, contested, REGISTRY).rule == "issue is claimed by claude, codex, not codex"
    assert issue_refusal(board, CFG2, pm, Verb.RELEASE, contested, REGISTRY) is None          # either claimant may release
    ready_claimed = mk_issue(14, status="ready", claimed_by="claude")
    board = mk_board(issues={14: ready_claimed}, phase="executing")
    assert issue_refusal(board, CFG2, pm, Verb.CLAIM, ready_claimed, REGISTRY).rule == "already claimed by claude"
```

Append to `tests/test_rules_challenges.py`:

```python
def test_challenged_party_of_an_issue_is_its_claim_holder():
    from meld.rules import challenged_party
    from tests.factories import CFG2, mk_board, mk_issue
    board = mk_board(issues={12: mk_issue(12, status="in_progress", claimed_by="codex"),
                             13: mk_issue(13, status="in_progress", claimed_by="human"),
                             14: mk_issue(14, status="in_progress", extra_labels=("meld:claimed-by:claude", "meld:claimed-by:codex"))})
    assert challenged_party(board, CFG2, "#12") == "codex"
    assert challenged_party(board, CFG2, "#13") is None      # a human holder is not a group
    assert challenged_party(board, CFG2, "#14") is None      # contested: nobody owns it
```

In `tests/test_collect.py`, find the test that makes collect record a run as `superseded` because the issue changed hands (it sets issue 11's `assignees` to the other group's bot, ~line 253) and make it move the claim instead: replace the assignee edit with `i["labels"] = [l for l in i["labels"] if not l["name"].startswith("meld:claimed-by:")] + [{"name": "meld:claimed-by:codex"}]`, and assert the reason text contains `claimed by codex`.

In `tests/test_status.py`, the issue-shape assertion that names `assignees` now names `claimed_by` and `claimants` (e.g. `{"claimed_by": "claude", "claimants": ["claude"]}` for issue 12).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q -p no:cacheprovider tests/test_rules_issues.py tests/test_rules_challenges.py tests/test_collect.py tests/test_status.py -k "claim or challenged_party or superseded or status"` → the new assertions fail on the old assignee wording.

- [ ] **Step 3: Implement**

`src/meld/rules.py`:

```python
# in _claim, replacing the two assignee lines:
    if issue.claimants:
        return f"already claimed by {', '.join(issue.claimants)}"

# in _dispatch, replacing the assignee check:
    if issue.claimed_by != group.name:
        return (f"issue is claimed by {', '.join(issue.claimants)}, not {group.name}" if issue.claimants
                else f"issue is not claimed by {group.name}")

# in _release, replacing `if group.bot in issue.assignees: return None` and the final message:
    if group.name in issue.claimants:
        return None
    if actor.is_pm and lease_rule(board, issue):
        return None
    return f"issue is not claimed by {group.name}"

# challenged_party, the issue branch:
        if n in board.issues:
            holder = board.issues[n].claimed_by
            return holder if holder in cfg.groups else None
```

Update `challenged_party`'s docstring: "an issue's claim holder's group (never a human holder or a contested issue)".

`src/meld/dispatch.py::_collect_build`:

```python
    if issue.status != "in_progress" or issue.claimed_by != group.name:
        # The claim moved on while the worker ran (our lease expired and another group re-claimed, a human
        # released or re-labelled, or the issue is contested): the run is over, but nothing of it may reach GitHub.
        # The worktree stays for inspection, detached so the branch name is free for whoever now holds the issue.
        holder = ", ".join(issue.claimants) or "nobody"
        ...
        return {"collected": number, "outcome": "superseded",
                "reason": f"issue is {issue.status} and claimed by {holder}; {group.name} no longer holds it"}
```

`src/meld/status.py:49`: replace `"assignees": list(i.assignees)` with `"claimed_by": i.claimed_by, "claimants": list(i.claimants)`.

Run the whole suite and fix every test whose expectation names the old wording (`grep -rn "assigned to\|already claimed by" tests`): the messages now name groups (`claude`, `codex`), not logins.

- [ ] **Step 4: Run the suite; commit**

Run: `uv run pytest -q -p no:cacheprovider` → green.

```bash
git -C /Users/scottbrant/Documents/meld add src/meld/rules.py src/meld/dispatch.py src/meld/status.py tests/
git -C /Users/scottbrant/Documents/meld commit -m "feat: claim, dispatch, release, collect, and status read the claim label"
```

---

### Task 3: `meld claim` and `meld release` move to the label; `GitHub.ensure_label`

**Files:**
- Modify: `src/meld/actions.py` (`claim`, `release`, the `Refused` comment at ~23), `src/meld/github.py` (add `ensure_label` beside `create_label`; delete `add_assignees`/`remove_assignees`)
- Test: `tests/test_actions.py`, `tests/test_github.py`, `tests/test_audit.py` (~44, ~152), `tests/test_tick.py` (~66)

**Interfaces:**
- Produces: `GitHub.ensure_label(name: str, color: str, description: str = "") -> bool` (True when created; a 422 "already exists" returns False; other errors raise); `actions.CLAIM_LABEL_COLOR = "c5def5"`; `claim` returns `{"claimed": n, "claimed_by": <group or "human">}`; `release` returns `{"released": n, "previous_claimants": [...]}`.
- Consumes: `claim_label`, `claim_group`, `HUMAN_CLAIMANT` (Task 1); `GitHubError.status`.

- [ ] **Step 1: Write the failing tests**

Rewrite the claim/release tests in `tests/test_actions.py` around the label. The shapes (keep the file's existing helpers — `make_product`, `R`, the mutation-list idiom):

```python
CLAIM = "meld:claimed-by:claude"
CLAIM_ENC = "meld%3Aclaimed-by%3Aclaude"


def test_claim_adds_the_label_verifies_it_and_swaps_status_labels(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    fake.route("POST", R + "labels", {"name": CLAIM}, status=201)                       # ensure_label creates it
    fake.route("POST", R + "issues/11/labels", [])
    fake.route("GET", R + "issues/11", {**fixture_issue(11), "labels": fixture_issue(11)["labels"] + [{"name": CLAIM}]})
    fake.route("DELETE", R + "issues/11/labels/meld%3Aready", {}, status=200)
    out = claim(ctx, 11)
    assert out == {"claimed": 11, "claimed_by": "claude"}
    mutations = [(c.method, c.endpoint) for c in fake.calls if c.method != "GET"]
    assert mutations[:2] == [("POST", R + "labels"), ("POST", R + "issues/11/labels")]      # ensure, then claim
    assert [c.body["labels"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/11/labels"] == [[CLAIM], ["meld:in-progress"]]
    assert ("DELETE", R + "issues/11/labels/meld%3Aready") in mutations
    assert not any("assignees" in c.endpoint for c in fake.calls)


def test_claim_treats_a_422_from_label_creation_as_already_exists(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    fake.route("POST", R + "labels", {"message": "Validation Failed", "errors": [{"code": "already_exists"}]}, status=422)
    fake.route("POST", R + "issues/11/labels", [])
    fake.route("GET", R + "issues/11", {**fixture_issue(11), "labels": fixture_issue(11)["labels"] + [{"name": CLAIM}]})
    fake.route("DELETE", R + "issues/11/labels/meld%3Aready", {}, status=200)
    assert claim(ctx, 11)["claimed"] == 11


def test_claim_loses_the_race_when_another_claim_label_is_present_after_the_re_read(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    fake.route("POST", R + "labels", {}, status=422)
    fake.route("POST", R + "issues/11/labels", [])
    fake.route("GET", R + "issues/11", {**fixture_issue(11), "labels": fixture_issue(11)["labels"] + [{"name": CLAIM}, {"name": "meld:claimed-by:codex"}]})
    fake.route("DELETE", R + f"issues/11/labels/{CLAIM_ENC}", {}, status=200)
    with pytest.raises(Refused) as e:
        claim(ctx, 11)
    assert "lost the claim race to codex" in str(e.value) and e.value.violation_posted is None
    mutations = [(c.method, c.endpoint) for c in fake.calls if c.method != "GET"]
    assert ("DELETE", R + f"issues/11/labels/{CLAIM_ENC}") in mutations
    assert ["meld:in-progress"] not in [c.body.get("labels") for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/11/labels"]


def test_claim_undoes_its_label_when_the_status_swap_fails(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    fake.route("POST", R + "labels", {}, status=422)
    fake.route("POST", R + "issues/11/labels", [])
    fake.route("GET", R + "issues/11", {**fixture_issue(11), "labels": fixture_issue(11)["labels"] + [{"name": CLAIM}]})
    fake.route("DELETE", R + "issues/11/labels/meld%3Aready", {"message": "boom"}, status=500)
    fake.route("DELETE", R + f"issues/11/labels/{CLAIM_ENC}", {}, status=200)
    fake.route("DELETE", R + "issues/11/labels/meld%3Ain-progress", {}, status=200)
    with pytest.raises(GitHubError):
        claim(ctx, 11)
    mutations = [(c.method, c.endpoint) for c in fake.calls if c.method != "GET"]
    assert ("DELETE", R + f"issues/11/labels/{CLAIM_ENC}") in mutations and ("DELETE", R + "issues/11/labels/meld%3Ain-progress") in mutations


def test_a_human_claims_with_the_shared_human_label(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, role="human")      # whatever the file uses today for the human actor
    fake.route("POST", R + "labels", {}, status=422)
    fake.route("POST", R + "issues/11/labels", [])
    fake.route("GET", R + "issues/11", {**fixture_issue(11), "labels": fixture_issue(11)["labels"] + [{"name": "meld:claimed-by:human"}]})
    fake.route("DELETE", R + "issues/11/labels/meld%3Aready", {}, status=200)
    assert claim(ctx, 11) == {"claimed": 11, "claimed_by": "human"}


def test_release_removes_every_claim_label_and_reports_the_previous_claimants(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    fake.route("DELETE", R + f"issues/12/labels/{CLAIM_ENC}", {}, status=200)
    fake.route("POST", R + "issues/12/labels", [])
    fake.route("DELETE", R + "issues/12/labels/meld%3Ain-progress", {}, status=200)
    fake.route("POST", R + "issues/1/comments", {"id": 900})            # RELEASED on the coordination issue (its number is in the fixture)
    out = release(ctx, 12)
    assert out == {"released": 12, "previous_claimants": ["claude"]}
    mutations = [(c.method, c.endpoint) for c in fake.calls if c.method != "GET"]
    assert mutations[0] == ("DELETE", R + f"issues/12/labels/{CLAIM_ENC}")
    assert not any("assignees" in c.endpoint for c in fake.calls)
```

`fixture_issue(n)` is a small helper to add in the test module: `next(i for i in fixture("issues.json") if i["number"] == n)` (import `fixture` from `tests.productfixtures`). Keep the existing tests for the stale-LEASE supersession and the force-release note, re-routing their assignee calls to the label calls (`DELETE issues/<n>/labels/<encoded claim label>`), and keep their assertions about the comments posted. Delete `tests/test_github.py:68-69` (the assignee method calls) and add:

```python
def test_ensure_label_creates_once_and_treats_422_as_existing(board1_gh):
    fake, gh = board1_gh
    fake.route("POST", "repos/acme/widgets/labels", {"name": "meld:claimed-by:claude"}, status=201)
    assert gh.ensure_label("meld:claimed-by:claude", "c5def5", "Claimed by claude") is True
    fake.route("POST", "repos/acme/widgets/labels", {"message": "Validation Failed"}, status=422)
    assert gh.ensure_label("meld:claimed-by:claude", "c5def5", "Claimed by claude") is False
    fake.route("POST", "repos/acme/widgets/labels", {"message": "boom"}, status=500)
    with pytest.raises(GitHubError):
        gh.ensure_label("meld:claimed-by:claude", "c5def5")
```

In `tests/test_audit.py` (~44 and ~152) and `tests/test_tick.py` (~66), the `DELETE issues/13/assignees` routes become `DELETE issues/13/labels/meld%3Aclaimed-by%3Acodex` (issue 13 is codex's in the fixture) and the assertions that looked for the assignee DELETE look for that endpoint instead.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q -p no:cacheprovider tests/test_actions.py tests/test_github.py -k "claim or release or ensure_label"` → fail (assignee calls still made; `ensure_label` missing).

- [ ] **Step 3: Implement**

`src/meld/github.py`, next to `create_label`; delete `add_assignees` and `remove_assignees`:

```python
    def ensure_label(self, name: str, color: str, description: str = "") -> bool:
        """Create `name` if the repository lacks it; True when created. GitHub answers 422 when it already exists, which is
        success here, so callers need no read first (one call per claim, idempotent)."""
        try:
            self.create_label(name, color, description)
        except GitHubError as e:
            if e.status == 422:
                return False
            raise
        return True
```

`src/meld/actions.py`:

```python
from meld.board import HUMAN_CLAIMANT, Board, claim_group, claim_label, derive_leases

CLAIM_LABEL_COLOR = "c5def5"


def _claim_labels_of(raw_issue: dict) -> tuple[str, ...]:
    """Claimants named by a raw GitHub issue payload (the fresh re-read in `claim`), sorted like Issue.claimants."""
    names = ((l.get("name", "") if isinstance(l, dict) else str(l)) for l in raw_issue.get("labels", []))
    return tuple(sorted(g for g in (claim_group(n) for n in names) if g))


def claim(ctx, number: int) -> dict:
    _, board = build_status(ctx)
    issue = _issue_or_refuse(ctx, board, Verb.CLAIM, number)
    actor = ctx.actor()
    refusal = issue_refusal(board, ctx.cfg, actor, Verb.CLAIM, issue, ctx.models)
    if refusal:
        refuse(ctx, board, refusal)
    holder = HUMAN_CLAIMANT if actor.is_human else actor.group
    own = claim_label(holder)   # type: ignore[arg-type]
    ctx.gh.ensure_label(own, CLAIM_LABEL_COLOR, f"Claimed by {holder}")
    ctx.gh.add_labels(number, [own])
    try:
        claimants = _claim_labels_of(ctx.gh.get_issue(number, fresh=True))
        if claimants != (holder,):
            ctx.gh.remove_label(number, own)
            others = [g for g in claimants if g != holder]
            # Losing the race is the designed outcome of first-label-wins (spec 6), not an illegal call, so it is
            # refused without posting a PROTOCOL-VIOLATION. Both racers can lose and both retry; none can both win.
            raise Refused(Refusal(Verb.CLAIM, f"#{number}", f"lost the claim race to {', '.join(others) or 'unknown'}"))
        ctx.gh.add_labels(number, [IN_PROGRESS])
        ctx.gh.remove_label(number, READY)
    except GitHubError:
        # Undo both halves of the claim, each best-effort, then surface the original error.
        for undo in (lambda: ctx.gh.remove_label(number, own), lambda: ctx.gh.remove_label(number, IN_PROGRESS)):
            try:
                undo()
            except GitHubError:
                pass
        raise
    # (the stale-LEASE supersession block stays exactly as it is)
    return {"claimed": number, "claimed_by": holder}
```

In `release`, replace `if issue.assignees: ctx.gh.remove_assignees(number, list(issue.assignees))` with `for g in issue.claimants: ctx.gh.remove_label(number, claim_label(g))`; the force note becomes `f"Force-released #{number} from {', '.join(issue.claimants) or 'nobody'}."`; return `{"released": number, "previous_claimants": list(issue.claimants)}`. Update the `Refused` docstring comment ("first-assignment-wins" → "first-label-wins").

- [ ] **Step 4: Run the suite; commit**

Run: `uv run pytest -q -p no:cacheprovider` → green (`grep -rn "assignees" src/meld` now finds only `housekeeping.py`'s off-protocol sweep and `board.py`/`assemble.py`'s soon-to-go field).

```bash
git -C /Users/scottbrant/Documents/meld add src/meld/actions.py src/meld/github.py tests/
git -C /Users/scottbrant/Documents/meld commit -m "feat: meld claim and release hold the issue through the meld:claimed-by label"
```

---

### Task 4: Retire assignees, validate group names, drop the assignability check

**Files:**
- Modify: `src/meld/board.py` (delete `Issue.assignees`), `src/meld/assemble.py::_issue` (stop reading assignees), `src/meld/config.py` (`GROUP_NAME_RE`, validation in `from_dict`'s group loop), `src/meld/doctor.py` (delete the `assignable:<g>` block ~209–216, `APP_ASSIGNEE_FIX`/`PAT_ASSIGNEE_FIX` and their comment ~48–56, the docstring sentence at ~12–13, and `quote` if now unused), `src/meld/setup.py` (delete the "known limitation" `next_steps` element ~191–193)
- Modify: `tests/factories.py` (drop `assignees` from `mk_issue`), `tests/productfixtures.py` (drop the `assignees` line in `claim_issue_11`), `tests/fixtures/board1/issues.json` (every `"assignees": [...]` → `[]`), `tests/test_collect.py`/`tests/test_wait.py` (the fixture edits that set `assignees` set the claim label instead)
- Test: `tests/test_config.py`, `tests/test_doctor.py` (delete the two `assignable` tests), `tests/test_setup.py` (the `--app` next-steps test no longer expects the limitation line), `tests/test_board.py`

**Interfaces:**
- Produces: `config.GROUP_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")`; `ConfigError` for an invalid or reserved group name.
- Consumes: `HUMAN_CLAIMANT` (Task 1).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
@pytest.mark.parametrize("name", ["Claude", "my group", "a/b", "human", "x" * 33, "-lead"])
def test_group_names_must_be_label_and_path_safe(name):
    from meld.config import Config, ConfigError
    import tomllib
    data = tomllib.loads(PROJECT_TOML.replace("[groups.claude]", f"[groups.{name!r}]" if not name.isidentifier() else f"[groups.{name}]"))
    with pytest.raises(ConfigError, match="group names are lowercase"):
        Config.from_dict(data)
```

(`PROJECT_TOML` is the conftest fixture text the file already imports; quoting the key as `[groups."my group"]` keeps the TOML parseable.) Also `assert Config.from_dict(tomllib.loads(PROJECT_TOML)).group_names == ("claude", "codex")` still holds.

Append to `tests/test_board.py`:

```python
def test_issue_has_no_assignees_field():
    from dataclasses import fields
    from meld.board import Issue
    assert "assignees" not in {f.name for f in fields(Issue)}
```

In `tests/test_doctor.py` delete `test_doctor_checks_every_bot_login_is_an_assignable_user` and `test_doctor_says_an_app_bot_cannot_hold_the_assignee_claim_lock`, and add:

```python
def test_doctor_has_no_assignability_check(tmp_path, keypair_pem):
    by_token = fakes()
    by_token[None].route("GET", "users/meld-claude[bot]", {"login": "meld-claude[bot]", "id": 99})
    get, post, _ = app_http()
    names = {c.name for c in run_app(tmp_path, by_token, keypair_pem, get=get, post=post)}
    assert not any(n.startswith("assignable:") for n in names)
    assert not any("assignees/" in c.endpoint for c in by_token[None].calls)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q -p no:cacheprovider tests/test_config.py tests/test_board.py tests/test_doctor.py -k "label_and_path_safe or no_assignees_field or no_assignability"` → fail.

- [ ] **Step 3: Implement**

`src/meld/config.py`: near the top, `GROUP_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")` and, as the first statement inside the `for name, g in ...groups...` loop of `from_dict`:

```python
            if not GROUP_NAME_RE.fullmatch(name) or name == "human":
                raise ConfigError(f"[groups.{name}]: group names are lowercase letters, digits, '-' or '_' (1-32 characters) and never 'human': "
                                  "a name becomes the meld:claimed-by:<group> label and a worktree path component")
```

(`"human"` is spelled out here rather than imported: `config.py` must not import `board.py`.)

`src/meld/board.py`: delete the `assignees: tuple[str, ...]` field from `Issue`. `src/meld/assemble.py::_issue`: delete the `assignees=...` argument. `src/meld/doctor.py`: delete the `assignable` block, the two `*_ASSIGNEE_FIX` constants with their comment, and the docstring sentence; if `quote` is no longer used, drop its import. `src/meld/setup.py`: delete the three-line "known limitation" element from the App `next_steps`. Fixtures and factories as listed in Files. Then run the suite and fix every remaining reference: `grep -rn "assignees" src tests` must show only `housekeeping.py`'s off-protocol sweep (events, not the Issue field), `test_housekeeping.py`'s matching event fixtures, and `test_github.py`'s arbitrary payload fields.

- [ ] **Step 4: Run the suite; commit**

Run: `uv run pytest -q -p no:cacheprovider` → green, no warnings.

```bash
git -C /Users/scottbrant/Documents/meld add src/meld/board.py src/meld/assemble.py src/meld/config.py src/meld/doctor.py src/meld/setup.py tests/
git -C /Users/scottbrant/Documents/meld commit -m "refactor: meld no longer reads or writes issue assignees; group names are label-safe; doctor drops the assignability check"
```

---

### Task 5: Documentation for the label lock

**Files:**
- Modify: `docs/superpowers/specs/2026-09-03-meld-design.md` (§20 step 1 ~462: delete the "An App group cannot claim and build yet…" sentence and say App groups claim like PAT groups; §21 item 3: `**Landing in plan 6b**` → `**Landed in plan 6b:** the \`meld:claimed-by:<group>\` label lock for every group (\`board.claim_label\`, \`actions.claim\`), assignees retired, \`assignable:<g>\` removed from doctor`; §21 `### Carried into plan 6` item 2's "login-status" annotation untouched), `README.md` (~101 and ~104: delete the limitation and the `assignable:<group>` warning sentence; the verbs table row for `meld claim` if it mentions assignees; the Doctor subsection sentence about the App warning), `docs/HANDOFF.md` (rewrite for the plan-6b milestone: delete the "one capability gap" paragraph at ~7; the repository map's `doctor.py` line drops `assignable`; a "Decisions for the owner (plan 6b)" section with this plan's ten decisions, each with the alternative not taken and the cost if wrong; "What is next" drops plan 6b and leads with plan 7 (the three claude-vibe-squad items) then the remaining `### Carried into plan 6` list; the migration note from decision 10; the test count from the suite), `src/meld/prompts/pm.md`/`orchestrator.md` only if they mention assignees (`grep -n assignee src/meld/prompts -r`).

**Interfaces:** documentation only.

- [ ] **Step 1: Make the edits**, verifying every sentence against the code at this branch's head (grep before writing).

- [ ] **Step 2: Verify**

Run: `grep -rn -i "assign" README.md docs/HANDOFF.md docs/superpowers/specs/2026-09-03-meld-design.md src/meld/prompts` — the stem, not `assignee\|assignable`, because the drift the narrower pattern missed said *assigned*/*assignment* (whole-branch review, Important 1). The only survivors are the off-protocol rows (assignee changes by strangers are still reported), the history clauses in §3/§6/§21 item 3 ("was the issue assignee") and HANDOFF's migration note, the negative statements ("assignees are not read", "assigning oneself holds nothing"), and §7/§13/§21's unrelated sense of the word (a ref number or a review concern is *assigned*). `uv run pytest -q -p no:cacheprovider` → green; put its count into HANDOFF.

- [ ] **Step 3: Commit**

```bash
git -C /Users/scottbrant/Documents/meld add docs/superpowers/specs/2026-09-03-meld-design.md README.md docs/HANDOFF.md src/meld/prompts
git -C /Users/scottbrant/Documents/meld commit -m "docs: the claim lock is the meld:claimed-by label; App groups claim and build; handoff for plan 6b"
```

---

## Self-review

- **Spec coverage:** §3 Identity row and §6 lock paragraph (rewritten with this plan) → Tasks 1–4; §8 claim/dispatch/release/collect bullets → Task 2 (legality) and Task 3 (actions); §11 fetch → Task 1/4 (assignees not read); §14 off-protocol row unchanged (decision 5); §20 → Task 5; §21 item 3 → Task 5.
- **Type consistency:** `claim_label(group) -> str`, `claim_group(label) -> str | None`, `Issue.claimants -> tuple[str, ...]`, `Issue.claimed_by -> str | None`, `Board.bots: dict[str, str]`, `Board.holder_login(issue) -> str | None`, `held_leases(leases, issues)`, `in_progress_count(group)`, `GitHub.ensure_label(name, color, description="") -> bool`, `GROUP_NAME_RE`, `HUMAN_CLAIMANT = "human"`, `CLAIM_LABEL_COLOR = "c5def5"` — used identically wherever they appear.
- **Placeholders:** none; the test helpers named (`fixture_issue`, `REGISTRY`, the human-actor `make_product` form) are defined or located in the task text.
