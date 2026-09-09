import http.client
from datetime import datetime, timedelta, timezone

from meld.dispatch import tick
from meld.notifier import NUDGE_STAGGER_MINUTES, notify, slack_text
from meld.status import build_status
from tests.productfixtures import R, fixture, make_product

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def events_for(fake, number, stamps):
    fake.route("GET", f"repos/acme/widgets/issues/{number}/events",
               [{"id": 500 + i, "event": "labeled", "label": {"name": "meld:nudged"}, "actor": {"login": "meld-codex-bot"}, "created_at": s} for i, s in enumerate(stamps)])


def test_notify_sends_once_and_repeats_after_the_configured_delay_using_the_shared_label(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    events_for(fake, 14, [])
    fake.route("POST", "repos/acme/widgets/issues/14/labels", [], status=200)
    sent = []
    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    _, board = build_status(ctx)
    out = notify(ctx, board, now, post=lambda url, payload: sent.append(payload), env={"MELD_SLACK_DM_WEBHOOK": "https://hooks.example/x"})
    assert out["notified"] == [14] and len(sent) == 1
    labels = [c.body["labels"] for c in fake.calls if c.method == "POST" and c.endpoint.endswith("issues/14/labels")]
    assert labels == [["meld:nudged"]]
    events_for(fake, 14, ["2026-09-05T12:00:00Z"])            # what GitHub now reports, for ANY party
    other_party_ctx = make_product(tmp_path / "codex", group="codex")[1]   # a different PM, different state dir
    other_party_ctx.gh = ctx.gh                                # ...but the same GitHub: the label event is shared, not a local file
    out2 = notify(other_party_ctx, board, now + timedelta(minutes=30), post=lambda u, p: sent.append(p), env={"MELD_SLACK_DM_WEBHOOK": "https://hooks.example/x"})
    assert out2["notified"] == [] and len(sent) == 1
    out3 = notify(ctx, board, now + timedelta(minutes=121), post=lambda u, p: sent.append(p), env={"MELD_SLACK_DM_WEBHOOK": "https://hooks.example/x"})
    assert out3["notified"] == [14] and len(sent) == 2


def test_notify_staggers_a_later_party_so_it_is_not_immediately_due(tmp_path):
    """Two parties ticking within the same GitHub round-trip must not both see "no recent nudge" and both send: each
    party's due check is offset by its alphabetical position among the configured groups, so with no nudge events at
    all the alphabetically-first party (claude, offset 0) is due the instant the item appears, but the next party
    (codex, offset NUDGE_STAGGER_MINUTES) is not due until the item is that much older."""
    fake, ctx, repo, origin = make_product(tmp_path)                       # claude: first alphabetically, offset 0
    other_party_ctx = make_product(tmp_path / "codex", group="codex")[1]   # codex: second, offset NUDGE_STAGGER_MINUTES
    other_party_ctx.gh = ctx.gh                                            # same GitHub, different party
    events_for(fake, 14, [])
    _, board = build_status(ctx)
    then = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)                 # issue #14's updated_at in the fixture
    assert notify(ctx, board, then, env={})["due"] == [14]
    assert notify(other_party_ctx, board, then, env={})["due"] == []       # not due yet at the same moment
    later = then + timedelta(minutes=NUDGE_STAGGER_MINUTES)
    assert notify(other_party_ctx, board, later, env={})["due"] == [14]    # due once it's one stagger-minute older


def test_notify_second_party_waits_window_plus_offset_after_the_first_partys_label(tmp_path):
    """Once a nudge label exists (posted by whichever party), the repeat window is measured from that shared event --
    but a later party still adds its own offset on top of the window, not just the window alone."""
    fake, ctx, repo, origin = make_product(tmp_path)                       # claude: offset 0
    other_party_ctx = make_product(tmp_path / "codex", group="codex")[1]   # codex: offset NUDGE_STAGGER_MINUTES
    other_party_ctx.gh = ctx.gh
    last = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    events_for(fake, 14, [last.strftime("%Y-%m-%dT%H:%M:%SZ")])
    _, board = build_status(ctx)
    at_window = last + timedelta(minutes=ctx.cfg.notify_repeat_after_minutes)
    assert notify(ctx, board, at_window, env={})["due"] == [14]                  # window alone is enough for the first party
    assert notify(other_party_ctx, board, at_window, env={})["due"] == []        # ...but not for the second
    at_window_plus_offset = last + timedelta(minutes=ctx.cfg.notify_repeat_after_minutes + NUDGE_STAGGER_MINUTES)
    assert notify(other_party_ctx, board, at_window_plus_offset, env={})["due"] == [14]


def test_notify_ignores_a_labeled_event_from_a_non_bot_login(tmp_path):
    """`last_nudge` only counts a `labeled meld:nudged` event whose actor is a configured bot; a human who hand-added
    the label (e.g. while triaging) must not be mistaken for a nudge that was already sent."""
    fake, ctx, repo, origin = make_product(tmp_path)
    fake.route("GET", R + "issues/14/events",
               [{"id": 501, "event": "labeled", "label": {"name": "meld:nudged"}, "actor": {"login": "empowerScott"}, "created_at": "2026-09-03T11:59:00Z"}])
    _, board = build_status(ctx)
    out = notify(ctx, board, NOW, env={})
    assert out["due"] == [14]


def test_notify_clears_a_stale_label_even_without_a_webhook_configured(tmp_path):
    """Clearing a stale `meld:nudged` label is not "sending" -- it must run whether or not a Slack webhook is
    configured. Only the send itself is gated on the webhook."""
    fake, ctx, repo, origin = make_product(tmp_path)
    issues = fixture("issues.json")
    for issue in issues:
        if issue["number"] == 11:                                   # ready, but still carries the nudge label
            issue["labels"].append({"name": "meld:nudged"})
    fake.route("GET", R + "issues", issues)
    fake.route("DELETE", R + "issues/11/labels/meld%3Anudged", {}, status=200)
    fake.route("DELETE", R + "issues/11/labels/meld:nudged", {}, status=200)   # whichever encoding remove_label uses
    _, board = build_status(ctx)
    out = notify(ctx, board, NOW, env={})   # no MELD_SLACK_DM_WEBHOOK configured at all
    assert out["skipped"] is not None
    assert out["cleared"] == [11]
    assert not [c for c in fake.calls if c.method == "POST" and "labels" in c.endpoint]   # nothing sent or nudge-labeled


def test_notify_removes_the_label_when_the_item_no_longer_needs_a_human(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    issues = fixture("issues.json")
    for issue in issues:
        if issue["number"] == 11:                                   # ready, but still carries the nudge label
            issue["labels"].append({"name": "meld:nudged"})
    fake.route("GET", "repos/acme/widgets/issues", issues)
    events_for(fake, 14, [])
    fake.route("POST", "repos/acme/widgets/issues/14/labels", [], status=200)
    fake.route("DELETE", "repos/acme/widgets/issues/11/labels/meld%3Anudged", {}, status=200)
    fake.route("DELETE", "repos/acme/widgets/issues/11/labels/meld:nudged", {}, status=200)   # whichever encoding remove_label uses
    _, board = build_status(ctx)
    out = notify(ctx, board, datetime(2026, 9, 5, 12, tzinfo=timezone.utc), post=lambda u, p: None, env={"MELD_SLACK_DM_WEBHOOK": "https://hooks.example/x"})
    assert out["cleared"] == [11] and out["notified"] == [14]
    assert any(c.method == "DELETE" and "issues/11/labels/" in c.endpoint and "nudged" in c.endpoint for c in fake.calls)


def test_notify_skips_without_a_webhook_and_dry_runs(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    _, board = __import__("meld.status", fromlist=["build_status"]).build_status(ctx)
    sent = []
    out = notify(ctx, board, NOW, post=lambda u, p: sent.append(p), env={})
    assert out["notified"] == [] and out["due"] == [14] and "not set" in out["skipped"] and sent == []
    env = {ctx.cfg.notify_webhook_env: "https://hooks.slack.test/abc"}
    dry = notify(ctx, board, NOW, post=lambda u, p: sent.append(p), env=env, dry_run=True)
    assert dry["notified"] == [] and dry["due"] == [14] and "#14" in dry["text"] and sent == []
    assert not [c for c in fake.calls if c.method in ("POST", "DELETE") and "labels" in c.endpoint]   # dry run labels nothing


def test_slack_text_lists_each_item_with_age_and_link():
    text = slack_text([{"issue": 14, "title": "Needs API key", "age_minutes": 90}], "acme/widgets")
    assert text.startswith("Meld needs a human on acme/widgets:") and "#14 Needs API key (1h 30m)" in text and "https://github.com/acme/widgets/issues/14" in text


def test_notify_contains_a_failing_post_and_adds_no_label(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    _, board = build_status(ctx)
    env = {"MELD_SLACK_DM_WEBHOOK": "https://hooks.slack.test/abc"}

    def failing_post(url, payload):
        raise OSError("boom")

    out = notify(ctx, board, NOW, post=failing_post, env=env)
    assert out["due"] == [14] and out["notified"] == []
    assert len(out["errors"]) == 1 and "OSError" in out["errors"][0]
    assert env["MELD_SLACK_DM_WEBHOOK"] not in out["errors"][0]
    assert not [c for c in fake.calls if c.method == "POST" and c.endpoint.endswith("issues/14/labels")]  # no label on a failed send

    sent: list[dict] = []
    fake.route("POST", R + "issues/14/labels", [], status=200)
    healthy = notify(ctx, board, NOW, post=lambda u, p: sent.append(p), env=env)
    assert healthy["due"] == [14] and healthy["notified"] == [14] and len(sent) == 1


def test_notify_contains_an_events_fetch_failure_and_never_double_nudges(tmp_path):
    """A GitHubError fetching one issue's nudge history (e.g. a transient 5xx) makes that item not due this call --
    never a duplicate nudge guessed from incomplete information -- and is reported rather than raised."""
    fake, ctx, repo, origin = make_product(tmp_path)
    fake.route("GET", R + "issues/14/events", {"message": "server error"}, status=500)
    env = {"MELD_SLACK_DM_WEBHOOK": "https://hooks.slack.test/abc"}
    sent: list[dict] = []
    _, board = build_status(ctx)
    out = notify(ctx, board, NOW, post=lambda u, p: sent.append(p), env=env)
    assert out["due"] == [] and out["notified"] == [] and sent == []
    assert len(out["errors"]) == 1 and "GitHubError" in out["errors"][0]
    assert not [c for c in fake.calls if c.method == "POST" and c.endpoint.endswith("issues/14/labels")]


def test_notify_contains_a_failed_label_add_but_still_counts_as_notified(tmp_path):
    """Contained: the Slack send already happened, so the item counts as notified either way -- a label that failed
    to attach (POST issues/14/labels is deliberately left unrouted here) just means the next call may re-nudge once
    more than strictly needed, the documented cost of never silently losing a send that really went out."""
    fake, ctx, repo, origin = make_product(tmp_path)
    env = {"MELD_SLACK_DM_WEBHOOK": "https://hooks.slack.test/abc"}
    sent: list[dict] = []
    _, board = build_status(ctx)
    out = notify(ctx, board, NOW, post=lambda u, p: sent.append(p), env=env)
    assert out["notified"] == [14] and len(sent) == 1
    assert len(out["errors"]) == 1 and "GitHubError" in out["errors"][0]


def test_notify_treats_a_malformed_event_timestamp_as_never_nudged(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    fake.route("GET", R + "issues/14/events",
               [{"id": 501, "event": "labeled", "label": {"name": "meld:nudged"}, "actor": {"login": "meld-codex-bot"}, "created_at": "last tuesday-ish"}])
    fake.route("POST", R + "issues/14/labels", [], status=200)
    env = {"MELD_SLACK_DM_WEBHOOK": "https://hooks.slack.test/abc"}
    sent: list[dict] = []
    _, board = build_status(ctx)
    out = notify(ctx, board, NOW, post=lambda u, p: sent.append(p), env=env)
    assert out["due"] == [14] and out["notified"] == [14] and len(sent) == 1    # the bad value reads as never nudged, not as an error


def test_notify_contains_an_http_client_exception_and_tick_surfaces_it(tmp_path):
    # F7: urllib can raise http.client exceptions that are not OSErrors (a bad status line, a truncated body); tick must
    # neither raise nor swallow them silently.
    fake, ctx, repo, origin = make_product(tmp_path)
    _, board = build_status(ctx)
    env = {"MELD_SLACK_DM_WEBHOOK": "https://hooks.slack.test/abc"}

    def failing_post(url, payload):
        raise http.client.BadStatusLine("HTTP/1.1 garbage")

    out = notify(ctx, board, NOW, post=failing_post, env=env)
    assert out["notified"] == [] and len(out["errors"]) == 1 and "BadStatusLine" in out["errors"][0]
    assert env["MELD_SLACK_DM_WEBHOOK"] not in out["errors"][0]
    ticked = tick(ctx, now=NOW, env=env, post=failing_post)
    assert ticked["notified"] == [] and ticked["notify_errors"] == out["errors"]
    fake.route("POST", R + "issues/14/labels", [], status=200)
    healthy = tick(ctx, now=NOW, env=env, post=lambda u, p: None)
    assert healthy["notified"] == [14] and healthy["notify_errors"] == []
