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


def _save(path: Path, data: dict) -> None:
    """Write the state file. May raise OSError; callers contain that so a write problem never aborts a sweep."""
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(data, indent=2))


def _first_group(cfg) -> str:
    return min(cfg.group_names)


def ci_red_sweep(ctx, board: Board, group: GroupConfig) -> list[dict]:
    """A red check on one of our open PRs is a request for changes: the linked issue goes back to in-progress once per head."""
    from meld.dispatch import IN_REVIEW, _swap_labels  # local import: dispatch imports this module
    path = ctx.store.state_dir / CI_RED_STATE
    handled = _load(path)
    out = []
    for pr in sorted(board.pulls.values(), key=lambda p: p.number):
        if pr.state != "open" or pr.checks != "red" or party_of_login(ctx.cfg, pr.author) != group.name or pr.issue_number is None:
            continue
        issue = board.issues.get(pr.issue_number)
        if issue is None or issue.status != "in_review" or handled.get(str(pr.number)) == pr.head_sha:
            continue
        _swap_labels(ctx, pr.issue_number, IN_PROGRESS, (IN_REVIEW,))
        ctx.gh.create_comment(pr.issue_number, f"Meld: CI is red on PR #{pr.number} (head `{pr.head_sha[:7]}`); treated as request-changes. "
                                               f"The next build run on this issue fixes the checks; the PR moves back to review when they pass.")
        handled[str(pr.number)] = pr.head_sha
        out.append({"pr": pr.number, "issue": pr.issue_number, "head": pr.head_sha})
    if out:
        try:
            _save(path, handled)
        except OSError:
            pass
    return out


def flag_ignored_comments(ctx, board: Board, group: GroupConfig) -> dict:
    """Comments from accounts outside the configured bots and humans are ignored by the parser and flagged once (spec 7)."""
    path = ctx.store.state_dir / IGNORED_STATE
    state = _load(path)
    cursor = int(state.get("last_id", 0))
    new = sorted((c for c in board.comments if not c.authorized and c.id > cursor), key=lambda c: c.id)
    if not new:
        return {"flagged": [], "posted": False}
    posted = False
    if group.name == _first_group(ctx.cfg):
        body = "\n".join(f"- comment {c.id} by `{c.author}`" for c in new)
        ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "IGNORED-COMMENT", None, None),
                                                                f"Comments from accounts that are neither a configured bot nor a listed human were ignored:\n{body}"))
        posted = True
    try:
        _save(path, {"last_id": new[-1].id})
    except OSError:
        pass
    return {"flagged": [c.id for c in new], "posted": posted}


def off_protocol_sweep(ctx, board: Board, group: GroupConfig) -> list[dict]:
    """Label or assignee changes on milestone issues by an actor that is neither a bot nor a listed human (spec 17)."""
    known = {g.bot for g in ctx.cfg.groups.values()} | set(ctx.cfg.humans)
    path = ctx.store.state_dir / OFF_PROTOCOL_STATE
    cursors = _load(path)
    out = []
    for n, issue in sorted(board.issues.items()):
        if issue.state != "open":
            continue
        last = int(cursors.get(str(n), 0))
        newest = last
        try:
            events = ctx.gh.issue_events(n)
        except GitHubError:
            # One flaky/unreachable issue must not abort housekeeping for the rest of the milestone.
            continue
        for e in events:
            eid = int(e.get("id", 0))
            newest = max(newest, eid)
            actor = (e.get("actor") or {}).get("login", "")
            if eid <= last or e.get("event") not in _EVENTS or actor in known:
                continue
            what = (e.get("label") or {}).get("name") or (e.get("assignee") or {}).get("login") or ""
            record = {"issue": n, "event_id": eid, "actor": actor, "event": e.get("event"), "what": what}
            out.append(record)
            if group.name == _first_group(ctx.cfg):
                ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "PROTOCOL-VIOLATION", None, None),
                                                                        f"off-protocol change on #{n}: {actor} {e.get('event')} {what}".rstrip()))
        if newest != last:
            cursors[str(n)] = newest
    try:
        _save(path, cursors)
    except OSError:
        pass
    return out
