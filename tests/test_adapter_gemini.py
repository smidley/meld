from pathlib import Path

import meld.adapters.gemini as gemini_mod
from meld.adapters.base import LaunchSpec
from meld.adapters.gemini import GeminiAdapter
from meld.transcript import Tail

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
NOW = "2026-10-26T21:00:00Z"


def spec(tmp_path: Path, **over) -> LaunchSpec:
    base = dict(role="worker", model="gemini-3-pro-preview", effort="default", cwd=tmp_path, prompt="Read .meld-brief.md and do it.",
                log_path=tmp_path / "log.txt", timeout_seconds=1200, env={"PATH": "/usr/bin", "HOME": "/Users/x", "MELD_WORKER": "1"}, max_turns=200)
    base.update(over)
    return LaunchSpec(**base)


def test_argv_is_the_documented_headless_shape(tmp_path: Path):
    argv = GeminiAdapter().argv(spec(tmp_path))
    assert argv[0] == "gemini" and argv[argv.index("-p") + 1] == "Read .meld-brief.md and do it."
    assert argv[argv.index("-m") + 1] == "gemini-3-pro-preview"
    assert argv[argv.index("--output-format") + 1] == "stream-json" and argv[argv.index("--approval-mode") + 1] == "yolo"
    assert "--yolo" not in argv and "--effort" not in argv                       # deprecated alias; no effort setting exists
    assert set(GeminiAdapter().required_flags()) <= set(argv)


def test_env_trusts_the_worktree_and_keeps_the_rest(tmp_path: Path):
    s = spec(tmp_path)
    env = GeminiAdapter().env_for(s)
    assert env["GEMINI_CLI_TRUST_WORKSPACE"] == "1" and {k: env[k] for k in s.env} == s.env


def test_launch_uses_argv_and_log(tmp_path: Path, monkeypatch):
    captured = {}
    monkeypatch.setattr(gemini_mod, "popen_detached", lambda argv, spec_, log: captured.update(argv=argv, log=log.name) or "proc")
    assert GeminiAdapter().launch(spec(tmp_path)) == "proc"
    assert captured["argv"][0] == "gemini" and captured["log"] == str(tmp_path / "log.txt")


def test_probes_and_skills(tmp_path: Path):
    a = GeminiAdapter(home=tmp_path)
    (tmp_path / "skills" / "deploy").mkdir(parents=True)
    (tmp_path / "skills" / "deploy" / "SKILL.md").write_text("---\nname: deploy\ndescription: Ship it.\n---\n")
    assert [s.name for s in a.installed_skills()] == ["deploy"] and a.project_skills_dir() == ".gemini/skills"
    assert a.supports_effort() is False and a.login_argv() == [] and a.login_status("", "", 0) == (None, "this adapter has no login probe")
    assert a.version_argv() == ["gemini", "--version"] and a.help_argv()[0] == a.version_argv()[0]


def test_parse_events_renders_a_whole_gemini_run():
    events = Tail(FIXTURES / "gemini-run.jsonl").read(GeminiAdapter().parse_events, NOW)
    assert [e.kind for e in events] == ["raw", "run_started", "tool_call", "tool_result", "message", "message", "tool_call", "tool_result",
                                        "cli_error", "usage_limit", "raw", "run_finished"]
    assert events[1].meta == {"session_id": "abc123def", "model": "gemini-3-pro-preview"}
    assert events[2].tool == "Shell" and events[2].text == "meld status" and events[3].ok is True
    assert events[4].text == "The board is executing; " and events[4].meta["delta"] is True
    assert events[7].ok is False and "HALT" in events[7].text
    assert events[8].ok is False and "API key" in events[8].text
    assert events[9].meta.get("resets_at") == "2026-10-27T00:00:00Z"
    assert events[11].ok is True and events[11].meta["usage"]["tool_calls"] == 2
