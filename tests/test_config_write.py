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


def test_every_control_character_survives_the_round_trip():
    """TOML forbids a raw control character in a basic string. A name pasted from a coloured terminal carries an
    escape; if the renderer emits it raw, the file it writes cannot be read back at all."""
    data = minimal()
    for code in list(range(0x20)) + [0x7F]:
        data["groups"]["claude"]["git_name"] = f"before{chr(code)}after"
        cfg = Config.from_dict(data)
        back = Config.from_dict(tomllib.loads(dumps(cfg)))
        assert back.groups["claude"].git_name == cfg.groups["claude"].git_name, hex(code)
