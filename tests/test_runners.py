import json
import os
import sys
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
from meld.runners import (MAX_FREEZE_MINUTES, _StopRequested, freeze_path, launch_role, limit_of, read_freeze,
                          read_record, role_env, role_run_path, run_group, supervisor_path)
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
    # plan 43: the role's own git authenticates as the group through a helper that asks `meld token`, after the machine's helpers are cleared
    pairs = [(e[f"GIT_CONFIG_KEY_{i}"], e[f"GIT_CONFIG_VALUE_{i}"]) for i in range(int(e["GIT_CONFIG_COUNT"]))]
    assert pairs == [("credential.helper", ""), ("credential.helper", '!f() { echo username=x-access-token; echo "password=$(meld token)"; }; f')]
    assert e["GIT_TERMINAL_PROMPT"] == "0"
    e2 = role_env(ctx, ctx.cfg.groups["claude"], "pm", base_env={**env, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "x.y", "GIT_CONFIG_VALUE_0": "z"})
    assert e2["GIT_CONFIG_COUNT"] == "3" and e2["GIT_CONFIG_KEY_0"] == "x.y" and e2["GIT_CONFIG_KEY_2"] == "credential.helper"


def test_cli_token_prints_the_groups_credential_bare_and_only_for_a_role(tmp_path, monkeypatch):
    """Plan 43: `meld token` is what the role's git credential helper runs; the output is the token and nothing else."""
    from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
    from tests.fakegh import FakeGh
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    fake.route("GET", "user", {"login": "empowerScott"})
    monkeypatch.setattr(cli, "RUN", fake)
    out = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "token"], env=env_for(tmp_path, role="orchestrator"))
    assert out.exit_code == 0 and out.output == "tok-c"
    out = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "token"], env=env_for(tmp_path, group=None))
    assert out.exit_code != 0 and "only a group" in out.output
    out = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "token"], env={**env_for(tmp_path), "MELD_WORKER": "1"})
    assert out.exit_code != 0 and "tok-c" not in out.output


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


def test_run_group_relaunches_a_clean_exit_no_sooner_than_one_wait_timeout_after_its_launch(tmp_path):
    """Plan 39: a clean exit after real work is still the steady state (restarts stay 0, the reason is `exit`), but the
    relaunch waits until polling.wait_timeout_seconds have passed since the launch: the scratch product's codex
    orchestrator ended 45-second turns and launched 649 times overnight."""
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
    floor = ctx.cfg.wait_timeout_seconds
    assert any(0 < s < floor and s != 0.01 for s in slept) and all(s <= floor for s in slept)   # the remainder of the wait timeout, never a backoff
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
    """`FlakyAdapter` raises `RuntimeError`, not an `OSError`: the per-role boundary catches `OSError` only, so
    a programming error still reaches the caller with the group torn down rather than being backed off for ever."""
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
    assert out["roles"]["pm"]["launches"] == 1                           # the freeze was cleared before its first launch
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
        assert out["roles"][role]["launches"] == 0                       # a live freeze is never launched into
        assert read_freeze(path) == live                                 # nor rewritten, nor announced again
        # The report says the role is frozen and until when, so a frozen role is not read as one that failed to
        # launch -- and the instant it names is the six-hour cap, not the 2099 the freeze file claims.
        assert out["roles"][role]["frozen_until"] == "2026-09-20T02:11:19Z"


def test_an_untimed_freeze_reprobes_after_limit_retry_minutes(tmp_path):
    fake, ctx, env = make(tmp_path)
    fake.route("POST", COMMENTS, {"id": 999, "html_url": "u"})
    path = freeze_path(ctx.store.home, "claude", "orchestrator")
    path.parent.mkdir(parents=True, exist_ok=True)
    # No resets_at: the freeze is re-probed limit_retry_minutes after `at`, which is long past here.
    path.write_text(json.dumps({"posted": True, "at": "2020-01-01T00:00:00Z"}))

    out = run_group(ctx, adapter=FakeAdapter(), cycles=1, interval=0, sleep=lambda s: None, base_env=env,
                    wall=lambda: NOW)
    assert out["roles"]["orchestrator"]["launches"] == 1                  # cleared, then launched, in one cycle
    assert out["roles"]["orchestrator"]["reason"] == "limit"
    assert read_freeze(path) is None


def test_an_untimed_freeze_stands_until_the_cadence_has_passed(tmp_path):
    """The re-probe is due `limit_retry_minutes` after the persisted `at`, not after now: 11 minutes in, the
    freeze still stands, and the role is neither polled nor launched."""
    fake, ctx, env = make(tmp_path)
    fake.route("POST", COMMENTS, {"id": 999, "html_url": "u"})           # a wrong post would succeed, and be seen
    path = freeze_path(ctx.store.home, "claude", "orchestrator")
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = {"posted": True, "at": "2026-09-12T02:00:00Z"}               # NOW is 02:11:19; the cadence is 30m
    path.write_text(json.dumps(fresh))

    out = run_group(ctx, adapter=FakeAdapter(), cycles=1, interval=0, sleep=lambda s: None, base_env=env,
                    wall=lambda: NOW)
    assert out["roles"]["orchestrator"]["launches"] == 0                  # not even the supervisor's first launch
    assert out["roles"]["orchestrator"]["reason"] is None                 # it has no process to have a reason
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


def test_a_notice_that_could_not_be_posted_is_recorded_and_the_freeze_stands(tmp_path):
    """A supervisor rate-limited at the moment a quota runs out must not leave the board silent for the whole
    window -- that is the original symptom. The freeze is honoured either way, the failure is recorded, and the
    attempt is *not* repeated on the next poll: one attempt is `find_active_milestone`'s paginated reads plus a
    POST, aimed at a board that is already failing, and the poll interval is five seconds."""
    fake, ctx, env = make(tmp_path)
    fake.route("POST", COMMENTS, {"message": "boom"}, status=500)         # the board is unreachable
    adapter = RecordingAdapter(script=LIMIT_SCRIPT)

    out = run_group(ctx, adapter=adapter, cycles=4, interval=0, sleep=_wait_for(adapter), base_env=env,
                    wall=lambda: NOW)

    attempts = _limited_posts(fake)
    assert len(attempts) == 2, attempts                                   # one per role for four cycles, not four
    for role in ("orchestrator", "pm"):
        freeze = read_freeze(freeze_path(ctx.store.home, "claude", role))
        assert freeze is not None and freeze["posted"] is False           # recorded as unannounced
        assert freeze["announce_attempted_at"] == NOW                     # and when the attempt was made
        assert out["roles"][role]["launches"] == 1                        # and frozen all the same
        assert out["roles"][role]["frozen_until"] == "2026-09-12T02:41:19Z"   # `at` plus the 30-minute cadence


def test_an_unposted_notice_is_retried_on_the_cadence_and_stops_once_it_lands(tmp_path):
    """The retry runs on `runners.limit_retry_minutes`, not on every poll, and the last attempt is persisted so
    the cadence holds across a supervisor restart. The freeze here names a reset four hours out, so it outlives
    several cadences -- an untimed freeze re-probes at the first one and the relaunch posts for itself."""
    fake, ctx, env = make(tmp_path)
    fake.route("POST", COMMENTS, {"message": "boom"}, status=500)         # the board is still unreachable
    path = freeze_path(ctx.store.home, "claude", "orchestrator")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": NOW, "resets_at": "2026-09-12T06:11:19Z", "posted": False}))

    run_group(ctx, adapter=FakeAdapter(), cycles=6, interval=0, sleep=lambda s: None, base_env=env,
              wall=lambda: NOW)
    assert len(_limited_posts(fake)) == 1                                 # six cycles, one attempt
    assert read_freeze(path) == {"at": NOW, "resets_at": "2026-09-12T06:11:19Z", "posted": False,
                                "announce_attempted_at": NOW}             # written even though it failed

    mark, later = len(fake.calls), "2026-09-12T02:42:19Z"                 # NOW + 31 minutes: the cadence passed
    run_group(ctx, adapter=FakeAdapter(), cycles=4, interval=0, sleep=lambda s: None, base_env=env,
              wall=lambda: later)
    assert len(_limited_posts(fake, mark)) == 1, "exactly one more attempt, not one per cycle"
    assert read_freeze(path)["announce_attempted_at"] == later
    assert read_freeze(path)["posted"] is False

    fake.route("POST", COMMENTS, {"id": 999, "html_url": "u"})            # the board comes back
    mark, landed_at = len(fake.calls), "2026-09-12T03:13:19Z"
    run_group(ctx, adapter=FakeAdapter(), cycles=4, interval=0, sleep=lambda s: None, base_env=env,
              wall=lambda: landed_at)
    landed = _limited_posts(fake, mark)
    assert len(landed) == 1 and landed[0].splitlines()[0] == "[claude] LIMITED"
    assert read_freeze(path)["posted"] is True

    mark = len(fake.calls)
    run_group(ctx, adapter=FakeAdapter(), cycles=4, interval=0, sleep=lambda s: None, base_env=env,
              wall=lambda: "2026-09-12T05:00:00Z")
    assert not _limited_posts(fake, mark)                                 # a notice that landed is never re-posted


def test_a_named_reset_is_believed_only_up_to_the_eight_day_cap(tmp_path):
    """A CLI's limit message is prose, and one stray instant in it ("...the milestone closes 2026-12-01") would
    otherwise freeze a role for months: the freeze is persisted, survives every restart, is re-probed by nothing,
    and no local surface shows it. The named instant is honoured only as far as the cap."""
    assert MAX_FREEZE_MINUTES == 8 * 24 * 60                               # eight days, from NOW = 2026-09-12T02:11:19 (plan 38: six hours re-probed a week-long quota 48 times)
    fake, ctx, env = make(tmp_path)
    fake.route("POST", COMMENTS, {"id": 999, "html_url": "u"})
    path = freeze_path(ctx.store.home, "claude", "pm")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"resets_at": "2027-09-12T02:11:19Z", "posted": True, "at": NOW}))

    out = run_group(ctx, adapter=FakeAdapter(), cycles=1, interval=0, sleep=lambda s: None, base_env=env,
                    wall=lambda: "2026-09-12T07:11:19Z")                  # five hours in: still frozen
    assert out["roles"]["pm"]["launches"] == 0 and read_freeze(path) is not None
    assert out["roles"]["pm"]["frozen_until"] == "2026-09-20T02:11:19Z"

    out = run_group(ctx, adapter=FakeAdapter(), cycles=1, interval=0, sleep=lambda s: None, base_env=env,
                    wall=lambda: "2026-09-20T02:12:19Z")                  # eight days and a minute in: re-probed
    assert out["roles"]["pm"]["reason"] == "limit" and read_freeze(path) is None
    assert out["roles"]["pm"]["frozen_until"] is None
    assert not _limited_posts(fake)                                       # a thaw announces nothing


def test_the_cap_never_shortens_a_window_the_cli_really_named(tmp_path):
    """The cap is a ceiling on belief, not a floor on the wait: a two-hour reset thaws at two hours."""
    fake, ctx, env = make(tmp_path)
    path = freeze_path(ctx.store.home, "claude", "pm")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"resets_at": "2026-09-12T04:11:19Z", "posted": True, "at": NOW}))

    out = run_group(ctx, adapter=FakeAdapter(), cycles=1, interval=0, sleep=lambda s: None, base_env=env,
                    wall=lambda: "2026-09-12T04:00:00Z")                  # eleven minutes to go
    assert out["roles"]["pm"]["launches"] == 0
    assert out["roles"]["pm"]["frozen_until"] == "2026-09-12T04:11:19Z"   # its own instant, not the cap

    out = run_group(ctx, adapter=FakeAdapter(), cycles=1, interval=0, sleep=lambda s: None, base_env=env,
                    wall=lambda: "2026-09-12T04:12:19Z")
    assert out["roles"]["pm"]["reason"] == "limit" and read_freeze(path) is None


@pytest.mark.parametrize("resets", ["later", 12345, ["2026-09-12T03:00:00Z"]])
def test_a_resets_at_that_is_not_an_instant_fails_open(tmp_path, resets):
    """A corrupted or hand-edited `resets_at` must fail open exactly as `at` already does. It goes through the one
    parser, so a string that is not an instant and a JSON number both leave the deadline to `at` plus the re-probe
    cadence -- long passed here -- instead of raising `TypeError` or stranding the role for ever."""
    fake, ctx, env = make(tmp_path)
    fake.route("POST", COMMENTS, {"id": 999, "html_url": "u"})
    path = freeze_path(ctx.store.home, "claude", "orchestrator")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"resets_at": resets, "posted": True, "at": "2020-01-01T00:00:00Z"}))

    out = run_group(ctx, adapter=FakeAdapter(), cycles=1, interval=0, sleep=lambda s: None, base_env=env,
                    wall=lambda: NOW)
    assert out["roles"]["orchestrator"]["reason"] == "limit" and read_freeze(path) is None
    assert out["roles"]["orchestrator"]["frozen_until"] is None


# --- a launch that cannot start backs off instead of ending the run (plan 9 task 3) --------------------

THAWED = "2026-09-12T09:00:00Z"        # well past NOW plus the 30-minute re-probe cadence


def test_a_launch_that_cannot_start_backs_off_and_keeps_the_run_alive(tmp_path):
    _, ctx, env = make(tmp_path)

    class Refusing:
        """An adapter whose launch fails the way a full disk fails, then recovers."""
        name = "refusing"

        def __init__(self, fail_times: int):
            self.left = fail_times
            self.ok = FakeAdapter()

        def launch(self, spec):
            if self.left > 0:
                self.left -= 1
                raise OSError(28, "No space left on device")
            return self.ok.launch(spec)

        def __getattr__(self, item):                 # everything else behaves like the fake
            return getattr(self.ok, item)

    slept: list[float] = []
    out = run_group(ctx, adapter=Refusing(fail_times=2), cycles=3, interval=0,
                    sleep=slept.append, clock=Clock(), wall=lambda: NOW, base_env=env)

    assert slept, "a failed launch must back off rather than spin"
    assert out["group"] == "claude"                  # the run returned normally instead of raising
    assert out["roles"]["orchestrator"]["launch_errors"] >= 1


def test_one_roles_launch_failure_does_not_take_the_other_role_down(tmp_path):
    _, ctx, env = make(tmp_path)

    class HalfBroken:
        name = "half"

        def __init__(self):
            self.ok = FakeAdapter()

        def launch(self, spec):
            if spec.role == "orchestrator":
                raise OSError(28, "No space left on device")
            return self.ok.launch(spec)

        def __getattr__(self, item):
            return getattr(self.ok, item)

    out = run_group(ctx, adapter=HalfBroken(), cycles=2, interval=0, sleep=lambda s: None,
                    clock=Clock(), wall=lambda: NOW, base_env=env)
    assert out["roles"]["pm"]["launches"] >= 1        # the healthy role ran
    assert out["roles"]["orchestrator"]["launch_errors"] >= 1


def test_a_supervisor_started_inside_a_live_freeze_never_launches_the_frozen_role(tmp_path):
    """A restart inside a live freeze must not burn a launch on a role whose quota is known to be gone: the
    initial launches go through the same guarded per-role path as every relaunch, so there is no unguarded
    gap before the loop starts."""
    fake, ctx, env = make(tmp_path)
    path = freeze_path(ctx.store.home, "claude", "orchestrator")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"resets_at": "2099-01-01T00:00:00Z", "posted": True, "at": "2026-09-12T02:11:19Z"}))
    adapter = FakeAdapter()

    out = run_group(ctx, adapter=adapter, cycles=2, interval=0, sleep=lambda s: None,
                    clock=Clock(), wall=lambda: "2026-09-12T02:11:19Z", base_env=env)

    assert [s.role for s in adapter.launches] == ["pm"]          # the healthy role only, ever
    assert out["roles"]["orchestrator"]["launches"] == 0
    assert out["roles"]["pm"]["launches"] >= 1


class _PollGuard:
    """A process that refuses to be polled while its role's freeze stands.

    Deleting `run_group`'s freeze check while keeping its thaw branch leaves every other freeze test green --
    Task 2's review proved that, because a relaunch reads the same either way. So the proof that the check is
    still the FIRST thing done for a role is a process that screams when the loop reaches past it.
    """

    def __init__(self, proc, freeze: Path):
        self._proc = proc
        self._freeze = freeze

    def poll(self):
        if self._freeze.exists():
            raise AssertionError("polled a frozen role")
        return self._proc.poll()

    def __getattr__(self, item):
        return getattr(self.__dict__["_proc"], item)    # __dict__, so a missing _proc cannot recurse


@dataclass
class GuardedAdapter(FakeAdapter):
    """A FakeAdapter whose processes are _PollGuards over this group's freeze files."""
    home: Path | None = None
    procs: list = field(default_factory=list)

    def launch(self, spec):
        proc = _PollGuard(super().launch(spec), freeze_path(self.home, "claude", spec.role))
        self.procs.append(proc)
        return proc


def test_a_frozen_role_is_never_polled_while_its_freeze_stands(tmp_path):
    """One run through the whole freeze: both roles launch, hit a limit, stand frozen for a cycle without
    being touched at all, and come back as `limit` once the window passes."""
    fake, ctx, env = make(tmp_path)
    fake.route("POST", COMMENTS, {"id": 999, "html_url": "u"})
    adapter = GuardedAdapter(script=LIMIT_SCRIPT, home=ctx.store.home)
    ticks = {"n": 0}

    def sleep(_seconds):
        ticks["n"] += 1
        for proc in adapter.procs:
            proc.wait(timeout=10)        # the exits are waited for, not slept for, so the cycles are exact

    # cycle 1 launches, cycle 2 reads the limit and freezes, cycle 3 must leave the freeze alone, cycle 4 thaws.
    out = run_group(ctx, adapter=adapter, cycles=4, interval=0, sleep=sleep, clock=Clock(), base_env=env,
                    wall=lambda: NOW if ticks["n"] < 3 else THAWED)

    assert len(_limited_posts(fake)) == 2                                          # it really did freeze
    for role in ("orchestrator", "pm"):
        assert read_freeze(freeze_path(ctx.store.home, "claude", role)) is None    # stood, then passed
        assert out["roles"][role]["launches"] == 2                                 # one launch, one thaw
        assert out["roles"][role]["reason"] == "limit"


def test_a_failed_relaunch_still_owes_the_launch_it_was_making(tmp_path):
    """A launch that could not be made changes nothing about what the role is owed: the same launch number,
    the same reason, the same exit behind it. Otherwise a role that had crashed three times would come back
    as a first launch and say nothing about it."""
    _, ctx, env = make(tmp_path)

    class BreaksOnRelaunch:
        """Every launch works except the orchestrator's second attempt, which fails the way a full disk does."""
        name = "breaks"

        def __init__(self):
            self.ok = FakeAdapter(script="import sys; sys.exit(1)")
            self.tried: list = []
            self.procs: list = []

        def launch(self, spec):
            self.tried.append(spec)
            if spec.role == "orchestrator" and len([s for s in self.tried if s.role == spec.role]) == 2:
                raise OSError(28, "No space left on device")
            proc = self.ok.launch(spec)
            self.procs.append(proc)
            return proc

        def __getattr__(self, item):
            return getattr(self.ok, item)

    adapter = BreaksOnRelaunch()
    # cycle 1 launches both, cycle 2 reads the crash and cannot relaunch the orchestrator, cycle 3 can.
    out = run_group(ctx, adapter=adapter, cycles=3, interval=0, sleep=_wait_for(adapter), clock=Clock(),
                    wall=lambda: NOW, base_env=env)

    made = [s for s in adapter.tried if s.role == "orchestrator"]
    assert len(made) == 3                                                 # a launch, a refusal, a launch
    assert "- Launch number: 2\n- Reason: `crash` (" in made[2].prompt    # the very launch it was owed
    assert out["roles"]["orchestrator"]["launches"] == 2                  # of which two were made
    assert out["roles"]["orchestrator"]["restarts"] == 1
    assert out["roles"]["orchestrator"]["launch_errors"] == 1
    assert out["roles"]["orchestrator"]["last_launch_error"].startswith("cannot start breaks: ")


# --- one OSError boundary around the whole per-role step (plan 9 fix wave) -----------------------------


def test_a_freeze_the_supervisor_cannot_write_does_not_end_the_run(tmp_path):
    """Everything a role's turn does sits behind one `except OSError`, not just its launch. Here the launch
    works, the role hits a limit, and the freeze file cannot be written because its directory is read-only --
    a bare `OSError` out of `write_atomic` that used to end the supervisor, which is the failure this plan
    exists to remove. The role's cycle is recorded as failed, backed off, and the other role still runs."""
    fake, ctx, env = make(tmp_path)
    fake.route("POST", COMMENTS, {"id": 999, "html_url": "u"})
    stuck = freeze_path(ctx.store.home, "claude", "orchestrator").parent
    stuck.mkdir(parents=True, exist_ok=True)
    adapter = RecordingAdapter(script=LIMIT_SCRIPT)

    stuck.chmod(0o500)                                   # readable, so read_freeze works; nothing may be created
    try:
        out = run_group(ctx, adapter=adapter, cycles=3, interval=0, sleep=_wait_for(adapter), base_env=env,
                        wall=lambda: NOW)                # returns instead of raising PermissionError
    finally:
        stuck.chmod(0o700)

    assert out["group"] == "claude"
    assert out["roles"]["orchestrator"]["launch_errors"] >= 1
    assert "cycle failed" in out["roles"]["orchestrator"]["last_launch_error"]
    assert out["roles"]["pm"]["launches"] == 1            # the other role took its turn, and froze normally
    assert read_freeze(freeze_path(ctx.store.home, "claude", "pm"))["posted"] is True


@dataclass
class MissingGh(FakeGh):
    """A `gh` that is no longer on the PATH: `subprocess.run` raises `FileNotFoundError`, which is an `OSError`
    and so is not caught by `_announce`'s `except (GitHubError, BoardError)`."""
    armed: bool = False

    def __call__(self, cmd, input=None, **kw):
        if self.armed:
            raise FileNotFoundError(2, "No such file or directory", "gh")
        return super().__call__(cmd, input=input, **kw)


def test_a_gh_that_vanished_mid_run_does_not_end_the_run(tmp_path):
    """`_announce` reaches `subprocess.run` through `github._request`, so a missing or upgraded `gh` raises an
    `OSError` straight through it. The supervisor records the role's failed cycle and keeps going."""
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = MissingGh()
    load_board1_routes(fake)
    env = env_for(tmp_path, role="orchestrator")
    ctx = load_context(tmp_path / ".meld.toml", env=env, run=fake, adapter_for=lambda name: FakeAdapter())
    adapter = RecordingAdapter(script=LIMIT_SCRIPT)
    fake.armed = True                                    # every gh call from here on is a missing binary

    out = run_group(ctx, adapter=adapter, cycles=2, interval=0, sleep=_wait_for(adapter), base_env=env,
                    wall=lambda: NOW)

    assert out["group"] == "claude"
    for role in ("orchestrator", "pm"):
        assert out["roles"][role]["launch_errors"] >= 1
        assert "No such file or directory" in out["roles"][role]["last_launch_error"]


def test_run_group_records_its_own_pid_and_each_role_launch(tmp_path):
    """The viewer's liveness evidence (spec 22): who the supervisor is, and which process is writing which log."""
    _, ctx, env = make(tmp_path)
    adapter = FakeAdapter(script="import sys; sys.exit(0)")
    home = tmp_path / "home"
    run_group(ctx, adapter=adapter, once=True, interval=0.05, base_env=env,
              wall=lambda: "2026-09-18T12:00:00Z", start_of=lambda pid: f"born-{pid}")
    assert read_record(supervisor_path(home, "claude")) == {
        "pid": os.getpid(), "started_at": "2026-09-18T12:00:00Z", "proc_start": f"born-{os.getpid()}"}
    pm = read_record(role_run_path(home, "claude", "pm"))
    launched = next(s for s in adapter.launches if s.role == "pm")
    assert pm["log_path"] == str(launched.log_path) and pm["reason"] == "start" and pm["launch_no"] == 1
    assert pm["pid"] > 0 and pm["proc_start"] == f"born-{pm['pid']}" and pm["started_at"] == "2026-09-18T12:00:00Z"
    # records outlive the run: a dead pid in one is "confirmed gone", which is more than "no record" can say
    assert supervisor_path(home, "claude").exists() and role_run_path(home, "claude", "orchestrator").exists()


def test_a_relaunch_rewrites_the_role_record_with_its_new_launch(tmp_path):
    _, ctx, env = make(tmp_path)
    adapter = RecordingAdapter(script="import sys; sys.exit(1)")
    clock = Clock()
    wait = _wait_for(adapter)     # wait on the tiny real children, rather than betting 0.05s is enough for them

    def sleep(s):
        clock.sleep(s)
        wait(s)

    out = run_group(ctx, adapter=adapter, cycles=4, interval=0.01, sleep=sleep, clock=clock, base_env=env,
                    start_of=lambda pid: "born")
    rec = read_record(role_run_path(tmp_path / "home", "claude", "orchestrator"))
    assert rec["launch_no"] == out["roles"]["orchestrator"]["launches"] >= 2 and rec["reason"] == "crash"


def test_a_record_that_cannot_be_written_never_costs_a_launch(tmp_path):
    """Observability does not outrank the run: a launch that succeeded is tracked whatever happened to its record,
    or the next cycle would launch the role a second time."""
    _, ctx, env = make(tmp_path)
    state_dir = tmp_path / "home" / "state" / "claude"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_dir.chmod(0o500)
    try:
        adapter = FakeAdapter(script="import sys; sys.exit(0)")
        out = run_group(ctx, adapter=adapter, once=True, interval=0.05, base_env=env, start_of=lambda pid: "born")
    finally:
        state_dir.chmod(0o700)
    assert out["roles"]["pm"]["launches"] == 1 and out["roles"]["orchestrator"]["launches"] == 1
    assert len(adapter.launches) == 2                       # each role launched exactly once: no orphan, no duplicate
    assert out["roles"]["pm"]["launch_errors"] == 0          # a record failing is not the role's cycle failing
    assert read_record(supervisor_path(tmp_path / "home", "claude")) is None


def test_cli_run_detach_ps_and_stop_manage_a_background_supervisor(tmp_path, monkeypatch):
    """Handoff residual 17: the launcher lived outside the repo. `meld run --detach` starts the supervisor in its own
    session with its output on a log, `meld ps` reads the records it keeps, `meld stop` ends it by its record (never a
    reused pid). The detached child here is a sleeping python, never a real CLI; the sleep records nothing, so the
    test writes the record the real supervisor writes at startup."""
    import signal
    from meld.runners import supervisor_path
    from meld.state import pid_alive
    _, ctx, env = make(tmp_path)
    home = tmp_path / "home"
    monkeypatch.setattr(cli, "detach_argv", lambda config_path, group: [sys.executable, "-c", "import time; time.sleep(60)"])
    monkeypatch.setattr(cli, "START_OF", lambda pid: "fixed-start")
    args = ["--config", str(tmp_path / ".meld.toml"), "--compact"]
    result = CliRunner().invoke(cli.main, [*args, "run", "claude", "--detach"], env=env)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    pid = data["pid"]
    try:
        assert data["detached"] is True and data["group"] == "claude" and pid_alive(pid) and Path(data["log"]).exists()
        assert os.getpgid(pid) == pid                                      # its own session: it outlives this shell
        record = supervisor_path(home, "claude")
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({"pid": pid, "started_at": "2026-09-03T12:00:00Z", "proc_start": "fixed-start"}))
        again = CliRunner().invoke(cli.main, [*args, "run", "claude", "--detach"], env=env)
        assert again.exit_code == 1 and "already running" in again.output and f"meld stop claude" in again.output
        exclusive = CliRunner().invoke(cli.main, [*args, "run", "claude", "--detach", "--once"], env=env)
        assert exclusive.exit_code == 1 and "exclusive" in exclusive.output
        ps = json.loads(CliRunner().invoke(cli.main, [*args, "ps"], env=env).output)
        assert ps["claude"]["supervisor"]["state"] == "alive" and ps["claude"]["supervisor"]["pid"] == pid
        assert ps["codex"]["supervisor"]["state"] == "unknown" and ps["claude"]["roles"]["pm"]["state"] == "unknown"
        assert ps["claude"]["roles"]["pm"]["frozen"] is None
        stop = CliRunner().invoke(cli.main, [*args, "stop", "claude"], env=env)
        assert stop.exit_code == 0, stop.output
        assert json.loads(stop.output) == {"group": "claude", "pid": pid, "stopped": True, "how": "term"}
        assert not pid_alive(pid)
        again_stop = json.loads(CliRunner().invoke(cli.main, [*args, "stop", "claude"], env=env).output)
        assert again_stop["stopped"] is False and again_stop["state"] == "dead" and "gone" in again_stop["reason"]
        assert json.loads(CliRunner().invoke(cli.main, [*args, "ps"], env=env).output)["claude"]["supervisor"]["state"] == "dead"
        unknown = CliRunner().invoke(cli.main, [*args, "stop", "bogus"], env=env)
        assert unknown.exit_code == 1 and "unknown group" in unknown.output
    finally:
        if pid_alive(pid):
            os.kill(pid, signal.SIGKILL)


def test_role_env_adds_the_groups_own_variables_under_its_identity(tmp_path):
    from dataclasses import replace
    _, ctx, env = make(tmp_path)
    group = replace(ctx.cfg.groups["claude"], env={"ANTHROPIC_BASE_URL": "http://server:11434", "MELD_GROUP": "codex"},
                    cli_config={"model": "qwen3-coder"})
    e = role_env(ctx, group, "pm", base_env=env)
    assert e["ANTHROPIC_BASE_URL"] == "http://server:11434" and e["MELD_GROUP"] == "claude"
    adapter = FakeAdapter()
    launch_role(ctx, group, adapter, "pm", base_env=env).proc.wait(timeout=10)
    assert adapter.launches[0].cli_config == {"model": "qwen3-coder"} and adapter.launches[0].env["ANTHROPIC_BASE_URL"] == "http://server:11434"


def test_a_re_probe_that_hits_the_limit_again_backs_off_up_to_a_day(tmp_path):
    """Plan 38: the second live run's Antigravity group re-probed a week-long quota every thirty minutes, emailing the
    owner each time. The interval now doubles while re-probes keep hitting the limit, and a provider's named reset is
    believed for days."""
    from meld.runners import MAX_RETRY_MINUTES, last_limit_path, next_retry_minutes, write_record
    home = tmp_path / "home"
    assert next_retry_minutes(home, "claude", "pm", "2026-10-26T23:40:00Z", 30) == 30                    # first time: as configured
    write_record(last_limit_path(home, "claude", "pm"), {"at": "2026-10-26T23:00:00Z", "retry_minutes": 30, "until": "2026-10-26T23:30:00Z"})
    assert next_retry_minutes(home, "claude", "pm", "2026-10-26T23:40:00Z", 30) == 60                    # re-frozen right after the last freeze ended
    write_record(last_limit_path(home, "claude", "pm"), {"at": "2026-10-26T20:00:00Z", "retry_minutes": 960, "until": "2026-10-27T12:00:00Z"})
    assert next_retry_minutes(home, "claude", "pm", "2026-10-27T12:30:00Z", 30) == MAX_RETRY_MINUTES      # capped at a day
    write_record(last_limit_path(home, "claude", "pm"), {"at": "2026-10-20T00:00:00Z", "retry_minutes": 240, "until": "2026-10-20T04:00:00Z"})
    assert next_retry_minutes(home, "claude", "pm", "2026-10-26T23:40:00Z", 30) == 30                    # a freeze long past: start over


def test_run_group_relaunches_a_role_whose_model_was_at_capacity_after_a_fixed_pause(tmp_path):
    """Plan 50: a session that died of "Selected model is at capacity" is relaunched after CAPACITY_PAUSE_SECONDS with the
    reason `capacity`, its restart count untouched: not a crash (no doubling backoff), not a limit (no freeze)."""
    from meld.runners import CAPACITY_PAUSE_SECONDS, processes
    _, ctx, env = make(tmp_path)
    adapter = FakeAdapter(script="print('Selected model is at capacity. Please try a different model.'); raise SystemExit(1)")
    clock = Clock()
    slept: list[float] = []

    def sleep(s):
        slept.append(s)
        clock.sleep(s)
        time.sleep(0.05)

    out = run_group(ctx, adapter=adapter, cycles=3, interval=0.01, sleep=sleep, clock=clock, base_env=env)
    assert out["roles"]["orchestrator"]["launches"] >= 2 and out["roles"]["orchestrator"]["restarts"] == 0 and out["roles"]["orchestrator"]["last_exit"] == 1
    assert CAPACITY_PAUSE_SECONDS in slept and 5.0 not in slept
    relaunch = next(s for s in adapter.launches if "Launch number: 2" in s.prompt)
    assert "- Reason: `capacity` (" in relaunch.prompt and "busy, not out of quota" in relaunch.prompt
    rec = read_record(role_run_path(tmp_path / "home", "claude", "orchestrator"))
    assert rec["reason"] == "capacity"
    assert processes(ctx.cfg, tmp_path / "home", alive=lambda pid: False, start_of=lambda pid: "")["claude"]["roles"]["orchestrator"]["reason"] == "capacity"
