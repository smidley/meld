from pathlib import Path

import meld.adapters.grok as grok_mod
from meld.adapters.base import LaunchSpec
from meld.adapters.grok import GrokAdapter
from meld.transcript import Tail

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
NOW = "2026-10-26T21:00:00Z"


def spec(tmp_path: Path, **over) -> LaunchSpec:
    base = dict(role="worker", model="grok-build-0.1", effort="high", cwd=tmp_path, prompt="Read .meld-brief.md and do it.",
                log_path=tmp_path / "log.txt", timeout_seconds=1200, env={"PATH": "/usr/bin", "HOME": "/Users/x", "MELD_WORKER": "1"}, max_turns=200)
    base.update(over)
    return LaunchSpec(**base)


def test_argv_is_the_documented_headless_shape(tmp_path: Path):
    argv = GrokAdapter().argv(spec(tmp_path))
    assert argv[:2] == ["grok", "--no-auto-update"]
    assert argv[argv.index("-p") + 1] == "Read .meld-brief.md and do it." and argv[argv.index("-m") + 1] == "grok-build-0.1"
    assert argv[argv.index("--effort") + 1] == "high" and argv[argv.index("--output-format") + 1] == "streaming-json"
    assert "--always-approve" in argv and argv[argv.index("--cwd") + 1] == str(tmp_path) and argv[argv.index("--max-turns") + 1] == "200"
    assert set(GrokAdapter().required_flags()) <= set(argv)


def test_launch_env_and_probes(tmp_path: Path, monkeypatch):
    captured = {}
    monkeypatch.setattr(grok_mod, "popen_detached", lambda argv, spec_, log: captured.update(argv=argv, env=spec_.env) or "proc")
    s = spec(tmp_path)
    assert GrokAdapter().launch(s) == "proc" and captured["env"] == s.env
    a = GrokAdapter()
    assert a.installed_skills() == [] and a.project_skills_dir() is None and a.supports_effort() is True
    assert a.version_argv() == ["grok", "version"] and a.help_argv() == ["grok", "--help"] and a.login_argv() == []


def test_parse_events_renders_a_whole_grok_run_and_keeps_what_it_cannot_read():
    events = Tail(FIXTURES / "grok-run.jsonl").read(GrokAdapter().parse_events, NOW)
    assert [e.kind for e in events] == ["raw", "thinking", "message", "tool_call", "tool_result", "tool_call", "tool_result",
                                        "usage", "usage_limit", "raw", "run_finished"]
    assert events[3].tool == "shell" and events[3].text == "meld status" and events[3].meta["id"] == "call_1"
    assert events[4].ok is True and "executing" in events[4].text         # the running update carried nothing; the completed one did
    assert events[5].text == "src/x.py" and events[6].ok is False and events[6].text == "file not found"
    assert events[7].meta["usage"] == {"input_tokens": 1200, "output_tokens": 300, "cost_usd": 0.004}
    assert events[8].meta.get("resets_at")                                  # "try again in 30 minutes" is a limit with a reset
    assert events[9].text.startswith('{"type":"plan"')                       # an undocumented type is kept, never dropped
    assert events[10].ok is True and events[10].text == "end_turn"
