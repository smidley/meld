"""Mutating verbs on issues: claim and release. Each re-checks legality before acting."""
from __future__ import annotations

from typing import NoReturn

from meld.board import Board, derive_leases
from meld.config import Party
from meld.github import GitHubError
from meld.grammar import Header, compose
from meld.rules import Refusal, Verb, issue_refusal
from meld.status import build_status

READY, IN_PROGRESS, BLOCKED, PLANNED = "meld:ready", "meld:in-progress", "meld:blocked", "meld:planned"


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
    # A LEASE comment outlives the claim it describes (meld release posts nothing to retract it).
    # Without this, a stale LEASE from an earlier run of ours would revive on this fresh claim and
    # let another PM release it once the old `until` passed, even though nothing we hold has expired.
    # Superseding it with EXPIRED here, at the moment we reclaim the issue, closes that gap.
    if actor.is_pm:
        stale = derive_leases(board.comments).get(number)   # the raw thread, not board.leases: a lease we no longer hold is filtered there
        if stale is not None and stale.group == actor.group:
            ctx.gh.create_comment(board.coordination_issue, compose(
                Header(Party("pm", actor.group), "EXPIRED", f"#{number}", None),
                f"stale lease superseded by a new claim; the next `meld dispatch {number}` posts a fresh LEASE"))
    return {"claimed": number, "assignee": actor.login}


def release(ctx, number: int, force: bool = False, announce: bool = True) -> dict:
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
    if not actor.is_human and announce:
        # Decision 2 (plan 5): a framework-only RELEASED comment so a stale LEASE on this issue reads as void
        # immediately, rather than waiting for a PM's tick to notice and post EXPIRED (board.derive_leases closes
        # the lease on either verb from a group or PM party). announce=False lets a caller that is about to post its
        # own, more specific framework comment for this same event (dispatch.tick's expired-lease reclaim posts
        # EXPIRED, naming the lease that expired) skip this one -- one event, one framework comment.
        note = compose(Header(actor.party, "RELEASED", f"#{number}", None), f"Released #{number}; any earlier LEASE on it is void.")
        ctx.gh.create_comment(board.coordination_issue, note)
    if force:
        note = compose(Header(Party("human"), "RELEASE", f"#{number}", None),
                       f"Force-released #{number} from {', '.join(issue.assignees) or 'nobody'}.")
        ctx.gh.create_comment(board.coordination_issue, note)
    return {"released": number, "previous_assignees": list(issue.assignees)}
