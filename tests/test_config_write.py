import ast
import inspect
import textwrap
import tomllib

import pytest

from meld import config as config_module
from meld.config import (KNOWN, KNOWN_GROUP, KNOWN_ROLE, Config, ConfigError, dotted_key, dumps, load_config,
                         unknown_keys)
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
    being declared twice, so a second block would not even parse.

    `repo.default_branch` and `repo.private` are pinned away from the parser's own defaults ("main", True): a
    renderer that forgot to emit either line would go undetected if the reparsed value only ever happened to
    match the default `from_dict` fills in for an absent key."""
    data = tomllib.loads(MINIMAL + TABLES)
    data["repo"]["default_branch"] = "trunk"
    data["repo"]["private"] = False
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


def app_env() -> dict:
    """The codex group in App form using the *other* key setting: `private_key_env` rather than
    `private_key_file`. An App group sets exactly one of the two, so `app()` alone never exercises this one --
    without this fixture, dropping `private_key_env` from `dumps` would go undetected."""
    data = tomllib.loads(MINIMAL)
    data["groups"]["codex"] = {**data["groups"]["codex"], "bot": "meld-codex[bot]", "auth": "app",
                               "app_id": 4911515, "installation_id": 160922345,
                               "private_key_env": "MELD_APP_KEY_CODEX"}
    data["groups"]["codex"].pop("token_env", None)
    return data


def pat_with_all_group_fields() -> dict:
    """One PAT group carrying every `KNOWN_GROUP` field at once, including *both* private_key_env and
    private_key_file. Legal because the App-only rules -- "exactly one key setting", app_id/installation_id
    required, bot ends with "[bot]" -- all live inside `if auth == "app":` and never fire while auth stays
    "pat" (the default). No App-form fixture could carry both key settings or leave auth unvalidated, so
    this is the one fixture that can pin every group and role key against `dumps` at once."""
    data = tomllib.loads(MINIMAL)
    data["groups"]["claude"].update({
        "concurrency": 3, "review_concurrency": 1, "git_name": "Nightly Bot", "git_email": "nightly@example.invalid",
        "app_id": 4911515, "installation_id": 160922345,
        "private_key_env": "MELD_APP_KEY_CLAUDE", "private_key_file": "~/.meld/keys/meld-claude.pem",
    })
    return data


def roundtrip(data: dict) -> tuple[Config, Config]:
    """The config a parsed file describes, and the config its canonical rendering describes."""
    first = Config.from_dict(data)
    return first, Config.from_dict(tomllib.loads(dumps(first)))


@pytest.mark.parametrize("build, what", [(minimal, "a minimal file"), (full, "every field set"), (app, "an App group"),
                                          (app_env, "an App group using private_key_env")])
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
    assert unknown_keys(data) == [("groups", "claude", "concurency"), ("groups", "claude", "orchestrator", "modell"),
                                  ("nonsense",), ("review", "quorumm")]
    # a dotted string is what a message shows, and only that: see `dotted_key`, and the sibling test below
    assert [dotted_key(k) for k in unknown_keys(data)] == ["groups.claude.concurency",
                                                           "groups.claude.orchestrator.modell",
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


def test_every_control_character_survives_the_round_trip():
    """TOML forbids a raw control character in a basic string. A name pasted from a coloured terminal carries an
    escape; if the renderer emits it raw, the file it writes cannot be read back at all."""
    data = minimal()
    for code in list(range(0x20)) + [0x7F]:
        data["groups"]["claude"]["git_name"] = f"before{chr(code)}after"
        cfg = Config.from_dict(data)
        back = Config.from_dict(tomllib.loads(dumps(cfg)))
        assert back.groups["claude"].git_name == cfg.groups["claude"].git_name, hex(code)


def test_a_pat_group_carrying_app_settings_round_trips_them_unchanged():
    """A half-finished App migration leaves app_id/installation_id/a key setting sitting in a group whose auth
    is still "pat" (setup.starter_toml ships exactly this shape as a commented block to uncomment). These four
    fields are inert while auth is "pat" -- nothing reads them unless is_app -- but the file must still come
    back with them intact rather than silently deleting them on the first save."""
    data = minimal()
    data["groups"]["codex"]["app_id"] = 4911515
    data["groups"]["codex"]["installation_id"] = 160922345
    data["groups"]["codex"]["private_key_file"] = "~/.meld/keys/meld-codex.pem"
    first, second = roundtrip(data)
    assert second.groups["codex"].auth == "pat"
    assert second.groups["codex"].app_id == 4911515
    assert second.groups["codex"].installation_id == 160922345
    assert second.groups["codex"].private_key_file == "~/.meld/keys/meld-codex.pem"
    assert first == second


def test_every_key_the_schema_knows_is_a_key_the_renderer_writes():
    """`KNOWN`/`KNOWN_GROUP`/`KNOWN_ROLE`, `from_dict`'s reads and `dumps`'s emitters are three copies of one
    schema. This pins the third against the first, so a field added to the schema but forgotten in `dumps`
    -- which would be silently reset to its default (or dropped) on the next save -- fails here instead of
    in somebody's file. Table keys and group/role keys need different fixtures: no single group can be both
    "pat" and "app", but a PAT group is free to carry every optional field at once (the App-only rules never
    fire), so `pat_with_all_group_fields` alone covers all 48 keys between it and `full()`."""
    rendered = tomllib.loads(dumps(Config.from_dict(full())))
    for table, keys in KNOWN.items():
        assert set(rendered[table]) == set(keys), table

    group_rendered = tomllib.loads(dumps(Config.from_dict(pat_with_all_group_fields())))["groups"]["claude"]
    assert set(group_rendered) - {"orchestrator", "pm"} == set(KNOWN_GROUP)
    assert set(group_rendered["orchestrator"]) == set(KNOWN_ROLE)
    assert set(group_rendered["pm"]) == set(KNOWN_ROLE)


def test_unknown_keys_does_not_mistake_a_malformed_table_for_an_unknown_key():
    """A `groups` that is not a table, or a group that is not a table, is a shape problem for `Config.from_dict`
    to report -- not a name `unknown_keys` should invent."""
    assert unknown_keys({"groups": 5}) == []
    assert unknown_keys({"groups": {"claude": 5}}) == []


def test_an_infinite_app_id_is_a_config_error_not_a_crash():
    """`inf` is a legal TOML float. On an App group it must still be the clean ConfigError `_positive_int`
    raises for anything that is not a positive int -- `int(float("inf"))` raises `OverflowError`, which is
    not `_positive_int`'s to catch, but `_loose_int`'s pre-read must not let it escape before `_positive_int`
    gets a turn. On a PAT group, where the field is inert, it must not crash the parser at all."""
    app_data = app()
    app_data["groups"]["codex"]["app_id"] = float("inf")
    with pytest.raises(ConfigError, match=r"groups\.codex\.app_id must be a positive integer"):
        Config.from_dict(app_data)

    pat_data = minimal()
    pat_data["groups"]["codex"]["app_id"] = float("inf")
    Config.from_dict(pat_data)     # must not raise -- auth stays "pat", so app_id is inert


def test_unknown_keys_keeps_a_quoted_key_that_contains_a_dot_in_one_piece():
    """R37.2. TOML lets a quoted key carry a literal `.`, so a dotted string is a lossy rendering of a path
    and re-splitting one is not its inverse. `[review]` with `"a.b" = 1` is a single unknown key two levels
    deep, not a three-level one: the viewer walks this tuple to copy the value across, and splitting the
    dotted form instead is what made `GET /api/config` answer 500 `KeyError: 'a'` -- the whole form
    unavailable for that file, against everything else the route does to make sure `GET` answers."""
    data = full()
    data["review"]["a.b"] = 1
    assert unknown_keys(data) == [("review", "a.b")]
    assert dotted_key(("review", "a.b")) == "review.a.b"     # ambiguous, which is why it is display-only


def _reads_of(source: str) -> dict[str, set[str]]:
    """`{local name: {the literal string keys read off it}}` for `x.get("k", ...)`, `x["k"]` and
    `_require(x, "k", ...)` anywhere in `source`. A static read of how the parser reaches into a parsed
    file, so the pin below can compare it against the schema table `unknown_keys` walks."""
    out: dict[str, set[str]] = {}
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        target = key = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" \
                and isinstance(node.func.value, ast.Name) and node.args and isinstance(node.args[0], ast.Constant):
            target, key = node.func.value.id, node.args[0].value
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_require" \
                and len(node.args) >= 2 and isinstance(node.args[0], ast.Name) and isinstance(node.args[1], ast.Constant):
            target, key = node.args[0].id, node.args[1].value
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                and isinstance(node.slice, ast.Constant):
            target, key = node.value.id, node.slice.value
        if target is not None and isinstance(key, str):
            out.setdefault(target, set()).add(key)
    return out


def test_every_key_from_dict_reads_is_a_key_the_schema_knows():
    """The other direction from the sibling test above, and the one with teeth for the form. `unknown_keys`
    refuses, by name, every key in a candidate that `KNOWN`/`KNOWN_GROUP`/`KNOWN_ROLE` does not list -- so a
    setting `from_dict` reads but the schema table forgot would be refused on `PUT` even though it is
    perfectly legal, and the round trip would fail on a file `meld` itself considers valid. Nothing else
    would catch that: `dumps` would not write the field either, so the render comparison stays equal and
    every other test passes.

    Static, over `from_dict`'s own source, because there is no runtime signal for "a key the parser looked
    for and did not find" -- `.get` with a default is indistinguishable from never asking."""
    reads = _reads_of(inspect.getsource(Config.from_dict))

    # The local each table is bound to (`review = data.get("review", {})`); anything else reading string
    # keys off a bare name in `from_dict` is a table this test does not know about, which is the point.
    tables = {name: name for name in KNOWN}
    assert set(reads) <= set(tables) | {"data", "g"}, sorted(set(reads) - set(tables) - {"data", "g"})

    # every table pulled off the parsed file is one `KNOWN` holds -- and every one it holds is read
    assert reads["data"] == set(KNOWN) | {"groups"}      # `groups` is keyed by names the product chooses

    for local, table in tables.items():
        extra = reads.get(local, set()) - set(KNOWN[table])
        assert not extra, f"{table}: read by from_dict, missing from KNOWN: {sorted(extra)}"

    # a group's own fields. `orchestrator`/`pm` are legal without being in `KNOWN_GROUP` because
    # `unknown_keys` matches them by name and then walks them against `KNOWN_ROLE`.
    extra = reads["g"] - set(KNOWN_GROUP) - {"orchestrator", "pm"}
    assert not extra, f"groups.<g>: read by from_dict, missing from KNOWN_GROUP: {sorted(extra)}"

    role = _reads_of(inspect.getsource(config_module._role))
    assert role["data"] == set(KNOWN_ROLE)
