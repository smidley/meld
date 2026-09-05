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


def _safe(text: str) -> str:
    """Backtick-quote text sourced from GitHub (a label name, a login) before it reaches a coordination comment
    body, and defuse a leading '@' so it can never read as a bare, auto-linkable mention."""
    if text.startswith("@"):
        text = "@​" + text[1:]   # zero-width space breaks mention auto-linking without changing how it reads
    return f"`{text}`"


def ci_red_sweep(ctx, board: Board, group: GroupConfig) -> list[dict]:
    """A red check on one of our open PRs is a request for changes: the linked issue goes back to in-progress once
    per head. State (the handled head per PR) advances only once the label swap and the comment both succeed, and
    is persisted right after that item -- a `GitHubError` (including `RateLimited`) on one PR is recorded and does
    not stop the sweep, and never causes an earlier PR's success to be lost or a later retry to duplicate it."""
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
        try:
            _swap_labels(ctx, pr.issue_number, IN_PROGRESS, (IN_REVIEW,))
            ctx.gh.create_comment(pr.issue_number, f"Meld: CI is red on PR #{pr.number} (head `{pr.head_sha[:7]}`); treated as request-changes. "
                                                   f"The next build run on this issue fixes the checks; the PR moves back to review when they pass.")
        except GitHubError as e:
            out.append({"pr": pr.number, "issue": pr.issue_number, "head": pr.head_sha, "error": type(e).__name__})
            continue
        handled[str(pr.number)] = pr.head_sha
        out.append({"pr": pr.number, "issue": pr.issue_number, "head": pr.head_sha})
        try:
            _save(path, handled)
        except OSError:
            pass
    return out


def flag_ignored_comments(ctx, board: Board, group: GroupConfig) -> dict:
    """Comments from accounts outside the configured bots and humans are ignored by the parser and flagged once
    (spec 7). The posting group's cursor advances only once its post succeeds (so a failed post is retried, never
    lost or duplicated); a group that isn't posting has nothing that can fail and always advances."""
    known = {g.bot for g in ctx.cfg.groups.values()} | set(ctx.cfg.humans)
    path = ctx.store.state_dir / IGNORED_STATE
    state = _load(path)
    cursor = int(state.get("last_id", 0))
    new = sorted((c for c in board.comments if c.author not in known and c.id > cursor), key=lambda c: c.id)
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
    known = {g.bot for g in ctx.cfg.groups.values()} | set(ctx.cfg.humans)
    path = ctx.store.state_dir / OFF_PROTOCOL_STATE
    cursors = _load(path)
    out = []
    for n, issue in sorted(board.issues.items()):
        if issue.state != "open":
            continue
        last = int(cursors.get(str(n), 0))
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
            if e.get("event") not in _EVENTS or actor in known:
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
