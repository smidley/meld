from pathlib import Path

import meld.adapters.codex as codex_mod
from meld.adapters.base import LaunchSpec
from meld.adapters.codex import CodexAdapter
from meld.transcript import Tail, render_line

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
NOW = "2026-09-12T02:11:19Z"


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
    assert spec(tmp_path).role == "worker"                                         # this file's spec is a worker launch
    assert argv[argv.index("-s") + 1] == "workspace-write" and "sandbox_workspace_write.network_access=true" in argv
    assert "-a" not in argv                                                          # exec never prompts; roles get danger-full-access (tests/test_adapters.py)
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


def test_parse_events_renders_a_whole_codex_run():
    events = Tail(FIXTURES / "codex-run.jsonl").read(CodexAdapter().parse_events, NOW)
    assert [e.kind for e in events] == [
        "raw", "run_started", "message", "tool_call", "tool_result",
        "tool_call", "tool_result", "raw", "usage",
    ]
    assert events[0].text.startswith("2026-09-12T02:11:19.320141Z ERROR")


def test_parse_events_pairs_a_command_start_with_its_exit_code():
    start = ('{"type": "item.started", "item": {"id": "i1", "type": "command_execution", '
             '"command": "meld status", "exit_code": null, "status": "in_progress"}}')
    done = ('{"type": "item.completed", "item": {"id": "i1", "type": "command_execution", '
            '"command": "meld status", "aggregated_output": "ok", "exit_code": 0, "status": "completed"}}')
    (call,) = CodexAdapter().parse_events(start, NOW)
    (result,) = CodexAdapter().parse_events(done, NOW)
    assert (call.kind, call.tool, call.text) == ("tool_call", "command", "meld status")
    assert (result.kind, result.ok, result.meta["exit_code"]) == ("tool_result", True, 0)


def test_parse_events_marks_a_nonzero_exit_as_failed():
    done = ('{"type": "item.completed", "item": {"id": "i2", "type": "command_execution", '
            '"command": "meld dispatch 45", "aggregated_output": "refused", "exit_code": 1}}')
    (result,) = CodexAdapter().parse_events(done, NOW)
    assert result.ok is False and result.meta["exit_code"] == 1


def test_parse_events_drops_turn_started_and_an_in_progress_message():
    assert CodexAdapter().parse_events('{"type": "turn.started"}', NOW) == []
    assert CodexAdapter().parse_events('{"type": "item.started", "item": {"type": "agent_message", "text": "x"}}', NOW) == []


def test_parse_events_reports_a_failed_turn_as_a_cli_error():
    """`cli_error` is the only kind exclusive to one adapter: Codex's `turn.failed` is the only thing that
    produces it, so nothing else in the suite can cover the kind, its `ok=False`, or its rendered label."""
    line = '{"type": "turn.failed", "error": {"message": "stream disconnected before completion"}}'
    (event,) = CodexAdapter().parse_events(line, NOW)
    assert (event.kind, event.ok, event.text) == ("cli_error", False, "stream disconnected before completion")
    assert render_line(event) == "02:11:19  error    stream disconnected before completion"


def test_parse_events_reports_a_failed_turn_with_no_message_at_all():
    (event,) = CodexAdapter().parse_events('{"type": "turn.failed"}', NOW)
    assert (event.kind, event.ok, event.text) == ("cli_error", False, "turn failed")


def test_parse_events_maps_a_completed_reasoning_item_to_thinking():
    line = ('{"type": "item.completed", "item": {"id": "item_4", "type": "reasoning", '
            '"text": "The audit failed on defects already fixed on main."}}')
    (event,) = CodexAdapter().parse_events(line, NOW)
    assert (event.kind, event.text) == ("thinking", "The audit failed on defects already fixed on main.")
    assert render_line(event) == "02:11:19  think    The audit failed on defects already fixed on main."
    # Only the completed form carries content, the same as an agent_message.
    assert CodexAdapter().parse_events('{"type": "item.started", "item": {"type": "reasoning", "text": "half"}}', NOW) == []


def test_parse_events_formats_usage_as_sorted_key_value_pairs():
    line = '{"type": "turn.completed", "usage": {"output_tokens": 800, "input_tokens": 12000, "cached_input_tokens": 9568}}'
    (event,) = CodexAdapter().parse_events(line, NOW)
    assert event.kind == "usage"
    assert event.text == "cached_input_tokens=9568, input_tokens=12000, output_tokens=800"
    assert event.meta["usage"]["input_tokens"] == 12000
    assert render_line(event) == "02:11:19  usage    cached_input_tokens=9568, input_tokens=12000, output_tokens=800"
    # A turn that reported no usage still ends the turn, with nothing to say about it.
    (empty,) = CodexAdapter().parse_events('{"type": "turn.completed"}', NOW)
    assert (empty.kind, empty.text) == ("usage", "")


def test_parse_events_keeps_an_unknown_item_type_as_raw():
    (raw,) = CodexAdapter().parse_events('{"type": "item.completed", "item": {"type": "web_search", "query": "x"}}', NOW)
    assert raw.kind == "raw" and "web_search" in raw.text


def test_parse_events_never_raises_on_junk():
    (raw,) = CodexAdapter().parse_events("thread panicked at src/main.rs:12", NOW)
    assert raw.kind == "raw"
    assert CodexAdapter().parse_events("", NOW) == []


def test_parse_events_turns_a_rate_limited_turn_failure_into_usage_limit():
    line = '{"type": "turn.failed", "error": {"message": "429 Too Many Requests; rate limited"}}'
    (event,) = CodexAdapter().parse_events(line, "2026-09-12T02:11:19Z")
    assert event.kind == "usage_limit" and event.ok is False


def test_parse_events_leaves_an_ordinary_turn_failure_a_cli_error():
    line = '{"type": "turn.failed", "error": {"message": "sandbox denied write to /etc"}}'
    (event,) = CodexAdapter().parse_events(line, "2026-09-12T02:11:19Z")
    assert event.kind == "cli_error" and event.ok is False


def test_parse_events_recognises_a_limit_on_a_bare_stderr_line():
    (event,) = CodexAdapter().parse_events(
        "2026-09-12T02:11:19Z ERROR codex_core: quota exceeded for this organization", "2026-09-12T02:11:19Z")
    assert event.kind == "usage_limit"


def test_login_status_reads_the_words_not_the_exit_code():
    """`meld doctor`'s cli-login probe: `codex login status` says `Logged in using ...` or `Not logged in`; anything
    else is an answer nobody read -- None, so doctor reports NOT MEASURED rather than trusting the exit code."""
    a = CodexAdapter()
    assert a.login_argv() == ["codex", "login", "status"]
    assert a.login_status("Logged in using ChatGPT\n", "", 0) == (True, "Logged in using ChatGPT")
    assert a.login_status("", "Logged in using API key\n", 0) == (True, "Logged in using API key")
    assert a.login_status("Not logged in\n", "", 1) == (False, "not logged in")
    assert a.login_status("", "", 0)[0] is None
    assert a.login_status("error: unrecognized subcommand 'status'", "", 2)[0] is None


def test_cli_config_becomes_c_overrides_in_key_order(tmp_path: Path):
    """Plan 28: a custom provider for an ephemeral `codex exec`, which reads no config file."""
    s = spec(tmp_path, cli_config={"model_providers.ollama.base_url": "http://server:11434/v1", "model_provider": "ollama",
                                   "disable_response_storage": True, "retries": 2})
    argv = CodexAdapter().argv(s)
    pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
    assert pairs == ['model_reasoning_effort="high"', "disable_response_storage=true", 'model_provider="ollama"',
                     'model_providers.ollama.base_url="http://server:11434/v1"', "retries=2", "sandbox_workspace_write.network_access=true"]


def test_parse_events_turns_a_model_at_capacity_into_a_capacity_event_not_a_cli_error():
    """Plan 50: "Selected model is at capacity. Please try a different model." killed the first real run's worker on #3 and
    meld counted it as a failed attempt. It is transient: its own event kind, which the supervisor and collect retry."""
    for line in ('{"type": "turn.failed", "error": {"message": "Selected model is at capacity. Please try a different model."}}',
                 '{"type": "error", "message": "Selected model is at capacity. Please try a different model."}'):
        (event,) = CodexAdapter().parse_events(line, NOW)
        assert (event.kind, event.ok) == ("capacity", False) and "at capacity" in event.text
