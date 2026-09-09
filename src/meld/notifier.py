"""Slack nudges for items that need a person: one message listing every due needs-human issue, each repeated after a
configured delay -- gated by GitHub's own record of the shared `meld:nudged` label rather than a local per-party file,
so N groups nudging the same board can never send N independent copies."""
from __future__ import annotations

import http.client
import json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Mapping

from meld.board import Board, NUDGED_LABEL
from meld.github import GitHubError
from meld.status import _age_minutes

_ISO = "%Y-%m-%dT%H:%M:%SZ"


def _post_webhook(url: str, payload: dict) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10):  # noqa: S310 - the URL comes from the operator's own environment
        pass


def _age(minutes: int) -> str:
    return f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes}m"


def slack_text(items: list[dict], repo: str) -> str:
    lines = [f"Meld needs a human on {repo}:"]
    for i in items:
        lines.append(f"• #{i['issue']} {i['title']} ({_age(i['age_minutes'])}) https://github.com/{repo}/issues/{i['issue']}")
    return "\n".join(lines)


def _stamp(value: str | None) -> datetime | None:
    """A GitHub event timestamp, or None when there is none or it is not in the shape GitHub writes -- a malformed or
    foreign value reads as "never nudged" (the item is due) rather than raising out of tick."""
    if not value:
        return None
    try:
        return datetime.strptime(value, _ISO).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def last_nudge(events: list[dict], bots: set[str]) -> datetime | None:
    """The newest `labeled meld:nudged` event by a configured bot (a human adding the label by hand does not count as a nudge)."""
    stamps = [_stamp(e.get("created_at")) for e in events
              if e.get("event") == "labeled" and (e.get("label") or {}).get("name") == NUDGED_LABEL and (e.get("actor") or {}).get("login") in bots]
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None


def notify(ctx, board: Board, now: datetime, *, post: Callable[[str, dict], None] | None = None, env: Mapping[str, str] | None = None,
           dry_run: bool = False, events: Callable[[int], list[dict]] | None = None) -> dict:
    env = os.environ if env is None else env
    events_fn = events or ctx.gh.issue_events
    bots = {g.bot for g in ctx.cfg.groups.values()}
    items = sorted(({"issue": i.number, "title": i.title, "age_minutes": _age_minutes(i.updated_at, now)}
                    for i in board.issues.values() if i.status == "needs_human"), key=lambda d: d["issue"])
    errors: list[str] = []
    due = []
    for item in items:
        try:
            evs = events_fn(item["issue"])
        except GitHubError as e:
            # A failed fetch must never risk a duplicate nudge: this item is simply not due on this call (it is
            # reconsidered from scratch next time) rather than guessed at from incomplete information.
            errors.append(f"{type(e).__name__}: failed to read nudge history for #{item['issue']}")
            continue
        last = last_nudge(evs, bots)
        if last is None or (now - last).total_seconds() >= ctx.cfg.notify_repeat_after_minutes * 60:
            due.append(item)
    numbers = [d["issue"] for d in due]
    text = slack_text(due, ctx.cfg.repo_name) if due else None

    webhook_env = ctx.cfg.notify_webhook_env
    url = env.get(webhook_env) if webhook_env else None
    skipped = None
    if not webhook_env:
        skipped = "no [notify] slack_dm_webhook_env configured"
    elif not url:
        skipped = f"{webhook_env} is not set"
    if skipped is not None:
        result = {"due": numbers, "notified": [], "skipped": skipped, "text": text, "cleared": []}
        if errors:
            result["errors"] = errors
        return result

    if dry_run:
        result = {"due": numbers, "notified": [], "skipped": None, "text": text, "cleared": []}
        if errors:
            result["errors"] = errors
        return result

    notified: list[int] = []
    if due:
        try:
            (post or _post_webhook)(url, {"text": text})
        except (OSError, ValueError, http.client.HTTPException) as e:
            # urllib raises OSError (URLError) for most transport failures but http.client exceptions (a bad status
            # line, a truncated body) for others; none of them may take tick down.
            # Never interpolate the exception itself into the message: some connection
            # errors embed the target URL in their args, and the webhook URL must never
            # appear in output. The class name plus a generic description is enough to
            # act on.
            errors.append(f"{type(e).__name__}: failed to post the Slack notification")
        else:
            notified = numbers
            for n in numbers:
                try:
                    ctx.gh.add_labels(n, [NUDGED_LABEL])
                except GitHubError as e:
                    # Contained: the send already happened, so the item counts as notified either way. A label that
                    # failed to attach just means the next call may re-nudge once more than strictly needed -- the
                    # documented cost of never silently losing a send that really went out.
                    errors.append(f"{type(e).__name__}: failed to record the nudge label on #{n}")

    cleared: list[int] = []
    for issue in sorted(board.issues.values(), key=lambda i: i.number):
        if NUDGED_LABEL in issue.labels and issue.status != "needs_human":
            try:
                ctx.gh.remove_label(issue.number, NUDGED_LABEL)
            except GitHubError as e:
                errors.append(f"{type(e).__name__}: failed to clear the nudge label on #{issue.number}")
            else:
                cleared.append(issue.number)

    result = {"due": numbers, "notified": notified, "skipped": None, "text": text, "cleared": cleared}
    if errors:
        result["errors"] = errors
    return result
