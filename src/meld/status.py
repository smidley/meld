"""Render the board plus legal actions for the calling party."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from meld.assemble import build_board
from meld.board import Board
from meld.config import Config
from meld.grammar import AGREEMENT_VERBS, agreement_for
from meld.models import ModelRegistry
from meld.rules import (
    Actor,
    approving_parties,
    committer_party,
    legal_issue_verbs,
    legal_pr_verbs,
    party_of_login,
    pingpong_exceeded,
)
from meld.state import board_digest


def _age_minutes(iso: str, now: datetime) -> int:
    if not iso:
        return 0
    then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return max(0, int((now - then).total_seconds() // 60))


def _lease_json(board: Board, issue) -> dict | None:
    if issue.status != "in_progress":
        return None
    lease = board.leases.get(issue.number)
    if lease is None:
        return None
    return {"group": lease.group, "until": lease.until, "expired": board.lease_expired(issue.number)}


def render_status(board: Board, cfg: Config, actor: Actor, models: ModelRegistry, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    needs_human = sorted(
        ({"issue": i.number, "title": i.title, "age_minutes": _age_minutes(i.updated_at, now)}
         for i in board.issues.values() if i.status == "needs_human"),
        key=lambda d: -d["age_minutes"],
    )
    issues = [
        {"number": i.number, "title": i.title, "status": i.status, "assignees": list(i.assignees), "worker": i.worker,
         "model": i.model, "effort": i.effort, "size": i.size, "challenged": board.is_challenged(f"#{i.number}"),
         "legal": legal_issue_verbs(board, cfg, actor, i, models), "lease": _lease_json(board, i)}
        for i in sorted(board.issues.values(), key=lambda i: i.number)
    ]
    pulls = [
        {"number": p.number, "issue": p.issue_number, "state": p.state, "head_sha": p.head_sha,
         "committer_party": committer_party(cfg, p), "approvals": sorted(approving_parties(cfg, p)),
         "changes_requested": sorted({q for q in (party_of_login(cfg, a) for a in p.changes_requested_on_head()) if q}),
         "checks": p.checks, "pingpong_exceeded": pingpong_exceeded(cfg, p),
         "merged": p.merged, "merged_at": p.merged_at, "files": len(p.files),
         "touches_security_paths": p.touches(cfg.security_paths), "audited_by": dict(board.audits.get(p.number, {})),
         "legal": legal_pr_verbs(board, cfg, actor, p)}
        for p in sorted(board.pulls.values(), key=lambda p: p.number)
    ]
    runs = [
        {"group": r.group, "issue": r.issue, "kind": r.kind, "pr": r.pr, "pid": r.pid,
         "state": "running" if r.alive else "collected" if r.collected else "finished", "started_at": r.started_at,
         "deadline": r.deadline, "branch": r.branch, "timed_out": r.timed_out}
        for g in sorted(board.runs) for r in sorted(board.runs[g].values(), key=lambda r: r.issue)
    ]
    refs = sorted({c.header.ref for c in board.comments if c.ok and c.header and c.header.ref and c.header.verb in AGREEMENT_VERBS})
    agreements = {ref: {"status": ag.status, "joint_verified": ag.joint_verified} for ref, ag in ((r, agreement_for(board.comments, r, cfg)) for r in refs)}
    return {
        "repo": board.repo, "milestone": board.milestone, "coordination_issue": board.coordination_issue,
        "phase": board.phase, "halted": board.halted, "party": actor.party.label, "actor": actor.login,
        "quorum": cfg.effective_quorum, "needs_human": needs_human,
        "budget": {"spent": board.spend, "cap": cfg.budget},
        "issues": issues, "pulls": pulls, "runs": runs,
        "coordination": {"comments": len(board.comments), "last_comment_id": max((c.id for c in board.comments), default=0),
                         "open_challenges": sorted(board.open_challenges), "ignored_comments": board.ignored_comments,
                         "challenges": [{"ref": c.ref, "challenger": c.challenger, "comment_id": c.comment_id, "opened_at": c.opened_at}
                                        for c in sorted(board.challenges.values(), key=lambda c: c.ref)],
                         "agreements": agreements},
        "audit": audit_summary(board, cfg, actor, now),
        "digest": board_digest(board),
    }


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


def build_status(ctx, now: datetime | None = None) -> tuple[dict, Board]:
    runs = ctx.store.load_runs()
    board = build_board(ctx.gh, ctx.cfg, runs, {}, errors=ctx.store.errors)   # non-fatal read problems join state_errors below
    board = replace(board, spend=ctx.store.load_spend(board.milestone))
    data = render_status(board, ctx.cfg, ctx.actor(), ctx.models, now)
    data["state_errors"] = list(ctx.store.errors)
    return data, board
