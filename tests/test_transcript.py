import dataclasses

import pytest

from meld.transcript import KINDS, TEXT_CAP, Event, cap, render, render_line


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
