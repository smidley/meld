"""meld inbox and meld chat: what needs a person, and one console to answer as [human]."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Callable, Iterable

from meld.actions import Refused
from meld.board import Board
from meld.grammar import AGREEMENT_VERBS
from meld.challenge import challenge
from meld.config import Config
from meld.dispatch import DispatchError
from meld.github import GitHubError
from meld.plan import plan_ack, plan_approve
from meld.posting import PostError, post
from meld.status import _age_minutes, build_status
from meld.waiting import wait_for_change

_ASK_RE = re.compile(r"^ask-(\d+)$")
_DIR_RE = re.compile(r"^dir-(\d+)$")
_REF_RE = re.compile(r"^(#\d+|[a-z][a-z0-9]*(?:-[a-z0-9]+)*-\d+)$")   # #13, budget-1, plan-add-2, audit-40-1
# The verbs a reader catching up can skip: liveness and bookkeeping the framework and the PMs post on a cadence. The
# viewer's conversation panel hides them by default; the classification is made here so the page keeps no verb lists.
HOUSEKEEPING_VERBS = frozenset({"STATUS", "LEASE", "EXPIRED", "RELEASED", "RESUMED", "CONFIG", "ROSTER", "ROSTER-CHANGE", "LIMITED"})


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
    budget = []
    for c in comments:
        if c.header.verb != "BUDGET":
            continue
        ref = c.header.ref
        answered = (any(h.id > c.id and h.header.party.kind == "human" and h.header.verb != "ASK" and h.header.ref == ref for h in comments) if ref
                    else any(h.id > c.id and h.header.party.kind == "human" and h.header.verb != "ASK" for h in comments))
        if not answered:
            budget.append({"group": c.header.party.group, "ref": ref, "comment_id": c.id, "age_minutes": _age_minutes(c.created_at, now), "text": c.text})
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
    lines += ["Budget:"] + [f"  {b['group']} {b['ref'] or '(no ref)'}: {b['text'].splitlines()[0] if b['text'] else ''}" for b in box["budget"]]
    lines += ["Disagreements awaiting you:"] + [f"  {d['verb']} {d['ref']}" for d in box["disagree"]]
    lines += ["Plan additions awaiting ACK:"] + [f"  {p['ref']}" for p in box["plan_add"]]
    lines += ["Open asks:"] + [f"  {a['ref']}{'' if a['answered'] else ' (unanswered)'}" for a in box["asks"]]
    return "\n".join(lines)


def thread_lines(board: Board, last: int | None = None) -> list[str]:
    """The coordination thread as text, oldest first: every parsed, authorized comment (the ones the board reads), as
    `#<id> <time> <header>` followed by its text; `last` keeps only the newest N. The one paginated reader, for a
    person catching up (`/recent` in chat) and a restarted role rebuilding its picture (`meld thread`): a bare
    `gh api` returns thirty comments, and the first live run's thread passed a hundred in hours."""
    comments = _ordered(board)
    if last is not None:
        comments = comments[-last:] if last > 0 else comments
    return [f"#{c.id} {c.created_at} " + f"{c.header.party.label} {c.header.verb} {c.header.ref or ''} {c.header.qualifier or ''}".strip()
            + f"\n{c.text}" for c in comments]


def thread_entries(board: Board, last: int | None = None) -> list[dict]:
    """The same thread as `thread_lines`, as data for the viewer's conversation panel: oldest first, the newest `last`."""
    comments = _ordered(board)
    if last is not None and last > 0:
        comments = comments[-last:]
    return [{"id": c.id, "at": c.created_at, "party": c.header.party.label, "kind": c.header.party.kind, "group": c.header.party.group,
             "verb": c.header.verb, "ref": c.header.ref, "qualifier": c.header.qualifier, "text": c.text,
             "housekeeping": c.header.verb in HOUSEKEEPING_VERBS, "agreement": c.header.verb in AGREEMENT_VERBS} for c in comments]


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
    out(_render_inbox(inbox(board, ctx.cfg, datetime.now(timezone.utc))))

    def do_post(verb: str, ref: str | None, text: str, qualifier: str | None = None) -> None:
        nonlocal board, last_seen
        resp = post(ctx, verb, ref=ref, qualifier=qualifier, text=text)
        posted.append(resp["header"])
        out(resp["header"])
        _, board = build_status(ctx)
        last_seen = max(last_seen, resp["comment_id"] or last_seen)

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            if line == "/quit":
                break
            if line == "/inbox":
                out(_render_inbox(inbox(board, ctx.cfg, datetime.now(timezone.utc))))
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
            elif line == "/recent" or line.startswith("/recent "):
                count = line[len("/recent"):].strip() or "10"
                if not count.isdigit():
                    raise PostError("/recent takes a count, like /recent 20")
                _, board = build_status(ctx)
                for text in thread_lines(board, last=int(count)):
                    out(text)
            elif line.split(" ", 1)[0] in ("/provided", "/resolved", "/revert"):
                verb, _, rest = line[1:].partition(" ")
                ref, _, text = rest.partition(" ")
                # A NEEDS-HUMAN names an issue, but a BUDGET names budget-<n> and a DIRECTIVE dir-<n>: any ref the
                # grammar would accept is answerable from here, not only #N (handoff residual 13).
                if not _REF_RE.match(ref):
                    raise PostError(f"/{verb} needs a ref like #13 or budget-1, then the text")
                do_post(verb.upper(), ref, text or f"{verb} from meld chat")
            elif line.split(" ", 1)[0] == "/challenge":
                parts = line.split(" ", 3)
                if len(parts) < 3:
                    raise PostError("/challenge needs ACTION REF TEXT")
                action, ref = parts[1], parts[2]
                resp = challenge(ctx, action, ref, parts[3] if len(parts) > 3 else "")
                header = f"[human] {resp['verb']} {resp['ref']}"
                posted.append(header)
                out(header)
                _, board = build_status(ctx)
                last_seen = max(last_seen, resp["comment_id"] or last_seen)
            elif line.split(" ", 1)[0] == "/ack":
                ref = line[len("/ack"):].strip()
                if not ref:
                    raise PostError("/ack needs a ref like plan-add-1")
                out(json.dumps(plan_ack(ctx, ref)))
                _, board = build_status(ctx)
            elif line == "/approve":
                out(json.dumps(plan_approve(ctx)))
                _, board = build_status(ctx)
            elif line.startswith("/"):
                raise PostError(f"unknown command {line.split()[0]}; try /inbox /recent /wait /halt /resume /directive /provided /resolved /revert /ack /approve /challenge /quit")
            else:
                do_post("ASK", next_ask_id(board), line)
        except (PostError, Refused, DispatchError, GitHubError, ValueError) as e:
            errors.append(str(e))
            out(f"error: {e}")
    return {"posted": posted, "errors": errors}
