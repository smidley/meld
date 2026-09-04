import json
from pathlib import Path

import meld.adapters.claude as claude_mod
from meld.adapters.base import LaunchSpec
from meld.adapters.claude import ClaudeAdapter


def spec(tmp_path: Path, **over) -> LaunchSpec:
    base = dict(role="worker", model="claude-sonnet-5", effort="high", cwd=tmp_path, prompt="Read .meld-brief.md and do it.",
                log_path=tmp_path / "log.txt", timeout_seconds=1200,
                env={"PATH": "/usr/bin", "HOME": "/Users/x", "CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli", "MELD_WORKER": "1"}, max_turns=200)
    base.update(over)
    return LaunchSpec(**base)


def test_argv_is_the_documented_unattended_shape(tmp_path: Path):
    argv = ClaudeAdapter().argv(spec(tmp_path))
    assert argv[:3] == ["claude", "-p", "Read .meld-brief.md and do it."]
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5"
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--strict-mcp-config" in argv and json.loads(argv[argv.index("--mcp-config") + 1]) == {"mcpServers": {}}
    assert json.loads(argv[argv.index("--settings") + 1]) == {"disableAllHooks": True, "autoMemoryEnabled": False}
    assert argv[argv.index("--setting-sources") + 1] == "project"
    assert argv[argv.index("--max-turns") + 1] == "200"
    assert "--effort" not in argv and "--bare" not in argv and "--dangerously-skip-permissions" not in argv


def test_env_sets_effort_and_strips_nested_session_guard(tmp_path: Path):
    env = ClaudeAdapter().env_for(spec(tmp_path))
    assert env["CLAUDE_CODE_EFFORT_LEVEL"] == "high" and env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert "CLAUDECODE" not in env and "CLAUDE_CODE_ENTRYPOINT" not in env
    assert env["PATH"] == "/usr/bin" and env["HOME"] == "/Users/x" and env["MELD_WORKER"] == "1"


def test_launch_uses_argv_env_and_log(tmp_path: Path, monkeypatch):
    captured = {}

    def fake_popen(argv, spec_, log):
        captured.update(argv=argv, spec=spec_, log_name=log.name)
        return "proc"

    monkeypatch.setattr(claude_mod, "popen_detached", fake_popen)
    s = spec(tmp_path)
    assert ClaudeAdapter().launch(s) == "proc"
    assert captured["argv"][0] == "claude" and captured["spec"].env["CLAUDE_CODE_EFFORT_LEVEL"] == "high"
    assert captured["spec"].cwd == tmp_path and captured["log_name"] == str(tmp_path / "log.txt")


def test_installed_skills_scans_personal_and_plugin_skills(tmp_path: Path):
    home = tmp_path / ".claude"
    (home / "skills" / "brand").mkdir(parents=True)
    (home / "skills" / "brand" / "SKILL.md").write_text("---\nname: brand\ndescription: Brand voice.\n---\n")
    plug = home / "plugins" / "cache" / "market" / "design" / "1.0.0" / "skills" / "slides"
    plug.mkdir(parents=True)
    (plug / "SKILL.md").write_text("---\nname: slides\ndescription: Decks.\n---\n")
    names = [(s.name, s.description) for s in ClaudeAdapter(home=home).installed_skills()]
    assert names == [("brand", "Brand voice."), ("slides", "Decks.")]
    assert ClaudeAdapter(home=tmp_path / "nowhere").installed_skills() == []
    assert ClaudeAdapter().command_timeout_ceiling_seconds() == 600 and ClaudeAdapter().supports_effort() is True
