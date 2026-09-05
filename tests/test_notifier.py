import json
from datetime import datetime, timedelta, timezone

from meld.notifier import NOTIFY_STATE, notify, slack_text
from tests.productfixtures import make_product

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def test_notify_sends_once_and_repeats_after_the_configured_delay(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    _, board = __import__("meld.status", fromlist=["build_status"]).build_status(ctx)
    sent: list[tuple[str, dict]] = []
    env = {"MELD_SLACK_DM_WEBHOOK": "https://hooks.slack.test/abc"}
    out = notify(ctx, board, NOW, post=lambda url, payload: sent.append((url, payload)), env=env)
    assert out["due"] == [14] and out["notified"] == [14] and out["skipped"] is None
    assert sent[0][0] == "https://hooks.slack.test/abc" and "#14" in sent[0][1]["text"] and "Needs API key" in sent[0][1]["text"]
    state = json.loads((ctx.store.state_dir / NOTIFY_STATE).read_text())
    assert state["14"] == "2026-09-03T12:00:00Z"
    again = notify(ctx, board, NOW + timedelta(minutes=5), post=lambda url, payload: sent.append((url, payload)), env=env)
    assert again["due"] == [] and again["notified"] == [] and len(sent) == 1
    later = notify(ctx, board, NOW + timedelta(minutes=ctx.cfg.notify_repeat_after_minutes + 1), post=lambda url, payload: sent.append((url, payload)), env=env)
    assert later["notified"] == [14] and len(sent) == 2


def test_notify_skips_without_a_webhook_and_dry_runs(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    _, board = __import__("meld.status", fromlist=["build_status"]).build_status(ctx)
    sent = []
    out = notify(ctx, board, NOW, post=lambda u, p: sent.append(p), env={})
    assert out["notified"] == [] and out["due"] == [14] and "not set" in out["skipped"] and sent == []
    env = {ctx.cfg.notify_webhook_env: "https://hooks.slack.test/abc"}
    dry = notify(ctx, board, NOW, post=lambda u, p: sent.append(p), env=env, dry_run=True)
    assert dry["notified"] == [] and dry["due"] == [14] and "#14" in dry["text"] and sent == [] and not (ctx.store.state_dir / NOTIFY_STATE).exists()


def test_slack_text_lists_each_item_with_age_and_link():
    text = slack_text([{"issue": 14, "title": "Needs API key", "age_minutes": 90}], "acme/widgets")
    assert text.startswith("Meld needs a human on acme/widgets:") and "#14 Needs API key (1h 30m)" in text and "https://github.com/acme/widgets/issues/14" in text
