import json
import os
from dataclasses import replace
from pathlib import Path

from meld.config import load_config
from meld.runners import role_run_path, write_record
from meld.state import StateStore
from meld.transcript import Event
from meld.viewer import streams
from meld.viewer.streams import END, PING, discover, events, frame, record_for, stream_id, stream_path
from tests.conftest import PROJECT_TOML
from tests.factories import mk_run
from tests.fakeadapter import FakeAdapter

PARSE = FakeAdapter().parse_events   # one plain line -> one `message`; a limit line -> `usage_limit`
NOW = "2026-09-18T12:00:00Z"


def home_with(tmp_path: Path):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    home = tmp_path / "home"
    (home / "logs" / "claude").mkdir(parents=True)
    return load_config(tmp_path / ".meld.toml"), home


def frames(chunks: list[bytes]) -> list[dict]:
    """SSE bytes back into frames: {"id", "event", "data"} per frame, {"comment": ...} for a heartbeat."""
    out = []
    for block in b"".join(chunks).decode().split("\n\n"):
        if not block:
            continue
        fields: dict = {}
        for line in block.split("\n"):
            if line.startswith(":"):
                fields["comment"] = line[1:].strip()
            else:
                name, _, value = line.partition(": ")
                fields[name] = value
        out.append(fields)
    return out


def test_stream_path_names_only_logs_under_the_logs_root(tmp_path):
    _, home = home_with(tmp_path)
    logs = home / "logs"
    log = logs / "claude" / "pm-1.log"
    log.write_text("hi\n")
    (tmp_path / "secret.log").write_text("x")
    assert stream_path(logs, "claude/pm-1.log") == log and stream_id(logs, log) == "claude/pm-1.log"
    for bad in ("claude/../../secret.log", "../secret.log", "claude", "claude/pm-1.log/x", "claude/pm-1.txt",
                "", "claude//pm-1.log", "./pm-1.log", "claude/missing.log"):
        assert stream_path(logs, bad) is None, bad


def test_stream_path_returns_none_for_an_over_long_name_instead_of_raising(tmp_path):
    _, home = home_with(tmp_path)
    logs = home / "logs"
    assert stream_path(logs, "claude/" + "a" * 400 + ".log") is None


def test_stream_path_returns_none_for_a_symlink_that_escapes_logs_root(tmp_path):
    _, home = home_with(tmp_path)
    logs = home / "logs"
    outside = tmp_path / "outside.txt"
    outside.write_text("not a log")
    os.symlink(outside, logs / "claude" / "evil.log")
    assert stream_path(logs, "claude/evil.log") is None


def test_discover_lists_a_roles_recorded_launch_else_its_newest_log_then_every_worker_run(tmp_path, monkeypatch):
    cfg, home = home_with(tmp_path)
    logs = home / "logs"
    (logs / "claude" / "pm-20260918T120000Z.log").write_text("pm says hi\n")
    (logs / "claude" / "orchestrator-20260918T110000Z.log").write_text("old\n")
    (logs / "claude" / "orchestrator-20260918T120500Z.log").write_text("newer\n")
    write_record(role_run_path(home, "claude", "pm"),
                 {"pid": 4242, "started_at": NOW, "proc_start": "T0", "log_path": str(logs / "claude" / "pm-20260918T120000Z.log"),
                  "reason": "start", "launch_no": 1})
    (logs / "codex").mkdir()
    worker_log = logs / "codex" / "build-11-20260918T120100Z.log"
    worker_log.write_text("worker\n")
    StateStore(home, "codex").write_run(replace(mk_run("codex", 11), log_path=str(worker_log), proc_start="T0"))
    swept = []
    monkeypatch.setattr(streams, "sweep_logs", lambda root, max_bytes: swept.append((root, max_bytes)) or [])

    found = discover(cfg, home, alive=lambda pid: True, start_of=lambda pid: "T0")

    assert [(s["id"], s["kind"], s["role"], s["issue"], s["liveness"]["state"]) for s in found] == [
        ("claude/orchestrator-20260918T120500Z.log", "role", "orchestrator", None, "unknown"),  # no record: newest log
        ("claude/pm-20260918T120000Z.log", "role", "pm", None, "alive"),                        # the recorded launch
        ("codex/build-11-20260918T120100Z.log", "build", None, 11, "alive"),                    # the worker run
    ]
    assert found[1]["liveness"]["pid"] == 4242 and found[1]["bytes"] == len("pm says hi\n") and found[1]["group"] == "claude"
    assert found[0]["liveness"]["reason"] == "no record of a process" and found[2]["log_path"] == str(worker_log)
    assert swept == [(logs, cfg.logs_max_bytes_per_run)]          # the viewer's turn of the log cap (spec 22)


def test_discover_falls_back_to_the_newest_log_when_the_recorded_one_is_gone(tmp_path):
    cfg, home = home_with(tmp_path)
    logs = home / "logs"
    (logs / "claude" / "pm-20260918T120000Z.log").write_text("still here\n")
    write_record(role_run_path(home, "claude", "pm"),
                 {"pid": 4242, "started_at": NOW, "proc_start": "T0", "log_path": str(logs / "claude" / "pm-gone.log"),
                  "reason": "start", "launch_no": 1})
    found = discover(cfg, home, alive=lambda pid: True, start_of=lambda pid: "T0")
    assert [(s["id"], s["liveness"]["state"]) for s in found] == [("claude/pm-20260918T120000Z.log", "unknown")]


def test_record_for_finds_the_role_record_or_the_worker_record_whose_log_this_is(tmp_path):
    cfg, home = home_with(tmp_path)
    logs = home / "logs"
    pm_log = logs / "claude" / "pm-1.log"
    pm_log.write_text("x\n")
    write_record(role_run_path(home, "claude", "pm"),
                 {"pid": 7, "started_at": NOW, "proc_start": "P", "log_path": str(pm_log), "reason": "start", "launch_no": 1})
    worker_log = logs / "claude" / "build-11-1.log"
    worker_log.write_text("y\n")
    StateStore(home, "claude").write_run(replace(mk_run("claude", 11), log_path=str(worker_log), proc_start="W"))
    assert record_for(cfg, home, pm_log)["pid"] == 7
    assert record_for(cfg, home, worker_log) == {"pid": 4242, "started_at": "2026-09-03T10:00:00Z", "proc_start": "W"}
    assert record_for(cfg, home, logs / "claude" / "nobody.log") is None


def test_frame_is_one_sse_event_carrying_the_whole_event_as_json():
    ev = Event(seq=3, at=NOW, kind="tool_call", text="ls", tool="Bash", ok=None, meta={"id": "toolu_01"})
    assert frame(ev) == (b'id: 3\nevent: tool_call\ndata: {"seq": 3, "at": "2026-09-18T12:00:00Z", "kind": "tool_call", '
                         b'"text": "ls", "tool": "Bash", "ok": null, "meta": {"id": "toolu_01"}}\n\n')


def test_events_replays_then_follows_then_ends_when_the_writer_is_confirmed_gone(tmp_path):
    log = tmp_path / "pm-1.log"
    log.write_text("one\ntwo\n")
    states = iter(["alive", "alive", "dead"])
    appended = []

    def sleep(seconds):
        if not appended:
            with log.open("a") as f:
                f.write("three\nfour")        # the fourth line unterminated: the writer died mid-write
            appended.append(seconds)

    out = frames(list(events(log, PARSE, after=0, now=lambda: NOW, writer=lambda: {"state": next(states)},
                             sleep=sleep, interval=0.5, check_every=1, max_polls=10)))
    texts = [(f.get("id"), f.get("event"), json.loads(f["data"])["text"] if "data" in f and f["event"] != "end" else None)
             for f in out if "comment" not in f]
    assert texts == [("1", "message", "one"), ("2", "message", "two"), ("3", "message", "three"), ("4", "message", "four"),
                     ("5", "run_finished", "process exited; the transcript ends without a terminal event"), (None, "end", None)]
    assert json.loads(out[-2]["data"])["meta"] == {"synthesized": True}
    assert [f for f in out if "comment" in f] == [{"comment": "ping"}, {"comment": "ping"}]   # one per idle check while alive
    assert appended == [0.5]


def test_events_resumes_after_a_seq_and_treats_a_shortfall_as_a_trim(tmp_path):
    log = tmp_path / "pm-1.log"
    log.write_text("one\ntwo\n")
    dead = lambda: {"state": "dead"}  # noqa: E731
    kw = dict(now=lambda: NOW, writer=dead, sleep=lambda s: None, check_every=1, max_polls=1)

    resumed = frames(list(events(log, PARSE, after=1, **kw)))
    assert [(f.get("id"), f.get("event")) for f in resumed] == [("2", "message"), ("3", "run_finished"), (None, "end")]

    nothing_new = frames(list(events(log, PARSE, after=2, **kw)))
    assert [(f.get("id"), f.get("event")) for f in nothing_new] == [("3", "run_finished"), (None, "end")]

    trimmed = frames(list(events(log, PARSE, after=9, **kw)))   # the client saw nine events; the file holds two
    assert [(f.get("id"), f.get("event")) for f in trimmed] == [("0", "log_truncated"), ("1", "message"), ("2", "message"),
                                                                 ("3", "run_finished"), (None, "end")]
    assert "trimmed" in json.loads(trimmed[0]["data"])["text"]


def test_events_does_not_synthesize_an_end_the_transcript_already_has(tmp_path):
    log = tmp_path / "pm-1.log"
    log.write_text("working\nYou have hit your usage limit. Resets at 3pm.\n")   # FakeAdapter: a limit line -> usage_limit
    out = frames(list(events(log, PARSE, now=lambda: NOW, writer=lambda: {"state": "dead"}, sleep=lambda s: None,
                             check_every=1, max_polls=1)))
    assert [f.get("event") for f in out] == ["message", "usage_limit", "end"]


def test_events_stays_open_on_an_unknown_writer_and_checks_it_only_every_check_every_polls(tmp_path):
    log = tmp_path / "pm-1.log"
    log.write_text("one\n")
    checks = []

    def writer():
        checks.append(1)
        return {"state": "unknown"}

    out = frames(list(events(log, PARSE, now=lambda: NOW, writer=writer, sleep=lambda s: None, check_every=3, max_polls=7)))
    assert [f.get("event") for f in out if "comment" not in f] == ["message"]          # no end, no synthesis
    assert len([f for f in out if "comment" in f]) == 2 and len(checks) == 2           # polls 3 and 6 of 7
