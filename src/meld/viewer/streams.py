"""Which transcripts the viewer can show, and one of them as a stream of server-sent events (spec section 22).

A stream is a log file under `$MELD_HOME/logs`, named by its path there. Discovery pairs each one with the record
of the process that wrote it, so the page can say whether the writer is alive, dead, or unknown; the event stream
is plan 8a's `Tail` framed as SSE, replaying from the `seq` a client last saw and ending only when the writer is
confirmed gone.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

from meld.config import Config
from meld.prompts import ROLES
from meld.runners import read_record, role_run_path
from meld.state import StateStore, liveness, pid_alive, proc_start_of
from meld.transcript import Event, Tail, newest_log, sweep_logs

#: A transcript ending in one of these needs no synthesized end: the CLI said how it ended.
TERMINAL_KINDS = ("run_finished", "usage_limit", "cli_error")
#: Idle polls between writer checks (and heartbeats): the check may run `ps`, and six seconds is prompt enough.
CHECK_EVERY = 6
PING = b": ping\n\n"


def stream_id(logs_root: Path, path: Path) -> str:
    """`<group>/<file>.log`: a log's name to the page, and the only form `stream_path` accepts back."""
    return path.relative_to(logs_root).as_posix()


def stream_path(logs_root: Path, stream: str) -> Path | None:
    """The log a stream id names, or None for anything that is not `<group>/<file>.log` under `logs_root`.

    Two components, a `.log` suffix, nothing empty or dotted, and the file must exist: a request cannot name a
    file outside the logs directory, because no id resolves to one.
    """
    parts = stream.split("/")
    if len(parts) != 2 or not parts[1].endswith(".log") or any(p in ("", ".", "..") for p in parts):
        return None
    path = logs_root / parts[0] / parts[1]
    try:
        # A name a filesystem call chokes on (ENAMETOOLONG) or a symlink that resolves outside `logs_root`
        # (`logs/claude/evil.log -> ../../secret`) must answer None like any other bad id, not raise or escape.
        return path if path.is_file() and path.resolve().is_relative_to(logs_root.resolve()) else None
    except OSError:
        return None


def _role_record(home: Path, group: str, role: str) -> tuple[dict | None, Path | None]:
    """A role's recorded current launch and the log it names -- the log only when it still exists."""
    record = read_record(role_run_path(home, group, role))
    log = record.get("log_path") if record else None
    path = Path(log) if isinstance(log, str) and log else None
    return record, (path if path is not None and path.is_file() else None)


def _worker_records(home: Path) -> Iterator[tuple[dict, object]]:
    # `alive` is a no-op here: liveness is answered by `state.liveness`, not by this probe, and load_runs would
    # otherwise signal every recorded pid just to fill a field nothing below reads.
    for group_runs in StateStore(home, None).load_runs(alive=lambda pid: True).values():
        for rec in group_runs.values():
            yield {"pid": rec.pid, "started_at": rec.started_at, "proc_start": rec.proc_start}, rec


def record_for(cfg: Config, home: Path, path: Path) -> dict | None:
    """The record of the process writing `path`: a role's current launch, or a worker's run record, else None."""
    for group in cfg.groups:
        for role in ROLES:
            record, log = _role_record(home, group, role)
            if record is not None and log == path:
                return record
    for record, rec in _worker_records(home):
        if rec.log_path == str(path):
            return record
    return None


def discover(cfg: Config, home: Path, *, alive: Callable[[int], bool] = pid_alive,
             start_of: Callable[[int], str] = proc_start_of) -> list[dict]:
    """Every transcript worth a column: each configured role's recorded launch (else its newest log, `unknown`),
    then every worker run still on record. Also the viewer's turn of the log cap: `sweep_logs` runs here because
    this is the call the page polls (spec 22)."""
    logs_root = home / "logs"
    sweep_logs(logs_root, cfg.logs_max_bytes_per_run)
    out: list[dict] = []

    def entry(path: Path, *, group: str, kind: str, role: str | None, issue: int | None, record: dict | None) -> None:
        try:
            sid = stream_id(logs_root, path)
        except ValueError:
            return                                      # a log recorded outside logs/ has no id, so no stream
        out.append({"id": sid, "group": group, "kind": kind, "role": role, "issue": issue, "log_path": str(path),
                    "bytes": path.stat().st_size, "liveness": liveness(record, alive=alive, start_of=start_of)})

    for group in cfg.groups:
        for role in ROLES:
            record, path = _role_record(home, group, role)
            if path is None:
                record, path = None, newest_log(logs_root, f"{group}/{role}")
            if path is not None:
                entry(path, group=group, kind="role", role=role, issue=None, record=record)
    for record, rec in _worker_records(home):
        path = Path(rec.log_path)
        if path.is_file():
            entry(path, group=rec.group, kind=rec.kind, role=None, issue=rec.issue, record=record)
    return sorted(out, key=lambda s: (s["group"], s["kind"] != "role", s["role"] or "", s["issue"] or 0))


#: One transcript inside a connection. `writer` is called to ask whether its process is still alive, so a
#: pid that dies mid-connection is noticed; `parse` is its group's adapter's, because only an adapter knows
#: how its CLI talks.
@dataclass(frozen=True)
class Source:
    stream: str
    path: Path
    parse: Callable[[str, str], list[Event]]
    writer: Callable[[], dict]


@dataclass
class _Reader:
    """What one connection remembers about one transcript: where its reader is, how much the client has, and
    whether the stream has said its last word."""
    src: Source
    tail: Tail
    sent: int = 0
    last_kind: str | None = None
    first: bool = True
    ended: bool = False


def encode_cursor(counts: Mapping[str, int]) -> str:
    """Every stream's position in one string, for the `id:` line: `<stream>=<count>`, joined by `|`.

    Every stream the connection knows is named, a count of zero included: a cursor that omitted the zeros would
    be empty on the frame that says a stream was trimmed, and an empty `Last-Event-ID` resets every other stream
    to the start as well. It costs about 45 bytes per stream per frame on a loopback connection, which is the
    price of the browser doing the reconnecting -- `Last-Event-ID` is one opaque string it hands straight back,
    so the client needs no resume logic of its own.
    """
    return "|".join(f"{stream}={n}" for stream, n in sorted(counts.items()))


def decode_cursor(text: str) -> dict[str, int]:
    """`encode_cursor` read back. Anything unreadable is dropped rather than guessed: a client that sends
    nonsense is resumed from the start of whatever it did not name, which shows too much, never too little."""
    out: dict[str, int] = {}
    for part in (text or "").split("|"):
        stream, sep, count = part.rpartition("=")
        if not sep or not stream:
            continue
        try:
            out[stream] = max(0, int(count))
        except ValueError:
            continue
    return out


def frame(stream: str, event: Event, *, cursor: str | None = None) -> bytes:
    """One SSE frame: the kind as the event name, the stream and the event as the data, the cursor as the id.

    `cursor=None` omits the `id:` line, for an event that is no position in any file -- a synthesized
    `run_finished` -- so it can never become a client's resume point.
    """
    id_line = f"id: {cursor}\n" if cursor is not None else ""
    payload = {"stream": stream, "event": asdict(event)}
    return f"{id_line}event: {event.kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def end_frame(stream: str) -> bytes:
    """One stream has said its last word. No id: an ending is not a position, and the connection lives on."""
    return f"event: end\ndata: {json.dumps({'stream': stream})}\n\n".encode()


def events(sources: Callable[[], list[Source]], *, now: Callable[[], str], sleep: Callable[[float], None],
           cursor: str = "", interval: float = 1.0, check_every: int = CHECK_EVERY,
           max_polls: int | None = None) -> Iterator[bytes]:
    """Every transcript `sources()` names, as one stream of server-sent events.

    One connection carries them all, because a browser allows about six to an origin and a page that opened one
    per transcript starved its own polls. Each frame names its stream and carries the whole cursor, so the
    browser's own reconnect resumes every stream at once.

    Per stream, everything plan 8a and 8b established still holds: a fresh `Tail` numbers the file 1..n; a
    client that has seen more than the file holds is told the log was trimmed and shown it from the start; a
    trim that lands under the connection restarts that stream's count the same way; and a writer confirmed dead
    has its unterminated last line flushed, a `run_finished` synthesized when the transcript names no end (a
    Codex run ends at `usage`), and one `end` frame. The connection itself does not end when a stream does: a
    role relaunches into a new log, and the next refresh picks it up.
    """
    seen = decode_cursor(cursor)
    readers: dict[str, _Reader] = {}
    polls = idle = 0

    def cursor_now() -> str:
        return encode_cursor({stream: r.sent for stream, r in readers.items()})

    def notice(r: _Reader, why: str) -> bytes:
        r.sent, r.last_kind = 0, None
        return frame(r.src.stream, Event(seq=0, at=now(), kind="log_truncated",
                                         text=f"{r.src.path.name} {why}; showing it from the start"), cursor=cursor_now())

    while max_polls is None or polls < max_polls:
        if polls % check_every == 0:          # the first pass, then on the writer-check cadence
            current = {s.stream: s for s in sources()}
            for stream in [s for s, r in readers.items() if r.ended and s not in current]:
                del readers[stream]           # ended and no longer listed: its count can never matter again
            for stream, source in current.items():
                if stream not in readers:
                    readers[stream] = _Reader(src=source, tail=Tail(source.path), sent=seen.get(stream, 0))
        polls += 1
        wrote = False
        for r in list(readers.values()):
            if r.ended:
                continue
            batch = r.tail.read(r.src.parse, now())
            if any(ev.kind == "log_truncated" for ev in batch):
                # `Tail` keeps counting across a truncation while the reader a reconnect builds numbers the same
                # post-trim lines from 1. Throw the batch away, start the reader over with the file, and say so.
                r.tail = Tail(r.src.path)
                batch = r.tail.read(r.src.parse, now())
                yield notice(r, "was trimmed while this page was reading it")
            elif r.first and r.sent > len(batch):
                yield notice(r, "was trimmed since this page last read it")
            if batch:
                # The file's last kind, whether or not this client still needed those events: a caught-up client
                # whose transcript already ends in `usage_limit` must not be handed a synthesized `run_finished`.
                r.last_kind = batch[-1].kind
            if r.first:
                batch = batch[r.sent:]
                r.first = False
            for ev in batch:
                r.sent = ev.seq
                yield frame(r.src.stream, ev, cursor=cursor_now())
            wrote = wrote or bool(batch)
        if wrote:
            idle = 0
        else:
            idle += 1
            if idle % check_every == 0:
                said = False
                for r in list(readers.values()):
                    if r.ended or r.src.writer().get("state") != "dead":
                        continue
                    said = True
                    for ev in r.tail.flush(r.src.parse, now()):
                        r.last_kind, r.sent = ev.kind, ev.seq
                        yield frame(r.src.stream, ev, cursor=cursor_now())
                    if r.last_kind not in TERMINAL_KINDS:
                        # No id: this event was never a position in the file, so it must not become the client's
                        # resume cursor -- `seq` still orders it for display, inside the JSON body only.
                        yield frame(r.src.stream, Event(seq=r.tail.seq + 1, at=now(), kind="run_finished",
                                                        text="process exited; the transcript ends without a terminal event",
                                                        meta={"synthesized": True}))
                    r.ended = True
                    yield end_frame(r.src.stream)
                if not said:
                    yield PING          # a heartbeat is for a round that said nothing; an ending already did
        sleep(interval)
