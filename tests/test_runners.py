import json
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.adapters.claude import ClaudeAdapter
from meld.dispatch import DispatchError
from meld.roster import WorkerType
from meld.runners import _StopRequested, freeze_path, launch_role, limit_of, read_freeze, role_env, run_group
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
    return fake, ctx, env


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_role_env_carries_identity_and_tokens(tmp_path):
    _, ctx, env = make(tmp_path)
    e = role_env(ctx, ctx.cfg.groups["claude"], "pm", base_env=env)
    assert e["MELD_GROUP"] == "claude" and e["MELD_ROLE"] == "pm" and e["MELD_TOKEN_CLAUDE"] == "tok-c" and e["MELD_HOME"] == str(tmp_path / "home")


def test_launch_role_builds_the_spec(tmp_path):
    _, ctx, env = make(tmp_path)
    adapter = FakeAdapter()
    rp = launch_role(ctx, ctx.cfg.groups["claude"], adapter, "orchestrator", base_env=env)
    spec = adapter.launches[0]
    assert spec.role == "orchestrator" and spec.model == "claude-opus-5" and spec.effort == "high" and spec.cwd == tmp_path
    assert spec.prompt.startswith("# Meld: how every long-lived role works") and "# You are the orchestrator" in spec.prompt
    assert spec.timeout_seconds == 4 * 3600 and spec.env["MELD_ROLE"] == "orchestrator" and spec.log_path.parent == tmp_path / "home" / "logs" / "claude"
    rp.proc.wait(timeout=10)


def test_run_group_once_launches_both_roles_and_returns_when_they_exit(tmp_path):
    _, ctx, env = make(tmp_path)
    adapter = FakeAdapter(script="import sys; sys.exit(0)")
    clock = Clock()
    out = run_group(ctx, adapter=adapter, once=True, interval=0.05, base_env=env)
    assert sorted(s.role for s in adapter.launches) == ["orchestrator", "pm"]
    assert out["group"] == "claude" and out["roles"]["pm"]["launches"] == 1 and out["roles"]["pm"]["last_exit"] == 0


def test_run_group_restarts_exited_roles_with_backoff(tmp_path):
    _, ctx, env = make(tmp_path)
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
    assert out["roles"]["orchestrator"]["restarts"] >= 1
    assert 5.0 in slept  # first backoff is 5 s (virtual)
    relaunch = next(s for s in adapter.launches if "Launch number: 2" in s.prompt)
    assert "- Reason: `crash` (" in relaunch.prompt


def test_run_group_relaunches_a_clean_exit_after_a_minute_at_once(tmp_path):
    _, ctx, env = make(tmp_path)
    adapter = FakeAdapter(script="import sys; sys.exit(0)")
    clock = Clock()
    slept: list[float] = []

    def sleep(s):
        slept.append(s)
        clock.t += 61.0  # every supervisor pause is a virtual minute: each session ran long enough to count as a clean exit
        time.sleep(0.05)

    out = run_group(ctx, adapter=adapter, cycles=3, interval=0.01, sleep=sleep, clock=clock, base_env=env)
    for role in ("orchestrator", "pm"):
        assert out["roles"][role]["launches"] >= 2 and out["roles"][role]["restarts"] == 0 and out["roles"][role]["last_exit"] == 0
    assert slept and all(s == 0.01 for s in slept)  # only the poll interval, never a backoff
    first = next(s for s in adapter.launches if s.role == "pm")
    assert "- Launch number: 1 (the first launch under this supervisor)\n- Reason: `start` (" in first.prompt
    relaunch = next(s for s in adapter.launches if "Launch number: 2" in s.prompt)
    assert "- Reason: `exit` (" in relaunch.prompt


def test_run_group_throttles_a_quick_zero_exit_without_calling_it_a_crash(tmp_path):
    """A session that exits 0 within a minute is backed off like a crash (no hot loop) but is still told `exit`, not
    `crash`: the first live run's codex PM finished honest 50-second turns and posted RESUMED on every relaunch."""
    _, ctx, env = make(tmp_path)
    adapter = FakeAdapter(script="import sys; sys.exit(0)")
    clock = Clock()
    slept: list[float] = []

    def sleep(s):
        slept.append(s)
        clock.sleep(s)  # the clock only advances by the sleeps themselves: the session died within a minute
        time.sleep(0.05)

    out = run_group(ctx, adapter=adapter, cycles=3, interval=0.01, sleep=sleep, clock=clock, base_env=env)
    assert 5.0 in slept and out["roles"]["orchestrator"]["restarts"] >= 1
    assert "- Reason: `exit` (" in next(s for s in adapter.launches if "Launch number: 2" in s.prompt).prompt


def test_launch_role_reports_launch_number_and_reason_and_refreshes_the_roster(tmp_path):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    skills: list[WorkerType] = []
    ctx = load_context(tmp_path / ".meld.toml", env=env_for(tmp_path, role="orchestrator"), run=fake,
                       adapter_for=lambda name: FakeAdapter(skills=list(skills)))
    adapter = FakeAdapter()
    env = env_for(tmp_path, role="orchestrator")
    rp = launch_role(ctx, ctx.cfg.groups["claude"], adapter, "pm", base_env=env, launch_no=3, reason="recycle")
    assert rp.launches == 3 and "- Launch number: 3\n- Reason: `recycle` (" in adapter.launches[0].prompt
    assert "`marketing`" not in adapter.launches[0].prompt
    skills.append(WorkerType("marketing", "Launch copy.", "skill", None))  # a skill installed while the supervisor runs
    rp2 = launch_role(ctx, ctx.cfg.groups["claude"], adapter, "pm", base_env=env, launch_no=4, reason="exit")
    assert "- `marketing` (skill): Launch copy." in adapter.launches[1].prompt
    with pytest.raises(ValueError, match="reason must be one of"):
        launch_role(ctx, ctx.cfg.groups["claude"], adapter, "pm", base_env=env, launch_no=5, reason="rebooted")
    assert len(adapter.launches) == 2
    for p in (rp, rp2):
        p.proc.wait(timeout=10)


def test_missing_cli_binary_is_a_clean_run_error(tmp_path, monkeypatch):
    _, ctx, env = make(tmp_path)
    absent = ClaudeAdapter(binary=str(tmp_path / "no-such-claude"), home=tmp_path / "no-such-home")
    with pytest.raises(DispatchError, match="cannot start claude: .*no-such-claude"):
        launch_role(ctx, ctx.cfg.groups["claude"], absent, "orchestrator", base_env=env)
    fake = FakeGh()
    load_board1_routes(fake)
    monkeypatch.setattr(cli, "RUN", fake)
    monkeypatch.setattr(cli, "ADAPTER_FOR", lambda name: absent)
    result = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "run", "claude", "--once"], env=env)
    assert result.exit_code == 1 and "Traceback" not in result.output and "cannot start claude" in result.output


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
    _, ctx, env = make(tmp_path)
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


@dataclass
class LateStopAdapter(FakeAdapter):
    """A FakeAdapter whose third launch() raises _StopRequested, standing in for the handler firing in the
    sliver between sleep() returning and _pause clearing the sleeping flag."""
    calls: int = 0
    procs: list = field(default_factory=list)

    def launch(self, spec):
        self.calls += 1
        if self.calls == 3:
            raise _StopRequested()
        proc = super().launch(spec)
        self.procs.append(proc)
        return proc


def test_run_group_treats_a_late_stop_request_as_a_clean_stop(tmp_path):
    _, ctx, env = make(tmp_path)
    adapter = LateStopAdapter(script="import sys; sys.exit(1)")
    prev_term = signal.getsignal(signal.SIGTERM)

    def sleep(s):
        time.sleep(0.05)

    out = run_group(ctx, adapter=adapter, interval=0.01, base_env=env, sleep=sleep)  # returns instead of raising
    assert adapter.calls == 3 and len(adapter.procs) == 2
    assert all(not pid_alive(p.pid) for p in adapter.procs)
    assert set(out["roles"]) == {"orchestrator", "pm"}
    assert signal.getsignal(signal.SIGTERM) == prev_term


def test_run_group_cleans_up_first_role_when_second_launch_fails(tmp_path):
    _, ctx, env = make(tmp_path)
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


# --- a usage-limited role freezes instead of relaunching (plan 9 task 2) -------------------------------

LIMIT_LINE = "You've hit your individual spend limit. It resets at 6pm."
# What a limited role really leaves behind: the limit on stdout, then a non-zero exit -- which is what the
# supervisor used to read as a crash and relaunch every few minutes for 45 minutes.
LIMIT_SCRIPT = (
    "import sys; "
    "sys.stdout.write('{\"type\": \"turn.failed\", \"error\": {\"message\": "
    "\"You have reached your usage limit for this model.\"}}\\n'); "
    "raise SystemExit(1)"
)
NOW = "2026-09-12T02:11:19Z"           # every freeze test injects `wall`, so none of them reads the clock
COMMENTS = "repos/acme/widgets/issues/10/comments"


def _limited_posts(fake, since: int = 0) -> list[str]:
    """The LIMITED comments the supervisor sent. FakeGh records a call whatever the route answers, so a test that
    routes a failure reads the freeze file for the outcome and passes `since` to count only what landed after."""
    return [c.body["body"] for c in fake.calls[since:]
            if c.method == "POST" and "comments" in c.endpoint and "LIMITED" in c.body["body"]]


def _wait_for(adapter):
    """A supervisor pause that waits for the launched sessions instead of sleeping for them."""
    def sleep(_seconds):
        for proc in adapter.procs:
            proc.wait(timeout=10)
    return sleep


def test_limit_of_distinguishes_no_limit_a_timed_limit_and_an_untimed_one(tmp_path):
    from meld.adapters.codex import CodexAdapter
    adapter = CodexAdapter()
    now = NOW

    quiet = tmp_path / "quiet.log"
    quiet.write_text('{"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}\n')
    assert limit_of(quiet, adapter, now) is False

    timed = tmp_path / "timed.log"
    timed.write_text(json.dumps({"type": "turn.failed", "error": {"message": LIMIT_LINE}}) + "\n")
    assert limit_of(timed, adapter, now) == "2026-09-12T18:00:00Z"

    untimed = tmp_path / "untimed.log"
    untimed.write_text(json.dumps({"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}) + "\n")
    assert limit_of(untimed, adapter, now) is None

    assert limit_of(tmp_path / "absent.log", adapter, now) is False


def test_limit_of_reads_an_unterminated_final_line(tmp_path):
    """A role killed mid-write leaves its last line without a newline, and that is the line with the limit."""
    from meld.adapters.codex import CodexAdapter
    path = tmp_path / "torn.log"
    path.write_text(json.dumps({"type": "turn.failed", "error": {"message": LIMIT_LINE}}))  # no trailing newline
    assert limit_of(path, CodexAdapter(), NOW) == "2026-09-12T18:00:00Z"


def test_a_limited_role_freezes_rather_than_relaunching_and_posts_LIMITED_once(tmp_path):
    fake, ctx, env = make(tmp_path)
    fake.route("POST", COMMENTS, {"id": 999, "html_url": "u"})
    adapter = RecordingAdapter(script=LIMIT_SCRIPT)

    out = run_group(ctx, adapter=adapter, cycles=3, interval=0, sleep=_wait_for(adapter), base_env=env,
                    wall=lambda: NOW)

    limited = _limited_posts(fake)
    assert len(limited) == 2, limited                                   # once per freeze, not once per cycle
    assert {b.splitlines()[0] for b in limited} == {"[claude] LIMITED", "[claude:pm] LIMITED"}
    assert all("usage limit" in b for b in limited)
    for role in ("orchestrator", "pm"):
        freeze = read_freeze(freeze_path(ctx.store.home, "claude", role))
        assert freeze is not None and freeze["posted"] is True and freeze["at"] == NOW
        assert "resets_at" not in freeze                                # the CLI named no time, so re-probe
        assert out["roles"][role]["launches"] == 1                       # frozen, so never relaunched


def test_a_freeze_expires_on_the_injected_clock_and_the_role_comes_back_as_limit(tmp_path):
    """The reset instant is compared against an injected now, so this test does not wait for one."""
    fake, ctx, env = make(tmp_path)
    fake.route("POST", COMMENTS, {"id": 999, "html_url": "u"})
    path = freeze_path(ctx.store.home, "claude", "pm")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"resets_at": "2020-01-01T00:00:00Z", "posted": True, "at": "2020-01-01T00:00:00Z"}))

    out = run_group(ctx, adapter=FakeAdapter(), cycles=1, interval=0, sleep=lambda s: None, base_env=env,
                    wall=lambda: NOW)
    assert out["roles"]["pm"]["launches"] == 2
    assert read_freeze(path) is None                                    # a passed freeze is cleared, not re-read
    assert out["roles"]["pm"]["reason"] == "limit"                       # so the role posts RESUMED itself
    assert out["roles"]["orchestrator"]["reason"] == "start"             # one role's freeze is not the other's
    assert not _limited_posts(fake)                                      # a thaw announces nothing


def test_a_live_freeze_survives_a_supervisor_restart_without_posting_again(tmp_path):
    fake, ctx, env = make(tmp_path)
    fake.route("POST", COMMENTS, {"id": 999, "html_url": "u"})
    live = {"resets_at": "2099-01-01T00:00:00Z", "posted": True, "at": NOW}
    paths = {role: freeze_path(ctx.store.home, "claude", role) for role in ("orchestrator", "pm")}
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(live))
    adapter = RecordingAdapter(script=LIMIT_SCRIPT)   # both sessions end in a limit again

    out = run_group(ctx, adapter=adapter, cycles=3, interval=0, sleep=_wait_for(adapter), base_env=env,
                    wall=lambda: NOW)

    assert not [c for c in fake.calls if c.method == "POST" and "LIMITED" in str(c.body)]
    for role, path in paths.items():
        assert out["roles"][role]["launches"] == 1                       # a live freeze is not relaunched
        assert read_freeze(path) == live                                 # nor rewritten, nor announced again


def test_an_untimed_freeze_reprobes_after_limit_retry_minutes(tmp_path):
    fake, ctx, env = make(tmp_path)
    fake.route("POST", COMMENTS, {"id": 999, "html_url": "u"})
    path = freeze_path(ctx.store.home, "claude", "orchestrator")
    path.parent.mkdir(parents=True, exist_ok=True)
    # No resets_at: the freeze is re-probed limit_retry_minutes after `at`, which is long past here.
    path.write_text(json.dumps({"posted": True, "at": "2020-01-01T00:00:00Z"}))

    out = run_group(ctx, adapter=FakeAdapter(), cycles=1, interval=0, sleep=lambda s: None, base_env=env,
                    wall=lambda: NOW)
    assert out["roles"]["orchestrator"]["launches"] == 2
    assert out["roles"]["orchestrator"]["reason"] == "limit"
    assert read_freeze(path) is None


def test_an_untimed_freeze_stands_until_the_cadence_has_passed(tmp_path):
    """The re-probe is due `limit_retry_minutes` after the persisted `at`, not after now: 11 minutes in, the
    freeze still stands, and the role is neither polled nor relaunched."""
    fake, ctx, env = make(tmp_path)
    fake.route("POST", COMMENTS, {"id": 999, "html_url": "u"})           # a wrong post would succeed, and be seen
    path = freeze_path(ctx.store.home, "claude", "orchestrator")
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = {"posted": True, "at": "2026-09-12T02:00:00Z"}               # NOW is 02:11:19; the cadence is 30m
    path.write_text(json.dumps(fresh))

    out = run_group(ctx, adapter=FakeAdapter(), cycles=1, interval=0, sleep=lambda s: None, base_env=env,
                    wall=lambda: NOW)
    assert out["roles"]["orchestrator"]["launches"] == 1
    assert out["roles"]["orchestrator"]["reason"] == "start"
    assert read_freeze(path) == fresh
    assert not _limited_posts(fake)                                      # a standing freeze says nothing new


def test_a_freeze_naming_no_instant_at_all_reads_as_passed(tmp_path):
    """Fail open, not shut: a freeze file with neither `resets_at` nor `at` says nothing about when the quota
    comes back, and a freeze with no end would strand the group for ever."""
    fake, ctx, env = make(tmp_path)
    fake.route("POST", COMMENTS, {"id": 999, "html_url": "u"})
    path = freeze_path(ctx.store.home, "claude", "orchestrator")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"posted": True}))

    out = run_group(ctx, adapter=FakeAdapter(), cycles=1, interval=0, sleep=lambda s: None, base_env=env,
                    wall=lambda: NOW)
    assert out["roles"]["orchestrator"]["reason"] == "limit" and read_freeze(path) is None


def test_a_notice_that_could_not_be_posted_is_retried_while_the_freeze_stands(tmp_path):
    """A supervisor rate-limited at the moment a quota runs out must not leave the board silent for the whole
    window -- that is the original symptom. The freeze is honoured either way; the notice is retried."""
    fake, ctx, env = make(tmp_path)
    fake.route("POST", COMMENTS, {"message": "boom"}, status=500)         # the board is unreachable
    adapter = RecordingAdapter(script=LIMIT_SCRIPT)

    out = run_group(ctx, adapter=adapter, cycles=2, interval=0, sleep=_wait_for(adapter), base_env=env,
                    wall=lambda: NOW)

    assert _limited_posts(fake), "the supervisor tried to announce the freeze"
    for role in ("orchestrator", "pm"):
        freeze = read_freeze(freeze_path(ctx.store.home, "claude", role))
        assert freeze is not None and freeze["posted"] is False           # recorded as unannounced
        assert out["roles"][role]["launches"] == 1                        # and frozen all the same

    fake.route("POST", COMMENTS, {"id": 999, "html_url": "u"})            # the board comes back
    mark = len(fake.calls)
    adapter2 = RecordingAdapter(script=LIMIT_SCRIPT)
    out2 = run_group(ctx, adapter=adapter2, cycles=3, interval=0, sleep=_wait_for(adapter2), base_env=env,
                     wall=lambda: NOW)

    landed = _limited_posts(fake, mark)
    assert len(landed) == 2, landed                                       # one per role, and not again per cycle
    assert {b.splitlines()[0] for b in landed} == {"[claude] LIMITED", "[claude:pm] LIMITED"}
    for role in ("orchestrator", "pm"):
        assert read_freeze(freeze_path(ctx.store.home, "claude", role))["posted"] is True
        assert out2["roles"][role]["launches"] == 1                       # still frozen, still not relaunched
