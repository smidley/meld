import json
from datetime import datetime, timezone

import pytest

from meld.actions import Refused, release
from meld.context import load_context
from meld.dispatch import DispatchError, dispatch, tick
from meld.rules import Verb, issue_refusal
from meld.state import pid_alive
from meld.status import build_status
from tests.conftest import env_for
from tests.fakeadapter import no_skills
from tests.fakeadapter import FakeAdapter
from tests.productfixtures import R, SLEEP_SCRIPT, claim_issue_11, fixture, make_product, wait_for_exit

PAST = datetime(2020, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def coordination_posts(fake):
    return [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]


def test_tick_kills_overdue_runs_and_marks_them_timed_out(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=SLEEP_SCRIPT), now=PAST)  # deadline is in 2020
    assert pid_alive(out["pid"])
    result = tick(ctx, now=NOW, env={})
    assert result["killed"] == [{"issue": 11, "how": "terminated"}] and result["checked"] == 1
    wait_for_exit(out["pid"], timeout=5)
    rec = json.loads((tmp_path / "home" / "runs" / "claude" / "11.json").read_text())
    assert rec["timed_out"] is True
    assert tick(ctx, now=NOW, env={})["checked"] == 0  # dead runs are not re-checked


def test_tick_creates_a_missing_lease(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=SLEEP_SCRIPT), now=NOW)
    rec_path = tmp_path / "home" / "runs" / "claude" / "11.json"
    rec = json.loads(rec_path.read_text())
    rec["lease_comment"] = None
    rec_path.write_text(json.dumps(rec))
    before = len(coordination_posts(fake))
    result = tick(ctx, now=NOW, env={})
    assert result["leases_created"] == [11] and result["killed"] == []
    assert coordination_posts(fake)[before].startswith("[claude:pm] LEASE #11\n") and "until: 2026-09-03T12:30:00Z" in coordination_posts(fake)[before]
    assert json.loads(rec_path.read_text())["lease_comment"] == 310
    from meld.dispatch import kill_run
    from meld.board import RunRecord
    kill_run(RunRecord(**{**rec, "alive": True}), grace_seconds=2)


def test_tick_releases_another_groups_expired_lease(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    issues = fixture("issues.json")
    for i in issues:
        if i["number"] == 13:  # in_review in the fixture; make it codex's in-progress claim
            i["labels"] = [l for l in i["labels"] if l["name"] != "meld:in-review"] + [{"name": "meld:in-progress"}]
    fake.route("GET", R + "issues", issues)
    comments = fixture("comments_10.json") + [{"id": 120, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:30:00Z",
                                               "body": "[codex:pm] LEASE #13\n\nuntil: 2026-09-03T11:00:00Z\nrun: codex/#13"}]
    fake.route("GET", R + "issues/10/comments", comments)
    fake.route("DELETE", R + "issues/13/labels/meld%3Aclaimed-by%3Acodex", {})
    fake.route("POST", R + "issues/13/labels", [])
    fake.route("DELETE", R + "issues/13/labels/meld%3Ain-progress", [])
    result = tick(ctx, now=NOW, env={})
    assert result["expired"] == [13]
    mutations = [(c.method, c.endpoint) for c in fake.calls if c.method != "GET" and "issues/13" in c.endpoint]
    assert ("DELETE", R + "issues/13/labels/meld%3Aclaimed-by%3Acodex") in mutations and ("POST", R + "issues/13/labels") in mutations
    # Housekeeping's own sweeps (Task 8) may post again afterward (e.g. IGNORED-COMMENT on the fixture's
    # ever-unauthorized comment 103), so the EXPIRED post is no longer necessarily the last one -- find it directly.
    expired_post = next(c for c in coordination_posts(fake) if c.startswith("[claude:pm] EXPIRED"))
    assert expired_post.startswith("[claude:pm] EXPIRED #13\n") and "expired at 2026-09-03T11:00:00Z" in expired_post
    fresh = tick(ctx, now=datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc), env={})  # not yet expired at 10:00
    assert fresh["expired"] == []


def test_tick_posts_exactly_one_framework_comment_when_it_reclaims_an_expired_lease(tmp_path):
    # Fix round 1: release(ctx, n) inside tick's expired-lease reclaim used to also post its own framework RELEASED
    # note (actions.release's Decision-2 post, Task 2 round 1), so one event -- a lease expiring and tick reclaiming
    # the issue -- wrote two framework comments for #13. release(..., announce=False) suppresses that half; the
    # reclaim now writes exactly one framework comment, the more specific EXPIRED note (it already names the lease
    # that expired, and derive_leases treats both verbs as closers, so nothing about the lease itself is lost).
    fake, ctx, repo, origin = make_product(tmp_path)
    issues = fixture("issues.json")
    for i in issues:
        if i["number"] == 13:  # in_review in the fixture; make it codex's in-progress claim
            i["labels"] = [l for l in i["labels"] if l["name"] != "meld:in-review"] + [{"name": "meld:in-progress"}]
    fake.route("GET", R + "issues", issues)
    comments = fixture("comments_10.json") + [{"id": 120, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:30:00Z",
                                               "body": "[codex:pm] LEASE #13\n\nuntil: 2026-09-03T11:00:00Z\nrun: codex/#13"}]
    fake.route("GET", R + "issues/10/comments", comments)
    fake.route("DELETE", R + "issues/13/labels/meld%3Aclaimed-by%3Acodex", {})
    fake.route("POST", R + "issues/13/labels", [])
    fake.route("DELETE", R + "issues/13/labels/meld%3Ain-progress", [])
    assert tick(ctx, now=NOW, env={})["expired"] == [13]
    for_13 = [c for c in coordination_posts(fake) if c.startswith("[claude:pm] EXPIRED #13") or c.startswith("[claude:pm] RELEASED #13")]
    assert len(for_13) == 1 and for_13[0].startswith("[claude:pm] EXPIRED #13\n")


def test_tick_is_pm_only(tmp_path):
    fake, orch, *_ = make_product(tmp_path, role="orchestrator")
    with pytest.raises(DispatchError, match="PM"):
        tick(orch, now=NOW, env={})


def test_a_stale_lease_cannot_release_another_groups_fresh_claim(tmp_path):
    fake, codex, repo, origin = make_product(tmp_path, group="codex")
    claude = load_context(repo / ".meld.toml", env=env_for(tmp_path, group="claude", role="pm"), run=fake, adapter_for=no_skills)
    issues = fixture("issues.json")
    for i in issues:
        if i["number"] == 13:  # codex's in-progress claim (assigned to meld-codex-bot in the fixture)
            i["labels"] = [l for l in i["labels"] if l["name"] != "meld:in-review"] + [{"name": "meld:in-progress"}]
    fake.route("GET", R + "issues", issues)
    # claude leased #13 earlier, released it without a comment, and the lease has since expired
    comments = fixture("comments_10.json") + [{"id": 120, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:30:00Z",
                                               "body": "[claude:pm] LEASE #13\n\nuntil: 2026-09-03T11:00:00Z\nrun: claude/#13"}]
    fake.route("GET", R + "issues/10/comments", comments)
    fake.route("DELETE", R + "issues/13/labels/meld%3Aclaimed-by%3Acodex", {})
    fake.route("POST", R + "issues/13/labels", [])
    fake.route("DELETE", R + "issues/13/labels/meld%3Ain-progress", [])
    assert tick(codex, now=NOW, env={})["expired"] == []                       # codex's own claim is not "expired"
    _, board = build_status(claude)
    assert board.leases == {} and issue_refusal(board, claude.cfg, claude.actor(), Verb.RELEASE, board.issues[13], claude.models).rule == \
        "issue is not claimed by claude"
    with pytest.raises(Refused, match="not claimed by claude"):
        release(claude, 13)                                             # claude cannot cite its own dead lease
    assert not [c for c in fake.calls if c.method == "DELETE" and c.endpoint == R + "issues/13/labels/meld%3Aclaimed-by%3Acodex"]


def test_tick_notifies_needs_human_items_when_a_webhook_is_configured(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    sent = []
    import meld.dispatch as dispatch_mod
    env = {"MELD_SLACK_DM_WEBHOOK": "https://hooks.slack.test/abc"}
    fake.route("POST", R + "issues/14/labels", [], status=200)
    out = tick(ctx, now=NOW, env=env, post=lambda u, p: sent.append(p))
    assert out["notified"] == [14] and len(sent) == 1
    # GitHub now reports the label tick just added: the shared meld:nudged event is what throttles the repeat, not a local file.
    fake.route("GET", R + "issues/14/events", [{"id": 501, "event": "labeled", "label": {"name": "meld:nudged"},
                                                 "actor": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T12:00:00Z"}])
    assert tick(ctx, now=NOW, env=env, post=lambda u, p: sent.append(p))["notified"] == []


def test_tick_with_an_explicit_env_never_reaches_the_webhook_in_the_process_environment(tmp_path, monkeypatch):
    # F6: the fixture board has a needs_human issue (#14), so a tick that fell back to os.environ would post to a real webhook.
    fake, ctx, repo, origin = make_product(tmp_path)
    monkeypatch.setenv("MELD_SLACK_DM_WEBHOOK", "https://hooks.slack.test/never")
    sent: list[str] = []
    monkeypatch.setattr("meld.notifier._post_webhook", lambda url, payload: sent.append(url))
    out = tick(ctx, now=NOW, env={})
    assert out["notified"] == [] and sent == []


def test_tick_runs_the_housekeeping_sweeps(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    for n in (11, 12, 13, 14):
        fake.route("GET", R + f"issues/{n}/events", [])
    out = tick(ctx, now=NOW, env={})
    assert out["ci_red"] == [] and out["off_protocol"] == [] and out["ignored"]["flagged"] == [103] and out["notified"] == []


def test_kill_run_skips_a_pid_that_leads_no_process_group(monkeypatch):
    import os, signal, subprocess, sys
    from meld.board import RunRecord
    from meld.dispatch import kill_run
    from tests.factories import mk_run
    leader = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    rec = RunRecord(**{**mk_run("claude", 1).__dict__, "pid": leader.pid})
    try:
        real_getpgid = os.getpgid
        # pretend the worker's pid has been reused by a process that leads no group: it must not be signalled
        monkeypatch.setattr(os, "getpgid", lambda pid: real_getpgid(pid) + 1 if pid == leader.pid else real_getpgid(pid))
        assert kill_run(rec, grace_seconds=1) == "gone"
        assert leader.poll() is None
        monkeypatch.undo()
        assert kill_run(rec, grace_seconds=5) == "terminated"  # the real session leader is still killed as before
    finally:
        if leader.poll() is None:
            os.killpg(leader.pid, signal.SIGKILL)
        leader.wait()
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])  # in our own group
    try:
        assert kill_run(RunRecord(**{**mk_run("claude", 1).__dict__, "pid": bystander.pid}), grace_seconds=1) == "gone"
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait()
    assert kill_run(mk_run("claude", 2, alive=True), grace_seconds=1) == "gone"  # pid 4242: dead or not ours
