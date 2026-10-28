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
from meld.posting import PostError, next_ref, post
from meld.status import _age_minutes, build_status
from meld.waiting import wait_for_change

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
        needs_human.append({"issue": i.number, "ref": f"#{i.number}", "title": i.title, "age_minutes": _age_minutes(i.updated_at, now),
                            "ask": asks[-1].text if asks else "", "comment_id": asks[-1].id if asks else None})
    # A NEEDS-HUMAN about something other than a needs-human issue -- a PR, a bare number, no ref at all -- has no label to
    # carry it, so it waits here until a person posts anything on that ref: the second live run's codex PM asked about
    # `NEEDS-HUMAN 40` (a merged PR) and the page said nothing waited.
    covered = {item["ref"] for item in needs_human}
    for c in comments:
        if c.header.verb != "NEEDS-HUMAN" or c.header.party.kind == "human":
            continue
        raw = c.header.ref
        ref = f"#{raw}" if raw and raw.isdigit() else raw
        if ref in covered or any(h.id > c.id and h.header.party.kind == "human" and h.header.verb != "ASK" and (ref is None or h.header.ref in (ref, raw)) for h in comments):
            continue
        covered.add(ref)
        needs_human.append({"issue": None, "ref": ref, "party": c.header.party.label.strip("[]"), "title": (c.text.strip().splitlines() or [""])[0][:120],
                            "age_minutes": _age_minutes(c.created_at, now), "ask": c.text, "comment_id": c.id})
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
        ref = item["ref"]
        out.append({"id": f"needs-human:{ref}", "kind": "needs_human", "ref": ref, "age_minutes": item["age_minutes"], "comment_id": item["comment_id"],
                    "title": (f"Issue {ref} needs you: {item['title']}" if item["issue"] is not None
                              else f"{item['party']} needs you{f' about {ref}' if ref else ''}: {item['title']}"),
                    "summary": (_lines(item["ask"]) or "(no ask was posted; see the issue)")
                               + "\n\nWhat to do: if it asks for a credential, put it where it says, press Provided and say where (never the value). "
                                 "If it reports something Meld got wrong or a stuck state, fix that (or ask Claude Code to), press Resolved and say what changed. "
                                 "Either posts your reply on the thread for the agents to read.",
                    "choices": [{"label": "Provided", "body": {"verb": "post", "action": "PROVIDED", "ref": ref}, "needs_text": True,
                                 "hint": "one line for the agents: where you put it, or what changed"},
                                {"label": "Resolved", "body": {"verb": "post", "action": "RESOLVED", "ref": ref}, "needs_text": True,
                                 "hint": "one line for the agents: what changed"}]})
    for item in box["budget"]:
        ref = item["ref"]
        choice = ({"label": "Resolved", "body": {"verb": "post", "action": "RESOLVED", "ref": ref}, "needs_text": True} if ref
                  else {"label": "Give a directive", "body": {"verb": "post", "action": "DIRECTIVE", "ref": next_ref(board, "dir")}, "needs_text": True})
        out.append({"id": f"budget:{item['comment_id']}", "kind": "budget", "ref": ref, "age_minutes": item["age_minutes"], "comment_id": item["comment_id"],
                    "title": f"{item['group']} hit its budget" + (f" ({ref})" if ref else ""), "summary": _lines(item["text"]),
                    "choices": [{**choice, "hint": "raise the cap under Configuration first if the group should continue"}]})
    for item in box["disagree"]:
        ref, cmt = item["ref"], by_id.get(item["comment_id"])
        out.append({"id": f"disagree:{ref}", "kind": "disagree", "ref": ref, "age_minutes": _age_minutes(cmt.created_at, now) if cmt else None, "comment_id": item["comment_id"],
                    "title": f"The groups disagree on {ref} ({item['verb']})", "summary": _lines(cmt.text if cmt else "", 6),
                    "choices": [{"label": "Approve as proposed", "body": {"verb": "post", "action": "APPROVE", "ref": ref}, "needs_text": False},
                                {"label": "Give a directive", "body": {"verb": "post", "action": "DIRECTIVE", "ref": next_ref(board, "dir")}, "needs_text": True},
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


STEPS = ("claimed", "built", "pull request", "reviewed", "merged", "audited", "closed")


def _minutes(since: str | None, now: datetime) -> int | None:
    if not since:
        return None
    try:
        then = datetime.strptime(since, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0, int((now - then).total_seconds() // 60))


def journey(issue: dict, status: dict, now: datetime) -> dict:
    """Where one issue is on its way from claimed to closed, from `meld status`'s own JSON (plan 29): seven steps, each
    done, now or next with a detail, plus one sentence for what is happening and one for what comes next. The page draws
    the steps and prints the sentences; it decides nothing."""
    n = issue["number"]
    runs = status.get("runs", [])
    builds = [r for r in runs if r.get("kind") == "build" and r.get("issue") == n]
    prs = [p for p in status.get("pulls", []) if p.get("issue") == n]
    pr = max(prs, key=lambda p: p["number"]) if prs else None
    checks = [r for r in runs if pr and r.get("kind") in ("review", "audit") and r.get("issue") == pr["number"]]
    running = lambda rs: [r for r in rs if r.get("state") == "running"]   # noqa: E731
    quorum = status.get("quorum") or 1
    st = issue["status"]
    who = issue.get("claimed_by")
    done: dict[str, str] = {}
    if who or st in ("in_progress", "in_review", "closed", "blocked", "needs_human"):
        done["claimed"] = f"by {who}" if who else "claimed"
    if pr or any(r.get("outcome") in ("done", "no_change") for r in builds):
        last = [r for r in builds if r.get("outcome") in ("done", "no_change")]
        done["built"] = f"{last[-1]['group']}'s {last[-1].get('worker') or 'build'} worker" if last else "built"
    if pr:
        done["pull request"] = f"PR #{pr['number']}"
        if pr.get("merged") or len(pr.get("approvals") or []) >= quorum:
            done["reviewed"] = "approved by " + ", ".join(pr.get("approvals") or []) if pr.get("approvals") else "approved"
        if pr.get("merged"):
            done["merged"] = f"PR #{pr['number']} merged"
        if pr.get("audited_by"):
            done["audited"] = ", ".join(f"{g}: {v}" for g, v in sorted(pr["audited_by"].items()))
    if st == "closed":
        done["closed"] = "closed"
    # what is happening now, in the first step that is not done
    now_text, now_step = "", None
    for step in STEPS:
        if step in done:
            continue
        now_step = step
        break
    live_build, live_checks = running(builds), running(checks)
    if st == "needs_human":
        now_text = "waiting on a person"
    elif st == "blocked":
        now_text = "blocked: the worker could not finish; a retry or a person is needed"
    elif issue.get("challenged"):
        now_text = "under challenge"
    elif live_build:
        r = live_build[0]
        mins = _minutes(r.get("started_at"), now)
        now_text = f"{r['group']}'s {r.get('worker') or 'build'} worker is building it" + (f", {mins} min in" if mins is not None else "")
    elif live_checks:
        r = live_checks[0]
        mins = _minutes(r.get("started_at"), now)
        now_text = f"{r['group']} is {'auditing' if r.get('kind') == 'audit' else 'reviewing'} PR #{r.get('pr') or r.get('issue')}" + (f", {mins} min in" if mins is not None else "")
    elif pr and not pr.get("merged"):
        if pr.get("changes_requested"):
            now_text = f"PR #{pr['number']}: changes requested by {', '.join(pr['changes_requested'])}; the author group fixes and asks again"
        elif len(pr.get("approvals") or []) >= quorum:
            now_text = f"PR #{pr['number']} is approved" + ("; checks are red" if pr.get("checks") == "red" else "; checks are pending" if pr.get("checks") == "pending" else " and ready to merge")
        else:
            have = len(pr.get("approvals") or [])
            now_text = f"PR #{pr['number']} waits for a review ({have} of {quorum} approvals)"
    elif pr and pr.get("merged") and not pr.get("audited_by"):
        now_text = "merged; an audit is owed before the milestone closes"
    elif st == "ready":
        now_text = "ready for a group to claim"
    elif st == "planned":
        now_text = "planned; waits for the plan's approval"
    elif st == "in_progress" and not live_build and builds and builds[-1].get("outcome") == "capacity":
        now_text = f"{builds[-1]['group']}'s model was at capacity; the build is retried after a short pause"
    elif st == "in_progress" and not live_build:
        now_text = f"claimed by {who}; no worker is on it yet" if who else "in progress"
    elif st == "closed":
        now_text = "done"
    remaining = [s for s in STEPS if s not in done and s != now_step]
    next_text = "then " + ", ".join(remaining) if remaining else ""
    steps = [{"name": s, "state": "done" if s in done else ("now" if s == now_step else "next"), "detail": done.get(s, now_text if s == now_step else "")}
             for s in STEPS]
    return {"issue": n, "steps": steps, "now": now_text, "next": next_text}


def journeys(status: dict, now: datetime) -> dict[int, dict]:
    return {i["number"]: journey(i, status, now) for i in status.get("issues", [])}


def progress_line(status: dict, now: datetime) -> str:
    """The one line under the progress bar: what is left and where each piece stands (plan 29)."""
    issues = status.get("issues", [])
    left = [i for i in issues if i["status"] != "closed"]
    if not issues or not left:
        return ""
    js = journeys(status, now)
    if len(left) <= 3:
        return " · ".join(f"#{i['number']}: {js[i['number']]['now']}" for i in left)
    counts: dict[str, int] = {}
    for i in left:
        step = next((s["name"] for s in js[i["number"]]["steps"] if s["state"] == "now"), "closed")
        counts[step] = counts.get(step, 0) + 1
    return f"{len(left)} left: " + ", ".join(f"{c} at {s}" for s, c in counts.items())


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


def next_ask_id(board: Board) -> str:
    return next_ref(board, "ask")


def _render_inbox(box: dict) -> str:
    lines = ["Needs a human:"] + [f"  {i['ref'] or '(no ref)'} {i['title']} ({i['age_minutes']} min): {i['ask'].splitlines()[0] if i['ask'] else '(no ask posted)'}" for i in box["needs_human"]]
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
                do_post("DIRECTIVE", next_ref(board, "dir"), line[len("/directive "):])
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
