from pathlib import Path

import meld.adapters.grok as grok_mod
from meld.adapters.base import LaunchSpec
from meld.adapters.grok import GrokAdapter
from meld.transcript import Tail

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
NOW = "2026-10-26T21:00:00Z"


def spec(tmp_path: Path, **over) -> LaunchSpec:
    base = dict(role="worker", model="grok-4.7", effort="high", cwd=tmp_path, prompt="Read .meld-brief.md and do it.",
                log_path=tmp_path / "log.txt", timeout_seconds=1200, env={"PATH": "/usr/bin", "HOME": "/Users/x", "MELD_WORKER": "1"}, max_turns=200)
    base.update(over)
    return LaunchSpec(**base)


def test_argv_is_the_verified_headless_shape(tmp_path: Path):
    """grok 1.0.70 (2026-10-26): every flag below is in `grok --help` except --no-auto-update, which is accepted but undocumented."""
    argv = GrokAdapter().argv(spec(tmp_path))
    assert argv[:2] == ["grok", "--no-auto-update"]
    assert argv[argv.index("-p") + 1] == "Read .meld-brief.md and do it." and argv[argv.index("-m") + 1] == "grok-4.7"
    assert argv[argv.index("--effort") + 1] == "high" and argv[argv.index("--output-format") + 1] == "streaming-messages-json"
    assert "--always-approve" in argv and argv[argv.index("--cwd") + 1] == str(tmp_path) and argv[argv.index("--max-turns") + 1] == "200"
    assert set(GrokAdapter().required_flags()) <= set(argv) and "--no-auto-update" not in GrokAdapter().required_flags()


def test_launch_env_and_probes(tmp_path: Path, monkeypatch):
    captured = {}
    monkeypatch.setattr(grok_mod, "popen_detached", lambda argv, spec_, log: captured.update(argv=argv, env=spec_.env) or "proc")
    s = spec(tmp_path)
    assert GrokAdapter().launch(s) == "proc" and captured["env"] == s.env
    a = GrokAdapter(home=tmp_path)
    assert a.installed_skills() == [] and a.project_skills_dir() is None and a.supports_effort() is True
    assert a.version_argv() == ["grok", "version"] and a.help_argv() == ["grok", "--help"] and a.login_argv() == ["grok", "models"]
    assert a.login_status("You are logged in with grok.com.\n\nDefault model: grok-4.7\n", "", 0) == (True, "You are logged in with grok.com.")
    assert a.login_status("", "Please log in with `grok login`", 1)[0] is False
    assert a.login_status("???", "", 0)[0] is None


def test_parse_events_renders_a_whole_grok_run_through_the_claude_parser():
    """`streaming-messages-json` is Claude Code's stream-json, so the run reads exactly as a Claude run does."""
    events = Tail(FIXTURES / "grok-run.jsonl").read(GrokAdapter().parse_events, NOW)
    assert [e.kind for e in events] == ["raw", "run_started", "thinking", "tool_call", "tool_result", "message", "run_finished"]
    assert events[1].meta["model"] == "grok-4.7" and events[3].tool == "list_dir" and events[4].ok is True
    assert events[5].text == "ok" and events[6].ok is True and events[6].meta["cost_usd"] == 0.087652 and events[6].meta["num_turns"] == 2


def test_a_tool_result_envelope_is_unwrapped_into_the_text_a_person_would_read():
    """grok 1.0.70 wraps a command's output as a byte array inside its own envelope; the transcript showed numbers."""
    a = GrokAdapter()
    line = ('{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"c1","content":'
            '"{\\"type\\":\\"Bash\\",\\"output\\":[104,105,10]}","is_error":false}]}}')
    (e,) = a.parse_events(line, NOW)
    assert e.kind == "tool_result" and e.text == "hi\n" and e.meta["envelope"] == "Bash"
    line2 = ('{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"c2","content":'
             '"{\\"type\\":\\"ReadFile\\",\\"FileContent\\":{\\"content\\":\\"# tally\\"}}","is_error":false}]}}')
    (e2,) = a.parse_events(line2, NOW)
    assert e2.text == "# tally" and e2.meta["envelope"] == "ReadFile"
    (plain,) = a.parse_events('{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"c3","content":"just text","is_error":true}]}}', NOW)
    assert plain.text == "just text" and plain.ok is False
