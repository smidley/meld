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
from meld.grammar import agreement_for
from meld.plan import parse_plan_items, plan_ack, plan_approve
from meld.rules import Actor, challenge_rule, plan_rule
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
        needs_human.append({"issue": i.number, "title": i.title, "age_minutes": _age_minutes(i.updated_at, now), "ask": asks[-1].text if asks else "",
                            "comment_id": asks[-1].id if asks else None})
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


def _lines(text: str | None, n: int = 4) -> str:
    """The first `n` non-empty lines of a comment's text, which is all a decision card has room to say."""
    kept = [l.strip() for l in (text or "").splitlines() if l.strip() and not l.strip().lower().startswith("verified-by:")]
    return "\n".join(kept[:n]) + ("\n…" if len(kept) > n else "")


def decisions(board: Board, cfg: Config, actor: Actor, now: datetime) -> list[dict]:
    """Everything waiting on the human, each as one decision with its choices: the plan awaiting approval, a
    needs-human ask, a budget alert, a disagreement, a plan addition, a challenge the human must settle. Each
    choice is the request body `/api/action` takes (its `verb` is the dispatch name; `action` the header verb),
    plus whether the person must type a message; the page renders the card and sends exactly that. Legality
    stays `rules.py`'s: a choice is offered only when its rule answers None, and a refusal still comes back
    verbatim if the board moved meanwhile (spec 22)."""
    by_id = {c.id: c for c in board.comments}
    out: list[dict] = []
    plan_ref = f"plan-{board.milestone}"
    if board.phase == "planning" and plan_rule(board, cfg, actor, "approve", plan_ref) is None:
        agreement = agreement_for(board.comments, plan_ref, cfg)
        items, _ = parse_plan_items(agreement.text or "")
        out.append({"id": f"plan:{plan_ref}", "kind": "plan_approve", "ref": plan_ref, "age_minutes": None, "comment_id": agreement.joint_comment_id,
                    "title": f"The plan for milestone {board.milestone} is ready for your approval",
                    "summary": f"{len(items)} item(s): " + "; ".join(i.title for i in items) if items else "the agreed plan",
                    "choices": [{"label": "Approve the plan", "body": {"verb": "plan-approve"}, "needs_text": False}]})
    box = inbox(board, cfg, now)
    for item in box["needs_human"]:
        ref = f"#{item['issue']}"
        out.append({"id": f"needs-human:{ref}", "kind": "needs_human", "ref": ref, "age_minutes": item["age_minutes"], "comment_id": item["comment_id"],
                    "title": f"Issue {ref} needs you: {item['title']}", "summary": _lines(item["ask"]) or "(no ask was posted; see the issue)",
                    "choices": [{"label": "Provided", "body": {"verb": "post", "action": "PROVIDED", "ref": ref}, "needs_text": True,
                                 "hint": "say where you put it (a variable or 1Password item), never the value"},
                                {"label": "Resolved", "body": {"verb": "post", "action": "RESOLVED", "ref": ref}, "needs_text": True}]})
    for item in box["budget"]:
        ref = item["ref"]
        choice = ({"label": "Resolved", "body": {"verb": "post", "action": "RESOLVED", "ref": ref}, "needs_text": True} if ref
                  else {"label": "Give a directive", "body": {"verb": "post", "action": "DIRECTIVE", "ref": _next_id(board, _DIR_RE, "dir")}, "needs_text": True})
        out.append({"id": f"budget:{item['comment_id']}", "kind": "budget", "ref": ref, "age_minutes": item["age_minutes"], "comment_id": item["comment_id"],
                    "title": f"{item['group']} hit its budget" + (f" ({ref})" if ref else ""), "summary": _lines(item["text"]),
                    "choices": [{**choice, "hint": "raise the cap under Configuration first if the group should continue"}]})
    for item in box["disagree"]:
        ref, cmt = item["ref"], by_id.get(item["comment_id"])
        out.append({"id": f"disagree:{ref}", "kind": "disagree", "ref": ref, "age_minutes": _age_minutes(cmt.created_at, now) if cmt else None, "comment_id": item["comment_id"],
                    "title": f"The groups disagree on {ref} ({item['verb']})", "summary": _lines(cmt.text if cmt else "", 6),
                    "choices": [{"label": "Approve as proposed", "body": {"verb": "post", "action": "APPROVE", "ref": ref}, "needs_text": False},
                                {"label": "Give a directive", "body": {"verb": "post", "action": "DIRECTIVE", "ref": _next_id(board, _DIR_RE, "dir")}, "needs_text": True},
                                {"label": "Amend", "body": {"verb": "post", "action": "AMEND", "ref": ref}, "needs_text": True}]})
    for item in box["plan_add"]:
        ref, cmt = item["ref"], by_id.get(item["comment_id"])
        choices = [{"label": "Acknowledge", "body": {"verb": "plan-ack", "ref": ref}, "needs_text": False}] if plan_rule(board, cfg, actor, "ack", ref) is None else []
        out.append({"id": f"plan-add:{ref}", "kind": "plan_add", "ref": ref, "age_minutes": _age_minutes(cmt.created_at, now) if cmt else None, "comment_id": item["comment_id"],
                    "title": f"Plan addition {ref} awaits your acknowledgement", "summary": _lines(cmt.text if cmt else "", 6), "choices": choices})
    for ref, ch in sorted(board.challenges.items()):
        if challenge_rule(board, cfg, actor, "uphold", ref) is not None:
            continue
        cmt = by_id.get(ch.comment_id)
        out.append({"id": f"challenge:{ref}", "kind": "challenge", "ref": ref, "age_minutes": _age_minutes(cmt.created_at, now) if cmt else None, "comment_id": ch.comment_id,
                    "title": f"Challenge on {ref} by {ch.challenger}: your call", "summary": _lines(cmt.text if cmt else ""),
                    "choices": [{"label": "Uphold", "body": {"verb": "challenge", "action": "uphold", "ref": ref}, "needs_text": True},
                                {"label": "Dismiss", "body": {"verb": "challenge", "action": "dismiss", "ref": ref}, "needs_text": True}]})
    return out


def headline(status: dict, decisions: list[dict]) -> str:
    """One plain sentence about where the milestone stands and whether anything waits on the person, from `meld status`'s
    own JSON and the decisions: the first thing a person reads on the page, and the first line `meld chat` prints."""
    n = len(decisions)
    you = "nothing waits on you" if n == 0 else ("1 decision waits on you" if n == 1 else f"{n} decisions wait on you")
    if status.get("halted"):
        return f"Halted by a human: nothing moves until a RESUME; {you}."
    milestone = status.get("milestone")
    if status.get("phase") == "planning":
        if any(d["kind"] == "plan_approve" for d in decisions):
            return f"Planning milestone {milestone}: the groups have agreed a plan and it is ready for your approval."
        return f"Planning milestone {milestone}: the groups are agreeing a plan on the coordination issue; {you}."
    issues = status.get("issues", [])
    if issues and all(i["status"] == "closed" for i in issues):
        return f"Milestone {milestone} is complete: every issue is closed; {you}."
    counts: dict[str, int] = {}
    for i in issues:
        counts[i["status"]] = counts.get(i["status"], 0) + 1
    words = (("in_progress", "in progress"), ("in_review", "in review"), ("ready", "ready"), ("blocked", "blocked"),
             ("needs_human", "waiting on a person"), ("closed", "closed"))
    parts = [f"{counts[key]} {word}" for key, word in words if counts.get(key)]
    running = sum(1 for r in status.get("runs", []) if r.get("state") == "running")
    open_prs = [p for p in status.get("pulls", []) if p.get("state") == "open" and not p.get("merged")]
    mergeable = sum(1 for p in open_prs if "merge" in (p.get("legal") or []))
    bits = ["issues: " + (", ".join(parts) if parts else "none yet"),
            f"{running} worker{'' if running == 1 else 's'} running",
            f"{len(open_prs)} open PR{'' if len(open_prs) == 1 else 's'}" + (f" ({mergeable} ready to merge)" if mergeable else "")]
    return f"Executing milestone {milestone}: " + "; ".join(bits) + f"; {you}."


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
    now = datetime.now(timezone.utc)
    out(headline(data, decisions(board, ctx.cfg, actor, now)))
    out(_render_inbox(inbox(board, ctx.cfg, now)))

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
