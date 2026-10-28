"""meld plan: a verified [joint] PLAN becomes planned issues; the human approves the plan or acks an addition."""
from __future__ import annotations

import re
from dataclasses import dataclass

from meld.actions import PLANNED, READY, refuse
from meld.board import PLAN_APPROVED_LABEL
from meld.grammar import agreement_for
from meld.models import SIZES
from meld.posting import post
from meld.rules import Refusal, Verb, plan_rule
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


def _planned_for(board, ref: str) -> list[int]:
    return sorted(n for n, i in board.issues.items() if PLANNED in i.labels and f"plan-ref: {ref}" in (i.body or ""))


def _ready(ctx, numbers: list[int]) -> None:
    for n in numbers:
        ctx.gh.add_labels(n, [READY])
        ctx.gh.remove_label(n, PLANNED)


def plan_issues(ctx, ref: str, *, dry_run: bool = False) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    rule = plan_rule(board, ctx.cfg, actor, "issues", ref)
    agreement = agreement_for(board.comments, ref, ctx.cfg)
    items, errors = parse_plan_items(agreement.text or "")
    existing = {m.group(1) for i in board.issues.values() for m in [MARKER_RE.search(i.body or "")] if m}
    if rule and rule.endswith("its issues can no longer be created") and items and all(item.id in existing for item in items):
        # Plan 54: the orchestrator read a planning board, the person approved the plan before its call landed, and the call
        # would create nothing: the rule guards new issues after approval, and there are none. A refusal here is a
        # PROTOCOL-VIOLATION for a benign race (the rehearsal hit it one run in three).
        return {"plan": ref, "created": [], "skipped": [item.id for item in items], "errors": errors, "would_create": [], "dry_run": dry_run, "already": True}
    if rule:
        refuse(ctx, board, Refusal(Verb.PLAN, ref, rule))
    created: list[int] = []
    would_create: list[str] = []
    skipped: list[str] = []
    for item in items:
        if item.id in existing:
            skipped.append(item.id)
            continue
        would_create.append(item.id)
        body = f"plan-item: {item.id}\nplan-ref: {ref}\n\n## Acceptance criteria\n" + "\n".join(f"- {c}" for c in item.criteria)
        labels = [PLANNED, f"worker:{item.worker}", f"model:{item.model}", f"effort:{item.effort}", f"size:{item.size}"]
        if not dry_run:
            created.append(int(ctx.gh.create_issue(item.title, body, labels, board.milestone)["number"]))
    if agreement.joint_verb == "PLAN" and not dry_run:
        ctx.gh.update_milestone(board.milestone, description=agreement.text or "")
    return {"plan": ref, "created": created, "skipped": skipped, "errors": errors, "would_create": would_create, "dry_run": dry_run}


def plan_approve(ctx) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    ref = f"plan-{board.milestone}"
    rule = plan_rule(board, ctx.cfg, actor, "approve", ref)
    if rule:
        refuse(ctx, board, Refusal(Verb.PLAN, ref, rule))
    ready = sorted(n for n, i in board.issues.items() if PLANNED in i.labels)
    # Flip first, label last: the label is what makes the milestone `executing` (and this very call refuse as "already
    # approved"), so a failure while flipping leaves a still-planning board that a retry completes -- flipping is idempotent.
    _ready(ctx, ready)
    ctx.gh.add_labels(board.coordination_issue, [PLAN_APPROVED_LABEL])
    return {"approved": ref, "ready": ready}


def plan_ack(ctx, ref: str) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    rule = plan_rule(board, ctx.cfg, actor, "ack", ref)
    if rule:
        refuse(ctx, board, Refusal(Verb.PLAN, ref, rule))
    already_acked = any(c.ok and c.header is not None and c.header.party.kind == "human" and c.header.verb == "ACK" and c.header.ref == ref
                        for c in board.comments)
    if not already_acked:
        post(ctx, "ACK", ref=ref, text=f"{ref} acknowledged; its issues are ready")
    ready = _planned_for(board, ref)
    _ready(ctx, ready)
    return {"acked": ref, "ready": ready}
