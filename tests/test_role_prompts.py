from pathlib import Path

import pytest

from meld.prompts import ROLES, role_prompt
from tests.factories import CFG2


def test_role_prompt_composes_preamble_role_and_configuration(tmp_path: Path):
    text = role_prompt("orchestrator", CFG2, CFG2.groups["claude"], tmp_path)
    for needle in ("# Meld: how every long-lived role works", "# You are the orchestrator", "## Your configuration",
                   "acme/widgets", "meld-claude-bot", "codex (bot `meld-codex-bot`)", "empowerScott", "claude-opus-5", "effort high",
                   "concurrency 8", "quorum 1", "## Your roster", "backend", "## Commands available", "meld tag"):
        assert needle in text, needle
    pm = role_prompt("pm", CFG2, CFG2.groups["codex"], tmp_path)
    assert "# You are the project manager" in pm and "claude-sonnet-5" in pm and "meld tick" in pm
    assert ROLES == ("orchestrator", "pm")
    with pytest.raises(ValueError):
        role_prompt("worker", CFG2, CFG2.groups["claude"], tmp_path)
