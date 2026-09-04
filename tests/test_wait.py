import json

from moot.context import load_context
from moot.waiting import wait_for_change
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.fakegh import FakeGh


def make_ctx(tmp_path):
    (tmp_path / ".moot.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    ctx = load_context(tmp_path / ".moot.toml", env=env_for(tmp_path), run=fake)
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
            i["labels"] = [l for l in i["labels"] if l["name"] != "moot:ready"] + [{"name": "moot:in-progress"}]
            i["assignees"] = [{"login": "moot-codex-bot"}]

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
    import moot.cli as cli
    from click.testing import CliRunner
    (tmp_path / ".moot.toml").write_text(PROJECT_TOML)
    monkeypatch.setattr(cli, "RUN", FakeGh())
    for args, hint in ((["--interval", "0"], "--interval"), (["--interval", "-1"], "--interval"),
                       (["--timeout", "-1"], "--timeout")):
        result = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".moot.toml"), "wait", *args],
                                    env=env_for(tmp_path))
        assert result.exit_code == 2, result.output
        assert hint in result.output
