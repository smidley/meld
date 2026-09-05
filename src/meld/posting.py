"""Post a coordination comment in the header grammar, including verified [joint] comments."""
from __future__ import annotations

from typing import NoReturn

from meld.actions import refuse
from meld.board import Board
from meld.config import Party
from meld.grammar import AGREEMENT_VERBS, Agreement, Header, agreement_for, allowed_verbs, compose, validate
from meld.rules import Refusal, Verb
from meld.status import build_status


class PostError(ValueError):
    """A usage error: the call is malformed, not a legal call that the rules refuse."""


# Written by meld itself (dispatch and tick) as `[<group>:pm]`; `meld post` refuses them for every
# caller, so a lease can only be asserted by an actual run and only expired by a tick.
FRAMEWORK_ONLY_VERBS = frozenset({"LEASE", "EXPIRED", "IGNORED-COMMENT"})
# Challenge verbs carry a label change and closer rules; `meld challenge` applies them, `meld post` refuses them.
COMMAND_VERBS = frozenset({"CHALLENGE", "EVIDENCE", "RETRACT", "UPHELD", "DISMISSED"})


def _refuse(ctx, board: Board, verb: str, ref: str | None, rule: str) -> NoReturn:
    """Refuse a rule-level violation: a group party also posts PROTOCOL-VIOLATION."""
    refuse(ctx, board, Refusal(Verb.POST, ref or verb, rule))


def _indent(text: str) -> str:
    """Indent continuation lines so markdown keeps multi-line text inside its bullet."""
    return text.replace("\n", "\n  ")


def _bullet(label: str, text: str) -> str:
    return f"- **{label}**: {_indent(text)}"


def _dissent_section(dissent: dict[str, str]) -> str:
    return "\n".join(_bullet(g, t) for g, t in sorted(dissent.items()))


def _joint_body(agreement: Agreement, ref: str, qualifier: str | None, text: str) -> str:
    """The body of a [joint] comment: the agreed text verbatim, never the caller's substitute."""
    addendum = text.strip()
    if agreement.status in ("agreed", "majority"):
        body = agreement.text or ""
        if agreement.status == "majority":
            body = f"{body}\n\n## DISSENT\n{_dissent_section(agreement.dissent)}"
        if addendum:
            body = f"{body}\n\n## Addendum\n{addendum}"
        return body
    if qualifier != "DISAGREE":
        raise PostError(f"agreement on {ref} has no majority; post with qualifier DISAGREE")
    proposal = f"- **{agreement.proposer}** (proposal): {_indent(agreement.text or '')}"
    return f"## Positions\n{proposal}\n{_dissent_section(agreement.dissent)}\n\n{addendum}".strip()


def post(ctx, verb: str, ref: str | None = None, qualifier: str | None = None, text: str = "", joint: bool = False) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    if joint and actor.is_human:
        raise PostError("humans cannot post [joint] comments")
    if verb in FRAMEWORK_ONLY_VERBS:
        raise PostError(f"{verb} is written by meld itself (dispatch, tick, claim, and the PM summary), never through meld post")
    if verb in COMMAND_VERBS:
        raise PostError(f"{verb} is posted through `meld challenge <open|evidence|retract|uphold|dismiss> REF -m ...`, not meld post")
    party = Party("joint") if joint else actor.party
    if verb not in allowed_verbs(party):
        raise PostError(f"{verb} is not a verb {party.label} may post (allowed: {', '.join(sorted(allowed_verbs(party)))})")
    body_text = text
    if joint:
        if not ref:
            raise PostError("joint comments require a ref")
        agreement = agreement_for(board.comments, ref, ctx.cfg)
        if agreement.status in ("none", "open"):
            _refuse(ctx, board, verb, ref, f"agreement on {ref} is {agreement.status}; cannot post [joint]")
        if agreement.proposer != actor.group:
            _refuse(ctx, board, verb, ref, f"only the proposer ({agreement.proposer}) may post the joint comment for {ref}")
        if verb == "PLAN" and board.phase != "planning":
            _refuse(ctx, board, verb, ref, "PLAN may only be posted during planning")
        if verb == "PLAN-ADD" and board.phase != "executing":
            _refuse(ctx, board, verb, ref, "PLAN-ADD may only be posted during executing")
        if agreement.joint_verified:
            # Only a verified [joint] closes the door: a hand-written one (wrong author, or posted before the round
            # closed) must not stop the proposer from posting the real one, which then supersedes it (latest decides).
            _refuse(ctx, board, verb, ref, f"{ref} already has a [joint] comment")
        body_text = _joint_body(agreement, ref, qualifier, text)
    elif verb in AGREEMENT_VERBS and party.kind in ("group", "pm"):
        if not ref:
            raise PostError(f"{verb} requires a ref")
        agreement = agreement_for(board.comments, ref, ctx.cfg)
        if agreement.joint_posted:
            # The round is frozen once a [joint] comment exists (grammar.agreement_for ignores anything later);
            # refusing the post keeps the thread honest about it rather than letting a dead vote sit there.
            _refuse(ctx, board, verb, ref, f"{ref} is closed by a [joint] comment; open a new ref")
        if verb in ("PROPOSE", "DRAFT") and agreement.text is not None:
            _refuse(ctx, board, verb, ref, f"{ref} already has a proposal; post AMEND (or AGREE/DISSENT) instead")
        if verb == "AMEND" and not agreement.can_amend(ctx.cfg):
            _refuse(ctx, board, verb, ref, f"amend cap ({ctx.cfg.amend_cap}) reached for {ref}; post AGREE or DISSENT")
    header = Header(party, verb, ref, qualifier)
    errors = validate(header, body_text)
    if errors:
        raise PostError("; ".join(errors))
    body = compose(header, body_text)
    resp = ctx.gh.create_comment(board.coordination_issue, body)
    return {"comment_id": resp.get("id"), "url": resp.get("html_url"), "header": body.splitlines()[0]}
