"""meld challenge: open a CHALLENGE on a claim, answer it with EVIDENCE, or close it (RETRACT, UPHELD, DISMISSED)."""
from __future__ import annotations

from meld.actions import refuse
from meld.board import CHALLENGED_LABEL
from meld.grammar import Header, compose, validate
from meld.posting import PostError
from meld.rules import CHALLENGE_ACTIONS, Refusal, Verb, challenge_rule, challenged_party
from meld.status import build_status

ACTION_VERB = {"open": "CHALLENGE", "evidence": "EVIDENCE", "retract": "RETRACT", "uphold": "UPHELD", "dismiss": "DISMISSED"}
CLOSERS = ("retract", "uphold", "dismiss")


def _number(ref: str) -> int | None:
    return int(ref[1:]) if ref.startswith("#") and ref[1:].isdigit() else None


def challenge(ctx, action: str, ref: str, text: str) -> dict:
    if action not in CHALLENGE_ACTIONS:
        raise PostError(f"action must be one of {', '.join(CHALLENGE_ACTIONS)}")
    if not text.strip():
        raise PostError("say what is doubted, shown, or decided in -m")
    _, board = build_status(ctx)
    actor = ctx.actor()
    header = Header(actor.party, ACTION_VERB[action], ref, None)
    errors = validate(header, text)          # EVIDENCE needs a verified-by: line; every challenge verb needs its ref
    if errors:
        raise PostError("; ".join(errors))
    rule = challenge_rule(board, ctx.cfg, actor, action, ref)
    if rule:
        refuse(ctx, board, Refusal(Verb.CHALLENGE, ref, rule))
    resp = ctx.gh.create_comment(board.coordination_issue, compose(header, text))
    n = _number(ref)
    label = None
    # `open` only labels a number still on the board (spec 14): a challenge on a stale/unknown
    # number labels nothing. A closer must clear the label unconditionally, even when the
    # challenged PR closed unmerged and dropped off the board (assemble filters out closed-unmerged
    # PRs) -- GitHub.remove_label already tolerates a 404, so removing is safe whether or not the
    # label, or the PR itself, still exists.
    if action == "open":
        if n is not None and (n in board.issues or n in board.pulls):
            ctx.gh.add_labels(n, [CHALLENGED_LABEL])
            label = "added"
    elif action in CLOSERS and n is not None:
        ctx.gh.remove_label(n, CHALLENGED_LABEL)
        label = "removed"
    return {"challenge": action, "ref": ref, "verb": ACTION_VERB[action], "comment_id": resp.get("id"), "label": label,
            "challenged_party": challenged_party(board, ctx.cfg, ref)}
