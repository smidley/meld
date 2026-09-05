"""Slack nudges for items that need a person: one message per needs-human issue, repeated after a configured delay."""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from meld.atomic import write_atomic
from meld.board import Board
from meld.status import _age_minutes

NOTIFY_STATE = "notify.json"
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


def _load_state(path: Path) -> dict[str, str]:
    """A missing, corrupt, unreadable, or non-dict state file is treated as empty rather than raising."""
    try:
        value = json.loads(path.read_text()) if path.is_file() else {}
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}


def _persist(state_path: Path, data: dict) -> None:
    """Write the pruned state. May raise OSError/ValueError; callers decide how (or whether) to report that."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(state_path, json.dumps(data, indent=2))


def notify(ctx, board: Board, now: datetime, *, post: Callable[[str, dict], None] | None = None, env: Mapping[str, str] | None = None,
           dry_run: bool = False) -> dict:
    env = os.environ if env is None else env
    items = sorted(({"issue": i.number, "title": i.title, "age_minutes": _age_minutes(i.updated_at, now)}
                    for i in board.issues.values() if i.status == "needs_human"), key=lambda d: d["issue"])
    current_ids = {str(i["issue"]) for i in items}
    state_path = ctx.store.state_dir / NOTIFY_STATE
    sent = _load_state(state_path)
    stale = set(sent) - current_ids  # tracked before, not needs_human (or gone) now
    due = []
    for item in items:
        last = sent.get(str(item["issue"]))
        if last is None or (now - datetime.strptime(last, _ISO).replace(tzinfo=timezone.utc)).total_seconds() >= ctx.cfg.notify_repeat_after_minutes * 60:
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
        # Never send while misconfigured, but don't let a resolved issue keep "cooling
        # down" against a stale timestamp for the whole misconfiguration window: prune on
        # a best-effort basis (a failed write here just means the next call tries again).
        if stale:
            try:
                _persist(state_path, {k: v for k, v in sent.items() if k in current_ids})
            except (OSError, ValueError):
                pass
        return {"due": numbers, "notified": [], "skipped": skipped, "text": text}

    if dry_run:
        return {"due": numbers, "notified": [], "skipped": None, "text": text}

    notified: list[int] = []
    errors: list[str] = []
    if due:
        try:
            (post or _post_webhook)(url, {"text": text})
        except (OSError, ValueError) as e:
            # Never interpolate the exception itself into the message: some connection
            # errors embed the target URL in their args, and the webhook URL must never
            # appear in output. The class name plus a generic description is enough to
            # act on. Recording no state here means every item in this batch is due again
            # next call, not silently marked as sent.
            errors.append(f"{type(e).__name__}: failed to post the Slack notification")
        else:
            stamp = now.strftime(_ISO)
            for n in numbers:
                sent[str(n)] = stamp
            notified = numbers
    if due or stale:
        # Prune regardless of whether this call sent anything: an issue that left
        # needs_human (or left the board) should not keep cooling down against an old
        # timestamp, so a later return to needs_human is a fresh notification.
        try:
            _persist(state_path, {k: v for k, v in sent.items() if k in current_ids})
        except (OSError, ValueError) as e:
            # The Slack send itself (if any) already happened and is reflected in
            # `notified` above; only the on-disk record failed here (full disk,
            # permissions, two same-party writers racing on write_atomic's .tmp name).
            # Report it rather than raise, so this doesn't take tick() down with it.
            errors.append(f"state not recorded ({type(e).__name__}); items may be re-notified")
    result = {"due": numbers, "notified": notified, "skipped": None, "text": text}
    if errors:
        result["errors"] = errors
    return result
