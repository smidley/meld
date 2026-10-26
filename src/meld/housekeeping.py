"""PM housekeeping run from `meld tick`: red CI on our PRs, comments from strangers, and label or assignee changes made outside meld."""
from __future__ import annotations

import json
from pathlib import Path

from meld.actions import IN_PROGRESS
from meld.atomic import write_atomic
from meld.board import Board
from meld.config import GroupConfig, Party
from meld.github import GitHubError
from meld.grammar import Header, compose
from meld.rules import party_of_login

CI_RED_STATE = "ci-red.json"
HUMAN_HEAD_STATE = "human-head.json"
IGNORED_STATE = "ignored.json"
OFF_PROTOCOL_STATE = "off-protocol.json"
_EVENTS = ("labeled", "unlabeled", "assigned", "unassigned")


def _load(path: Path) -> dict:
    """A missing, corrupt, unreadable, or non-dict state file is treated as empty rather than raising."""
    try:
        value = json.loads(path.read_text()) if path.is_file() else {}
    except (json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _int(value) -> int:
    """A persisted cursor as an int; anything that is not one (hand-edited, or written by another version) reads as 0, so
    the sweep re-processes from the start rather than raising out of tick."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _save(path: Path, data: dict) -> None:
    """Write the state file. May raise OSError; callers contain that so a write problem never aborts a sweep."""
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(data, indent=2))


def _first_group(cfg) -> str:
    return min(cfg.group_names)


def _safe(text: str) -> str:
    """Backtick-quote text sourced from GitHub (a label name, a login) before it reaches a coordination comment
    body, and defuse a leading '@' so it can never read as a bare, auto-linkable mention."""
    if text.startswith("@"):
        text = "@​" + text[1:]   # zero-width space breaks mention auto-linking without changing how it reads
    return f"`{text}`"


def _normalize_head_record(value) -> dict:
    """Read one PR's per-head state entry. An older `pr -> head` string entry is treated as fully complete (both
    sub-steps done for that head); anything that is not a dict with a string head reads as no record."""
    if isinstance(value, str):
        return {"head": value, "commented": True, "swapped": True}
    if isinstance(value, dict) and isinstance(value.get("head"), str):
        return {"head": value["head"], "commented": bool(value.get("commented")), "swapped": bool(value.get("swapped"))}
    return {"head": None, "commented": False, "swapped": False}


def _head_sweep(ctx, board: Board, *, state_file: str, wanted, reprocess_when: str, post, add: str, remove: tuple[str, ...]) -> list[dict]:
    """Once per head, over our open PRs: for each one `wanted(pr)` selects, `post(pr)` the comment that is the durable
    evidence, then swap the linked issue's labels (`add`, `remove`).

    The comment is posted BEFORE the label swap, deliberately: a comment failure never changes the issue's visible
    status, so a fresh board on the next tick still shows the status `reprocess_when` names and the sweep fires
    again. Each PR's progress is tracked as `{"head", "commented", "swapped"}` in `state_file`, persisted the moment
    a sub-step succeeds, so a `GitHubError` (including `RateLimited`) on either sub-step is recorded on the returned
    item rather than raised, leaving exactly the missing sub-step(s) for the next tick -- without ever re-posting the
    comment or re-running a sub-step that already succeeded. A PR is processed when its issue's status is
    `reprocess_when` and its recorded head differs (or there is no complete record for the current head), OR when
    there is an incomplete record for the current exact head regardless of the issue's status (the swap may already
    have moved it on). Records for PRs no longer open are pruned."""
    from meld.dispatch import _swap_labels  # local import: dispatch imports this module
    path = ctx.store.state_dir / state_file
    raw = _load(path)
    out = []
    for pr in sorted(board.pulls.values(), key=lambda p: p.number):
        if pr.state != "open" or pr.issue_number is None or not wanted(pr):
            continue
        issue = board.issues.get(pr.issue_number)
        if issue is None:
            continue
        key = str(pr.number)
        record = _normalize_head_record(raw.get(key))
        same_head = record["head"] == pr.head_sha
        complete = same_head and record["commented"] and record["swapped"]
        should_process = (not complete) if same_head else (issue.status == reprocess_when)
        if not should_process:
            continue
        if not same_head:
            record = {"head": pr.head_sha, "commented": False, "swapped": False}
        error = None
        if not record["commented"]:
            try:
                post(pr)
            except GitHubError as e:
                error = type(e).__name__
            else:
                record["commented"] = True
                raw[key] = record
                try:
                    _save(path, raw)
                except OSError:
                    pass
        if error is None and record["commented"] and not record["swapped"]:
            try:
                _swap_labels(ctx, pr.issue_number, add, remove)
            except GitHubError as e:
                error = type(e).__name__
            else:
                record["swapped"] = True
                raw[key] = record
                try:
                    _save(path, raw)
                except OSError:
                    pass
        item = {"pr": pr.number, "issue": pr.issue_number, "head": pr.head_sha}
        if error is not None:
            item["error"] = error
            item["commented"] = record["commented"]
            item["swapped"] = record["swapped"]
        out.append(item)
    open_prs = {str(n) for n, p in board.pulls.items() if p.state == "open"}
    stale = [k for k in raw if k not in open_prs]
    if stale:
        for k in stale:
            raw.pop(k, None)
            out.append({"pr": int(k) if k.isdigit() else k, "pruned": True})
        try:
            _save(path, raw)
        except OSError:
            pass
    return out


def ci_red_sweep(ctx, board: Board, group: GroupConfig) -> list[dict]:
    """A red check on one of our open PRs is a request for changes: the linked issue goes back to in-progress once
    per head, and the comment naming the PR is posted once per head as the durable evidence of it (_head_sweep)."""
    from meld.dispatch import IN_REVIEW  # local import: dispatch imports this module
    return _head_sweep(
        ctx, board, state_file=CI_RED_STATE,
        wanted=lambda pr: pr.checks == "red" and party_of_login(ctx.cfg, pr.author) == group.name,
        reprocess_when="in_review",
        post=lambda pr: ctx.gh.create_comment(pr.issue_number, f"Meld: CI is red on PR #{pr.number} (head `{pr.head_sha[:7]}`); treated as request-changes. "
                                                               f"The next build run on this issue fixes the checks; the PR moves back to review when they pass."),
        add=IN_PROGRESS, remove=(IN_REVIEW,))


def human_head_sweep(ctx, board: Board, group: GroupConfig) -> list[dict]:
    """A listed human's push to one of our open PRs is a head nobody collected: the linked issue goes to in-review and
    a `REVIEW-REQUEST` names the head, once per head, so reviewers look at what the person pushed instead of the PM
    dispatching a retry worker on top of it (handoff residual 10). A head committed by a bot is `collect`'s or
    `review`'s business, and an issue with a live build run is left to that run's `collect`."""
    from meld.dispatch import IN_REVIEW  # local import: dispatch imports this module
    from meld.rules import committer_party

    def wanted(pr) -> bool:
        if party_of_login(ctx.cfg, pr.author) != group.name:
            return False
        who = committer_party(ctx.cfg, pr)
        if who is None or not who.startswith("human:"):
            return False
        rec = board.run_for(group.name, pr.issue_number)
        return rec is None or not rec.alive

    def post(pr) -> None:
        who = (committer_party(ctx.cfg, pr) or "human:").partition(":")[2]
        ctx.gh.create_comment(board.coordination_issue,
                              compose(Header(Party("pm", group.name), "REVIEW-REQUEST", f"#{pr.number}", None),
                                      f"Issue #{pr.issue_number}, head {pr.head_sha[:7]} pushed by `{who}` (human). Please dispatch a reviewer."))

    return _head_sweep(ctx, board, state_file=HUMAN_HEAD_STATE, wanted=wanted, reprocess_when="in_progress", post=post,
                       add=IN_REVIEW, remove=(IN_PROGRESS,))


def flag_ignored_comments(ctx, board: Board, group: GroupConfig) -> dict:
    """Comments from accounts outside the configured bots and humans are ignored by the parser and flagged once
    (spec 7). The posting group's cursor advances only once its post succeeds (so a failed post is retried, never
    lost or duplicated); a group that isn't posting has nothing that can fail and always advances."""
    path = ctx.store.state_dir / IGNORED_STATE
    state = _load(path)
    cursor = _int(state.get("last_id", 0))
    new = sorted((c for c in board.comments if not ctx.cfg.is_party(c.author) and c.id > cursor), key=lambda c: c.id)
    if not new:
        return {"flagged": [], "posted": False}
    posted = False
    advance = True
    errors: list[str] = []
    if group.name == _first_group(ctx.cfg):
        body = "\n".join(f"- comment {c.id} by `{c.author}`" for c in new)
        try:
            ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "IGNORED-COMMENT", None, None),
                                                                    f"Comments from accounts that are neither a configured bot nor a listed human were ignored:\n{body}"))
        except GitHubError as e:
            advance = False
            errors.append(type(e).__name__)
        else:
            posted = True
    if advance:
        try:
            _save(path, {"last_id": new[-1].id})
        except OSError:
            pass
    result = {"flagged": [c.id for c in new], "posted": posted}
    if errors:
        result["errors"] = errors
    return result


def off_protocol_sweep(ctx, board: Board, group: GroupConfig) -> list[dict]:
    """Label or assignee changes on milestone issues by an actor that is neither a bot nor a listed human (spec
    17). Each issue's cursor is persisted as soon as that issue is done, and advances only up to (not past) an
    event whose post failed -- a failed post on one issue is recorded, does not stop the sweep over the rest of
    the milestone, and is retried next time without duplicating anything that already posted."""
    path = ctx.store.state_dir / OFF_PROTOCOL_STATE
    cursors = _load(path)
    out = []
    for n, issue in sorted(board.issues.items()):
        if issue.state != "open":
            continue
        last = _int(cursors.get(str(n), 0))
        try:
            events = ctx.gh.issue_events(n)
        except GitHubError:
            # One flaky/unreachable issue must not abort housekeeping for the rest of the milestone.
            continue
        newest = last
        for e in sorted(events, key=lambda e: int(e.get("id", 0))):
            eid = int(e.get("id", 0))
            if eid <= last:
                continue
            actor = (e.get("actor") or {}).get("login", "")
            if e.get("event") not in _EVENTS or ctx.cfg.is_party(actor):
                newest = max(newest, eid)
                continue
            what = (e.get("label") or {}).get("name") or (e.get("assignee") or {}).get("login") or ""
            record = {"issue": n, "event_id": eid, "actor": actor, "event": e.get("event"), "what": what}
            if group.name == _first_group(ctx.cfg):
                try:
                    ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "PROTOCOL-VIOLATION", None, None),
                                                                            f"off-protocol change on #{n}: {_safe(actor)} {e.get('event')} {_safe(what)}".rstrip()))
                except GitHubError as ge:
                    out.append({**record, "error": type(ge).__name__})
                    break   # do not advance past (or beyond) a failed post: retry it next time, never duplicate it
            out.append(record)
            newest = max(newest, eid)
        if newest != last:
            cursors[str(n)] = newest
            try:
                _save(path, cursors)
            except OSError:
                pass
    return out
