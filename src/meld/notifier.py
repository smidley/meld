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
    try:
        return json.loads(path.read_text()) if path.is_file() else {}
    except json.JSONDecodeError:
        return {}


def notify(ctx, board: Board, now: datetime, *, post: Callable[[str, dict], None] | None = None, env: Mapping[str, str] | None = None,
           dry_run: bool = False) -> dict:
    env = os.environ if env is None else env
    items = sorted(({"issue": i.number, "title": i.title, "age_minutes": _age_minutes(i.updated_at, now)}
                    for i in board.issues.values() if i.status == "needs_human"), key=lambda d: d["issue"])
    state_path = ctx.store.state_dir / NOTIFY_STATE
    sent = _load_state(state_path)
    due = []
    for item in items:
        last = sent.get(str(item["issue"]))
        if last is None or (now - datetime.strptime(last, _ISO).replace(tzinfo=timezone.utc)).total_seconds() >= ctx.cfg.notify_repeat_after_minutes * 60:
            due.append(item)
    numbers = [d["issue"] for d in due]
    text = slack_text(due, ctx.cfg.repo_name) if due else None
    if not ctx.cfg.notify_webhook_env:
        return {"due": numbers, "notified": [], "skipped": "no [notify] slack_dm_webhook_env configured", "text": text}
    url = env.get(ctx.cfg.notify_webhook_env)
    if not url:
        return {"due": numbers, "notified": [], "skipped": f"{ctx.cfg.notify_webhook_env} is not set", "text": text}
    if dry_run or not due:
        return {"due": numbers, "notified": [], "skipped": None, "text": text}
    (post or _post_webhook)(url, {"text": text})
    stamp = now.strftime(_ISO)
    for n in numbers:
        sent[str(n)] = stamp
    state_path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(state_path, json.dumps(sent, indent=2))
    return {"due": numbers, "notified": numbers, "skipped": None, "text": text}
