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
         "legal": legal_issue_verbs(board, cfg, actor, i, models)}
        for i in sorted(board.issues.values(), key=lambda i: i.number)
    ]
    pulls = [
        {"number": p.number, "issue": p.issue_number, "state": p.state, "head_sha": p.head_sha,
         "committer_party": committer_party(cfg, p), "approvals": sorted(approving_parties(cfg, p)),
         "changes_requested": sorted({q for q in (party_of_login(cfg, a) for a in p.changes_requested_on_head()) if q}),
         "checks": p.checks, "pingpong_exceeded": pingpong_exceeded(cfg, p),
         "legal": legal_pr_verbs(board, cfg, actor, p)}
        for p in sorted(board.pulls.values(), key=lambda p: p.number)
    ]
    runs = [
        {"group": r.group, "issue": r.issue, "kind": r.kind, "pr": r.pr, "pid": r.pid,
         "state": "running" if r.alive else "collected" if r.collected else "finished", "started_at": r.started_at}
        for g in sorted(board.runs) for r in sorted(board.runs[g].values(), key=lambda r: r.issue)
    ]
    refs = sorted({c.header.ref for c in board.comments if c.ok and c.header and c.header.ref and c.header.verb in AGREEMENT_VERBS})
    agreements = {ref: agreement_for(board.comments, ref, cfg).status for ref in refs}
    return {
        "repo": board.repo, "milestone": board.milestone, "coordination_issue": board.coordination_issue,
        "phase": board.phase, "halted": board.halted, "party": actor.party.label, "actor": actor.login,
        "quorum": cfg.effective_quorum, "needs_human": needs_human,
        "budget": {"spent": board.spend, "cap": cfg.budget},
        "issues": issues, "pulls": pulls, "runs": runs,
        "coordination": {"comments": len(board.comments), "last_comment_id": max((c.id for c in board.comments), default=0),
                         "open_challenges": sorted(board.open_challenges), "ignored_comments": board.ignored_comments,
                         "agreements": agreements},
        "digest": board_digest(board),
    }


def build_status(ctx, now: datetime | None = None) -> tuple[dict, Board]:
    runs = ctx.store.load_runs()
    board = build_board(ctx.gh, ctx.cfg, runs, {})
    board = replace(board, spend=ctx.store.load_spend(board.milestone))
    data = render_status(board, ctx.cfg, ctx.actor(), ctx.models, now)
    data["state_errors"] = list(ctx.store.errors)
    return data, board
