import json
from pathlib import Path

from click.testing import CliRunner

from meld.context import load_context
from meld.waiting import wait_for_change
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.fakegh import FakeGh
import meld.cli as cli


def make_ctx(tmp_path):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    ctx = load_context(tmp_path / ".meld.toml", env=env_for(tmp_path), run=fake)
    return fake, ctx


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_first_call_returns_initial_snapshot(tmp_path):
    fake, ctx = make_ctx(tmp_path)
    clock = Clock()
    out = wait_for_change(ctx, timeout=300, interval=30, sleep=clock.sleep, clock=clock)
    assert out["changes"] == ["initial snapshot"] and out["timed_out"] is False
    assert ctx.store.load_snapshot() == out["digest"]


def test_returns_when_board_changes(tmp_path):
    fake, ctx = make_ctx(tmp_path)
    clock = Clock()
    wait_for_change(ctx, timeout=300, interval=30, sleep=clock.sleep, clock=clock)
    issues = json.loads((__import__("pathlib").Path(__file__).parent / "fixtures/board1/issues.json").read_text())
    for i in issues:
        if i["number"] == 11:
            i["labels"] = [l for l in i["labels"] if l["name"] != "meld:ready"] + [{"name": "meld:in-progress"}]
            i["assignees"] = [{"login": "meld-codex-bot"}]

    def sleep_then_mutate(s):
        clock.sleep(s)
        fake.route("GET", "repos/acme/widgets/issues", issues)

    out = wait_for_change(ctx, timeout=300, interval=30, sleep=sleep_then_mutate, clock=clock)
    assert out["timed_out"] is False
    assert "issue #11: ready -> in_progress" in out["changes"]
    assert ctx.store.load_snapshot() == out["digest"]


def test_times_out_without_changes(tmp_path):
    fake, ctx = make_ctx(tmp_path)
    clock = Clock()
    wait_for_change(ctx, timeout=300, interval=30, sleep=clock.sleep, clock=clock)
    out = wait_for_change(ctx, timeout=90, interval=30, sleep=clock.sleep, clock=clock)
    assert out["timed_out"] is True and out["changes"] == [] and out["rate_limited"] is False
    assert clock.t >= 90


def test_rate_limit_backs_off_and_recovers(tmp_path):
    fake, ctx = make_ctx(tmp_path)
    clock = Clock()
    wait_for_change(ctx, timeout=300, interval=30, sleep=clock.sleep, clock=clock)
    fake.rate_limit_next = True
    slept = []

    def sleep(s):
        slept.append(s)
        clock.sleep(s)

    out = wait_for_change(ctx, timeout=100, interval=30, sleep=sleep, clock=clock)
    assert out["timed_out"] is True and slept[0] == 30


def test_last_sleep_is_clamped_to_the_deadline(tmp_path):
    fake, ctx = make_ctx(tmp_path)
    clock = Clock()
    wait_for_change(ctx, timeout=300, interval=30, sleep=clock.sleep, clock=clock)  # store the snapshot
    clock.t = 0.0
    slept = []

    def sleep(s):
        slept.append(s)
        clock.sleep(s)

    out = wait_for_change(ctx, timeout=45, interval=30, sleep=sleep, clock=clock)
    assert out["timed_out"] is True
    assert slept == [30, 15]      # not [30, 30]: the second poll may not overshoot the deadline
    assert clock.t == 45


def test_rate_limit_backoff_is_also_clamped(tmp_path):
    fake, ctx = make_ctx(tmp_path)
    clock = Clock()
    wait_for_change(ctx, timeout=300, interval=30, sleep=clock.sleep, clock=clock)
    clock.t = 0.0
    fake.rate_limit_next = True
    slept = []

    def sleep(s):
        slept.append(s)
        clock.sleep(s)

    wait_for_change(ctx, timeout=10, interval=30, sleep=sleep, clock=clock)
    assert slept[0] == 10 and clock.t == 10


def test_cli_wait_rejects_a_non_positive_interval_and_a_negative_timeout(tmp_path, monkeypatch):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    monkeypatch.setattr(cli, "RUN", FakeGh())
    for args, hint in ((["--interval", "0"], "--interval"), (["--interval", "-1"], "--interval"),
                       (["--timeout", "-1"], "--timeout")):
        result = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "wait", *args],
                                    env=env_for(tmp_path))
        assert result.exit_code == 2, result.output
        assert hint in result.output


def _fixture(name):
    return json.loads((Path(__file__).parent / "fixtures" / "board1" / name).read_text())


def test_filter_ignores_other_kinds_but_still_saves_snapshot(tmp_path):
    fake, ctx = make_ctx(tmp_path)
    clock = Clock()
    wait_for_change(ctx, timeout=300, interval=30, sleep=clock.sleep, clock=clock)
    issues = _fixture("issues.json")
    for i in issues:
        if i["number"] == 11:
            i["labels"] = [l for l in i["labels"] if l["name"] != "meld:ready"] + [{"name": "meld:in-progress"}]
            i["assignees"] = [{"login": "meld-codex-bot"}]

    def sleep_then_mutate(s):
        clock.sleep(s)
        fake.route("GET", "repos/acme/widgets/issues", issues)

    out = wait_for_change(ctx, timeout=90, interval=30, kinds={"comments"}, sleep=sleep_then_mutate, clock=clock)
    assert out["timed_out"] is True and out["changes"] == [] and out["change_records"] == []
    assert ctx.store.load_snapshot()["issues"]["11"]["status"] == "in_progress"

    comments = _fixture("comments_10.json") + [{"id": 105, "user": {"login": "meld-claude-bot"},
                                                 "created_at": "2026-09-03T12:00:00Z", "body": "[claude] ROSTER-CHANGE\n\n+marketing"}]

    def sleep_then_comment(s):
        clock.sleep(s)
        fake.route("GET", "repos/acme/widgets/issues/10/comments", comments)

    out = wait_for_change(ctx, timeout=300, interval=30, kinds={"comments"}, sleep=sleep_then_comment, clock=clock)
    assert out["timed_out"] is False
    assert out["change_records"][0]["kind"] == "comments" and out["changes"] == ["comments: new up to #105"]


def test_cli_wait_filter_validates_kinds(tmp_path, monkeypatch):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    monkeypatch.setattr(cli, "RUN", fake)
    bad = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "wait", "--timeout", "0", "--filter", "issue,bogus"],
                             env=env_for(tmp_path))
    assert bad.exit_code == 2 and "bogus" in bad.output
    ok = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "wait", "--timeout", "0", "--filter", "issue,comments"],
                            env=env_for(tmp_path))
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.output)["changes"] == ["initial snapshot"]
