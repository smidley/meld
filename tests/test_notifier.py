import json
from datetime import datetime, timedelta, timezone

from meld.notifier import NOTIFY_STATE, notify, slack_text
from meld.status import build_status
from tests.productfixtures import R, fixture, make_product

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


def test_notify_contains_a_failing_post_and_records_no_state_for_it(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    _, board = build_status(ctx)
    env = {"MELD_SLACK_DM_WEBHOOK": "https://hooks.slack.test/abc"}

    def failing_post(url, payload):
        raise OSError("boom")

    out = notify(ctx, board, NOW, post=failing_post, env=env)
    assert out["due"] == [14] and out["notified"] == []
    assert len(out["errors"]) == 1 and "OSError" in out["errors"][0]
    assert env["MELD_SLACK_DM_WEBHOOK"] not in out["errors"][0]
    state_path = ctx.store.state_dir / NOTIFY_STATE
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    assert "14" not in state  # not recorded as sent: it will be due again next tick

    sent: list[dict] = []
    healthy = notify(ctx, board, NOW, post=lambda u, p: sent.append(p), env=env)
    assert healthy["due"] == [14] and healthy["notified"] == [14] and len(sent) == 1


def test_notify_treats_a_corrupt_state_file_as_empty_and_proceeds(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    _, board = build_status(ctx)
    env = {"MELD_SLACK_DM_WEBHOOK": "https://hooks.slack.test/abc"}
    state_path = ctx.store.state_dir / NOTIFY_STATE
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{not valid json at all")
    sent: list[dict] = []
    out = notify(ctx, board, NOW, post=lambda u, p: sent.append(p), env=env)
    assert out["due"] == [14] and out["notified"] == [14] and len(sent) == 1
    state = json.loads(state_path.read_text())
    assert state["14"] == "2026-09-03T12:00:00Z"


def test_notify_prunes_a_resolved_item_and_renotifies_immediately_on_its_return(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    env = {"MELD_SLACK_DM_WEBHOOK": "https://hooks.slack.test/abc"}
    sent: list[dict] = []

    _, board = build_status(ctx)
    out = notify(ctx, board, NOW, post=lambda u, p: sent.append(p), env=env)
    assert out["notified"] == [14] and len(sent) == 1
    state = json.loads((ctx.store.state_dir / NOTIFY_STATE).read_text())
    assert state["14"] == "2026-09-03T12:00:00Z"

    # Issue 14 leaves needs_human: its throttle entry is pruned even though nothing is due.
    issues = fixture("issues.json")
    for i in issues:
        if i["number"] == 14:
            i["labels"] = [l for l in i["labels"] if l["name"] != "meld:needs-human"] + [{"name": "meld:ready"}]
    fake.route("GET", R + "issues", issues)
    _, resolved_board = build_status(ctx)
    out2 = notify(ctx, resolved_board, NOW + timedelta(minutes=1), post=lambda u, p: sent.append(p), env=env)
    assert out2["due"] == [] and out2["notified"] == [] and len(sent) == 1
    state2 = json.loads((ctx.store.state_dir / NOTIFY_STATE).read_text())
    assert "14" not in state2

    # It returns to needs_human well within the repeat delay: pruning made it a fresh notification.
    fake.route("GET", R + "issues", fixture("issues.json"))
    _, returned_board = build_status(ctx)
    out3 = notify(ctx, returned_board, NOW + timedelta(minutes=2), post=lambda u, p: sent.append(p), env=env)
    assert out3["notified"] == [14] and len(sent) == 2
