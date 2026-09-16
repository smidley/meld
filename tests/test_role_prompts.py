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
    assert "On launch 1, post `[<group>] CONFIG`" in orch and "When the reason is `crash`, `recycle` or `limit`, post `RESUMED`" in orch
    assert "post `[<group>:pm] RESUMED` only when the reason is `crash`, `recycle` or `limit`" in first
    assert "retry it once before reporting it" in first


def test_role_prompt_has_product_standards_and_the_plan_3_commands(tmp_path):
    text = role_prompt("pm", CFG2, CFG2.groups["claude"], tmp_path)
    assert "## Product standards" in text and "## Your roster" in text and text.index("## Product standards") < text.index("## Your roster")
    for cmd in ("meld review N", "meld merge N", "meld audit N", "meld reopen N", "meld challenge <open|evidence|retract|uphold|dismiss> REF -m TEXT",
                "meld dispatch N [--kind review|audit] [--worker security]"):
        assert cmd in text, cmd
    (tmp_path / ".meld").mkdir()
    (tmp_path / ".meld" / "STANDARDS.md").write_text("Ship dark mode everywhere.\n")
    assert "Ship dark mode everywhere." in role_prompt("orchestrator", CFG2, CFG2.groups["claude"], tmp_path)

def test_role_prompts_describe_the_review_merge_audit_and_challenge_loops(tmp_path):
    pm = role_prompt("pm", CFG2, CFG2.groups["claude"], tmp_path)
    for needle in ("`meld review N` right after `meld collect N` finishes a review run", "`meld merge N` for every PR whose `legal` includes `merge`",
                   "`--worker security`", "`audit.due`", "`meld audit N`", "`meld reopen", "ping-pong"):
        assert needle in pm, needle
    orch = role_prompt("orchestrator", CFG2, CFG2.groups["claude"], tmp_path)
    for needle in ("meld challenge open", "meld challenge uphold", "the human adjudicates", "AUDIT #<pr> FAIL", "visual design, performance, and security",
                   "security_paths"):
        assert needle in orch, needle

def test_role_prompts_cover_planning_memory_and_the_human_console(tmp_path):
    orch = role_prompt("orchestrator", CFG2, CFG2.groups["claude"], tmp_path)
    for needle in ("- id: <slug> | title:", "meld plan issues plan-<milestone>", "meld plan approve", "meld recall", "meld decide <id>", "meld remember <ref>",
                   "## Memory", "verified [joint]"):
        assert needle in orch, needle
    pm = role_prompt("pm", CFG2, CFG2.groups["claude"], tmp_path)
    for needle in ("memory PR", "red CI", "IGNORED-COMMENT", "off-protocol", "Slack"):
        assert needle in pm, needle
    for cmd in ("meld decide REF", "meld remember REF -m TEXT", "meld recall QUERY", "meld plan issues REF [--dry-run]", "meld plan approve",
                "meld plan ack REF", "meld inbox", "meld chat [--once TEXT]", "meld notify [--dry-run]"):
        assert cmd in pm, cmd

def test_role_prompts_name_setup_and_doctor(tmp_path):
    pm = role_prompt("pm", CFG2, CFG2.groups["claude"], tmp_path)
    for needle in ("meld doctor [--probe-status]", "meld setup REPO --goal TEXT"):
        assert needle in pm, needle
    assert "meld doctor" in pm.split("## When something is off")[1]


def test_a_limit_relaunch_is_a_reason_and_both_roles_announce_it():
    from meld.prompts import LAUNCH_REASONS, launch_block
    assert "limit" in LAUNCH_REASONS
    block = "\n".join(launch_block(4, "limit"))
    assert "`limit`" in block and "quota" in block.lower()
    for name in ("orchestrator.md", "pm.md"):
        text = (Path(__file__).parents[1] / "src" / "meld" / "prompts" / name).read_text()
        # the supervisor posted LIMITED while the role was gone, so the role's RESUMED closes that pair -- and the
        # role is told the verb is not its to post, because `posting.FRAMEWORK_ONLY_VERBS` refuses it
        assert "`limit`" in text and "RESUMED" in text and "LIMITED" in text, name
        assert "`meld post LIMITED` is refused" in text, name
