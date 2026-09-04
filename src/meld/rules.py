"""Legal-action computation. Every invariant lives here and has a test."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from meld.board import Board, Issue, PullRequest
from meld.config import Config, Party
from meld.models import SIZES, ModelRegistry, size_fits


class Verb(str, Enum):
    CLAIM = "claim"
    RELEASE = "release"
    DISPATCH = "dispatch"
    COLLECT = "collect"
    REOPEN = "reopen"
    APPROVE = "approve"
    REQUEST_CHANGES = "request-changes"
    PUSH_FIX = "push-fix"
    MERGE = "merge"
    POST = "post"


# POST is deliberately absent from both: it is refusable but not a per-issue or per-PR verb,
# so it never appears in an issue's or a PR's legal-action list.
ISSUE_VERBS = (Verb.CLAIM, Verb.RELEASE, Verb.DISPATCH, Verb.COLLECT, Verb.REOPEN)
PR_VERBS = (Verb.APPROVE, Verb.REQUEST_CHANGES, Verb.PUSH_FIX, Verb.MERGE)


@dataclass(frozen=True)
class Actor:
    party: Party
    login: str
    roster: frozenset[str] = frozenset()

    @property
    def is_human(self) -> bool:
        return self.party.kind == "human"

    @property
    def is_pm(self) -> bool:
        return self.party.kind == "pm"

    @property
    def group(self) -> str | None:
        return self.party.group

    @property
    def name(self) -> str:
        return self.group or f"human:{self.login}"


@dataclass(frozen=True)
class Refusal:
    verb: Verb
    target: str
    rule: str

    def __str__(self) -> str:
        return f"{self.verb.value} {self.target} refused: {self.rule}"


def party_of_login(cfg: Config, login: str) -> str | None:
    g = cfg.group_for_bot(login)
    if g is not None:
        return g.name
    if cfg.is_human(login):
        return f"human:{login}"
    return None


# ---- issue verbs -------------------------------------------------------------

def _claim(board: Board, cfg: Config, actor: Actor, issue: Issue, models: ModelRegistry) -> str | None:
    if board.halted:
        return "HALT is in effect"
    if board.phase != "executing":
        return "milestone is still in planning (no meld:plan-approved label)"
    if not (actor.is_pm or actor.is_human):
        return "only a PM or a human may claim"
    if issue.status != "ready":
        return f"issue is {issue.status}, not ready"
    if issue.assignees:
        return f"already claimed by {', '.join(issue.assignees)}"
    if actor.is_human:
        return None
    group = cfg.groups[actor.group]  # type: ignore[index]
    if issue.worker is None:
        return "issue has no worker: label"
    if issue.worker not in actor.roster:
        return f"worker type '{issue.worker}' is not in {group.name}'s roster"
    n = board.in_progress_count(group.bot)
    if n >= group.concurrency:
        return f"{group.name} is at its concurrency cap ({n}/{group.concurrency})"
    if issue.model is None:
        return "issue has no model: label"
    tier = models.tier_of(group.cli, issue.model)
    if tier is None:
        return f"model '{issue.model}' is unknown for adapter '{group.cli}'"
    used = board.spend.get(group.name, {}).get(tier, 0)
    cap = cfg.budget.get(tier, 0)
    if used >= cap:
        return f"{group.name} has exhausted its {tier}-tier budget ({used}/{cap})"
    return None


def _dispatch(board: Board, cfg: Config, actor: Actor, issue: Issue, models: ModelRegistry) -> str | None:
    if board.halted:
        return "HALT is in effect"
    if not actor.is_pm:
        return "only the PM may dispatch"
    group = cfg.groups[actor.group]  # type: ignore[index]
    if issue.status != "in_progress":
        return f"issue is {issue.status}, not in-progress"
    if group.bot not in issue.assignees:
        return f"issue is not assigned to {group.bot}"
    run = board.run_for(group.name, issue.number)
    if run is not None and (run.alive or not run.collected):
        return "a worker run already exists for this issue (running or uncollected)"
    if board.is_challenged(f"#{issue.number}"):
        return "issue has an open CHALLENGE"
    missing = [name for name, val in (("model", issue.model), ("effort", issue.effort), ("size", issue.size)) if val is None]
    if missing:
        return f"issue is missing labels: {', '.join(missing)}"
    errors = models.validate(group.cli, issue.model, issue.effort)  # type: ignore[arg-type]
    if errors:
        return errors[0]
    if issue.size not in SIZES:
        return f"size '{issue.size}' is not one of S, M, L"
    spec = models.get(group.cli, issue.model)  # type: ignore[arg-type]
    assert spec is not None
    if not size_fits(issue.size, spec.context_class):  # type: ignore[arg-type]
        return f"size {issue.size} does not fit model {issue.model} (context class {spec.context_class})"
    return None


def _collect(board: Board, cfg: Config, actor: Actor, issue: Issue) -> str | None:
    if not actor.is_pm:
        return "only the PM may collect"
    run = board.run_for(actor.group or "", issue.number)
    if run is None:
        return f"no run record for #{issue.number} in {actor.group}"
    if run.alive:
        return "worker is still running"
    if run.collected:
        return "run already collected"
    return None


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


def _reopen(board: Board, cfg: Config, actor: Actor, issue: Issue) -> str | None:
    if issue.state != "closed":
        return "issue is not closed"
    if actor.is_human:
        return None
    ref = f"#{issue.number}"
    audits = [
        c for c in board.comments
        if c.ok and c.header is not None and c.header.verb == "AUDIT" and c.header.ref == ref
        and c.header.qualifier == "FAIL" and c.header.party.group == actor.group
    ]
    if not audits:
        return f"no AUDIT {ref} FAIL posted by {actor.group}"
    pr = board.pr_for_issue(issue.number)
    if pr is not None and party_of_login(cfg, pr.author) == actor.group:
        return f"{actor.group} authored the PR that closed {ref}"
    return None


def issue_refusal(board: Board, cfg: Config, actor: Actor, verb: Verb, issue: Issue, models: ModelRegistry) -> Refusal | None:
    if verb == Verb.CLAIM:
        rule = _claim(board, cfg, actor, issue, models)
    elif verb == Verb.DISPATCH:
        rule = _dispatch(board, cfg, actor, issue, models)
    elif verb == Verb.COLLECT:
        rule = _collect(board, cfg, actor, issue)
    elif verb == Verb.RELEASE:
        rule = _release(board, cfg, actor, issue)
    elif verb == Verb.REOPEN:
        rule = _reopen(board, cfg, actor, issue)
    else:
        rule = f"{verb.value} is not an issue verb"
    return Refusal(verb, f"#{issue.number}", rule) if rule else None


def legal_issue_verbs(board: Board, cfg: Config, actor: Actor, issue: Issue, models: ModelRegistry) -> list[str]:
    return [v.value for v in ISSUE_VERBS if issue_refusal(board, cfg, actor, v, issue, models) is None]


# ---- PR verbs ----------------------------------------------------------------

UNKNOWN_COMMITTER = "head committer unknown; cannot verify who made the last commit"


def committer_party(cfg: Config, pr: PullRequest) -> str | None:
    return None if pr.head_committer is None else party_of_login(cfg, pr.head_committer)


def approving_parties(cfg: Config, pr: PullRequest) -> frozenset[str]:
    committer = committer_party(cfg, pr)
    parties = (party_of_login(cfg, login) for login in pr.approvals_on_head())
    return frozenset(p for p in parties if p is not None and p != committer)


def pingpong_exceeded(cfg: Config, pr: PullRequest) -> bool:
    return pr.changes_requested_total() >= cfg.pingpong_cap


def _approve(board: Board, cfg: Config, actor: Actor, pr: PullRequest) -> str | None:
    if actor.name == committer_party(cfg, pr):
        return f"{actor.name} made the last commit on this PR and cannot approve its own head"
    if board.is_challenged(f"#{pr.number}"):
        return "PR has an open CHALLENGE"
    if pr.head_committer is None:
        return UNKNOWN_COMMITTER
    return None


def _other_party_only(cfg: Config, actor: Actor, pr: PullRequest, verb: Verb) -> str | None:
    if actor.name == committer_party(cfg, pr):
        return f"{actor.name} made the last commit on this PR; only other parties may {verb.value}"
    return None


def _merge(board: Board, cfg: Config, actor: Actor, pr: PullRequest) -> str | None:
    if board.halted:
        return "HALT is in effect"
    if not (actor.is_pm or actor.is_human):
        return "only a PM or a human may merge"
    if board.is_challenged(f"#{pr.number}"):
        return "PR has an open CHALLENGE"
    if pr.head_committer is None:
        return UNKNOWN_COMMITTER
    if pr.checks != "green":
        return f"required checks are {pr.checks}, not green"
    have = approving_parties(cfg, pr)
    need = cfg.effective_quorum
    if len(have) < need:
        return f"{len(have)}/{need} approvals from non-committer parties (have: {sorted(have)})"
    return None


def pr_refusal(board: Board, cfg: Config, actor: Actor, verb: Verb, pr: PullRequest) -> Refusal | None:
    if pr.state != "open":
        rule: str | None = "PR is not open"
    elif verb == Verb.APPROVE:
        rule = _approve(board, cfg, actor, pr)
    elif verb in (Verb.REQUEST_CHANGES, Verb.PUSH_FIX):
        rule = _other_party_only(cfg, actor, pr, verb)
    elif verb == Verb.MERGE:
        rule = _merge(board, cfg, actor, pr)
    else:
        rule = f"{verb.value} is not a PR verb"
    return Refusal(verb, f"#{pr.number}", rule) if rule else None


def legal_pr_verbs(board: Board, cfg: Config, actor: Actor, pr: PullRequest) -> list[str]:
    return [v.value for v in PR_VERBS if pr_refusal(board, cfg, actor, v, pr) is None]
