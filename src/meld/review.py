"""meld review and meld merge: a collected reviewer verdict becomes a GitHub review; a PR lands under the quorum rule."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from meld.actions import BLOCKED, IN_PROGRESS, refuse
from meld.board import AUTHOR_VERDICT_MARKER
from meld.config import Party
from meld.dispatch import IN_REVIEW, DispatchError, _iso, _now, _swap_labels, neutralise_closers
from meld.github import GitHubError
from meld.gitops import commits_ahead, head_sha, push_branch
from meld.grammar import Header, agreement_for, compose, verified_joint
from meld.memory import DECISIONS_DIR, LESSONS_FILE, LESSONS_HEADER, comment_url, content_hash, render_decision, superseded_trailer
from meld.results import cap_body, format_findings, load_result, mark_submitted, store_result, update_result
from meld.rules import VERB_FOR, Refusal, Verb, approving_parties, author_party, memory_review_rule, pr_refusal, review_verdict_rule, status_context
from meld.status import build_status

EVENTS = {"approve": "APPROVE", "request-changes": "REQUEST_CHANGES", "push-fix": "COMMENT"}
# Spec 21.4 asked for a neutral check run; a personal token cannot create one, so every group's review is also a
# commit status: visible on the PR, never blocking unless a human lists it as a required check. An App group
# (ctx.app_auth is not None) additionally gets the real thing -- a create_check_run with conclusion "neutral" --
# alongside the status. The status context itself (`meld/review/<group>`) is defined in rules.status_context,
# since the quorum reads only the status, never the check run.
STATUS_STATE = {"approve": "success", "request-changes": "failure", "push-fix": "pending"}
_AUTHOR_VERB = {"approve": "approving", "request-changes": "requesting changes on"}
_DATE_LINE_RE = re.compile(r"^- Date: (\d{4}-\d{2}-\d{2})$", re.MULTILINE)
# The decision-file form of a supersession link (memory.SUPERSEDES_RE is the lessons.md form, without the bullet).
# `(.+)` rather than `(\S+)`: the file being superseded may be hand-authored and its name may hold a space, and the
# captured name is checked twice anyway -- fed straight back into render_decision (so a line anywhere but the
# header fails the byte-equality check) and compared against the path the PR actually changes.
_SUPERSEDES_LINE_RE = re.compile(r"^- Supersedes: (.+)$", re.MULTILINE)


def _decision_date(text: str) -> str | None:
    """The decision file's `- Date:` value when it is a real YYYY-MM-DD date, else None. It is the one input the
    verifier takes from the file itself; everything else it re-renders from the board."""
    m = _DATE_LINE_RE.search(text)
    if m is None:
        return None
    try:
        datetime.strptime(m.group(1), "%Y-%m-%d")
    except ValueError:
        return None
    return m.group(1)


def submit_review(gh, number: int, event: str, body: str, commit_id: str, comments: list[dict]) -> dict:
    """Post the review with diff comments; when GitHub rejects a comment position (422), fold the findings into the body.
    Bodies are capped (results.cap_body) so a verbose reviewer cannot push the request past GitHub's size limit."""
    try:
        return gh.create_review(number, event, cap_body(body), commit_id, comments or None)
    except GitHubError as e:
        if e.status != 422 or not comments:
            raise
        return gh.create_review(number, event, cap_body(f"{body}\n\n## Findings\n{format_findings(comments)}"), commit_id, None)


def _no_action(ctx, group, number: int, verdict, reason: str, outcome: str, when: str, note: str, extra: dict | None = None) -> dict:
    ctx.gh.create_comment(number, note)
    mark_submitted(ctx.store.home, group.name, "review", number, {"outcome": outcome, "event": None, **(extra or {})}, when)
    return {"reviewed": number, "verdict": verdict, "event": None, "reason": reason}


def verify_memory_pr(ctx, board, pr) -> tuple[bool, str]:
    kind, ref, digest = pr.memory  # type: ignore[misc]
    paths = list(pr.files)
    if not 1 <= len(paths) <= 2:
        return False, f"a memory PR changes one file, or two for a supersession; this one changes {len(paths)}"
    if len(paths) == 2 and kind != "decision":
        return False, f"a {kind} PR changes exactly one file, this one changes 2"
    for p in paths:
        if ".." in p or p.startswith("/"):
            return False, f"{p} is not a valid memory-PR path"
    texts: dict[str, str] = {}
    for p in paths:
        at_head = ctx.gh.get_contents(p, pr.head_sha)
        if at_head is None:
            return False, f"{p} is missing at {pr.head_sha[:7]}"
        texts[p] = at_head
    main = paths[0]
    old_path: str | None = None
    if len(paths) == 2:
        # Which of the two is the new decision is decided by the marker digest, then by the `- Supersedes:` line:
        # GitHub lists a PR's changed files alphabetically, so the older `0001-...md` arrives ahead of the
        # `0002-...md` that supersedes it and `paths[0]` is exactly the wrong guess. When neither file answers to
        # either test (or both do, which two identical paths would), the PR is refused rather than guessed at.
        partner = {paths[0]: paths[1], paths[1]: paths[0]}
        named = [p for p in paths if (m := _SUPERSEDES_LINE_RE.search(texts[p])) and m.group(1) == Path(partner[p]).name]
        candidates = [p for p in paths if content_hash(texts[p]) == digest] or named
        if len(candidates) != 1:
            return False, ("a two-file memory PR is one new decision naming the file it supersedes, plus that file; "
                           f"neither {paths[0]} nor {paths[1]} matches the meld-memory marker")
        main = candidates[0]
        old_path = partner[main]
    text = texts[main]
    if kind == "decision":
        if not (main.startswith(f"{DECISIONS_DIR}/") and main.endswith(".md") and "/" not in main[len(DECISIONS_DIR) + 1:]):
            return False, f"a decision PR changes one file under {DECISIONS_DIR}, not {main}"
        if content_hash(text) != digest:
            return False, "file hash does not match the meld-memory marker"
        agreement = agreement_for(board.comments, ref, ctx.cfg)
        if not (agreement.joint_posted and agreement.joint_verified and agreement.joint_verb == "DECISION"):
            return False, f"no verified [joint] DECISION {ref}"
        if agreement.status not in ("agreed", "majority"):
            return False, f"{ref} ended in DISAGREE; the human decides"
        date = _decision_date(text)
        if date is None:
            return False, "the decision file's `- Date:` line is missing or is not a YYYY-MM-DD date"
        joint = verified_joint(board.comments, ref, ctx.cfg)
        if joint is None:   # unreachable once joint_verified held above; kept so the link can never come from an unverified comment
            return False, f"no verified [joint] DECISION {ref}"
        m = _SUPERSEDES_LINE_RE.search(text)
        supersedes = m.group(1) if m else None
        # Byte equality with `meld decide`'s own rendering: the header lines (date, agreeing groups, the verified
        # joint comment's URL, the superseded file when there is one) and the text under `## Decision` are all
        # checked at once, and nothing else -- no preamble, note, or trailing addition -- can ride into the repo
        # (and into every brief's `## Relevant memory`). Feeding `supersedes` back in is what pins that line's
        # position, immediately after `- Source:`.
        if text != render_decision(ref, agreement, comment_url(board, joint.id), date, supersedes):
            return False, "the decision file does not match meld decide's rendering of the agreed [joint] DECISION byte for byte"
        if supersedes is None:
            if old_path is not None:
                return False, f"{old_path} also changed, but {Path(main).name} carries no `- Supersedes:` line"
        elif old_path is None:
            return False, f"{Path(main).name} supersedes {supersedes}, which this PR does not change"
        elif old_path != f"{DECISIONS_DIR}/{supersedes}":
            return False, f"{Path(main).name} supersedes {supersedes}, but this PR also changes {old_path}"
        else:
            # Superseded, never edited: the old file at the head must be origin's byte for byte plus that one
            # trailer, so a supersession can neither rewrite the decision it replaces nor point it somewhere else.
            at_base = ctx.gh.get_contents(old_path, ctx.cfg.default_branch)
            if at_base is None or texts[old_path] != at_base + superseded_trailer(Path(main).name, date):
                return False, (f"{old_path} must be the {ctx.cfg.default_branch} file plus exactly the superseded "
                               f"trailer naming {Path(main).name}")
        return True, "hash and agreed text verified"
    if main != LESSONS_FILE:
        return False, f"a lesson PR changes {LESSONS_FILE}, not {main}"
    last = text.rfind("\n## ")
    entry = text[last:] if last >= 0 else ""
    if content_hash(entry) != digest:
        return False, "the appended lesson entry's hash does not match the meld-memory marker"
    # Append-only by verification: the head must be exactly the default branch's file plus that one entry, so a
    # lesson PR can neither rewrite or drop an earlier lesson nor smuggle a second entry in under one marker.
    base = ctx.gh.get_contents(LESSONS_FILE, ctx.cfg.default_branch)
    if text != (LESSONS_HEADER if base is None else base) + entry:
        return False, (f"{LESSONS_FILE} at the head is not the {ctx.cfg.default_branch} file plus the one appended entry "
                       "(an earlier lesson was changed or dropped, or the branch needs a rebase)")
    n = int(ref[1:]) if ref.startswith("#") and ref[1:].isdigit() else None
    issue = board.issues.get(n) if n is not None else None
    if issue is None and (n is None or n not in board.pulls):
        return False, f"{ref} is not on the board"
    if issue is not None and issue.status in ("blocked", "needs_human"):
        return False, f"{ref} is still {issue.status}"
    if board.is_challenged(ref):
        return False, f"{ref} has an open CHALLENGE"
    return True, "hash and resolution verified"


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
    if pr.memory:
        # A memory PR has no dispatched worker and so no "collect". Only a completed APPROVE at the PR's CURRENT
        # head is a settled, one-time outcome (return it immediately: no re-verify, no re-post); a REQUEST_CHANGES
        # is provisional -- re-running `meld review N` must always re-verify, so a corrected file is reflected on
        # the next call -- and even a cached APPROVE must be re-checked once the head moves (a human pushed a new
        # commit), so the short-circuit below is keyed on (head_sha, event), never on review_id alone.
        cached = load_result(ctx.store.home, group.name, "review", number)
        if cached is not None and cached.get("head_sha") == pr.head_sha and cached.get("event") == "APPROVE":
            out = {"reviewed": number, "memory": pr.memory[0], "event": "APPROVE", "review_id": cached.get("review_id")}
            if cached.get("status_error"):
                # The review already landed (on this exact head); only the best-effort status post failed last
                # time. Retry just that -- never re-verify or re-post the review itself for an already-settled APPROVE.
                try:
                    ctx.gh.create_status(pr.head_sha, "success", status_context(group.name), f"{group.name} memory check: ok"[:140])
                except GitHubError as e:
                    out["status_error"] = str(e)
                    update_result(ctx.store.home, group.name, "review", number, {"status_error": str(e)})
                else:
                    update_result(ctx.store.home, group.name, "review", number, {"status_error": None})
                    out["status_retried"] = True
            return out
        rule = memory_review_rule(board, ctx.cfg, actor, pr)
        if rule:
            refuse(ctx, board, Refusal(Verb.APPROVE, target, rule))
        ok, reason = verify_memory_pr(ctx, board, pr)
        event = "APPROVE" if ok else "REQUEST_CHANGES"
        # A re-verify that reproduces exactly what is already on record (same head, same verdict, same reason)
        # has nothing new to post -- this is what keeps an unchanged tampered PR from getting a second
        # REQUEST_CHANGES review every time someone re-runs `meld review N` against it.
        if cached is not None and cached.get("head_sha") == pr.head_sha and cached.get("event") == event and cached.get("reason") == reason:
            out = {"reviewed": number, "memory": pr.memory[0], "event": event, "review_id": cached.get("review_id")}
            if not ok:
                out["reason"] = reason
            return out
        body = (f"Memory PR check by {group.name}: {reason}.\n\nverified-by: meld review {number} (file at {pr.head_sha[:7]} fetched and hashed)")
        resp = ctx.gh.create_review(number, event, body, pr.head_sha)
        # Record the post before anything else can fail (create_status below is best-effort, like the ordinary
        # path's own create_status), so a retry never files a second review no matter what happens next.
        record = {"review_id": resp.get("id"), "head_sha": pr.head_sha, "event": event, "reason": reason}
        if cached is None:
            store_result(ctx.store.home, group.name, "review", number, record, _iso(_now(now)))
        else:
            update_result(ctx.store.home, group.name, "review", number, record)
        status_error: str | None = None
        try:
            ctx.gh.create_status(pr.head_sha, "success" if ok else "failure", status_context(group.name), f"{group.name} memory check: {'ok' if ok else reason}"[:140], resp.get("html_url"))
        except GitHubError as e:
            status_error = str(e)
            update_result(ctx.store.home, group.name, "review", number, {"status_error": status_error})
        check_run_error: str | None = None
        if ctx.app_auth is not None:
            memory_check_description = f"{group.name} memory check: {'ok' if ok else reason}"
            try:
                ctx.gh.create_check_run(status_context(group.name), pr.head_sha, "neutral", memory_check_description, memory_check_description)
            except GitHubError as e:
                check_run_error = str(e)
        out = {"reviewed": number, "memory": pr.memory[0], "event": event, "review_id": resp.get("id")}
        if not ok:
            out["reason"] = reason
        if status_error is not None:
            out["status_error"] = status_error
        if check_run_error is not None:
            out["check_run_error"] = check_run_error
        return out
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
    # GitHub shows our own bot as the PR's last committer, which would make the own-head check wrongly
    # refuse resuming the report. Skip the rule for a resume; the open-PR gate it would also enforce still applies.
    resuming_push = verdict == "push-fix" and bool(pushed) and pushed == pr.head_sha
    if resuming_push:
        if pr.state != "open":
            refuse(ctx, board, Refusal(VERB_FOR[verdict], target, "PR is not open"))
    else:
        rule = review_verdict_rule(board, ctx.cfg, actor, pr, verdict)
        if rule:
            refuse(ctx, board, Refusal(VERB_FOR[verdict], target, rule))
    # The author group's approve or request-changes is legal (review_verdict_rule) but cannot be a GitHub APPROVE or
    # REQUEST_CHANGES: GitHub refuses those from the account that opened the PR. It goes out as a COMMENT review whose
    # first line names the verdict, and the `meld/review/<group>` status carries it into rules.approving_parties.
    as_author = verdict != "push-fix" and actor.name == author_party(ctx.cfg, pr)
    event = "COMMENT" if as_author else EVENTS[verdict]
    comments = [c for c in result.get("comments") or []
                if isinstance(c, dict) and {"path", "line", "body"} <= set(c)
                and (isinstance(c.get("line"), int) or str(c.get("line", "")).isdigit())]
    comments = [{**c, "body": neutralise_closers(str(c["body"]))} for c in comments]
    body = f"{neutralise_closers(result.get('summary', ''))}\n\nverified-by: {neutralise_closers(result.get('verified_by', ''))}"
    if as_author:
        body = (f"{AUTHOR_VERDICT_MARKER} {verdict}\n\nVerdict: {verdict} — posted as a comment review because GitHub forbids a PR author's account from "
                f"{_AUTHOR_VERB[verdict]} its own PR; the `{status_context(group.name)}` commit status carries this verdict.\n\n{body}")
    sha = pr.head_sha
    # Idempotency is keyed on our own record, not on what GitHub shows: `review_id` is written into the verdict file
    # the moment the review lands, so a retry after a later failure skips the post, while a second verdict on the same
    # head from another worker (the `security` review after the `reviewer`) is a new file and posts its own review.
    already_submitted = stored.get("review_id") is not None
    if verdict == "push-fix":
        rec = board.run_for(group.name, number)
        wt = Path(rec.worktree) if rec else None
        if pushed:
            # The push already happened (this call is resuming after a failure downstream of it): don't push
            # again, don't lose track of the head it produced, and don't re-post the review if it already landed.
            sha = pushed
            if wt is not None and wt.is_dir():
                actual = head_sha(wt)
                if actual != pushed:
                    raise DispatchError(f"review worktree {wt} is at {actual[:7]} but the push recorded for {target} was {pushed[:7]}; "
                                        "the worktree moved after the push -- inspect it, then re-dispatch the review")
            body = (f"Already pushed to `{pushed[:7]}` on an earlier attempt; `{group.name}` is now the last committer "
                    f"and may not approve this head.\n\n{body}")
        else:
            pushed_commits = commits_ahead(wt, f"origin/{pr.head_ref}") if wt is not None and wt.is_dir() else []
            if not pushed_commits:
                # Nothing to push: the reviewer wrote push-fix without committing, or the worktree is gone (reaped).
                # Like a missing verdict, this is a no-action outcome rather than an error, so the verdict file is
                # consumed and the PM is told to re-dispatch.
                return _no_action(ctx, group, number, verdict, "no commits", "no-commits", when,
                                  f"Meld: `{group.name}` reviewer returned a push-fix verdict but no commits were found in the review "
                                  "worktree; re-dispatch a review.")
            push_branch(wt, pr.head_ref, ctx.gh.token)  # type: ignore[arg-type]
            sha = head_sha(wt)  # type: ignore[arg-type]
            # Record the push before anything else can fail: it is irreversible, and a retry must see it happened.
            update_result(ctx.store.home, group.name, "review", number, {"pushed": sha, "pushed_at": when})
            body = (f"Pushed {len(pushed_commits)} fix commit(s) ({pushed_commits[0][:7]}..{pushed_commits[-1][:7]}); `{group.name}` is now the last committer "
                    f"and may not approve this head.\n\n{body}")
    if already_submitted:
        resp: dict = {"id": stored["review_id"], "html_url": stored.get("review_url")}
        outcome = "already-submitted"
    else:
        # commit_id is `sha`, not `pr.head_sha`: for approve/request-changes the two are always equal, but for a
        # push-fix `sha` is the head the push produced, which is where the review and the status belong.
        resp = submit_review(ctx.gh, number, event, body, sha, comments)
        # Record the post before anything else can fail, so a retry never files a second review (which would also
        # inflate pr.changes_requested_total() for a request-changes).
        update_result(ctx.store.home, group.name, "review", number,
                      {"review_id": resp.get("id"), "review_url": resp.get("html_url"), "review_event": event})
        outcome = "submitted"
    description = f"{group.name} review: {verdict}" + (" (awaiting the author group's review of the new head)" if verdict == "push-fix" else "")
    status_error: str | None = None
    try:
        ctx.gh.create_status(sha, STATUS_STATE[verdict], status_context(group.name), description, resp.get("html_url"))
    except GitHubError as e:
        status_error = str(e)
    check_run_error: str | None = None
    if ctx.app_auth is not None:
        try:
            ctx.gh.create_check_run(status_context(group.name), sha, "neutral", description, body[:60000])
        except GitHubError as e:
            check_run_error = str(e)
    issue_status = None
    if verdict == "request-changes" and pr.issue_number is not None and pr.issue_number in board.issues:
        # A review that already went out as REQUEST_CHANGES is in pr.changes_requested_total(); adding 1 again would
        # double-count it on a retry and could push the issue to blocked one round too early. The author group's
        # COMMENT review (its body opens with AUTHOR_VERDICT_MARKER) is promoted to a CHANGES_REQUESTED Review by
        # assemble._pull, so it is already counted in that total too -- no as_author special case is needed here
        # any more; every path gets the same "count it unless this call is a retry of an already-posted review" rule.
        extra = 0 if already_submitted else 1
        if pr.changes_requested_total() + extra >= ctx.cfg.pingpong_cap:
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
                   {"outcome": outcome, "event": event, "review_id": resp.get("id"), "status_error": status_error,
                    "new_head": sha if verdict == "push-fix" else None, "as_author": as_author}, when)
    out = {"reviewed": number, "verdict": verdict, "event": event, "review_id": resp.get("id"), "issue": pr.issue_number,
           "issue_status": issue_status, "head": sha, "status_error": status_error}
    if check_run_error is not None:
        out["check_run_error"] = check_run_error
    if as_author:
        out["as_author"] = True
    return out


def merge(ctx, number: int) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    target = f"#{number}"
    pr = board.pulls.get(number)
    if pr is None:
        refuse(ctx, board, Refusal(Verb.MERGE, target, "PR is not on a meld/ branch of the active repo"))
    if pr.merged:
        # Already landed (another PM won the race, or this is a retry after our own merge call failed to report): a
        # no-op success rather than a refusal, so a PM that sees `merge` in a stale status does not post a violation.
        return {"merged": number, "sha": pr.merge_commit_sha, "issue": pr.issue_number, "approvals": [], "already_merged": True}
    refusal = pr_refusal(board, ctx.cfg, actor, Verb.MERGE, pr)
    if refusal:
        refuse(ctx, board, refusal)
    # The sha guard makes GitHub refuse (409) if the head moved since this board; the PR body (already
    # closer-neutralised by collect, except the framework's own `Closes #N` line) becomes the squash message, so a
    # worker's commit messages never reach the default branch (spec 21 item 5).
    resp = ctx.gh.merge_pull(number, pr.head_sha, pr.title, message=pr.body)
    return {"merged": number, "sha": resp.get("sha"), "issue": pr.issue_number, "approvals": sorted(approving_parties(ctx.cfg, pr))}
