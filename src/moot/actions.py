"""Mutating verbs on issues: claim and release. Each re-checks legality before acting."""
from __future__ import annotations

from typing import NoReturn

from moot.board import Board
from moot.config import Party
from moot.github import GitHubError
from moot.grammar import Header, compose
from moot.rules import Refusal, Verb, issue_refusal
from moot.status import build_status

READY, IN_PROGRESS, BLOCKED = "moot:ready", "moot:in-progress", "moot:blocked"


class Refused(Exception):
    def __init__(self, refusal: Refusal, post_error: str | None = None, violation_posted: bool | None = None):
        super().__init__(str(refusal))
        self.refusal = refusal
        self.post_error = post_error
        # True: a PROTOCOL-VIOLATION comment was posted. False: posting it was attempted
        # and failed. None: no post was attempted (a human refusal, or a lost claim race,
        # which is the designed outcome of first-assignment-wins rather than an illegal call).
        self.violation_posted = violation_posted


def refuse(ctx, board: Board | None, refusal: Refusal) -> NoReturn:
    actor = ctx.actor()
    post_error = None
    violation_posted: bool | None = None
    if board is not None and not actor.is_human:
        header = Header(actor.party, "PROTOCOL-VIOLATION", refusal.target, None)
        try:
            ctx.gh.create_comment(board.coordination_issue, compose(header, f"Refused `{refusal.verb.value}`: {refusal.rule}"))
            violation_posted = True
        except GitHubError as e:
            post_error = str(e)
            violation_posted = False
    raise Refused(refusal, post_error=post_error, violation_posted=violation_posted)


def _issue_or_refuse(ctx, board: Board, verb: Verb, number: int):
    issue = board.issues.get(number)
    if issue is None:
        refuse(ctx, board, Refusal(verb, f"#{number}", "issue is not in the active milestone"))
    return issue


def claim(ctx, number: int) -> dict:
    _, board = build_status(ctx)
    issue = _issue_or_refuse(ctx, board, Verb.CLAIM, number)
    actor = ctx.actor()
    refusal = issue_refusal(board, ctx.cfg, actor, Verb.CLAIM, issue, ctx.models)
    if refusal:
        refuse(ctx, board, refusal)
    ctx.gh.add_assignees(number, [actor.login])
    try:
        fresh = ctx.gh.get_issue(number, fresh=True)
        assignees = [a["login"] for a in fresh.get("assignees", [])]
        if set(assignees) != {actor.login}:
            ctx.gh.remove_assignees(number, [actor.login])
            others = [a for a in assignees if a != actor.login]
            # Losing the race is the designed outcome of first-assignment-wins, not an
            # illegal call, so it is refused without posting a PROTOCOL-VIOLATION.
            raise Refused(Refusal(Verb.CLAIM, f"#{number}", f"lost the claim race to {', '.join(others) or 'unknown'}"))
        ctx.gh.add_labels(number, [IN_PROGRESS])
        ctx.gh.remove_label(number, READY)
    except GitHubError:
        # Undo both halves of the claim, each best-effort, then surface the original error.
        for undo in (lambda: ctx.gh.remove_assignees(number, [actor.login]),
                     lambda: ctx.gh.remove_label(number, IN_PROGRESS)):
            try:
                undo()
            except GitHubError:
                pass
        raise
    return {"claimed": number, "assignee": actor.login}


def release(ctx, number: int, force: bool = False) -> dict:
    _, board = build_status(ctx)
    issue = _issue_or_refuse(ctx, board, Verb.RELEASE, number)
    actor = ctx.actor()
    if force and not actor.is_human:
        refuse(ctx, board, Refusal(Verb.RELEASE, f"#{number}", "--force is human-only"))
    refusal = issue_refusal(board, ctx.cfg, actor, Verb.RELEASE, issue, ctx.models)
    if refusal:
        refuse(ctx, board, refusal)
    if issue.assignees:
        ctx.gh.remove_assignees(number, list(issue.assignees))
    ctx.gh.add_labels(number, [READY])
    for label in (IN_PROGRESS, BLOCKED):
        if label in issue.labels:
            ctx.gh.remove_label(number, label)
    if force:
        note = compose(Header(Party("human"), "RELEASE", f"#{number}", None),
                       f"Force-released #{number} from {', '.join(issue.assignees) or 'nobody'}.")
        ctx.gh.create_comment(board.coordination_issue, note)
    return {"released": number, "previous_assignees": list(issue.assignees)}
