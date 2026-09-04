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


def test_role_prompt_ends_with_the_launch_block(tmp_path: Path):
    first = role_prompt("pm", CFG2, CFG2.groups["claude"], tmp_path)
    assert first.rstrip().endswith("## This launch\n\n- Launch number: 1 (the first launch under this supervisor)\n"
                                   "- Reason: `start` (the supervisor has just started; this is the first session of this role)")
    later = role_prompt("orchestrator", CFG2, CFG2.groups["claude"], tmp_path, launch_no=4, reason="crash")
    assert "- Launch number: 4\n- Reason: `crash` (the previous session ended abnormally" in later and "first launch" not in later
    for reason in ("exit", "recycle"):
        assert f"- Reason: `{reason}` (" in role_prompt("pm", CFG2, CFG2.groups["codex"], tmp_path, launch_no=2, reason=reason)
    with pytest.raises(ValueError, match="reason must be one of start, exit, crash, recycle"):
        role_prompt("pm", CFG2, CFG2.groups["claude"], tmp_path, launch_no=2, reason="rebooted")
    with pytest.raises(ValueError, match="launch_no"):
        role_prompt("pm", CFG2, CFG2.groups["claude"], tmp_path, launch_no=0)
    # the role prompts tell each role how to read the block
    orch = role_prompt("orchestrator", CFG2, CFG2.groups["claude"], tmp_path)
    assert "On launch 1, post `[<group>] CONFIG`" in orch and "When the reason is `crash` or `recycle`, post `RESUMED`" in orch
    assert "post `[<group>:pm] RESUMED` only when the reason is `crash` or `recycle`" in first
    assert "retry it once before reporting it" in first
