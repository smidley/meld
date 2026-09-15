"""One event model for every adapter's run output, plus the reader and renderer above it.

An adapter translates its own CLI's vocabulary into `Event` (spec section 22); nothing above `adapters/`
knows an event name belonging to any particular CLI. Two rules keep that boundary usable: a line no
adapter recognizes becomes `kind="raw"` rather than an exception, and text is capped when it is parsed
rather than when it is shown, so one command that printed four megabytes cannot sit in a reader's memory
at full size.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable

#: Every kind an adapter may produce. `raw` is the escape hatch for a line no adapter recognized;
#: `log_truncated` is written by `Tail` itself, never by an adapter.
KINDS = ("run_started", "message", "thinking", "tool_call", "tool_result",
         "usage", "run_finished", "log_truncated", "cli_error", "raw")

#: Per-event character cap, applied at parse time. About memory, not about reading.
TEXT_CAP = 2000

#: Ceiling on the unterminated trailing bytes one `Tail` holds between reads. `TEXT_CAP` guards an event's
#: text once a line has been parsed; this guards the bytes before there is a line at all, so a writer that
#: emits a megabyte without a newline cannot grow a reader without bound. Generously above any real event
#: line (500x `TEXT_CAP`), so only a pathological writer ever meets it.
PARTIAL_CAP = 1 << 20

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


class Tail:
    """A byte-offset reader over one growing log. Yields only complete lines, and notices a trim.

    Two failure modes this exists to prevent. A JSONL line read halfway is not a parse error to recover
    from, it is a line that has not happened yet, so an incomplete trailing line is held as bytes (not as
    text: a multi-byte character split across two reads must not be decoded twice). And a log that
    `trim_log` truncated under a reader would otherwise look like a file that had shrunk to nonsense, so a
    size below the held offset is reported as one `log_truncated` event and the offset resets to zero.

    Held bytes are capped at `PARTIAL_CAP`: a line that has not ended after a megabyte is a writer that is
    never going to end it, and only what a reader holds *between* reads can grow without bound (a long line
    that does arrive with its newline is parsed whole, however long it is). And a reader being retired calls
    `flush`, because once nothing more is coming the unterminated line is no longer "not yet" -- it is the
    crash message a dying CLI wrote without a newline.
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
        # "" when the chunk ended on a newline, else a partial line -- kept head-first and bounded, so a
        # never-terminated line still renders its opening bytes (as `raw`: a clipped JSON object does not parse).
        self._partial = parts.pop()[:PARTIAL_CAP]
        for raw in parts:
            line = raw.rstrip(b"\r").decode("utf-8", errors="replace")
            if not line.strip():
                continue
            for ev in parse(line, now):
                out.append(self._stamp(ev))
        return out

    def flush(self, parse: Callable[[str, str], list[Event]], now: str) -> list[Event]:
        """The held partial line as a final event, for a reader that will not read again.

        `read` withholds an unterminated trailing line because in a growing log it is a line that has not
        happened yet. When nothing further is coming -- a one-shot `--no-follow` read, or the failure tail of
        a worker that died -- that residue is the most interesting line in the file, because a process that
        crashed mid-write left no newline behind it. It is parsed like any other line, so a truncated JSON
        object arrives as `raw` through `parse_doc`. Following a live log must NOT call this.
        """
        residue, self._partial = self._partial, b""
        line = residue.rstrip(b"\r").decode("utf-8", errors="replace")
        if not line.strip():
            return []
        return [self._stamp(ev) for ev in parse(line, now)]


def trim_log(path: Path, max_bytes: int, *, keep_bytes: int | None = None) -> bool:
    """Cap one log by truncating it in place. `True` when it was trimmed.

    Not a rename: the CLI writing this file holds its descriptor and the run outlives the `meld` process
    (section 11), so renaming would leave the writer appending to the renamed inode. `open_log` opens with
    `O_APPEND`, which recomputes the offset from the file's end on every write, so truncating to zero lets
    the writer's next line land at the new end. The last `keep_bytes` go to `<name>.1` for post-mortem
    reading; a previous `.1` is replaced, so the newest two windows survive and no more.

    Read the tail, truncate, *then* write the sidecar -- in that order, because the moment this mechanism
    exists for is a disk with no free bytes. Writing `.1` first fails with ENOSPC and frees nothing, and
    until it fails it transiently ADDS up to `keep_bytes`, so on a log's first trim it can be the write
    that crosses the line. A `.1` this could not write is reported as a trim anyway: the space is the
    incident, the tail is only the post-mortem.
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
        os.truncate(path, 0)
    except OSError:
        return False
    try:
        path.with_suffix(path.suffix + ".1").write_bytes(tail)
    except OSError:
        pass                                 # the space is freed; losing the tail is the cheaper loss
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
