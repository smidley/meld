# Plan 9: run reliability — a usage limit freezes, a full disk backs off, a stale refusal stays quiet

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the three failures that turned the first live run into a firefight, all three still open as `docs/HANDOFF.md` residuals 3, 4 and 14: a usage limit read as an ordinary crash and relaunched silently for 45 minutes, an `OSError` opening a log file killing both group supervisors mid-milestone when the disk filled, and the same refusal re-derived on every launch until 109 protocol violations sat on the coordination issue.

**Architecture:** Each fix lands at exactly one site, and each is small. Plan 8a is what makes the first one possible: a role that exhausts its quota does not crash in any way an exit code distinguishes from a clean finish, so the runner has to read what the role *said* — which it can now do, through `Tail` and the adapter's own `parse_events`. So the adapters gain one more recognition (`usage_limit`, with the reset instant when the CLI named one), `runners.run_group` gains a freeze that a persisted file survives a restart, `run_group`'s launch path catches its own `DispatchError` instead of propagating it out of `meld run`, and `actions.refuse` reads the board it already holds to see whether the comment it is about to post is one it already posted.

**Tech Stack:** Python 3.13, standard library plus `click` only. No new dependency. `FakeGh` and injected clocks in tests; no network, no real CLI, no sleeping.

**Spec:** `docs/superpowers/specs/2026-09-03-meld-design.md` — §7 (`LIMITED` in the liveness verb row), §17 (the three new rows, one per residual), §22 (the `usage_limit` kind and who recognises it), §5 (`[runners] limit_retry_minutes`, `[logs] warn_free_mb`), §19 (what the new suites prove). Those amendments are commit `73c831c`, the first commit on this branch; this plan implements them and adds nothing they do not say.

## Global Constraints

- Python `>=3.12` (3.13 locally); runtime dependency `click>=8.1` only, inside `src/meld/`.
- `rules.py` is the only legality authority: no new `if actor...` checks outside it; `meld status` must list every legal verb. Task 4 changes whether a refusal is *announced*, never whether it is a refusal.
- `github.py` is the only GitHub path (plus `appauth.py`'s two App-JWT calls); `gitops.py` the only git runner.
- **Adapters are the only code that knows how a CLI is launched or how it talks.** Limit recognition is therefore adapter work: no module outside `src/meld/adapters/` may match a CLI's prose. `runners.py` may only ask "is there a `usage_limit` event".
- Workers never hold credentials; anything a worker writes that reaches GitHub passes through `neutralise_closers`. Nothing in this plan changes what a worker writes.
- Tests are hermetic and real: `FakeGh` over real subprocesses, real files in `tmp_path`; no network, no real CLI, no host `~/.claude`/gitconfig/wall-clock dependence; inject `now`; pass `adapter_for=` to every `load_context` in tests. **No test may sleep** — the supervisor's clock, its sleep, and its launcher are all already injectable (`clock=`, `sleep=`, `adapter=`), and a freeze test uses the injected clock rather than waiting.
- `uv run pytest -q -p no:cacheprovider` must be green AND print **no warnings** before every commit. The pytest config does not turn warnings into errors, so read the output.
- Never set `MELD_SMOKE=1`.
- Conventional commit subjects from `feat:`/`fix:`/`docs:`/`test:`/`refactor:`, **no attribution trailers of any kind**, one logical change per commit; repo-local identity is already configured.
- Work in `/Users/scottbrant/code/meld-main` on branch `plan-9-run-reliability`. `~/Documents/meld` is iCloud-synced and stalls git; do not work there. Commit with `git -C /Users/scottbrant/code/meld-main ...`.
- Baseline at this plan's base (`73c831c`, spec-only): **716 passed**, no warnings.

## Decisions (rulings made while writing the plan; recorded so executors do not re-litigate)

1. **A limit replaces the terminal event rather than accompanying it.** When an adapter is about to emit `run_finished`, `cli_error` or `raw` and that event's text matches a limit, it emits a single `usage_limit` event instead, carrying the same text plus `meta["resets_at"]`. One event, one meaning: a run whose quota ran out did not finish, and `run_finished` asserts the model returned a final answer. The check runs at all three emission points in both adapters, not only the terminal one, because a rate limit reaches Codex's log as a `turn.failed` and can reach either CLI's log as a bare stderr line.
2. **Recognition is best-effort prose matching, and its failure mode is today's behaviour.** There is no captured limit transcript for either CLI, so the patterns come from prose observed in the first live run plus the tested set in `Maurdekye/orgtree`'s `limits.py` (MIT). A pattern that fails to match means the role is treated as a crash and backed off exactly as it is now — the current bug, not a new one. A pattern that matches wrongly freezes a healthy role until `limit_retry_minutes` passes, so the patterns use a closed list of quota nouns, require context around a bare 429, and never match the bare word "limit". More important than any pattern: **the model's own narration never triggers a limit** — only what the CLI reports as an error or writes to stderr is checked, so a successful run cannot be reclassified whatever its summary says. Task 1's tests include negative cases for all of this, and its first review round is what forced the tightening: the original patterns fired on "you've hit your context limit" and on "Closes #429".
3. **A reset instant is used when the CLI named one and re-probing is used when it did not.** `meta["resets_at"]` is an ISO instant or absent. Absent means the supervisor re-probes every `[runners] limit_retry_minutes` (default 30) rather than guessing a window length, because guessing wrong in the long direction strands a group for hours.
4. **The supervisor posts `LIMITED`, not the role.** The role that hit the limit is already gone. `run_group` resolves the coordination issue with `assemble.find_active_milestone(ctx.gh)`, which is one call and no board, and posts once per freeze — the persisted freeze file carries `posted`, so a supervisor restart inside the same freeze does not post again.
5. **A fifth launch reason, `limit`.** `LAUNCH_REASONS` and `_REASON_TEXT` in `prompts.py` gain it, and both role prompts add it to the list of reasons that warrant a `RESUMED`. Reusing `crash` would tell the role something false; reusing `exit` would suppress the `RESUMED` that closes the `LIMITED` pair.
6. **A launch failure backs off on the same schedule as a crash and never ends the run.** `run_group` catches `DispatchError` from `launch_role`, both for the initial launches and for every relaunch, records it as a failed launch, and keeps the other role alive. A supervisor whose disk is full should be noisy and alive, not gone.
7. **The refusal throttle is the board, not local state.** `actions.refuse` skips its comment when the newest `PROTOCOL-VIOLATION` from the same party for the same target already names the same rule. Reading GitHub's own record rather than a local file is the pattern plan 4 established for `meld:nudged`, and it is what makes the throttle correct across two supervisors and a restart. The `Refused` exception still carries `violation_posted`, now `None` when the post was skipped as a duplicate, so a caller can tell "skipped" from "failed".
8. **The free-space check warns, never fails.** Low disk is a machine condition a person fixes, not a misconfiguration, and `meld doctor` exits non-zero only on `ok is False`. It reports MB free on the volume `$MELD_HOME` sits on and warns below `[logs] warn_free_mb`.

## File structure

| File | Responsibility |
|---|---|
| `src/meld/transcript.py` | `usage_limit` in `KINDS` and `_LABELS` |
| `src/meld/adapters/base.py` | shared limit recognition: the patterns, `is_limit(text)`, `reset_at(text, now)`, and `limit_event(now, text)` |
| `src/meld/adapters/claude.py`, `codex.py` | each emission point that could carry a limit routes through it |
| `src/meld/config.py` | `[runners] limit_retry_minutes`, `[logs] warn_free_mb` |
| `src/meld/prompts.py`, `prompts/orchestrator.md`, `prompts/pm.md` | the `limit` launch reason, and `RESUMED` on it |
| `src/meld/runners.py` | read the exited role's transcript; freeze, persist, post `LIMITED`; back off on a launch that raised |
| `src/meld/actions.py` | `refuse` skips a duplicate `PROTOCOL-VIOLATION` |
| `src/meld/doctor.py` | the `disk-free` check |
| `tests/test_adapters.py`, `test_adapter_claude.py`, `test_adapter_codex.py`, `test_runners.py`, `test_actions.py`, `test_doctor.py`, `test_config.py`, `test_role_prompts.py` | one suite per site |
| `docs/HANDOFF.md` | residuals 3, 4 and 14 marked landed; the plan-9 section |

---

### Task 1: the `usage_limit` kind, and each adapter's recognition of its own CLI's limit

**Files:**
- Modify: `src/meld/transcript.py` (`KINDS`, `_LABELS`)
- Modify: `src/meld/adapters/base.py` (the patterns and three helpers)
- Modify: `src/meld/adapters/claude.py`, `src/meld/adapters/codex.py` (route each emission point through them)
- Modify: `src/meld/config.py` (`logs_warn_free_mb`, `runners_limit_retry_minutes`)
- Test: `tests/test_adapters.py`, `tests/test_adapter_claude.py`, `tests/test_adapter_codex.py`, `tests/test_transcript.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: `transcript.Event`, `cap`, `KINDS` and each adapter's existing `parse_events` (plan 8a).
- Produces: `"usage_limit"` in `transcript.KINDS` with the label `limit`; `adapters.base.is_limit(text) -> bool`; `adapters.base.reset_at(text: str, now: str) -> str | None`; `adapters.base.limit_event(now: str, text: str) -> list[Event]`; `Config.logs_warn_free_mb: int`; `Config.runners_limit_retry_minutes: int`.

Read decision 2 before you start. There is no captured limit transcript for either CLI, so these patterns are best-effort. A pattern that misses leaves today's behaviour in place (the role is treated as a crash and backed off); a pattern that over-matches freezes a healthy role, which is worse. So the patterns require a possessive limit phrase, an explicit rate-limit phrase, or a 429, and the negative tests below exist to keep them that way. In particular a role that exhausted `--max-turns` is **not** usage-limited and must stay an ordinary exit.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_adapters.py`:

```python
from meld.adapters.base import is_limit, limit_event, reset_at

NOW = "2026-09-12T02:11:19Z"


def test_is_limit_recognises_the_prose_each_cli_actually_prints():
    for text in [
        "You've hit your individual spend limit. It resets at 6pm.",
        "You have reached your usage limit for this model.",
        "Claude AI usage limit reached|1789500000",
        "You've reached your session limit",
        "Your weekly limit for this model has been used up",
        "429 Too Many Requests",
        "rate limited; retry later",
        "quota exceeded for this organization",
    ]:
        assert is_limit(text), text


def test_is_limit_does_not_fire_on_prose_that_merely_says_limit():
    for text in [
        "error_max_turns",                                   # --max-turns, an ordinary exit, never a freeze
        "the max turns limit was reached",                   # same, phrased as prose
        "I refactored the rate limiter and its limit test",   # a worker talking about code
        "limit",
        "the limits of this approach are worth naming",
        "",
    ]:
        assert not is_limit(text), text


def test_reset_at_reads_an_explicit_instant_a_clock_time_and_a_duration():
    assert reset_at("resets at 2026-09-12T18:00:00Z", NOW) == "2026-09-12T18:00:00Z"
    assert reset_at("It resets at 6pm.", NOW) == "2026-09-12T18:00:00Z"
    assert reset_at("resets at 18:00", NOW) == "2026-09-12T18:00:00Z"
    assert reset_at("try again in 3 hours", NOW) == "2026-09-12T05:11:19Z"
    assert reset_at("try again in 45 minutes", NOW) == "2026-09-12T02:56:19Z"


def test_reset_at_rolls_a_clock_time_that_has_already_passed_to_tomorrow():
    assert reset_at("resets at 1am", "2026-09-12T02:11:19Z") == "2026-09-13T01:00:00Z"


def test_reset_at_is_none_when_no_time_is_named():
    assert reset_at("You've hit your limit.", NOW) is None
    assert reset_at("resets soon", NOW) is None


def test_limit_event_carries_the_text_and_the_reset_when_there_is_one():
    (with_reset,) = limit_event(NOW, "You've hit your spend limit. It resets at 6pm.")
    assert with_reset.kind == "usage_limit" and with_reset.meta["resets_at"] == "2026-09-12T18:00:00Z"
    (without,) = limit_event(NOW, "You've hit your spend limit.")
    assert without.kind == "usage_limit" and "resets_at" not in without.meta
```

Add to `tests/test_adapter_claude.py`:

```python
def test_parse_events_turns_a_limited_result_into_usage_limit_not_run_finished():
    line = ('{"type":"result","subtype":"error_usage_limit","is_error":true,'
            '"result":"You\'ve hit your individual spend limit. It resets at 6pm.","num_turns":3}')
    (event,) = ClaudeAdapter().parse_events(line, "2026-09-12T02:11:19Z")
    assert event.kind == "usage_limit"
    assert event.meta["resets_at"] == "2026-09-12T18:00:00Z"
    assert "spend limit" in event.text


def test_parse_events_leaves_a_max_turns_result_an_ordinary_run_finished():
    line = '{"type":"result","subtype":"error_max_turns","is_error":true,"result":"","num_turns":10000}'
    (event,) = ClaudeAdapter().parse_events(line, "2026-09-12T02:11:19Z")
    assert event.kind == "run_finished" and event.ok is False


def test_parse_events_recognises_a_limit_on_a_bare_stderr_line():
    (event,) = ClaudeAdapter().parse_events("Claude AI usage limit reached|1789500000", "2026-09-12T02:11:19Z")
    assert event.kind == "usage_limit"
```

Add to `tests/test_adapter_codex.py`:

```python
def test_parse_events_turns_a_rate_limited_turn_failure_into_usage_limit():
    line = '{"type": "turn.failed", "error": {"message": "429 Too Many Requests; rate limited"}}'
    (event,) = CodexAdapter().parse_events(line, "2026-09-12T02:11:19Z")
    assert event.kind == "usage_limit" and event.ok is False


def test_parse_events_leaves_an_ordinary_turn_failure_a_cli_error():
    line = '{"type": "turn.failed", "error": {"message": "sandbox denied write to /etc"}}'
    (event,) = CodexAdapter().parse_events(line, "2026-09-12T02:11:19Z")
    assert event.kind == "cli_error" and event.ok is False


def test_parse_events_recognises_a_limit_on_a_bare_stderr_line():
    (event,) = CodexAdapter().parse_events(
        "2026-09-12T02:11:19Z ERROR codex_core: quota exceeded for this organization", "2026-09-12T02:11:19Z")
    assert event.kind == "usage_limit"
```

Add to `tests/test_transcript.py`:

```python
def test_usage_limit_is_a_kind_and_renders_as_limit():
    assert "usage_limit" in KINDS
    assert render_line(mk("usage_limit", text="You've hit your spend limit.")) == \
        "02:11:19  limit    You've hit your spend limit."
```

Add to `tests/test_config.py`:

```python
def test_limit_retry_and_free_space_knobs_have_defaults_and_floors():
    base = {
        "repo": {"name": "acme/widgets"},
        "humans": {"accounts": ["empowerScott"]},
        "groups": {"claude": {"cli": "claude", "bot": "b", "token_env": "T",
                              "orchestrator": {"model": "claude-opus-5", "effort": "high"},
                              "pm": {"model": "claude-sonnet-5", "effort": "medium"}}},
    }
    default = Config.from_dict(base)
    assert default.runners_limit_retry_minutes == 30
    assert default.logs_warn_free_mb == 2048

    tuned = Config.from_dict({**base, "runners": {"limit_retry_minutes": 5}, "logs": {"warn_free_mb": 1}})
    assert tuned.runners_limit_retry_minutes == 5 and tuned.logs_warn_free_mb == 1

    with pytest.raises(ConfigError, match="limit_retry_minutes"):
        Config.from_dict({**base, "runners": {"limit_retry_minutes": 0}})
    with pytest.raises(ConfigError, match="warn_free_mb"):
        Config.from_dict({**base, "logs": {"warn_free_mb": 0}})
```

The config test's `groups` fixture carries `orchestrator` and `pm` because `Config.from_dict` requires both unconditionally for every group — the plan-8a task that omitted them hit exactly that.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_adapters.py tests/test_adapter_claude.py tests/test_adapter_codex.py tests/test_transcript.py tests/test_config.py -q -p no:cacheprovider
```

Expected: `ImportError: cannot import name 'is_limit' from 'meld.adapters.base'`, then assertion failures on the kind and the config fields. Capture the real output.

- [ ] **Step 3: Write the implementation**

In `src/meld/transcript.py`, add `"usage_limit"` to `KINDS` immediately after `"run_finished"`, and `"usage_limit": "limit",` to `_LABELS`. Both are single-line edits; `_LABEL_WIDTH` derives itself, so the column does not move (`limit` is shorter than `trimmed`).

In `src/meld/adapters/base.py`, add `import re` and `from datetime import datetime, timedelta, timezone`, then:

```python
# A CLI's answer when the subscription's quota is gone. Best-effort by nature: there is no captured
# transcript of either CLI hitting a limit, so these come from prose observed in the first live run plus
# the tested set in Maurdekye/orgtree's limits.py (MIT). Getting it wrong in one direction leaves today's
# behaviour (the role is treated as a crash and backed off); wrong in the other freezes a healthy role.
# So every alternative below requires a possessive limit phrase, an explicit rate-limit phrase, or a 429 --
# never the bare word "limit", and never the max-turns ceiling, which is an ordinary exit.
_LIMIT_RE = re.compile(
    r"(?:\b(?:your|the)\b[^.]{0,40}?\b(?:usage|rate|spend|session|weekly|daily|monthly)\s+limit\b"
    r"|\busage\s+limit\s+reached\b"
    r"|\brate[-\s]?limit(?:ed|ing)?\b"
    r"|\bquota\s+exceeded\b"
    r"|\b429\s+too\s+many\s+requests\b"
    r"|\b(?:http|status|code)\s*:?\s*429\b)",
    re.IGNORECASE)
# The max-turns ceiling says "limit" and is not one: a role that used its turns exited, it was not throttled.
_NOT_LIMIT_RE = re.compile(r"\bmax[-_\s]?turns\b", re.IGNORECASE)
_ISO_RESET_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})Z?\b")
_CLOCK_RESET_RE = re.compile(r"\b(?:reset(?:s|ting)?|available|try\s+again|back)\b[^.]{0,20}?\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
                             re.IGNORECASE)
_DURATION_RESET_RE = re.compile(r"\bin\s+(?:about\s+)?(\d{1,3})\s*(second|minute|hour)s?\b", re.IGNORECASE)
_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def is_limit(text: object) -> bool:
    """Does this text say the subscription's quota is gone, rather than merely containing the word "limit"?"""
    s = text if isinstance(text, str) else ("" if text is None else str(text))
    if not s or _NOT_LIMIT_RE.search(s):
        return False
    return bool(_LIMIT_RE.search(s))


def _parse_now(now: str) -> datetime | None:
    try:
        return datetime.strptime(now, _ISO_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def reset_at(text: str, now: str) -> str | None:
    """When the quota comes back, as an ISO instant, or None when the text named no time.

    None is a real answer, not a failure: the supervisor re-probes on a fixed cadence rather than guessing a
    window length, because guessing long strands a whole group for hours (spec section 17).
    """
    base = _parse_now(now)
    if base is None:
        return None
    m = _ISO_RESET_RE.search(text or "")
    if m:
        # A codex stderr line leads with its own RFC3339 timestamp, so a matched instant is only believed
        # when it is strictly after `now` -- otherwise it is the log line's own clock, not a stated reset,
        # and an already-expired "reset" would relaunch a limited role at once (the 45-minute silent
        # relaunch storm this plan exists to stop). A match that fails that test falls through to the
        # duration and clock branches below, as if it had not matched -- a real "resets in 3 hours" further
        # in the same line still gets its chance.
        candidate_str = m.group(1).replace(" ", "T") + "Z"
        candidate = _parse_now(candidate_str)
        if candidate is not None and candidate > base:
            return candidate_str
    m = _DURATION_RESET_RE.search(text or "")
    if m:
        unit = {"second": "seconds", "minute": "minutes", "hour": "hours"}[m.group(2).lower()]
        return (base + timedelta(**{unit: int(m.group(1))})).strftime(_ISO_FMT)
    m = _CLOCK_RESET_RE.search(text or "")
    if m:
        hour, minute, meridiem = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            return None
        when = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when <= base:                      # the named clock time already passed today
            when += timedelta(days=1)
        return when.strftime(_ISO_FMT)
    return None


def limit_event(now: str, text: str) -> list[Event]:
    """The one event a supervisor acts on. `ok` is False: a quota that ran out is not a success."""
    when = reset_at(text, now)
    meta = {"resets_at": when} if when else {}
    return [Event(seq=0, at=now, kind="usage_limit", ok=False, text=cap(text), meta=meta)]
```

In `src/meld/adapters/claude.py`, route the three emission points through it. In `parse_events`, the `raw` fallback becomes a limit check first:

```python
        doc, stripped = parse_doc(line)
        if doc is None:
            return limit_event(now, stripped) if is_limit(stripped) else raw_event(now, stripped)
```

the `result` branch checks its own text before building `run_finished`:

```python
        if kind == "result":
            usage = doc.get("usage") if isinstance(doc.get("usage"), dict) else {}
            # The model's own narration never triggers a limit -- only what the CLI itself reports as an
            # error may. A successful result's text is the model talking about its own work (it can say
            # "429" or "the rate limit for GitHub's API" while doing so), so is_limit is consulted only when
            # the CLI marked the run an error; a clean result is always an ordinary run_finished.
            if doc.get("is_error", False):
                said = str(doc.get("result", "")) + " " + str(doc.get("subtype", ""))
                if is_limit(said):
                    return limit_event(now, str(doc.get("result", "")) or str(doc.get("subtype", "")))
            return [Event(seq=0, at=now, kind="run_finished", ok=not doc.get("is_error", False),
                          text=cap(doc.get("result", doc.get("subtype", ""))),
                          meta={"subtype": str(doc.get("subtype", "")), "num_turns": doc.get("num_turns"),
                                "cost_usd": doc.get("total_cost_usd"), "usage": usage})]
```

The `is_error` gate is the substance: a result the CLI calls a success is the model talking, and the model's own narration never triggers a limit. Only the guarded block is new; the `run_finished` construction is exactly what is there today.

and the trailing `return raw_event(now, stripped)` for an unknown type becomes the same pair as the first one. Import `is_limit` and `limit_event` alongside the existing `parse_doc, raw_event`.

In `src/meld/adapters/codex.py`, the same three: the `doc is None` fallback, the `turn.failed` branch (check `error.get("message")` and emit `limit_event` instead of `cli_error` when it matches), and the trailing unknown-type `raw_event`. Leave codex's `_item` alone: a `command_execution`'s output is the worker's own CLI call, not the role whose quota the supervisor judges, so a limit printed inside a command must not freeze the role. Its `raw_event` fallback stays unrouted.

In `src/meld/config.py`, add the two fields beside the ones plan 8a added, with the same `_at_least` validation those use:

```python
    runners_limit_retry_minutes: int = 30
    logs_warn_free_mb: int = 2048
```

and in `from_dict`, next to the existing `runners`/`logs` lookups:

```python
            runners_limit_retry_minutes=_at_least(runners.get("limit_retry_minutes", 30), RUNNERS_MIN_LIMIT_RETRY_MINUTES,
                                                  "runners.limit_retry_minutes", "minutes"),
            logs_warn_free_mb=_at_least(logs.get("warn_free_mb", 2048), LOGS_MIN_WARN_FREE_MB, "logs.warn_free_mb", "MB"),
```

with `RUNNERS_MIN_LIMIT_RETRY_MINUTES = 1` and `LOGS_MIN_WARN_FREE_MB = 1` beside the existing `LOGS_MIN_*` constants. `_at_least(value, minimum, path, unit)` takes the resolved value first, not the table and a key — that is the signature plan 8a's fix wave landed, and it refuses a `bool` and a float as well as anything under the minimum, which is what makes the two `pytest.raises` cases above pass.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_adapters.py tests/test_adapter_claude.py tests/test_adapter_codex.py tests/test_transcript.py tests/test_config.py -q -p no:cacheprovider
uv run pytest -q -p no:cacheprovider
```

Expected: the new tests pass and the suite is 716 plus the new ones, no warnings. Plan 8a's golden render test in `tests/test_adapter_claude.py` must still pass unchanged — none of its fixture lines is a limit, so if it moves you have over-matched.

- [ ] **Step 5: Commit**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/transcript.py src/meld/adapters src/meld/config.py tests/
git -C /Users/scottbrant/code/meld-main commit -m "feat: each adapter recognises its own CLI's usage limit

A role that exhausts its quota does not crash in any way an exit code
distinguishes from a clean finish, so the runner has to read what the
role said. usage_limit is that signal, and recognising it is adapter
work because the prose belongs to the CLI. The patterns require a
possessive limit phrase, a rate-limit phrase or a 429, never the bare
word, and never the max-turns ceiling, which is an ordinary exit."
```

---

### Task 2: a usage-limited role freezes instead of relaunching, and the board is told once

**Files:**
- Modify: `src/meld/runners.py` (the exit branch of `run_group`, plus two helpers)
- Modify: `src/meld/prompts.py` (`LAUNCH_REASONS`, `_REASON_TEXT`)
- Modify: `src/meld/prompts/orchestrator.md`, `src/meld/prompts/pm.md` (`RESUMED` on a `limit` relaunch)
- Test: `tests/test_runners.py`, `tests/test_role_prompts.py`

**Interfaces:**
- Consumes: `transcript.Tail` and each adapter's `parse_events` (plan 8a); `usage_limit` and `Config.runners_limit_retry_minutes` (Task 1).
- Produces: `runners.limit_of(log_path: Path, adapter, now: str) -> str | None | Literal[False]` — see below for why three outcomes; `runners.freeze_path(home, group, role) -> Path`; `runners.read_freeze(path) -> dict | None`; `runners.write_freeze(path, payload) -> None`; a `"limit"` entry in `prompts.LAUNCH_REASONS`.

`limit_of` answers three things, so it returns three kinds of value: `False` when the role's transcript shows no limit, a reset instant (a string) when it shows one that named a time, and `None` when it shows a limit that named no time. A plain `Optional[str]` cannot express "limited, time unknown" distinctly from "not limited", and conflating them is the difference between a 30-minute re-probe and an immediate hot relaunch. If you prefer a small dataclass, use one and say so in your report — but do not collapse the three.

- [ ] **Step 1: Write the failing tests**

`tests/test_runners.py` already drives `run_group` with `adapter=FakeAdapter(script=...)`, `clock=`, `sleep=` and `cycles=`/`once=`, which is what makes these tests sleepless. Three facts about that file you need:

- Its fixture is `make(tmp_path) -> (ctx, env)`, which builds the `FakeGh` internally and **discards it**. Your tests must assert on posted comments, so change `make` to `return fake, ctx, env` and update its ten call sites from `ctx, env = make(tmp_path)` to `_, ctx, env = make(tmp_path)`. Ten mechanical edits beat duplicating the fixture, which a reviewer would rightly flag.
- `load_board1_routes` routes **GET only**. `create_comment` needs a POST route or it raises, and `_freeze` swallows that and records `posted: False` — which silently breaks the "posted once" assertion. So each test that expects a post adds `fake.route("POST", "repos/acme/widgets/issues/10/comments", {"id": 999, "html_url": "u"})`.
- Board1's coordination issue is **10**, and its milestone is 3.

Now add:

```python
import json

from meld.runners import freeze_path, limit_of, read_freeze


LIMIT_LINE = "You've hit your individual spend limit. It resets at 6pm."


def test_limit_of_distinguishes_no_limit_a_timed_limit_and_an_untimed_one(tmp_path):
    from meld.adapters.codex import CodexAdapter
    adapter = CodexAdapter()
    now = "2026-09-12T02:11:19Z"

    quiet = tmp_path / "quiet.log"
    quiet.write_text('{"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}\n')
    assert limit_of(quiet, adapter, now) is False

    timed = tmp_path / "timed.log"
    timed.write_text(json.dumps({"type": "turn.failed", "error": {"message": LIMIT_LINE}}) + "\n")
    assert limit_of(timed, adapter, now) == "2026-09-12T18:00:00Z"

    untimed = tmp_path / "untimed.log"
    untimed.write_text(json.dumps({"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}) + "\n")
    assert limit_of(untimed, adapter, now) is None

    assert limit_of(tmp_path / "absent.log", adapter, now) is False
```

Then the supervisor behaviour. The role's own log is what the supervisor reads, so the fake script writes the limit line to stdout and exits non-zero — which is exactly what a real limited role does, and what today's code reads as a crash:

```python
LIMIT_SCRIPT = (
    "import sys; "
    "sys.stdout.write('{\"type\": \"turn.failed\", \"error\": {\"message\": "
    "\"You have reached your usage limit for this model.\"}}\\n'); "
    "raise SystemExit(1)"
)


def test_a_limited_role_freezes_rather_than_relaunching_and_posts_LIMITED_once(tmp_path):
    fake, ctx, env = make(tmp_path)
    fake.route("POST", "repos/acme/widgets/issues/10/comments", {"id": 999, "html_url": "u"})
    out = run_group(ctx, adapter=FakeAdapter(script=LIMIT_SCRIPT), cycles=3, interval=0, sleep=lambda s: None)

    posts = [c.body["body"] for c in fake.calls if c.method == "POST" and "comments" in c.endpoint]
    limited = [b for b in posts if "LIMITED" in b]
    assert len(limited) == 1, posts                      # once per freeze, not once per cycle
    assert "usage limit" in limited[0]

    freeze = read_freeze(freeze_path(ctx.store.home, "claude", "orchestrator"))
    assert freeze is not None and freeze["posted"] is True
    assert out["roles"]["orchestrator"]["launches"] == 1   # frozen, so never relaunched


def test_a_freeze_expires_on_the_injected_clock_and_the_role_comes_back_as_limit(tmp_path):
    """The reset instant is compared against an injected now, so this test does not wait for one."""
    fake, ctx, env = make(tmp_path)
    fake.route("POST", "repos/acme/widgets/issues/10/comments", {"id": 999, "html_url": "u"})
    path = freeze_path(ctx.store.home, "claude", "pm")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"resets_at": "2020-01-01T00:00:00Z", "posted": True, "at": "2020-01-01T00:00:00Z"}))

    out = run_group(ctx, adapter=FakeAdapter(), cycles=1, interval=0, sleep=lambda s: None)
    assert out["roles"]["pm"]["launches"] >= 1
    assert read_freeze(path) is None                     # a passed freeze is cleared, not left to re-read
    assert out["roles"]["pm"]["reason"] == "limit"


def test_a_live_freeze_survives_a_supervisor_restart_without_posting_again(tmp_path):
    fake, ctx, env = make(tmp_path)
    fake.route("POST", "repos/acme/widgets/issues/10/comments", {"id": 999, "html_url": "u"})
    path = freeze_path(ctx.store.home, "claude", "orchestrator")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"resets_at": "2099-01-01T00:00:00Z", "posted": True, "at": "2026-09-12T02:11:19Z"}))

    run_group(ctx, adapter=FakeAdapter(), cycles=1, interval=0, sleep=lambda s: None)
    assert not [c for c in fake.calls if c.method == "POST" and "LIMITED" in str(c.body)]


def test_an_untimed_freeze_reprobes_after_limit_retry_minutes(tmp_path):
    fake, ctx, env = make(tmp_path)
    fake.route("POST", "repos/acme/widgets/issues/10/comments", {"id": 999, "html_url": "u"})
    path = freeze_path(ctx.store.home, "claude", "orchestrator")
    path.parent.mkdir(parents=True, exist_ok=True)
    # No resets_at: the freeze is re-probed limit_retry_minutes after `at`, which is long past here.
    path.write_text(json.dumps({"posted": True, "at": "2020-01-01T00:00:00Z"}))

    out = run_group(ctx, adapter=FakeAdapter(), cycles=1, interval=0, sleep=lambda s: None)
    assert out["roles"]["orchestrator"]["launches"] >= 1
```

`run_group` already returns `{"group": name, "roles": {role: {"launches", "restarts", "last_exit"}}}`, so the assertions above use `out["roles"]["orchestrator"]["launches"]` — **fix them to that shape, not the `out["launches"][role]` shorthand written above** — and Task 2 adds one key, `"reason"`, carrying that role's most recent launch reason, which is what the freeze-expiry test reads.

Add to `tests/test_role_prompts.py`:

```python
def test_a_limit_relaunch_is_a_reason_and_both_roles_announce_it():
    from meld.prompts import LAUNCH_REASONS, launch_block
    assert "limit" in LAUNCH_REASONS
    block = "\n".join(launch_block(4, "limit"))
    assert "`limit`" in block and "quota" in block.lower()
    for name in ("orchestrator.md", "pm.md"):
        text = (Path(__file__).parents[1] / "src" / "meld" / "prompts" / name).read_text()
        assert "limit" in text and "RESUMED" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_runners.py tests/test_role_prompts.py -q -p no:cacheprovider
```

Expected: `ImportError: cannot import name 'limit_of' from 'meld.runners'`, then `ValueError: reason must be one of start, exit, crash, recycle, got 'limit'`. Capture the real output.

- [ ] **Step 3: Write the implementation**

In `src/meld/prompts.py`:

```python
LAUNCH_REASONS = ("start", "exit", "crash", "recycle", "limit")
```

and one entry in `_REASON_TEXT`, beside the four already there:

```python
_REASON_TEXT = {
    # ... the four existing entries, unchanged ...
    "limit": "the previous session ended because this account's quota ran out; the supervisor froze the "
             "role and is relaunching now that the window has passed",
}
```

In both `src/meld/prompts/orchestrator.md` and `src/meld/prompts/pm.md`, add `limit` to the list of reasons that warrant the announcement. In `pm.md` the sentence today reads "post `[<group>:pm] RESUMED` only when the reason is `crash` or `recycle`"; make it `crash`, `recycle` or `limit`, and add one clause saying a `limit` relaunch means the supervisor already posted `LIMITED`, so `RESUMED` closes that pair rather than opening a new topic. Make the matching edit in `orchestrator.md` next to its own `RESUMED` instruction.

In `src/meld/runners.py`, add the imports (`json`, `from datetime import datetime, timedelta, timezone`, `from meld.assemble import BoardError, find_active_milestone`, `from meld.atomic import write_atomic`, `from meld.github import GitHubError`, `from meld.grammar import Header, compose`, `from meld.config import Party`, `from meld.transcript import Tail`) and:

```python
def freeze_path(home: Path, group: str, role: str) -> Path:
    """Where a usage freeze is remembered. Local state, so a supervisor restart honours a live freeze."""
    return home / "state" / group / role / "limit.json"


def read_freeze(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_freeze(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(payload, indent=2) + "\n")


def limit_of(log_path: Path, adapter, now: str) -> str | None | bool:
    """`False` when this role's transcript shows no usage limit; the reset instant when it shows one that
    named a time; `None` when it shows one that named no time.

    Three outcomes rather than two on purpose: "limited, time unknown" has to be distinguishable from "not
    limited", because the first waits `runners.limit_retry_minutes` and the second relaunches at once.
    """
    reader = Tail(log_path)
    # read then flush, exactly as dispatch.render_tail does: a role killed mid-write leaves its last line
    # unterminated, and that is the line most likely to carry the limit.
    events = reader.read(adapter.parse_events, now) + reader.flush(adapter.parse_events, now)
    for event in reversed(events):
        if event.kind == "usage_limit":
            return event.meta.get("resets_at") or None
    return False
```

Then, in `run_group`, the exit branch. Today it reads:

```python
                if code is not None:
                    rp.last_exit = code
                    if once:
                        continue
                    if code == 0 and clock() - rp.started >= CLEAN_EXIT_SECONDS:
                        restarts, reason = 0, "exit"
                    else:
                        restarts, reason = rp.restarts + 1, ("exit" if code == 0 else "crash")
                        _pause(min(5.0 * (2 ** rp.restarts), _MAX_BACKOFF))
```

Insert the limit check between `if once: continue` and the exit-code classification, because a limit outranks both: a limited role exited in a way the code cannot distinguish, and relaunching it is the bug.

```python
                    limited = limit_of(rp.log_path, adp, _iso_now())
                    if limited is not False:
                        _freeze(ctx, group, role, resets_at=limited, now=_iso_now())
                        continue                     # do not relaunch; the next cycle re-checks the freeze
```

and at the top of the per-role loop, before `rp.proc.poll()`, honour a freeze that is already in place:

```python
            for role, rp in procs.items():
                if _frozen(ctx, group, role, now=_iso_now()):
                    continue
```

`_freeze` writes the file and posts once:

```python
def _freeze(ctx, group: GroupConfig, role: str, *, resets_at: str | None, now: str) -> None:
    """Remember the freeze, and tell the board once. The role that hit the limit is already gone, so the
    supervisor is the only thing left that can say so (spec section 7)."""
    path = freeze_path(ctx.store.home, group.name, role)
    existing = read_freeze(path)
    if existing is not None and existing.get("posted"):
        return                                        # a restart inside a live freeze says nothing new
    payload = {"at": now, "posted": False}
    if resets_at:
        payload["resets_at"] = resets_at
    try:
        _, coordination = find_active_milestone(ctx.gh)
        party = Party("pm", group.name) if role == "pm" else Party("group", group.name)
        until = f"resets: {resets_at}" if resets_at else f"resets: unknown; re-probing every {ctx.cfg.runners_limit_retry_minutes}m"
        ctx.gh.create_comment(coordination, compose(Header(party, "LIMITED", None, None),
                                                    f"{group.name}'s {role} hit its usage limit and is frozen.\n{until}"))
        payload["posted"] = True
    except (GitHubError, BoardError):
        pass                                          # the freeze matters more than the announcement; retried next cycle
    write_freeze(path, payload)
```

`_frozen` decides whether the freeze still stands:

```python
def _frozen(ctx, group: GroupConfig, role: str, *, now: str) -> bool:
    """True while the freeze stands. A passed freeze is cleared here, so the next poll relaunches."""
    path = freeze_path(ctx.store.home, group.name, role)
    freeze = read_freeze(path)
    if freeze is None:
        return False
    resets_at = freeze.get("resets_at")
    deadline = resets_at or _plus_minutes(freeze.get("at", now), ctx.cfg.runners_limit_retry_minutes)
    if deadline and now < deadline:                   # ISO-8601 Z strings sort chronologically
        return True
    path.unlink(missing_ok=True)
    return False
```

with `_iso_now()` returning `datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")` and `_plus_minutes(iso, minutes)` returning the same format, both taking an injectable clock if the file's existing `clock=` seam reaches them — check how `run_group` threads `clock` and use it rather than adding a second notion of now. **The tests must not depend on the wall clock**, which is why the freeze deadline is compared as a string against an injected `now`.

The relaunch after a freeze passes uses `reason="limit"`, so the role posts `RESUMED`: where `_frozen` returns False after clearing a file that existed, carry that through to the `launch_role(..., reason="limit")` call on that cycle.

Finally, have `run_group`'s return value carry `launches` and `reasons` per role if it does not already, since that is what the tests assert on.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_runners.py tests/test_role_prompts.py -q -p no:cacheprovider
uv run pytest -q -p no:cacheprovider
```

Expected: PASS, whole suite green with no warnings. Check specifically that no existing `test_runners.py` test slowed down: if any test's duration jumped, a freeze is being honoured where the old behaviour was asserted, and that test needs its own `limit.json` cleared rather than the production code relaxed.

- [ ] **Step 5: Commit**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/runners.py src/meld/prompts.py src/meld/prompts tests/
git -C /Users/scottbrant/code/meld-main commit -m "feat: a usage-limited role freezes instead of hot-relaunching

The first live run relaunched a role for 45 minutes after its quota ran
out, with nothing on the board to say why, because an exhausted quota
exits in a way no exit code distinguishes from a clean finish. The
supervisor now reads the role's own transcript, freezes until the reset
the CLI named (or re-probes when it named none), and posts LIMITED
once. The freeze is persisted, so a supervisor restart honours it and
does not announce it twice."
```

---

### Task 3: a launch that cannot start backs off instead of ending the run

**Files:**
- Modify: `src/meld/runners.py` (`run_group`'s initial launches and its relaunch)
- Test: `tests/test_runners.py`

**Interfaces:**
- Consumes: `launch_role`, which already raises `DispatchError` when the adapter's `launch` raises `OSError`.
- Produces: no new symbol. `run_group` no longer propagates a `DispatchError` from a launch, and each role's entry in the return value gains `"launch_errors": int`.

This is residual 4, and it is the one that actually ended the first live run. `launch_role` wraps `adapter.launch` in `except OSError as e: raise DispatchError(f"cannot start {adapter.name}: {e}")`. `open_log` creates the log file, so a full disk raises `OSError` there, `DispatchError` propagates out of `run_group`, and `meld run` exits — which is how both groups died mid-milestone on 2026-09-12 with `[Errno 28] No space left on device` as their last word. A supervisor whose disk is full should be noisy and alive.

- [ ] **Step 1: Write the failing test**

```python
def test_a_launch_that_cannot_start_backs_off_and_keeps_the_run_alive(tmp_path):
    _, ctx, env = make(tmp_path)

    class Refusing:
        """An adapter whose launch fails the way a full disk fails, then recovers."""
        name = "refusing"

        def __init__(self, fail_times: int):
            self.left = fail_times
            self.ok = FakeAdapter()

        def launch(self, spec):
            if self.left > 0:
                self.left -= 1
                raise OSError(28, "No space left on device")
            return self.ok.launch(spec)

        def __getattr__(self, item):                 # everything else behaves like the fake
            return getattr(self.ok, item)

    slept: list[float] = []
    out = run_group(ctx, adapter=Refusing(fail_times=2), cycles=3, interval=0,
                    sleep=slept.append, clock=Clock(), base_env=env)

    assert slept, "a failed launch must back off rather than spin"
    assert out["group"] == "claude"                  # the run returned normally instead of raising
    assert out["roles"]["orchestrator"]["launch_errors"] >= 1


def test_one_roles_launch_failure_does_not_take_the_other_role_down(tmp_path):
    _, ctx, env = make(tmp_path)

    class HalfBroken:
        name = "half"

        def __init__(self):
            self.ok = FakeAdapter()

        def launch(self, spec):
            if spec.role == "orchestrator":
                raise OSError(28, "No space left on device")
            return self.ok.launch(spec)

        def __getattr__(self, item):
            return getattr(self.ok, item)

    out = run_group(ctx, adapter=HalfBroken(), cycles=2, interval=0, sleep=lambda s: None,
                    clock=Clock(), base_env=env)
    assert out["roles"]["pm"]["launches"] >= 1        # the healthy role ran
    assert out["roles"]["orchestrator"]["launch_errors"] >= 1
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_runners.py -q -p no:cacheprovider
```

Expected: `meld.dispatch.DispatchError: cannot start refusing: [Errno 28] No space left on device` escaping `run_group`. Capture it — that traceback is the bug this task fixes, and it is worth having in the record.

- [ ] **Step 3: Write the implementation**

In `run_group`, wrap both launch sites. The initial loop becomes a helper both sites share, so the backoff and the bookkeeping exist once:

```python
    def _try_launch(role: str, *, launch_no: int, reason: str, prior: RoleProcess | None) -> RoleProcess | None:
        """Launch a role, or record the failure and back off. Never raises: a supervisor that cannot start
        a role has a problem a person must fix (no disk, no binary, no permission), and exiting hides it
        instead of reporting it. Residual 4: an OSError opening a log file used to end `meld run`."""
        try:
            fresh = launch_role(ctx, group, adp, role, base_env=base_env, clock=clock,
                                launch_no=launch_no, reason=reason)
        except DispatchError as e:
            errors[role] = errors.get(role, 0) + 1
            failures[role] = str(e)
            _pause(min(5.0 * (2 ** (errors[role] - 1)), _MAX_BACKOFF))
            return None
        fresh.restarts = prior.restarts + 1 if prior is not None else 0
        return fresh
```

with `errors: dict[str, int] = {}` and `failures: dict[str, str] = {}` beside `procs`, and `procs` becoming tolerant of a missing role: the per-role loop iterates over `ROLES` rather than `procs.items()`, and a role with no live process is a role to try launching again on this cycle. That inversion is the substance of the task — today the loop can only ever inspect processes that started.

Add `"launch_errors": errors.get(role, 0)` and `"last_launch_error": failures.get(role)` to each role's entry in the return value.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_runners.py -q -p no:cacheprovider
uv run pytest -q -p no:cacheprovider
```

Expected: PASS, whole suite green with no warnings. `test_run_group_once_launches_both_roles_and_returns_when_they_exit` and the backoff tests must still pass untouched — if the loop inversion changed how many times a healthy role launches, it is wrong.

- [ ] **Step 5: Commit**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/runners.py tests/test_runners.py
git -C /Users/scottbrant/code/meld-main commit -m "fix: a launch that cannot start no longer ends the run

open_log creates the log file, so a full disk raised OSError, which
launch_role turns into DispatchError, which propagated out of run_group
and exited meld run. That is how both groups died mid-milestone on
2026-09-12. A failed launch now backs off on the crash schedule, records
the error, and leaves the other role running."
```

---

### Task 4: the same refusal, re-derived, stays off the board

**Files:**
- Modify: `src/meld/actions.py` (`refuse`)
- Test: `tests/test_actions.py`

**Interfaces:**
- Consumes: `Board.comments` (a tuple of `ParsedComment`, each with `.header`, `.text`, `.author`, `.created_at`) and `Refused`, which already carries `violation_posted`.
- Produces: no new symbol. `refuse` skips its comment when the newest matching `PROTOCOL-VIOLATION` already names the same rule, and `Refused.violation_posted` is `None` for that case — distinct from `True` (posted) and `False` (posting failed).

This is residual 14. A PM re-derives the same intention from the board on every launch and re-issues a call the rules already refused; `refuse` posts a `PROTOCOL-VIOLATION` each time. One live run accumulated 109 of them, most identical. The throttle is the board itself, exactly as `meld:nudged` throttles the notifier (spec section 14): if the newest violation from this party about this target says the same thing, saying it again adds nothing.

- [ ] **Step 1: Write the failing test**

`tests/test_actions.py` already has `make(tmp_path, group, role) -> (fake, ctx)`, `fixture_comments()`, `R = "repos/acme/widgets/"` and `posted_headers(fake)`. A `Board` comes from `build_status(ctx)[1]`, and a pre-existing violation goes in by re-routing the comments endpoint before assembling. Add these imports to the file — `from meld.actions import Refusal, refuse`, `from meld.rules import Verb`, `from meld.status import build_status` — then:

```python
def violation(rule: str, *, target="#11", created_at="2026-09-03T11:00:00Z", cid=8001) -> dict:
    """A PROTOCOL-VIOLATION as claude's PM would have posted it. The author must be the configured bot or
    the parser treats it as an outsider's comment and it never reaches the board's headers."""
    return {"id": cid, "user": {"login": "meld-claude-bot"}, "created_at": created_at,
            "body": f"[claude:pm] PROTOCOL-VIOLATION {target}\nRefused `collect`: {rule}"}


def board_with(fake, ctx, extra):
    fake.route("GET", R + "issues/10/comments", fixture_comments() + extra)
    fake.route("POST", R + "issues/10/comments", {"id": 9001, "html_url": "u"})
    return build_status(ctx)[1]


def posts(fake):
    return [c for c in fake.calls if c.method == "POST" and c.endpoint.endswith("/comments")]


RULE = "only the PM may collect"


def test_an_identical_refusal_is_refused_without_a_second_comment(tmp_path):
    """Residual 14: 109 protocol violations on one coordination issue, most the same one re-derived."""
    fake, ctx = make(tmp_path, role="pm")
    board = board_with(fake, ctx, [violation(RULE)])
    before = len(posts(fake))

    with pytest.raises(Refused) as caught:
        refuse(ctx, board, Refusal(Verb.COLLECT, "#11", RULE))

    assert len(posts(fake)) == before                 # nothing new on the board
    assert caught.value.violation_posted is None      # skipped, distinct from posted (True) or failed (False)


def test_a_refusal_naming_a_different_rule_is_still_announced(tmp_path):
    fake, ctx = make(tmp_path, role="pm")
    board = board_with(fake, ctx, [violation(RULE)])

    with pytest.raises(Refused) as caught:
        refuse(ctx, board, Refusal(Verb.COLLECT, "#11", "worker is still running"))

    assert caught.value.violation_posted is True
    assert "worker is still running" in posts(fake)[-1].body["body"]


def test_a_refusal_about_a_different_target_is_still_announced(tmp_path):
    fake, ctx = make(tmp_path, role="pm")
    board = board_with(fake, ctx, [violation(RULE, target="#11")])

    with pytest.raises(Refused) as caught:
        refuse(ctx, board, Refusal(Verb.COLLECT, "#12", RULE))

    assert caught.value.violation_posted is True


def test_only_the_newest_violation_about_a_target_throttles(tmp_path):
    """A different rule posted since means the board moved on, so the earlier one is news again."""
    fake, ctx = make(tmp_path, role="pm")
    board = board_with(fake, ctx, [
        violation(RULE, created_at="2026-09-03T11:00:00Z", cid=8001),
        violation("worker is still running", created_at="2026-09-03T11:30:00Z", cid=8002),
    ])

    with pytest.raises(Refused) as caught:
        refuse(ctx, board, Refusal(Verb.COLLECT, "#11", RULE))

    assert caught.value.violation_posted is True


def test_a_human_still_never_posts_a_violation_at_all(tmp_path):
    """Unchanged behaviour, asserted here because the new branch sits next to it."""
    fake, ctx = make(tmp_path, group=None, role="pm")
    board = board_with(fake, ctx, [])
    before = len(posts(fake))

    with pytest.raises(Refused):
        refuse(ctx, board, Refusal(Verb.COLLECT, "#11", RULE))

    assert len(posts(fake)) == before
```

If `make(tmp_path, group=None, ...)` is not how that file produces a human actor, use whatever it does — `env_for(tmp_path, group=None)` omits `MELD_GROUP`, which is what makes the party human.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_actions.py -q -p no:cacheprovider
```

Expected: the first test fails because a comment is posted and `violation_posted` is `True`. Capture the real output.

- [ ] **Step 3: Write the implementation**

In `src/meld/actions.py`, add the predicate and consult it in `refuse`:

```python
def _already_said(board: Board, party: Party, target: str, rule: str) -> bool:
    """Is the newest PROTOCOL-VIOLATION from this party about this target already this rule?

    The board is the throttle, not a local file: two supervisors and a restart all read the same history,
    so GitHub's own record is the only place the count can be right (the `meld:nudged` argument, spec 14).
    Only the NEWEST matching violation counts — a different rule posted since means the topic moved.
    """
    for comment in sorted(board.comments, key=lambda c: (c.created_at, c.id), reverse=True):
        h = comment.header
        if h is None or h.verb != "PROTOCOL-VIOLATION" or h.party != party or h.ref != target:
            continue
        return rule in comment.text
    return False
```

and in `refuse`, between building the header and posting:

```python
    if board is not None and not actor.is_human:
        header = Header(actor.party, "PROTOCOL-VIOLATION", refusal.target, None)
        if _already_said(board, actor.party, refusal.target, refusal.rule):
            raise Refused(refusal, post_error=None, violation_posted=None)
        try:
            ctx.gh.create_comment(board.coordination_issue, compose(header, f"Refused `{refusal.verb.value}`: {refusal.rule}"))
            violation_posted = True
        except GitHubError as e:
            post_error = str(e)
            violation_posted = False
    raise Refused(refusal, post_error=post_error, violation_posted=violation_posted)
```

Only the two `_already_said` lines are new; everything below them is exactly what `refuse` does today.

Update `Refused`'s own docstring where it explains `violation_posted` (it documents `True` and `False` today) to name the third case: `None` means the board already carried this refusal and nothing was posted.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_actions.py -q -p no:cacheprovider
uv run pytest -q -p no:cacheprovider
```

Expected: PASS, whole suite green with no warnings. Watch for existing tests that assert a violation *was* posted: any that build a board already carrying the same violation will now correctly see none, and those are the tests to read carefully rather than adjust reflexively — if one breaks, decide whether its board was realistic.

- [ ] **Step 5: Commit**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/actions.py tests/test_actions.py
git -C /Users/scottbrant/code/meld-main commit -m "fix: an identical refusal is not announced twice

A PM re-derives the same intention from the board each launch and
re-issues a call the rules already refused; one live run accumulated 109
protocol violations, most of them the same one. The newest violation
from this party about this target is now the throttle, which is the same
argument that makes meld:nudged a label rather than a local file: two
supervisors and a restart all read one history."
```

---

### Task 5: doctor warns on free space, and the handoff records all three

**Files:**
- Modify: `src/meld/doctor.py` (a `disk-free` check)
- Modify: `docs/HANDOFF.md`
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `Config.logs_warn_free_mb` (Task 1).
- Produces: a `disk-free` check in `run_checks`, warning-only.

- [ ] **Step 1: Write the failing test**

`tests/test_doctor.py` already has `cfg()`, `fakes()`, `run(tmp_path, by_token, ...)` and `by_name(checks)`. `run_checks` takes no disk seam today, so add one: a `disk_usage=shutil.disk_usage` keyword, defaulted, which the test overrides. That keeps the check hermetic — no test may depend on the machine's actual free space, which on this machine has swung between 0 and 23 GB in one day.

```python
def test_disk_free_check_reports_space_and_warns_below_the_threshold(tmp_path):
    import collections
    usage = collections.namedtuple("usage", "total used free")

    plenty = by_name(run(tmp_path, fakes(), disk_usage=lambda p: usage(0, 0, 50 * 1024 * 1024 * 1024)))["disk-free"]
    assert plenty.ok is True and "51200 MB free" in plenty.detail and plenty.fix == ""

    scarce = by_name(run(tmp_path, fakes(), disk_usage=lambda p: usage(0, 0, 100 * 1024 * 1024)))["disk-free"]
    assert scarce.ok is None                      # a warning: low disk is a machine condition, not a misconfiguration
    assert "100 MB free" in scarce.detail
    assert "supervisor" in scarce.fix

    assert "disk-free" not in summary(run(tmp_path, fakes(),
                                          disk_usage=lambda p: usage(0, 0, 100 * 1024 * 1024)))["failed"]
```

Add `disk_usage=shutil.disk_usage` to that file's `run` helper's signature and pass it through, matching how `which` is already threaded.

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_doctor.py -q -p no:cacheprovider
```

Expected: `TypeError: run_checks() got an unexpected keyword argument 'disk_usage'`.

- [ ] **Step 3: Write the implementation**

Add the parameter to `run_checks`'s signature beside `which`:

```python
               disk_usage: Callable[[Path], object] = shutil.disk_usage,
```

and the check beside the `logs-size` one plan 8a added:

```python
    try:
        free_mb = disk_usage(home).free // (1024 * 1024)
    except OSError as e:
        checks.append(Check("disk-free", None, f"cannot read free space on {home}: {e}"))
    else:
        low = free_mb < cfg.logs_warn_free_mb
        checks.append(Check("disk-free", None if low else True, f"{free_mb} MB free on the volume holding {home}",
                            "a supervisor that cannot create a log file backs off rather than exiting, but every "
                            "launch will keep failing until there is room" if low else ""))
```

The fix text names the Task 3 behaviour deliberately: the operator should know the supervisor survives a full disk but makes no progress on one.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_doctor.py -q -p no:cacheprovider
uv run pytest -q -p no:cacheprovider
```

- [ ] **Step 5: Update the handoff**

In `docs/HANDOFF.md`:

- In the numbered live-run residual list, mark items **3**, **4** and **14** as landed in plan 9, in the style the existing entries use for landed items ("**Landed in plan N**: ..." with what actually shipped and any variation from what the item asked for). Item 3 asked for the adapter to recognise a limit and the runner to respect it, which is what shipped; item 4 asked for both the supervisor backoff and a doctor warning, and both shipped; item 14 asked that a refusal not be re-issued, and what shipped is narrower — the refusal still happens, it is just no longer announced twice. Say so plainly rather than claiming the wider fix.
- Add a `## Plan 9 — run reliability (landed)` section above `## What is next`, covering: the `usage_limit` kind and that recognition is per-adapter prose matching whose failure mode is the old behaviour; the freeze, where it is persisted, and that the supervisor posts `LIMITED` because the role is already gone; the launch backoff; the refusal throttle reading the board rather than local state; and the two new config knobs.
- **Record the honest caveat**: no captured limit transcript exists for either CLI, so the patterns come from prose observed in the first live run plus orgtree's tested set, and the next live run that hits a limit should capture the real text into `tests/fixtures/transcripts/` and tighten the patterns against it. This is the same shape of caveat plan 8a recorded for the streaming fixture, and for the same reason.
- Correct the stale line in `## What is next` that still describes plan 8a as sitting on `docs/plan-8-observability`; it merged as 8be02cf.

- [ ] **Step 6: Commit**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/doctor.py tests/test_doctor.py docs/HANDOFF.md
git -C /Users/scottbrant/code/meld-main commit -m "feat: doctor warns on free space, and the handoff records plan 9

The log cap bounds one run's log; nothing bounds the volume, which is
what actually ran out. The check warns rather than fails, because low
disk is a machine condition a person fixes. Residuals 3, 4 and 14 are
marked landed, with 14's narrower scope stated plainly: the refusal
still happens, it is only no longer announced twice."
```

---

## Self-review

**Spec coverage.** Every amendment in `73c831c` has a task. §7's `LIMITED` verb: Task 2. §17's three rows: Tasks 2, 3 and 4 respectively. §22's `usage_limit` kind and per-adapter recognition: Task 1. §5's `[runners] limit_retry_minutes` and `[logs] warn_free_mb`: Task 1 declares them, Tasks 2 and 5 consume them. §19's new bullet names five behaviours and each has a test: the freeze (Task 2), the persisted freeze surviving a restart (Task 2), the launch backoff leaving the other role alive (Task 3), the identical refusal (Task 4), and the free-space warning (Task 5).

**Nothing in the spec is left unimplemented, and nothing here exceeds it.** In particular this plan does not add a `meld doctor` probe of either CLI's login state, does not touch the 8b–8d viewer work, and does not attempt residuals 1, 2 or 5–13.

**Placeholder scan.** Clean: no "TBD", no "add error handling", no "similar to Task N", and every test body is complete code against a fixture that exists. Three blocks show an insertion into existing code rather than a whole function — the `_REASON_TEXT` entry, the two new lines in `refuse`, and the two new lines in the Claude adapter's `result` branch — and each says explicitly which lines are new and that the rest is unchanged. Every Python block in this plan parses; the limit patterns, the reset parsing and all fourteen of their test cases were executed before the plan was written.

**Type consistency.** `limit_of(log_path, adapter, now) -> str | None | False` is used with that three-way meaning in Task 2's tests, `_freeze`'s `resets_at` argument, and the exit branch. `freeze_path(home, group, role) -> Path` and `read_freeze(path) -> dict | None` match across Task 2's tests and helpers. The freeze payload's keys are `at`, `posted` and the optional `resets_at` in every place it is written or read. `is_limit(text) -> bool`, `reset_at(text, now) -> str | None` and `limit_event(now, text) -> list[Event]` match between `base.py`, both adapters, and Task 1's tests. `run_group`'s return value is `out["roles"][role][...]` throughout, matching what it already returns, plus the two keys Tasks 2 and 3 add (`reason`, `launch_errors`/`last_launch_error`).

**Two risks worth naming.** First, limit recognition is unverifiable without a real limit transcript, and Task 1's negative tests are the only guard against over-matching; the worst case is a healthy role frozen for `limit_retry_minutes`, which is recoverable and visible on the board, and the best case is the 45-minute silent storm never recurring. Second, Task 3 inverts `run_group`'s per-role loop from iterating live processes to iterating `ROLES`, which is the one structural change in this plan; the existing runner tests are the regression net, and Step 4 says explicitly to check that a healthy role's launch count did not move.
