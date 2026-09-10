"""Builders for pure board objects used across rules/status tests."""
from __future__ import annotations

from meld.board import Board, Issue, Lease, PullRequest, Review, RunRecord, claim_label
from meld.config import Config
from meld.grammar import ParsedComment, parse_comment
from meld.models import ModelRegistry, ModelSpec


def _group(cli, bot, token):
    return {"cli": cli, "bot": bot, "token_env": token,
            "orchestrator": {"model": "claude-opus-5", "effort": "high"},
            "pm": {"model": "claude-sonnet-5", "effort": "medium"}}


CFG2 = Config.from_dict({
    "repo": {"name": "acme/widgets"},
    "humans": {"accounts": ["empowerScott"]},
    "groups": {"claude": _group("claude", "meld-claude-bot", "T1"), "codex": _group("codex", "meld-codex-bot", "T2")},
})

CFG3 = Config.from_dict({
    "repo": {"name": "acme/widgets"},
    "humans": {"accounts": ["empowerScott"]},
    "groups": {"claude": _group("claude", "meld-claude-bot", "T1"), "codex": _group("codex", "meld-codex-bot", "T2"),
               "gemini": _group("gemini", "meld-gemini-bot", "T3")},
})

STATUS_TO_LABEL = {"planned": "meld:planned", "ready": "meld:ready", "in_progress": "meld:in-progress",
                   "in_review": "meld:in-review", "blocked": "meld:blocked", "needs_human": "meld:needs-human"}


def mk_issue(number: int, status: str = "ready", worker: str | None = "backend", model: str | None = "claude-sonnet-5",
             effort: str | None = "medium", size: str | None = "S",
             claimed_by: str | None = None, extra_labels: tuple[str, ...] = (), state: str = "open",
             title: str | None = None, updated_at: str = "2026-09-03T10:00:00Z") -> Issue:
    labels = set(extra_labels)
    if status in STATUS_TO_LABEL:
        labels.add(STATUS_TO_LABEL[status])
    for prefix, val in (("worker:", worker), ("model:", model), ("effort:", effort), ("size:", size)):
        if val is not None:
            labels.add(prefix + val)
    if claimed_by is not None:
        labels.add(claim_label(claimed_by))
    return Issue(number=number, title=title or f"Issue {number}", body="", labels=frozenset(labels),
                 state=state, milestone=3, created_at="2026-09-03T09:00:00Z",
                 updated_at=updated_at)


def mk_pr(number: int, issue: int, head_sha: str = "abc123", committer: str | None = "meld-claude-bot",
          author: str | None = None, approvals: tuple[str, ...] = (), stale_approvals: tuple[str, ...] = (),
          changes_requested: tuple[str, ...] = (), extra_changes_requested_history: int = 0,
          checks: str = "green", state: str = "open", merged: bool = False,
          extra_labels: tuple[str, ...] = (), statuses: dict[str, str] | None = None) -> PullRequest:
    reviews: list[Review] = []
    t = 0
    for a in stale_approvals:
        t += 1
        reviews.append(Review(a, "APPROVED", "old000", f"2026-09-03T10:{t:02d}:00Z"))
    for _ in range(extra_changes_requested_history):
        t += 1
        reviews.append(Review((committer or "nobody") + "-other", "CHANGES_REQUESTED", "old000", f"2026-09-03T10:{t:02d}:00Z"))
    for a in changes_requested:
        t += 1
        reviews.append(Review(a, "CHANGES_REQUESTED", head_sha, f"2026-09-03T10:{t:02d}:00Z"))
    for a in approvals:
        t += 1
        reviews.append(Review(a, "APPROVED", head_sha, f"2026-09-03T10:{t:02d}:00Z"))
    return PullRequest(number=number, title=f"PR {number}", body=f"Closes #{issue}\n\nverified-by: pytest",
                       head_ref=f"meld/{issue}-slug", head_sha=head_sha, author=author or committer or "",
                       head_committer=committer, issue_number=issue, reviews=tuple(reviews), checks=checks,
                       labels=frozenset(extra_labels), state=state, merged=merged, statuses=dict(statuses or {}))


def mk_comment(*args, created_at: str | None = None) -> ParsedComment:
    """Accepts either the original positional shape (cfg, id, author, body, created_at=None), used throughout the
    rest of the suite, or the newer (id, author, body, created_at=None) shape (plan 5 task 1's test_rules_agreements.py
    and the test_status.py addition), which defaults cfg to CFG2. The two are disambiguated by the type of the first
    positional argument, since a Config and a comment id never overlap."""
    if args and isinstance(args[0], Config):
        cfg, id, author, body, *rest = args
    else:
        cfg = CFG2
        id, author, body, *rest = args
    if rest:
        created_at = rest[0]
    return parse_comment({"id": id, "user": {"login": author}, "body": body,
                          "created_at": created_at or f"2026-09-03T10:{id % 60:02d}:00Z"}, cfg)


def mk_run(group: str, issue: int, alive: bool = True, collected: bool = False, kind: str = "build",
           tier: str = "medium", pr: int | None = None) -> RunRecord:
    return RunRecord(group=group, issue=issue, kind=kind, pr=pr, pid=4242, started_at="2026-09-03T10:00:00Z",
                     worktree=f"/tmp/wt/{group}/{issue}", log_path=f"/tmp/wt/{group}/{issue}.log",
                     model="claude-sonnet-5", effort="medium", tier=tier, milestone=3, alive=alive, collected=collected)


def _keyed_by_number(items):
    """`issues`/`pulls` accept either a sequence of objects (the original shape, keyed here by `.number`) or a dict
    already keyed by number (plan 5 task 1's test_rules_agreements.py, which builds boards straight from
    `{n: mk_issue(n, ...)}`/`{n: mk_pr(n, ...)}`)."""
    return dict(items) if isinstance(items, dict) else {i.number: i for i in items}


def mk_board(cfg: Config = CFG2, issues=(), pulls=(), comments=(), phase: str = "executing", halted: bool = False,
             runs: dict[str, dict[int, RunRecord]] | None = None, spend: dict[str, dict[str, int]] | None = None,
             open_challenges: frozenset[str] = frozenset(), leases: dict[int, Lease] | None = None,
             plan_text: str = "", now: str = "2026-09-03T12:00:00Z") -> Board:
    return Board(repo=cfg.repo_name, milestone=3, coordination_issue=10, phase=phase, halted=halted,
                 issues=_keyed_by_number(issues), pulls=_keyed_by_number(pulls), comments=tuple(comments),
                 runs=runs or {g: {} for g in cfg.group_names}, spend=spend or {g: {} for g in cfg.group_names},
                 open_challenges=open_challenges, ignored_comments=0,
                 leases=leases or {}, plan_text=plan_text, now=now,
                 bots={g.name: g.bot for g in cfg.groups.values()})


def mk_lease(issue: int, group: str = "claude", until: str = "2026-09-03T13:00:00Z", comment_id: int = 900) -> Lease:
    return Lease(issue, group, until, comment_id)


def _spec(adapter: str, model_id: str, context_class: str, tier: str) -> ModelSpec:
    return ModelSpec(adapter, model_id, context_class, ("low", "medium", "high"), tier)


REG = ModelRegistry({
    "claude": {
        "claude-sonnet-5": _spec("claude", "claude-sonnet-5", "large", "medium"),
        "claude-haiku-4-5-20251001": _spec("claude", "claude-haiku-4-5-20251001", "medium", "small"),
    },
    "codex": {"gpt-test": _spec("codex", "gpt-test", "large", "medium")},
})
