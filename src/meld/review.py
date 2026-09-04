"""meld review and meld merge: a collected reviewer verdict becomes a GitHub review; a PR lands under the quorum rule."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from meld.actions import BLOCKED, IN_PROGRESS, refuse
from meld.config import Party
from meld.dispatch import IN_REVIEW, DispatchError, _iso, _now, _swap_labels, neutralise_closers
from meld.github import GitHubError
from meld.gitops import commits_ahead, head_sha, push_branch
from meld.grammar import Header, compose
from meld.results import format_findings, load_result, mark_submitted
from meld.rules import Refusal, Verb, approving_parties, pr_refusal
from meld.status import build_status

EVENTS = {"approve": "APPROVE", "request-changes": "REQUEST_CHANGES", "push-fix": "COMMENT"}
VERBS = {"approve": Verb.APPROVE, "request-changes": Verb.REQUEST_CHANGES, "push-fix": Verb.PUSH_FIX}
# Spec 21.4 asked for a neutral check run; personal tokens cannot create check runs, so each group's review is a
# commit status instead: visible on the PR, never blocking unless a human lists it as a required check.
STATUS_STATE = {"approve": "success", "request-changes": "failure", "push-fix": "pending"}


def status_context(group: str) -> str:
    return f"meld/review/{group}"


def submit_review(gh, number: int, event: str, body: str, commit_id: str, comments: list[dict]) -> dict:
    """Post the review with diff comments; when GitHub rejects a comment position (422), fold the findings into the body."""
    try:
        return gh.create_review(number, event, body, commit_id, comments or None)
    except GitHubError as e:
        if e.status != 422 or not comments:
            raise
        return gh.create_review(number, event, f"{body}\n\n## Findings\n{format_findings(comments)}", commit_id, None)


def _no_action(ctx, group, number: int, verdict, reason: str, outcome: str, when: str, note: str, extra: dict | None = None) -> dict:
    ctx.gh.create_comment(number, note)
    mark_submitted(ctx.store.home, group.name, "review", number, {"outcome": outcome, "event": None, **(extra or {})}, when)
    return {"reviewed": number, "verdict": verdict, "event": None, "reason": reason}


def review(ctx, number: int, *, now: datetime | None = None) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    target = f"#{number}"
    if not actor.is_pm:
        refuse(ctx, board, Refusal(Verb.REVIEW, target, "only the PM may submit a review"))
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    pr = board.pulls.get(number)
    if pr is None:
        refuse(ctx, board, Refusal(Verb.REVIEW, target, "PR is not on a meld/ branch of the active repo"))
    stored = load_result(ctx.store.home, group.name, "review", number)
    if stored is None:
        raise DispatchError(f"no collected review result for {target}; run `meld collect {number}` first")
    when = _iso(_now(now))
    result = stored.get("result") or {}
    verdict = result.get("verdict")
    if verdict not in EVENTS:
        reason = stored.get("reason") or (f"verdict {verdict!r} is not a review verdict" if verdict else "the reviewer wrote no verdict")
        return _no_action(ctx, group, number, verdict, reason, "no-verdict", when,
                          f"Meld: `{group.name}` reviewer produced no usable verdict: {reason}. Re-dispatch a review.")
    if stored.get("head_sha") and stored["head_sha"] != pr.head_sha:
        return _no_action(ctx, group, number, verdict, "stale head", "stale", when,
                          f"Meld: `{group.name}` review of `{stored['head_sha'][:7]}` is stale; the head is now `{pr.head_sha[:7]}`. Re-dispatch a review.",
                          {"current_head": pr.head_sha})
    refusal = pr_refusal(board, ctx.cfg, actor, VERBS[verdict], pr)
    if refusal:
        refuse(ctx, board, refusal)
    comments = [c for c in result.get("comments") or [] if isinstance(c, dict) and {"path", "line", "body"} <= set(c)]
    body = f"{neutralise_closers(result.get('summary', ''))}\n\nverified-by: {neutralise_closers(result.get('verified_by', ''))}"
    sha = pr.head_sha
    if verdict == "push-fix":
        rec = board.run_for(group.name, number)
        wt = Path(rec.worktree) if rec else None
        pushed = commits_ahead(wt, f"origin/{pr.head_ref}") if wt is not None and wt.is_dir() else []
        if not pushed:
            raise DispatchError(f"push-fix verdict on {target} but the reviewer made no commits in its worktree")
        push_branch(wt, pr.head_ref, ctx.gh.token)  # type: ignore[arg-type]
        sha = head_sha(wt)  # type: ignore[arg-type]
        body = (f"Pushed {len(pushed)} fix commit(s) ({pushed[0][:7]}..{pushed[-1][:7]}); `{group.name}` is now the last committer "
                f"and may not approve this head.\n\n{body}")
    resp = submit_review(ctx.gh, number, EVENTS[verdict], body, pr.head_sha, comments)
    description = f"{group.name} review: {verdict}" + (" (awaiting the author group's review of the new head)" if verdict == "push-fix" else "")
    ctx.gh.create_status(sha, STATUS_STATE[verdict], status_context(group.name), description, resp.get("html_url"))
    issue_status = None
    if verdict == "request-changes" and pr.issue_number is not None and pr.issue_number in board.issues:
        if pr.changes_requested_total() + 1 >= ctx.cfg.pingpong_cap:
            _swap_labels(ctx, pr.issue_number, BLOCKED, (IN_REVIEW, IN_PROGRESS))
            ctx.gh.create_comment(pr.issue_number, f"Meld: review ping-pong cap ({ctx.cfg.pingpong_cap}) reached on PR #{number}; "
                                                    "the orchestrators must post a resolution before work resumes.")
            issue_status = "blocked"
        else:
            _swap_labels(ctx, pr.issue_number, IN_PROGRESS, (IN_REVIEW,))
            issue_status = "in_progress"
    if verdict == "push-fix":
        ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "REVIEW-REQUEST", target, None),
                              f"Head moved to {sha[:7]} by a push-fix from {group.name}; the author group must review the new head."))
    mark_submitted(ctx.store.home, group.name, "review", number,
                   {"outcome": "submitted", "event": EVENTS[verdict], "review_id": resp.get("id"), "new_head": sha if verdict == "push-fix" else None}, when)
    return {"reviewed": number, "verdict": verdict, "event": EVENTS[verdict], "review_id": resp.get("id"), "issue": pr.issue_number,
            "issue_status": issue_status, "head": sha}


def merge(ctx, number: int) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    target = f"#{number}"
    pr = board.pulls.get(number)
    if pr is None:
        refuse(ctx, board, Refusal(Verb.MERGE, target, "PR is not on a meld/ branch of the active repo"))
    refusal = pr_refusal(board, ctx.cfg, actor, Verb.MERGE, pr)
    if refusal:
        refuse(ctx, board, refusal)
    resp = ctx.gh.merge_pull(number, pr.head_sha, pr.title)   # the sha guard makes GitHub refuse (409) if the head moved since this board
    return {"merged": number, "sha": resp.get("sha"), "issue": pr.issue_number, "approvals": sorted(approving_parties(ctx.cfg, pr))}
