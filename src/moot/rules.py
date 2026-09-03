"""Legal-action computation. Every invariant lives here and has a test."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from moot.board import Board, Issue, PullRequest
from moot.config import Config, Party
from moot.models import SIZES, ModelRegistry, size_fits


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
        return "milestone is still in planning (no moot:plan-approved label)"
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


def _release(board: Board, cfg: Config, actor: Actor, issue: Issue) -> str | None:
    if issue.status not in ("in_progress", "blocked"):
        return f"issue is {issue.status}; release is only legal from in-progress or blocked"
    if actor.is_human:
        return None
    group = cfg.groups[actor.group]  # type: ignore[index]
    if group.bot not in issue.assignees:
        return f"issue is not assigned to {group.bot}"
    return None


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
