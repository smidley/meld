import json
from pathlib import Path

import meld.adapters.claude as claude_mod
from meld.adapters.base import SCRUBBED_EXACT, LaunchSpec
from meld.adapters.claude import ClaudeAdapter
from meld.transcript import Tail, render

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
NOW = "2026-09-12T02:11:19Z"


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
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--strict-mcp-config" in argv and json.loads(argv[argv.index("--mcp-config") + 1]) == {"mcpServers": {}}
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings == {"disableAllHooks": True, "autoMemoryEnabled": False, "env": {name: "" for name in SCRUBBED_EXACT}}
    # every scrubbed name is blanked, so a user/project settings.json `env` block (loaded via --setting-sources) cannot re-inject one
    assert {"GH_TOKEN", "GITHUB_TOKEN", "MELD_GROUP", "MELD_ROLE", "MELD_PUSH_TOKEN"} <= set(settings["env"]) and set(settings["env"].values()) == {""}
    assert argv[argv.index("--setting-sources") + 1] == "user,project"
    assert argv[argv.index("--max-turns") + 1] == "200"
    assert "--effort" not in argv and "--bare" not in argv and "--dangerously-skip-permissions" not in argv


def test_argv_streams_events_and_verbose_is_required_for_it(tmp_path: Path):
    argv = ClaudeAdapter().argv(spec(tmp_path))
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    # claude -p refuses stream-json without --verbose, so the flag is part of the contract, not a preference.
    assert "--verbose" in argv
    assert "--include-partial-messages" not in argv          # token-level streaming is out of plan 8 (spec §21)
    assert "--verbose" in ClaudeAdapter().required_flags()


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


def test_parse_events_renders_a_whole_claude_run():
    events = Tail(FIXTURES / "claude-run.jsonl").read(ClaudeAdapter().parse_events, NOW)
    assert [e.kind for e in events] == [
        "run_started", "message", "tool_call", "tool_result", "thinking",
        "tool_call", "tool_result", "raw", "run_finished",
    ]
    assert render(events, width=96) == "\n".join([
        "02:11:19  started  claude-opus-5",
        "02:11:19  message  Checking the board first.",
        "02:11:19  tool     Bash: meld status",
        '02:11:19  result   ok · {"repo": "acme/widgets"}',
        "02:11:19  think    The audit failed on defects already fixed on main.",
        "02:11:19  tool     Bash: meld post STATUS -m 'board is clean'",
        "02:11:19  result   FAILED · refused: HALT is in effect",
        "02:11:19  raw      a bare line the CLI printed before its first event",
        "02:11:19  done     PR 15 merged.",
    ])


def test_parse_events_splits_one_assistant_line_into_its_content_blocks():
    line = ('{"type":"assistant","message":{"role":"assistant","content":['
            '{"type":"text","text":"Checking."},'
            '{"type":"tool_use","id":"toolu_9","name":"Bash","input":{"command":"meld status"}}]}}')
    events = ClaudeAdapter().parse_events(line, NOW)
    assert [(e.kind, e.tool, e.text) for e in events] == [
        ("message", None, "Checking."), ("tool_call", "Bash", "meld status")]


def test_parse_events_marks_a_failed_tool_result():
    line = ('{"type":"user","message":{"role":"user","content":[{"type":"tool_result",'
            '"tool_use_id":"toolu_2","content":"refused: HALT is in effect","is_error":true}]}}')
    (event,) = ClaudeAdapter().parse_events(line, NOW)
    assert (event.kind, event.ok, event.text) == ("tool_result", False, "refused: HALT is in effect")


def test_parse_events_reads_the_result_event_as_the_runs_end():
    line = ('{"type":"result","subtype":"success","is_error":false,"num_turns":89,'
            '"total_cost_usd":8.343978,"result":"PR 15 merged.","usage":{"output_tokens":76943}}')
    (event,) = ClaudeAdapter().parse_events(line, NOW)
    assert (event.kind, event.ok, event.text) == ("run_finished", True, "PR 15 merged.")
    assert event.meta["num_turns"] == 89 and event.meta["cost_usd"] == 8.343978


def test_parse_events_drops_a_partial_stream_event_and_keeps_an_unknown_type_as_raw():
    assert ClaudeAdapter().parse_events('{"type":"stream_event","event":{"type":"content_block_delta"}}', NOW) == []
    (unknown,) = ClaudeAdapter().parse_events('{"type":"weather_report","temp":21}', NOW)
    assert unknown.kind == "raw" and "weather_report" in unknown.text


def test_parse_events_renders_an_all_unknown_block_line_as_raw():
    """The most likely shape of a future Claude Code change is a new content block type inside an `assistant`
    line -- `thinking`, `redacted_thinking`, `server_tool_use` and `mcp_tool_use` all arrived that way. A line
    whose blocks are all unrecognized must degrade to `raw`, not disappear."""
    line = ('{"type":"assistant","message":{"role":"assistant","content":'
            '[{"type":"server_tool_use","id":"srvtoolu_1","name":"web_search","input":{"query":"x"}}]}}')
    (event,) = ClaudeAdapter().parse_events(line, NOW)
    assert event.kind == "raw" and "server_tool_use" in event.text


def test_parse_events_keeps_only_the_recognized_block_when_a_line_mixes_them():
    """The line itself WAS understood, so it does not also render as `raw`: that would duplicate the text."""
    line = ('{"type":"assistant","message":{"role":"assistant","content":'
            '[{"type":"text","text":"Searching."},{"type":"server_tool_use","id":"srvtoolu_2","name":"web_search"}]}}')
    events = ClaudeAdapter().parse_events(line, NOW)
    assert [(e.kind, e.text) for e in events] == [("message", "Searching.")]


def test_parse_events_never_raises_on_junk():
    (raw,) = ClaudeAdapter().parse_events("not json at all", NOW)
    assert raw.kind == "raw" and raw.text == "not json at all"
    (broken,) = ClaudeAdapter().parse_events('{"type": "assistant", "message": {', NOW)
    assert broken.kind == "raw"
    assert ClaudeAdapter().parse_events("   ", NOW) == []


def test_parse_events_turns_a_limited_result_into_usage_limit_not_run_finished():
    line = ('{"type":"result","subtype":"error_usage_limit","is_error":true,'
            '"result":"You\'ve hit your individual spend limit. It resets at 6pm.","num_turns":3}')
    (event,) = ClaudeAdapter().parse_events(line, "2026-09-12T02:11:19Z")
    assert event.kind == "usage_limit"
    assert event.meta["resets_at"] == "2026-09-12T18:00:00Z"
    assert "spend limit" in event.text


def test_parse_events_leaves_a_max_turns_result_an_ordinary_run_finished():
    line = '{"type":"result","subtype":"error_max_turns","is_error":true,"result":"","num_turns":10000}'
    (event,) = ClaudeAdapter().parse_events(line, "2026-09-12T02:11:19Z")
    assert event.kind == "run_finished" and event.ok is False


def test_parse_events_recognises_a_limit_on_a_bare_stderr_line():
    (event,) = ClaudeAdapter().parse_events("Claude AI usage limit reached|1789500000", "2026-09-12T02:11:19Z")
    assert event.kind == "usage_limit"


def test_a_successful_result_is_never_a_usage_limit_whatever_it_says():
    """The model's own narration must not trigger a freeze; only a CLI-reported error may."""
    line = ('{"type":"result","subtype":"success","is_error":false,"num_turns":12,'
            '"result":"I added middleware that returns 429 when the rate limit is exceeded, plus tests. '
            'Next scheduled deploy window is 2026-09-20T00:00:00Z."}')
    (event,) = ClaudeAdapter().parse_events(line, "2026-09-16T02:11:19Z")
    assert event.kind == "run_finished" and event.ok is True
    assert "resets_at" not in event.meta


def test_a_success_that_talks_about_a_rate_limit_is_still_a_success():
    line = ('{"type":"result","subtype":"success","is_error":false,"num_turns":5,'
            '"result":"You\'ve reached the rate limit for GitHub\'s API; retrying in a moment. Done."}')
    (event,) = ClaudeAdapter().parse_events(line, "2026-09-16T02:11:19Z")
    assert event.kind == "run_finished" and event.ok is True


def test_an_error_result_about_a_limit_is_still_a_usage_limit():
    line = ('{"type":"result","subtype":"error_during_execution","is_error":true,'
            '"result":"You have reached your usage limit for this model. It resets at 6pm."}')
    (event,) = ClaudeAdapter().parse_events(line, "2026-09-16T02:11:19Z")
    assert event.kind == "usage_limit" and event.meta["resets_at"] == "2026-09-16T18:00:00Z"


def test_login_status_reads_only_the_loggedin_key():
    """`meld doctor`'s cli-login probe: the `loggedIn` key of `claude auth status --json` is the oracle. Exit 0 with no
    such key, or output that is not JSON, is an answer nobody read -- None, so doctor reports NOT MEASURED, not a pass."""
    a = ClaudeAdapter()
    assert a.login_argv() == ["claude", "auth", "status", "--json"]
    assert a.login_status('{\n  "loggedIn": true,\n  "authMethod": "claude.ai",\n  "apiProvider": "firstParty"\n}\n', "", 0) == (True, "logged in (claude.ai)")
    assert a.login_status('{"loggedIn": false, "authMethod": null}', "", 1) == (False, "not logged in")
    assert a.login_status('{"apiProvider": "firstParty"}', "", 0)[0] is None
    assert a.login_status("", "error: unknown command 'auth'", 1)[0] is None
    assert a.login_status('{"loggedIn": "yes"}', "", 0)[0] is None


def test_cli_config_merges_under_the_frameworks_own_settings(tmp_path: Path):
    """Plan 28: a group's Claude settings ride along, but hooks stay disabled, memory off and credentials blanked."""
    import json as _json
    s = spec(tmp_path, cli_config={"model": "qwen3-coder", "disableAllHooks": False, "env": {"GH_TOKEN": "leak"}})
    argv = ClaudeAdapter().argv(s)
    settings = _json.loads(argv[argv.index("--settings") + 1])
    assert settings["model"] == "qwen3-coder" and settings["disableAllHooks"] is True and settings["env"]["GH_TOKEN"] == ""


def test_parse_events_turns_an_overloaded_result_into_a_capacity_event():
    """Plan 50: Anthropic's 529 overloaded_error is the model being busy, not the account out of quota."""
    import json
    line = json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True, "num_turns": 3,
                       "result": 'API Error: 529 {"type":"overloaded_error","message":"Overloaded"}'})
    (event,) = ClaudeAdapter().parse_events(line, NOW)
    assert (event.kind, event.ok) == ("capacity", False) and "529" in event.text
