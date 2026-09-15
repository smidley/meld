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
    """`[logs]` is hand-edited (and a later plan puts it in a form). `max_bytes_per_run = 0` or a negative
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
