# Plan 8c: the human verbs from the page — one multiplexed feed, `POST /api/action`, and a board you can act on

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A person watching `meld watch` can answer the run without leaving the page — claim or release an issue, post any human header verb, challenge a claim, record a decision or a lesson, approve the plan, ack an addition, merge a PR — with `rules.py` deciding every one of them and a refusal rendered verbatim; and every transcript streams over one connection instead of four.

**Architecture:** `viewer/streams.py`'s `events()` becomes a multiplexed reader: one SSE connection carries every transcript, each frame names its stream, and the `id:` line carries the whole cursor so the browser's own reconnect resumes all of them at once — which removes plan 8b's `MAX_SOURCES = 4` cap and the starvation it worked around. `viewer/actions.py` is a dispatch table from a verb name to the very function `meld`'s own command calls, with argument coercion and nothing else; `POST /api/action` reads a JSON body, refuses any party but `human`, runs the verb, and turns a `Refused` into the CLI's own refusal shape. The page grows a board panel (issues, pull requests, agreements, each row's legal verbs) and one action bar that every button prefills and only the person runs.

**Tech Stack:** Python 3.13, standard library plus `click` only. No new dependency, no build step; the page stays three hand-written files. Real files in `tmp_path`, the recorded board over `FakeGh`, every seam injected.

**Spec:** `docs/superpowers/specs/2026-09-03-meld-design.md` — §22 "The viewer" (the routes table, "**Streams.**", "**What the page may do.**", "**What it may not do.**"), §14 (the human party, the plan gate, challenges), §7 (the header grammar the page's `post` uses), §4 (the layout and the verb list), §19 (the suite), §21 (what plan 8 excludes). Task 1 amends §22 and §4 first; this plan implements those amendments and adds nothing they do not say. **Out of scope:** `/api/config` and the configuration form (plan 8d) — the server keeps answering 404 for it.

## Global Constraints

- Python `>=3.12` (3.13 locally); runtime dependency `click>=8.1` only. Standard library plus click inside `src/meld/`. The page references nothing off the machine.
- **`rules.py` is the only legality authority.** `viewer/actions.py` coerces arguments and calls the same function the CLI calls; it contains no `if actor…` check, no verb-permission table of its own, and no decision a `meld` command would make differently. A verb the page offers that `rules.py` refuses is a rendering bug, never a second authority.
- `github.py` is the only GitHub path (plus `appauth.py`'s two App-JWT calls); `gitops.py` the only git runner. Adapters are the only code that knows a CLI.
- **The page acts as `human` and as nothing else.** The request body names its party and the server holds it to it; `[joint]` is a party, so the page never posts one. It may not start or stop a supervisor, kill a run, edit a run record, or write into a worktree.
- Workers never hold credentials; `neutralise_closers` guards worker-authored text only, so nothing the page posts as `human` passes through it.
- The token stays out of every log and every error body. No CORS header is ever sent — which is also what makes a cross-origin `POST` with `Content-Type: application/json` fail its preflight.
- Tests are hermetic and real: real files over fakes where a file is the thing under test; no network, no real CLI, no host `~/.claude`/gitconfig/wall-clock dependence; inject `now`, `clock`, `sleep`, `alive`, `start_of`, `disk_usage`, `max_polls`; pass `adapter_for=` to every `load_context` in tests. `uv run pytest -q` must be green with **no warnings** before every commit.
- Never set `MELD_SMOKE=1`.
- Conventional commit subjects, **no attribution trailers of any kind**, one logical change per commit; the repo-local identity `Scott Brant <421573+smidley@users.noreply.github.com>` is configured in the clone — check `git log -1 --format='%an <%ae>'` after the first commit of each task.
- Work in `/Users/scottbrant/code/meld-main` on branch `plan-8c-human-verbs` (off `main` at `deb7c83`). `~/Documents/meld` is iCloud-synced and stalls git; do not work there. Commit with `git -C /Users/scottbrant/code/meld-main ...`.
- Baseline at this plan's base (`main` = `deb7c83`, plan 8b merged): `uv run pytest -q` → **839 passed**, no warnings.
- **Test counts are read, never predicted.** Each task says how many test *functions* it adds and how many parametrized cases they expand to; the implementer runs the suite and records the printed total in its report. Plan 8b's three arithmetic slips came from predicting cumulative totals in the plan text; this plan does not.

## Decisions (rulings made while writing the plan; recorded so executors do not re-litigate)

1. **One connection carries every transcript, and the cursor rides on the `id:` line.** A browser allows about six HTTP/1.1 connections per origin, so plan 8b capped the page at four `EventSource`s and starved worker columns behind role columns. Multiplexing removes the cap. The cursor is `<stream>=<count>` pairs joined by `|` (`claude/pm-1.log=7|claude/orchestrator-2.log=3`), because `Last-Event-ID` is one opaque string and the browser sends it back unchanged on its own reconnect — so resumption still needs no client logic. It costs about 45 bytes per live stream on every frame, on a loopback connection; that is the price of the browser doing the reconnecting.
2. **A stream ends; the connection does not.** `event: end` names one stream in its data. The connection stays open on a heartbeat, because a role relaunches into a new log and the next refresh picks it up — one connection for the life of the page.
3. **An ended reader is dropped once `sources()` stops naming it**, which bounds both the reader set and the cursor over a long session. While it is still named, it stays in the reader map so it is never reopened and replayed.
4. **`viewer/actions.py` is a dispatch table, not a layer.** One entry per verb, each a call into the module the CLI already calls, with the request's JSON coerced into that function's arguments. Argument problems are `BadRequest` → 400; `rules.py`'s refusals are 409 in the CLI's own `_refusal_json` shape; anything from GitHub, git, config or the filesystem is 502; a `Refused` for a human never posts a `PROTOCOL-VIOLATION` (`actions.refuse` already skips that for `is_human`), so `violation_posted` is always null here.
5. **Exactly the ten verbs §22 names** — `claim`, `release`, `post`, `tag`, `challenge`, `decide`, `remember`, `plan-approve`, `plan-ack`, `merge`. `plan-issues`, `dispatch`, `collect`, `reap`, `review`, `audit`, `reopen`, `approve`, `request-changes` and `push-fix` may appear in a human's `legal` list and are deliberately not offered: they dispatch workers, act as a reviewer, or belong to the groups. The page renders a legal verb it cannot drive as dim text rather than a button, so the gap is visible instead of silent.
6. **`post` never carries `joint=True`.** `[joint]` is a party, and §22 forbids the page posting as any party but `human`; `posting.post` refuses it for a human anyway.
7. **Every button prefills the action bar; only `Run` acts.** One execution path, no double-click hazard on `merge`, and the person always sees the exact verb and ref before it goes. `tag` has eight independent fields and gets no control in this plan — the API supports it, and a form over structured fields is plan 8d's shape.
8. **`POST /api/action` clears the `/api/state` cache** on success: the board just moved, and a five-second stale header after the person's own action reads as a bug.
9. **The request body is capped at `MAX_BODY_BYTES = 1 MiB` at the transport**, before the gate, like a malformed request line. A `Content-Length` above it is answered 413 and the body is not read.
10. **`api_action` ends with `except Exception` → 500.** An unhandled exception inside `handle` drops the connection and prints a traceback to the person's terminal every time they press a button; a rendered 500 is strictly better. `_StopRequested` and `KeyboardInterrupt` are `BaseException` and still propagate.
11. **The page renders every string as a text node** (`textContent`), including refusal text, which comes from `rules.py` but reaches the page through GitHub. `innerHTML` never appears in `app.js`.

## File structure

| File | Responsibility |
|---|---|
| `docs/superpowers/specs/2026-09-03-meld-design.md` | §22 routes table (`/api/events` one or many; `/api/action`'s shape; `/api/config` still 404), a new "**Actions.**" paragraph, the "**Streams.**" paragraph's multiplexing sentences; §4's layout gains `viewer/actions.py`. Task 1. |
| `src/meld/viewer/streams.py` | `Source`, `_Reader`, `encode_cursor`, `decode_cursor`, `frame` (reshaped), `end_frame`, `events` (multiplexed). Task 2. |
| `src/meld/viewer/server.py` | `api_events` over many streams, `_source`; `Request.body`, `MAX_BODY_BYTES`, `_Handler` reading a body (Task 4); `api_action` and the `ROUTES` entry (Task 4). |
| `src/meld/viewer/actions.py` | `BadRequest`, the coercion helpers, `VERBS`. Task 4. |
| `src/meld/viewer/static/app.js`, `app.css`, `app.html` | One feed (Task 3); the board panel (Task 5); the action bar (Task 6). |
| `tests/test_viewer_streams.py`, `tests/test_viewer.py` | Tasks 2, 4. |
| `tests/test_viewer_actions.py` (new) | Task 4. |
| `docs/HANDOFF.md`, `README.md` | Task 7. |

---

### Task 1: The spec says what this plan builds

**Files:**
- Modify: `docs/superpowers/specs/2026-09-03-meld-design.md` (§4's layout block under `viewer/`; §22's routes table and the sentence under it; §22's "**Streams.**" paragraph; a new "**Actions.**" paragraph)

**Interfaces:** none; this task is the authority the rest of the plan argues from.

- [ ] **Step 1: §4's layout.** Under `viewer/`, after the `streams.py` line, add:

```
      actions.py            # the one write: a human verb, dispatched to the function the CLI calls (section 22)
```

- [ ] **Step 2: §22's routes table.** Replace the `/api/events` row and the `/api/action` row with:

```
| `/api/events` | GET | `text/event-stream` for one run or for every run at once, resumable from a cursor |
| `/api/action` | POST | one human verb, as the `human` party |
```

and replace the sentence under the table —

```
`/api/action` and `/api/config` are plans 8c and 8d; until they land the server answers 404 for both, and the page offers no button for either.
```

— with:

```
`/api/config` is plan 8d; until it lands the server answers 404 for it, and the page offers no control for it.
```

- [ ] **Step 3: §22's "Streams." paragraph.** Append to the end of that paragraph:

```
 One connection carries every transcript. A browser allows about six connections to an origin, so a page that opened one per transcript starved its own polls; `/api/events` with no `stream` names every transcript `/api/streams` would list, refreshed on the same cadence as the writer check, and each frame names the stream it belongs to. The `id:` line therefore carries the whole cursor — `<stream>=<count>` for every stream the connection has sent, joined by `|` — so the browser's own reconnect resumes all of them at once and the client still holds no resume logic. `event: end` names one stream and does not end the connection: a role relaunches into a new log and the next refresh picks it up. A reader whose stream is no longer listed and has already ended is dropped, which bounds both the reader set and the cursor over a long session.
```

- [ ] **Step 4: §22's new "Actions." paragraph.** Immediately after the "**What it may not do.**" paragraph, add:

```
**Actions.** `POST /api/action` takes a JSON object naming the party (`human`, and the server refuses any other), the verb, and that verb's own arguments, and calls the same function the matching `meld` command calls — `claim`, `release`, `post`, `tag`, `challenge`, `decide`, `remember`, `plan-approve`, `plan-ack`, `merge`. An argument the verb cannot use is 400 and never reaches it; a `Refused` is 409 carrying the refusal, the verb, the target and the rule, exactly as the CLI prints them; a failure of GitHub, git, or the configuration is 502. A human's refusal posts no `PROTOCOL-VIOLATION`, so nothing a person tries on the page reaches the board unless it was legal. A successful action drops the cached `/api/state`, because the board has just moved. The body is capped at one mebibyte at the transport, before the gate.
```

- [ ] **Step 5: Run the suite and commit.**

Run: `uv run pytest -q` — no test reads the spec; the run is the rule. It must print `839 passed` with no warnings.

```bash
git -C /Users/scottbrant/code/meld-main add docs/superpowers/specs/2026-09-03-meld-design.md
git -C /Users/scottbrant/code/meld-main commit -m "docs: section 22 names the multiplexed feed and the action route"
```

---

### Task 2: One connection carries every transcript

**Files:**
- Modify: `src/meld/viewer/streams.py` (add `Source`, `_Reader`, `encode_cursor`, `decode_cursor`, `end_frame`; reshape `frame`; rewrite `events`)
- Modify: `src/meld/viewer/server.py` (`api_events`, a new `_source` helper)
- Test: `tests/test_viewer_streams.py` (rewrite the four `events` tests, add four), `tests/test_viewer.py` (the two `/api/events` tests)

**Interfaces:**
- Consumes: `transcript.Tail`, `transcript.Event`, `TERMINAL_KINDS`, `CHECK_EVERY`, `stream_path`, `record_for`, `discover`; `state.liveness`; `meld.adapters.UnknownAdapter`.
- Produces: `Source(stream, path, parse, writer)` (frozen dataclass); `encode_cursor(counts: Mapping[str, int]) -> str`; `decode_cursor(text: str) -> dict[str, int]`; `frame(stream: str, event: Event, *, cursor: str | None = None) -> bytes`; `end_frame(stream: str) -> bytes`; `events(sources: Callable[[], list[Source]], *, now, sleep, cursor: str = "", interval: float = 1.0, check_every: int = CHECK_EVERY, max_polls: int | None = None) -> Iterator[bytes]`; `Viewer._source(stream) -> Source | None` (raises `UnknownAdapter`). Task 3 consumes the frame shape; nothing else changes.

- [ ] **Step 1: Write the failing tests.** In `tests/test_viewer_streams.py`, replace the four tests whose names begin `test_events_` with these eight, and extend the imports to `from meld.viewer.streams import END_KINDS_NOTE …` — no: the import line becomes

```python
from meld.viewer.streams import Source, decode_cursor, discover, encode_cursor, end_frame, events, frame, record_for, stream_id, stream_path
```

(`END` and `PING` leave the import; `END` is replaced by `end_frame`.) Keep `frames()` as it is — it already parses `id`, `event`, `data` and a `comment`. Add above the new tests:

```python
def src(log, writer_state="alive", stream=None):
    """One Source over a real file, with its writer's liveness injected as a fixed state or a callable."""
    state = writer_state if callable(writer_state) else (lambda: {"state": writer_state})
    return Source(stream=stream or f"claude/{log.name}", path=log, parse=PARSE, writer=state)


def only(*sources):
    return lambda: list(sources)
```

and the tests:

```python
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
```

Add `from dataclasses import asdict` and `from meld.transcript import Event` to the file's imports if they are not already there (`Event` is; `asdict` is not).

- [ ] **Step 2: Run them to verify they fail.**

Run: `uv run pytest tests/test_viewer_streams.py -q`
Expected: FAIL — `ImportError: cannot import name 'Source' from 'meld.viewer.streams'`.

- [ ] **Step 3: Implement `streams.py`.** Replace `END` and the whole of `frame` and `events` with the following (keep `TERMINAL_KINDS`, `CHECK_EVERY`, `PING`, `stream_id`, `stream_path`, `_role_record`, `_worker_records`, `record_for`, `discover` exactly as they are), and add `from dataclasses import asdict, dataclass, field` to the imports:

```python
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
```

Delete the module constant `END`; `Mapping` joins the `collections.abc` import.

- [ ] **Step 4: Implement `server.py`'s side.** Replace `api_events` with:

```python
    def _source(self, stream: str) -> st.Source | None:
        """One transcript's reader, or None when `stream` names no log of a configured group. An adapter the
        configuration names but this build does not have raises `UnknownAdapter`, which the caller decides about:
        a stream the person asked for by name is a 502, one that merely turned up in `discover` is skipped."""
        home, cfg = self.ctx.store.home, self.ctx.cfg
        path = st.stream_path(home / "logs", stream)
        group_cfg = cfg.groups.get(stream.partition("/")[0])
        if path is None or group_cfg is None:
            return None
        parse = self.ctx.adapter_for(group_cfg.cli).parse_events
        record = st.record_for(cfg, home, path)
        return st.Source(stream=stream, path=path, parse=parse,
                         writer=lambda: liveness(record, alive=self.alive, start_of=self.start_of))

    def api_events(self, req: Request) -> Response:
        """Every transcript, or the ones `stream=` names, as one event stream (spec 22)."""
        wanted = [s for s in req.query.get("stream", []) if s]
        for stream in wanted:
            try:
                if self._source(stream) is None:
                    return _json(404, {"error": f"no transcript {stream!r} of a configured group"})
            except UnknownAdapter as e:
                return _json(502, {"error": str(e)})

        def sources() -> list[st.Source]:
            # Re-read on every refresh, so a role that relaunched into a new log joins the connection and the
            # record behind each writer check is the current one.
            names = wanted or [s["id"] for s in st.discover(self.ctx.cfg, self.ctx.store.home,
                                                            alive=self.alive, start_of=self.start_of)]
            out = []
            for stream in names:
                try:
                    source = self._source(stream)
                except UnknownAdapter:
                    continue                  # a group whose CLI this build cannot translate contributes nothing
                if source is not None:
                    out.append(source)
            return out

        body = st.events(sources, now=self.now, sleep=self.sleep, interval=self.interval,
                         cursor=req.headers.get("last-event-id") or req.first("after"),
                         check_every=self.check_every, max_polls=self.max_polls)
        return Response(200, body, "text/event-stream; charset=utf-8", SECURITY_HEADERS + (("Connection", "close"),))
```

`_int` is now unused by `api_events`; leave it if anything else uses it, delete it if nothing does (`grep -n "_int(" src/meld/viewer/server.py`).

- [ ] **Step 5: Update the two `/api/events` tests in `tests/test_viewer.py`.** Replace `test_api_events_streams_a_transcript_and_resumes_from_last_event_id` with:

```python
def test_api_events_streams_every_transcript_and_resumes_from_the_cursor(tmp_path):
    # max_polls=2: the replay happens inside the loop now, so the idle check that ends a stream is the second poll.
    v, ctx, _ = viewer_for(tmp_path, alive=lambda pid: False, max_polls=2)   # the writer is confirmed gone
    home = ctx.store.home
    log = home / "logs" / "claude" / "pm-1.log"
    log.parent.mkdir(parents=True)
    log.write_text("one\ntwo\n")
    write_record(role_run_path(home, "claude", "pm"),
                 {"pid": 7, "started_at": NOW, "proc_start": "T0", "log_path": str(log), "reason": "start", "launch_no": 1})

    resp = get(v, "/api/events")                                   # no stream=: everything discover finds
    assert resp.status == 200 and resp.content_type == "text/event-stream; charset=utf-8" and resp.streaming
    text = b"".join(resp.body).decode()
    assert text.startswith("id: claude/pm-1.log=1\nevent: message\n") and '"stream": "claude/pm-1.log"' in text
    assert '"text": "two"' in text and 'event: run_finished' in text and '"synthesized": true' in text
    assert text.endswith('event: end\ndata: {"stream": "claude/pm-1.log"}\n\n')

    named = b"".join(get(v, "/api/events?stream=claude/pm-1.log").body).decode()
    assert named.startswith("id: claude/pm-1.log=1\n")
    resumed = b"".join(get(v, "/api/events", headers={"Last-Event-ID": "claude/pm-1.log=1"}).body).decode()
    assert '"text": "one"' not in resumed and '"text": "two"' in resumed
    by_query = b"".join(get(v, "/api/events?after=claude%2Fpm-1.log%3D2").body).decode()
    assert "event: message" not in by_query and by_query.startswith("event: run_finished\n")
    junk = b"".join(get(v, "/api/events", headers={"Last-Event-ID": "seven"}).body).decode()
    assert junk.startswith("id: claude/pm-1.log=1\n")               # unreadable cursor: from the start
```

Leave `test_api_events_answers_404_for_a_stream_that_is_not_a_log_of_a_configured_group` as it is — every case in it still answers 404 — except that the `""` case must be dropped from its parametrize list, because an empty `stream=` now means "every stream" rather than a bad one. Add, in its place, a case proving that:

```python
def test_api_events_with_no_stream_is_every_stream_not_an_error(tmp_path):
    v, ctx, _ = viewer_for(tmp_path, alive=lambda pid: False)
    (ctx.store.home / "logs" / "claude").mkdir(parents=True)
    (ctx.store.home / "logs" / "claude" / "pm-1.log").write_text("hi\n")
    assert get(v, "/api/events").status == 200 and get(v, "/api/events?stream=").status == 200
```

- [ ] **Step 5b: Update the socket test's SSE assertions** in `tests/test_viewer.py`. `test_serve_binds_an_os_assigned_loopback_port_and_answers_over_a_socket` builds its viewer with `viewer_for(tmp_path, alive=lambda pid: False)`; give it `max_polls=2` as well, and replace its two SSE assertions

```python
        assert data.startswith(b"id: 1\nevent: message\n") and data.endswith(b"event: end\ndata: {}\n\n")
```

with

```python
        assert data.startswith(b"id: claude/pm-1.log=1\nevent: message\n")
        assert data.endswith(b'event: end\ndata: {"stream": "claude/pm-1.log"}\n\n')
```

(the line's exact current text may differ; find it by `data.startswith(b"id: 1`).

- [ ] **Step 6: Run the focused tests, then the suite.**

Run: `uv run pytest tests/test_viewer_streams.py tests/test_viewer.py -q`
Then: `uv run pytest -q` — green, no warnings. Record the printed total in your report.

- [ ] **Step 7: Commit.**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/viewer/streams.py src/meld/viewer/server.py tests/test_viewer_streams.py tests/test_viewer.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: one event stream carries every transcript, resumable by cursor"
```

---

### Task 3: The page reads one feed

**Files:**
- Modify: `src/meld/viewer/static/app.js` (one `EventSource`; `columnFor`; `renderStreams`; delete `MAX_SOURCES`, `SOURCE_PRIORITY`, `openWaiting`, `openStream`'s per-column source)
- Modify: `src/meld/viewer/static/app.html` (a feed badge in the header)
- Modify: `src/meld/viewer/static/app.css` (`.badge.reconnecting`; drop `.badge.waiting`)
- Test: `tests/test_viewer.py` (the page-content assertions)

**Interfaces:**
- Consumes: Task 2's frame shape — `data` is `{"stream": "<id>", "event": {…}}` for a kind, `{"stream": "<id>"}` for `end`; `/api/streams`'s `{id, group, kind, role, issue, liveness}`.
- Produces: nothing Python consumes; the page is tested for what Python can see.

- [ ] **Step 1: Write the failing test.** In `tests/test_viewer.py`, extend `test_the_page_references_nothing_off_the_machine_and_renders_text_as_text` with:

```python
    assert "MAX_SOURCES" not in js and "openWaiting" not in js      # one connection now, so no cap and no queue
    assert js.count("new EventSource") == 1
    assert "msg.stream" in js and "feed" in js
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `uv run pytest tests/test_viewer.py -q -k page`
Expected: FAIL — `assert 'MAX_SOURCES' not in js`.

- [ ] **Step 3: `app.html`.** In the `#machine` row, before `<span id="stamp" …>`, add:

```html
    <span id="feed" class="badge unknown">feed connecting</span>
```

- [ ] **Step 4: `app.css`.** Replace the `.badge.frozen` rule's neighbourhood so the state colours read:

```css
.badge.alive { color: var(--ok); border-color: var(--ok); }
.badge.dead { color: var(--bad); border-color: var(--bad); }
.badge.unknown { color: var(--unknown); border-color: var(--unknown); }
.badge.frozen { color: var(--warn); border-color: var(--warn); }
.badge.reconnecting { color: var(--warn); border-color: var(--warn); }
```

(`.badge.waiting`, if present, goes.)

- [ ] **Step 5: `app.js`.** Replace the constants `MAX_SOURCES`, `SOURCE_PRIORITY` and `BADGE_STATES`, and the functions `openStream`, `openWaiting` and `renderStreams`, with:

```javascript
const BADGE_STATES = ["alive", "dead", "unknown", "frozen", "reconnecting"];
```

```javascript
function columnFor(id) {
  // A frame can name a stream the five-second /api/streams poll has not listed yet; give it a column at once,
  // titled by its id, and let the next poll replace the title and the badge. `listed` is what keeps that
  // column alive: only a column a poll has named, and then stopped naming, is removed.
  let col = columns.get(id);
  if (!col) {
    const root = el("column").content.firstElementChild.cloneNode(true);
    col = { root, list: root.querySelector(".events"), live: root.querySelector(".live"),
            why: root.querySelector(".why"), listed: false, liveness: { state: "unknown", reason: "not listed yet" } };
    text(root.querySelector(".who"), id);
    el("streams").append(root);
    columns.set(id, col);
  }
  return col;
}

function openFeed() {
  // One connection for every transcript (spec 22): a browser allows about six to an origin, and one source per
  // column starved these two polls. EventSource reconnects by itself and sends Last-Event-ID, which carries
  // every stream's position, so resumption needs nothing here.
  const feed = new EventSource(api("/api/events"));
  for (const kind of KINDS) {
    feed.addEventListener(kind, (e) => {
      const msg = JSON.parse(e.data);
      appendEvent(columnFor(msg.stream), msg.event);
    });
  }
  feed.addEventListener("end", (e) => columnFor(JSON.parse(e.data).stream).root.classList.add("ended"));
  feed.onopen = () => badge(el("feed"), "alive", "feed live");
  feed.onerror = () => badge(el("feed"), "reconnecting", "feed reconnecting");
}

function renderStreams(list) {
  const seen = new Set();
  for (const s of list) {
    seen.add(s.id);
    const col = columnFor(s.id);
    col.listed = true;
    col.liveness = s.liveness;
    text(col.root.querySelector(".who"), s.kind === "role" ? `${s.group} / ${s.role}` : `${s.group} / ${s.kind} #${s.issue}`);
  }
  for (const [id, col] of columns) {
    if (col.listed && !seen.has(id)) { col.root.remove(); columns.delete(id); }
  }
  for (const col of columns.values()) {
    badge(col.live, col.liveness.state);
    text(col.why, col.liveness.reason);
  }
}
```

and replace the two trailing calls with:

```javascript
pollState();
pollStreams();
openFeed();
```

The `columns` comment at the top becomes `// stream id -> {root, list, live, why, listed, liveness}`.

- [ ] **Step 6: Read `app.js` once as a program.** There is no JavaScript runtime in this toolchain, so this reading is the only check its logic gets before a live run. Confirm: every DOM write is `textContent` or `append` of a node; no `.append(...).x` chain (`append` returns `undefined`); every `el(...)` id exists in `app.html`; `columnFor` is the only place a column is created; a column created by a frame is never removed before a poll has listed it; `badge()` still never assigns `className`. Write what you traced in your report.

- [ ] **Step 7: Run the suite and commit.**

Run: `uv run pytest -q` — green, no warnings; record the printed total.

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/viewer/static tests/test_viewer.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: the page reads every transcript from one event stream"
```

---

### Task 4: `POST /api/action`

**Files:**
- Create: `src/meld/viewer/actions.py`
- Modify: `src/meld/viewer/server.py` (`MAX_BODY_BYTES`; `Request.body`; `ROUTES`; `api_action`; `handle`'s method check; `_Handler._dispatch` reading the body)
- Test: `tests/test_viewer_actions.py` (new), `tests/test_viewer.py`

**Interfaces:**
- Consumes: `actions.claim`, `actions.release`, `actions.Refused`; `posting.post`, `posting.PostError`; `tagging.tag`; `challenge.challenge`; `memory.decide`, `memory.remember`; `plan.plan_approve`, `plan.plan_ack`; `review.merge`.
- Produces: `viewer.actions.BadRequest`; `viewer.actions.VERBS: dict[str, Callable[[Context, Mapping], dict]]`; `POST /api/action`. Task 6 consumes the route's request and response shapes.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_viewer_actions.py`:

```python
import json

import pytest

from meld.viewer import actions as va
from tests.test_viewer import TOKEN, body, get, viewer_for


def comments(fake):
    """Every coordination comment posted through this fake, newest last — the spelling test_actions.py uses."""
    return [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint.endswith("/comments")]


def act(v, payload, **kw):
    """POST /api/action with `payload` as the JSON body."""
    from meld.viewer.server import Request
    target = f"/api/action?t={kw.pop('token', TOKEN)}"
    headers = {"Host": kw.pop("host", "127.0.0.1:8765"), "Content-Type": "application/json"}
    return v.handle(Request.parse("POST", target, headers, json.dumps(payload).encode()))


def test_the_dispatch_table_is_exactly_the_ten_verbs_section_22_names():
    assert sorted(va.VERBS) == ["challenge", "claim", "decide", "merge", "plan-ack", "plan-approve",
                                "post", "release", "remember", "tag"]


def test_an_action_runs_the_same_function_the_cli_runs(tmp_path):
    v, ctx, fake = viewer_for(tmp_path)
    resp = act(v, {"party": "human", "verb": "post", "ref": "ask-1", "text": "who owns #13?"})
    assert resp.status == 200
    out = body(resp)
    assert out["ok"] is True and out["verb"] == "post" and out["result"]["header"].startswith("[human] ASK ask-1")
    posted = [c for c in comments(fake) if "ASK ask-1" in c]
    assert len(posted) == 1 and "who owns #13?" in posted[0]


def test_a_refusal_comes_back_whole_with_the_rule_that_named_it(tmp_path):
    v, ctx, _ = viewer_for(tmp_path)
    resp = act(v, {"party": "human", "verb": "claim", "number": 13})     # board1: #13 is claimed by codex
    assert resp.status == 409
    out = body(resp)
    assert out["verb"] == "claim" and out["target"] == "#13" and "codex" in out["rule"]
    assert out["rule"] in out["refused"] and out["violation_posted"] is None   # a human's refusal posts nothing


def test_the_page_may_act_as_no_other_party(tmp_path):
    v, _, fake = viewer_for(tmp_path)
    before = len(comments(fake))
    for party in ("claude", "joint", None, ""):
        resp = act(v, {"party": party, "verb": "post", "ref": "ask-1", "text": "x"})
        assert resp.status == 400 and "human" in body(resp)["error"]
    assert len(comments(fake)) == before


@pytest.mark.parametrize("payload, wants", [
    ({"party": "human", "verb": "nope"}, "unknown verb"),
    ({"party": "human", "verb": "claim"}, "integer 'number'"),
    ({"party": "human", "verb": "claim", "number": "13"}, "integer 'number'"),
    ({"party": "human", "verb": "claim", "number": True}, "integer 'number'"),
    ({"party": "human", "verb": "post"}, "non-empty 'verb'"),
    ({"party": "human", "verb": "remember", "ref": "#13"}, "non-empty 'text'"),
    ({"party": "human", "verb": "plan-ack"}, "non-empty 'ref'"),
    ({"party": "human", "verb": "tag", "number": 11, "skills": "qa"}, "list of strings"),
])
def test_an_argument_the_verb_cannot_use_never_reaches_it(tmp_path, payload, wants):
    v, _, fake = viewer_for(tmp_path)
    before = len(comments(fake))
    resp = act(v, payload)
    assert resp.status == 400 and wants in body(resp)["error"]
    assert len(comments(fake)) == before


def test_a_malformed_body_is_a_400_not_a_traceback(tmp_path):
    from meld.viewer.server import Request
    v, _, _ = viewer_for(tmp_path)
    headers = {"Host": "127.0.0.1:8765"}
    for raw in (b"", b"not json", b"[1, 2]", b'"a string"'):
        resp = v.handle(Request.parse("POST", f"/api/action?t={TOKEN}", headers, raw))
        assert resp.status == 400 and "error" in body(resp)


def test_a_github_failure_is_502_and_an_unexpected_error_is_500(tmp_path, monkeypatch):
    from meld.github import GitHubError
    v, _, _ = viewer_for(tmp_path)
    monkeypatch.setitem(va.VERBS, "claim", lambda ctx, b: (_ for _ in ()).throw(GitHubError("gh: 500")))
    assert act(v, {"party": "human", "verb": "claim", "number": 11}).status == 502
    monkeypatch.setitem(va.VERBS, "claim", lambda ctx, b: (_ for _ in ()).throw(RuntimeError("boom")))
    resp = act(v, {"party": "human", "verb": "claim", "number": 11})
    assert resp.status == 500 and "RuntimeError: boom" in body(resp)["error"]


def test_a_successful_action_drops_the_cached_state(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    assert get(v, "/api/state").status == 200 and v._state_cache is not None
    act(v, {"party": "human", "verb": "post", "ref": "ask-1", "text": "hello"})
    assert v._state_cache is None
    assert get(v, "/api/state").status == 200 and v._state_cache is not None
    act(v, {"party": "human", "verb": "claim", "number": 13})        # refused, so the board did not move
    assert v._state_cache is not None


def test_the_gate_comes_before_the_action(tmp_path):
    v, _, fake = viewer_for(tmp_path)
    before = len(comments(fake))
    assert act(v, {"party": "human", "verb": "post", "ref": "ask-1", "text": "x"}, token="wrong").status == 403
    assert act(v, {"party": "human", "verb": "post", "ref": "ask-1", "text": "x"}, host="evil.example:8765").status == 403
    assert len(comments(fake)) == before
```

In `tests/test_viewer.py`, replace the `/api/action` line of `test_unknown_routes_are_404_and_the_wrong_method_is_405_behind_the_gate` — `assert get(v, "/api/action", method="POST").status == 404` — with:

```python
    assert get(v, "/api/config", method="PUT").status == 404        # plan 8d
    assert get(v, "/api/action").status == 405                      # POST only
    assert get(v, "/api/state", method="POST").status == 405
```

(the existing `/api/config` line stays; the `/api/state` line stays.) And add, next to the socket test:

```python
def test_a_body_larger_than_the_cap_is_refused_at_the_transport(tmp_path):
    """A Content-Length above the cap is answered 413 without reading the body, like a malformed request line."""
    import http.client
    v, _, _ = viewer_for(tmp_path)
    httpd = serve(v, port=0)
    try:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.putrequest("POST", f"/api/action?t={TOKEN}")
        conn.putheader("Content-Length", str(server.MAX_BODY_BYTES + 1))
        conn.putheader("Content-Type", "application/json")
        conn.endheaders()
        resp = conn.getresponse()
        assert resp.status == 413
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
```

- [ ] **Step 2: Run them to verify they fail.**

Run: `uv run pytest tests/test_viewer_actions.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'meld.viewer.actions'`.

- [ ] **Step 3: Create `src/meld/viewer/actions.py`:**

```python
"""The one write the viewer can make: a human verb, dispatched to the function the CLI calls (spec section 22).

The viewer holds no legality logic. Every entry below coerces the request's JSON into the arguments its verb's
own module already takes and calls it; `rules.py` decides whether the call is allowed, and the refusal that
comes back is rendered verbatim, rule included. Nothing here decides anything a `meld` command would decide
differently, and nothing here posts as any party but the human whose context the server holds.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

from meld.actions import claim, release
from meld.challenge import challenge
from meld.memory import decide, remember
from meld.plan import plan_ack, plan_approve
from meld.posting import post
from meld.review import merge
from meld.tagging import tag


class BadRequest(ValueError):
    """The request named arguments its verb cannot use. Answered 400; the verb is never called."""


def _number(body: Mapping, verb: str) -> int:
    value = body.get("number")
    # bool is an int subclass, and `True` would silently claim issue #1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise BadRequest(f"{verb} needs an integer 'number' (the issue or pull request), got {value!r}")
    return value


def _string(body: Mapping, key: str, verb: str, *, required: bool = False) -> str:
    value = body.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise BadRequest(f"{verb} needs a string '{key}', got {value!r}")
    if required and not value.strip():
        raise BadRequest(f"{verb} needs a non-empty '{key}'")
    return value


def _optional(body: Mapping, key: str, verb: str) -> str | None:
    value = body.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise BadRequest(f"{verb} needs a string '{key}' or nothing at all, got {value!r}")
    return value


def _strings(body: Mapping, key: str, verb: str) -> tuple[str, ...] | None:
    value = body.get(key) or []
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise BadRequest(f"{verb} needs a list of strings for '{key}', got {value!r}")
    return tuple(value) or None


#: Verb name (as the CLI spells it) to the call that runs it. Exactly the ten verbs section 22 grants the page:
#: every other legal verb either dispatches a worker, acts as a reviewer, or belongs to a group.
VERBS: dict[str, Callable[..., dict]] = {
    "claim": lambda ctx, b: claim(ctx, _number(b, "claim")),
    "release": lambda ctx, b: release(ctx, _number(b, "release"), force=bool(b.get("force", False))),
    # No `joint=`: [joint] is a party, and the page posts as the human and nothing else.
    "post": lambda ctx, b: post(ctx, _string(b, "verb", "post", required=True).upper(),
                                ref=_optional(b, "ref", "post"), qualifier=_optional(b, "qualifier", "post"),
                                text=_string(b, "text", "post")),
    "tag": lambda ctx, b: tag(ctx, _number(b, "tag"), worker=_optional(b, "worker", "tag"),
                              model=_optional(b, "model", "tag"), effort=_optional(b, "effort", "tag"),
                              size=_optional(b, "size", "tag"), skills=_strings(b, "skills", "tag"),
                              scope=_strings(b, "scope", "tag"), may_delete=_strings(b, "may_delete", "tag"),
                              held=_strings(b, "held", "tag")),
    "challenge": lambda ctx, b: challenge(ctx, _string(b, "action", "challenge", required=True),
                                          _string(b, "ref", "challenge", required=True),
                                          _string(b, "text", "challenge")),
    "decide": lambda ctx, b: decide(ctx, _string(b, "ref", "decide", required=True),
                                    supersedes=_optional(b, "supersedes", "decide")),
    "remember": lambda ctx, b: remember(ctx, _string(b, "ref", "remember", required=True),
                                        _string(b, "text", "remember", required=True),
                                        supersedes=_optional(b, "supersedes", "remember")),
    "plan-approve": lambda ctx, b: plan_approve(ctx),
    "plan-ack": lambda ctx, b: plan_ack(ctx, _string(b, "ref", "plan-ack", required=True)),
    "merge": lambda ctx, b: merge(ctx, _number(b, "merge")),
}
```

- [ ] **Step 4: `server.py`.** Add to the imports:

```python
from meld.actions import Refused
from meld.appauth import AppAuthError
from meld.config import ConfigError
from meld.dispatch import DispatchError
from meld.gitops import GitError
from meld.posting import PostError
from meld.results import ResultsError
from meld.viewer import actions as va
```

Add a module constant beside `STATE_TTL_SECONDS`:

```python
#: The most a request body may be. A page's action is a few hundred bytes; this is the transport saying no to
#: anything that is not one, before the gate and before anything is read.
MAX_BODY_BYTES = 1 << 20
```

Give `Request` a body:

```python
@dataclass(frozen=True)
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]          # names lower-cased
    body: bytes = b""

    @classmethod
    def parse(cls, method: str, target: str, headers: Mapping[str, str], body: bytes = b"") -> Request:
        parts = urlsplit(target)
        return cls(method.upper(), parts.path or "/", parse_qs(parts.query, keep_blank_values=True),
                   {k.lower(): v for k, v in headers.items()}, body)
```

Add the route and teach `handle` which method each takes:

```python
    ROUTES: dict[str, str] = {"/": "page", "/static/app.css": "asset_css", "/static/app.js": "asset_js",
                              "/api/state": "api_state", "/api/streams": "api_streams", "/api/events": "api_events",
                              "/api/action": "api_action"}
    #: The one route that writes is the one route that is not a GET.
    METHODS: dict[str, str] = {"/api/action": "POST"}
```

and in `handle`, replace `if req.method != "GET":` with:

```python
        if req.method != self.METHODS.get(req.path, "GET"):
            return _json(405, {"error": f"{req.method} is not allowed on {req.path}"})
```

Add the route method after `api_events`:

```python
    def api_action(self, req: Request) -> Response:
        """One human verb, run by the same function `meld` runs (spec 22).

        The viewer decides nothing here: it coerces arguments, calls, and reports. A `Refused` comes back whole
        -- the verb, the target, and the rule that named it -- because a person reading a refusal needs the rule,
        not a summary of it. A human's refusal posts no PROTOCOL-VIOLATION (`actions.refuse` skips that for a
        human), so a button pressed in error leaves nothing on the board.
        """
        try:
            body = json.loads(req.body or b"")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return _json(400, {"error": f"the request body is not JSON: {e}"})
        if not isinstance(body, dict):
            return _json(400, {"error": "the request body must be a JSON object"})
        if body.get("party") != "human":
            # Spec 22: the page may post as no other party. The context this server holds is the human's anyway,
            # so this is the page saying who it thinks it is and being held to it.
            return _json(400, {"error": f"the viewer acts as the human party only, not {body.get('party')!r}"})
        verb = body.get("verb")
        run = va.VERBS.get(verb) if isinstance(verb, str) else None
        if run is None:
            return _json(400, {"error": f"unknown verb {verb!r}; the page offers: {', '.join(sorted(va.VERBS))}"})
        try:
            result = run(self.ctx, body)
        except va.BadRequest as e:
            return _json(400, {"error": str(e)})
        except Refused as e:
            return _json(409, {"refused": str(e), "verb": e.refusal.verb.value, "target": e.refusal.target,
                               "rule": e.refusal.rule, "violation_posted": e.violation_posted})
        except PostError as e:
            return _json(409, {"error": str(e)})
        except (GitHubError, BoardError, DispatchError, GitError, ResultsError, ConfigError, AppAuthError,
                UnknownAdapter, OSError) as e:
            return _json(502, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            # A long-lived local tool: an unhandled exception here drops the connection and prints a traceback in
            # the person's terminal every time they press a button. A rendered 500 is strictly better, and
            # `_StopRequested`/`KeyboardInterrupt` are BaseException, so a stop still propagates.
            return _json(500, {"error": f"{type(e).__name__}: {e}"})
        with self._lock:
            self._state_cache = None      # the board has just moved; the next /api/state rebuilds rather than lying
        return _json(200, {"ok": True, "verb": verb, "result": result})
```

Note the order: `ConfigError` and `BadRequest` are both `ValueError` subclasses, so `BadRequest` is caught first and `ConfigError` only by the 502 tuple. Do not add a bare `except ValueError`.

- [ ] **Step 5: `_Handler` reads the body.** Replace `_dispatch`'s first line with:

```python
    def _dispatch(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            # Before the gate, like a malformed request line: nothing is read, so a hostile Content-Length
            # cannot make this process wait on a body it will never accept.
            self.send_error(413, "request body too large")
            return
        resp = self.server.viewer.handle(  # type: ignore[attr-defined]
            Request.parse(self.command, self.path, self.headers, self.rfile.read(length) if length else b""))
```

(the rest of `_dispatch` is unchanged).

- [ ] **Step 6: Run the focused tests, then the suite.**

Run: `uv run pytest tests/test_viewer_actions.py tests/test_viewer.py -q`
Then: `uv run pytest -q` — green, no warnings; record the printed total.

- [ ] **Step 7: Commit.**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/viewer/actions.py src/meld/viewer/server.py tests/test_viewer_actions.py tests/test_viewer.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: the viewer runs one human verb through the function the CLI calls"
```

---

### Task 5: The board on the page

**Files:**
- Modify: `src/meld/viewer/static/app.html` (a board panel and its row template)
- Modify: `src/meld/viewer/static/app.css` (the panel's layout)
- Modify: `src/meld/viewer/static/app.js` (`renderBoard`, called from `renderState`)
- Test: `tests/test_viewer.py`

**Interfaces:**
- Consumes: `/api/state`'s `status.issues[]` (`number`, `title`, `status`, `claimed_by`, `claimants`, `worker`, `challenged`, `legal`), `status.pulls[]` (`number`, `issue`, `state`, `merged`, `approvals`, `changes_requested`, `checks`, `legal`), `status.coordination.agreements` (`{ref: {status, joint_verified, legal}}`).
- Produces: a `.verb` button per driveable legal verb, carrying `data-action`, `data-ref` and `data-number`; Task 6 reads those attributes.

- [ ] **Step 1: Write the failing test.** In `tests/test_viewer.py`, add:

```python
def test_the_page_renders_the_board_and_offers_only_verbs_it_can_drive(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    html, js = get(v, "/").body.decode(), get(v, "/static/app.js").body.decode()
    assert 'id="issues"' in html and 'id="pulls"' in html and 'id="agreements"' in html
    assert 'id="row"' in html                       # the row template the board clones
    # The ten verbs the API offers are the ten the page may turn into buttons; anything else is shown as text.
    assert "DRIVEABLE" in js and "data-action" in js
    for verb in ("claim", "release", "merge", "decide", "remember", "challenge", "post", "tag",
                 "plan-approve", "plan-ack"):
        assert f'"{verb}"' in js
    assert "innerHTML" not in js
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `uv run pytest tests/test_viewer.py -q -k board`
Expected: FAIL — `assert 'id="issues"' in html`.

- [ ] **Step 3: `app.html`.** After `</header>` and before `<main id="streams">`, add:

```html
<section id="board-panel">
  <div class="panel"><h2>Issues</h2><div id="issues"></div></div>
  <div class="panel"><h2>Pull requests</h2><div id="pulls"></div></div>
  <div class="panel"><h2>Agreements</h2><div id="agreements"></div></div>
</section>
<template id="row">
  <div class="brow">
    <span class="rid"></span>
    <span class="rwhat"></span>
    <span class="rverbs"></span>
  </div>
</template>
```

- [ ] **Step 4: `app.css`.** Append:

```css
#board-panel { display: flex; gap: 8px; padding: 8px 8px 0; align-items: flex-start; }
.panel { flex: 1 1 0; min-width: 0; max-height: 22vh; overflow-y: auto; background: var(--panel);
  border: 1px solid var(--line); border-radius: 4px; }
.panel h2 { margin: 0; padding: 6px 10px; font-size: 13px; border-bottom: 1px solid var(--line); }
.brow { display: grid; grid-template-columns: 4.5rem 1fr auto; gap: 8px; padding: 2px 10px; align-items: baseline; }
.brow:hover { background: #1d212b; }
.rid { color: var(--accent); }
.rwhat { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rverbs { display: flex; gap: 4px; flex-wrap: wrap; }
button.verb { font: inherit; color: var(--fg); background: #222735; border: 1px solid var(--line);
  border-radius: 3px; padding: 0 6px; cursor: pointer; }
button.verb:hover { border-color: var(--accent); color: var(--accent); }
.rverbs .dim { padding: 0 4px; }
#streams { height: calc(100vh - 110px - 22vh); }
```

(the last rule replaces the existing `#streams { … height: calc(100vh - 110px); }` height only — edit that declaration in place rather than adding a second `#streams` rule.)

- [ ] **Step 5: `app.js`.** Add near the constants:

```javascript
// The verbs the API can run (spec 22). A legal verb outside this set — dispatch, collect, review, audit,
// reopen, reap, plan-issues, approve, request-changes, push-fix — belongs to a group or a worker run, so the
// page shows it as text rather than offering a button that would only ever be refused.
const DRIVEABLE = new Set(["claim", "release", "post", "tag", "challenge", "decide", "remember",
                           "plan-approve", "plan-ack", "merge"]);
```

and, before `renderState`, the renderer:

```javascript
function verbControls(into, legal, { ref, number }) {
  into.replaceChildren();
  for (const verb of legal || []) {
    if (!DRIVEABLE.has(verb)) { into.append(cell("dim", verb)); continue; }
    const b = text(document.createElement("button"), verb);
    b.className = "verb";
    b.dataset.action = verb;
    if (ref != null) b.dataset.ref = ref;
    if (number != null) b.dataset.number = String(number);
    into.append(b);
  }
}

function boardRow(into, id, what, legal, keys) {
  const row = el("row").content.firstElementChild.cloneNode(true);
  text(row.querySelector(".rid"), id);
  text(row.querySelector(".rwhat"), what);
  verbControls(row.querySelector(".rverbs"), legal, keys);
  into.append(row);
}

function renderBoard(s) {
  const issues = el("issues");
  issues.replaceChildren();
  for (const i of s.issues || []) {
    const held = [i.status, i.claimed_by && `claimed by ${i.claimed_by}`, i.worker, i.challenged && "challenged"];
    boardRow(issues, `#${i.number}`, `${i.title} — ${held.filter(Boolean).join(", ")}`, i.legal,
             { ref: `#${i.number}`, number: i.number });
  }
  const pulls = el("pulls");
  pulls.replaceChildren();
  for (const p of s.pulls || []) {
    const how = [p.merged ? "merged" : p.state, p.issue && `closes #${p.issue}`,
                 p.approvals.length && `approved by ${p.approvals.join(", ")}`,
                 p.changes_requested.length && `changes requested by ${p.changes_requested.join(", ")}`];
    boardRow(pulls, `#${p.number}`, how.filter(Boolean).join(", "), p.legal, { ref: `#${p.number}`, number: p.number });
  }
  const agreements = el("agreements");
  agreements.replaceChildren();
  for (const [ref, a] of Object.entries((s.coordination && s.coordination.agreements) || {})) {
    boardRow(agreements, ref, a.joint_verified ? `${a.status} (verified)` : a.status, a.legal, { ref });
  }
}
```

and call it at the end of `renderState`: `renderBoard(s);`.

- [ ] **Step 6: Read `app.js` once as a program**, as in Task 3, and write the trace in your report: `renderBoard` against a state with no issues, no pulls and no agreements; an issue whose `legal` is `["claim", "dispatch"]` (one button, one dim word); a PR with empty `approvals`; `cell()` returning a node (it does — check it) so `append(cell(...))` is sound.

- [ ] **Step 7: Run the suite and commit.**

Run: `uv run pytest -q` — green, no warnings; record the printed total.

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/viewer/static tests/test_viewer.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: the page shows the board and what the human may do to it"
```

---

### Task 6: The action bar

**Files:**
- Modify: `src/meld/viewer/static/app.html` (the bar and its result line)
- Modify: `src/meld/viewer/static/app.css` (the bar)
- Modify: `src/meld/viewer/static/app.js` (`FIELDS`, `showFields`, `runAction`, the click and submit handlers)
- Test: `tests/test_viewer.py`

**Interfaces:**
- Consumes: Task 4's `POST /api/action` request and response shapes; Task 5's `data-action`/`data-ref`/`data-number` buttons.
- Produces: nothing Python consumes.

- [ ] **Step 1: Write the failing test.** In `tests/test_viewer.py`, add:

```python
def test_the_page_has_one_action_bar_that_posts_and_renders_a_refusal_verbatim(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    html, js = get(v, "/").body.decode(), get(v, "/static/app.js").body.decode()
    assert 'id="bar"' in html and 'id="action"' in html and 'id="run"' in html and 'id="result"' in html
    assert '"/api/action"' in js and 'method: "POST"' in js and '"party": "human"' in js
    # every header verb a human may post is offered, and the command verbs are not (they go through challenge)
    for verb in ("ASK", "DIRECTIVE", "HALT", "RESUME", "REVERT", "PROVIDED", "RESOLVED", "ACK", "AMEND",
                 "APPROVE", "NEEDS-HUMAN"):
        assert f'"{verb}"' in js
    for verb in ("LEASE", "EXPIRED", "RELEASED", "LIMITED"):
        assert f'"{verb}"' not in js
    assert "refused" in js and "rule" in js and "innerHTML" not in js
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `uv run pytest tests/test_viewer.py -q -k action_bar`
Expected: FAIL — `assert 'id="bar"' in html`.

- [ ] **Step 3: `app.html`.** Directly after `</section>` closing `#board-panel`, add:

```html
<form id="bar" autocomplete="off">
  <select id="action" aria-label="action"></select>
  <select id="verb" aria-label="header verb" class="hidden"></select>
  <input id="ref" placeholder="ref (#13, plan-add-1)" aria-label="ref">
  <input id="text" placeholder="message" aria-label="message">
  <button id="run" type="submit">Run</button>
  <span id="result" class="dim"></span>
</form>
```

- [ ] **Step 4: `app.css`.** Append:

```css
#bar { display: flex; gap: 6px; align-items: center; padding: 6px 8px; background: var(--panel);
  border-bottom: 1px solid var(--line); }
#bar select, #bar input, #bar button { font: inherit; color: var(--fg); background: #222735;
  border: 1px solid var(--line); border-radius: 3px; padding: 2px 6px; }
#bar #text { flex: 1 1 auto; min-width: 8rem; }
#bar button { cursor: pointer; }
#bar button:hover { border-color: var(--accent); color: var(--accent); }
#result.bad { color: var(--bad); }
#result.ok { color: var(--ok); }
```

and change `#streams`'s height declaration to `height: calc(100vh - 110px - 22vh - 36px);`.

- [ ] **Step 5: `app.js`.** Add near the constants:

```javascript
// What each action needs from the bar: `ref`, free `text`, and a header `verb` for the two that carry one.
// Anything an action does not name is hidden and not sent, so `post` never carries a stale `--supersedes`
// and `plan-approve` never carries a ref.
const FIELDS = {
  "claim": ["ref"], "release": ["ref"], "merge": ["ref"], "decide": ["ref"],
  "plan-ack": ["ref"], "plan-approve": [],
  "remember": ["ref", "text"], "challenge": ["ref", "verb", "text"], "post": ["ref", "verb", "text"],
};
const POST_VERBS = ["ASK", "DIRECTIVE", "HALT", "RESUME", "REVERT", "PROVIDED", "RESOLVED", "ACK", "AMEND",
                    "APPROVE", "NEEDS-HUMAN"];
const CHALLENGE_ACTIONS = ["open", "evidence", "retract", "uphold", "dismiss"];
```

`tag` is deliberately absent from `FIELDS`: it takes eight independent fields and belongs to a form, not a bar. Its button still prefills (the action select simply has no entry, so the button is not rendered — `DRIVEABLE` keeps `tag` because the API runs it, and `FIELDS` is what the bar can spell). **Add `"tag": ["ref"]` only if the reviewer's task says to; do not improvise a tag control.**

Then the bar itself, after `renderBoard`:

```javascript
function options(select, values) {
  select.replaceChildren();
  for (const v of values) {
    const o = document.createElement("option");
    o.value = v;
    text(o, v);
    select.append(o);
  }
}

function showFields() {
  const wants = FIELDS[el("action").value] || [];
  for (const name of ["ref", "verb", "text"]) el(name).classList.toggle("hidden", !wants.includes(name));
  if (wants.includes("verb")) options(el("verb"), el("action").value === "challenge" ? CHALLENGE_ACTIONS : POST_VERBS);
}

function showResult(message, ok) {
  const node = el("result");
  node.className = ok ? "ok" : "bad";
  text(node, message);
}

async function runAction() {
  const action = el("action").value;
  const wants = FIELDS[action] || [];
  const payload = { party: "human", verb: action };
  if (wants.includes("ref")) {
    const ref = el("ref").value.trim();
    payload.ref = ref;
    const n = /^#?(\d+)$/.exec(ref);
    if (n) payload.number = Number(n[1]);
  }
  if (wants.includes("text")) payload.text = el("text").value;
  if (wants.includes("verb")) payload[action === "challenge" ? "action" : "verb"] = el("verb").value;
  showResult(`${action} …`, true);
  let response;
  try {
    response = await fetch(api("/api/action"), {
      method: "POST", cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    showResult(`${action}: ${e.message}`, false);
    return;
  }
  let out = {};
  try { out = await response.json(); } catch (e) { out = {}; }
  if (response.ok) {
    showResult(`${action} ok: ${JSON.stringify(out.result)}`, true);
    pollState();                       // the server dropped its cached state; show the moved board at once
  } else if (out.refused) {
    // Verbatim, rule included: the rule is the whole content of a refusal, and summarising it would make the
    // page a second authority on what is legal.
    showResult(`refused ${out.verb} ${out.target}: ${out.rule}`, false);
  } else {
    showResult(`${action}: ${response.status} ${out.error || ""}`, false);
  }
}
```

and the wiring, replacing the three trailing calls:

```javascript
options(el("action"), Object.keys(FIELDS));
showFields();
el("action").addEventListener("change", showFields);
el("bar").addEventListener("submit", (e) => { e.preventDefault(); runAction(); });
// A row's button never acts: it fills the bar and hands the person the keyboard. One execution path, and a
// merge is never one stray click away.
el("board-panel").addEventListener("click", (e) => {
  const b = e.target.closest("button.verb");
  if (!b) return;
  el("action").value = b.dataset.action;
  showFields();
  el("ref").value = b.dataset.ref || "";
  el(FIELDS[b.dataset.action]?.includes("text") ? "text" : "run").focus();
});

pollState();
pollStreams();
openFeed();
```

Note `pollState()` is called from `runAction` as well as on its timer; that is deliberate and harmless — it re-arms the same `setTimeout` chain, which doubles the state poll rate for the rest of the session if called often. **Guard it:** give `pollState` a `scheduled` flag so only the timer re-arms:

```javascript
let statePending = null;
async function pollState() {
  if (statePending) clearTimeout(statePending);
  try { renderState(await getJSON("/api/state")); } catch (e) { text(el("error"), e.message); }
  statePending = setTimeout(pollState, STATE_EVERY_MS);
}
```

(replace the existing `pollState` with this; `pollStreams` is unchanged.)

- [ ] **Step 6: Read `app.js` once as a program** — the whole file this time, since this is the last change to it. Trace: pressing Run with `action = "plan-approve"` (no ref, no text sent); with `action = "claim"` and `ref = "13"` (`number: 13` and `ref: "#13"`? — it sends `ref: "13"` and `number: 13`; `claim` uses only `number`, so that is correct, and say so); with `action = "post"`, `verb = "HALT"`, empty ref (the payload carries `ref: ""`, which `_optional` turns into `None`); clicking a `claim` button (fills ref, focuses Run); clicking a `remember` button (focuses text). Confirm `el("bar")` is a form so Enter submits, and that `e.preventDefault()` stops the page reloading. Write the traces in your report.

- [ ] **Step 7: Run the suite and commit.**

Run: `uv run pytest -q` — green, no warnings; record the printed total.

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/viewer/static tests/test_viewer.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: one action bar runs a human verb and shows what rules.py said"
```

---

### Task 7: The handoff and the README describe what the page can do

**Files:**
- Modify: `docs/HANDOFF.md` (the banner; the `viewer/` map bullet; a new "Plan 8c" section; the plan 8b section's "Still open" line; the "What is next" bullet about the multiplexed stream, now done)
- Modify: `README.md` (the `meld watch` row; the "Watching a run" paragraph)

**Interfaces:** none; documentation of Tasks 1–6 as landed.

- [ ] **Step 1: The banner.** Rewrite the `**Milestone:**` paragraph so it names plan 8b as the last plan merged to `main` (PR #23, merge commit `deb7c83`), says plan 8c (`meld watch`'s human verbs and the multiplexed feed) is this branch, `plan-8c-human-verbs`, and carries the suite count **you** measured in Task 6 (`uv run pytest -q` is the authority — do not copy a number from this plan).

- [ ] **Step 2: The map.** Extend the `viewer/` bullet with `actions.py` (the ten-verb dispatch table, `BadRequest` → 400, `Refused` → 409 in the CLI's own shape, everything from GitHub/git/config → 502, an unexpected error → 500), the `/api/action` route (party held to `human`, the body capped at `MAX_BODY_BYTES`, a success dropping the state cache), and the multiplexed `/api/events` (`Source`, `_Reader`, `encode_cursor`/`decode_cursor`, one connection, per-stream `end`, readers pruned when they have ended and are no longer listed). Update the `cli.py` bullet's command count only if it changed (it did not).

- [ ] **Step 3: The new section.** Before `## Plan 9 — run reliability (landed)`, add `## Plan 8c — the human verbs from the page (landed)` covering: what the ten verbs are and that `rules.py` decides every one; that a refusal is rendered verbatim with its rule and that a human's refusal posts no `PROTOCOL-VIOLATION`; that every row button prefills the bar and only `Run` acts; that `tag` is API-only for now; that the feed is one connection and what the cursor looks like; and a **Decisions for the owner** list (the cursor's per-frame cost, the `except Exception` → 500, buttons prefilling rather than acting, `tag` deferred to a form) and a **Known follow-ups** list (whatever the task reviews deferred — read them out of `.superpowers/sdd/2026-09-19-plan-8c-human-verbs/progress.md`, plus: the page still does not render `state_errors`; `Tail` detects a trim only by shrinkage; the framework's own worker-liveness decisions still use the bare boolean; `/api/config` is 8d).

- [ ] **Step 4: The 8b section and "What is next".** In the plan 8b section, the "Still open" sentence now names 8d alone. In "## What is next", the top bullet that schedules the multiplexed SSE stream as plan 8c's first task is done — replace it with one naming plan 8d (the `.meld.toml` form over `/api/config`) as what remains of plan 8, and say the four-source cap is gone.

- [ ] **Step 5: README.** The `meld watch` row gains the action half: it now says the page shows the board and runs the human verbs (`claim`, `release`, `post`, `challenge`, `decide`, `remember`, `plan approve`, `plan ack`, `merge`), that `rules.py` decides each one and a refusal is shown with its rule, and that every transcript streams over one connection. Drop "up to four transcripts at a time (`MAX_SOURCES`), alive roles first" — it is no longer true. The "Watching a run" paragraph gains one sentence: a row's button fills the action bar and nothing runs until you press Run.

- [ ] **Step 6: Run the suite and commit.**

Run: `uv run pytest -q` — the same count as Task 6, no warnings (no test reads the docs).

```bash
git -C /Users/scottbrant/code/meld-main add docs/HANDOFF.md README.md
git -C /Users/scottbrant/code/meld-main commit -m "docs: the handoff and README describe the page's human verbs"
```

---

## Self-review

**Spec coverage.** §22 "What the page may do": all ten verbs are in `VERBS` (Task 4) — `claim`, `release`, `post`, `tag`, `challenge`, `decide`, `remember`, `plan-approve`, `plan-ack`, `merge`; the human's overrides are header verbs `post` carries (Task 6's `POST_VERBS`, validated by `grammar.validate` inside `posting.post`, not by the page); "the viewer holds no legality logic" — `viewer/actions.py` has no conditional on the actor and `DRIVEABLE` is a list of what the API implements, not of what is legal (the legality list comes from `render_status`); "a refusal is rendered verbatim, including the rule it names" (Task 4's 409 shape, Task 6's `showResult`). §22 "What it may not do": no route starts, stops, kills or edits a run, and the party check refuses everything but `human`. §22's routes table and the streams paragraph are amended in Task 1 and implemented in Tasks 2–4. §4's layout gains `actions.py` (Task 1). §21's exclusions: no curses view, no token-level streaming, no supervisor control, no guided setup. **Gap found and closed:** the first draft left `/api/action`'s method unchecked — `handle` allowed only GET, so the route would have answered 405 forever; Task 4 adds `METHODS`. **Second gap:** `Request` had no body at all and `_Handler` never read one; Task 4 adds both.

**Placeholder scan.** No TBD/TODO. Two steps deliberately defer a number to measurement rather than prediction (Task 7's banner count, and each task's "record the printed total") — that is the Global Constraint, not a placeholder. Task 7's HANDOFF section is described rather than written out verbatim because it must quote the task reviews' deferred minors, which do not exist yet; its required content is enumerated.

**Type consistency.** `Source(stream, path, parse, writer)` — same field order in `streams.py`, `server._source` and the tests' `src()` helper. `events(sources, *, now, sleep, cursor, interval, check_every, max_polls)` — `server.api_events` passes exactly those keywords; note `cursor` is a string here, where plan 8b's `after` was an int. `frame(stream, event, *, cursor)` — two positional arguments now, and `end_frame(stream)` replaces the `END` constant; both call sites are in `events`. `VERBS`' values take `(ctx, body)` and `api_action` calls `run(self.ctx, body)`. The page's `data-action` values are `FIELDS`' keys, which are a subset of `DRIVEABLE`, which is `VERBS`' keys minus `tag`. `Refused.refusal.verb.value` is the CLI's spelling (`rules.Verb`), which is what `legal_issue_verbs` puts in `legal` — so the page's button labels and the refusal's `verb` agree.
