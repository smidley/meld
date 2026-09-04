import json
from pathlib import Path

from meld.runners import launch_role, role_env, run_group
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.fakeadapter import FakeAdapter
from tests.fakegh import FakeGh
from meld.context import load_context


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
