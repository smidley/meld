# Plan 8b: the loopback viewer — `meld watch`, one page, every agent's transcript side by side

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A person runs `meld watch`, opens the printed URL, and sees every live role and worker's transcript streaming in its own column, with the board's state, each supervisor's liveness as a tri-state with its reason, each role's standing freeze, budget spend, free disk and log bytes above them — reading only, nothing posted, nothing the framework depends on.

**Architecture:** `viewer/server.py` is a loopback HTTP server over `http.server.ThreadingHTTPServer`, fronted by a per-launch token in the URL and a `Host` check, routing to three JSON/SSE read routes and one hand-written page; every route is a method on a `Viewer` object that takes a plain `Request` and returns a plain `Response`, so the whole surface is tested in process without a socket. `viewer/streams.py` decides which transcripts exist and turns one into server-sent events over plan 8a's `Tail`, replaying from a `seq`, recovering from a trim, and synthesizing the terminal event a Codex transcript never writes. Liveness is a tri-state computed in `state.py` from records the processes write about themselves — `meld run` records its own pid and process start time and each role launch's, `dispatch` records a worker's — because a bare `kill(pid, 0)` is what announced a dead worker as running when its pid was reused.

**Tech Stack:** Python 3.13, standard library plus `click` only: `http.server`, `secrets`, `hmac`, `urllib.parse`, `importlib.resources`, `subprocess` (for `ps -o lstart=`), `threading`. One hand-written page — `static/app.html`, `app.css`, `app.js` — no CDN, no build step, no framework. Real files in `tmp_path` and `FakeGh` boards in tests; one test binds an OS-assigned loopback port.

**Spec:** `docs/superpowers/specs/2026-09-03-meld-design.md` — §22 "The viewer" (the whole of it: routes, authentication and binding, what the page may and may not do, the liveness tri-state) and §22 "Testing"; §4 (the `viewer/` layout and `watch` in the verb list); §5 (`[viewer]`); §2 (the loopback carve-out of the polling-only non-goal); §17 (the freeze file the page reports); §19 (the viewer suite); §21 (what plan 8 leaves out: a curses view, token-level streaming, supervisor control from the page, guided setup). Task 1 amends §4, §19 and §22 first; this plan implements those amendments and adds nothing they do not say. **Out of scope:** `/api/action` (plan 8c) and `/api/config` (plan 8d) — the server answers 404 for both until then.

## Global Constraints

- Python `>=3.12` (3.13 locally); runtime dependency `click>=8.1` only. Standard library plus click inside `src/meld/`. The page is three static files with no external reference of any kind.
- `rules.py` is the only legality authority. **Nothing in this plan touches legality:** the viewer reads and renders; the one place it shows legal verbs is `status.render_status`'s own `legal` lists, unchanged.
- `github.py` is the only GitHub path (plus `appauth.py`'s two App-JWT calls); `gitops.py` the only git runner. The viewer reaches GitHub only through `status.build_status`, which already goes through `github.py`.
- Adapters are the only code that knows a CLI. The viewer parses a transcript through the group's adapter's `parse_events` and never inspects a line itself.
- Workers never hold credentials, and a worker (`MELD_WORKER=1`) cannot run `meld watch`: `load_context` refuses it exactly as it refuses every other verb.
- **The token never touches disk and never appears in a log line.** It lives in the process and in the URL the human pasted; the request logger is silenced.
- **Records are evidence and are never deleted.** A supervisor or role record with a dead pid says "confirmed gone", which is more than "no record" can say; the next launch overwrites it.
- **Observability never outranks the run.** A record that cannot be written is swallowed, never raised: a role launch that succeeded must be tracked in `procs` whatever happened to its record, or the next cycle launches it twice.
- Tests are hermetic and real: real files over fakes where a file is the thing under test; no network, no real CLI, no host `~/.claude`/gitconfig/wall-clock dependence; inject `now`, `clock`, `sleep`, `alive`, `start_of`, `disk_usage`; pass `adapter_for=` to every `load_context` in tests. `uv run pytest -q` must be green with **no warnings** before every commit.
- Never set `MELD_SMOKE=1`.
- Conventional commit subjects, **no attribution trailers of any kind**, one logical change per commit; the repo-local identity `Scott Brant <421573+smidley@users.noreply.github.com>` is configured in the clone — check `git log -1 --format='%an <%ae>'` after the first commit.
- Work in `/Users/scottbrant/code/meld-main` on branch `plan-8b-viewer` (off `main` at `93be370`). `~/Documents/meld` is iCloud-synced and stalls git; do not work there. Commit with `git -C /Users/scottbrant/code/meld-main ...`.
- Baseline at this plan's base (`main` = `93be370`, plan 9 merged): `uv run pytest -q` → **773 passed**, no warnings. Each task leaves the suite green.

## Decisions (rulings made while writing the plan; recorded so executors do not re-litigate)

1. **Layout follows §4 literally, plus one module.** `src/meld/viewer/server.py` (HTTP, the gate, the routes) and `src/meld/viewer/static/{app.html,app.css,app.js}` are the spec's names. `src/meld/viewer/streams.py` is added beside them for stream discovery and the SSE generator, so `server.py` stays about HTTP; Task 1 adds it to §4's layout.
2. **The token rides in the query string, on every request.** `EventSource` cannot set a header, so `?t=<token>` is the one mechanism, compared with `hmac.compare_digest`. The page's own stylesheet and script are fetched through the same gate: the served HTML carries the token in their URLs (a `__TOKEN__` placeholder substituted at serve time), so no route is ever exempt.
3. **`Host` must be a loopback name with the served port.** Exactly `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>`; anything else is 403 with an empty body, same as a missing token. This is the DNS-rebinding defence §22 names. The server binds `127.0.0.1` only, so the printed URL always works and `localhost` (which may resolve to `::1` first) is accepted in the header but not advertised.
4. **Liveness evidence is written by the processes themselves.** `run_group` writes `$MELD_HOME/state/<group>/supervisor.json` `{pid, started_at, proc_start}` at startup and `state/<group>/<role>/current.json` `{pid, started_at, proc_start, log_path, reason, launch_no}` on every successful launch; every worker `RunRecord` gains `proc_start`. `proc_start` is `ps -o lstart= -p <pid>`'s own output — an opaque string compared for equality, never parsed: the same for the same process on every call, different for a reused pid, on macOS and Linux alike. Records are never deleted (Global Constraints).
5. **The tri-state is one function, `state.liveness`.** `alive` needs a live pid *and* a matching start time. `dead` is a pid that is gone, or a live pid whose start time differs from the record's (reused). `unknown` is everything the check cannot settle: no record, a record with no pid, a live pid with no recorded start time to compare against, or one `ps` cannot read. Every answer carries the reason that produced it, because "could not confirm alive" and "confirmed gone" are different facts (§22).
6. **`/api/state` is cached for five seconds, single-flight.** `build_status` is several paginated `gh` calls; a page polling every ten seconds in two tabs would otherwise spend a meaningful fraction of the token's rate limit. The cache is keyed on the injected monotonic `clock`; a failed build (502) is never cached. `/api/state` also carries each role's standing freeze (`limit.json`, plan 9): plan 9's own follow-up noted that a frozen role was invisible everywhere but `meld run`'s report, and this is the surface a person actually looks at — Task 1 amends the routes table to say so.
7. **A stream is a log file, identified by its path under `logs/`.** `<group>/<file>.log` and nothing else: two path components, a `.log` suffix, no empty or dot components, and the file must exist — so a request cannot name anything outside `$MELD_HOME/logs`. `/api/streams` lists one stream per configured role (its recorded current launch's log, else the newest log on disk marked `unknown`, because a log with no record of its writer is exactly "could not confirm") plus every worker run still on record, and it is the viewer's turn of the log cap: `sweep_logs(home / "logs", cfg.logs_max_bytes_per_run)` runs on every call, the same call `dispatch.tick` makes (plan 8a decision 8).
8. **Resume is by count, and a shortfall means a trim.** A fresh `Tail` stamps a file's events 1..n, so a client that last saw `seq` gets events `seq+1..n`. A client whose `seq` exceeds `n` was reading a log that has since been trimmed: it is told so with one `log_truncated` frame (id 0) and then the whole file, from the start. `Last-Event-ID` (which `EventSource` sends on its own reconnects) wins over `?after=`; a value that is not an integer reads as 0.
9. **The reader synthesizes the terminal event; the adapter never does.** §22's table leaves it to this plan: when a stream's writer is `dead` and its transcript, after `flush`, does not end in `run_finished`, `usage_limit` or `cli_error`, the stream emits one `run_finished` with `meta["synthesized"] = True` and then an `end` frame. A Codex transcript ends at `usage`; this is how the page learns the run is over. While the writer is `alive` or `unknown` the stream stays open and sends a comment heartbeat, and it checks the writer only every `check_every` idle polls (six, one second apart in production) because the check may run `ps`.
10. **`meld watch` is a human verb with a board.** It calls `load_context` like `meld status` — the human's `gh` login, the human's legal verbs in `render_status` (which plan 8c's buttons will read), and `load_context`'s worker refusal for free. It binds `[viewer] port` (default 0, the OS chooses) unless `--port` says otherwise, prints exactly one line — the URL — to stdout, opens the browser only when `[viewer] open_browser` or `--open` asks, and runs until Ctrl-C. The request logger is silenced entirely: the default one prints the request line, token included.
11. **The page renders event text as text nodes only.** Every string it shows came from a model or a tool's output; `innerHTML` never appears in `app.js`. `EventSource` handles reconnection and `Last-Event-ID` by itself, so the client holds no resume logic. Auto-scroll follows the newest event unless the person has scrolled up.
12. **The page is tested for what Python can see.** The three files are served with the right content types behind the gate, carry no external reference, name only routes the server has, and `app.js` uses `textContent` and never `innerHTML`. Rendering itself is not unit-tested: there is no JavaScript runtime in the toolchain and the spec puts the tests on the server. The first live run with the viewer open is the page's test.

## File structure

| File | Responsibility |
|---|---|
| `docs/superpowers/specs/2026-09-03-meld-design.md` | §4 layout (`streams.py`), §22 routes table (`/api/state` carries freezes; 8c/8d routes answer 404 until then), §22 liveness (the records, the `ps` string), §22 streams paragraph (discovery, resume, synthesis), §19 (the suite as built). Task 1. |
| `src/meld/config.py` | `viewer_port`, `viewer_open_browser`; `_port` validator. Task 1. |
| `src/meld/state.py` | `proc_start_of(pid, run=)`, `liveness(record, *, alive=, start_of=)`. Task 2. |
| `src/meld/board.py` | `RunRecord.proc_start: str = ""`. Task 2. |
| `src/meld/dispatch.py` | Both `RunRecord(...)` constructions record `proc_start`. Task 2. |
| `src/meld/runners.py` | `read_record`/`write_record` (with `read_freeze`/`write_freeze` as aliases), `supervisor_path`, `role_run_path`, `_note`; `run_group(start_of=)` writes the supervisor record and each role launch's record. Task 3. |
| `src/meld/viewer/__init__.py` | Empty; makes `meld.viewer` a package so `importlib.resources` can find `static/`. Task 4. |
| `src/meld/viewer/streams.py` | `stream_id`, `stream_path`, `record_for`, `discover`, `frame`, `events`, `TERMINAL_KINDS`, `END`, `PING`. Task 4. |
| `src/meld/viewer/server.py` | `Request`, `Response`, `Viewer` (gate + routes + state cache), `new_token` (Task 5); page and asset routes (Task 6); `_Handler`, `serve`, `url` (Task 7). |
| `src/meld/viewer/static/app.html`, `app.css`, `app.js` | The one page. Task 6. |
| `src/meld/cli.py` | `meld watch`; `SERVE` seam. Task 7. |
| `tests/test_config.py`, `tests/test_state.py`, `tests/test_dispatch.py`, `tests/test_runners.py` | Tasks 1–3. |
| `tests/test_viewer_streams.py` (new), `tests/test_viewer.py` (new), `tests/test_cli.py` | Tasks 4–7. |
| `docs/HANDOFF.md`, `README.md` | Task 8. |

---

### Task 1: The spec says what this plan builds, and `[viewer]` parses

**Files:**
- Modify: `docs/superpowers/specs/2026-09-03-meld-design.md` (§4 layout at the `viewer/` block; §19's "Transcripts and the viewer" bullet; §22's routes table, liveness paragraph, and a new streams paragraph)
- Modify: `src/meld/config.py` (two `Config` fields, `_port`, the `[viewer]` read in `from_dict`)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.viewer_port: int` (0–65535, default 0) and `Config.viewer_open_browser: bool` (default `False`), read from `[viewer] port` / `[viewer] open_browser`. Task 7's `meld watch` consumes both.

- [ ] **Step 1: Amend the spec — §4's layout.** In the layout block under `viewer/`, the two lines

```
      server.py             # loopback HTTP, token and Host checks, routes (section 22)
      static/               # one hand-written page: app.html, app.css, app.js; no CDN, no build step
```

become three:

```
      server.py             # loopback HTTP, token and Host checks, routes (section 22)
      streams.py            # which transcripts exist, and one run's events as a stream; the viewer's turn of the log cap
      static/               # one hand-written page: app.html, app.css, app.js; no CDN, no build step
```

- [ ] **Step 2: Amend the spec — §22's routes table.** The `/api/state` row

```
| `/api/state` | GET | `status.render_status`, plus supervisor liveness, budget spend, free disk, and log bytes |
```

becomes

```
| `/api/state` | GET | `status.render_status`, plus supervisor liveness, each role's standing freeze (`limit.json`, section 17), budget spend, free disk, and log bytes |
```

and directly under the table (before "**Authentication and binding.**") add this paragraph:

```
`/api/action` and `/api/config` are plans 8c and 8d; until they land the server answers 404 for both, and the page offers no button for either.

**Streams.** `/api/streams` lists one transcript per configured role — its recorded current launch's log, else the newest log on disk for that role, marked `unknown` because a log with no record of its writer is exactly "could not confirm" — plus every worker run still on record; and it is the viewer's turn of the log cap: `sweep_logs` runs on every call, the same call `meld tick` makes. A stream is named by its path under `logs/`, `<group>/<file>.log` and nothing else, so a request cannot reach outside that directory. `/api/events` replays from the `seq` the client last saw (`Last-Event-ID`, which the browser sends on its own reconnects, else `after=`); a client that has seen more events than the file now holds was reading a log that has since been trimmed, and is told so with one `log_truncated` frame before the file is replayed from the start. When the writer is confirmed dead and the transcript, after its last unterminated line is flushed, does not end in `run_finished`, `usage_limit` or `cli_error` — a Codex run ends at `usage` — the stream synthesizes one `run_finished` marked `synthesized` and closes with an `end` frame. While the writer is alive or unknown the stream stays open on a comment heartbeat.
```

- [ ] **Step 3: Amend the spec — §22's liveness paragraph.** After the sentence ending `"Could not confirm alive" and "confirmed gone" are different facts and the page shows which one it has.` append, in the same paragraph:

```
 The evidence is written by the processes themselves: `meld run` records its own pid and process start time at `$MELD_HOME/state/<group>/supervisor.json` and each role launch's at `state/<group>/<role>/current.json` (with the log it writes, its launch number and reason), and every worker's run record carries `proc_start`. The start time is `ps -o lstart=`'s own string, compared for equality and never parsed — the same for the same process on every call, different for a reused pid. `alive` needs a live pid and a matching start time; a pid that is gone, or a live one whose start time differs, is `dead`; a live pid with no recorded start time to compare against, or one `ps` cannot read, is `unknown`. A record is never deleted — a dead pid in one is the fact "confirmed gone", which is more than "no record" can say — and the next launch overwrites it. A record that cannot be written is swallowed, never raised: observability does not outrank the run it observes.
```

- [ ] **Step 4: Amend the spec — §19's bullet.** Replace the bullet beginning `- **Transcripts and the viewer**` with:

```
- **Transcripts and the viewer** (`test_transcript.py`, `test_viewer.py`, `test_viewer_streams.py`, `test_state.py`, `test_config_write.py`): the event vocabulary and tail reader over recorded JSONL fixtures for both CLIs; the viewer's token, `Host`, and routing checks, driven in-process against the same board fixtures the rules tests use, with a `Request` in and a `Response` out and no socket; stream discovery, SSE replay from a `seq`, trim recovery and terminal synthesis over `tmp_path` logs with injected `now`, `sleep` and writer liveness; the liveness tri-state over injected `alive` and `start_of` probes; the config writer's round trip (plan 8d); each adapter's parse over every event type that CLI emits. One test binds an OS-assigned loopback port to prove the wiring; no test launches a CLI, and none depends on wall-clock time — `now` is injected everywhere. One test does sleep: the `O_APPEND` case polls a real child process's log until it has written, against a bounded deadline, because the thing under test is that a live writer survives a truncation.
```

- [ ] **Step 5: Commit the spec.**

```bash
git -C /Users/scottbrant/code/meld-main add docs/superpowers/specs/2026-09-03-meld-design.md
git -C /Users/scottbrant/code/meld-main commit -m "docs: section 22 names the viewer's streams, records and liveness evidence"
```

- [ ] **Step 6: Write the failing config tests.** Append to `tests/test_config.py` (add `import tomllib` to its imports if it is not already there):

```python
def test_viewer_table_has_defaults_and_is_overridable():
    base = tomllib.loads(MINIMAL)
    default = Config.from_dict(base)
    assert default.viewer_port == 0 and default.viewer_open_browser is False
    tuned = Config.from_dict({**base, "viewer": {"port": 8642, "open_browser": True}})
    assert tuned.viewer_port == 8642 and tuned.viewer_open_browser is True


@pytest.mark.parametrize("bad", [65536, -1, "8080", True, 1.5])
def test_viewer_port_must_be_a_tcp_port_number(bad):
    """0 is legitimate (the OS chooses); anything that is not an int in 0..65535 is a config error naming the key."""
    base = tomllib.loads(MINIMAL)
    with pytest.raises(ConfigError, match="viewer.port"):
        Config.from_dict({**base, "viewer": {"port": bad}})
```

- [ ] **Step 7: Run them to verify they fail.**

Run: `uv run pytest tests/test_config.py -q -k viewer`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'viewer_port'` for the first, and the second does not raise.

- [ ] **Step 8: Implement.** In `src/meld/config.py`, directly after `_positive_int` add:

```python
def _port(value: Any, path: str) -> int:
    """A TCP port for `meld watch` to bind: a real TOML integer in 0..65535, where 0 lets the OS choose. `int()` would
    quietly accept "8080", 1.5 and True (bool is an int subclass)."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 65535:
        raise ConfigError(f"{path} must be an integer from 0 to 65535 (0 lets the OS choose), got {value!r}")
    return value
```

In the `Config` dataclass, after `logs_warn_free_mb: int = 2048` add:

```python
    viewer_port: int = 0
    viewer_open_browser: bool = False
```

In `from_dict`, after `logs = data.get("logs", {})` add `viewer = data.get("viewer", {})`, and at the end of the `cls(...)` call, after the `logs_warn_free_mb=...` argument, add:

```python
            viewer_port=_port(viewer.get("port", 0), "viewer.port"),
            viewer_open_browser=bool(viewer.get("open_browser", False)),
```

- [ ] **Step 9: Run the suite.**

Run: `uv run pytest -q`
Expected: **779 passed**, no warnings (773 + 1 + the parametrized test's 5 cases).

- [ ] **Step 10: Commit.**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/config.py tests/test_config.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: [viewer] port and open_browser are configuration"
```

---

### Task 2: The liveness tri-state, and the start time every pid is paired with

**Files:**
- Modify: `src/meld/state.py` (add `proc_start_of`, `liveness`; `import subprocess`)
- Modify: `src/meld/board.py:290-310` (`RunRecord` gains `proc_start: str = ""` as its last field)
- Modify: `src/meld/state.py:103-114` (`_read_record` reads `proc_start`)
- Modify: `src/meld/dispatch.py:45` (import), `:454-456` and `:514-516` (both `RunRecord(...)` constructions)
- Test: `tests/test_state.py`, `tests/test_dispatch.py`

**Interfaces:**
- Produces: `state.proc_start_of(pid: int, run=subprocess.run) -> str` — `ps -o lstart= -p <pid>` stripped, `""` on any failure or for `pid <= 0`. `state.liveness(record: Mapping | None, *, alive=pid_alive, start_of=proc_start_of) -> dict` with keys `state` (`"alive" | "dead" | "unknown"`), `reason: str`, `pid: int | None`, `started_at: str | None`, `proc_start: str`. `RunRecord.proc_start: str`. Tasks 3, 4 and 5 consume all three.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_state.py` (add `import subprocess` and extend the `meld.state` import with `liveness, proc_start_of`; `replace` and `mk_run` are already imported there):

```python
def test_proc_start_of_returns_ps_own_words_and_empty_on_any_failure():
    calls = []

    def ps_ok(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="Tue Sep 16 08:27:05 2026\n", stderr="")

    assert proc_start_of(4242, run=ps_ok) == "Tue Sep 16 08:27:05 2026"
    assert calls == [["ps", "-o", "lstart=", "-p", "4242"]]
    assert proc_start_of(4242, run=lambda argv, **kw: subprocess.CompletedProcess(argv, 1, stdout="", stderr="")) == ""

    def missing(argv, **kw):
        raise FileNotFoundError("ps")

    def slow(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 5)

    assert proc_start_of(4242, run=missing) == ""
    assert proc_start_of(4242, run=slow) == ""
    assert proc_start_of(0, run=ps_ok) == "" and len(calls) == 1   # no pid, no ps


LIVE = {"pid": 4242, "started_at": "2026-09-18T12:00:00Z", "proc_start": "Fri Sep 18 05:00:00 2026"}


@pytest.mark.parametrize("record, alive, start, state, reason_has", [
    (None, True, "x", "unknown", "no record"),
    ({"started_at": "2026-09-18T12:00:00Z"}, True, "x", "unknown", "no pid"),
    (LIVE, False, "x", "dead", "gone"),
    ({**LIVE, "proc_start": ""}, True, "x", "unknown", "no start time"),
    (LIVE, True, "", "unknown", "could not be read"),
    (LIVE, True, "Sat Sep 19 09:00:00 2026", "dead", "reused"),
    (LIVE, True, LIVE["proc_start"], "alive", "started"),
])
def test_liveness_is_a_tri_state_with_a_reason(record, alive, start, state, reason_has):
    out = liveness(record, alive=lambda pid: alive, start_of=lambda pid: start)
    assert out["state"] == state and reason_has in out["reason"]
    assert out["pid"] == (4242 if record and "pid" in record else None)
    assert out["started_at"] == (record or {}).get("started_at")
    assert out["proc_start"] == (record or {}).get("proc_start", "")


def test_liveness_never_asks_ps_about_a_pid_that_is_gone():
    def start_of(pid):
        raise AssertionError("ps must not run for a dead pid")

    assert liveness(LIVE, alive=lambda pid: False, start_of=start_of)["state"] == "dead"


def test_run_record_round_trips_proc_start_and_reads_an_older_file_as_empty(tmp_path):
    store = StateStore(tmp_path, "claude")
    rec = replace(mk_run("claude", 12), proc_start="Fri Sep 18 05:00:00 2026")
    path = store.write_run(rec)
    assert json.loads(path.read_text())["proc_start"] == "Fri Sep 18 05:00:00 2026"
    assert store.load_runs(alive=lambda pid: True)["claude"][12].proc_start == "Fri Sep 18 05:00:00 2026"
    older = json.loads(path.read_text())
    older.pop("proc_start")
    path.write_text(json.dumps(older))              # a record written before this field existed
    assert store.load_runs(alive=lambda pid: True)["claude"][12].proc_start == ""
```

Append to `tests/test_dispatch.py` (add `import meld.dispatch as dispatch_module` to its imports):

```python
def test_dispatch_records_the_process_start_time_beside_the_pid(tmp_path, monkeypatch):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    monkeypatch.setattr(dispatch_module, "proc_start_of", lambda pid: f"started-{pid}")
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=DONE_SCRIPT), now=NOW)
    rec = json.loads((tmp_path / "home" / "runs" / "claude" / "11.json").read_text())
    assert rec["proc_start"] == f"started-{out['pid']}"
```

- [ ] **Step 2: Run them to verify they fail.**

Run: `uv run pytest tests/test_state.py tests/test_dispatch.py -q -k "proc_start or liveness"`
Expected: FAIL — `ImportError: cannot import name 'liveness'` (the whole module fails to import), and the dispatch test fails with `AttributeError: module 'meld.dispatch' has no attribute 'proc_start_of'`.

- [ ] **Step 3: Implement `state.py`.** Add `import subprocess` to its imports, then after `pid_alive`:

```python
def proc_start_of(pid: int, run=subprocess.run) -> str:
    """The instant the kernel started `pid`, in `ps`'s own words, or "" when it cannot be read.

    An opaque string compared for equality and never parsed: `ps -o lstart=` prints the same value for the same
    process on every call and a different one for a pid that has been reused, which is all a liveness check needs
    to know. Empty on every failure -- no `ps`, a timeout, a pid that is gone -- so a machine without `ps`
    degrades to `unknown` rather than to a crash.
    """
    if pid <= 0:
        return ""
    try:
        out = run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def liveness(record: Mapping[str, object] | None, *, alive: Callable[[int], bool] = pid_alive,
             start_of: Callable[[int], str] = proc_start_of) -> dict:
    """Is the process a record describes still the process it described? A tri-state with its reason (spec 22).

    `alive` needs a live pid AND a matching start time. `dead` is a pid that is gone, or a live pid whose start
    time is not the recorded one: the pid was reused, which is what announced a dead audit worker as running on
    2026-09-11. `unknown` is everything the check cannot settle -- no record, no pid, no recorded start time to
    compare against, or a start time `ps` could not read -- because "could not confirm alive" and "confirmed
    gone" are different facts and the page shows which one it has.
    """
    if not record:
        return {"state": "unknown", "reason": "no record of a process", "pid": None, "started_at": None, "proc_start": ""}
    try:
        pid = int(record.get("pid", 0))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        pid = 0
    started_at = record.get("started_at")
    recorded = str(record.get("proc_start") or "")
    base = {"pid": pid or None, "started_at": started_at if isinstance(started_at, str) else None, "proc_start": recorded}
    if pid <= 0:
        return {**base, "state": "unknown", "reason": "the record carries no pid"}
    if not alive(pid):
        return {**base, "state": "dead", "reason": f"pid {pid} is gone"}
    if not recorded:
        return {**base, "state": "unknown", "reason": f"pid {pid} is alive, but the record carries no start time to match it against"}
    current = start_of(pid)
    if not current:
        return {**base, "state": "unknown", "reason": f"pid {pid} is alive, but its start time could not be read"}
    if current != recorded:
        return {**base, "state": "dead", "reason": f"pid {pid} was reused: it started {current}; the record says {recorded}"}
    return {**base, "state": "alive", "reason": f"pid {pid} started {recorded}"}
```

`Mapping` and `Callable` come from `collections.abc`; add whichever `state.py` does not already import.

- [ ] **Step 4: Implement the record field.** In `src/meld/board.py`, add as the last field of `RunRecord`, after `worker: str = ""`:

```python
    proc_start: str = ""        # `ps -o lstart=` for `pid` at launch, so a reused pid is not mistaken for the run (spec 22)
```

In `src/meld/state.py`'s `_read_record`, add `proc_start=str(data.get("proc_start", ""))` as the last keyword of the `RunRecord(...)` call. `write_run` serializes with `asdict`, so the field round-trips with no further change.

- [ ] **Step 5: Implement the dispatch side.** In `src/meld/dispatch.py` change `from meld.state import pid_alive` to `from meld.state import pid_alive, proc_start_of`. In both `RunRecord(...)` constructions (the build one after `pid, log_path = _launch(...)` near line 454 and the review/audit one near line 514) add the keyword `proc_start=proc_start_of(pid)` — it must be evaluated after `_launch` returned, i.e. inside the `RunRecord(...)` call as written.

- [ ] **Step 6: Run the suite.**

Run: `uv run pytest -q`
Expected: **790 passed** (779 + 1 + 7 parametrized cases + 1 + 1 in `test_state.py`, + 1 in `test_dispatch.py`), no warnings.

- [ ] **Step 7: Commit.**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/state.py src/meld/board.py src/meld/dispatch.py tests/test_state.py tests/test_dispatch.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: liveness is a tri-state, and every recorded pid carries its process start time"
```

---

### Task 3: The supervisor and its roles record themselves

**Files:**
- Modify: `src/meld/runners.py:129-144` (path helpers, `read_record`/`write_record`, `_note`), `:236-238` (the `run_group` signature), `:266-271` (the supervisor record), `:303-305` (the role record in `_try_launch`)
- Test: `tests/test_runners.py`

**Interfaces:**
- Consumes: `state.proc_start_of` (Task 2).
- Produces: `runners.supervisor_path(home, group) -> Path` (`state/<group>/supervisor.json`), `runners.role_run_path(home, group, role) -> Path` (`state/<group>/<role>/current.json`), `runners.read_record(path) -> dict | None`, `runners.write_record(path, payload)`; `run_group(..., start_of: Callable[[int], str] = proc_start_of)`. The supervisor record is `{"pid", "started_at", "proc_start"}`; a role record is `{"pid", "started_at", "proc_start", "log_path", "reason", "launch_no"}`. Task 4 consumes the paths and `read_record`; Task 5 consumes `supervisor_path`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_runners.py` (extend the `meld.runners` import with `read_record, role_run_path, supervisor_path`):

```python
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
    adapter = FakeAdapter(script="import sys; sys.exit(1)")
    clock = Clock()

    def sleep(s):
        clock.sleep(s)
        time.sleep(0.05)  # let the tiny real processes exit

    out = run_group(ctx, adapter=adapter, cycles=4, interval=0.01, sleep=sleep, clock=clock, base_env=env,
                    start_of=lambda pid: "born")
    rec = read_record(role_run_path(tmp_path / "home", "claude", "orchestrator"))
    assert rec["launch_no"] == out["roles"]["orchestrator"]["launches"] >= 2 and rec["reason"] == "crash"


def test_a_record_that_cannot_be_written_never_costs_a_launch(tmp_path):
    """Observability does not outrank the run: a launch that succeeded is tracked whatever happened to its record,
    or the next cycle would launch the role a second time."""
    _, ctx, env = make(tmp_path)
    state_dir = tmp_path / "home" / "state" / "claude"
    state_dir.mkdir(parents=True)
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
```

- [ ] **Step 2: Run them to verify they fail.**

Run: `uv run pytest tests/test_runners.py -q -k "records or relaunch_rewrites or cannot_be_written"`
Expected: FAIL — `ImportError: cannot import name 'read_record' from 'meld.runners'`.

- [ ] **Step 3: Implement the helpers.** In `src/meld/runners.py`, add `from meld.state import proc_start_of` to the imports, and replace the three functions `freeze_path`, `read_freeze`, `write_freeze` (lines 129–144) with:

```python
def freeze_path(home: Path, group: str, role: str) -> Path:
    """Where a usage freeze is remembered. Local state, so a supervisor restart honours a live freeze."""
    return home / "state" / group / role / "limit.json"


def supervisor_path(home: Path, group: str) -> Path:
    """Where a group's supervisor records itself: its pid and the instant the kernel started it (spec 22)."""
    return home / "state" / group / "supervisor.json"


def role_run_path(home: Path, group: str, role: str) -> Path:
    """Where a role's current launch is recorded: pid, start time, the log it writes, launch number and reason."""
    return home / "state" / group / role / "current.json"


def read_record(path: Path) -> dict | None:
    """A JSON object at `path`, or None when there is none, it is unreadable, or it is not an object."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_record(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(payload, indent=2) + "\n")


# The freeze file is one such record; these names are what plan 9 and its tests call the same two functions.
read_freeze = read_record
write_freeze = write_record


def _note(path: Path, payload: dict) -> None:
    """Write a record about a process, and swallow the failure if the disk will not take it.

    A record is evidence for `meld watch`, nothing more: no decision in this module reads one back. So a state
    directory that cannot be written -- the full disk of 2026-09-12, a permission -- must not turn a launch that
    succeeded into an exception, because the process is running whether or not it was written down, and a
    `_try_launch` that raised after `adapter.launch` returned would leave it out of `procs` and launch it again.
    """
    try:
        write_record(path, payload)
    except OSError:
        pass
```

- [ ] **Step 4: Implement the records in `run_group`.** Change the signature to add the seam:

```python
def run_group(ctx, *, adapter: Adapter | None = None, once: bool = False, cycles: int | None = None, interval: float = 5.0,
              sleep=time.sleep, clock=time.monotonic, wall: Callable[[], str] = _iso_now,
              start_of: Callable[[int], str] = proc_start_of, base_env: dict | None = None) -> dict:
```

and extend the docstring's last sentence: `Both are injected so tests never wait; \`start_of\` reads a process's start time (\`state.proc_start_of\`) and is injected so tests never run \`ps\`.`

Directly after `previous = {sig: signal.signal(sig, on_signal) for sig in (signal.SIGTERM, signal.SIGINT)}` add:

```python
    # Who this supervisor is, for `meld watch`'s liveness check (spec 22). Never removed: a dead pid in this file
    # is "confirmed gone", which is more than a missing file can say, and the next supervisor overwrites it.
    _note(supervisor_path(ctx.store.home, group.name),
          {"pid": os.getpid(), "started_at": wall(), "proc_start": start_of(os.getpid())})
```

In `_try_launch`, between `pending.pop(role, None)` and `fresh.restarts, fresh.last_exit = restarts, last_exit`, add:

```python
        _note(role_run_path(ctx.store.home, group.name, role),
              {"pid": fresh.proc.pid, "started_at": wall(), "proc_start": start_of(fresh.proc.pid),
               "log_path": str(fresh.log_path), "reason": reason, "launch_no": launch_no})
```

- [ ] **Step 5: Run the suite.**

Run: `uv run pytest -q`
Expected: **793 passed**, no warnings. Every pre-existing runner test still passes: `start_of` defaults to the real `proc_start_of`, which runs `ps` for the tiny fake processes and answers `""` for one that has already exited — harmless, and no existing assertion looks at the records.

- [ ] **Step 6: Commit.**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/runners.py tests/test_runners.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: the supervisor and each role launch record their pid and start time"
```

---

### Task 4: `viewer/streams.py` — which transcripts exist, and one of them as a stream

**Files:**
- Create: `src/meld/viewer/__init__.py` (empty)
- Create: `src/meld/viewer/streams.py`
- Test: `tests/test_viewer_streams.py`

**Interfaces:**
- Consumes: `transcript.Tail`, `transcript.newest_log`, `transcript.sweep_logs`, `transcript.Event`; `runners.read_record`, `runners.role_run_path` (Task 3); `state.StateStore`, `state.liveness`, `state.pid_alive`, `state.proc_start_of` (Task 2); `prompts.ROLES`.
- Produces: `stream_id(logs_root, path) -> str`; `stream_path(logs_root, stream) -> Path | None`; `record_for(cfg, home, path) -> dict | None`; `discover(cfg, home, *, alive=pid_alive, start_of=proc_start_of) -> list[dict]` (each `{"id", "group", "kind", "role", "issue", "log_path", "bytes", "liveness"}`); `frame(event) -> bytes`; `TERMINAL_KINDS`; `END`; `PING`; `events(path, parse, *, after=0, now, writer, sleep, interval=1.0, check_every=6, max_polls=None) -> Iterator[bytes]`. Task 5 consumes all of them.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_viewer_streams.py`:

```python
import json
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
```

- [ ] **Step 2: Run them to verify they fail.**

Run: `uv run pytest tests/test_viewer_streams.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'meld.viewer'`.

- [ ] **Step 3: Implement.** Create `src/meld/viewer/__init__.py` containing only `"""The loopback viewer: meld watch (spec section 22)."""`. Create `src/meld/viewer/streams.py`:

```python
"""Which transcripts the viewer can show, and one of them as a stream of server-sent events (spec section 22).

A stream is a log file under `$MELD_HOME/logs`, named by its path there. Discovery pairs each one with the record
of the process that wrote it, so the page can say whether the writer is alive, dead, or unknown; the event stream
is plan 8a's `Tail` framed as SSE, replaying from the `seq` a client last saw and ending only when the writer is
confirmed gone.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import asdict
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
END = b"event: end\ndata: {}\n\n"
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
    return path if path.is_file() else None


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


def frame(event: Event) -> bytes:
    """One SSE frame: the event's `seq` as the id (what `Last-Event-ID` carries back), its kind as the event name."""
    return f"id: {event.seq}\nevent: {event.kind}\ndata: {json.dumps(asdict(event), ensure_ascii=False)}\n\n".encode()


def events(path: Path, parse: Callable[[str, str], list[Event]], *, after: int = 0, now: Callable[[], str],
           writer: Callable[[], dict], sleep: Callable[[float], None], interval: float = 1.0,
           check_every: int = CHECK_EVERY, max_polls: int | None = None) -> Iterator[bytes]:
    """One transcript as SSE frames, from `after` onwards, until its writer is confirmed gone.

    Resume is by count: a fresh `Tail` stamps the file's events 1..n, so the client gets `after+1..n`. A client
    whose `after` exceeds n was reading a log that has since been trimmed; it is told so with one `log_truncated`
    frame and then the whole file. Then the log is followed. Every `check_every` idle polls the writer is asked
    after (`writer()` is `state.liveness` for its record, which may run `ps`): `dead` flushes the unterminated
    last line, synthesizes the terminal event a Codex transcript never writes (spec 22's table leaves that to the
    reader), and ends the stream; anything else sends a comment heartbeat and keeps following.
    """
    tail = Tail(path)
    replay = tail.read(parse, now())
    if after > len(replay):
        yield frame(Event(seq=0, at=now(), kind="log_truncated",
                          text=f"{path.name} was trimmed since this page last read it; showing it from the start"))
        after = 0
    last_kind = replay[-1].kind if replay else None
    for ev in replay[after:]:
        yield frame(ev)
    idle = polls = 0
    while max_polls is None or polls < max_polls:
        polls += 1
        fresh = tail.read(parse, now())
        for ev in fresh:
            last_kind = ev.kind
            yield frame(ev)
        if fresh:
            idle = 0
        else:
            idle += 1
            if idle % check_every == 0:
                if writer().get("state") == "dead":
                    for ev in tail.flush(parse, now()):
                        last_kind = ev.kind
                        yield frame(ev)
                    if last_kind not in TERMINAL_KINDS:
                        yield frame(Event(seq=tail.seq + 1, at=now(), kind="run_finished",
                                          text="process exited; the transcript ends without a terminal event",
                                          meta={"synthesized": True}))
                    yield END
                    return
                yield PING
        sleep(interval)
```

- [ ] **Step 4: Run the new tests.**

Run: `uv run pytest tests/test_viewer_streams.py -q`
Expected: 10 passed. If `test_events_replays_then_follows...` disagrees on the heartbeat count, trace the polls: poll 1 reads nothing (the append happens in the sleep after it), checks `alive`, pings; poll 2 reads `three`; poll 3 reads nothing, checks `alive`, pings; poll 4 reads nothing, checks `dead`, flushes `four`, synthesizes, ends. Two pings, three writer checks.

- [ ] **Step 5: Run the suite.**

Run: `uv run pytest -q`
Expected: **803 passed**, no warnings.

- [ ] **Step 6: Commit.**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/viewer/__init__.py src/meld/viewer/streams.py tests/test_viewer_streams.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: the viewer discovers transcripts and streams one as server-sent events"
```

---

### Task 5: `viewer/server.py` — the gate and the three API routes, in process

**Files:**
- Create: `src/meld/viewer/server.py`
- Test: `tests/test_viewer.py`

**Interfaces:**
- Consumes: everything Task 4 produces; `status.build_status`; `runners.freeze_path`, `runners.read_record`, `runners.supervisor_path` (Task 3); `state.liveness`/`pid_alive`/`proc_start_of` (Task 2); `transcript.total_bytes`; `adapters.UnknownAdapter`; `Context.adapter_for`, `Context.cfg`, `Context.store.home`.
- Produces: `Request(method, path, query, headers)` with `Request.parse(method, target, headers)`; `Response(status, body, content_type, headers)` with `.streaming`; `Viewer(ctx, *, token, port, now=, clock=, sleep=, alive=, start_of=, disk_usage=, state_ttl=5.0, interval=1.0, check_every=6, max_polls=None)` with `.token`, `.port`, `.admitted(req) -> Response | None`, `.handle(req) -> Response`, and a `ROUTES` table mapping a path to a method name; `new_token() -> str`; `LOOPBACK_HOSTS`; `SECURITY_HEADERS`; `STATE_TTL_SECONDS`. Tasks 6 and 7 extend `ROUTES` and wrap `handle` in HTTP.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_viewer.py`:

```python
import json
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest

from meld.context import load_context
from meld.github import GitHubError
from meld.runners import freeze_path, role_run_path, supervisor_path, write_record
from meld.state import StateStore
from meld.viewer import server
from meld.viewer.server import LOOPBACK_HOSTS, Request, Response, Viewer, new_token
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.factories import mk_run
from tests.fakeadapter import FakeAdapter
from tests.fakegh import FakeGh

NOW = "2026-09-18T12:00:00Z"
TOKEN = "t0k3n-fixed-for-tests"


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def viewer_for(tmp_path, **overrides):
    """The human's context over the recorded board (PROJECT_TOML configures groups `claude` and `codex`), and a
    Viewer with every seam injected."""
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    ctx = load_context(tmp_path / ".meld.toml", env=env_for(tmp_path, group=None), run=fake,
                       adapter_for=lambda name: FakeAdapter())
    kw = dict(token=TOKEN, port=8765, now=lambda: NOW, clock=Clock(), sleep=lambda s: None, alive=lambda pid: True,
              start_of=lambda pid: "T0", disk_usage=lambda p: SimpleNamespace(total=0, used=0, free=3 * 1024 ** 3),
              check_every=1, max_polls=1)
    kw.update(overrides)
    return Viewer(ctx, **kw), ctx, fake


def get(v: Viewer, path: str, *, token: str | None = TOKEN, host: str = "127.0.0.1:8765", method: str = "GET",
        headers: dict | None = None) -> Response:
    target = path + (("&" if "?" in path else "?") + f"t={token}" if token is not None else "")
    return v.handle(Request.parse(method, target, {"Host": host, **(headers or {})}))


def body(resp: Response) -> dict:
    assert not resp.streaming
    return json.loads(resp.body)


def test_request_parse_splits_the_target_and_lower_cases_header_names():
    req = Request.parse("get", "/api/events?stream=claude%2Fpm-1.log&t=abc&after=4", {"Host": "127.0.0.1:1", "Last-Event-ID": "7"})
    assert req.method == "GET" and req.path == "/api/events"
    assert req.query == {"stream": ["claude/pm-1.log"], "t": ["abc"], "after": ["4"]}
    assert req.headers == {"host": "127.0.0.1:1", "last-event-id": "7"}
    assert Request.parse("GET", "", {}).path == "/"


def test_new_token_is_urlsafe_and_fresh_per_call():
    a, b = new_token(), new_token()
    assert a != b and len(a) >= 43 and set(a) <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


@pytest.mark.parametrize("token, host", [
    (None, "127.0.0.1:8765"),                 # no token
    ("", "127.0.0.1:8765"),                   # empty token
    ("t0k3n-fixed-for-testS", "127.0.0.1:8765"),  # one character off
    (TOKEN, "evil.example:8765"),             # a rebinding host with the right port
    (TOKEN, "127.0.0.1:9999"),                # our host, another port
    (TOKEN, "127.0.0.1"),                     # no port at all
    (TOKEN, ""),                              # no Host header value
])
def test_the_gate_answers_403_with_an_empty_body(tmp_path, token, host):
    v, _, _ = viewer_for(tmp_path)
    resp = get(v, "/api/streams", token=token, host=host)
    assert (resp.status, resp.body) == (403, b"")


@pytest.mark.parametrize("host", LOOPBACK_HOSTS)
def test_every_loopback_host_with_the_served_port_is_admitted(tmp_path, host):
    v, _, _ = viewer_for(tmp_path)
    assert get(v, "/api/streams", host=f"{host}:8765").status == 200


def test_no_cors_header_is_ever_sent_and_the_security_headers_always_are(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    for resp in (get(v, "/api/streams"), get(v, "/api/streams", token=None), get(v, "/nowhere")):
        names = {k.lower() for k, _ in resp.headers}
        assert "access-control-allow-origin" not in names
        assert {"cache-control", "x-content-type-options", "referrer-policy"} <= names


def test_unknown_routes_are_404_and_the_wrong_method_is_405_behind_the_gate(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    assert get(v, "/nowhere").status == 404 and "no route" in body(get(v, "/nowhere"))["error"]
    assert get(v, "/api/action", method="POST").status == 404       # plan 8c
    assert get(v, "/api/config", method="PUT").status == 404        # plan 8d
    assert get(v, "/api/state", method="POST").status == 405
    assert get(v, "/nowhere", token=None).status == 403             # the gate comes first


def test_api_state_carries_the_status_liveness_freezes_disk_and_logs(tmp_path):
    v, ctx, _ = viewer_for(tmp_path)
    home = ctx.store.home
    write_record(supervisor_path(home, "claude"), {"pid": 4242, "started_at": NOW, "proc_start": "T0"})
    write_record(freeze_path(home, "claude", "pm"), {"at": NOW, "posted": True, "resets_at": "2026-09-18T15:00:00Z"})
    (home / "logs" / "claude").mkdir(parents=True)
    (home / "logs" / "claude" / "pm-1.log").write_bytes(b"x" * (3 * 1024 * 1024))

    resp = get(v, "/api/state")
    assert resp.status == 200 and resp.content_type.startswith("application/json")
    state = body(resp)
    assert state["status"]["repo"] == "acme/widgets" and state["status"]["party"] == "human" and "budget" in state["status"]
    assert state["supervisors"]["claude"]["state"] == "alive" and state["supervisors"]["codex"]["state"] == "unknown"
    assert state["frozen"]["claude"]["pm"]["resets_at"] == "2026-09-18T15:00:00Z" and state["frozen"]["claude"]["orchestrator"] is None
    assert state["disk"] == {"free_mb": 3072, "warn_free_mb": ctx.cfg.logs_warn_free_mb}
    assert state["logs"] == {"total_mb": 3, "warn_total_mb": ctx.cfg.logs_warn_total_mb} and state["generated_at"] == NOW


def test_api_state_is_cached_single_flight_for_the_ttl_and_a_failure_is_not(tmp_path, monkeypatch):
    clock = Clock()
    v, ctx, _ = viewer_for(tmp_path, clock=clock, state_ttl=5.0)
    real, calls = server.build_status, []

    def counting(c, now=None):
        calls.append(now)
        if len(calls) == 3:
            raise GitHubError("gh: rate limited")
        return real(c, now=now)

    monkeypatch.setattr(server, "build_status", counting)
    assert get(v, "/api/state").status == 200 and get(v, "/api/state").status == 200 and len(calls) == 1
    clock.t += 5.0
    assert get(v, "/api/state").status == 200 and len(calls) == 2
    clock.t += 5.0
    failed = get(v, "/api/state")
    assert failed.status == 502 and body(failed) == {"error": "gh: rate limited"}
    assert get(v, "/api/state").status == 200 and len(calls) == 4      # the failure was not cached


def test_api_state_reports_an_unreadable_disk_as_null_rather_than_failing(tmp_path):
    def broken(p):
        raise OSError("statvfs")

    v, _, _ = viewer_for(tmp_path, disk_usage=broken)
    assert body(get(v, "/api/state"))["disk"]["free_mb"] is None


def test_api_streams_lists_what_discover_finds(tmp_path):
    v, ctx, _ = viewer_for(tmp_path)
    home = ctx.store.home
    log = home / "logs" / "claude" / "pm-1.log"
    log.parent.mkdir(parents=True)
    log.write_text("hello\n")
    write_record(role_run_path(home, "claude", "pm"),
                 {"pid": 7, "started_at": NOW, "proc_start": "T0", "log_path": str(log), "reason": "start", "launch_no": 1})
    streams = body(get(v, "/api/streams"))["streams"]
    assert [(s["id"], s["kind"], s["liveness"]["state"]) for s in streams] == [("claude/pm-1.log", "role", "alive")]


def test_api_events_streams_a_transcript_and_resumes_from_last_event_id(tmp_path):
    v, ctx, _ = viewer_for(tmp_path, alive=lambda pid: False)     # the writer is confirmed gone: the stream ends
    home = ctx.store.home
    log = home / "logs" / "claude" / "pm-1.log"
    log.parent.mkdir(parents=True)
    log.write_text("one\ntwo\n")
    write_record(role_run_path(home, "claude", "pm"),
                 {"pid": 7, "started_at": NOW, "proc_start": "T0", "log_path": str(log), "reason": "start", "launch_no": 1})

    resp = get(v, "/api/events?stream=claude/pm-1.log")
    assert resp.status == 200 and resp.content_type == "text/event-stream; charset=utf-8" and resp.streaming
    text = b"".join(resp.body).decode()
    assert text.startswith("id: 1\nevent: message\n") and '"text": "two"' in text and text.endswith("event: end\ndata: {}\n\n")
    assert 'event: run_finished' in text and '"synthesized": true' in text

    resumed = b"".join(get(v, "/api/events?stream=claude/pm-1.log", headers={"Last-Event-ID": "1"}).body).decode()
    assert "id: 1\n" not in resumed and resumed.startswith("id: 2\nevent: message\n")
    by_query = b"".join(get(v, "/api/events?stream=claude/pm-1.log&after=2").body).decode()
    assert "event: message" not in by_query and by_query.startswith("id: 3\nevent: run_finished\n")
    junk = b"".join(get(v, "/api/events?stream=claude/pm-1.log", headers={"Last-Event-ID": "seven"}).body).decode()
    assert junk.startswith("id: 1\n")                                    # not an integer: from the start


@pytest.mark.parametrize("stream", ["", "claude/nope.log", "../.meld.toml", "ghost/pm-1.log", "claude"])
def test_api_events_answers_404_for_a_stream_that_is_not_a_log_of_a_configured_group(tmp_path, stream):
    v, ctx, _ = viewer_for(tmp_path)
    (ctx.store.home / "logs" / "ghost").mkdir(parents=True)
    (ctx.store.home / "logs" / "ghost" / "pm-1.log").write_text("x\n")   # exists on disk, but `ghost` is no group
    resp = get(v, f"/api/events?stream={stream}")
    assert resp.status == 404 and "error" in body(resp)
```

- [ ] **Step 2: Run them to verify they fail.**

Run: `uv run pytest tests/test_viewer.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'meld.viewer.server'`.

- [ ] **Step 3: Implement.** Create `src/meld/viewer/server.py`:

```python
"""The loopback viewer's server: the gate in front of every request, and the routes behind it (spec section 22).

Every route is a method taking a plain `Request` and returning a plain `Response`, so the whole surface is driven
in process by the tests; `_Handler` and `serve` (below, once they land) are the only code that touches a socket.
"""
from __future__ import annotations

import hmac
import json
import secrets
import shutil
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from meld.adapters import UnknownAdapter
from meld.assemble import BoardError
from meld.github import GitHubError
from meld.prompts import ROLES
from meld.runners import freeze_path, read_record, supervisor_path
from meld.state import liveness, pid_alive, proc_start_of
from meld.status import build_status
from meld.transcript import total_bytes
from meld.viewer import streams as st

#: The only hosts a request may name (with the served port): what stops a page in the browser reaching the API
#: through a DNS name rebound to 127.0.0.1. The server itself binds 127.0.0.1 alone.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
#: How long one `/api/state` answer is reused: `build_status` is several paginated `gh` calls, and a page polling
#: from two tabs would otherwise spend a real share of the token's rate limit on the same answer.
STATE_TTL_SECONDS = 5.0
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"))
_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime(_ISO_FMT)


def new_token() -> str:
    """A 32-byte URL-safe token, minted once per `meld watch`; it lives in the process and in the printed URL."""
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]          # names lower-cased

    @classmethod
    def parse(cls, method: str, target: str, headers: Mapping[str, str]) -> Request:
        parts = urlsplit(target)
        return cls(method.upper(), parts.path or "/", parse_qs(parts.query, keep_blank_values=True),
                   {k.lower(): v for k, v in headers.items()})

    def first(self, name: str) -> str:
        return (self.query.get(name) or [""])[0]


@dataclass
class Response:
    status: int
    body: bytes | Iterator[bytes] = b""
    content_type: str = "application/json; charset=utf-8"
    headers: tuple[tuple[str, str], ...] = SECURITY_HEADERS

    @property
    def streaming(self) -> bool:
        return not isinstance(self.body, (bytes, bytearray))


def _json(status: int, payload: object) -> Response:
    return Response(status, json.dumps(payload, ensure_ascii=False).encode())


def _empty(status: int) -> Response:
    return Response(status, b"", "text/plain; charset=utf-8")


def _int(text: str) -> int:
    try:
        return max(0, int(text))
    except (TypeError, ValueError):
        return 0


class Viewer:
    """One `meld watch`: its token, its port, and the routes. Reads only; posts nothing; holds no legality logic."""

    ROUTES: dict[str, str] = {"/api/state": "api_state", "/api/streams": "api_streams", "/api/events": "api_events"}

    def __init__(self, ctx, *, token: str, port: int, now: Callable[[], str] = _iso_now, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, alive: Callable[[int], bool] = pid_alive,
                 start_of: Callable[[int], str] = proc_start_of, disk_usage: Callable[[Path], object] = shutil.disk_usage,
                 state_ttl: float = STATE_TTL_SECONDS, interval: float = 1.0, check_every: int = st.CHECK_EVERY,
                 max_polls: int | None = None) -> None:
        self.ctx = ctx
        self.token = token
        self.port = port
        self.now, self.clock, self.sleep = now, clock, sleep
        self.alive, self.start_of, self.disk_usage = alive, start_of, disk_usage
        self.state_ttl, self.interval, self.check_every, self.max_polls = state_ttl, interval, check_every, max_polls
        self._state_cache: tuple[float, Response] | None = None
        self._lock = threading.Lock()

    # ---- the gate ---------------------------------------------------------------------------------------

    def admitted(self, req: Request) -> Response | None:
        """403 with an empty body for a request that is not ours: no token, a wrong one, or a Host that is not
        this loopback server at its port. The token compare is constant-time; nothing else about the refusal
        distinguishes the cases, on purpose."""
        given = req.first("t")
        if not given or not hmac.compare_digest(given.encode(), self.token.encode()):
            return _empty(403)
        if req.headers.get("host", "") not in {f"{h}:{self.port}" for h in LOOPBACK_HOSTS}:
            return _empty(403)
        return None

    def handle(self, req: Request) -> Response:
        refused = self.admitted(req)
        if refused is not None:
            return refused
        name = self.ROUTES.get(req.path)
        if name is None:
            return _json(404, {"error": f"no route {req.path}"})
        if req.method != "GET":
            return _json(405, {"error": f"{req.method} is not allowed on {req.path}"})
        return getattr(self, name)(req)

    # ---- routes -----------------------------------------------------------------------------------------

    def api_state(self, req: Request) -> Response:
        with self._lock:                                  # single-flight: one build at a time, shared by every tab
            cached = self._state_cache
            if cached is not None and self.clock() - cached[0] < self.state_ttl:
                return cached[1]
            resp = self._build_state()
            if resp.status == 200:
                self._state_cache = (self.clock(), resp)
            return resp

    def _build_state(self) -> Response:
        home, cfg = self.ctx.store.home, self.ctx.cfg
        now = datetime.strptime(self.now(), _ISO_FMT).replace(tzinfo=timezone.utc)
        try:
            status, _ = build_status(self.ctx, now=now)
        except (GitHubError, BoardError) as e:
            return _json(502, {"error": str(e)})
        supervisors = {g: liveness(read_record(supervisor_path(home, g)), alive=self.alive, start_of=self.start_of) for g in cfg.groups}
        frozen = {g: {r: read_record(freeze_path(home, g, r)) for r in ROLES} for g in cfg.groups}
        try:
            free_mb: int | None = self.disk_usage(home).free // (1024 * 1024)  # type: ignore[attr-defined]
        except OSError:
            free_mb = None
        return _json(200, {"status": status, "supervisors": supervisors, "frozen": frozen,
                           "disk": {"free_mb": free_mb, "warn_free_mb": cfg.logs_warn_free_mb},
                           "logs": {"total_mb": total_bytes(home / "logs") // (1024 * 1024), "warn_total_mb": cfg.logs_warn_total_mb},
                           "generated_at": self.now()})

    def api_streams(self, req: Request) -> Response:
        return _json(200, {"streams": st.discover(self.ctx.cfg, self.ctx.store.home, alive=self.alive, start_of=self.start_of)})

    def api_events(self, req: Request) -> Response:
        home, cfg = self.ctx.store.home, self.ctx.cfg
        stream = req.first("stream")
        path = st.stream_path(home / "logs", stream)
        group_cfg = cfg.groups.get(stream.partition("/")[0])
        if path is None or group_cfg is None:
            return _json(404, {"error": f"no transcript {stream!r} of a configured group"})
        try:
            parse = self.ctx.adapter_for(group_cfg.cli).parse_events
        except UnknownAdapter as e:
            return _json(502, {"error": str(e)})
        record = st.record_for(cfg, home, path)
        after = _int(req.headers.get("last-event-id") or req.first("after"))
        body = st.events(path, parse, after=after, now=self.now, sleep=self.sleep, interval=self.interval,
                         check_every=self.check_every, max_polls=self.max_polls,
                         writer=lambda: liveness(record, alive=self.alive, start_of=self.start_of))
        return Response(200, body, "text/event-stream; charset=utf-8", SECURITY_HEADERS + (("Connection", "close"),))
```

- [ ] **Step 4: Run the new tests.**

Run: `uv run pytest tests/test_viewer.py -q`
Expected: **24 passed** — 9 plain tests plus 7 gate cases, 3 loopback hosts and 5 bad streams from the parametrized ones.

- [ ] **Step 5: Run the suite.**

Run: `uv run pytest -q`
Expected: **827 passed**, no warnings.

- [ ] **Step 6: Commit.**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/viewer/server.py tests/test_viewer.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: the viewer's gate and its three read routes, driven in process"
```

---

### Task 6: The page — three static files behind the same gate

**Files:**
- Create: `src/meld/viewer/static/app.html`, `src/meld/viewer/static/app.css`, `src/meld/viewer/static/app.js`
- Modify: `src/meld/viewer/server.py` (`ROUTES` gains `/`, `/static/app.css`, `/static/app.js`; three methods; `_static`)
- Test: `tests/test_viewer.py`

**Interfaces:**
- Consumes: `Viewer.ROUTES`, `Response`, `Viewer.token` (Task 5); the three API routes' JSON shapes (Task 5) and SSE frames (Task 4).
- Produces: `GET /` → `text/html; charset=utf-8` with every `__TOKEN__` replaced by the token; `GET /static/app.css` → `text/css; charset=utf-8`; `GET /static/app.js` → `text/javascript; charset=utf-8`. Task 7's socket test fetches `/`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_viewer.py`:

```python
def test_the_page_and_its_assets_are_served_behind_the_gate_with_the_token_substituted(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    page = get(v, "/")
    assert page.status == 200 and page.content_type == "text/html; charset=utf-8"
    html = page.body.decode()
    assert "__TOKEN__" not in html and f"/static/app.css?t={TOKEN}" in html and f"/static/app.js?t={TOKEN}" in html
    assert html.lstrip().lower().startswith("<!doctype html>") and '<meta charset="utf-8">' in html
    assert get(v, "/", token=None).status == 403 and get(v, "/static/app.js", token=None).status == 403
    assert get(v, "/static/app.css").content_type == "text/css; charset=utf-8"
    assert get(v, "/static/app.js").content_type == "text/javascript; charset=utf-8"
    assert get(v, "/", method="POST").status == 405


def test_the_page_references_nothing_off_the_machine_and_renders_text_as_text(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    html, css, js = (get(v, p).body.decode() for p in ("/", "/static/app.css", "/static/app.js"))
    for text in (html, css, js):
        assert "http://" not in text and "https://" not in text and "//cdn" not in text and "@import" not in text
    assert "innerHTML" not in js and "textContent" in js and "EventSource" in js
    for route in ("/api/state", "/api/streams", "/api/events"):
        assert route in js
    assert 'name="referrer" content="no-referrer"' in html
```

- [ ] **Step 2: Run them to verify they fail.**

Run: `uv run pytest tests/test_viewer.py -q -k "page"`
Expected: FAIL — `assert 404 == 200` (no route for `/`).

- [ ] **Step 3: Create `src/meld/viewer/static/app.html`:**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>meld watch</title>
<link rel="stylesheet" href="/static/app.css?t=__TOKEN__">
</head>
<body>
<header id="top">
  <div id="board" class="row">
    <span id="repo" class="strong">loading…</span>
    <span id="milestone"></span>
    <span id="phase" class="badge"></span>
    <span id="halted" class="badge bad hidden">HALTED</span>
    <span id="budget"></span>
  </div>
  <div id="machine" class="row">
    <span id="disk"></span>
    <span id="logs"></span>
    <span id="stamp" class="dim"></span>
    <span id="error" class="bad"></span>
  </div>
  <div id="supervisors" class="row"></div>
  <div id="needs-human" class="row"></div>
</header>
<main id="streams"></main>
<template id="column">
  <section class="column">
    <h2><span class="who"></span> <span class="live badge"></span> <span class="why dim"></span></h2>
    <ol class="events"></ol>
  </section>
</template>
<script src="/static/app.js?t=__TOKEN__"></script>
</body>
</html>
```

- [ ] **Step 4: Create `src/meld/viewer/static/app.css`:**

```css
:root {
  --bg: #0f1115; --panel: #171a21; --line: #2a2f3a; --fg: #d6dae2; --dim: #8a92a3;
  --ok: #3fb950; --bad: #f85149; --warn: #d29922; --unknown: #8b949e; --accent: #58a6ff;
  --message: #d6dae2; --thinking: #a5a9b8; --tool: #79c0ff; --result: #8ddb8c; --usage: #c9a6ff; --raw: #b0a070;
}
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; background: var(--bg); color: var(--fg);
  font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
#top { position: sticky; top: 0; padding: 8px 12px; background: var(--panel); border-bottom: 1px solid var(--line); z-index: 1; }
.row { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: baseline; margin: 2px 0; }
.strong { font-weight: 600; }
.dim { color: var(--dim); }
.bad { color: var(--bad); }
.hidden { display: none; }
.badge { border: 1px solid var(--line); border-radius: 3px; padding: 0 6px; }
.badge.alive { color: var(--ok); border-color: var(--ok); }
.badge.dead { color: var(--bad); border-color: var(--bad); }
.badge.unknown { color: var(--unknown); border-color: var(--unknown); }
.badge.frozen { color: var(--warn); border-color: var(--warn); }
#streams { display: flex; gap: 8px; padding: 8px; overflow-x: auto; height: calc(100vh - 110px); }
.column { flex: 0 0 34rem; max-width: 46rem; display: flex; flex-direction: column; background: var(--panel);
  border: 1px solid var(--line); border-radius: 4px; min-height: 0; }
.column.ended { opacity: 0.75; }
.column.reconnecting h2 { border-bottom-color: var(--warn); }
.column h2 { margin: 0; padding: 6px 10px; font-size: 13px; border-bottom: 1px solid var(--line); }
.events { list-style: none; margin: 0; padding: 4px 0; overflow-y: auto; flex: 1 1 auto; min-height: 0; }
.events li { display: grid; grid-template-columns: 5.5rem 5rem 1fr; gap: 8px; padding: 2px 10px; white-space: pre-wrap;
  word-break: break-word; border-bottom: 1px solid transparent; }
.events li:hover { background: #1d212b; }
.events .at { color: var(--dim); }
.events .kind { text-transform: lowercase; }
li.message .text { color: var(--message); }
li.thinking .text { color: var(--thinking); font-style: italic; }
li.tool_call .kind, li.tool_call .text { color: var(--tool); }
li.tool_result .kind { color: var(--result); }
li.tool_result.failed .kind, li.tool_result.failed .text, li.cli_error .text, li.usage_limit .text { color: var(--bad); }
li.usage .text, li.run_started .text, li.run_finished .text { color: var(--usage); }
li.log_truncated .text { color: var(--warn); }
li.raw .text { color: var(--raw); }
li.synthesized .text { color: var(--dim); font-style: italic; }
```

- [ ] **Step 5: Create `src/meld/viewer/static/app.js`:**

```javascript
"use strict";
// The viewer's one page: state and streams polled from the API, one column per transcript fed by EventSource.
// Every string shown here came from a model or a tool, so it only ever reaches the document as a text node.
const TOKEN = new URLSearchParams(location.search).get("t") || "";
const KINDS = ["run_started", "message", "thinking", "tool_call", "tool_result", "usage", "run_finished",
               "usage_limit", "log_truncated", "cli_error", "raw"];
const STATE_EVERY_MS = 10000;
const STREAMS_EVERY_MS = 5000;
const columns = new Map();   // stream id -> {root, list, source}

function api(path, params) {
  const url = new URL(path, location.origin);
  url.searchParams.set("t", TOKEN);
  for (const [k, v] of Object.entries(params || {})) url.searchParams.set(k, v);
  return url;
}

const el = (id) => document.getElementById(id);

function text(node, value) { node.textContent = value == null ? "" : String(value); return node; }

function badge(node, state, label) {
  node.className = "badge " + state;
  return text(node, label || state);
}

async function getJSON(path) {
  const response = await fetch(api(path), { cache: "no-store" });
  if (!response.ok) {
    let detail = "";
    try { detail = (await response.json()).error || ""; } catch (e) { detail = ""; }
    throw new Error(`${path}: ${response.status}${detail ? " " + detail : ""}`);
  }
  return response.json();
}

function renderState(state) {
  const s = state.status;
  text(el("repo"), s.repo);
  text(el("milestone"), s.milestone ? `milestone ${s.milestone}` : "");
  badge(el("phase"), "", s.phase || "");
  el("halted").classList.toggle("hidden", !s.halted);
  const spent = Object.entries(s.budget.spent || {}).map(([k, v]) => `${k} ${v}`).join(", ");
  text(el("budget"), spent ? `spent: ${spent}` : "");
  const disk = state.disk.free_mb == null ? "disk: unreadable" : `disk: ${state.disk.free_mb} MB free`;
  text(el("disk"), disk).classList.toggle("bad", state.disk.free_mb != null && state.disk.free_mb < state.disk.warn_free_mb);
  text(el("logs"), `logs: ${state.logs.total_mb} MB`).classList.toggle("bad", state.logs.total_mb > state.logs.warn_total_mb);
  text(el("stamp"), `as of ${state.generated_at}`);
  text(el("error"), "");

  const sup = el("supervisors");
  sup.replaceChildren();
  for (const [group, live] of Object.entries(state.supervisors)) {
    const span = document.createElement("span");
    const reason = text(document.createElement("span"), ` ${live.reason}`);
    reason.className = "dim";
    span.append(text(document.createElement("span"), `${group}: `), badge(document.createElement("span"), live.state), reason);
    for (const [role, freeze] of Object.entries(state.frozen[group] || {})) {
      if (freeze) {
        const f = badge(document.createElement("span"), "frozen", `${role} frozen`);
        f.title = freeze.resets_at ? `resets ${freeze.resets_at}` : `since ${freeze.at}, re-probing`;
        span.append(" ", f);
      }
    }
    sup.append(span);
  }
  const nh = el("needs-human");
  nh.replaceChildren();
  for (const item of s.needs_human || []) {
    const line = text(document.createElement("span"), `needs human: #${item.issue} ${item.title} (${item.age_minutes}m)`);
    line.className = "bad";
    nh.append(line);
  }
}

function cell(className, value) {
  const span = text(document.createElement("span"), value);
  span.className = className;
  return span;
}

function appendEvent(col, ev) {
  const li = document.createElement("li");
  li.className = ev.kind + (ev.ok === false ? " failed" : "") + (ev.meta && ev.meta.synthesized ? " synthesized" : "");
  li.append(cell("at", (ev.at || "").slice(11, 19)),
            cell("kind", ev.tool ? `${ev.kind} ${ev.tool}` : ev.kind),
            cell("text", ev.text));
  const list = col.list;
  const pinned = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
  list.append(li);
  if (pinned) list.scrollTop = list.scrollHeight;
}

function openStream(col, id) {
  // EventSource reconnects by itself and sends Last-Event-ID, so resumption needs nothing here.
  const source = new EventSource(api("/api/events", { stream: id }));
  for (const kind of KINDS) source.addEventListener(kind, (e) => appendEvent(col, JSON.parse(e.data)));
  source.addEventListener("end", () => { col.root.classList.add("ended"); source.close(); });
  source.onerror = () => col.root.classList.add("reconnecting");
  source.onopen = () => col.root.classList.remove("reconnecting");
  col.source = source;
}

function renderStreams(list) {
  const seen = new Set();
  for (const s of list) {
    seen.add(s.id);
    let col = columns.get(s.id);
    if (!col) {
      const root = el("column").content.firstElementChild.cloneNode(true);
      col = { root, list: root.querySelector(".events"), source: null };
      const who = s.kind === "role" ? `${s.group} / ${s.role}` : `${s.group} / ${s.kind} #${s.issue}`;
      text(root.querySelector(".who"), who);
      el("streams").append(root);
      columns.set(s.id, col);
      openStream(col, s.id);
    }
    badge(col.root.querySelector(".live"), s.liveness.state);
    text(col.root.querySelector(".why"), s.liveness.reason);
  }
  for (const [id, col] of columns) {
    if (!seen.has(id)) { if (col.source) col.source.close(); col.root.remove(); columns.delete(id); }
  }
}

async function pollState() {
  try { renderState(await getJSON("/api/state")); } catch (e) { text(el("error"), e.message); }
  setTimeout(pollState, STATE_EVERY_MS);
}

async function pollStreams() {
  try { renderStreams((await getJSON("/api/streams")).streams); } catch (e) { text(el("error"), e.message); }
  setTimeout(pollStreams, STREAMS_EVERY_MS);
}

pollState();
pollStreams();
```

- [ ] **Step 6: Wire the routes.** In `src/meld/viewer/server.py` add `from importlib import resources` to the imports, extend `ROUTES`:

```python
    ROUTES: dict[str, str] = {"/": "page", "/static/app.css": "asset_css", "/static/app.js": "asset_js",
                              "/api/state": "api_state", "/api/streams": "api_streams", "/api/events": "api_events"}
```

add a module-level helper after `_int`:

```python
def _static(name: str) -> str:
    return resources.files("meld.viewer").joinpath("static", name).read_text(encoding="utf-8")
```

and three methods on `Viewer`, before `api_state`:

```python
    def page(self, req: Request) -> Response:
        # The stylesheet and script are fetched through the same gate as everything else, so the page carries the
        # token in their URLs; it is already in the URL the person pasted, so this discloses nothing new.
        return Response(200, _static("app.html").replace("__TOKEN__", self.token).encode(), "text/html; charset=utf-8")

    def asset_css(self, req: Request) -> Response:
        return Response(200, _static("app.css").encode(), "text/css; charset=utf-8")

    def asset_js(self, req: Request) -> Response:
        return Response(200, _static("app.js").encode(), "text/javascript; charset=utf-8")
```

- [ ] **Step 7: Run the suite.**

Run: `uv run pytest -q`
Expected: **829 passed**, no warnings.

- [ ] **Step 8: Commit.**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/viewer/static src/meld/viewer/server.py tests/test_viewer.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: the viewer's page, one column per transcript, served behind the gate"
```

---

### Task 7: `serve` over a socket, and `meld watch`

**Files:**
- Modify: `src/meld/viewer/server.py` (`_Handler`, `serve`, `url`)
- Modify: `src/meld/cli.py` (imports, `SERVE`, the `watch` command after `tail`)
- Test: `tests/test_viewer.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `Viewer`, `Request`, `Response`, `new_token` (Tasks 5–6); `Config.viewer_port`, `Config.viewer_open_browser` (Task 1).
- Produces: `serve(viewer, *, host="127.0.0.1", port=0) -> ThreadingHTTPServer` (binds, sets `viewer.port` to the bound port, attaches `server.viewer`); `url(server, token) -> str`; `meld watch [--port N] [--open/--no-open]`; `cli.SERVE` seam.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_viewer.py` (add `import http.client`, `import threading` and extend the server import with `serve, url`):

```python
def test_serve_binds_an_os_assigned_loopback_port_and_answers_over_a_socket(tmp_path):
    """The one socket test: the wiring from bytes on a connection to `Viewer.handle` and back, including a
    streaming body and the silenced request log."""
    v, ctx, _ = viewer_for(tmp_path, alive=lambda pid: False)
    log = ctx.store.home / "logs" / "claude" / "pm-1.log"
    log.parent.mkdir(parents=True)
    log.write_text("hello\n")
    httpd = serve(v, port=0)
    try:
        port = httpd.server_address[1]
        assert httpd.server_address[0] == "127.0.0.1" and port > 0 and v.port == port
        assert url(httpd, v.token) == f"http://127.0.0.1:{port}/?t={TOKEN}"
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        def fetch(target, headers=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("GET", target, headers=headers or {})
            resp = conn.getresponse()
            data = resp.read()
            conn.close()
            return resp, data

        resp, data = fetch(f"/api/streams?t={TOKEN}")
        assert resp.status == 200 and resp.getheader("Content-Type") == "application/json; charset=utf-8"
        assert json.loads(data)["streams"][0]["id"] == "claude/pm-1.log" and resp.getheader("Content-Length") == str(len(data))
        resp, data = fetch("/api/streams")
        assert (resp.status, data) == (403, b"")
        resp, data = fetch(f"/?t={TOKEN}")
        assert resp.status == 200 and data.lstrip().lower().startswith(b"<!doctype html>")
        resp, data = fetch(f"/api/events?stream=claude/pm-1.log&t={TOKEN}")
        assert resp.status == 200 and resp.getheader("Content-Type") == "text/event-stream; charset=utf-8"
        assert data.startswith(b"id: 1\nevent: message\n") and data.endswith(b"event: end\ndata: {}\n\n")
        resp, data = fetch(f"/api/streams?t={TOKEN}", headers={"Host": f"evil.example:{port}"})
        assert (resp.status, data) == (403, b"")
    finally:
        httpd.shutdown()
        httpd.server_close()
```

Append to `tests/test_cli.py` (add `from tests.conftest import env_for, load_board1_routes` and `from tests.fakegh import FakeGh` to its imports):

```python
class _ServerStandIn:
    server_address = ("127.0.0.1", 4321)

    def __init__(self):
        self.served = self.closed = False

    def serve_forever(self):
        self.served = True

    def server_close(self):
        self.closed = True


def _watch(tmp_path, monkeypatch, args, env_extra=None):
    config = tmp_path / ".meld.toml"
    config.write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    monkeypatch.setattr(cli, "RUN", fake)
    seen = {}

    def fake_serve(viewer, *, host="127.0.0.1", port=0):
        seen["viewer"], seen["port"], seen["server"] = viewer, port, _ServerStandIn()
        viewer.port = 4321
        return seen["server"]

    monkeypatch.setattr(cli, "SERVE", fake_serve)
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda link: opened.append(link) or True)
    env = {**env_for(tmp_path, group=None), **(env_extra or {})}
    result = CliRunner().invoke(main, ["--config", str(config), "watch", *args], env=env)
    return result, seen, opened


def test_watch_prints_exactly_the_url_with_a_fresh_token_and_serves_until_stopped(tmp_path, monkeypatch):
    result, seen, opened = _watch(tmp_path, monkeypatch, [])
    assert result.exit_code == 0, result.output
    token = seen["viewer"].token
    assert len(token) >= 43 and result.output == f"http://127.0.0.1:4321/?t={token}\n"
    assert seen["port"] == 0                         # [viewer] port's default: the OS chooses
    assert seen["server"].served and seen["server"].closed and opened == []


def test_watch_honours_port_and_open_flags(tmp_path, monkeypatch):
    result, seen, opened = _watch(tmp_path, monkeypatch, ["--port", "4321", "--open"])
    assert result.exit_code == 0, result.output
    assert seen["port"] == 4321 and opened == [result.output.strip()]


def test_watch_is_refused_to_a_worker_like_every_other_verb(tmp_path, monkeypatch):
    result, seen, _ = _watch(tmp_path, monkeypatch, [], env_extra={"MELD_WORKER": "1"})
    assert result.exit_code != 0 and "viewer" not in seen
```

- [ ] **Step 2: Run them to verify they fail.**

Run: `uv run pytest tests/test_viewer.py tests/test_cli.py -q -k "serve or watch"`
Expected: FAIL — `ImportError: cannot import name 'serve' from 'meld.viewer.server'`, and the CLI tests fail on `AttributeError: module 'meld.cli' has no attribute 'SERVE'`.

- [ ] **Step 3: Implement the HTTP wiring.** In `src/meld/viewer/server.py` add `from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer` to the imports and append at the end of the module:

```python
class _Handler(BaseHTTPRequestHandler):
    """Bytes on a connection in, `Viewer.handle` out. HTTP/1.0 on purpose: every response closes its connection,
    so a streaming body needs no chunked encoding and an SSE client simply reconnects when the stream ends."""

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        resp = self.server.viewer.handle(Request.parse(self.command, self.path, self.headers))  # type: ignore[attr-defined]
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.content_type)
        for name, value in resp.headers:
            self.send_header(name, value)
        if not resp.streaming:
            self.send_header("Content-Length", str(len(resp.body)))
        self.end_headers()
        try:
            if resp.streaming:
                for chunk in resp.body:
                    self.wfile.write(chunk)
                    self.wfile.flush()
            else:
                self.wfile.write(resp.body)
        except (BrokenPipeError, ConnectionResetError):
            pass                                        # the tab was closed; nothing to do

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Silenced: the default prints the request line, token included, to stderr."""


def serve(viewer: Viewer, *, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Bind the viewer to loopback -- `port` 0 lets the OS choose -- and tell it the port it got, which its
    `Host` check needs. The caller runs `serve_forever`."""
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.daemon_threads = True
    httpd.viewer = viewer  # type: ignore[attr-defined]
    viewer.port = httpd.server_address[1]
    return httpd


def url(httpd: ThreadingHTTPServer, token: str) -> str:
    return f"http://127.0.0.1:{httpd.server_address[1]}/?t={token}"
```

- [ ] **Step 4: Implement `meld watch`.** In `src/meld/cli.py` add `import webbrowser` to the standard-library imports and `from meld.viewer.server import Viewer, new_token, serve, url` to the meld imports; after `SLEEP = time.sleep  # ...` add:

```python
SERVE = serve  # tests monkeypatch this so no test binds a port through the CLI
```

and after the `tail` command add:

```python
@main.command()
@click.option("--port", type=int, default=None, help="Loopback port to bind (default: [viewer] port; 0 lets the OS choose).")
@click.option("--open/--no-open", "open_browser", default=None,
              help="Open the page in the default browser (default: [viewer] open_browser).")
@click.pass_context
def watch(ctx: click.Context, port: int | None, open_browser: bool | None) -> None:
    """Serve the local viewer and print its URL; Ctrl-C stops it.

    Binds 127.0.0.1 with a per-launch token in the URL. Reads only -- transcripts, board, state -- and posts
    nothing; nothing in Meld depends on it running, and every verb it will offer already exists on the CLI.
    """
    c = _load(ctx)                                    # the human's context, and load_context's worker refusal
    viewer = Viewer(c, token=new_token(), port=0)
    httpd = SERVE(viewer, port=port if port is not None else c.cfg.viewer_port)
    link = url(httpd, viewer.token)
    click.echo(link)                                  # the one line this prints to stdout
    wants_browser = c.cfg.viewer_open_browser if open_browser is None else open_browser
    if wants_browser:
        webbrowser.open(link)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
```

- [ ] **Step 5: Run the suite.**

Run: `uv run pytest -q`
Expected: **833 passed**, no warnings. The socket test must finish in well under a second; if it hangs, the streaming response is not ending — check that `Viewer.max_polls` from `viewer_for` (1) reached `st.events`.

- [ ] **Step 6: Try it once by hand (no CLI is launched; this reads the local `$MELD_HOME`).** From `/Users/scottbrant/code/meld-playground` if it still has a `.meld.toml`, else skip: `PYTHONPATH=/Users/scottbrant/code/meld-main/src uv run --project /Users/scottbrant/code/meld-main meld watch`, open the printed URL, confirm the header renders and the columns show the last live run's logs (their writers long dead: every badge `dead` or `unknown`, every column `ended` with a synthesized end where the CLI wrote none), then Ctrl-C. Record what you saw in the report; do not fix anything you notice here in this task — note it for the final review.

- [ ] **Step 7: Commit.**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/viewer/server.py src/meld/cli.py tests/test_viewer.py tests/test_cli.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: meld watch serves the viewer on loopback and prints its URL"
```

---

### Task 8: The handoff and the README describe the viewer

**Files:**
- Modify: `docs/HANDOFF.md` (the banner; the repository map; a new "Plan 8b — the loopback viewer (landed)" section before "## Plan 9 — run reliability (landed)"; the "Still open" sentence in the plan 8a section; the test count)
- Modify: `README.md` (the verbs table; a "Watching a run" paragraph under "## Running a group"; Quick start step 8)

**Interfaces:** none; documentation of Tasks 1–7 as landed.

- [ ] **Step 1: Rewrite the banner.** Replace the paragraph beginning `**Milestone:** plan 8a — normalized transcripts` with (keep everything after it):

```markdown
**Milestone:** plan 9 — run reliability (a usage limit freezes the role and posts `LIMITED`, a launch that cannot start backs off, an identical refusal is announced once, `meld doctor` warns on free disk) — is the last plan merged to `main`, as PR #22 (https://github.com/smidley/meld/pull/22, merge commit `93be370`). Plans 1–7, 8a and 9 are merged; `uv run pytest -q` must be green with no warnings before every commit (**<COUNT> passed** as this branch stands — the number Task 7's suite run printed). **Plan 8b (the loopback viewer, `meld watch`) is this branch, `plan-8b-viewer`**, and is the subject of the "Plan 8b" section below.
```

Use the real count from Task 7's suite run in place of `<COUNT>`.

- [ ] **Step 2: Extend the repository map.** After the `adapters/` bullet add:

```markdown
- `viewer/` — `meld watch` (plan 8b). `server.py`: `Request`/`Response`, the `Viewer` whose `handle` is the whole surface (a per-launch `secrets.token_urlsafe(32)` in `?t=` on **every** request including the page and its assets, compared constant-time; `Host` must be `127.0.0.1|localhost|[::1]:<served port>`; both refusals are a bare 403; no CORS header ever; `Cache-Control: no-store`, `nosniff`, `Referrer-Policy: no-referrer` on everything), the three read routes (`/api/state` — `build_status` plus supervisor liveness, each role's `limit.json`, free disk and log bytes, cached five seconds single-flight, a `GitHubError`/`BoardError` answered as 502 and never cached; `/api/streams`; `/api/events`), the page routes, and `serve`/`_Handler` (HTTP/1.0 so a streaming body needs no chunking; the request log is silenced because the default prints the token). `streams.py`: `stream_path` (only `<group>/<file>.log` under `logs/`), `discover` (each role's recorded launch else its newest log as `unknown`, then every worker run on record; runs `sweep_logs` — the viewer's turn of the log cap), `record_for`, and `events` (SSE over `Tail`: replay from `Last-Event-ID`/`after=` by count, a shortfall is a trim and replays from the start after one `log_truncated`; the writer is checked every `CHECK_EVERY` idle polls, `dead` flushes, synthesizes `run_finished` when the transcript has no terminal event, and closes with `end`). `static/app.html|css|js`: the page, one column per transcript, `textContent` only, `EventSource` reconnects by itself.
```

And in the `cli.py` bullet change `the 27 commands` to `the 28 commands` (add `watch`).

- [ ] **Step 3: Add the plan 8b section.** Before `## Plan 9 — run reliability (landed)` insert:

```markdown
## Plan 8b — the loopback viewer (landed)

`meld watch` serves one page on `127.0.0.1` and prints its URL, token included; it is a tool a person runs, not a service the framework depends on: no role may start it, nothing reads from it, and it holds no legality logic (section 22). This plan is the read-only half of the viewer. `/api/action` (plan 8c, the human verbs from the page) and `/api/config` (plan 8d, the `.meld.toml` form) answer 404 until they land.

**Liveness is a tri-state with evidence.** The first live run announced a dead audit worker as running because `kill(pid, 0)` succeeded on a reused pid. Now `state.liveness` answers `alive` only for a live pid whose process start time matches the recorded one, `dead` for a pid that is gone or one whose start time differs (reused), and `unknown` for anything it cannot settle — no record, no pid, no recorded start time, or a start time `ps` could not read — each with its reason. The evidence is written by the processes themselves: `run_group` records `state/<group>/supervisor.json` (`pid`, `started_at`, `proc_start`) at startup and `state/<group>/<role>/current.json` (plus `log_path`, `reason`, `launch_no`) on every successful launch, and every worker `RunRecord` carries `proc_start`. `proc_start` is `ps -o lstart=`'s own string, compared for equality and never parsed. **Records are never deleted** (a dead pid in one is "confirmed gone", which is more than "no record" can say), and a record that cannot be written is swallowed (`runners._note`): a launch that succeeded must reach `procs` whatever happened to its record, or the next cycle would launch the role twice.

**The stream is plan 8a's `Tail`, framed.** A stream is a log under `$MELD_HOME/logs`, named `<group>/<file>.log`; `stream_path` accepts nothing else, so a request cannot reach outside that directory. Resume is by count (`Last-Event-ID`, which the browser sends on its own reconnects, else `after=`); a client that has seen more events than the file now holds was reading a log that has since been trimmed, and gets one `log_truncated` frame then the whole file. When the writer is confirmed dead and the transcript, flushed, does not end in `run_finished`/`usage_limit`/`cli_error`, the stream synthesizes one `run_finished` marked `synthesized` (a Codex transcript ends at `usage`; section 22's table leaves the terminal event to the reader) and closes with `end`. While the writer is alive or unknown the stream stays open on a comment heartbeat and checks the writer every six idle polls, because the check may run `ps`.

**Decisions for the owner (each reversible):**
1. The token rides in the query string on every request, the page's own stylesheet and script included (the HTML carries it in their URLs). `EventSource` cannot set headers, so there is no header alternative for the stream anyway.
2. `/api/state` is cached five seconds, single-flight, and also carries each role's `limit.json` — plan 9's follow-up said a frozen role was visible only in `meld run`'s report.
3. `[viewer] port` defaults to 0 (the OS chooses) and `open_browser` to false; `--port`/`--open` override per launch.
4. The page is tested only for what Python can see (served behind the gate, no external reference, `textContent` not `innerHTML`, only real routes named). Rendering is not unit-tested: no JavaScript runtime is in the toolchain.
5. A stream whose writer is `unknown` never ends on its own; the person closes the tab.

**Known follow-ups:** `discover` and `record_for` read every role record and run record per call (fine at two groups); `liveness` runs `ps` once per check for a live pid, so a page with many live columns runs a few `ps` a second — cache per pid if it ever shows; the `[::1]` host is accepted but the server binds IPv4 only; worker records written before this plan carry no `proc_start` and read `unknown` while alive.
```

- [ ] **Step 4: Update the plan 8a section's "Still open" sentence.** Replace `**Still open, in order:** plan 8b (the loopback viewer, read-only — \`meld watch\` serves the page, nothing reads from it, and every verb it offers already exists on the CLI), 8c (the human verbs reachable from the page), and 8d (the \`.meld.toml\` form for editing config from the viewer).` with `**Still open, in order:** plan 8c (the human verbs reachable from the page — \`/api/action\`, which the viewer answers 404 for today) and 8d (the \`.meld.toml\` form — \`/api/config\`, likewise 404). Plan 8b landed on this branch; see its section below.` Keep the rest of that paragraph.

- [ ] **Step 5: README.** In the verbs table, after the `meld tail` row add:

```markdown
| `meld watch [--port N] [--open/--no-open]` | Serve the local viewer on `127.0.0.1` and print its URL (a per-launch token is in it): every live role's and worker's transcript in its own column, the board, each supervisor's liveness (`alive`/`dead`/`unknown`, with the reason), each role's standing freeze, budget, free disk and log bytes. Reads local files and the board; posts nothing; Ctrl-C stops it |
```

Under `## Running a group`, at the end of that section, add:

```markdown
**Watching a run.** `meld watch` (from the product clone, as the human) prints a `http://127.0.0.1:<port>/?t=<token>` URL; open it. The token is minted per launch and required on every request, so paste the whole URL. Nothing in Meld depends on the viewer running, and `meld tail <group>/<role>` remains the terminal view for a headless box.
```

Add Quick start step 8: `8. \`meld watch\`, and open the URL it prints, to watch the roles and workers as they run.`

- [ ] **Step 6: Run the suite once more and commit.**

Run: `uv run pytest -q`
Expected: the same count as Task 7, no warnings (no test reads the docs).

```bash
git -C /Users/scottbrant/code/meld-main add docs/HANDOFF.md README.md
git -C /Users/scottbrant/code/meld-main commit -m "docs: the handoff and README describe meld watch and the liveness records"
```

---

## Self-review

**Spec coverage.** §22 "The viewer": `meld watch` prints its URL, a tool a person runs (Task 7); routes `/`, `/api/state`, `/api/streams`, `/api/events` (Tasks 5–6), `/api/action` and `/api/config` deferred to 8c/8d and named so in the spec (Task 1); binding `127.0.0.1` on `[viewer] port` else OS-assigned, the 32-byte URL-safe token per launch printed in the URL and required on every request with 403 and an empty body, the `Host` loopback-and-port check, no CORS header, the token never on disk (Tasks 5, 7, and Decision 10's silenced logger); reading is unrestricted within `$MELD_HOME` and the board (Task 5); "what it may not do" — nothing in this plan starts, stops, kills, edits or posts (Global Constraints); liveness tri-state with reasons and pids paired with start times (Tasks 2–3, 5); the reader-side terminal event for Codex (Task 4, Decision 9); `trim_log` from the viewer's poll (Task 4's `sweep_logs` in `discover`). §22 "Testing": token, `Host`, routing and SSE resumption in `test_viewer.py` (Tasks 5–7); mutating-route refusals are 8c's. §4: `streams.py` in the layout (Task 1); `watch` already in the verb list. §5 `[viewer]` (Task 1). §19 (Task 1). §21: no curses view, no token-level streaming (whole events only), no supervisor control, no guided setup — none built. **Gap found and closed:** `Config.viewer_open_browser` exists in §5 but the first draft of Task 7 read only `--open`; the command now defaults from the config.

**Placeholder scan.** No TBD/TODO; every code step carries its code; Task 5's Step 4 asks the implementer to read pytest's own count rather than trusting mine, and Task 8's banner takes that number — that is deliberate, not a placeholder.

**Type consistency.** `liveness(record, *, alive, start_of)` — same keyword names in `state.py` (Task 2), `streams.discover`/`record_for`/`events`' `writer` lambda (Task 4) and `Viewer` (Task 5). `run_group(start_of=)` (Task 3) matches `proc_start_of`'s signature `(pid) -> str`. `events(path, parse, *, after, now, writer, sleep, interval, check_every, max_polls)` is called with exactly those keywords in Task 5's `api_events` and Task 4's tests. `Response.content_type` for JSON is `application/json; charset=utf-8` in `_json` and asserted so in Task 7's socket test. `stream_path(logs_root, stream)` and `stream_id(logs_root, path)` argument order is the same in Task 4's code and tests. `Viewer.ROUTES` is a class attribute replaced whole in Task 6 (six entries), not appended to. `read_freeze`/`write_freeze` remain importable names (aliases) so plan 9's tests and `runners._frozen`/`_freeze` are untouched.

## Corrections made during execution

Ruling R12: the task blocks above stay exactly as written — they are the record of what was planned. This section records what execution actually changed, one short entry per item, in the order it happened: the task, what the plan said, what landed instead, and why.

1. **Task 3** — the 0o500-permission test's `state_dir.mkdir(parents=True)` gained `exist_ok=True`. `load_context` may already have created `state/claude` before the test tries to, and the plan's version did not account for that.
2. **Task 4** — the brief's test block holds nine tests, not ten as the plan's running commentary assumed; every downstream "Expected … passed" count was off by one, and then by four once later tasks compounded it. The landed counts are: Task 1 779, Task 2 790, Task 3 793, Task 4 802 (805 after its own fix round below), Task 5 829, Task 6 831, Task 7 835.
3. **Task 4** — review found two Important defects in the plan's own code, fixed in commits `3637f69` and `bd7ca03`. `stream_path` now wraps `is_file()`/`resolve()` in `try/except OSError → None` and requires `path.resolve().is_relative_to(logs_root.resolve())`, because an over-long stream name raised `OSError` instead of answering `None`, and a symlink under `logs/` could resolve outside it. The synthesized `run_finished` frame carries **no `id:` line** (`frame(event, *, with_id=True)`): a synthesized event is not a position in the file, and giving it `id: n+1` made the resume rule report a false trim on a reconnect after `end`. `events()` clamps `after = max(0, after)`, so a malformed negative `Last-Event-ID` replays from the start instead of misbehaving.
4. **Task 5** — two test literals the plan wrote wrong: `render_status` labels the human party `"[human]"` (the header form), not `"human"`; and, following from item 3's `with_id=False`, the resumption test asserts `by_query.startswith("event: run_finished\n")` rather than `"id: 3\nevent: run_finished\n"`.
5. **Task 6** — review found a Critical defect in the plan's `app.js`, fixed in `d03d121`: `badge()` assigned `className` wholesale, which destroyed the static `.live` hook the next poll re-queried — the streams pane threw and froze after five seconds. It now uses `classList` over `BADGE_STATES`, and `renderStreams` caches the `.live`/`.why` nodes at column creation instead of re-querying them. The same review found that opening one `EventSource` per stream with no cap would exhaust a browser's six connections per origin once a run had more than a few live transcripts; the page now keeps at most `MAX_SOURCES = 4` open at a time, preferring alive, then dead, then unknown streams, with a `waiting` badge for the rest.
6. **Task 7** — the socket test omitted the `write_record(role_run_path(...), …)` call for the pm stream; without a record, `events()`'s `writer` lambda had nothing to report on, and the SSE stream never ended.
7. **Task 8** — this section exists instead of in-place rewrites of the plan's affected blocks (ruling R12).
8. **Final whole-branch review** — its fix wave (commits `6852555`, `a7500d9`, `b849939`, `8564edd`, `4686e09`, `a8ea253`, `43506a2`, `eb9b05a`, `170f371`) flattened the page's budget line, deduped `StateStore.errors`, restarted a stream's numbering on a trim under an open connection (spec §22 amended), caught `OSError` in `/api/state`'s 502 branch and in `meld watch`'s bind, replaced a fixed sleep with `_wait_for` in the relaunch-record test, removed dead imports, and corrected HANDOFF:18 and the four-source-cap prose (rulings R14–R19). The suite goes from 835 to **839 passed**, no warnings.
