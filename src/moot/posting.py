"""Post a coordination comment in the header grammar, including verified [joint] comments."""
from __future__ import annotations

from moot.config import Party
from moot.grammar import Header, agreement_for, allowed_verbs, compose, validate


class PostError(ValueError):
    pass


def _dissent_section(dissent: dict[str, str]) -> str:
    return "\n".join(f"- **{g}**: {t}" for g, t in sorted(dissent.items()))


def post(ctx, verb: str, ref: str | None = None, qualifier: str | None = None, text: str = "", joint: bool = False) -> dict:
    from moot.status import build_status

    _, board = build_status(ctx)
    actor = ctx.actor()
    if joint and actor.is_human:
        raise PostError("humans cannot post [joint] comments")
    party = Party("joint") if joint else actor.party
    if verb not in allowed_verbs(party):
        raise PostError(f"{verb} is not a verb {party.label} may post (allowed: {', '.join(sorted(allowed_verbs(party)))})")
    body_text = text
    if joint:
        if not ref:
            raise PostError("joint comments require a ref")
        agreement = agreement_for(board.comments, ref, ctx.cfg)
        if agreement.status in ("none", "open"):
            raise PostError(f"agreement on {ref} is {agreement.status}; cannot post [joint]")
        if agreement.proposer != actor.group:
            raise PostError(f"only the proposer ({agreement.proposer}) may post the joint comment for {ref}")
        if verb == "PLAN" and board.phase != "planning":
            raise PostError("PLAN may only be posted during planning")
        if verb == "PLAN-ADD" and board.phase != "executing":
            raise PostError("PLAN-ADD may only be posted during executing")
        base = text.strip() or (agreement.text or "")
        if agreement.status == "agreed":
            body_text = base
        elif agreement.status == "majority":
            body_text = f"{base}\n\n## DISSENT\n{_dissent_section(agreement.dissent)}"
        else:
            if qualifier != "DISAGREE":
                raise PostError(f"agreement on {ref} has no majority; post with qualifier DISAGREE")
            positions = f"- **{agreement.proposer}** (proposal): {agreement.text}\n{_dissent_section(agreement.dissent)}"
            body_text = f"## Positions\n{positions}\n\n{text.strip()}".strip()
    elif verb in ("PROPOSE", "DRAFT") and party.kind in ("group", "pm"):
        agreement = agreement_for(board.comments, ref, ctx.cfg)
        if agreement.text is not None:
            raise PostError(f"{ref} already has a proposal; post AMEND (or AGREE/DISSENT) instead")
    elif verb == "AMEND" and party.kind in ("group", "pm"):
        if not ref:
            raise PostError("AMEND requires a ref")
        agreement = agreement_for(board.comments, ref, ctx.cfg)
        if not agreement.can_amend(ctx.cfg):
            raise PostError(f"amend cap ({ctx.cfg.amend_cap}) reached for {ref}; post AGREE or DISSENT")
    header = Header(party, verb, ref, qualifier)
    errors = validate(header, body_text)
    if errors:
        raise PostError("; ".join(errors))
    body = compose(header, body_text)
    resp = ctx.gh.create_comment(board.coordination_issue, body)
    return {"comment_id": resp.get("id"), "url": resp.get("html_url"), "header": body.splitlines()[0]}
