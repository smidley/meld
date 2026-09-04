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
from meld.results import format_findings, load_result, mark_submitted, update_result
from meld.rules import Refusal, Verb, approving_parties, pr_refusal
from meld.status import build_status

EVENTS = {"approve": "APPROVE", "request-changes": "REQUEST_CHANGES", "push-fix": "COMMENT"}
VERBS = {"approve": Verb.APPROVE, "request-changes": Verb.REQUEST_CHANGES, "push-fix": Verb.PUSH_FIX}
# Spec 21.4 asked for a neutral check run; personal tokens cannot create check runs, so each group's review is a
# commit status instead: visible on the PR, never blocking unless a human lists it as a required check.
STATUS_STATE = {"approve": "success", "request-changes": "failure", "push-fix": "pending"}
# What an already-posted review looks like on GitHub, keyed by verdict: the state `submit_review` leaves on the
# head it targeted. Retrying after a failure downstream of a successful `create_review` must recognise its own
# earlier post instead of filing a second one (which would also inflate `changes_requested_total()`).
_POSTED_STATE = {"approve": "APPROVED", "request-changes": "CHANGES_REQUESTED"}


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
    pushed = stored.get("pushed")
    result = stored.get("result") or {}
    verdict = result.get("verdict")
    if verdict not in EVENTS:
        reason = stored.get("reason") or (f"verdict {verdict!r} is not a review verdict" if verdict else "the reviewer wrote no verdict")
        return _no_action(ctx, group, number, verdict, reason, "no-verdict", when,
                          f"Meld: `{group.name}` reviewer produced no usable verdict: {reason}. Re-dispatch a review.")
    # A push-fix that already landed moves the head itself; that is expected, not staleness.
    if stored.get("head_sha") and stored["head_sha"] != pr.head_sha and not (pushed and pushed == pr.head_sha):
        return _no_action(ctx, group, number, verdict, "stale head", "stale", when,
                          f"Meld: `{group.name}` review of `{stored['head_sha'][:7]}` is stale; the head is now `{pr.head_sha[:7]}`. Re-dispatch a review.",
                          {"current_head": pr.head_sha})
    # A resumed push-fix (the push already landed on this exact head) is not a new decision to re-validate:
    # legality, including the own-head check, was established before the irreversible push -- and by now
    # GitHub shows our own bot as the PR's last committer, which would make pr_refusal's own-head check
    # wrongly refuse resuming the report. Skip pr_refusal for a resume; the open-PR gate it would also
    # enforce still applies.
    resuming_push = verdict == "push-fix" and bool(pushed) and pushed == pr.head_sha
    if resuming_push:
        if pr.state != "open":
            refuse(ctx, board, Refusal(VERBS[verdict], target, "PR is not open"))
    else:
        refusal = pr_refusal(board, ctx.cfg, actor, VERBS[verdict], pr)
        if refusal:
            refuse(ctx, board, refusal)
    comments = [c for c in result.get("comments") or []
                if isinstance(c, dict) and {"path", "line", "body"} <= set(c)
                and (isinstance(c.get("line"), int) or str(c.get("line", "")).isdigit())]
    body = f"{neutralise_closers(result.get('summary', ''))}\n\nverified-by: {neutralise_closers(result.get('verified_by', ''))}"
    sha = pr.head_sha
    already_submitted = False
    if verdict == "push-fix":
        if pushed:
            # The push already happened (this call is resuming after a failure downstream of it): don't push
            # again, don't lose track of the head it produced, and don't re-post the review if it already landed.
            sha = pushed
            rec = board.run_for(group.name, number)
            wt = Path(rec.worktree) if rec else None
            if wt is not None and wt.is_dir():
                assert head_sha(wt) == pushed  # type: ignore[arg-type]
            body = (f"Already pushed to `{pushed[:7]}` on an earlier attempt; `{group.name}` is now the last committer "
                    f"and may not approve this head.\n\n{body}")
            already_submitted = any(r.author == group.bot and r.commit_id == pushed and r.state == "COMMENTED" for r in pr.reviews)
        else:
            rec = board.run_for(group.name, number)
            wt = Path(rec.worktree) if rec else None
            pushed_commits = commits_ahead(wt, f"origin/{pr.head_ref}") if wt is not None and wt.is_dir() else []
            if not pushed_commits:
                raise DispatchError(f"push-fix verdict on {target} but the reviewer made no commits in its worktree")
            push_branch(wt, pr.head_ref, ctx.gh.token)  # type: ignore[arg-type]
            sha = head_sha(wt)  # type: ignore[arg-type]
            # Record the push before anything else can fail: it is irreversible, and a retry must see it happened.
            update_result(ctx.store.home, group.name, "review", number, {"pushed": sha, "pushed_at": when})
            body = (f"Pushed {len(pushed_commits)} fix commit(s) ({pushed_commits[0][:7]}..{pushed_commits[-1][:7]}); `{group.name}` is now the last committer "
                    f"and may not approve this head.\n\n{body}")
    else:
        already_submitted = any(r.author == group.bot and r.commit_id == pr.head_sha and r.state == _POSTED_STATE[verdict] for r in pr.reviews)
    if already_submitted:
        resp: dict = {"id": None}
        outcome = "already-submitted"
    else:
        # commit_id is `sha`, not `pr.head_sha`: for approve/request-changes the two are always equal, but for a
        # push-fix `sha` is the head the push produced. Posting against anything else would make the idempotency
        # check above (which looks for that same `sha`/`pushed` on a retry) never recognise its own earlier post.
        resp = submit_review(ctx.gh, number, EVENTS[verdict], body, sha, comments)
        outcome = "submitted"
    description = f"{group.name} review: {verdict}" + (" (awaiting the author group's review of the new head)" if verdict == "push-fix" else "")
    status_error: str | None = None
    try:
        ctx.gh.create_status(sha, STATUS_STATE[verdict], status_context(group.name), description, resp.get("html_url"))
    except GitHubError as e:
        status_error = str(e)
    issue_status = None
    if verdict == "request-changes" and pr.issue_number is not None and pr.issue_number in board.issues:
        # already_submitted means our review is already counted in pr.changes_requested_total(); adding 1
        # again would double-count it on a retry and could push the issue to blocked one round too early.
        if pr.changes_requested_total() + (0 if already_submitted else 1) >= ctx.cfg.pingpong_cap:
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
                   {"outcome": outcome, "event": EVENTS[verdict], "review_id": resp.get("id"), "status_error": status_error,
                    "new_head": sha if verdict == "push-fix" else None}, when)
    return {"reviewed": number, "verdict": verdict, "event": EVENTS[verdict], "review_id": resp.get("id"), "issue": pr.issue_number,
            "issue_status": issue_status, "head": sha, "status_error": status_error}


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
