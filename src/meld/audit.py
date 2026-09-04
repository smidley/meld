"""meld audit and meld reopen: post an auditor's verdict on a merged PR; reopen an issue after a failed audit."""
from __future__ import annotations

from datetime import datetime

from meld.actions import BLOCKED, IN_PROGRESS, READY, refuse
from meld.config import Party
from meld.dispatch import IN_REVIEW, DispatchError, _iso, _now, neutralise_closers
from meld.grammar import Header, compose
from meld.results import format_findings, load_result, mark_submitted
from meld.rules import Refusal, Verb, issue_refusal
from meld.status import build_status

AUDIT_VERDICTS = ("pass", "fail")


def audit(ctx, number: int, *, now: datetime | None = None) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    target = f"#{number}"
    if not actor.is_pm:
        refuse(ctx, board, Refusal(Verb.AUDIT, target, "only the PM may post an audit"))
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    pr = board.pulls.get(number)
    if pr is None:
        refuse(ctx, board, Refusal(Verb.AUDIT, target, "PR is not on a meld/ branch of the active repo"))
    stored = load_result(ctx.store.home, group.name, "audit", number)
    if stored is None:
        raise DispatchError(f"no collected audit result for {target}; run `meld collect {number}` first")
    when = _iso(_now(now))
    result = stored.get("result") or {}
    verdict = result.get("verdict")
    if verdict not in AUDIT_VERDICTS:
        reason = stored.get("reason") or (f"verdict {verdict!r} is not an audit verdict" if verdict else "the auditor wrote no verdict")
        ctx.gh.create_comment(number, f"Meld: `{group.name}` auditor produced no usable verdict: {reason}. Re-dispatch the audit.")
        mark_submitted(ctx.store.home, group.name, "audit", number, {"outcome": "no-verdict"}, when)
        return {"audited": number, "verdict": None, "reason": reason}
    comments = [c for c in result.get("comments") or [] if isinstance(c, dict) and {"path", "line", "body"} <= set(c)]
    comments = [{**c, "body": neutralise_closers(str(c["body"]))} for c in comments]
    text = (f"{neutralise_closers(result.get('summary', ''))}\n\n## Findings\n{format_findings(comments) or '- none'}\n\n"
            f"verified-by: {neutralise_closers(result.get('verified_by', ''))}")
    resp = ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "AUDIT", target, verdict.upper()), text))
    if verdict == "fail" and pr.issue_number is not None:
        ctx.gh.create_comment(pr.issue_number, f"Meld: `{group.name}` audited PR #{number} and found it does not meet its acceptance criteria; "
                                               f"see the AUDIT comment on the coordination issue. `meld reopen {pr.issue_number}` is now legal for {group.name}.")
    mark_submitted(ctx.store.home, group.name, "audit", number, {"outcome": "posted", "comment_id": resp.get("id"), "verdict": verdict}, when)
    return {"audited": number, "verdict": verdict, "issue": pr.issue_number, "comment_id": resp.get("id")}


def reopen(ctx, number: int) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    target = f"#{number}"
    issue = board.issues.get(number)
    if issue is None:
        refuse(ctx, board, Refusal(Verb.REOPEN, target, "issue is not in the active milestone"))
    refusal = issue_refusal(board, ctx.cfg, actor, Verb.REOPEN, issue, ctx.models)
    if refusal:
        refuse(ctx, board, refusal)
    refs = {target} | ({f"#{pr.number}"} if (pr := board.pr_for_issue(number)) else set())
    fails = [c for c in board.comments if c.ok and c.header is not None and c.header.verb == "AUDIT" and c.header.ref in refs
             and c.header.qualifier == "FAIL" and (actor.group is None or c.header.party.group == actor.group)]
    findings = fails[-1].text if fails else "(see the coordination issue)"
    ctx.gh.update_issue(number, state="open")
    if issue.assignees:
        ctx.gh.remove_assignees(number, list(issue.assignees))
    ctx.gh.add_labels(number, [READY])
    for label in (IN_REVIEW, IN_PROGRESS, BLOCKED):
        ctx.gh.remove_label(number, label)
    ctx.gh.create_comment(number, f"Meld: reopened by `{actor.name}` after a failed audit.\n\n{findings}")
    return {"reopened": number, "by": actor.name}
