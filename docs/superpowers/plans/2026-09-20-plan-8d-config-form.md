# Plan 8d: the configuration form — a canonical writer, a validated `PUT`, and the last route

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A person watching `meld watch` can change what the run is allowed to spend, wait, keep and retry — the budget, the concurrencies, the models and efforts, the timeouts, the log caps — from the page, with the file rewritten canonically and refused unless it parses back into exactly the configuration the form described and passes the checks `meld doctor` makes of a configuration.

**Architecture:** `config.py` gains the writer the standard library does not have — `dumps(cfg)` renders a `Config` as TOML in a fixed order under a header from a template, and `unknown_keys(data)` names anything in the parsed file the schema does not know. `doctor.py` gains `config_checks(cfg, registry, adapter_for)`: the subset of its checks that are a function of the configuration and the model catalogue alone, extracted so `run_checks` and the route share one definition. `viewer/server.py` answers `GET /api/config` with the file as parsed, the model catalogue and the path, and `PUT /api/config` by validating a candidate through the same parser the CLI uses, rendering it, reading it back, comparing, checking, and only then replacing the file through `write_atomic`. The page grows a form generated from the payload, so a field added to the schema appears without a line of new JavaScript.

**Tech Stack:** Python 3.13, standard library plus `click` only — `tomllib` reads and `config.dumps` writes, because the standard library has no TOML writer and a hand-written one for a known schema is smaller and safer than a dependency. No build step; the page stays three hand-written files.

**Spec:** `docs/superpowers/specs/2026-09-03-meld-design.md` — §22 "The configuration form" (the whole of it) and §22's routes table and Testing paragraph; §5 (every field the form writes, and the starter file `meld setup` writes); §4 (the layout and the verb list); §19; §21's exclusions (guided setup beyond this form is not in plan 8: App creation still goes through GitHub's browser flow). Task 1 amends §22 first; this plan implements those amendments and adds nothing they do not say.

## Global Constraints

- Python `>=3.12` (3.13 locally); runtime dependency `click>=8.1` only. Standard library plus click inside `src/meld/`. The page references nothing off the machine.
- **`config.py` is the only parser and now the only writer.** No module outside it may render a `.meld.toml` field, and `setup.starter_toml` — which writes a *starter* file with commented alternatives and placeholders, for a repo that has no configuration yet — is a different job and stays exactly as it is.
- **The viewer decides nothing about validity.** `Config.from_dict` decides whether a candidate parses; `doctor.config_checks` decides whether it is sane; the route reports what they said. A refusal is rendered verbatim, the way `/api/action`'s already is.
- The page acts as the `human` party and no other, `meld watch` refuses to start otherwise, and every string it shows reaches the document as a text node (`textContent`, never `innerHTML`).
- The token stays out of every log and every error body; no CORS header is ever sent; the body cap and the `Host` check apply to this route exactly as to the others.
- **A write never leaves the file worse than it found it.** `write_atomic` replaces through a temporary file; every refusal happens before it is called; the original is untouched on any failure.
- Tests hermetic and real: real files in `tmp_path`, the recorded board over `FakeGh`, every seam injected; no network, no real CLI, no wall clock. `uv run pytest -q` must be green with **no warnings** before every commit.
- Never set `MELD_SMOKE=1`.
- Conventional commit subjects, **no attribution trailers of any kind**, one logical change per commit; the repo-local identity `Scott Brant <421573+smidley@users.noreply.github.com>` is configured — check `git log -1 --format='%an <%ae>'` after the first commit of each task.
- Work in `/Users/scottbrant/code/meld-main` on branch `plan-8d-config-form` (off `main` at `197ee85`). `~/Documents/meld` is iCloud-synced and stalls git; do not work there. Commit with `git -C /Users/scottbrant/code/meld-main ...`.
- Baseline at this plan's base (`main` = `197ee85`, plan 8c merged): `uv run pytest -q` → **881 passed**, no warnings.
- **Test counts are measured, never predicted.** Each task says how many test functions it adds; the implementer runs the suite and records the printed total. (Plan 8b's three arithmetic slips came from predicting cumulative totals; plan 8c measured and had none.)

## Decisions (rulings made while writing the plan; recorded so executors do not re-litigate)

1. **`dumps` emits every field the schema defines, defaults included.** The alternative — writing only what differs from a default — needs a second table of defaults that can drift from `from_dict`'s, and makes the round-trip comparison depend on which fields were omitted. Emitting everything makes the round trip exact, makes the form honest (a field it shows is a field it writes), and makes the file self-documenting. The cost, which is real and must be said out loud in the docs: **the first save turns a hand-written minimal `.meld.toml` into the full explicit file, and any comment in it is lost.** The header template is re-rendered; nothing else is preserved.
2. **The request and the response are the parsed file, as JSON.** `GET` returns `tomllib.loads(text)` plus the model catalogue and the path; `PUT` takes the same nested object. That keeps one shape for both directions, lets the form send back what it was given with a few values changed, and makes "refuses any key it does not know" a walk over that object rather than a diff.
3. **An unknown key is refused by name, and `from_dict` is not the thing that refuses it.** `Config.from_dict` reads with `.get` and ignores what it does not know — which is right for a parser that must tolerate a newer file, and wrong for a form that must not silently drop a field a person typed. `config.unknown_keys(data)` walks the known schema and returns dotted paths; the route refuses with them.
4. **The doctor subset is `models:<group>` and `timeout-ceiling:<group>`, and nothing else.** Those two are a function of the configuration and the model catalogue alone. Everything else in `run_checks` needs the network, a binary, a token, or the machine — `gh-login`, `openssl`, `cli:*`, `cli-flags:*`, `app:*`, `app-installation:*`, `token:*`, `token-permissions:*`, `git-identity:*` (it asks GitHub for the bot's id), `settings-env`, `pth-hidden`, `memory-size`, `logs-size`, `disk-free`, `webhook`, `origin-https` — and a form must not fail because the network is down. `run_checks` and the route share one definition so the two can never drift.
5. **The form offers what a running product may change; identity is `meld setup`'s.** `[repo]`, `[humans]`, and a group's `bot`, `auth`, `token_env`, `app_id`, `installation_id` and key settings are rendered **read-only**: changing a group's identity mid-milestone orphans its claims and its open PRs, and the fix is `meld setup`, not a text box. The API still accepts them, so the round trip is lossless and a later plan can open them without a route change.
6. **A write does not reconfigure anything already running.** The file is what every new `meld` process reads; the supervisors and this page keep the configuration they started with. `GET` therefore reads the **file**, not `ctx.cfg`, so the form always shows what is on disk — but `/api/state`'s derived numbers (the budget cap, the warn thresholds) come from `ctx.cfg` and stay stale until the page is restarted. The response says so and the page shows it.
7. **A serializer that cannot reproduce its own input is a 500, not a 400.** If `from_dict(loads(dumps(cfg))) != cfg`, the person did nothing wrong: `dumps` has a bug. It is reported as one, with the fields that differ, and nothing is written.
8. **The form is generated from the payload, not hand-written.** One renderer over the scalar tables means a field added to `Config` appears in the form with no JavaScript change — and means the page cannot quietly stop offering something the schema gained.

## File structure

| File | Responsibility |
|---|---|
| `docs/superpowers/specs/2026-09-03-meld-design.md` | §22's routes table (drop the 8d-is-404 sentence) and its configuration-form paragraph (the JSON shape, the unknown-key refusal, the named doctor subset, what the form does and does not offer, the comment loss). Task 1. |
| `src/meld/config.py` | `HEADER`, `dumps(cfg) -> str`, `unknown_keys(data) -> list[str]`, and the `KNOWN` schema table they share. Task 2. |
| `src/meld/doctor.py` | `_group_config_checks(cfg, group, registry, adapter)` extracted, `config_checks(cfg, registry, adapter_for)` added, `run_checks` delegating to the same helper in the same place. Task 3. |
| `src/meld/viewer/server.py` | `api_config` (GET and PUT), `ROUTES`/`METHODS` entries, `Viewer(config_path=…)`. Task 4. |
| `src/meld/cli.py` | `watch` resolves and passes the config path. Task 4. |
| `src/meld/viewer/static/app.{html,css,js}` | The form panel, its renderer, and Save. Task 5. |
| `tests/test_config_write.py` (new) | Task 2. |
| `tests/test_doctor.py`, `tests/test_viewer.py`, `tests/test_cli.py` | Tasks 3, 4, 5. |
| `docs/HANDOFF.md`, `README.md` | Task 6. |

---

### Task 1: The spec says what this plan builds

**Files:**
- Modify: `docs/superpowers/specs/2026-09-03-meld-design.md` (the sentence under §22's routes table; §22's "### The configuration form" paragraph)

**Interfaces:** none; this task is the authority the rest of the plan argues from.

- [ ] **Step 1: Drop the 404 sentence.** Under §22's routes table, the line

```
`/api/config` is plan 8d; until it lands the server answers 404 for it, and the page offers no control for it.
```

is deleted entirely (with its surrounding blank line, so the table is followed by the "**Streams.**" paragraph).

- [ ] **Step 2: Rewrite the configuration-form paragraph.** Replace the whole of §22's `### The configuration form` body — the single paragraph beginning "`PUT /api/config` accepts the fields section 5 defines" — with these four paragraphs:

```
`GET /api/config` answers with the file as `tomllib` parsed it, the path it came from, and the model catalogue `models.toml` holds, so the form offers model and effort choices that cannot be wrong rather than rejecting them afterwards. `PUT /api/config` takes that same object back. One shape in both directions: the page sends what it was given with a few values changed, and a key the schema does not know is refused by name rather than dropped — `Config.from_dict` reads with defaults and ignores what it has never heard of, which is right for a parser reading a newer file and wrong for a form that must not swallow a field a person typed.

`tomllib` reads; the standard library cannot write TOML, so `config.py` gains a canonical serializer for the known schema. That asymmetry is a feature rather than a workaround: a form that can only emit named fields, in a fixed order, under a comment header re-rendered from a template, cannot corrupt a table it does not understand. It writes every field the schema defines, defaults included, because a form that shows a field and then does not write it is lying and because a round trip is only exact if nothing is omitted. The cost is plain and worth saying: the first save turns a hand-written minimal file into the full explicit one, and the comments in it are not preserved.

A write is refused unless the candidate parses into a `Config`, that `Config` renders and parses back into an equal one, and `meld doctor`'s configuration checks pass against it; only then does it replace the file through `atomic.write_atomic`, so a refusal at any step leaves the original untouched. The configuration checks are the ones that are a function of the configuration and the model catalogue alone — each group's `models:` and `timeout-ceiling:` — and `meld doctor` and this route share one definition of them, because a form that could pass a check the CLI fails would be a second opinion about a settled question. Everything else `meld doctor` does needs the network, a binary, a token, or the machine, and a form must not refuse a budget change because the network is down. A render that cannot reproduce its own input is reported as the serializer bug it is, naming the fields that differ, and writes nothing.

What the form offers is what a running product may change: the budgets, the concurrencies, the models and efforts, the timeouts, the polling and runner intervals, the log caps, the viewer's own port. A group's identity — its bot, its authentication, its token variable, its App and key settings — and `[repo]` and `[humans]` are shown and not editable: changing them mid-milestone orphans a group's claims and its open pull requests, and the remedy is `meld setup`, not a text box on a page. The route still accepts them, so the round trip is lossless and opening them later needs no new route. And a write reconfigures nothing already running: the file is what the next `meld` process reads, while the supervisors and the page itself keep the configuration they started with, which is why `GET` reads the file rather than the configuration this process is holding.
```

- [ ] **Step 3: Run the suite and commit.**

Run: `uv run pytest -q` — no test reads the spec; the run is the rule. It must print `881 passed` with no warnings.

```bash
git -C /Users/scottbrant/code/meld-main add docs/superpowers/specs/2026-09-03-meld-design.md
git -C /Users/scottbrant/code/meld-main commit -m "docs: section 22 describes the configuration form it is about to get"
```

---

### Task 2: `config.dumps` and `config.unknown_keys`

**Files:**
- Modify: `src/meld/config.py` (add `KNOWN`, `HEADER`, `dumps`, `unknown_keys`, and the small emitters they use)
- Test: `tests/test_config_write.py` (new)

**Interfaces:**
- Consumes: `Config`, `GroupConfig`, `RoleConfig`, `load_config`, `Config.from_dict`.
- Produces: `config.dumps(cfg: Config) -> str` — the canonical TOML for `cfg`, such that `Config.from_dict(tomllib.loads(dumps(cfg))) == cfg`; `config.unknown_keys(data: Mapping) -> list[str]` — sorted dotted paths for every key the schema does not define. Task 4 consumes both.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_config_write.py`:

```python
import tomllib

import pytest

from meld.config import Config, ConfigError, dumps, load_config, unknown_keys
from tests.test_config import MINIMAL


TABLES = """
[review]
quorum = 3
pingpong_cap = 5
security_paths = ["auth/**", "infra/**"]

[agreement]
amend_cap = 2

[polling]
interval_seconds = 15
wait_timeout_seconds = 240

[runners]
restart_hours = 6
roster_refresh_minutes = 30
silence_grace_minutes = 10
summary_cadence_minutes = 90
limit_retry_minutes = 45

[timeouts]
S = "10m"
M = "30m"
L = "2h"

[budget]
small = 100
medium = 40
large = 12
wallclock_hours = 36

[notify]
slack_dm_webhook_env = "MELD_SLACK_DM_WEBHOOK"
repeat_after_minutes = 90

[worker]
log_tail_lines = 80
max_turns = 150

[lease]
grace_minutes = 20

[audit]
batch_merges = 3
batch_hours = 2

[logs]
max_bytes_per_run = 4194304
warn_total_mb = 1024
warn_free_mb = 512

[viewer]
port = 8642
open_browser = true
"""



def minimal() -> dict:
    return tomllib.loads(MINIMAL)


def full() -> dict:
    """Every table set, and a group carrying every optional field. Built by editing the parsed minimal file
    rather than by appending TOML text: `MINIMAL` already declares `[groups.claude]`, and TOML forbids a table
    being declared twice, so a second block would not even parse."""
    data = tomllib.loads(MINIMAL + TABLES)
    data["groups"]["claude"].update({"concurrency": 3, "review_concurrency": 1,
                                     "git_name": 'a "quoted" \\ name', "git_email": "claude@example.invalid"})
    return data


def app() -> dict:
    """The codex group in App form: no token_env, an app id, an installation id, one key setting, a [bot] login."""
    data = tomllib.loads(MINIMAL)
    data["groups"]["codex"] = {**data["groups"]["codex"], "bot": "meld-codex[bot]", "auth": "app",
                               "app_id": 4911515, "installation_id": 160922345,
                               "private_key_file": "~/.meld/keys/meld-codex.pem"}
    data["groups"]["codex"].pop("token_env", None)
    return data


def roundtrip(data: dict) -> tuple[Config, Config]:
    """The config a parsed file describes, and the config its canonical rendering describes."""
    first = Config.from_dict(data)
    return first, Config.from_dict(tomllib.loads(dumps(first)))


@pytest.mark.parametrize("build, what", [(minimal, "a minimal file"), (full, "every field set"), (app, "an App group")])
def test_a_rendered_config_parses_back_into_an_equal_one(build, what):
    first, second = roundtrip(build())
    assert first == second, what


def test_the_round_trip_is_equal_field_by_field_not_merely_by_dataclass():
    """`Config.__eq__` would hide a field the renderer forgot if the parser's default happened to match. Compare
    every field by name, and the groups one attribute at a time, so a dropped field is named rather than missed."""
    first, second = roundtrip(full())
    for field in first.__dataclass_fields__:
        if field == "groups":
            continue
        assert getattr(first, field) == getattr(second, field), field
    assert sorted(first.groups) == sorted(second.groups)
    for name, group in first.groups.items():
        for field in group.__dataclass_fields__:
            assert getattr(group, field) == getattr(second.groups[name], field), f"groups.{name}.{field}"


def test_rendering_is_stable_and_carries_the_header():
    once = dumps(Config.from_dict(full()))
    assert once == dumps(Config.from_dict(tomllib.loads(once)))     # a second save changes nothing
    assert once.startswith("#")                                      # the template header
    assert "meld" in once.splitlines()[0]
    assert once.endswith("\n")


def test_every_table_the_parser_reads_is_a_table_the_renderer_writes():
    """The two halves of the schema must not drift: a table `from_dict` reads and `dumps` never writes would be a
    field the form silently resets to its default on the first save."""
    rendered = tomllib.loads(dumps(Config.from_dict(full())))
    for table in ("repo", "humans", "groups", "review", "agreement", "polling", "runners", "timeouts",
                  "budget", "notify", "worker", "lease", "audit", "logs", "viewer"):
        assert table in rendered, table


def test_a_written_file_loads(tmp_path):
    path = tmp_path / ".meld.toml"
    path.write_text(dumps(Config.from_dict(full())))
    assert load_config(path) == Config.from_dict(full())


def test_unknown_keys_names_what_the_schema_does_not_define():
    data = full()
    assert unknown_keys(data) == []
    data["review"]["quorumm"] = 2
    data["nonsense"] = {"a": 1}
    data["groups"]["claude"]["concurency"] = 4
    data["groups"]["claude"]["orchestrator"]["modell"] = "x"
    assert unknown_keys(data) == ["groups.claude.concurency", "groups.claude.orchestrator.modell",
                                  "nonsense", "review.quorumm"]


def test_unknown_keys_does_not_mistake_a_group_name_for_an_unknown_key():
    data = full()
    data["groups"]["nightly"] = dict(data["groups"]["claude"])
    assert unknown_keys(data) == []      # any group name is legal; its *fields* are what the schema fixes


def test_a_rendered_config_cannot_smuggle_a_quote_or_a_newline():
    """Every string the renderer writes came from a file a person edits, so a value containing a quote, a
    backslash or a newline must come back as itself rather than breaking out of its own literal."""
    data = minimal()
    data["groups"]["claude"]["git_name"] = 'a "quoted" \\ name\nwith a line break'
    cfg = Config.from_dict(data)
    assert Config.from_dict(tomllib.loads(dumps(cfg))).groups["claude"].git_name == cfg.groups["claude"].git_name
```

- [ ] **Step 2: Run them to verify they fail.**

Run: `uv run pytest tests/test_config_write.py -q`
Expected: FAIL — `ImportError: cannot import name 'dumps' from 'meld.config'`.

- [ ] **Step 3: Implement.** Add to `src/meld/config.py`, after `parse_duration` and before the `Config` dataclass:

```python
#: The header a rendered file carries. Re-rendered from this template on every write: the form owns the file it
#: writes, and a comment a person left in it does not survive a save (spec section 22).
HEADER = """# .meld.toml -- written by meld. Every field the schema defines is written, defaults included, so
# what you read here is what runs. `meld doctor` verifies it; `meld watch`'s form edits it.
"""

#: The known schema, table by table: the keys `Config.from_dict` reads. `unknown_keys` walks a parsed file
#: against this, because `from_dict` reads with `.get` and ignores what it does not know -- right for a parser
#: reading a newer file, wrong for a form that must not swallow a field somebody typed (spec section 22).
KNOWN: dict[str, tuple[str, ...]] = {
    "repo": ("name", "default_branch", "private"),
    "humans": ("accounts",),
    "review": ("quorum", "pingpong_cap", "security_paths"),
    "agreement": ("amend_cap",),
    "polling": ("interval_seconds", "wait_timeout_seconds"),
    "runners": ("restart_hours", "roster_refresh_minutes", "silence_grace_minutes", "summary_cadence_minutes",
                "limit_retry_minutes"),
    "timeouts": ("S", "M", "L"),
    "budget": ("small", "medium", "large", "wallclock_hours"),
    "notify": ("slack_dm_webhook_env", "repeat_after_minutes"),
    "worker": ("log_tail_lines", "max_turns"),
    "lease": ("grace_minutes",),
    "audit": ("batch_merges", "batch_hours"),
    "logs": ("max_bytes_per_run", "warn_total_mb", "warn_free_mb"),
    "viewer": ("port", "open_browser"),
}

#: A group's own keys, and a role's. `groups` is keyed by a name the product chooses, so only the fields are fixed.
KNOWN_GROUP = ("cli", "bot", "token_env", "concurrency", "review_concurrency", "git_name", "git_email",
               "auth", "app_id", "installation_id", "private_key_env", "private_key_file")
KNOWN_ROLE = ("model", "effort")


def unknown_keys(data: Mapping[str, Any]) -> list[str]:
    """Dotted paths for every key in a parsed `.meld.toml` the schema does not define, sorted.

    A group's *name* is the product's to choose, so `groups.<anything>` is legal and only its fields are checked.
    """
    out: list[str] = []
    for table, value in data.items():
        if table == "groups":
            if not isinstance(value, Mapping):
                out.append("groups")
                continue
            for name, group in value.items():
                if not isinstance(group, Mapping):
                    out.append(f"groups.{name}")
                    continue
                for key, sub in group.items():
                    if key in ("orchestrator", "pm"):
                        if isinstance(sub, Mapping):
                            out += [f"groups.{name}.{key}.{k}" for k in sub if k not in KNOWN_ROLE]
                        continue
                    if key not in KNOWN_GROUP:
                        out.append(f"groups.{name}.{key}")
            continue
        if table not in KNOWN:
            out.append(table)
            continue
        if isinstance(value, Mapping):
            out += [f"{table}.{k}" for k in value if k not in KNOWN[table]]
    return sorted(out)


def _s(value: str) -> str:
    """One TOML basic string. Escaped rather than interpolated: a git_name with a quote, a backslash or a newline
    must come back as itself instead of breaking out of its own literal."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{escaped}"'


def _list(values) -> str:
    return "[" + ", ".join(_s(str(v)) for v in values) + "]"


def _lines(pairs) -> list[str]:
    """`key = value` for each pair whose value is not None; None means the field is absent from this file."""
    out = []
    for key, value in pairs:
        if value is None:
            continue
        if isinstance(value, bool):
            out.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, int):
            out.append(f"{key} = {value}")
        elif isinstance(value, (list, tuple)):
            out.append(f"{key} = {_list(value)}")
        else:
            out.append(f"{key} = {_s(str(value))}")
    return out


def _group_lines(g: "GroupConfig") -> list[str]:
    """One group, in the order section 5 writes it: what it is, how it authenticates, what it may run."""
    out = [f"[groups.{g.name}]"] + _lines([
        ("cli", g.cli), ("bot", g.bot), ("token_env", g.token_env),
        ("auth", g.auth),      # written even when it is the default "pat": every field, or the promise is a lie
        ("app_id", g.app_id), ("installation_id", g.installation_id),
        ("private_key_env", g.private_key_env), ("private_key_file", g.private_key_file),
        ("concurrency", g.concurrency), ("review_concurrency", g.review_concurrency),
        ("git_name", g.git_name), ("git_email", g.git_email),
    ])
    for role_name, role in (("orchestrator", g.orchestrator), ("pm", g.pm)):
        out += ["", f"[groups.{g.name}.{role_name}]"] + _lines([("model", role.model), ("effort", role.effort)])
    return out


def dumps(cfg: Config) -> str:
    """`cfg` as canonical TOML: every field the schema defines, in a fixed order, under the header template.

    The standard library reads TOML and does not write it. Writing only the known schema is what makes this safe
    to point at a person's file: a table this does not understand cannot be mangled, because it is not emitted at
    all -- and `unknown_keys` is what stops such a table reaching here unnoticed in the first place.
    """
    blocks: list[list[str]] = [
        ["[repo]"] + _lines([("name", cfg.repo_name), ("default_branch", cfg.default_branch), ("private", cfg.private)]),
        ["[humans]"] + _lines([("accounts", cfg.humans)]),
    ]
    for name in sorted(cfg.groups):
        blocks.append(_group_lines(cfg.groups[name]))
    blocks += [
        ["[review]"] + _lines([("quorum", cfg.review_quorum), ("pingpong_cap", cfg.pingpong_cap),
                               ("security_paths", cfg.security_paths)]),
        ["[agreement]"] + _lines([("amend_cap", cfg.amend_cap)]),
        ["[polling]"] + _lines([("interval_seconds", cfg.poll_interval_seconds),
                                ("wait_timeout_seconds", cfg.wait_timeout_seconds)]),
        ["[runners]"] + _lines([("restart_hours", cfg.restart_hours),
                                ("roster_refresh_minutes", cfg.roster_refresh_minutes),
                                ("silence_grace_minutes", cfg.silence_grace_minutes),
                                ("summary_cadence_minutes", cfg.summary_cadence_minutes),
                                ("limit_retry_minutes", cfg.runners_limit_retry_minutes)]),
        ["[timeouts]"] + _lines([(size, cfg.timeouts[size]) for size in ("S", "M", "L")]),
        ["[budget]"] + _lines([("small", cfg.budget["small"]), ("medium", cfg.budget["medium"]),
                               ("large", cfg.budget["large"]), ("wallclock_hours", cfg.budget_wallclock_hours)]),
        ["[notify]"] + _lines([("slack_dm_webhook_env", cfg.notify_webhook_env),
                               ("repeat_after_minutes", cfg.notify_repeat_after_minutes)]),
        ["[worker]"] + _lines([("log_tail_lines", cfg.worker_log_tail_lines), ("max_turns", cfg.worker_max_turns)]),
        ["[lease]"] + _lines([("grace_minutes", cfg.lease_grace_minutes)]),
        ["[audit]"] + _lines([("batch_merges", cfg.audit_batch_merges), ("batch_hours", cfg.audit_batch_hours)]),
        ["[logs]"] + _lines([("max_bytes_per_run", cfg.logs_max_bytes_per_run),
                             ("warn_total_mb", cfg.logs_warn_total_mb), ("warn_free_mb", cfg.logs_warn_free_mb)]),
        ["[viewer]"] + _lines([("port", cfg.viewer_port), ("open_browser", cfg.viewer_open_browser)]),
    ]
    return HEADER + "\n" + "\n\n".join("\n".join(b) for b in blocks) + "\n"
```

`Mapping` and `Any` are already imported in `config.py`; check and add only what is missing.

- [ ] **Step 4: Run the focused tests, then the suite.**

Run: `uv run pytest tests/test_config_write.py -q` — 10 test functions, 3 of them parametrized into 3 cases, so 12 tests.
Then: `uv run pytest -q` — green, no warnings. **Record the printed total.**

If `test_every_table_the_parser_reads_is_a_table_the_renderer_writes` fails for `notify`, look at what your `_lines` did with a `None` webhook: the table is written, the key is not, and `tomllib` still reports the table — if it does not, the block is empty and TOML still parses `[notify]` as an empty table, which is what the assertion wants.

- [ ] **Step 5: Commit.**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/config.py tests/test_config_write.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: config.py writes the TOML it reads, and names a key it does not know"
```

---

### Task 3: `doctor.config_checks`

**Files:**
- Modify: `src/meld/doctor.py` (extract `_group_config_checks`, add `config_checks`, have `run_checks` call the same helper)
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `Check`, `ModelRegistry.validate`, `Adapter.command_timeout_ceiling_seconds`.
- Produces: `doctor.config_checks(cfg, registry, adapter_for) -> list[Check]` — the checks that are a function of the configuration and the model catalogue alone, two per group, in group order. Task 4 consumes it.

- [ ] **Step 1: Write the failing tests.** Add to `tests/test_doctor.py` (match the file's existing fixture idiom — read how its other tests build a `Config` and a registry before writing these):

```python
def test_config_checks_are_the_subset_that_needs_no_network_or_binary(tmp_path):
    """The form runs these against a candidate file, so they must not touch gh, a CLI, a token or the machine —
    and they must be the same checks `meld doctor` runs, not a second opinion."""
    from meld.doctor import config_checks
    cfg = load_config(write_config(tmp_path))        # the file's own helper; see its neighbours
    checks = config_checks(cfg, REGISTRY, adapter_for=lambda name: FakeAdapter())
    assert [c.name for c in checks] == ["models:claude", "timeout-ceiling:claude", "models:codex", "timeout-ceiling:codex"]
    assert all(c.ok is not False for c in checks)


def test_config_checks_catch_a_model_the_catalogue_does_not_have(tmp_path):
    from meld.doctor import config_checks
    cfg = load_config(write_config(tmp_path, orchestrator_model="gpt-imaginary"))
    checks = {c.name: c for c in config_checks(cfg, REGISTRY, adapter_for=lambda name: FakeAdapter())}
    assert checks["models:claude"].ok is False and "gpt-imaginary" in checks["models:claude"].detail


def test_config_checks_catch_a_wait_timeout_above_the_cli_ceiling(tmp_path):
    from meld.doctor import config_checks
    cfg = load_config(write_config(tmp_path, wait_timeout_seconds=99999))
    checks = {c.name: c for c in config_checks(cfg, REGISTRY, adapter_for=lambda name: FakeAdapter())}
    assert checks["timeout-ceiling:claude"].ok is False


def test_config_checks_skip_a_cli_with_no_adapter_rather_than_failing(tmp_path):
    """An unknown adapter is a `cli:` problem, which is `meld doctor`'s to report; the configuration subset says
    'skipped' so a form cannot refuse a budget change over a CLI this build cannot load."""
    from meld.adapters import UnknownAdapter
    from meld.doctor import config_checks

    def missing(name):
        raise UnknownAdapter(name)

    cfg = load_config(write_config(tmp_path))
    checks = {c.name: c for c in config_checks(cfg, REGISTRY, adapter_for=missing)}
    assert checks["timeout-ceiling:claude"].ok is None and "skipped" in checks["timeout-ceiling:claude"].detail
    assert checks["models:claude"].ok is True          # the catalogue still knows the model
```

**And one more test, which this plan cannot write for you.** The point of the extraction is that `meld doctor` and the form share one definition, so something must pin that `run_checks` still reports exactly what `config_checks` does. `tests/test_doctor.py` already has a harness that calls `run_checks` with its seams injected — find it (look for the test that asserts on the full check list) and add a test beside it, in that harness's idiom, named `test_run_checks_reports_the_same_configuration_checks_the_form_runs`. It asserts that for every group, the `models:<group>` and `timeout-ceiling:<group>` `Check` objects in `run_checks`'s output are **equal** to the ones `config_checks` returns for the same `cfg`, registry and `adapter_for` — `Check` is a dataclass, so `==` compares name, ok, detail and fix at once. If that harness makes an exact comparison awkward (it injects a different adapter, say), pin the weaker property — same names, in the same order, with the same `ok` — and say in your report which you did and why.

- [ ] **Step 2: Run them to verify they fail.**

Run: `uv run pytest tests/test_doctor.py -q -k config_checks`
Expected: FAIL — `ImportError: cannot import name 'config_checks' from 'meld.doctor'`.

- [ ] **Step 3: Implement.** In `src/meld/doctor.py`, extract the two checks from `run_checks`'s per-group loop into a module-level helper and add the public entry point:

```python
def _group_config_checks(cfg: Config, group: GroupConfig, registry: ModelRegistry, adapter) -> list[Check]:
    """The two checks that are a function of the configuration and the model catalogue alone: the models and
    efforts this group asks for, and whether its CLI would even let a worker wait as long as polling does.

    Split out because `meld watch`'s configuration form runs exactly these against a candidate file before it is
    written (spec section 22), and a form that could pass a check the CLI fails would be a second opinion about a
    settled question. `adapter` is None when this build has no adapter for the group's `cli` -- that is a `cli:`
    problem for `run_checks` to report, so the ceiling is skipped rather than failed.
    """
    problems = []
    for role_name, role in (("orchestrator", group.orchestrator), ("pm", group.pm)):
        problems += [f"{role_name}: {p}" for p in registry.validate(group.cli, role.model, role.effort)]
    checks = [Check(f"models:{group.name}", not problems,
                    "; ".join(problems) or f"{group.orchestrator.model}/{group.orchestrator.effort}, {group.pm.model}/{group.pm.effort}",
                    "" if not problems else "pick models from `meld models`")]
    if adapter is None:
        checks.append(Check(f"timeout-ceiling:{group.name}", None, "skipped (unknown cli)"))
    else:
        ceiling = adapter.command_timeout_ceiling_seconds()
        ok = ceiling > cfg.wait_timeout_seconds
        checks.append(Check(f"timeout-ceiling:{group.name}", ok,
                            f"command ceiling {ceiling}s vs polling.wait_timeout_seconds {cfg.wait_timeout_seconds}s",
                            "" if ok else "lower polling.wait_timeout_seconds below the CLI's command ceiling"))
    return checks


def config_checks(cfg: Config, registry: ModelRegistry, adapter_for) -> list[Check]:
    """Everything `meld doctor` can say about a configuration without the network, a binary, a token or the
    machine. `meld watch`'s form runs these against a candidate before writing it."""
    out: list[Check] = []
    for group in cfg.groups.values():
        try:
            adapter = adapter_for(group.cli)
        except UnknownAdapter:
            adapter = None
        out += _group_config_checks(cfg, group, registry, adapter)
    return out
```

Then in `run_checks`, replace the bodies of steps 5 and 6 (`# 5. models` through the `timeout-ceiling` append) with `checks += _group_config_checks(cfg, group, registry, adapter)`, leaving everything around them — and the order of the output — exactly as it was. `UnknownAdapter` is already imported there if `run_checks` resolves adapters; check and add the import if not.

- [ ] **Step 4: Run the focused tests, then the suite.**

Run: `uv run pytest tests/test_doctor.py -q`
Then: `uv run pytest -q` — green, no warnings. **Record the printed total.** Every pre-existing doctor test must pass untouched; if one fails on check order, the extraction moved something — put it back rather than editing the test.

- [ ] **Step 5: Commit.**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/doctor.py tests/test_doctor.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: doctor names the checks a configuration alone can fail"
```

---

### Task 4: `GET` and `PUT /api/config`

**Files:**
- Modify: `src/meld/viewer/server.py` (`ROUTES`, `METHODS`, `Viewer.__init__`'s `config_path`, `api_config`)
- Modify: `src/meld/cli.py` (`watch` resolves the config path and passes it)
- Test: `tests/test_viewer.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `config.dumps`, `config.unknown_keys`, `Config.from_dict`, `ConfigError`, `doctor.config_checks`, `atomic.write_atomic`, `ModelRegistry.for_adapter`.
- Produces: `GET /api/config` → `{"path", "config", "models", "note"}`; `PUT /api/config` → 200 `{"ok": true, "config"}`, 400 `{"error"}`, 409 `{"error", "checks"}`, 500 `{"error", "differs"}`; `Viewer(…, config_path: Path | None = None)`. Task 5 consumes both shapes.

- [ ] **Step 1: Write the failing tests.** Add to `tests/test_viewer.py` (it already has `viewer_for`, `get`, `body`, `TOKEN`; `viewer_for` writes `PROJECT_TOML` to `tmp_path / ".meld.toml"`, so give `Viewer` that path — add `config_path=tmp_path / ".meld.toml"` to `viewer_for`'s default kwargs):

```python
def put(v, payload, **kw):
    from meld.viewer.server import Request
    target = f"/api/config?t={kw.pop('token', TOKEN)}"
    headers = {"Host": kw.pop("host", "127.0.0.1:8765"), "Content-Type": "application/json"}
    return v.handle(Request.parse("PUT", target, headers, json.dumps(payload).encode()))


def test_api_config_returns_the_file_the_path_and_the_model_catalogue(tmp_path):
    v, ctx, _ = viewer_for(tmp_path)
    out = body(get(v, "/api/config"))
    assert out["path"] == str(tmp_path / ".meld.toml")
    assert out["config"]["repo"]["name"] == ctx.cfg.repo_name
    assert out["config"]["groups"]["claude"]["orchestrator"]["model"] == ctx.cfg.groups["claude"].orchestrator.model
    assert out["models"]["claude"] and all({"id", "efforts", "tier"} <= set(m) for m in out["models"]["claude"])
    assert "note" in out                                   # a write reconfigures nothing already running


def test_a_put_writes_the_file_canonically_and_answers_with_what_it_wrote(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    current = body(get(v, "/api/config"))["config"]
    current["budget"] = {**current.get("budget", {}), "large": 26}
    resp = put(v, {"config": current})
    assert resp.status == 200, body(resp)
    written = (tmp_path / ".meld.toml").read_text()
    assert written.startswith("#") and "large = 26" in written
    assert load_config(tmp_path / ".meld.toml").budget["large"] == 26
    assert body(resp)["config"]["budget"]["large"] == 26       # the answer is the file, re-read
    assert body(get(v, "/api/config"))["config"]["budget"]["large"] == 26


def test_a_put_refuses_a_key_the_schema_does_not_know_and_names_it(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    current = body(get(v, "/api/config"))["config"]
    before = (tmp_path / ".meld.toml").read_text()
    current["review"]["quorumm"] = 9
    resp = put(v, {"config": current})
    assert resp.status == 400 and "review.quorumm" in body(resp)["error"]
    assert (tmp_path / ".meld.toml").read_text() == before      # the original is untouched


def test_a_put_refuses_a_config_the_parser_rejects(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    current = body(get(v, "/api/config"))["config"]
    before = (tmp_path / ".meld.toml").read_text()
    del current["groups"]["claude"]["cli"]
    resp = put(v, {"config": current})
    assert resp.status == 400 and "cli" in body(resp)["error"]
    assert (tmp_path / ".meld.toml").read_text() == before


def test_a_put_refuses_a_config_the_doctor_checks_reject(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    current = body(get(v, "/api/config"))["config"]
    before = (tmp_path / ".meld.toml").read_text()
    current["groups"]["claude"]["orchestrator"]["model"] = "gpt-imaginary"
    resp = put(v, {"config": current})
    assert resp.status == 409
    out = body(resp)
    assert any(c["name"] == "models:claude" and c["ok"] is False for c in out["checks"])
    assert "gpt-imaginary" in json.dumps(out["checks"])
    assert (tmp_path / ".meld.toml").read_text() == before


def test_a_put_needs_a_json_object_with_a_config(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    for payload in ({}, {"config": None}, {"config": []}, {"config": {}}):
        assert put(v, payload).status == 400, payload
    from meld.viewer.server import Request
    bad = v.handle(Request.parse("PUT", f"/api/config?t={TOKEN}", {"Host": "127.0.0.1:8765"}, b"not json"))
    assert bad.status == 400


def test_the_config_route_is_behind_the_gate_and_takes_only_get_and_put(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    assert get(v, "/api/config", token=None).status == 403
    assert get(v, "/api/config", host="evil.example:8765").status == 403
    assert get(v, "/api/config", method="POST").status == 405
    assert get(v, "/api/config").status == 200


def test_a_successful_put_drops_the_cached_state(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    assert get(v, "/api/state").status == 200 and v._state_cache is not None
    current = body(get(v, "/api/config"))["config"]
    current["budget"] = {**current.get("budget", {}), "large": 21}
    assert put(v, {"config": current}).status == 200
    assert v._state_cache is None
```

Add `from meld.config import load_config` to the file's imports if it is not already there.

In `tests/test_cli.py`, extend the `_watch` helper's assertions with one test:

```python
def test_watch_hands_the_viewer_the_config_file_it_loaded(tmp_path, monkeypatch):
    result, seen, _ = _watch(tmp_path, monkeypatch, [])
    assert result.exit_code == 0, result.output
    assert seen["viewer"].config_path == tmp_path / ".meld.toml"
```

- [ ] **Step 2: Run them to verify they fail.**

Run: `uv run pytest tests/test_viewer.py tests/test_cli.py -q -k config`
Expected: FAIL — `assert 404 == 200` for the GET (no route yet).

- [ ] **Step 3: Implement the route.** In `src/meld/viewer/server.py`, add to the imports:

```python
from meld.atomic import write_atomic
from meld.config import Config, ConfigError, dumps, unknown_keys
from meld.doctor import config_checks
```

Give `Viewer.__init__` a `config_path: Path | None = None` keyword, stored as `self.config_path`. Add the route and the method:

```python
    ROUTES: dict[str, str] = {"/": "page", "/static/app.css": "asset_css", "/static/app.js": "asset_js",
                              "/api/state": "api_state", "/api/streams": "api_streams", "/api/events": "api_events",
                              "/api/action": "api_action", "/api/config": "api_config"}
    #: The routes that are not plain GETs. `/api/config` is the one route that answers two methods.
    METHODS: dict[str, str] = {"/api/action": "POST"}
```

and, because `/api/config` takes two methods, change `handle`'s method check to allow a set:

```python
        allowed = self.METHODS.get(req.path, "GET")
        if req.method not in ({allowed} if isinstance(allowed, str) else set(allowed)):
            return _json(405, {"error": f"{req.method} is not allowed on {req.path}"})
```

with `METHODS["/api/config"] = ("GET", "PUT")` added to the table.

Then the route, after `api_action`:

```python
    #: What a write does not do. Said in the answer because it is the question a person asks next.
    CONFIG_NOTE = ("saved to the file; the supervisors and this page keep the configuration they started with, "
                   "and every new meld process reads the new one")

    def api_config(self, req: Request) -> Response:
        """The `.meld.toml` as parsed, and a validated write of it (spec 22).

        The viewer decides nothing about validity: `Config.from_dict` decides whether a candidate parses,
        `doctor.config_checks` decides whether it is sane, and this reports what they said. Every refusal happens
        before `write_atomic`, so a rejected write leaves the file exactly as it was.
        """
        if self.config_path is None:
            return _json(502, {"error": "this viewer was started without a configuration file"})
        if req.method == "GET":
            return self._read_config()
        try:
            body = json.loads(req.body or b"")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return _json(400, {"error": f"the request body is not JSON: {e}"})
        candidate = body.get("config") if isinstance(body, dict) else None
        if not isinstance(candidate, dict) or not candidate:
            return _json(400, {"error": "the request body must be a JSON object with a non-empty 'config'"})
        unknown = unknown_keys(candidate)
        if unknown:
            # `from_dict` would ignore these; a form must not swallow a field somebody typed.
            return _json(400, {"error": f"unknown setting(s): {', '.join(unknown)}"})
        try:
            cfg = Config.from_dict(candidate)
        except (ConfigError, AttributeError, TypeError) as e:
            # AttributeError/TypeError: a "table" that is not one (`review = 5`) makes `from_dict`'s own `.get`
            # raise rather than refuse. `unknown_keys` cannot see it either, since it only walks mappings. It is
            # still the candidate's problem, so it is a 400 naming what went wrong, never a traceback.
            return _json(400, {"error": f"{type(e).__name__}: {e}"})
        text = dumps(cfg)
        try:
            reparsed = Config.from_dict(tomllib.loads(text))
        except (ConfigError, tomllib.TOMLDecodeError) as e:
            return _json(500, {"error": f"the rendered file does not parse: {e}"})
        if reparsed != cfg:
            differs = sorted(f for f in cfg.__dataclass_fields__ if getattr(cfg, f) != getattr(reparsed, f))
            return _json(500, {"error": "the rendered file does not describe the same configuration", "differs": differs})
        checks = config_checks(cfg, self.ctx.models, self.ctx.adapter_for)
        if any(c.ok is False for c in checks):
            return _json(409, {"error": "meld doctor's configuration checks refuse this", "checks": [asdict(c) for c in checks]})
        try:
            write_atomic(self.config_path, text)
        except OSError as e:
            return _json(502, {"error": f"cannot write {self.config_path}: {e}"})
        with self._lock:
            self._state_cache = None      # the numbers /api/state derives from the old config are stale now
        return self._read_config(ok=True)

    def _read_config(self, *, ok: bool = False) -> Response:
        try:
            parsed = tomllib.loads(self.config_path.read_text())
        except (OSError, tomllib.TOMLDecodeError) as e:
            return _json(502, {"error": f"cannot read {self.config_path}: {e}"})
        models = {adapter: [{"id": s.id, "efforts": list(s.efforts), "tier": s.tier, "context_class": s.context_class}
                            for s in self.ctx.models.for_adapter(adapter)]
                  for adapter in self.ctx.models.adapters()}
        payload = {"path": str(self.config_path), "config": parsed, "models": models, "note": self.CONFIG_NOTE}
        return _json(200, {**payload, "ok": True} if ok else payload)
```

`import tomllib` and `from dataclasses import asdict` join the module's imports.

- [ ] **Step 4: Implement the CLI side.** In `cli.py`'s `watch`, resolve the path the same way `tail` does and pass it:

```python
    config_path = ctx.obj["config_path"] or _guard(lambda: find_config(Path.cwd()))
    viewer = Viewer(c, token=new_token(), port=0, config_path=config_path)
```

- [ ] **Step 5: Run the focused tests, then the suite.**

Run: `uv run pytest tests/test_viewer.py tests/test_cli.py -q`
Then: `uv run pytest -q` — green, no warnings. **Record the printed total.**

- [ ] **Step 6: Commit.**

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/viewer/server.py src/meld/cli.py tests/test_viewer.py tests/test_cli.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: the viewer reads the configuration file and writes a validated one"
```

---

### Task 5: The form

**Files:**
- Modify: `src/meld/viewer/static/app.html` (a config panel), `app.css` (its layout), `app.js` (`loadConfig`, `renderConfig`, `saveConfig`)
- Test: `tests/test_viewer.py`

**Interfaces:**
- Consumes: Task 4's `GET`/`PUT /api/config` shapes.
- Produces: nothing Python consumes; the page is tested for what Python can see.

- [ ] **Step 1: Write the failing test.** Add to `tests/test_viewer.py`:

```python
def test_the_page_has_a_config_form_that_reads_and_writes_the_known_schema(tmp_path):
    v, _, _ = viewer_for(tmp_path)
    html, js = get(v, "/").body.decode(), get(v, "/static/app.js").body.decode()
    assert 'id="config"' in html and 'id="config-fields"' in html and 'id="config-save"' in html
    assert '"/api/config"' in js and 'method: "PUT"' in js
    # identity is shown and not editable (spec 22): the form never turns these into inputs
    assert "READONLY_TABLES" in js and '"repo"' in js and '"humans"' in js
    assert "READONLY_GROUP_FIELDS" in js
    for field in ("bot", "auth", "token_env", "app_id", "installation_id", "private_key_env", "private_key_file"):
        assert f'"{field}"' in js
    # the form is generated, not hand-written: no per-field id list
    assert "config.models" in js or "payload.models" in js       # model choices come from the catalogue
    assert "innerHTML" not in js
```

- [ ] **Step 2: Run it to verify it fails.**

Run: `uv run pytest tests/test_viewer.py -q -k config_form`
Expected: FAIL — `assert 'id="config"' in html`.

- [ ] **Step 3: `app.html`.** After the `</form>` closing `#bar`, add:

```html
<details id="config">
  <summary>Configuration</summary>
  <div id="config-fields"></div>
  <div class="row">
    <button id="config-save" type="button">Save</button>
    <span id="config-result" class="dim"></span>
  </div>
</details>
```

- [ ] **Step 4: `app.css`.** Append:

```css
#config { background: var(--panel); border-bottom: 1px solid var(--line); padding: 4px 8px; }
#config > summary { cursor: pointer; padding: 2px 0; }
#config-fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(18rem, 1fr)); gap: 2px 10px;
  max-height: 34vh; overflow-y: auto; padding: 4px 0; }
#config-fields h3 { grid-column: 1 / -1; margin: 6px 0 0; font-size: 13px; color: var(--accent); }
#config-fields label { display: grid; grid-template-columns: 11rem 1fr; gap: 6px; align-items: baseline; }
#config-fields input, #config-fields select { font: inherit; color: var(--fg); background: #222735;
  border: 1px solid var(--line); border-radius: 3px; padding: 1px 4px; min-width: 0; }
#config-fields input[readonly] { color: var(--dim); background: transparent; border-color: transparent; }
#config-result.bad { color: var(--bad); }
#config-result.ok { color: var(--ok); }
```

- [ ] **Step 5: `app.js`.** Add near the constants:

```javascript
// Identity is `meld setup`'s, not a running page's: changing a repo, a human, or a group's bot/auth mid-milestone
// orphans that group's claims and its open PRs. Shown, never editable (spec 22). The API still accepts them, so
// what the form sends back is what it was given, with only the editable values replaced.
const READONLY_TABLES = ["repo", "humans"];
const READONLY_GROUP_FIELDS = ["cli", "bot", "auth", "token_env", "app_id", "installation_id",
                               "private_key_env", "private_key_file"];
let configState = null;     // {path, config, models, note} as last read, and the inputs' backing store
```

and the renderer and saver, after `runAction`:

```javascript
function field(into, path, value, { readonly = false, choices = null } = {}) {
  // One control per scalar, generated from the payload: a field the schema gains appears here without a line
  // of JavaScript. A list becomes a comma-separated text box; a table is walked, never rendered as a value.
  const label = document.createElement("label");
  label.append(text(document.createElement("span"), path));
  let input;
  if (choices) {
    input = document.createElement("select");
    options(input, choices.includes(value) ? choices : [value, ...choices]);
    input.value = value;
  } else if (typeof value === "boolean") {
    input = document.createElement("input");
    input.type = "checkbox";
    input.checked = value;
  } else {
    input = document.createElement("input");
    input.type = typeof value === "number" ? "number" : "text";
    input.value = Array.isArray(value) ? value.join(", ") : String(value);
  }
  input.dataset.path = path;
  input.dataset.kind = Array.isArray(value) ? "list" : typeof value;
  if (readonly) { input.readOnly = true; input.disabled = choices != null || typeof value === "boolean"; }
  label.append(input);
  into.append(label);
}

function modelChoices(cli) {
  const specs = (configState.models || {})[cli] || [];
  return specs.map((s) => s.id);
}

function effortChoices(cli, model) {
  const spec = ((configState.models || {})[cli] || []).find((s) => s.id === model);
  return spec ? spec.efforts : [];
}

function renderConfig() {
  const into = el("config-fields");
  into.replaceChildren();
  const cfg = configState.config;
  for (const [table, values] of Object.entries(cfg)) {
    if (table === "groups") continue;
    into.append(text(document.createElement("h3"), `[${table}]`));
    for (const [key, value] of Object.entries(values)) {
      field(into, `${table}.${key}`, value, { readonly: READONLY_TABLES.includes(table) });
    }
  }
  for (const [name, group] of Object.entries(cfg.groups || {})) {
    into.append(text(document.createElement("h3"), `[groups.${name}]`));
    for (const [key, value] of Object.entries(group)) {
      if (key === "orchestrator" || key === "pm") continue;
      field(into, `groups.${name}.${key}`, value, { readonly: READONLY_GROUP_FIELDS.includes(key) });
    }
    for (const role of ["orchestrator", "pm"]) {
      const r = group[role];
      if (!r) continue;
      field(into, `groups.${name}.${role}.model`, r.model, { choices: modelChoices(group.cli) });
      field(into, `groups.${name}.${role}.effort`, r.effort, { choices: effortChoices(group.cli, r.model) });
    }
  }
}

function collectConfig() {
  // Start from what the server gave us and replace only what the form shows, so a table the form does not
  // render -- today none, tomorrow whatever the schema gains before this page does -- rides through untouched.
  const out = JSON.parse(JSON.stringify(configState.config));
  for (const input of el("config-fields").querySelectorAll("[data-path]")) {
    if (input.readOnly || input.disabled) continue;
    const parts = input.dataset.path.split(".");
    let node = out;
    for (const part of parts.slice(0, -1)) node = node[part];
    const key = parts[parts.length - 1];
    if (input.dataset.kind === "boolean") node[key] = input.checked;
    else if (input.dataset.kind === "number") node[key] = Number(input.value);
    else if (input.dataset.kind === "list") node[key] = input.value.split(",").map((s) => s.trim()).filter(Boolean);
    else node[key] = input.value;
  }
  return out;
}

function showConfigResult(message, ok) {
  const node = el("config-result");
  node.classList.remove("ok", "bad");
  node.classList.add(ok ? "ok" : "bad");
  text(node, message);
  node.title = message;
}

async function loadConfig() {
  try {
    configState = await getJSON("/api/config");
    renderConfig();
    showConfigResult(`${configState.path} — ${configState.note}`, true);
  } catch (e) {
    showConfigResult(e.message, false);
  }
}

async function saveConfig() {
  if (!configState) return;
  const button = el("config-save");
  button.disabled = true;
  showConfigResult("saving …", true);
  try {
    const response = await fetch(api("/api/config"), {
      method: "PUT", cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config: collectConfig() }),
    });
    let out = {};
    try { out = await response.json(); } catch (e) { out = {}; }
    if (response.ok) {
      configState = out;
      renderConfig();
      showConfigResult(`saved — ${out.note}`, true);
      pollState();
    } else if (out.checks) {
      const bad = out.checks.filter((c) => c.ok === false).map((c) => `${c.name}: ${c.detail}`).join("; ");
      showConfigResult(`refused — ${bad}`, false);
    } else {
      showConfigResult(`${response.status} ${out.error || ""}`, false);
    }
  } catch (e) {
    showConfigResult(e.message, false);
  } finally {
    button.disabled = false;
  }
}
```

and the wiring, beside the existing bar wiring:

```javascript
el("config").addEventListener("toggle", () => { if (el("config").open && !configState) loadConfig(); });
el("config-save").addEventListener("click", saveConfig);
```

(the form loads the first time it is opened, not on every page load, because the page's job is watching.)

- [ ] **Step 6: Read `app.js` once as a program** and write the traces in your report: a `GET` payload whose `groups.codex` is an App group (which fields render readonly, and does `auth` appear?); a model `<select>` whose current value is not in the catalogue (the `choices.includes(value)` branch); `collectConfig` on an untouched form (does it reproduce the input exactly — check a bool, a number, a list and a string); a save that answers 409 with checks; a save that answers 400. Confirm `options()` is the helper the action bar already uses, that `field`'s readonly `<select>`/checkbox are actually non-editable (`readOnly` does nothing on either — hence the `disabled`), and that nothing here uses `innerHTML`.

- [ ] **Step 7: Run the suite and commit.**

Run: `uv run pytest -q` — green, no warnings; **record the printed total**.

```bash
git -C /Users/scottbrant/code/meld-main add src/meld/viewer/static tests/test_viewer.py
git -C /Users/scottbrant/code/meld-main commit -m "feat: the page edits the configuration it is watching"
```

---

### Task 6: The handoff and the README

**Files:**
- Modify: `docs/HANDOFF.md` (the banner; the `viewer/` and `config.py` map bullets; a "Plan 8d" section; plan 8c's "Still open" line; "What is next")
- Modify: `README.md` (the `meld watch` row; the "Watching a run" paragraph)

**Interfaces:** none; documentation of Tasks 1–5 as landed.

- [ ] **Step 1: The banner.** Rewrite it so plan 8c is the last plan merged to `main` (PR #24, merge commit `197ee85`), plan 8d is this branch, `plan-8d-config-form`, and the suite total is **what you measured**, not a number copied from this plan.

- [ ] **Step 2: The map.** Extend the `config.py` bullet with `dumps`/`unknown_keys`/`KNOWN` (the writer the standard library lacks; every field written, defaults included; a key the schema does not know refused by name), the `doctor.py` bullet with `config_checks`/`_group_config_checks` (the subset that is a function of the configuration and the catalogue alone, shared with `run_checks` so the two cannot drift), and the `viewer/` bullet with `GET`/`PUT /api/config` and its refusal ladder (unknown key → 400, parser → 400, render mismatch → 500, checks → 409, write failure → 502, and `write_atomic` last so a refusal leaves the file untouched).

- [ ] **Step 3: The new section.** Before `## Plan 9 — run reliability (landed)`, add `## Plan 8d — the configuration form (landed)` covering: what the form edits and what it only shows (and why identity is `meld setup`'s); the three gates a write passes; that a save reconfigures nothing already running, so the supervisors and the page keep what they started with; and — plainly — that **the first save rewrites the file canonically and does not preserve comments**. Then **Decisions for the owner** (every field written including defaults; comments lost; identity read-only; the doctor subset being two checks; a render mismatch being a 500) and **Known follow-ups** read out of `.superpowers/sdd/2026-09-20-plan-8d-config-form/progress.md`'s `minor (deferred)` lines, plus: `/api/state`'s derived numbers stay stale until the page restarts; nothing reloads a running supervisor; `setup.starter_toml` and `dumps` are two renderers of the same schema and could drift.

- [ ] **Step 4: Plan 8c's "Still open" line and "What is next".** 8c's section should now say plan 8 is complete. "What is next" loses the 8d bullet and gains what actually remains — spec §21's carried items and the plan-7 residuals — and should say that **plan 8 is finished**: transcripts, the viewer, the human verbs and the form.

- [ ] **Step 5: README.** The `meld watch` row gains the form (it edits the budgets, concurrencies, models, efforts, timeouts and log caps; identity is shown and not editable; a save rewrites the file canonically and drops comments). Add one sentence to "Watching a run" saying the same in passing.

- [ ] **Step 6: Run the suite and commit.**

Run: `uv run pytest -q` — the same count as Task 5, no warnings.

```bash
git -C /Users/scottbrant/code/meld-main add docs/HANDOFF.md README.md
git -C /Users/scottbrant/code/meld-main commit -m "docs: the handoff and README describe the configuration form"
```

---

## Self-review

**Spec coverage.** §22's configuration-form paragraph, as Task 1 rewrites it: `GET` returns the parsed file, the path and the catalogue (Task 4); `PUT` takes the same object (Task 4); an unknown key is refused by name (Task 2's `unknown_keys`, Task 4's 400); the canonical serializer lives in `config.py` and writes the known schema in a fixed order under a template header (Task 2); a write is refused unless the candidate parses, round-trips and passes the configuration checks, and only then replaces through `write_atomic` (Task 4); the checks are shared with `meld doctor` (Task 3); model choices come from `models.toml` (Task 4's payload, Task 5's selects); identity is shown and not editable (Task 5); a write reconfigures nothing running (Task 4's note, Task 6's docs). §22's routes table drops the 404 sentence (Task 1). §19 already names `test_config_write.py`, which Task 2 creates — no §19 change needed; confirm that before assuming it. §21: nothing here builds guided setup or App creation.

**Placeholder scan.** No TBD/TODO. Task 3's last test is deliberately specified as "write it against the file's existing harness, and if that is awkward pin the weaker property and say so" — that is an instruction with a decision rule, not a placeholder, because `tests/test_doctor.py`'s fixtures are not visible from here. Task 6's section content is enumerated rather than written out because it must quote the deferred minors, which do not exist yet.

**Type consistency.** `dumps(cfg) -> str` and `unknown_keys(data) -> list[str]` are called exactly that in Tasks 2 and 4. `config_checks(cfg, registry, adapter_for)` — same three arguments in Task 3's definition, its tests, and Task 4's call (`self.ctx.models`, `self.ctx.adapter_for`). `Check`'s fields (`name`, `ok`, `detail`, `fix`) are what Task 4 serializes with `asdict` and Task 5 reads as `c.name`/`c.ok`/`c.detail`. The `GET` payload's four keys (`path`, `config`, `models`, `note`) are what Task 5's `configState` reads, and `models[adapter][n]` has `id`/`efforts`/`tier`/`context_class` in both. `METHODS` becomes a table of strings *or* tuples, which Task 4's `handle` change accounts for — the existing `"/api/action": "POST"` entry keeps working unchanged.
