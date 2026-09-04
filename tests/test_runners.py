import json
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.runners import launch_role, role_env, run_group
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.fakeadapter import FakeAdapter
from tests.fakegh import FakeGh
from meld.context import load_context
from meld.state import pid_alive


def make(tmp_path: Path):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    env = env_for(tmp_path, role="orchestrator")
    ctx = load_context(tmp_path / ".meld.toml", env=env, run=fake, adapter_for=lambda name: FakeAdapter())
    return ctx, env


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_role_env_carries_identity_and_tokens(tmp_path):
    ctx, env = make(tmp_path)
    e = role_env(ctx, ctx.cfg.groups["claude"], "pm", base_env=env)
    assert e["MELD_GROUP"] == "claude" and e["MELD_ROLE"] == "pm" and e["MELD_TOKEN_CLAUDE"] == "tok-c" and e["MELD_HOME"] == str(tmp_path / "home")


def test_launch_role_builds_the_spec(tmp_path):
    ctx, env = make(tmp_path)
    adapter = FakeAdapter()
    rp = launch_role(ctx, ctx.cfg.groups["claude"], adapter, "orchestrator", base_env=env)
    spec = adapter.launches[0]
    assert spec.role == "orchestrator" and spec.model == "claude-opus-5" and spec.effort == "high" and spec.cwd == tmp_path
    assert spec.prompt.startswith("# Meld: how every long-lived role works") and "# You are the orchestrator" in spec.prompt
    assert spec.timeout_seconds == 4 * 3600 and spec.env["MELD_ROLE"] == "orchestrator" and spec.log_path.parent == tmp_path / "home" / "logs" / "claude"
    rp.proc.wait(timeout=10)


def test_run_group_once_launches_both_roles_and_returns_when_they_exit(tmp_path):
    ctx, env = make(tmp_path)
    adapter = FakeAdapter(script="import sys; sys.exit(0)")
    clock = Clock()
    out = run_group(ctx, adapter=adapter, once=True, interval=0.05, base_env=env)
    assert sorted(s.role for s in adapter.launches) == ["orchestrator", "pm"]
    assert out["group"] == "claude" and out["roles"]["pm"]["launches"] == 1 and out["roles"]["pm"]["last_exit"] == 0


def test_run_group_restarts_exited_roles_with_backoff(tmp_path):
    ctx, env = make(tmp_path)
    adapter = FakeAdapter(script="import sys; sys.exit(1)")
    clock = Clock()
    slept: list[float] = []

    def sleep(s):
        slept.append(s)
        clock.sleep(s)
        import time
        time.sleep(0.05)  # let the tiny real processes exit

    out = run_group(ctx, adapter=adapter, cycles=4, interval=0.01, sleep=sleep, clock=clock, base_env=env)
    assert out["roles"]["orchestrator"]["launches"] >= 2 and out["roles"]["orchestrator"]["last_exit"] == 1
    assert 5.0 in slept  # first backoff is 5 s (virtual)


@dataclass
class RecordingAdapter(FakeAdapter):
    """A FakeAdapter that also remembers the Popen objects launch() returned."""
    procs: list = field(default_factory=list)

    def launch(self, spec):
        proc = super().launch(spec)
        self.procs.append(proc)
        return proc


@dataclass
class FlakyAdapter(FakeAdapter):
    """A FakeAdapter whose second launch() call raises, simulating a mid-startup failure."""
    calls: int = 0
    procs: list = field(default_factory=list)

    def launch(self, spec):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("boom")
        proc = super().launch(spec)
        self.procs.append(proc)
        return proc


def test_run_group_stops_promptly_and_reports_signal_exit_on_shutdown(tmp_path):
    ctx, env = make(tmp_path)
    adapter = RecordingAdapter(script="import time; time.sleep(30)")
    prev_term = signal.getsignal(signal.SIGTERM)
    prev_int = signal.getsignal(signal.SIGINT)
    calls = {"n": 0}

    def sleep(s):
        calls["n"] += 1
        if calls["n"] == 2:
            os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(s)

    started = time.monotonic()
    out = run_group(ctx, adapter=adapter, interval=0.05, base_env=env, sleep=sleep)
    elapsed = time.monotonic() - started

    assert elapsed < 15.0  # the backoff/interval sleep must be cut short, not ridden out
    assert out["roles"]["orchestrator"]["last_exit"] == -signal.SIGTERM
    assert out["roles"]["pm"]["last_exit"] == -signal.SIGTERM
    assert len(adapter.procs) == 2
    for proc in adapter.procs:
        assert not pid_alive(proc.pid)
    assert signal.getsignal(signal.SIGTERM) == prev_term
    assert signal.getsignal(signal.SIGINT) == prev_int


def test_run_group_cleans_up_first_role_when_second_launch_fails(tmp_path):
    ctx, env = make(tmp_path)
    adapter = FlakyAdapter(script="import time; time.sleep(30)")
    prev_term = signal.getsignal(signal.SIGTERM)
    prev_int = signal.getsignal(signal.SIGINT)

    with pytest.raises(RuntimeError, match="boom"):
        run_group(ctx, adapter=adapter, interval=0.05, base_env=env)

    assert len(adapter.procs) == 1  # the second (failed) launch never produced a process to track
    assert not pid_alive(adapter.procs[0].pid)  # but the first role's child was cleaned up, not orphaned
    assert signal.getsignal(signal.SIGTERM) == prev_term
    assert signal.getsignal(signal.SIGINT) == prev_int


def test_cli_run_rejects_unknown_group_cleanly(tmp_path, monkeypatch):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    monkeypatch.setattr(cli, "RUN", fake)
    monkeypatch.setattr(cli, "ADAPTER_FOR", lambda name: FakeAdapter())
    result = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "run", "nope", "--once"],
                                env=env_for(tmp_path))
    assert result.exit_code == 1
    assert "Traceback" not in result.output
