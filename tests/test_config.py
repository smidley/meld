import copy
import inspect
import re
import tomllib
from pathlib import Path

import pytest

from meld.config import Config, ConfigError, Party, load_config, party_from_env, parse_duration

MINIMAL = """
[repo]
name = "acme/widgets"

[humans]
accounts = ["empowerScott"]

[groups.claude]
cli = "claude"
bot = "meld-claude-bot"
token_env = "MELD_TOKEN_CLAUDE"
[groups.claude.orchestrator]
model = "claude-opus-5"
effort = "high"
[groups.claude.pm]
model = "claude-sonnet-5"
effort = "medium"

[groups.codex]
cli = "codex"
bot = "meld-codex-bot"
token_env = "MELD_TOKEN_CODEX"
[groups.codex.orchestrator]
model = "gpt-x"
effort = "high"
[groups.codex.pm]
model = "gpt-x-mini"
effort = "medium"
"""


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / ".meld.toml"
    p.write_text(text)
    return p


def test_loads_groups_and_defaults(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.repo_name == "acme/widgets"
    assert cfg.default_branch == "main"
    assert cfg.private is True
    assert cfg.humans == ("empowerScott",)
    assert cfg.group_names == ("claude", "codex")
    assert cfg.groups["claude"].bot == "meld-claude-bot"
    assert cfg.groups["claude"].concurrency == 8
    assert cfg.groups["claude"].review_concurrency == 4
    assert cfg.groups["claude"].orchestrator.model == "claude-opus-5"
    assert cfg.review_quorum == 2
    assert cfg.pingpong_cap == 3
    assert cfg.amend_cap == 3
    assert cfg.poll_interval_seconds == 30
    assert cfg.wait_timeout_seconds == 300
    assert cfg.timeouts == {"S": "20m", "M": "45m", "L": "90m"}
    assert cfg.budget == {"small": 200, "medium": 80, "large": 20}
    assert cfg.budget_wallclock_hours == 72


def test_effective_quorum_is_min_of_quorum_and_n_minus_one(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.effective_quorum == 1  # two groups
    three = MINIMAL + """
[groups.gemini]
cli = "gemini"
bot = "meld-gemini-bot"
token_env = "MELD_TOKEN_GEMINI"
[groups.gemini.orchestrator]
model = "g"
effort = "high"
[groups.gemini.pm]
model = "g"
effort = "medium"
"""
    cfg3 = load_config(write(tmp_path, three))
    assert cfg3.effective_quorum == 2


def test_group_for_bot_and_is_human(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.group_for_bot("meld-codex-bot").name == "codex"
    assert cfg.group_for_bot("nobody") is None
    assert cfg.is_human("empowerScott")
    assert not cfg.is_human("meld-claude-bot")


def test_missing_repo_name_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="repo.name"):
        load_config(write(tmp_path, MINIMAL.replace('name = "acme/widgets"', "")))


def test_group_missing_bot_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="groups.codex.bot"):
        load_config(write(tmp_path, MINIMAL.replace('bot = "meld-codex-bot"', "")))


def test_party_labels_and_parse(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert Party("group", "claude").label == "[claude]"
    assert Party("pm", "claude").label == "[claude:pm]"
    assert Party("human").label == "[human]"
    assert Party("joint").label == "[joint]"
    assert Party.parse("codex:pm", cfg) == Party("pm", "codex")
    assert Party.parse("codex", cfg) == Party("group", "codex")
    assert Party.parse("human", cfg) == Party("human")
    assert Party.parse("mystery", cfg) is None
    assert Party.parse("codex:qa", cfg) is None


def test_party_from_env(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert party_from_env(cfg, {}) == Party("human")
    assert party_from_env(cfg, {"MELD_GROUP": "claude"}) == Party("group", "claude")
    assert party_from_env(cfg, {"MELD_GROUP": "claude", "MELD_ROLE": "pm"}) == Party("pm", "claude")
    with pytest.raises(ConfigError):
        party_from_env(cfg, {"MELD_GROUP": "nope"})
    with pytest.raises(ConfigError):
        party_from_env(cfg, {"MELD_GROUP": "claude", "MELD_ROLE": "worker"})


def test_reserved_and_malformed_group_names_are_rejected(tmp_path):
    for reserved in ("human", "joint"):
        with pytest.raises(ConfigError, match=f"groups.{reserved}: "):
            load_config(write(tmp_path, MINIMAL.replace("[groups.codex]", f"[groups.{reserved}]")
                                              .replace("[groups.codex.", f"[groups.{reserved}.")))
    with pytest.raises(ConfigError, match=r"groups.my group: group names must match"):
        load_config(write(tmp_path, MINIMAL.replace("[groups.codex]", '[groups."my group"]')
                                          .replace("[groups.codex.", '[groups."my group".')))


@pytest.mark.parametrize("name", ["Claude", "my group", "a/b", "x" * 33, "-lead"])
def test_group_names_must_be_lowercase_label_and_path_safe(name, tmp_path):
    """A group name becomes a meld:claimed-by:<group> GitHub label (case-insensitive for uniqueness) and a worktree
    path component, so it must be lowercase, path-safe, and short enough that the label prefix plus the name still
    fits GitHub's 50-character label limit."""
    with pytest.raises(ConfigError, match="group names must match"):
        load_config(write(tmp_path, MINIMAL.replace("[groups.codex]", f'[groups."{name}"]')
                                          .replace("[groups.codex.", f'[groups."{name}".')))


def test_git_identity_and_worker_lease_settings(tmp_path):
    text = (MINIMAL + """
[worker]
log_tail_lines = 80
max_turns = 50

[lease]
grace_minutes = 5
""").replace('bot = "meld-claude-bot"', 'bot = "meld-claude-bot"\ngit_name = "Meld Claude"\ngit_email = "1+meld-claude-bot@users.noreply.github.com"')
    cfg = load_config(write(tmp_path, text))
    assert cfg.groups["claude"].git_name == "Meld Claude"
    assert cfg.groups["claude"].git_email == "1+meld-claude-bot@users.noreply.github.com"
    assert cfg.groups["codex"].git_name is None and cfg.groups["codex"].git_email is None
    assert (cfg.worker_log_tail_lines, cfg.worker_max_turns, cfg.lease_grace_minutes) == (80, 50, 5)
    defaults = load_config(write(tmp_path, MINIMAL))
    assert (defaults.worker_log_tail_lines, defaults.worker_max_turns, defaults.lease_grace_minutes) == (50, 200, 10)


def test_parse_duration_and_timeout_seconds(tmp_path):
    assert parse_duration("90s") == 90 and parse_duration("20m") == 1200 and parse_duration("2h") == 7200 and parse_duration("45") == 45
    with pytest.raises(ConfigError, match="duration"):
        parse_duration("soon")
    cfg = load_config(write(tmp_path, MINIMAL))
    assert (cfg.timeout_seconds("S"), cfg.timeout_seconds("M"), cfg.timeout_seconds("L")) == (1200, 2700, 5400)
    with pytest.raises(ConfigError, match="unknown size 'XL'"):
        cfg.timeout_seconds("XL")
    with pytest.raises(ConfigError, match="timeouts.M"):
        load_config(write(tmp_path, MINIMAL + '\n[timeouts]\nM = "fast"\n'))


def test_audit_batch_defaults_and_overrides():
    base = {"repo": {"name": "acme/widgets"}, "groups": {"claude": {"cli": "claude", "bot": "b", "token_env": "T",
            "orchestrator": {"model": "m", "effort": "high"}, "pm": {"model": "m", "effort": "medium"}}}}
    cfg = Config.from_dict(base)
    assert (cfg.audit_batch_merges, cfg.audit_batch_hours) == (5, 4)
    cfg2 = Config.from_dict({**base, "audit": {"batch_merges": 3, "batch_hours": 1}})
    assert (cfg2.audit_batch_merges, cfg2.audit_batch_hours) == (3, 1)


def test_app_group_config_parses_and_validates():
    import pytest
    from meld.config import Config, ConfigError
    base = {"repo": {"name": "acme/widgets"}, "humans": {"accounts": ["empowerScott"]}}
    role = {"model": "claude-opus-5", "effort": "high"}
    app = {"cli": "claude", "bot": "meld-claude[bot]", "auth": "app", "app_id": 1234, "installation_id": 55,
           "private_key_env": "MELD_APP_KEY_CLAUDE", "orchestrator": role, "pm": role}
    cfg = Config.from_dict({**base, "groups": {"claude": app}})
    g = cfg.groups["claude"]
    assert g.is_app and g.app_id == 1234 and g.installation_id == 55 and g.private_key_env == "MELD_APP_KEY_CLAUDE" and g.token_env is None
    pat = {"cli": "codex", "bot": "meld-codex-bot", "token_env": "MELD_TOKEN_CODEX", "orchestrator": role, "pm": role}
    assert Config.from_dict({**base, "groups": {"codex": pat}}).groups["codex"].auth == "pat"
    with pytest.raises(ConfigError, match="token_env"):
        Config.from_dict({**base, "groups": {"codex": {k: v for k, v in pat.items() if k != "token_env"}}})
    with pytest.raises(ConfigError, match=r"\[bot\]"):
        Config.from_dict({**base, "groups": {"claude": {**app, "bot": "meld-claude-bot"}}})
    with pytest.raises(ConfigError, match="app_id"):
        Config.from_dict({**base, "groups": {"claude": {k: v for k, v in app.items() if k != "app_id"}}})
    with pytest.raises(ConfigError, match="exactly one"):
        Config.from_dict({**base, "groups": {"claude": {**app, "private_key_file": "/tmp/k.pem"}}})
    with pytest.raises(ConfigError, match="auth"):
        Config.from_dict({**base, "groups": {"claude": {**app, "auth": "oauth"}}})


def test_app_group_ids_must_be_positive_integers():
    """Final-review Minor 9 + 10: the starter config writes `app_id = 0`/`installation_id = 0` as placeholders, and
    an unfilled 0 used to reach GitHub and come back as an opaque 401/404. `int()` also silently accepted floats and
    booleans (`int(True)` is 1). Both settings must be a real, positive TOML integer."""
    base = {"repo": {"name": "acme/widgets"}, "humans": {"accounts": ["empowerScott"]}}
    role = {"model": "claude-opus-5", "effort": "high"}
    app = {"cli": "claude", "bot": "meld-claude[bot]", "auth": "app", "app_id": 1234, "installation_id": 55,
           "private_key_env": "MELD_APP_KEY_CLAUDE", "orchestrator": role, "pm": role}
    for field in ("app_id", "installation_id"):
        for bad in ("not-a-number", 0, -1, 12.5, True, None):
            with pytest.raises(ConfigError, match=rf"groups\.claude\.{field} must be a positive integer"):
                Config.from_dict({**base, "groups": {"claude": {**app, field: bad}}})
    with pytest.raises(ConfigError, match="0 is the starter placeholder"):
        Config.from_dict({**base, "groups": {"claude": {**app, "app_id": 0}}})
    assert Config.from_dict({**base, "groups": {"claude": app}}).groups["claude"].app_id == 1234


def test_logs_table_has_defaults_and_is_overridable():
    base = {
        "repo": {"name": "acme/widgets"},
        "humans": {"accounts": ["empowerScott"]},
        "groups": {"claude": {"cli": "claude", "bot": "meld-claude-bot", "token_env": "T1",
                             "orchestrator": {"model": "m", "effort": "high"}, "pm": {"model": "m", "effort": "medium"}}},
    }
    default = Config.from_dict(base)
    assert default.logs_max_bytes_per_run == 8 * 1024 * 1024
    assert default.logs_warn_total_mb == 2048

    tuned = Config.from_dict({**base, "logs": {"max_bytes_per_run": 1024 * 1024, "warn_total_mb": 1}})
    assert tuned.logs_max_bytes_per_run == 1024 * 1024
    assert tuned.logs_warn_total_mb == 1


def test_logs_table_refuses_a_size_that_would_destroy_every_log():
    """`[logs]` is hand-edited, and `meld watch`'s form now writes it too. `max_bytes_per_run = 0` or a negative
    makes every tick trim every log -- an empty `.1` over the previous one and the original zeroed, each
    cycle -- so one typo is silent, total log destruction. It is a config error instead."""
    base = {
        "repo": {"name": "acme/widgets"},
        "humans": {"accounts": ["empowerScott"]},
        "groups": {"claude": {"cli": "claude", "bot": "meld-claude-bot", "token_env": "T1",
                             "orchestrator": {"model": "m", "effort": "high"}, "pm": {"model": "m", "effort": "medium"}}},
    }
    for bad in (0, -1, 1024, 1.5, True, "8388608"):
        with pytest.raises(ConfigError, match="logs.max_bytes_per_run must be an integer of at least 65536 bytes"):
            Config.from_dict({**base, "logs": {"max_bytes_per_run": bad}})
    for bad in (0, -1, 2.5, False, "2048"):
        with pytest.raises(ConfigError, match="logs.warn_total_mb must be an integer of at least 1 megabytes"):
            Config.from_dict({**base, "logs": {"warn_total_mb": bad}})
    assert Config.from_dict({**base, "logs": {"max_bytes_per_run": 65536}}).logs_max_bytes_per_run == 65536


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
    assert default.runners_idle_minutes == 15 and Config.from_dict({**base, "runners": {"idle_minutes": 40}}).runners_idle_minutes == 40   # plan 64
    assert default.logs_warn_free_mb == 2048

    tuned = Config.from_dict({**base, "runners": {"limit_retry_minutes": 5}, "logs": {"warn_free_mb": 1}})
    assert tuned.runners_limit_retry_minutes == 5 and tuned.logs_warn_free_mb == 1

    with pytest.raises(ConfigError, match="limit_retry_minutes"):
        Config.from_dict({**base, "runners": {"limit_retry_minutes": 0}})
    with pytest.raises(ConfigError, match="warn_free_mb"):
        Config.from_dict({**base, "logs": {"warn_free_mb": 0}})


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


#: Every numeric setting `from_dict` reads, with the floor the parser holds it to. The rule is whether `0`
#: is a lie: `1` where `0` cannot mean what it says, because the code coerces it or acts on it in a way the
#: word does not describe; `0` where it means something, and only a negative is nonsense. A second, narrower
#: clause floors a `0` that is a runaway even where it is honest -- `polling.interval_seconds`, and
#: `notify.repeat_after_minutes`, which is the marginal call of the set. See `config.py`'s own note above
#: `LOGS_MIN_BYTES_PER_RUN` for the worked cases. Written out here rather than derived from
#: the source, because the interesting claim is *which* floor each field gets, and a table that derived
#: itself from the code could not be wrong about it. (`logs.*`, `runners.limit_retry_minutes` and
#: `viewer.port` have their own tests above; their floors are not 0 or 1.)
FLOORS = [
    ("groups.claude.concurrency", 1),
    ("groups.claude.review_concurrency", 1),
    ("review.quorum", 1),
    ("review.pingpong_cap", 1),
    ("agreement.amend_cap", 0),
    ("polling.interval_seconds", 1),
    ("polling.wait_timeout_seconds", 1),
    ("runners.restart_hours", 1),
    ("runners.roster_refresh_minutes", 1),
    ("runners.silence_grace_minutes", 1),
    ("runners.summary_cadence_minutes", 1),
    ("budget.small", 0),
    ("budget.medium", 0),
    ("budget.large", 0),
    ("budget.wallclock_hours", 0),
    ("notify.repeat_after_minutes", 1),
    ("worker.log_tail_lines", 1),
    ("worker.max_turns", 1),
    ("lease.grace_minutes", 0),
    ("audit.batch_merges", 1),          # cannot mean a batch of nothing, so it can only mean 1 -- a lie
    ("audit.batch_hours", 0),           # "do not wait on age": real, distinct, and nothing degenerates
]


def _at(data: dict, dotted: str, value) -> dict:
    """`data` with `dotted` set to `value`, deeply copied, creating any table on the way."""
    out = copy.deepcopy(data)
    node = out
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    return out


@pytest.mark.parametrize("path, floor", FLOORS, ids=[p for p, _ in FLOORS])
def test_every_numeric_setting_has_the_floor_its_meaning_calls_for(path, floor):
    """The form `meld watch` serves can write any of these, so the parser is what stands between a typo and a
    zero-sleep poll loop, a relaunch every cycle, a dispatch that is never legal, or a `lines[-0:]` that
    posts a whole transcript to GitHub. Each field is legal *at* its floor and refused below it; a float and
    a bool are refused at any value, which is what closes the silent `int(2.5) == 2` truncation as well."""
    base = tomllib.loads(MINIMAL)
    assert isinstance(Config.from_dict(_at(base, path, floor)), Config)     # the floor itself is legal
    for bad in (floor - 1, floor - 10, 2.5, True, False, "4"):
        with pytest.raises(ConfigError, match=rf"{re.escape(path)} must be an integer of at least {floor} "):
            Config.from_dict(_at(base, path, bad))


def test_no_numeric_setting_is_read_with_a_bare_int():
    """`int()` quietly accepts "12", truncates 12.5 to 12, and turns True into 1 -- and accepts 0 and every
    negative besides. Every numeric read in `from_dict` therefore goes through `_at_least`, `_positive_int`,
    `_loose_int` or `_port`. This pins the absence, so a numeric field added later with a bare `int()` --
    the way all twenty-one of the fields above were once read -- fails here rather than in somebody's file.
    A source check, so it proves the call is not written, not that the field behaves; the floors above are
    what test the behaviour."""
    source = inspect.getsource(Config.from_dict)
    bare = [line.strip() for line in source.splitlines() if re.search(r"(?<![_A-Za-z])int\(", line)]
    assert bare == [], bare


@pytest.mark.parametrize("size", ["S", "M", "L"])
@pytest.mark.parametrize("bad", ["0m", "0", "0s", "0h", 0], ids=["0m", "0", "0s", "0h", "bare-int-0"])
def test_a_worker_timeout_of_zero_is_refused(size, bad):
    """R41, the duration-shaped member of the same family as the numeric floors. `dispatch` sets a worker's
    deadline to `when + timeout_seconds(size)`, so a zero launches a worker already past its deadline --
    one paid CLI session per launch, which is `runners.restart_hours = 0` in different clothes. Every
    spelling of zero the grammar admits is covered, including the bare integer a TOML `S = 0` produces,
    and all three sizes, because they share one read site and a floor applied to only one of them would
    be worse than none.

    The floor lives at that read site, not inside `parse_duration`: zero is a well-formed duration and
    the helper's contract belongs to its other callers."""
    base = tomllib.loads(MINIMAL)
    timeouts = {"S": "20m", "M": "45m", "L": "90m"} | {size: bad}
    with pytest.raises(ConfigError, match=rf"timeouts\.{size} must be a duration greater than zero"):
        Config.from_dict({**base, "timeouts": timeouts})


@pytest.mark.parametrize("size", ["S", "M", "L"])
def test_a_negative_worker_timeout_never_reaches_the_floor(size):
    """`_DURATION_RE` matches digits and nothing else, so `-5m` is refused as an invalid duration before
    the floor is consulted. Pinned so a later widening of the pattern to accept a sign cannot quietly open
    a hole underneath the zero check above."""
    base = tomllib.loads(MINIMAL)
    timeouts = {"S": "20m", "M": "45m", "L": "90m"} | {size: "-5m"}
    with pytest.raises(ConfigError, match=rf"timeouts\.{size}: invalid duration"):
        Config.from_dict({**base, "timeouts": timeouts})


def test_the_smallest_usable_worker_timeout_is_still_legal():
    """The floor is zero, not a judgement about how short a run may be: one second parses and is kept."""
    base = tomllib.loads(MINIMAL)
    cfg = Config.from_dict({**base, "timeouts": {"S": "1s", "M": "1", "L": "1h"}})
    assert (cfg.timeout_seconds("S"), cfg.timeout_seconds("M"), cfg.timeout_seconds("L")) == (1, 1, 3600)


def test_parse_duration_itself_still_accepts_zero():
    """The helper's contract is unchanged -- zero is a well-formed duration and its other callers are not
    under this plan's change. Pinned so the floor cannot be "tidied up" into `parse_duration` later."""
    assert parse_duration("0m") == 0 and parse_duration("0") == 0


def test_logins_compare_case_insensitively(tmp_path):
    """GitHub treats logins case-insensitively; an entry whose case differs from GitHub's canonical form used to
    silently never match (spec 21 item 20). `is_party` is the one test the comment and event sweeps use."""
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.group_for_bot("Meld-Codex-Bot").name == "codex"
    assert cfg.is_human("EMPOWERSCOTT") and cfg.is_party("empowerscott") and cfg.is_party("MELD-CLAUDE-BOT")
    assert not cfg.is_party("nobody") and not cfg.is_human("meld-claude-bot")
