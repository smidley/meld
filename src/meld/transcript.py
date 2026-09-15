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
