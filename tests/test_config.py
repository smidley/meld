from pathlib import Path

import pytest

from moot.config import Config, ConfigError, Party, load_config, party_from_env

MINIMAL = """
[repo]
name = "acme/widgets"

[humans]
accounts = ["empowerScott"]

[groups.claude]
cli = "claude"
bot = "moot-claude-bot"
token_env = "MOOT_TOKEN_CLAUDE"
[groups.claude.orchestrator]
model = "claude-opus-5"
effort = "high"
[groups.claude.pm]
model = "claude-sonnet-5"
effort = "medium"

[groups.codex]
cli = "codex"
bot = "moot-codex-bot"
token_env = "MOOT_TOKEN_CODEX"
[groups.codex.orchestrator]
model = "gpt-x"
effort = "high"
[groups.codex.pm]
model = "gpt-x-mini"
effort = "medium"
"""


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / ".moot.toml"
    p.write_text(text)
    return p


def test_loads_groups_and_defaults(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.repo_name == "acme/widgets"
    assert cfg.default_branch == "main"
    assert cfg.private is True
    assert cfg.humans == ("empowerScott",)
    assert cfg.group_names == ("claude", "codex")
    assert cfg.groups["claude"].bot == "moot-claude-bot"
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
bot = "moot-gemini-bot"
token_env = "MOOT_TOKEN_GEMINI"
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
    assert cfg.group_for_bot("moot-codex-bot").name == "codex"
    assert cfg.group_for_bot("nobody") is None
    assert cfg.is_human("empowerScott")
    assert not cfg.is_human("moot-claude-bot")


def test_missing_repo_name_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="repo.name"):
        load_config(write(tmp_path, MINIMAL.replace('name = "acme/widgets"', "")))


def test_group_missing_bot_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="groups.codex.bot"):
        load_config(write(tmp_path, MINIMAL.replace('bot = "moot-codex-bot"', "")))


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
    assert party_from_env(cfg, {"MOOT_GROUP": "claude"}) == Party("group", "claude")
    assert party_from_env(cfg, {"MOOT_GROUP": "claude", "MOOT_ROLE": "pm"}) == Party("pm", "claude")
    with pytest.raises(ConfigError):
        party_from_env(cfg, {"MOOT_GROUP": "nope"})
    with pytest.raises(ConfigError):
        party_from_env(cfg, {"MOOT_GROUP": "claude", "MOOT_ROLE": "worker"})
