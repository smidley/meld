"""Mutating verbs on issues: claim and release. Each re-checks legality before acting."""
from __future__ import annotations

from typing import NoReturn

from meld.board import HUMAN_CLAIMANT, Board, claim_group, claim_label, derive_leases
from meld.config import Party
from meld.github import GitHubError
from meld.grammar import Header, compose
from meld.rules import Refusal, Verb, issue_refusal
from meld.status import build_status

READY, IN_PROGRESS, BLOCKED, PLANNED = "meld:ready", "meld:in-progress", "meld:blocked", "meld:planned"
CLAIM_LABEL_COLOR = "c5def5"


class Refused(Exception):
    def __init__(self, refusal: Refusal, post_error: str | None = None, violation_posted: bool | None = None):
        super().__init__(str(refusal))
        self.refusal = refusal
        self.post_error = post_error
        # True: a PROTOCOL-VIOLATION comment was posted. False: posting it was attempted and failed. None: no
        # post was attempted -- a human refusal, a lost claim race (the designed outcome of first-label-wins
        # rather than an illegal call), or the board already carried this same violation from this party about
        # this target (residual 14: a re-derived call that repeats a refusal already on the record).
        self.violation_posted = violation_posted


def _refusal_text(verb: Verb, rule: str) -> str:
    """The exact body `refuse` posts for a refusal of `verb` naming `rule`. Built in one place and used both
    to compose the post and, in `_already_said`, to compare against what is already on the board -- so the
    text that gets checked and the text that gets posted can never drift apart."""
    return f"Refused `{verb.value}`: {rule}"


def _already_said(board: Board, party: Party, target: str, verb: Verb, rule: str) -> bool:
    """Is the newest PROTOCOL-VIOLATION from this party about this target already this exact refusal?

    The board is the throttle, not a local file: two supervisors and a restart all read the same history,
    so GitHub's own record is the only place the count can be right (the `meld:nudged` argument, spec 14).
    Only the NEWEST matching violation counts -- a different refusal posted since means the topic moved.

    Comments are gated on `.ok` before their header is trusted, the same as every other reader of
    `ParsedComment.header` in this codebase (`agreement_for` in grammar.py; several checks in board.py):
    `parse_comment` parses `header` from the text alone, independent of who posted it, so an unauthorized
    comment that merely types the header line is not this party's own record and must not suppress a
    genuinely new refusal.

    The match compares the whole composed body, not a substring of the rule: `_claim` builds rules like
    "already claimed by claude" that are a literal prefix of "already claimed by claude, grunt", and
    `_collect`/`reap_rule` both return the exact text "worker is still running" for entirely different
    verbs -- a substring or rule-only match would let one throttle the other.
    """
    expected = _refusal_text(verb, rule)
    for comment in sorted(board.comments, key=lambda c: (c.created_at, c.id), reverse=True):
        if not comment.ok or comment.header is None:
            continue
        h = comment.header
        if h.verb != "PROTOCOL-VIOLATION" or h.party != party or h.ref != target:
            continue
        return comment.text.strip() == expected
    return False


def _status_said(ctx, board: Board, actor, refusal: Refusal) -> bool:
    """Was this refusal already in `meld status`'s `refused` table for this target and party (plan 67)? Then the party
    ran a verb the board had told it was illegal, and the refusal text is the whole answer: a PROTOCOL-VIOLATION on the
    thread would only wake the other sessions. The maze's claude PM re-ran refused dispatches and claims on every launch,
    and each one cost a post and an orchestrator's reply."""
    from meld.rules import issue_verb_rules, pr_verb_rules
    n = refusal.target.lstrip("#")
    if not n.isdigit():
        return False
    try:
        if int(n) in board.issues:
            table = issue_verb_rules(board, ctx.cfg, actor, board.issues[int(n)], ctx.models)
        elif int(n) in board.pulls:
            table = pr_verb_rules(board, ctx.cfg, actor, board.pulls[int(n)])
        else:
            return False
    except Exception:   # a table that cannot be computed is not a reason to lose the refusal
        return False
    said = [r for r in table.values() if r]
    return any(refusal.rule == r or refusal.rule.endswith(": " + r) for r in said)


def refuse(ctx, board: Board | None, refusal: Refusal, *, quiet: bool = False) -> NoReturn:
    """Refuse, and for a group party post PROTOCOL-VIOLATION on the thread unless the refusal is already said: by `quiet`,
    by an identical violation already posted, or by `meld status`'s own refused table (plan 67)."""
    actor = ctx.actor()
    post_error = None
    violation_posted: bool | None = None
    if board is not None and not actor.is_human and not quiet:
        header = Header(actor.party, "PROTOCOL-VIOLATION", refusal.target, None)
        if _already_said(board, actor.party, refusal.target, refusal.verb, refusal.rule) or _status_said(ctx, board, actor, refusal):
            raise Refused(refusal, post_error=None, violation_posted=None)
        try:
            ctx.gh.create_comment(board.coordination_issue, compose(header, _refusal_text(refusal.verb, refusal.rule)))
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


def _claim_labels_of(raw_issue: dict) -> tuple[str, ...]:
    """Claimants named by a raw GitHub issue payload (the fresh re-read in `claim`), sorted like Issue.claimants."""
    names = ((l.get("name", "") if isinstance(l, dict) else str(l)) for l in raw_issue.get("labels", []))
    return tuple(sorted(g for g in (claim_group(n) for n in names) if g))


def claim(ctx, number: int) -> dict:
    _, board = build_status(ctx)
    issue = _issue_or_refuse(ctx, board, Verb.CLAIM, number)
    actor = ctx.actor()
    refusal = issue_refusal(board, ctx.cfg, actor, Verb.CLAIM, issue, ctx.models)
    if refusal:
        refuse(ctx, board, refusal)
    holder = HUMAN_CLAIMANT if actor.is_human else actor.group
    own = claim_label(holder)   # type: ignore[arg-type]
    ctx.gh.ensure_label(own, CLAIM_LABEL_COLOR, f"Claimed by {holder}")
    ctx.gh.add_labels(number, [own])
    try:
        claimants = _claim_labels_of(ctx.gh.get_issue(number, fresh=True))
        if claimants != (holder,):
            ctx.gh.remove_label(number, own)
            others = [g for g in claimants if g != holder]
            # Losing the race is the designed outcome of first-label-wins (spec 6), not an illegal call, so it is
            # refused without posting a PROTOCOL-VIOLATION. Both racers can lose and both retry; none can both win.
            raise Refused(Refusal(Verb.CLAIM, f"#{number}", f"lost the claim race to {', '.join(others) or 'unknown'}"))
        ctx.gh.add_labels(number, [IN_PROGRESS])
        ctx.gh.remove_label(number, READY)
    except GitHubError:
        # Undo both halves of the claim, each best-effort, then surface the original error.
        for undo in (lambda: ctx.gh.remove_label(number, own), lambda: ctx.gh.remove_label(number, IN_PROGRESS)):
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
    return {"claimed": number, "claimed_by": holder}


def release(ctx, number: int, force: bool = False, announce: bool = True) -> dict:
    _, board = build_status(ctx)
    issue = _issue_or_refuse(ctx, board, Verb.RELEASE, number)
    actor = ctx.actor()
    if force and not actor.is_human:
        refuse(ctx, board, Refusal(Verb.RELEASE, f"#{number}", "--force is human-only"))
    refusal = issue_refusal(board, ctx.cfg, actor, Verb.RELEASE, issue, ctx.models)
    if refusal:
        refuse(ctx, board, refusal)
    for g in issue.claimants:
        ctx.gh.remove_label(number, claim_label(g))
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
                       f"Force-released #{number} from {', '.join(issue.claimants) or 'nobody'}.")
        ctx.gh.create_comment(board.coordination_issue, note)
    return {"released": number, "previous_claimants": list(issue.claimants)}
