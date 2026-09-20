import json
import os
from dataclasses import asdict, replace
from pathlib import Path

from meld.config import load_config
from meld.runners import role_run_path, write_record
from meld.state import StateStore
from meld.transcript import Event
from meld.viewer import streams
from meld.viewer.streams import Source, decode_cursor, discover, encode_cursor, end_frame, events, frame, record_for, stream_id, stream_path
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


def src(log, writer_state="alive", stream=None):
    """One Source over a real file, with its writer's liveness injected as a fixed state or a callable."""
    state = writer_state if callable(writer_state) else (lambda: {"state": writer_state})
    return Source(stream=stream or f"claude/{log.name}", path=log, parse=PARSE, writer=state)


def only(*sources):
    return lambda: list(sources)


def test_cursor_round_trips_and_ignores_anything_it_cannot_read():
    assert encode_cursor({"claude/b.log": 2, "claude/a.log": 7}) == "claude/a.log=7|claude/b.log=2"
    assert encode_cursor({"claude/a.log": 0, "claude/b.log": 3}) == "claude/a.log=0|claude/b.log=3"
    assert encode_cursor({}) == ""
    assert decode_cursor("claude/a.log=7|claude/b.log=2") == {"claude/a.log": 7, "claude/b.log": 2}
    assert decode_cursor("") == {} and decode_cursor("rubbish") == {} and decode_cursor("a.log=x") == {}
    assert decode_cursor("claude/a.log=-3") == {"claude/a.log": 0}                     # never a tail slice
    assert decode_cursor(encode_cursor({"claude/a.log": 4})) == {"claude/a.log": 4}


def test_frame_names_its_stream_and_carries_the_cursor_as_the_id():
    ev = Event(seq=3, at=NOW, kind="tool_call", text="ls", tool="Bash", ok=None, meta={"id": "toolu_01"})
    out = frame("claude/pm-1.log", ev, cursor="claude/pm-1.log=3").decode()
    assert out.startswith("id: claude/pm-1.log=3\nevent: tool_call\ndata: {")
    assert json.loads(out.split("data: ", 1)[1]) == {"stream": "claude/pm-1.log", "event": asdict(ev)}
    assert frame("claude/pm-1.log", ev).decode().startswith("event: tool_call\n")       # no cursor, no id line
    assert end_frame("claude/pm-1.log") == b'event: end\ndata: {"stream": "claude/pm-1.log"}\n\n'


def test_events_interleaves_two_streams_and_the_cursor_names_both(tmp_path):
    a, b = tmp_path / "pm-1.log", tmp_path / "orchestrator-1.log"
    a.write_text("a1\n")
    b.write_text("b1\nb2\n")
    out = frames(list(events(only(src(a), src(b)), now=lambda: NOW, sleep=lambda s: None,
                             check_every=99, max_polls=1)))
    assert [(f["event"], json.loads(f["data"])["stream"], json.loads(f["data"])["event"]["text"]) for f in out] == [
        ("message", "claude/pm-1.log", "a1"),
        ("message", "claude/orchestrator-1.log", "b1"),
        ("message", "claude/orchestrator-1.log", "b2"),
    ]
    assert [f["id"] for f in out] == ["claude/orchestrator-1.log=0|claude/pm-1.log=1",   # encode_cursor sorts
                                      "claude/orchestrator-1.log=1|claude/pm-1.log=1",
                                      "claude/orchestrator-1.log=2|claude/pm-1.log=1"]


def test_events_resumes_each_stream_from_the_cursor_it_was_given(tmp_path):
    a, b = tmp_path / "pm-1.log", tmp_path / "orchestrator-1.log"
    a.write_text("a1\na2\n")
    b.write_text("b1\nb2\n")
    out = frames(list(events(only(src(a), src(b)), now=lambda: NOW, sleep=lambda s: None, check_every=99, max_polls=1,
                             cursor="claude/pm-1.log=2|claude/orchestrator-1.log=1")))
    assert [(json.loads(f["data"])["stream"], json.loads(f["data"])["event"]["text"]) for f in out] == [
        ("claude/orchestrator-1.log", "b2")]     # pm caught up, orchestrator one behind
    # a cursor past the end of one file is that file's trim notice and a full replay, and only that file's
    out = frames(list(events(only(src(a)), now=lambda: NOW, sleep=lambda s: None, check_every=99, max_polls=1,
                             cursor="claude/pm-1.log=9")))
    assert [(f["event"], json.loads(f["data"])["event"]["text"]) for f in out] == [
        ("log_truncated", "pm-1.log was trimmed since this page last read it; showing it from the start"),
        ("message", "a1"), ("message", "a2")]
    assert out[0]["id"] == "claude/pm-1.log=0"


def test_events_ends_one_stream_without_ending_the_connection(tmp_path):
    a, b = tmp_path / "pm-1.log", tmp_path / "orchestrator-1.log"
    a.write_text("a1\n")
    b.write_text("b1\n")
    states = iter(["dead", "alive", "alive", "alive"])
    # Poll 1 replays both; poll 2's idle check ends pm; poll 3 is the idle round that proves the connection
    # outlived it -- one heartbeat, orchestrator still open, pm never reopened.
    out = frames(list(events(only(src(a, lambda: {"state": next(states)}), src(b)),
                             now=lambda: NOW, sleep=lambda s: None, check_every=1, max_polls=3)))
    kinds = [(f.get("event"), json.loads(f["data"])["stream"] if "data" in f else None) for f in out if "comment" not in f]
    assert kinds == [("message", "claude/pm-1.log"), ("message", "claude/orchestrator-1.log"),
                     ("run_finished", "claude/pm-1.log"), ("end", "claude/pm-1.log")]
    assert json.loads([f for f in out if f.get("event") == "run_finished"][0]["data"])["event"]["meta"] == {"synthesized": True}
    assert "id" not in [f for f in out if f.get("event") == "run_finished"][0]        # synthesized: not a position
    assert [f for f in out if "comment" in f] == [{"comment": "ping"}]


def test_events_does_not_reopen_a_stream_it_has_ended(tmp_path):
    a = tmp_path / "pm-1.log"
    a.write_text("a1\n")
    source = src(a, "dead")
    out = frames(list(events(only(source), now=lambda: NOW, sleep=lambda s: None, check_every=1, max_polls=4)))
    assert [f.get("event") for f in out if "comment" not in f] == ["message", "run_finished", "end"]


def test_events_picks_up_a_stream_that_appears_after_the_connection_opened(tmp_path):
    a, b = tmp_path / "pm-1.log", tmp_path / "orchestrator-1.log"
    a.write_text("a1\n")
    later = []

    def sources():
        if later:
            return [src(a), src(b)]
        b.write_text("b1\n")
        later.append(True)
        return [src(a)]

    out = frames(list(events(sources, now=lambda: NOW, sleep=lambda s: None, check_every=1, max_polls=2)))
    assert [json.loads(f["data"])["event"]["text"] for f in out if "comment" not in f] == ["a1", "b1"]


def test_events_restarts_one_stream_numbering_on_a_trim_under_the_connection(tmp_path):
    a = tmp_path / "pm-1.log"
    a.write_text("original1\noriginal2\noriginal3\n")
    trimmed = []

    def sleep(seconds):
        if not trimmed:
            a.write_text("fresh1\nfresh2\n")       # shorter: Tail sees the file shrink
            trimmed.append(True)

    out = frames(list(events(only(src(a)), now=lambda: NOW, sleep=sleep, check_every=99, max_polls=2)))
    assert [(f.get("event"), f.get("id")) for f in out] == [
        ("message", "claude/pm-1.log=1"), ("message", "claude/pm-1.log=2"), ("message", "claude/pm-1.log=3"),
        ("log_truncated", "claude/pm-1.log=0"), ("message", "claude/pm-1.log=1"), ("message", "claude/pm-1.log=2")]
