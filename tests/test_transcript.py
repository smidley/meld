import dataclasses
import json
import os
import subprocess
import sys
import time

import pytest

from meld.transcript import (KINDS, PARTIAL_CAP, TEXT_CAP, Event, Tail, cap, newest_log, render, render_line,
                             sweep_logs, total_bytes, trim_log)


def mk(kind: str, **kw) -> Event:
    return Event(seq=kw.pop("seq", 1), at=kw.pop("at", "2026-09-12T02:11:19Z"), kind=kind, **kw)


def test_every_kind_is_constructible_and_an_unknown_kind_is_refused():
    for kind in KINDS:
        assert mk(kind).kind == kind
    with pytest.raises(ValueError, match="unknown event kind 'explosion'"):
        mk("explosion")


def test_event_is_frozen_and_meta_is_per_instance():
    a, b = mk("message"), mk("message")
    a.meta["x"] = 1
    assert b.meta == {}
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.kind = "raw"


def test_cap_truncates_long_text_and_marks_it():
    assert cap("short") == "short"
    capped = cap("x" * (TEXT_CAP + 500))
    assert len(capped) == TEXT_CAP + len(" …(truncated)")
    assert capped.endswith(" …(truncated)")


def test_cap_tolerates_none_and_non_strings():
    assert cap(None) == ""
    assert cap(12) == "12"


def test_render_line_puts_the_clock_then_a_padded_kind():
    assert render_line(mk("message", text="Checking the board first.")) == \
        "02:11:19  message  Checking the board first."
    assert render_line(mk("run_started", text="claude-opus-5")) == "02:11:19  started  claude-opus-5"


def test_render_line_shows_a_tool_call_as_tool_then_argument():
    assert render_line(mk("tool_call", tool="Bash", text="meld status")) == \
        "02:11:19  tool     Bash: meld status"
    assert render_line(mk("tool_call", tool="Bash")) == "02:11:19  tool     Bash"


def test_render_line_puts_a_tool_results_outcome_before_its_text():
    assert render_line(mk("tool_result", tool="Bash", ok=True, text='{"repo": "acme/widgets"}')) == \
        '02:11:19  result   ok · {"repo": "acme/widgets"}'
    assert render_line(mk("tool_result", tool="Bash", ok=False, text="refused: HALT is in effect")) == \
        "02:11:19  result   FAILED · refused: HALT is in effect"
    assert render_line(mk("tool_result", tool="Bash", ok=True)) == "02:11:19  result   ok"


def test_render_line_collapses_newlines_and_truncates_to_the_width():
    line = render_line(mk("message", text="one\ntwo\nthree"), width=40)
    assert line == "02:11:19  message  one ⏎ two ⏎ three"
    narrow = render_line(mk("message", text="y" * 200), width=40)
    assert len(narrow) == 40 and narrow.endswith("…")


def test_render_line_keeps_a_timestamp_it_cannot_parse():
    assert render_line(mk("message", at="not-a-time", text="hi")) == "not-a-time  message  hi"


def test_render_orders_by_seq_and_renders_nothing_as_empty():
    events = [mk("message", seq=2, text="second"), mk("run_started", seq=1, text="claude-opus-5")]
    assert render(events) == "02:11:19  started  claude-opus-5\n02:11:19  message  second"
    assert render([]) == ""


NOW = "2026-09-12T02:11:19Z"


def fake_parse(line: str, now: str) -> list[Event]:
    """A miniature adapter: {"say": "..."} becomes one message; anything else becomes raw."""
    try:
        doc = json.loads(line)
    except json.JSONDecodeError:
        return [Event(seq=0, at=now, kind="raw", text=cap(line))]
    if isinstance(doc, dict) and "say" in doc:
        return [Event(seq=0, at=now, kind="message", text=cap(doc["say"]))]
    if isinstance(doc, dict) and "pair" in doc:
        return [Event(seq=0, at=now, kind="message", text="first"),
                Event(seq=0, at=now, kind="message", text="second")]
    return []


def test_tail_reads_only_complete_lines_and_holds_the_partial_one(tmp_path):
    log = tmp_path / "role.log"
    log.write_bytes(b'{"say": "one"}\n{"say": "two"}\n{"say": "thr')
    t = Tail(log)
    first = t.read(fake_parse, NOW)
    assert [e.text for e in first] == ["one", "two"]
    assert t.offset == log.stat().st_size

    with log.open("ab") as f:
        f.write(b'ee"}\n')
    assert [e.text for e in t.read(fake_parse, NOW)] == ["three"]


def test_tail_stamps_a_monotonic_seq_across_reads_and_across_multi_event_lines(tmp_path):
    log = tmp_path / "role.log"
    log.write_bytes(b'{"say": "one"}\n{"pair": true}\n')
    t = Tail(log)
    assert [e.seq for e in t.read(fake_parse, NOW)] == [1, 2, 3]
    with log.open("ab") as f:
        f.write(b'{"say": "four"}\n')
    assert [(e.seq, e.text) for e in t.read(fake_parse, NOW)] == [(4, "four")]
    assert t.seq == 4


def test_tail_reassembles_a_multibyte_character_split_across_two_reads(tmp_path):
    log = tmp_path / "role.log"
    log.write_bytes(b'{"say": "caf\xc3')
    t = Tail(log)
    assert t.read(fake_parse, NOW) == []
    with log.open("ab") as f:
        f.write(b'\xa9"}\n')
    assert [e.text for e in t.read(fake_parse, NOW)] == ["café"]


def test_tail_skips_blank_lines_and_drops_events_the_parser_declines(tmp_path):
    log = tmp_path / "role.log"
    log.write_bytes(b'\n\n{"ignored": true}\n{"say": "kept"}\n')
    t = Tail(log)
    assert [e.text for e in t.read(fake_parse, NOW)] == ["kept"]


def test_tail_reports_a_trim_and_resumes_from_the_start(tmp_path):
    import os

    log = tmp_path / "role.log"
    log.write_bytes(b'{"say": "before"}\n')
    t = Tail(log)
    assert [e.text for e in t.read(fake_parse, NOW)] == ["before"]

    os.truncate(log, 0)
    with log.open("ab") as f:
        f.write(b'{"say": "after"}\n')
    events = t.read(fake_parse, NOW)
    assert [e.kind for e in events] == ["log_truncated", "message"]
    assert "was trimmed" in events[0].text
    assert events[1].text == "after"
    assert [e.seq for e in events] == [2, 3]


def test_tail_flush_yields_the_unterminated_last_line_and_read_alone_does_not(tmp_path):
    """A process killed mid-write leaves no trailing newline; `read` withholds that line (it may still be
    growing), and `flush` is how a reader that will not read again claims it."""
    log = tmp_path / "role.log"
    log.write_bytes(b'{"say": "working"}\nTraceback (most recent call last): fatal, out of memory')
    t = Tail(log)
    assert [e.text for e in t.read(fake_parse, NOW)] == ["working"]
    flushed = t.flush(fake_parse, NOW)
    assert [(e.seq, e.kind, e.text) for e in flushed] == [(2, "raw", "Traceback (most recent call last): fatal, out of memory")]
    assert t.flush(fake_parse, NOW) == []                 # the residue is consumed, not re-emitted


def test_tail_flush_of_a_single_line_log_with_no_newline_is_that_line(tmp_path):
    """The pre-plan-8 Claude shape: one JSON blob, no trailing newline. Without a flush it read as nothing."""
    log = tmp_path / "role.log"
    log.write_bytes(b'{"say": "the whole run in one blob"}')
    t = Tail(log)
    assert t.read(fake_parse, NOW) == []
    assert [e.text for e in t.flush(fake_parse, NOW)] == ["the whole run in one blob"]


def test_tail_flush_of_a_log_that_ends_on_a_newline_is_empty(tmp_path):
    log = tmp_path / "role.log"
    log.write_bytes(b'{"say": "one"}\n')
    t = Tail(log)
    assert [e.text for e in t.read(fake_parse, NOW)] == ["one"]
    assert t.flush(fake_parse, NOW) == []


def _line_length(line: str, now: str) -> list[Event]:
    """A parser that reports the length of the line it was handed, so a test can see the buffer's bound."""
    return [Event(seq=0, at=now, kind="raw", text=str(len(line)))]


def test_tail_bounds_the_partial_line_it_holds_between_reads(tmp_path):
    """An unterminated line is held head-first and capped: a writer that never emits a newline cannot grow
    the reader without bound. The line is clipped, never dropped -- its opening bytes still render."""
    log = tmp_path / "role.log"
    log.write_bytes(b"x" * (PARTIAL_CAP + 50))
    t = Tail(log)
    assert t.read(_line_length, NOW) == []                # nothing complete yet
    with log.open("ab") as f:
        f.write(b"yyy\n")
    (event,) = t.read(_line_length, NOW)
    assert event.text == str(PARTIAL_CAP + 3)             # the 50 bytes past the cap were dropped, not the line


def test_tail_flush_of_an_over_cap_line_still_renders_its_head(tmp_path):
    log = tmp_path / "role.log"
    log.write_bytes(b'{"say": "' + b"z" * (PARTIAL_CAP + 100))
    t = Tail(log)
    assert t.read(fake_parse, NOW) == []
    (event,) = t.flush(fake_parse, NOW)
    assert event.kind == "raw"                            # a clipped JSON object does not parse
    assert event.text.startswith('{"say": "zzz') and event.text.endswith(" …(truncated)")


def test_tail_of_a_missing_file_is_empty_and_does_not_raise(tmp_path):
    assert Tail(tmp_path / "absent.log").read(fake_parse, NOW) == []


def test_tail_can_resume_from_a_caller_held_offset_and_seq(tmp_path):
    log = tmp_path / "role.log"
    log.write_bytes(b'{"say": "one"}\n{"say": "two"}\n')
    first = Tail(log)
    first.read(fake_parse, NOW)
    resumed = Tail(log, offset=first.offset, seq=first.seq)
    with log.open("ab") as f:
        f.write(b'{"say": "three"}\n')
    assert [(e.seq, e.text) for e in resumed.read(fake_parse, NOW)] == [(3, "three")]


def test_trim_log_leaves_a_small_file_alone(tmp_path):
    log = tmp_path / "role.log"
    log.write_bytes(b"short\n")
    assert trim_log(log, max_bytes=1024) is False
    assert log.read_bytes() == b"short\n"
    assert not (tmp_path / "role.log.1").exists()


def test_trim_log_keeps_the_tail_beside_the_emptied_file(tmp_path):
    log = tmp_path / "role.log"
    log.write_bytes(b"".join(b"line %d\n" % i for i in range(200)))
    assert trim_log(log, max_bytes=64, keep_bytes=32) is True
    assert log.stat().st_size == 0
    kept = (tmp_path / "role.log.1").read_bytes()
    assert len(kept) == 32
    assert kept.endswith(b"line 199\n")


def test_trim_log_frees_the_space_even_when_the_sidecar_cannot_be_written(tmp_path):
    """The order is load-bearing. The moment this mechanism exists for is a disk with no free bytes, where
    the sidecar write is the operation that fails -- so the truncate happens first and a `.1` that could not
    be written is still reported as a trim. A directory in the sidecar's place is that failure, for real."""
    log = tmp_path / "role.log"
    log.write_bytes(b"".join(b"line %d\n" % i for i in range(200)))
    (tmp_path / "role.log.1").mkdir()                      # write_bytes here raises IsADirectoryError

    assert trim_log(log, max_bytes=64, keep_bytes=32) is True
    assert log.stat().st_size == 0                         # the space was freed, which is the incident
    assert (tmp_path / "role.log.1").is_dir()              # and nothing pretended to write the tail


def test_trim_log_of_a_missing_file_is_false(tmp_path):
    assert trim_log(tmp_path / "absent.log", max_bytes=1) is False


def test_a_live_writer_keeps_writing_at_the_new_end_after_a_trim(tmp_path):
    """The load-bearing assumption: O_APPEND means truncation does not strand the child's descriptor."""
    log = tmp_path / "child.log"
    with open(log, "ab") as handle:
        child = subprocess.Popen(
            [sys.executable, "-u", "-c",
             "import sys,time\nfor i in range(6):\n sys.stdout.write('line %d\\n' % i)\n time.sleep(0.2)"],
            stdout=handle, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 5
            while log.stat().st_size == 0 and time.monotonic() < deadline:
                time.sleep(0.02)
            assert trim_log(log, max_bytes=1, keep_bytes=200) is True
            assert child.wait(timeout=10) == 0
        finally:
            if child.poll() is None:
                child.kill()
    after = log.read_text()
    assert "\x00" not in after                      # no sparse gap: the writer resumed at offset 0
    assert after.startswith("line ")
    assert (tmp_path / "child.log.1").read_text().startswith("line 0")


def test_sweep_logs_trims_only_the_logs_over_the_cap(tmp_path):
    (tmp_path / "claude").mkdir()
    big = tmp_path / "claude" / "orchestrator-1.log"
    small = tmp_path / "claude" / "pm-1.log"
    big.write_bytes(b"x" * 500)
    small.write_bytes(b"x" * 10)
    assert sweep_logs(tmp_path, max_bytes=100) == [big]
    assert big.stat().st_size == 0 and small.stat().st_size == 10
    assert sweep_logs(tmp_path, max_bytes=100) == []          # idempotent


def test_sweep_logs_ignores_a_directory_that_is_not_there(tmp_path):
    assert sweep_logs(tmp_path / "absent", max_bytes=1) == []


def test_total_bytes_counts_every_file_including_kept_tails(tmp_path):
    (tmp_path / "claude").mkdir()
    (tmp_path / "claude" / "pm-1.log").write_bytes(b"x" * 10)
    (tmp_path / "claude" / "pm-1.log.1").write_bytes(b"x" * 5)
    assert total_bytes(tmp_path) == 15
    assert total_bytes(tmp_path / "absent") == 0


def _log(root, group, name, mtime):
    d = root / group
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(b"{}\n")
    os.utime(p, (mtime, mtime))
    return p


def test_newest_log_picks_the_most_recently_written_across_groups(tmp_path):
    _log(tmp_path, "claude", "orchestrator-20260912T020000Z.log", 1000)
    newest = _log(tmp_path, "codex", "pm-20260912T030000Z.log", 2000)
    assert newest_log(tmp_path) == newest


def test_newest_log_narrows_to_a_group_then_to_a_role_or_kind(tmp_path):
    claude_pm = _log(tmp_path, "claude", "pm-20260912T010000Z.log", 1000)
    claude_orc = _log(tmp_path, "claude", "orchestrator-20260912T020000Z.log", 1500)
    _log(tmp_path, "codex", "pm-20260912T030000Z.log", 2000)
    assert newest_log(tmp_path, "claude") == claude_orc
    assert newest_log(tmp_path, "claude/pm") == claude_pm


def test_newest_log_matches_a_worker_kind_prefix(tmp_path):
    audit = _log(tmp_path, "claude", "audit-14-20260912T011514Z.log", 1000)
    _log(tmp_path, "claude", "review-40-20260912T022109Z.log", 900)
    assert newest_log(tmp_path, "claude/audit") == audit


def test_newest_log_is_none_when_there_is_nothing_to_read(tmp_path):
    assert newest_log(tmp_path) is None
    assert newest_log(tmp_path / "absent") is None
    _log(tmp_path, "claude", "pm-1.log", 1000)
    assert newest_log(tmp_path, "nosuchgroup") is None


def test_usage_limit_is_a_kind_and_renders_as_limit():
    assert "usage_limit" in KINDS
    assert render_line(mk("usage_limit", text="You've hit your spend limit.")) == \
        "02:11:19  limit    You've hit your spend limit."
