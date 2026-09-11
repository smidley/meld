from pathlib import Path

import meld.adapters.codex as codex_mod
from meld.adapters.base import LaunchSpec
from meld.adapters.codex import CodexAdapter


def spec(tmp_path: Path, **over) -> LaunchSpec:
    base = dict(role="worker", model="gpt-5.6-terra", effort="high", cwd=tmp_path, prompt="Read .meld-brief.md and do it.",
                log_path=tmp_path / "log.txt", timeout_seconds=1200, env={"PATH": "/usr/bin", "HOME": "/Users/x", "MELD_WORKER": "1"}, max_turns=200)
    base.update(over)
    return LaunchSpec(**base)


def test_argv_is_the_documented_unattended_shape(tmp_path: Path):
    argv = CodexAdapter().argv(spec(tmp_path))
    assert argv[:2] == ["codex", "exec"]
    assert argv[argv.index("-m") + 1] == "gpt-5.6-terra"
    assert argv[argv.index("-c") + 1] == 'model_reasoning_effort="high"'
    role = spec(tmp_path).role
    assert argv[argv.index("-s") + 1] == ("workspace-write" if role == "worker" else "danger-full-access") and "-a" not in argv
    assert ("sandbox_workspace_write.network_access=true" in argv) == (role == "worker")   # exec never prompts; the sandbox is per role
    assert argv[argv.index("-C") + 1] == str(tmp_path)
    assert "--json" in argv and "--ignore-user-config" in argv and "--ephemeral" in argv
    assert argv[argv.index("-o") + 1] == str(tmp_path / ".meld-last-message.txt")
    assert argv[-1] == "Read .meld-brief.md and do it."
    assert "--full-auto" not in argv and "--dangerously-bypass-approvals-and-sandbox" not in argv


def test_env_is_passed_through_unchanged(tmp_path: Path):
    s = spec(tmp_path)
    assert CodexAdapter().env_for(s) == s.env


def test_launch_uses_argv_and_log(tmp_path: Path, monkeypatch):
    captured = {}

    def fake_popen(argv, spec_, log):
        captured.update(argv=argv, spec=spec_, log_name=log.name)
        return "proc"

    monkeypatch.setattr(codex_mod, "popen_detached", fake_popen)
    s = spec(tmp_path)
    assert CodexAdapter().launch(s) == "proc"
    assert captured["argv"][:2] == ["codex", "exec"] and captured["spec"].cwd == tmp_path and captured["log_name"] == str(tmp_path / "log.txt")


def test_installed_skills_scans_agents_and_codex_dirs_but_not_system(tmp_path: Path):
    (tmp_path / ".agents" / "skills" / "deploy").mkdir(parents=True)
    (tmp_path / ".agents" / "skills" / "deploy" / "SKILL.md").write_text("---\nname: deploy\ndescription: Ship it.\n---\n")
    (tmp_path / ".codex" / "skills" / ".system" / "skill-creator").mkdir(parents=True)
    (tmp_path / ".codex" / "skills" / ".system" / "skill-creator" / "SKILL.md").write_text("---\nname: skill-creator\n---\n")
    (tmp_path / ".codex" / "skills" / "review").mkdir(parents=True)
    (tmp_path / ".codex" / "skills" / "review" / "SKILL.md").write_text("---\nname: review\ndescription: Review.\n---\n")
    names = [s.name for s in CodexAdapter(home=tmp_path).installed_skills()]
    assert names == ["deploy", "review"]
    assert CodexAdapter().command_timeout_ceiling_seconds() == 600 and CodexAdapter().supports_effort() is True
