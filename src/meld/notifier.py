"""Slack nudges for items that need a person: one message listing every due needs-human issue, each repeated after a
configured delay -- gated by GitHub's own record of the shared `meld:nudged` label rather than a local per-party file.
Concurrent parties are staggered `NUDGE_STAGGER_MINUTES` apart by their alphabetical position among `cfg.group_names`,
so under normal operation only one party is ever due to nudge a given item at a time even though GitHub offers no
compare-and-swap on labels. The residual: a duplicate nudge is still possible if the earlier party's label event takes
longer than `NUDGE_STAGGER_MINUTES` to land after it read the events (an unusually slow GitHub round-trip) -- accepted
as the cost of having no atomic check-then-act primitive."""
from __future__ import annotations

import http.client
import json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Collection, Mapping

from meld.board import Board, NUDGED_LABEL
from meld.github import GitHubError
from meld.status import _age_minutes

_ISO = "%Y-%m-%dT%H:%M:%SZ"
NUDGE_STAGGER_MINUTES = 1


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


def last_nudge(events: list[dict], nudgers: set[str]) -> datetime | None:
    """The newest `labeled meld:nudged` event by a party whose nudge counts: a configured bot or a listed human.

    Humans are in that set because a human running `meld notify` sends and labels under their own login, and the
    throttle would otherwise not see it -- the next PM tick inside the window would nudge the same people again.
    The consequence is deliberate: a listed human who adds the label by hand, without sending anything, snoozes
    the item for exactly one `notify.repeat_after_minutes` window, which is the most plausible reading of that
    act. Any other login (a passing collaborator, another tool's bot) still does not count."""
    stamps = [_stamp(e.get("created_at")) for e in events
              if e.get("event") == "labeled" and (e.get("label") or {}).get("name") == NUDGED_LABEL
              and ((e.get("actor") or {}).get("login") or "").lower() in nudgers]
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None


def _stagger_offset(ctx) -> int:
    """This caller's stagger offset in minutes: its position among the alphabetically sorted configured groups, times
    `NUDGE_STAGGER_MINUTES`, so concurrent parties are never all due for the same item in the same GitHub round-trip.
    A caller with no group (a human running `meld notify` directly) gets 0, same as the alphabetically first group."""
    names = sorted(ctx.cfg.group_names)
    group = ctx.actor().group
    return names.index(group) * NUDGE_STAGGER_MINUTES if group in names else 0


def _result(due: list[int], notified: list[int], skipped: str | None, text: str | None, cleared: list[int], errors: list[str]) -> dict:
    result = {"due": due, "notified": notified, "skipped": skipped, "text": text, "cleared": cleared}
    if errors:
        result["errors"] = errors
    return result


def notify(ctx, board: Board, now: datetime, *, post: Callable[[str, dict], None] | None = None, env: Mapping[str, str] | None = None,
           dry_run: bool = False, events: Callable[[int], list[dict]] | None = None, skip: Collection[int] = ()) -> dict:
    """`skip`: issues the caller has already acted on since `board` was built, so they are not nudged from a stale
    view. `dispatch.tick` passes the issues its resume loop just un-gated -- in `board` they still read needs_human,
    and the tick that lifts a hold must not also Slack a human about it."""
    env = os.environ if env is None else env
    events_fn = events or ctx.gh.issue_events
    nudgers = {g.bot.lower() for g in ctx.cfg.groups.values()} | {h.lower() for h in ctx.cfg.humans}   # logins are case-insensitive
    offset = _stagger_offset(ctx)
    items = sorted(({"issue": i.number, "title": i.title, "age_minutes": _age_minutes(i.updated_at, now)}
                    for i in board.issues.values() if i.status == "needs_human" and i.number not in skip), key=lambda d: d["issue"])
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
        last = last_nudge(evs, nudgers)
        # Staggered by party: with no prior nudge, a later party waits until the item is `offset` minutes old before
        # it is due; once a nudge exists, a later party waits `offset` minutes past the window on top of everyone
        # else. The alphabetically first group (and a human with no group) has offset 0, so its behaviour is exactly
        # the un-staggered rule.
        if last is None:
            is_due = item["age_minutes"] >= offset
        else:
            is_due = (now - last).total_seconds() >= (ctx.cfg.notify_repeat_after_minutes + offset) * 60
        if is_due:
            due.append(item)
    numbers = [d["issue"] for d in due]
    text = slack_text(due, ctx.cfg.repo_name) if due else None

    # Clearing a stale label is not "sending": it runs whenever this isn't a dry run, regardless of whether a Slack
    # webhook is configured, so an unconfigured deployment still keeps `meld:nudged` truthful. Only a dry run --
    # which must touch nothing -- skips it.
    cleared: list[int] = []
    if not dry_run:
        for issue in sorted(board.issues.values(), key=lambda i: i.number):
            if NUDGED_LABEL in issue.labels and issue.status != "needs_human":
                try:
                    ctx.gh.remove_label(issue.number, NUDGED_LABEL)
                except GitHubError as e:
                    errors.append(f"{type(e).__name__}: failed to clear the nudge label on #{issue.number}")
                else:
                    cleared.append(issue.number)

    webhook_env = ctx.cfg.notify_webhook_env
    url = env.get(webhook_env) if webhook_env else None
    skipped = None
    if not webhook_env:
        skipped = "no [notify] slack_dm_webhook_env configured"
    elif not url:
        skipped = f"{webhook_env} is not set"
    if skipped is not None:
        return _result(numbers, [], skipped, text, cleared, errors)

    if dry_run:
        return _result(numbers, [], None, text, cleared, errors)

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

    return _result(numbers, notified, None, text, cleared, errors)
