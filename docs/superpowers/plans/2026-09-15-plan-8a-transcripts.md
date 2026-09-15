# Plan 8a: normalized transcripts — one event model for every CLI, and a live `meld tail`

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a running role or worker watchable while it runs: both CLIs stream their output, each adapter translates its own vocabulary into one closed event type, and `meld tail` renders a live transcript in the terminal with no server and no page.

**Architecture:** `transcript.py` owns the event model (`Event`), the reader (`Tail`, a byte-offset reader that yields only complete lines and detects truncation), the terminal renderer, and the log cap (`trim_log`, `sweep_logs`). Each adapter gains one method, `parse_event(line, now) -> Event | None`, so the adapter stays the only layer that knows a CLI — how it is launched, and now how it talks. The Claude adapter switches from `--output-format json` (one blob at exit, invisible until then) to `--output-format stream-json --verbose`. Because the child process holds the log's file descriptor and outlives the `meld` process, the cap is enforced by truncation in place against `O_APPEND`, never by renaming the file.

**Tech Stack:** Python 3.13, standard library plus `click` only. No new dependency. Real files in `tmp_path` and recorded JSONL fixtures in tests; no network, no real CLI.

**Spec:** `docs/superpowers/specs/2026-09-03-meld-design.md` — §22 (all of "Normalized transcripts"; the viewer and the config form are plans 8b–8d and are **out of scope here**), §2 (the live-view goal), §4 (`transcript.py` in the layout; `tail` in the verb list), §5 (`[logs]`), §16 (`parse_event`; the Claude argv), §17 (the rendered failure tail), §19 (the new suite), §21 (what plan 8 leaves out).

## Global Constraints

- Python `>=3.12` (3.13 locally); runtime dependency `click>=8.1` only. Standard library plus click inside `src/meld/`.
- `rules.py` is the only legality authority: no new `if actor...` checks outside it; `meld status` must list every legal verb. **Nothing in this plan touches legality** — `meld tail` reads local files and posts nothing.
- `github.py` is the only GitHub path (plus `appauth.py`'s two App-JWT calls); `gitops.py` the only git runner. This plan adds no call to either.
- Adapters are the only code that knows how a CLI is launched, and now also how it talks. No module outside `src/meld/adapters/` may branch on a CLI's event names.
- Workers never hold credentials; anything a worker writes that reaches GitHub passes through `neutralise_closers`. Task 7 renders a worker's log tail into a GitHub comment, so the rendered text keeps passing through `neutralise_closers` exactly as the raw tail does today.
- `parse_event` never raises and never returns a partial parse: an unrecognized line becomes `kind="raw"`. A CLI that adds an event type must degrade the view, never break a run.
- Tests are hermetic and real: real files over fakes where a file is the thing under test; no network, no real CLI, no host `~/.claude`/gitconfig/wall-clock dependence; inject `now`; pass `adapter_for=` to every `load_context` in tests. `uv run pytest -q -p no:cacheprovider` must be green with **no warnings** before every commit.
- Never set `MELD_SMOKE=1`.
- Conventional commit subjects, **no attribution trailers of any kind**, one logical change per commit; repo-local identity `Scott Brant <421573+smidley@users.noreply.github.com>`.
- Work in `/Users/scottbrant/code/meld-main` on branch `docs/plan-8-observability` or a branch off it. `~/Documents/meld` is iCloud-synced and stalls git; do not work there. Commit with `git -C /Users/scottbrant/code/meld-main ...`.
- Baseline at this plan's base (branch `docs/plan-8-observability` at `ab4c8fb`): `uv run pytest -q -p no:cacheprovider` → **641 passed**, no warnings. Each task leaves the suite green.

## Decisions (rulings made while writing the plan; recorded so executors do not re-litigate)

1. **`Event` is a closed vocabulary, and `raw` is the escape hatch.** `KINDS` is a tuple in `transcript.py`; `Event.__post_init__` rejects a kind outside it. A CLI line an adapter does not recognize becomes `raw` with the line as `text`, truncated. This is what keeps a CLI upgrade from breaking `meld tail` and the failure tail.
2. **The adapter translates; `Tail` sequences.** `parse_event(line, now)` returns an `Event` with `seq=0`; `Tail` stamps the monotonic number with `dataclasses.replace`. An adapter therefore has exactly one job and no state.
3. **Text is capped at parse time, not at render time.** `TEXT_CAP = 2000` characters per event. A `tool_result` carrying 4 MB of command output must not sit in memory at full size while a viewer holds a thousand events. The renderer truncates again to the terminal width; the cap is about memory, the width is about reading.
4. **The log cap is truncation in place, never a rename.** `popen_detached` hands the open log to the CLI as its stdout and the run outlives the `meld` process, so a rename would leave the writer appending to the renamed inode and the live reader watching a dead file. `open_log` already opens with `"ab"` (`O_APPEND`), which recomputes the offset from the file's end on every write, so `trim_log` copies the tail to `<name>.1`, replaces any previous `.1`, and truncates the original to zero; the writer's next line lands at the new end. `Tail` detects a file shorter than its own offset, emits `log_truncated`, and resumes from zero.
5. **`meld tail` is a human verb with no party.** It reads files under `$MELD_HOME` and prints; it takes no `MELD_GROUP`, posts nothing, and needs no board. It therefore does not call `load_context`'s GitHub path — it needs `meld_home` and the config only for `[logs]` and the group's adapter name. A worker (`MELD_WORKER=1`) still cannot run `meld` at all, which is unchanged.
6. **The Claude switch is verified by `doctor`, not by a smoke run.** `--verbose` joins `REQUIRED_FLAGS`, so `meld doctor`'s `cli-flags:<group>` check fails loudly if a future CLI drops either flag, instead of every launch failing. No task in this plan runs a real CLI.
7. **The `result` fixture is a real capture; the streamed events are the documented shape.** `tests/fixtures/transcripts/claude-run.jsonl`'s final `result` line is the redacted shape captured from the first live run's own log, because `--output-format json` writes exactly that event and nothing else. The `system`, `assistant`, and `user` lines are written from Claude Code's documented `stream-json` shape. Decision 1's `raw` fallback is what covers the gap until a live run adds a full capture; Task 3 includes an unknown-type line and a non-JSON line as fixtures precisely so the fallback is tested rather than assumed.
8. **`sweep_logs` is a plain function over a directory.** It takes `(logs_root, max_bytes)` and returns the paths it trimmed, so it is tested against `tmp_path` with no `Context`, no board, and no fake GitHub. `dispatch.tick` calls it; plan 8b's viewer will call the same function from its poll.

## File structure

| File | Responsibility |
|---|---|
| `src/meld/transcript.py` | **new.** `Event`, `KINDS`, `TEXT_CAP`, `cap`; `Tail` (offset reader, complete lines only, truncation detection); `render_line`/`render`; `trim_log`, `sweep_logs`, `total_bytes` |
| `src/meld/config.py` | `[logs]`: `logs_max_bytes_per_run`, `logs_warn_total_mb` |
| `src/meld/adapters/base.py` | `Adapter` protocol gains `parse_event`; `Event` re-exported for adapters |
| `src/meld/adapters/claude.py` | `--output-format stream-json --verbose`; `--verbose` in `REQUIRED_FLAGS`; `parse_event` for `system`/`assistant`/`user`/`result` |
| `src/meld/adapters/codex.py` | `parse_event` for `thread.started`/`turn.*`/`item.started`/`item.completed` |
| `src/meld/cli.py` | `meld tail` |
| `src/meld/dispatch.py` | `tick` calls `sweep_logs`; the failure comment's tail is rendered through `transcript.render` |
| `src/meld/doctor.py` | `logs-size` check against `[logs] warn_total_mb` |
| `tests/fixtures/transcripts/claude-run.jsonl`, `codex-run.jsonl` | **new.** recorded, redacted event streams, each ending in a real terminal event, each containing one unknown type and one non-JSON line |
| `tests/test_transcript.py` | **new.** the event model, the renderer, `Tail`, `trim_log`/`sweep_logs`/`total_bytes` |
| `tests/test_adapter_claude.py`, `tests/test_adapter_codex.py` | `parse_event` per event type, the unknown type, the non-JSON line, the argv change |
| `tests/fakeadapter.py` | `FakeAdapter.parse_event` so the protocol stays satisfied in every existing test |
| `tests/test_config.py`, `tests/test_dispatch.py`, `tests/test_doctor.py`, `tests/test_cli.py` | the new config fields, the tick sweep, the rendered tail, the doctor check, the `tail` command |
| `docs/HANDOFF.md`, `README.md` | plan 8a state, `meld tail` in the verb list, the `[logs]` table |

---

### Task 1: `[logs]` config, the `Event` model, and the terminal renderer

**Files:**
- Create: `src/meld/transcript.py`
- Create: `tests/test_transcript.py`
- Modify: `src/meld/config.py` (a field pair on `Config`, and two lines in `from_dict`)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `transcript.KINDS: tuple[str, ...]`; `transcript.TEXT_CAP: int`; `transcript.cap(text: object) -> str`; `transcript.Event(seq: int, at: str, kind: str, text: str = "", tool: str | None = None, ok: bool | None = None, meta: dict = ...)`, frozen, raising `ValueError` on an unknown kind; `transcript.render_line(e: Event, width: int = 100) -> str`; `transcript.render(events: Iterable[Event], width: int = 100) -> str`; `Config.logs_max_bytes_per_run: int`; `Config.logs_warn_total_mb: int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_transcript.py`. Every expected string below was verified against the implementation in Step 3 — do not adjust the spacing by eye, the kind column is padded to eight characters:

```python
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
```

Add to `tests/test_config.py`:

```python
def test_logs_table_has_defaults_and_is_overridable():
    base = {
        "repo": {"name": "acme/widgets"},
        "humans": {"accounts": ["empowerScott"]},
        "groups": {"claude": {"cli": "claude", "bot": "meld-claude-bot", "token_env": "T1"}},
    }
    default = Config.from_dict(base)
    assert default.logs_max_bytes_per_run == 8 * 1024 * 1024
    assert default.logs_warn_total_mb == 2048

    tuned = Config.from_dict({**base, "logs": {"max_bytes_per_run": 1024, "warn_total_mb": 1}})
    assert tuned.logs_max_bytes_per_run == 1024
    assert tuned.logs_warn_total_mb == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_transcript.py tests/test_config.py -q -p no:cacheprovider
```

Expected: `ModuleNotFoundError: No module named 'meld.transcript'` from the new file, and `AssertionError` on `logs_max_bytes_per_run` from the config test.

- [ ] **Step 3: Write the implementation**

Create `src/meld/transcript.py`:

```python
"""One event model for every adapter's run output, plus the reader and renderer above it.

An adapter translates its own CLI's vocabulary into `Event` (spec section 22); nothing above `adapters/`
knows an event name belonging to any particular CLI. Two rules keep that boundary usable: a line no
adapter recognizes becomes `kind="raw"` rather than an exception, and text is capped when it is parsed
rather than when it is shown, so one command that printed four megabytes cannot sit in a reader's memory
at full size.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

#: Every kind an adapter may produce. `raw` is the escape hatch for a line no adapter recognized;
#: `log_truncated` is written by `Tail` itself, never by an adapter.
KINDS = ("run_started", "message", "thinking", "tool_call", "tool_result",
         "usage", "run_finished", "log_truncated", "cli_error", "raw")

#: Per-event character cap, applied at parse time. About memory, not about reading.
TEXT_CAP = 2000

_TRUNCATED = " …(truncated)"

#: The fixed-width label each kind renders as, so a transcript's second column always lines up.
_LABELS = {
    "run_started": "started", "message": "message", "thinking": "think",
    "tool_call": "tool", "tool_result": "result", "usage": "usage",
    "run_finished": "done", "log_truncated": "trimmed", "cli_error": "error", "raw": "raw",
}
_LABEL_WIDTH = max(len(v) for v in _LABELS.values()) + 1


def cap(text: object) -> str:
    """`text` as a string, truncated to `TEXT_CAP` with a visible marker. `None` becomes empty."""
    if text is None:
        return ""
    s = text if isinstance(text, str) else str(text)
    return s if len(s) <= TEXT_CAP else s[:TEXT_CAP] + _TRUNCATED


@dataclass(frozen=True)
class Event:
    """One thing a run did. `seq` is stamped by `Tail`, not by the adapter that parsed the line."""

    seq: int
    at: str
    kind: str
    text: str = ""
    tool: str | None = None
    ok: bool | None = None
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown event kind {self.kind!r}; expected one of {', '.join(KINDS)}")


def _clock(at: str) -> str:
    """`02:11:19` out of an ISO instant; the value unchanged when it is not one."""
    if len(at) >= 19 and at[10:11] in ("T", " ") and at[13:14] == ":":
        return at[11:19]
    return at


def render_line(e: Event, width: int = 100) -> str:
    """One transcript line: clock, a padded kind, then the event. Never contains a newline."""
    body = " ⏎ ".join(part.strip() for part in e.text.splitlines() if part.strip()) if e.text else ""
    if e.kind == "tool_call" and e.tool:
        body = f"{e.tool}: {body}" if body else e.tool
    elif e.kind == "tool_result" and e.ok is not None:
        outcome = "ok" if e.ok else "FAILED"
        body = f"{outcome} · {body}" if body else outcome
    line = f"{_clock(e.at)}  {_LABELS[e.kind]:<{_LABEL_WIDTH}} {body}".rstrip()
    return line if len(line) <= width else line[: width - 1] + "…"


def render(events: Iterable[Event], width: int = 100) -> str:
    """Every event as one line, in `seq` order."""
    return "\n".join(render_line(e, width) for e in sorted(events, key=lambda e: e.seq))
```

In `src/meld/config.py`, add two fields to the `Config` dataclass immediately after `audit_batch_hours: int = 4`:

```python
    logs_max_bytes_per_run: int = 8 * 1024 * 1024
    logs_warn_total_mb: int = 2048
```

In `Config.from_dict`, read the table alongside the other `data.get` lookups near `worker = data.get("worker", {})`:

```python
        logs = data.get("logs", {})
```

and pass both values in the constructor call, immediately after `audit_batch_hours=int(audit.get("batch_hours", 4)),`:

```python
            logs_max_bytes_per_run=int(logs.get("max_bytes_per_run", 8 * 1024 * 1024)),
            logs_warn_total_mb=int(logs.get("warn_total_mb", 2048)),
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_transcript.py tests/test_config.py -q -p no:cacheprovider
uv run pytest -q -p no:cacheprovider
```

Expected: the new tests pass; the full suite is 641 plus the new tests, with no warnings.

- [ ] **Step 5: Commit**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/transcript.py src/meld/config.py tests/test_transcript.py tests/test_config.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: one event model and renderer for run transcripts

Event is a closed vocabulary with raw as the escape hatch, so a CLI that
adds an event type degrades the view instead of breaking a run. Text is
capped at parse time because one command's megabytes of output must not
sit in a reader's memory at full size. [logs] carries the byte caps."
```

---

### Task 2: `Tail` — a byte-offset reader that survives partial lines and a trim

**Files:**
- Modify: `src/meld/transcript.py` (append `Tail`)
- Test: `tests/test_transcript.py`

**Interfaces:**
- Consumes: `Event`, `cap` from Task 1.
- Produces: `transcript.Tail(path: Path | str, *, offset: int = 0, seq: int = 0)` with read-only properties `offset: int` and `seq: int`, and `read(parse: Callable[[str, str], list[Event]], now: str) -> list[Event]`. `parse` is an adapter's `parse_events` (Task 3); `Tail` never imports an adapter.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_transcript.py`. `fake_parse` stands in for an adapter so this task does not depend on Task 3:

```python
import json

from meld.transcript import Tail

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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_transcript.py -q -p no:cacheprovider
```

Expected: `ImportError: cannot import name 'Tail' from 'meld.transcript'`.

- [ ] **Step 3: Write the implementation**

Append to `src/meld/transcript.py`, and add `from dataclasses import dataclass, field, replace`, `from pathlib import Path`, and `Callable` to the existing imports:

```python
class Tail:
    """A byte-offset reader over one growing log. Yields only complete lines, and notices a trim.

    Two failure modes this exists to prevent. A JSONL line read halfway is not a parse error to recover
    from, it is a line that has not happened yet, so an incomplete trailing line is held as bytes (not as
    text: a multi-byte character split across two reads must not be decoded twice). And a log that
    `trim_log` truncated under a reader would otherwise look like a file that had shrunk to nonsense, so a
    size below the held offset is reported as one `log_truncated` event and the offset resets to zero.
    """

    def __init__(self, path: Path | str, *, offset: int = 0, seq: int = 0) -> None:
        self.path = Path(path)
        self._offset = offset
        self._seq = seq
        self._partial = b""

    @property
    def offset(self) -> int:
        """Bytes consumed so far. A caller may hold this and rebuild the reader with it."""
        return self._offset

    @property
    def seq(self) -> int:
        """The last sequence number stamped."""
        return self._seq

    def _stamp(self, ev: Event) -> Event:
        self._seq += 1
        return replace(ev, seq=self._seq)

    def read(self, parse: Callable[[str, str], list[Event]], now: str) -> list[Event]:
        """Every event in the bytes appended since the last call. `parse` is an adapter's `parse_events`."""
        out: list[Event] = []
        try:
            size = self.path.stat().st_size
        except OSError:
            return out
        if size < self._offset:
            self._offset = 0
            self._partial = b""
            out.append(self._stamp(Event(seq=0, at=now, kind="log_truncated",
                                         text=cap(f"{self.path.name} was trimmed; resuming from the start"))))
        if size == self._offset:
            return out
        try:
            with self.path.open("rb") as f:
                f.seek(self._offset)
                chunk = f.read(size - self._offset)
        except OSError:
            return out
        self._offset += len(chunk)
        parts = (self._partial + chunk).split(b"\n")
        self._partial = parts.pop()          # "" when the chunk ended on a newline, else a partial line
        for raw in parts:
            line = raw.rstrip(b"\r").decode("utf-8", errors="replace")
            if not line.strip():
                continue
            for ev in parse(line, now):
                out.append(self._stamp(ev))
        return out
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_transcript.py -q -p no:cacheprovider
uv run pytest -q -p no:cacheprovider
```

Expected: PASS, whole suite green with no warnings.

- [ ] **Step 5: Commit**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/transcript.py tests/test_transcript.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: Tail reads a growing log without parsing half a line

An incomplete trailing line is held as bytes, not text, so a multi-byte
character split across two reads is not decoded twice. A file shorter
than the held offset means the log was trimmed, which becomes one
log_truncated event and a reset rather than a stream of nonsense."
```

---

### Task 3: `parse_events` on the adapter protocol, Claude and Codex

**Files:**
- Create: `tests/fixtures/transcripts/claude-run.jsonl`
- Create: `tests/fixtures/transcripts/codex-run.jsonl`
- Modify: `src/meld/adapters/base.py` (the `Adapter` protocol; a shared `parse_doc` helper)
- Modify: `src/meld/adapters/claude.py`, `src/meld/adapters/codex.py`
- Modify: `tests/fakeadapter.py`
- Test: `tests/test_adapter_claude.py`, `tests/test_adapter_codex.py`

**Interfaces:**
- Consumes: `transcript.Event`, `transcript.cap`, `transcript.Tail` from Tasks 1–2.
- Produces: `Adapter.parse_events(line: str, now: str) -> list[Event]` on the protocol and on `ClaudeAdapter`, `CodexAdapter`, and `FakeAdapter`; `adapters.base.parse_doc(line: str) -> tuple[dict | None, str]` (the JSON object and the stripped line, or `(None, stripped)` when the line is not a JSON object).

- [ ] **Step 1: Write the fixtures**

Create `tests/fixtures/transcripts/claude-run.jsonl`. The final `result` line is the redacted shape captured from the first live run's own log; the streamed lines above it are Claude Code's documented `stream-json` shape. The `stream_event` line and the bare-text line are deliberate: they prove the drop and the `raw` fallback.

```
{"type":"system","subtype":"init","session_id":"039d0c65","model":"claude-opus-5","cwd":"/tmp/wt/claude/47","tools":["Bash","Read"],"permissionMode":"bypassPermissions"}
{"type":"assistant","message":{"id":"msg_01","role":"assistant","model":"claude-opus-5","content":[{"type":"text","text":"Checking the board first."},{"type":"tool_use","id":"toolu_01","name":"Bash","input":{"command":"meld status","description":"Read the board"}}]},"session_id":"039d0c65"}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_01","content":"{\"repo\": \"acme/widgets\"}","is_error":false}]},"session_id":"039d0c65"}
{"type":"assistant","message":{"id":"msg_02","role":"assistant","model":"claude-opus-5","content":[{"type":"thinking","thinking":"The audit failed on defects already fixed on main.","signature":"sig"}]},"session_id":"039d0c65"}
{"type":"assistant","message":{"id":"msg_03","role":"assistant","model":"claude-opus-5","content":[{"type":"tool_use","id":"toolu_02","name":"Bash","input":{"command":"meld post STATUS -m 'board is clean'"}}]},"session_id":"039d0c65"}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_02","content":"refused: HALT is in effect","is_error":true}]},"session_id":"039d0c65"}
{"type":"stream_event","event":{"type":"content_block_delta"},"session_id":"039d0c65"}
a bare line the CLI printed before its first event
{"type":"result","subtype":"success","is_error":false,"num_turns":89,"duration_ms":1075447,"total_cost_usd":8.343978,"result":"PR 15 merged.","session_id":"039d0c65","usage":{"input_tokens":178,"output_tokens":76943,"cache_read_input_tokens":9568826}}
```

Create `tests/fixtures/transcripts/codex-run.jsonl`. The first line is a real stderr line from the first live run, which is why `raw` has to exist:

```
2026-09-12T02:11:19.320141Z ERROR codex_models_manager::cache: failed to load models cache
{"type": "thread.started", "thread_id": "01a09362-2bdd-79a1-b689-dc45eb695c2c"}
{"type": "turn.started"}
{"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "Starting with the required board check."}}
{"type": "item.started", "item": {"id": "item_1", "type": "command_execution", "command": "/bin/zsh -lc 'meld status'", "aggregated_output": "", "exit_code": null, "status": "in_progress"}}
{"type": "item.completed", "item": {"id": "item_1", "type": "command_execution", "command": "/bin/zsh -lc 'meld status'", "aggregated_output": "{\n  \"repo\": \"acme/widgets\"\n}", "exit_code": 0, "status": "completed"}}
{"type": "item.started", "item": {"id": "item_2", "type": "command_execution", "command": "/bin/zsh -lc 'meld dispatch 45'", "exit_code": null, "status": "in_progress"}}
{"type": "item.completed", "item": {"id": "item_2", "type": "command_execution", "command": "/bin/zsh -lc 'meld dispatch 45'", "aggregated_output": "refused: cannot review a memory PR", "exit_code": 1, "status": "completed"}}
{"type": "item.completed", "item": {"id": "item_3", "type": "web_search", "query": "an item type no adapter knows"}}
{"type": "turn.completed", "usage": {"input_tokens": 12000, "output_tokens": 800}}
```

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_adapter_claude.py`:

```python
from pathlib import Path

from meld.adapters.claude import ADAPTER
from meld.transcript import Tail, render

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
NOW = "2026-09-12T02:11:19Z"


def test_parse_events_renders_a_whole_claude_run():
    events = Tail(FIXTURES / "claude-run.jsonl").read(ADAPTER.parse_events, NOW)
    assert [e.kind for e in events] == [
        "run_started", "message", "tool_call", "tool_result", "thinking",
        "tool_call", "tool_result", "raw", "run_finished",
    ]
    assert render(events, width=96) == "\n".join([
        "02:11:19  started  claude-opus-5",
        "02:11:19  message  Checking the board first.",
        "02:11:19  tool     Bash: meld status",
        '02:11:19  result   ok · {"repo": "acme/widgets"}',
        "02:11:19  think    The audit failed on defects already fixed on main.",
        "02:11:19  tool     Bash: meld post STATUS -m 'board is clean'",
        "02:11:19  result   FAILED · refused: HALT is in effect",
        "02:11:19  raw      a bare line the CLI printed before its first event",
        "02:11:19  done     PR 15 merged.",
    ])


def test_parse_events_splits_one_assistant_line_into_its_content_blocks():
    line = ('{"type":"assistant","message":{"role":"assistant","content":['
            '{"type":"text","text":"Checking."},'
            '{"type":"tool_use","id":"toolu_9","name":"Bash","input":{"command":"meld status"}}]}}')
    events = ADAPTER.parse_events(line, NOW)
    assert [(e.kind, e.tool, e.text) for e in events] == [
        ("message", None, "Checking."), ("tool_call", "Bash", "meld status")]


def test_parse_events_marks_a_failed_tool_result():
    line = ('{"type":"user","message":{"role":"user","content":[{"type":"tool_result",'
            '"tool_use_id":"toolu_2","content":"refused: HALT is in effect","is_error":true}]}}')
    (event,) = ADAPTER.parse_events(line, NOW)
    assert (event.kind, event.ok, event.text) == ("tool_result", False, "refused: HALT is in effect")


def test_parse_events_reads_the_result_event_as_the_runs_end():
    line = ('{"type":"result","subtype":"success","is_error":false,"num_turns":89,'
            '"total_cost_usd":8.343978,"result":"PR 15 merged.","usage":{"output_tokens":76943}}')
    (event,) = ADAPTER.parse_events(line, NOW)
    assert (event.kind, event.ok, event.text) == ("run_finished", True, "PR 15 merged.")
    assert event.meta["num_turns"] == 89 and event.meta["cost_usd"] == 8.343978


def test_parse_events_drops_a_partial_stream_event_and_keeps_an_unknown_type_as_raw():
    assert ADAPTER.parse_events('{"type":"stream_event","event":{"type":"content_block_delta"}}', NOW) == []
    (unknown,) = ADAPTER.parse_events('{"type":"weather_report","temp":21}', NOW)
    assert unknown.kind == "raw" and "weather_report" in unknown.text


def test_parse_events_never_raises_on_junk():
    (raw,) = ADAPTER.parse_events("not json at all", NOW)
    assert raw.kind == "raw" and raw.text == "not json at all"
    (broken,) = ADAPTER.parse_events('{"type": "assistant", "message": {', NOW)
    assert broken.kind == "raw"
    assert ADAPTER.parse_events("   ", NOW) == []
```

Add to `tests/test_adapter_codex.py`:

```python
from pathlib import Path

from meld.adapters.codex import ADAPTER
from meld.transcript import Tail

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
NOW = "2026-09-12T02:11:19Z"


def test_parse_events_renders_a_whole_codex_run():
    events = Tail(FIXTURES / "codex-run.jsonl").read(ADAPTER.parse_events, NOW)
    assert [e.kind for e in events] == [
        "raw", "run_started", "message", "tool_call", "tool_result",
        "tool_call", "tool_result", "raw", "usage",
    ]
    assert events[0].text.startswith("2026-09-12T02:11:19.320141Z ERROR")


def test_parse_events_pairs_a_command_start_with_its_exit_code():
    start = ('{"type": "item.started", "item": {"id": "i1", "type": "command_execution", '
             '"command": "meld status", "exit_code": null, "status": "in_progress"}}')
    done = ('{"type": "item.completed", "item": {"id": "i1", "type": "command_execution", '
            '"command": "meld status", "aggregated_output": "ok", "exit_code": 0, "status": "completed"}}')
    (call,) = ADAPTER.parse_events(start, NOW)
    (result,) = ADAPTER.parse_events(done, NOW)
    assert (call.kind, call.tool, call.text) == ("tool_call", "command", "meld status")
    assert (result.kind, result.ok, result.meta["exit_code"]) == ("tool_result", True, 0)


def test_parse_events_marks_a_nonzero_exit_as_failed():
    done = ('{"type": "item.completed", "item": {"id": "i2", "type": "command_execution", '
            '"command": "meld dispatch 45", "aggregated_output": "refused", "exit_code": 1}}')
    (result,) = ADAPTER.parse_events(done, NOW)
    assert result.ok is False and result.meta["exit_code"] == 1


def test_parse_events_drops_turn_started_and_an_in_progress_message():
    assert ADAPTER.parse_events('{"type": "turn.started"}', NOW) == []
    assert ADAPTER.parse_events('{"type": "item.started", "item": {"type": "agent_message", "text": "x"}}', NOW) == []


def test_parse_events_keeps_an_unknown_item_type_as_raw():
    (raw,) = ADAPTER.parse_events('{"type": "item.completed", "item": {"type": "web_search", "query": "x"}}', NOW)
    assert raw.kind == "raw" and "web_search" in raw.text


def test_parse_events_never_raises_on_junk():
    (raw,) = ADAPTER.parse_events("thread panicked at src/main.rs:12", NOW)
    assert raw.kind == "raw"
    assert ADAPTER.parse_events("", NOW) == []
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
uv run pytest tests/test_adapter_claude.py tests/test_adapter_codex.py -q -p no:cacheprovider
```

Expected: `AttributeError: 'ClaudeAdapter' object has no attribute 'parse_events'`.

- [ ] **Step 4: Write the implementation**

In `src/meld/adapters/base.py`, add the import and the shared helper, and declare the method on the protocol:

```python
import json

from meld.transcript import Event, cap


def parse_doc(line: str) -> tuple[dict | None, str]:
    """`(the JSON object, the stripped line)`, or `(None, stripped)` when the line is not a JSON object.

    Every adapter's `parse_events` starts here, because every adapter faces the same two non-events: a
    blank line, and a line the CLI wrote to stderr that was never JSON at all (a Rust panic, a node
    warning, a cache error). Neither is a parse failure worth raising over.
    """
    s = line.strip()
    if not s or not s.startswith("{"):
        return None, s
    try:
        doc = json.loads(s)
    except json.JSONDecodeError:
        return None, s
    return (doc, s) if isinstance(doc, dict) else (None, s)


def raw_event(now: str, stripped: str) -> list[Event]:
    """The `raw` fallback: one event per unrecognized line, or nothing at all for a blank one."""
    return [Event(seq=0, at=now, kind="raw", text=cap(stripped))] if stripped else []
```

and inside `class Adapter(Protocol)`, next to the other method stubs:

```python
    def parse_events(self, line: str, now: str) -> list[Event]: ...
```

In `src/meld/adapters/claude.py`, add `from meld.adapters.base import ..., parse_doc, raw_event` to the existing base import and `from meld.transcript import Event, cap`, then add the method to `ClaudeAdapter`:

```python
    def parse_events(self, line: str, now: str) -> list[Event]:
        """One `stream-json` line as events. A content array is many events, which is why this is plural."""
        doc, stripped = parse_doc(line)
        if doc is None:
            return raw_event(now, stripped)
        kind = doc.get("type")
        if kind == "system":
            if doc.get("subtype") != "init":
                return []
            return [Event(seq=0, at=now, kind="run_started", text=cap(doc.get("model", "")),
                          meta={"session_id": str(doc.get("session_id", "")), "model": str(doc.get("model", ""))})]
        if kind == "stream_event":
            return []                                  # only present with --include-partial-messages
        if kind in ("assistant", "user"):
            message = doc.get("message")
            blocks = message.get("content") if isinstance(message, dict) else None
            if isinstance(blocks, str):                # a plain-string content field
                return [Event(seq=0, at=now, kind="message", text=cap(blocks))]
            if not isinstance(blocks, list):
                return raw_event(now, stripped)
            return [e for b in blocks if isinstance(b, dict) for e in _block(b, now)]
        if kind == "result":
            usage = doc.get("usage") if isinstance(doc.get("usage"), dict) else {}
            return [Event(seq=0, at=now, kind="run_finished", ok=not doc.get("is_error", False),
                          text=cap(doc.get("result", doc.get("subtype", ""))),
                          meta={"subtype": str(doc.get("subtype", "")), "num_turns": doc.get("num_turns"),
                                "cost_usd": doc.get("total_cost_usd"), "usage": usage})]
        return raw_event(now, stripped)
```

and the block translator as a module-level function in the same file:

```python
def _block(b: dict, now: str) -> list[Event]:
    """One content block as zero or one event. An unknown block type is silently skipped: the line it
    came from was recognized, so rendering the whole line as `raw` would duplicate what was understood."""
    kind = b.get("type")
    if kind == "text":
        return [Event(seq=0, at=now, kind="message", text=cap(b.get("text", "")))]
    if kind == "thinking":
        return [Event(seq=0, at=now, kind="thinking", text=cap(b.get("thinking", "")))]
    if kind == "tool_use":
        args = b.get("input") if isinstance(b.get("input"), dict) else {}
        summary = args.get("command") or args.get("file_path") or args.get("pattern") or args.get("description") or ""
        return [Event(seq=0, at=now, kind="tool_call", tool=cap(b.get("name", "tool")), text=cap(summary),
                      meta={"id": str(b.get("id", ""))})]
    if kind == "tool_result":
        content = b.get("content")
        if isinstance(content, list):
            content = " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
        return [Event(seq=0, at=now, kind="tool_result", ok=not b.get("is_error", False), text=cap(content),
                      meta={"id": str(b.get("tool_use_id", ""))})]
    return []
```

In `src/meld/adapters/codex.py`, same imports, then:

```python
    def parse_events(self, line: str, now: str) -> list[Event]:
        """One `--json` line as events. `turn.started` and an in-progress message carry nothing."""
        doc, stripped = parse_doc(line)
        if doc is None:
            return raw_event(now, stripped)
        kind = doc.get("type")
        if kind == "thread.started":
            thread = str(doc.get("thread_id", ""))
            return [Event(seq=0, at=now, kind="run_started", text=cap(thread), meta={"thread_id": thread})]
        if kind == "turn.started":
            return []
        if kind == "turn.completed":
            usage = doc.get("usage") if isinstance(doc.get("usage"), dict) else {}
            return [Event(seq=0, at=now, kind="usage",
                          text=cap(", ".join(f"{k}={v}" for k, v in sorted(usage.items()))), meta={"usage": usage})]
        if kind == "turn.failed":
            error = doc.get("error") if isinstance(doc.get("error"), dict) else {}
            return [Event(seq=0, at=now, kind="cli_error", ok=False, text=cap(error.get("message", "turn failed")))]
        if kind in ("item.started", "item.completed", "item.updated"):
            item = doc.get("item") if isinstance(doc.get("item"), dict) else {}
            return _item(kind, item, now, stripped)
        return raw_event(now, stripped)
```

and:

```python
def _item(kind: str, item: dict, now: str, stripped: str) -> list[Event]:
    """One `item.*` event. Only the completed form of a message or a command carries its content."""
    item_type = item.get("type") or item.get("item_type")
    if item_type in ("agent_message", "reasoning"):
        if kind != "item.completed":
            return []
        event_kind = "message" if item_type == "agent_message" else "thinking"
        return [Event(seq=0, at=now, kind=event_kind, text=cap(item.get("text", "")))]
    if item_type == "command_execution":
        command = cap(item.get("command", ""))
        if kind == "item.started":
            return [Event(seq=0, at=now, kind="tool_call", tool="command", text=command,
                          meta={"id": str(item.get("id", ""))})]
        if kind == "item.updated":
            return []
        code = item.get("exit_code")
        return [Event(seq=0, at=now, kind="tool_result", tool="command", ok=(code == 0),
                      text=cap(item.get("aggregated_output", "")),
                      meta={"exit_code": code, "command": command, "id": str(item.get("id", ""))})]
    return raw_event(now, stripped)
```

In `tests/fakeadapter.py`, satisfy the protocol so no existing test breaks:

```python
    def parse_events(self, line: str, now: str) -> list[Event]:
        """The fake CLI writes plain text, so every line is one message."""
        return [Event(seq=0, at=now, kind="message", text=cap(line))] if line.strip() else []
```

with `from meld.transcript import Event, cap` at the top of that file.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/test_adapter_claude.py tests/test_adapter_codex.py -q -p no:cacheprovider
uv run pytest -q -p no:cacheprovider
```

Expected: PASS, whole suite green with no warnings.

- [ ] **Step 6: Commit**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/adapters tests/fakeadapter.py tests/test_adapter_claude.py tests/test_adapter_codex.py tests/fixtures/transcripts
git -C /Users/scottbrant/code/meld-main commit -m "feat: each adapter translates its own CLI's events

parse_events is plural because one line is not one event: a Claude
assistant line carries a content array holding a sentence and the tool
call after it. An unknown type or a non-JSON line becomes raw, so a CLI
that grows an event type degrades the view instead of breaking a run."
```

---

### Task 4: the Claude adapter streams

**Files:**
- Modify: `src/meld/adapters/claude.py` (`argv`, `REQUIRED_FLAGS`)
- Test: `tests/test_adapter_claude.py`, `tests/test_doctor.py`

**Interfaces:**
- Consumes: Task 3's `parse_events` on the same adapter.
- Produces: no new symbol. `ClaudeAdapter.argv` now emits `--output-format stream-json --verbose`, and `--verbose` joins `REQUIRED_FLAGS` so `meld doctor`'s `cli-flags:claude` check covers it.

This is the change that makes a Claude role watchable at all. Until now its whole log was one JSON object written at exit, so nothing could be read while it ran and section 17's "last 50 log lines" was one unreadable line. Worker correctness is untouched: an outcome is the `.meld-result.json` the worker writes in its worktree, never the log.

- [ ] **Step 1: Write the failing test**

Change the existing assertion in `tests/test_adapter_claude.py` (it currently reads `assert argv[argv.index("--output-format") + 1] == "json"`):

```python
def test_argv_streams_events_and_verbose_is_required_for_it():
    argv = ADAPTER.argv(_spec())
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    # claude -p refuses stream-json without --verbose, so the flag is part of the contract, not a preference.
    assert "--verbose" in argv
    assert "--include-partial-messages" not in argv          # token-level streaming is out of plan 8 (spec §21)
    assert "--verbose" in ADAPTER.required_flags()
```

Reuse whatever `LaunchSpec` helper that file already builds for its other tests rather than writing a new one; if the existing tests construct the spec inline, construct it the same way.

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_adapter_claude.py -q -p no:cacheprovider
```

Expected: `AssertionError: assert 'json' == 'stream-json'`.

- [ ] **Step 3: Write the implementation**

In `src/meld/adapters/claude.py`, change the two lines in `argv`:

```python
            "--output-format", "stream-json",
            "--verbose",                       # claude -p refuses stream-json without it
```

and add the flag to the tuple:

```python
REQUIRED_FLAGS = ("-p", "--model", "--output-format", "--verbose", "--permission-mode", "--strict-mcp-config",
                  "--mcp-config", "--settings", "--setting-sources")
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_adapter_claude.py tests/test_doctor.py -q -p no:cacheprovider
uv run pytest -q -p no:cacheprovider
```

Expected: PASS. If a doctor test asserts the exact `REQUIRED_FLAGS` tuple, update it to include `--verbose`; do not weaken the assertion to a subset.

- [ ] **Step 5: Commit**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/adapters/claude.py tests/test_adapter_claude.py tests/test_doctor.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: the claude adapter streams its run

--output-format json wrote one blob at exit, so a claude role was
invisible until it died and the failure tail was one unreadable line.
--verbose is required alongside stream-json, so it joins REQUIRED_FLAGS
and doctor fails loudly if a future CLI drops either."
```

---

### Task 5: the log cap, the `tick` sweep, and doctor's size check

**Files:**
- Modify: `src/meld/transcript.py` (append `trim_log`, `sweep_logs`, `total_bytes`)
- Modify: `src/meld/dispatch.py` (`tick` calls `sweep_logs`)
- Modify: `src/meld/doctor.py` (a `logs-size` check)
- Test: `tests/test_transcript.py`, `tests/test_dispatch.py`, `tests/test_doctor.py`

**Interfaces:**
- Consumes: `Event`, `cap` from Task 1; `Tail`'s truncation behaviour from Task 2.
- Produces: `transcript.trim_log(path: Path, max_bytes: int, *, keep_bytes: int | None = None) -> bool`; `transcript.sweep_logs(logs_root: Path, max_bytes: int) -> list[Path]`; `transcript.total_bytes(logs_root: Path) -> int`.

The mechanism matters and is not the obvious one. `popen_detached` hands the open log to the CLI as its stdout, and a worker outlives the `meld` process that started it, so renaming the file would leave the writer appending to the renamed inode and the live reader watching a dead file. `open_log` opens with `"ab"`, so the descriptor carries `O_APPEND` and every write recomputes the offset from the file's end. Truncating to zero is therefore safe: the writer's next line lands at the new end. This was verified against a live child process before this plan was written.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_transcript.py`:

```python
import subprocess
import sys
import time

from meld.transcript import sweep_logs, total_bytes, trim_log


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
```

Add to `tests/test_doctor.py`, following whatever helper that file already uses to build a `Config` and call `run_checks`:

`tests/test_doctor.py` already has `cfg()` (a `Config` from `PROJECT_TOML`), `fakes()`, a `run(tmp_path, by_token, ...)` helper that calls `run_checks` with `home=tmp_path / "home"`, and `by_name(checks)`. Use them, write the logs under that same home, and build the over-threshold config the way the module's unknown-cli test builds `bad_cfg` — by editing the TOML text, not by mutating a frozen `Config`:

```python
def test_logs_size_check_reports_the_total_without_failing(tmp_path):
    logs = tmp_path / "home" / "logs" / "claude"
    logs.mkdir(parents=True)
    (logs / "orchestrator-1.log").write_bytes(b"x" * (3 * 1024 * 1024))

    check = by_name(run(tmp_path, fakes()))["logs-size"]
    assert check.ok is True                         # 3 MB is far under the 2048 MB default
    assert check.detail == f"3 MB of run logs under {tmp_path / 'home' / 'logs'}"
    assert check.fix == ""
    assert "logs-size" not in summary(run(tmp_path, fakes()))["failed"]


def test_logs_size_check_warns_but_never_fails_when_the_total_is_over(tmp_path):
    logs = tmp_path / "home" / "logs" / "claude"
    logs.mkdir(parents=True)
    (logs / "orchestrator-1.log").write_bytes(b"x" * (3 * 1024 * 1024))
    loud = Config.from_dict(tomllib.loads(PROJECT_TOML + "\n[logs]\nwarn_total_mb = 2\n"))

    by_token = fakes()
    checks = by_name(run_checks(loud, tmp_path, env={"MELD_TOKEN_CLAUDE": "tok-c", "MELD_TOKEN_CODEX": "tok-x"},
                                gh_factory=lambda token: GitHub("acme/widgets", token=token, run=by_token[token]),
                                registry=ModelRegistry.load(),
                                adapter_for=lambda name: FakeAdapter(ceiling=600, name=name),
                                which=lambda b: "/bin/" + b, home=tmp_path / "home",
                                user_home=tmp_path / "userhome"))
    check = checks["logs-size"]
    assert check.ok is None                         # a warning, never a failure: nothing is broken
    assert "3 MB" in check.detail and "meld tick" in check.fix
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_transcript.py tests/test_doctor.py -q -p no:cacheprovider
```

Expected: `ImportError: cannot import name 'trim_log'`, then a `KeyError: 'logs-size'`.

- [ ] **Step 3: Write the implementation**

Append to `src/meld/transcript.py`, adding `import os` to its imports:

```python
def trim_log(path: Path, max_bytes: int, *, keep_bytes: int | None = None) -> bool:
    """Cap one log by truncating it in place. `True` when it was trimmed.

    Not a rename: the CLI writing this file holds its descriptor and the run outlives the `meld` process
    (section 11), so renaming would leave the writer appending to the renamed inode. `open_log` opens with
    `O_APPEND`, which recomputes the offset from the file's end on every write, so truncating to zero lets
    the writer's next line land at the new end. The last `keep_bytes` go to `<name>.1` for post-mortem
    reading; a previous `.1` is replaced, so the newest two windows survive and no more.
    """
    keep = max_bytes // 4 if keep_bytes is None else keep_bytes
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size <= max_bytes:
        return False
    try:
        with path.open("rb") as f:
            f.seek(max(0, size - keep))
            tail = f.read()
        path.with_suffix(path.suffix + ".1").write_bytes(tail)
        os.truncate(path, 0)
    except OSError:
        return False
    return True


def sweep_logs(logs_root: Path, max_bytes: int) -> list[Path]:
    """Trim every `*.log` under `logs_root` that is over the cap; returns what was trimmed."""
    if not logs_root.is_dir():
        return []
    return [p for p in sorted(logs_root.rglob("*.log")) if trim_log(p, max_bytes)]


def total_bytes(logs_root: Path) -> int:
    """Every byte under `logs_root`, kept tails included. Unreadable entries count as zero."""
    if not logs_root.is_dir():
        return 0
    total = 0
    for p in logs_root.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total
```

In `src/meld/dispatch.py`, add `from meld.transcript import sweep_logs` and call it inside `tick`, at the top of the function body before the per-issue work, so a runaway log is capped on the same cadence everything else is:

```python
    sweep_logs(ctx.store.home / "logs", ctx.cfg.logs_max_bytes_per_run)
```

In `src/meld/doctor.py`, add `from meld.transcript import total_bytes` and append the check beside the existing `memory-size` one:

```python
    logs_mb = total_bytes(home / "logs") // (1024 * 1024)
    over = logs_mb > cfg.logs_warn_total_mb
    checks.append(Check("logs-size", None if over else True, f"{logs_mb} MB of run logs under {home / 'logs'}",
                        f"streamed transcripts accumulate; `meld tick` caps a single run at "
                        f"{cfg.logs_max_bytes_per_run // (1024 * 1024)} MB, but old runs are yours to delete" if over else ""))
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_transcript.py tests/test_dispatch.py tests/test_doctor.py -q -p no:cacheprovider
uv run pytest -q -p no:cacheprovider
```

Expected: PASS, whole suite green with no warnings.

- [ ] **Step 5: Commit**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/transcript.py src/meld/dispatch.py src/meld/doctor.py tests/test_transcript.py tests/test_dispatch.py tests/test_doctor.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: cap a live log in place, and sweep from tick

A streamed transcript is one to two orders of magnitude larger than the
result blob it replaces, and an exhausted disk is what killed both
supervisors on 2026-09-12. The cap truncates rather than renames,
because the child holds the descriptor: O_APPEND puts its next write at
the new end. doctor reports the total."
```

---

### Task 6: `meld tail`

**Files:**
- Modify: `src/meld/transcript.py` (append `newest_log`)
- Modify: `src/meld/cli.py` (the `tail` command, and a `SLEEP` hook beside the existing `RUN`/`ADAPTER_FOR`)
- Test: `tests/test_transcript.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `Tail`, `render_line` (Tasks 1–2); each adapter's `parse_events` (Task 3).
- Produces: `transcript.newest_log(logs_root: Path, target: str | None = None) -> Path | None`; the `meld tail [TARGET] [--run N] [--follow/--no-follow] [--width N] [--interval S]` command; `cli.SLEEP = time.sleep`, which tests monkeypatch so nothing in the suite actually sleeps.

`meld tail` is a human's verb with no party. It reads files under `$MELD_HOME` and prints; it takes no `MELD_GROUP`, touches no board, and posts nothing, so it never calls `load_context` and never reaches GitHub. It loads the config only to map a group to its CLI.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_transcript.py`:

```python
from meld.transcript import newest_log


def _log(root, group, name, mtime):
    import os
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
```

Add to `tests/test_cli.py`. `PROJECT_TOML` and the hermetic-environment fixtures already exist in the suite; write the config and the log under a `MELD_HOME` the test controls:

```python
import meld.cli as cli
from tests.conftest import PROJECT_TOML
from tests.fakeadapter import FakeAdapter

CLAUDE_LINE = ('{"type":"assistant","message":{"role":"assistant","content":'
               '[{"type":"text","text":"Checking the board first."}]}}')


def _tail_home(tmp_path, monkeypatch, line=CLAUDE_LINE):
    config = tmp_path / ".meld.toml"
    config.write_text(PROJECT_TOML)
    logs = tmp_path / "home" / "logs" / "claude"
    logs.mkdir(parents=True)
    (logs / "orchestrator-20260912T020000Z.log").write_text(line + "\n")
    monkeypatch.setenv("MELD_HOME", str(tmp_path / "home"))
    return config


def test_tail_renders_the_newest_log_once_without_following(tmp_path, monkeypatch):
    config = _tail_home(tmp_path, monkeypatch)
    result = CliRunner().invoke(main, ["--config", str(config), "tail", "--no-follow"])
    assert result.exit_code == 0
    assert "message  Checking the board first." in result.output


def test_tail_names_the_log_it_chose_on_stderr_not_in_the_transcript(tmp_path, monkeypatch):
    config = _tail_home(tmp_path, monkeypatch)
    result = CliRunner(mix_stderr=False).invoke(main, ["--config", str(config), "tail", "--no-follow"])
    assert "orchestrator-20260912T020000Z.log" in result.stderr
    assert "orchestrator-20260912T020000Z.log" not in result.stdout


def test_tail_accepts_a_group_and_role_target(tmp_path, monkeypatch):
    config = _tail_home(tmp_path, monkeypatch)
    result = CliRunner().invoke(main, ["--config", str(config), "tail", "claude/orchestrator", "--no-follow"])
    assert result.exit_code == 0 and "Checking the board first." in result.output


def test_tail_explains_itself_when_there_is_no_log(tmp_path, monkeypatch):
    config = tmp_path / ".meld.toml"
    config.write_text(PROJECT_TOML)
    monkeypatch.setenv("MELD_HOME", str(tmp_path / "empty"))
    result = CliRunner().invoke(main, ["--config", str(config), "tail", "--no-follow"])
    assert result.exit_code != 0
    assert "no run log" in result.output


def test_tail_refuses_an_unknown_group(tmp_path, monkeypatch):
    config = _tail_home(tmp_path, monkeypatch)
    result = CliRunner().invoke(main, ["--config", str(config), "tail", "ghost/pm", "--no-follow"])
    assert result.exit_code != 0 and "ghost" in result.output


def test_tail_follows_until_the_sleep_hook_stops_it(tmp_path, monkeypatch):
    """--follow loops; the suite never sleeps, so SLEEP raises to end the loop deterministically."""
    config = _tail_home(tmp_path, monkeypatch)
    calls = []

    def fake_sleep(seconds):
        calls.append(seconds)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "SLEEP", fake_sleep)
    result = CliRunner().invoke(main, ["--config", str(config), "tail", "--follow", "--interval", "0.5"])
    assert result.exit_code == 0
    assert calls == [0.5]
    assert "Checking the board first." in result.output
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_transcript.py tests/test_cli.py -q -p no:cacheprovider
```

Expected: `ImportError: cannot import name 'newest_log'`, then `Error: No such command 'tail'`.

- [ ] **Step 3: Write the implementation**

Append to `src/meld/transcript.py`:

```python
def newest_log(logs_root: Path, target: str | None = None) -> Path | None:
    """The most recently written log, narrowed by `<group>` or `<group>/<prefix>`; `None` when there is none.

    Every log a run writes lands in `logs/<group>/`: a role's as `<role>-<stamp>.log`, a worker's as
    `<kind>-<issue>-<stamp>.log`. One glob therefore covers both, and a prefix selects either a role
    (`claude/orchestrator`) or a worker kind (`claude/audit`).
    """
    if not logs_root.is_dir():
        return None
    group, _, prefix = (target or "").partition("/")
    candidates = [p for p in logs_root.glob(f"{group or '*'}/{prefix}*.log") if p.is_file()]
    return max(candidates, key=lambda p: (p.stat().st_mtime, p.name), default=None)
```

In `src/meld/cli.py`, add `import time` to the imports, the hook beside the existing ones at the top:

```python
SLEEP = time.sleep  # tests monkeypatch this so the suite never actually sleeps
```

the new imports:

```python
from meld.config import ConfigError, load_config
from meld.state import CHANGE_KINDS, meld_home
from meld.transcript import Tail, newest_log, render_line
```

(`load_config`, `meld_home`, and `find_config` are already imported; add only what is missing.)

and the command:

```python
@main.command()
@click.argument("target", required=False)
@click.option("--run", "issue", type=int, default=None,
              help="Tail a dispatched run by issue number instead of naming a log.")
@click.option("--follow/--no-follow", default=True, help="Keep reading until interrupted (Ctrl-C).")
@click.option("--width", default=100, show_default=True, help="Truncate each rendered line to this width.")
@click.option("--interval", default=1.0, show_default=True, help="Seconds between reads while following.")
@click.pass_context
def tail(ctx: click.Context, target: str | None, issue: int | None, follow: bool, width: int, interval: float) -> None:
    """Render a run's transcript as it is written.

    TARGET is `<group>` or `<group>/<role-or-kind>`; with neither TARGET nor --run, the newest log wins.
    Reads local files only: no board, no party, nothing posted.
    """
    config_path = ctx.obj["config_path"] or _guard(lambda: find_config(Path.cwd()))
    cfg = _guard(lambda: load_config(config_path))
    home = meld_home(os.environ)

    if issue is not None:
        record = None
        for group_runs in Store(home, None).load_runs().values():
            record = group_runs.get(issue) or record
        if record is None:
            raise click.ClickException(f"no run record for issue #{issue} under {home / 'runs'}")
        log_path, group = Path(record.log_path), record.group
    else:
        if target and target.partition("/")[0] not in cfg.groups:
            raise click.ClickException(f"unknown group {target.partition('/')[0]!r}; configured: {', '.join(cfg.groups)}")
        found = newest_log(home / "logs", target)
        if found is None:
            where = f" for {target}" if target else ""
            raise click.ClickException(f"no run log{where} under {home / 'logs'}")
        log_path, group = found, found.parent.name

    group_cfg = cfg.groups.get(group)
    if group_cfg is None:
        raise click.ClickException(f"log {log_path} belongs to group {group!r}, which is not in {config_path}")
    adapter = _guard(lambda: ADAPTER_FOR(group_cfg.cli))
    click.echo(f"{log_path} ({group}, {group_cfg.cli})", err=True)

    reader = Tail(log_path)
    while True:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for event in reader.read(adapter.parse_events, now):
            click.echo(render_line(event, width))
        if not follow:
            return
        try:
            SLEEP(interval)
        except KeyboardInterrupt:
            click.echo("", err=True)
            return
```

`Store` needs importing from `meld.state` alongside `meld_home`. The log's own path names its group, which is why nothing here needs `MELD_GROUP`.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_transcript.py tests/test_cli.py -q -p no:cacheprovider
uv run pytest -q -p no:cacheprovider
```

Expected: PASS, whole suite green with no warnings. Confirm by hand that `uv run meld tail --help` lists the options.

- [ ] **Step 5: Commit**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/transcript.py src/meld/cli.py tests/test_transcript.py tests/test_cli.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: meld tail renders a run's transcript as it is written

A human verb with no party: it reads logs under MELD_HOME and prints,
touching no board and posting nothing. The log's own path names its
group, so the adapter that parses it is chosen from the config rather
than from MELD_GROUP."
```

---

### Task 7: the failure tail is rendered, and the docs say so

**Files:**
- Modify: `src/meld/dispatch.py` (the failure comment's tail)
- Modify: `docs/HANDOFF.md`, `README.md`
- Test: `tests/test_dispatch.py`

**Interfaces:**
- Consumes: `Tail`, `render`, `newest_log` (Tasks 1–2, 6); each adapter's `parse_events` (Task 3).
- Produces: `dispatch.render_tail(path: Path, adapter, lines: int, now: str) -> str`.

This is what section 17 promised. Today `tail_lines` reads the last N lines raw, which for a streamed log is JSONL and for the pre-plan-8 Claude log was one 5 KB line. Rendering it through the transcript makes the comment a human can act on. The rendered text still passes through `neutralise_closers` at the call site exactly as the raw tail does, because it is a worker's words reaching GitHub.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_dispatch.py`:

```python
from meld.dispatch import render_tail
from meld.adapters.codex import ADAPTER as CODEX

NOW = "2026-09-12T02:11:19Z"


def test_render_tail_renders_events_rather_than_json(tmp_path):
    log = tmp_path / "audit-14.log"
    log.write_text("\n".join([
        '{"type": "thread.started", "thread_id": "t1"}',
        '{"type": "item.completed", "item": {"type": "agent_message", "text": "Running the suite."}}',
        '{"type": "item.completed", "item": {"id": "i1", "type": "command_execution", '
        '"command": "uv run pytest -q", "aggregated_output": "1 failed", "exit_code": 1}}',
    ]) + "\n")
    rendered = render_tail(log, CODEX, lines=50, now=NOW)
    assert "message  Running the suite." in rendered
    assert "result   FAILED · 1 failed" in rendered
    assert "thread.started" not in rendered          # the JSON itself never reaches the comment


def test_render_tail_keeps_only_the_last_n_events(tmp_path):
    log = tmp_path / "audit-14.log"
    log.write_text("".join(
        '{"type": "item.completed", "item": {"type": "agent_message", "text": "line %d"}}\n' % i
        for i in range(10)))
    rendered = render_tail(log, CODEX, lines=3, now=NOW)
    assert rendered.count("\n") == 2
    assert "line 9" in rendered and "line 6" not in rendered


def test_render_tail_of_a_missing_or_empty_log_is_empty(tmp_path):
    assert render_tail(tmp_path / "absent.log", CODEX, lines=50, now=NOW) == ""
    (tmp_path / "empty.log").write_text("")
    assert render_tail(tmp_path / "empty.log", CODEX, lines=50, now=NOW) == ""
```

Then find the existing test that asserts the failure comment's body (it matches on `"Last 50 log lines"`) and update its expectation to the rendered form. Search for it with:

```bash
grep -rn "log lines" tests/ src/meld/
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_dispatch.py -q -p no:cacheprovider
```

Expected: `ImportError: cannot import name 'render_tail' from 'meld.dispatch'`.

- [ ] **Step 3: Write the implementation**

In `src/meld/dispatch.py`, add `from meld.transcript import Tail, render, sweep_logs` (extending Task 5's import) and the function beside `tail_lines`, which stays for any caller that wants raw bytes:

```python
def render_tail(path: Path, adapter, lines: int, now: str) -> str:
    """The last `lines` events of a run's log, rendered. Empty when there is nothing to read.

    `tail_lines` above returns raw bytes, which a streamed transcript makes unreadable: JSONL at best,
    and under the pre-plan-8 Claude adapter one 5 KB line that said nothing a person could act on.
    """
    events = Tail(path).read(adapter.parse_events, now)
    return render(events[-lines:]) if events else ""
```

and at the failure call site, replace the raw tail with the rendered one. The surrounding lines read:

```python
    result, reason = _read_outcome(rec)
    tail = tail_lines(Path(rec.log_path), ctx.cfg.worker_log_tail_lines)
```

Change the second line to:

```python
    tail = render_tail(Path(rec.log_path), adp, ctx.cfg.worker_log_tail_lines, _iso(_now()))
```

`adp` is the group's adapter; if it is not already in scope at that point in `collect`, resolve it the same way the function's dispatch path does (`ADAPTER_FOR`/`ctx.adapter_for` for `group.cli`) rather than importing an adapter module directly — no module outside `adapters/` may name a CLI.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_dispatch.py -q -p no:cacheprovider
uv run pytest -q -p no:cacheprovider
```

Expected: PASS, whole suite green with no warnings.

- [ ] **Step 5: Update the docs**

In `README.md`, add `tail` to the verb list beside `status` and `run`, with one line: "render a run's transcript as it is written".

In `docs/HANDOFF.md`, add a `## Plan 8a — normalized transcripts (landed)` section above `## What is next` recording:

- What landed: one `Event` vocabulary, `Tail`, the renderer, the log cap, `meld tail`, the rendered failure tail, and each adapter's `parse_events`.
- The Claude adapter now launches with `--output-format stream-json --verbose`; a role's log is JSONL, not one blob, and `--verbose` is in `REQUIRED_FLAGS` so `doctor` catches a future CLI dropping either flag.
- The cap truncates in place rather than renaming, because the child holds the descriptor; `Tail` reports the trim as an event.
- Still open, in order: plan 8b (the loopback viewer, read-only), 8c (the human verbs in the page), 8d (the `.meld.toml` form). Spec section 22 is the binding design for all four; section 21 lists what plan 8 leaves out.
- The fixture caveat: `tests/fixtures/transcripts/claude-run.jsonl`'s `result` line is a real redacted capture, the streamed lines above it are written from the documented shape, and the `raw` fallback is what covers the difference until a live run contributes a full capture.

Then update the `## What is next` list so plan 8a is no longer described as unstarted.

- [ ] **Step 6: Commit**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/dispatch.py tests/test_dispatch.py README.md docs/HANDOFF.md
git -C /Users/scottbrant/code/meld-main commit -m "feat: a failed worker's log tail is rendered, not raw

Section 17 promised a tail a person could act on. Raw bytes of a
streamed log are JSONL, and raw bytes of the old claude log were one
5 KB line. render_tail puts the last events through the transcript
renderer instead."
```

---

## Self-review

**Spec coverage.** Section 22's "Normalized transcripts" is covered end to end: the two adapter changes (Tasks 3–4), `Event` and the closed vocabulary with `raw` and the dropped types (Tasks 1, 3), `Tail`'s partial-line and truncation behaviour (Task 2), `TEXT_CAP` at parse time (Task 1), the event mapping table for both CLIs (Task 3), `meld tail` in both its forms (Task 6), the log-volume cap with `trim_log`/`O_APPEND` and doctor's total (Task 5), and section 17's rendered failure tail (Task 7). Section 5's `[logs]` table is Task 1. Section 19's `test_transcript.py` is Tasks 1–2 and 5–6; its `test_viewer.py` and `test_config_write.py` belong to plans 8b and 8d and are deliberately absent here. Section 4's `viewer/` directory is plan 8b. Section 22's "The viewer" and "The configuration form" are out of scope for this plan by its own header.

**Not covered, deliberately.** `[viewer]` config (section 5) is parsed in plan 8b, not here, because nothing in 8a reads a port. `Config` therefore gains `logs_*` only.

**Placeholder scan.** No "TBD", no "add error handling", no "similar to Task N". Two places name a search rather than a line number, both because the target is a string the executor must locate in a file this plan does not otherwise touch: Task 4's possible `REQUIRED_FLAGS` assertion in `test_doctor.py`, and Task 7's existing `"Last 50 log lines"` assertion, which comes with the `grep` that finds it.

**Type consistency.** `parse_events(line, now) -> list[Event]` is the same name and signature in the protocol, both adapters, `FakeAdapter`, `Tail.read`'s `parse` argument, and `render_tail`. `Event`'s field order is fixed in Task 1 and every later construction uses keywords. `Tail(path, *, offset, seq)` with `offset`/`seq` properties is used identically in Tasks 2, 6, and 7. `trim_log(path, max_bytes, *, keep_bytes)` returns `bool` and `sweep_logs(logs_root, max_bytes)` returns `list[Path]` in both Task 5's tests and its callers. `newest_log(logs_root, target)` returns `Path | None` in Tasks 6's tests and the CLI.

**One risk worth naming.** Task 4 changes how every Claude role is launched, and the suite cannot prove the real CLI still behaves, because no test may run a CLI. Three things bound that risk: worker correctness never read the log (an outcome is `.meld-result.json`), `--verbose` is in `REQUIRED_FLAGS` so `meld doctor` fails loudly if the flag pair stops existing, and an unrecognized line renders as `raw` rather than raising. The first live run after this plan should capture one real Claude transcript into `tests/fixtures/transcripts/` and tighten Task 3's expectations against it.
