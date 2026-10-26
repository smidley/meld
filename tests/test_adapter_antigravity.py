from pathlib import Path

import meld.adapters.antigravity as agy_mod
from meld.adapters.antigravity import AntigravityAdapter
from meld.adapters.base import LaunchSpec
from meld.transcript import Tail

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
NOW = "2026-10-26T21:00:00Z"


def spec(tmp_path: Path, **over) -> LaunchSpec:
    base = dict(role="worker", model="claude-sonnet-4-6", effort="high", cwd=tmp_path, prompt="Read .meld-brief.md and do it.",
                log_path=tmp_path / "log.txt", timeout_seconds=1200, env={"PATH": "/usr/bin", "HOME": "/Users/x", "MELD_WORKER": "1"}, max_turns=200)
    base.update(over)
    return LaunchSpec(**base)


def test_argv_is_the_verified_print_mode_shape(tmp_path: Path):
    """agy 1.4.7 (2026-10-26): -p, --model, --effort, --output-format stream-json, --dangerously-skip-permissions are all in `agy --help`."""
    a = AntigravityAdapter()
    argv = a.argv(spec(tmp_path))
    assert argv[0] == "agy" and argv[argv.index("-p") + 1] == "Read .meld-brief.md and do it."
    assert argv[argv.index("--model") + 1] == "claude-sonnet-4-6" and argv[argv.index("--effort") + 1] == "high"
    assert argv[argv.index("--output-format") + 1] == "stream-json" and "--dangerously-skip-permissions" in argv
    assert set(a.required_flags()) <= set(argv) and a.help_argv()[0] == a.version_argv()[0] == "agy"


def test_launch_and_probes(tmp_path: Path, monkeypatch):
    captured = {}
    monkeypatch.setattr(agy_mod, "popen_detached", lambda argv, spec_, log: captured.update(argv=argv) or "proc")
    assert AntigravityAdapter().launch(spec(tmp_path)) == "proc" and captured["argv"][0] == "agy"
    a = AntigravityAdapter(home=tmp_path)
    assert a.installed_skills() == [] and a.project_skills_dir() is None and a.supports_effort() is True and a.login_argv() == ["agy", "models"]
    assert a.login_status("Fetching available models...\ngemini-3.8-flash-high\tGemini 3.8 Flash (High)\n", "", 0) == (True, "logged in (1 models offered)")
    assert a.login_status("", "please sign in first", 1)[0] is False and a.login_status("", "", 3)[0] is None


def test_parse_events_joins_deltas_per_step_and_reads_tools_and_the_result():
    a = AntigravityAdapter()
    events = Tail(FIXTURES / "antigravity-run.jsonl").read(a.parse_events, NOW)
    assert [e.kind for e in events] == ["run_started", "message", "usage", "tool_call", "tool_result", "tool_call", "tool_result",
                                        "message", "usage", "raw", "run_finished"]
    assert events[1].text == "I'll list the files first.\n" and events[2].meta["usage"]["output_tokens"] == 37
    assert events[3].tool == "run_command" and events[3].text == "ls -la"
    assert events[4].ok is False and "denied permission" in events[4].text
    assert events[5].text == "/tmp/wt/README.md" and events[6].ok is True and events[6].text == "# probe"
    assert events[7].text == "ok\n" and events[10].ok is True and events[10].meta["denied_actions"][0]["action"] == "command"
    assert a._partial == {}                                    # every step reached DONE, so nothing is left buffered


def test_a_model_with_its_level_in_the_name_gets_no_separate_effort(tmp_path: Path):
    """agy 1.4.7 refuses `--model gemini-3.8-flash-high --effort low` ("conflicts"), found by the first smoke run."""
    from meld.adapters.antigravity import baked_level
    assert baked_level("gemini-3.8-flash-high") == "high" and baked_level("gpt-oss-120b-medium") == "medium" and baked_level("claude-sonnet-4-6") is None
    argv = AntigravityAdapter().argv(spec(tmp_path, model="gemini-3.8-flash-high", effort="high"))
    assert "--effort" not in argv and argv[argv.index("--model") + 1] == "gemini-3.8-flash-high"
