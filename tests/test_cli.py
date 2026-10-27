from click.testing import CliRunner

import meld.cli as cli
from meld.adapters import get_adapter
from meld.cli import main
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.fakegh import FakeGh


def test_version_flag_prints_version():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


CLAUDE_LINE = ('{"type":"assistant","message":{"role":"assistant","content":'
               '[{"type":"text","text":"Checking the board first."}]}}')


def _tail_home(tmp_path, monkeypatch, line=CLAUDE_LINE):
    config = tmp_path / ".meld.toml"
    config.write_text(PROJECT_TOML)
    logs = tmp_path / "home" / "logs" / "claude"
    logs.mkdir(parents=True)
    (logs / "orchestrator-20260912T020000Z.log").write_text(line + "\n")
    monkeypatch.setenv("MELD_HOME", str(tmp_path / "home"))
    # The suite's autouse hermetic-adapter fixture (conftest._hermetic_cli_adapter) resolves every group to a
    # skill-less FakeAdapter so CLI tests never scan a real ~/.claude or ~/.codex. tail's own tests need the real
    # Claude adapter's stream-json parsing instead; parse_events touches no filesystem, so restoring it here for
    # this group of tests does not reopen that hole.
    monkeypatch.setattr(cli, "ADAPTER_FOR", get_adapter)
    return config


def test_tail_renders_the_newest_log_once_without_following(tmp_path, monkeypatch):
    config = _tail_home(tmp_path, monkeypatch)
    result = CliRunner().invoke(main, ["--config", str(config), "tail", "--no-follow"])
    assert result.exit_code == 0
    assert "message  Checking the board first." in result.output


def test_tail_names_the_log_it_chose_on_stderr_not_in_the_transcript(tmp_path, monkeypatch):
    config = _tail_home(tmp_path, monkeypatch)
    result = CliRunner().invoke(main, ["--config", str(config), "tail", "--no-follow"])
    assert "orchestrator-20260912T020000Z.log" in result.stderr
    assert "orchestrator-20260912T020000Z.log" not in result.stdout


def test_tail_accepts_a_group_and_role_target(tmp_path, monkeypatch):
    config = _tail_home(tmp_path, monkeypatch)
    result = CliRunner().invoke(main, ["--config", str(config), "tail", "claude/orchestrator", "--no-follow"])
    assert result.exit_code == 0 and "Checking the board first." in result.output


def test_tail_explains_itself_when_there_is_no_log(tmp_path, monkeypatch):
    config = tmp_path / ".meld.toml"
    config.write_text(PROJECT_TOML)
    monkeypatch.setenv("MELD_HOME", str(tmp_path / "empty"))
    result = CliRunner().invoke(main, ["--config", str(config), "tail", "--no-follow"])
    assert result.exit_code != 0
    assert "no run log" in result.output


def test_tail_refuses_an_unknown_group(tmp_path, monkeypatch):
    config = _tail_home(tmp_path, monkeypatch)
    result = CliRunner().invoke(main, ["--config", str(config), "tail", "ghost/pm", "--no-follow"])
    assert result.exit_code != 0 and "ghost" in result.output


def test_tail_without_follow_renders_the_unterminated_last_line(tmp_path, monkeypatch):
    """A one-shot read is the end of the reader's life, so the residue a live reader withholds is flushed."""
    config = _tail_home(tmp_path, monkeypatch)
    log = tmp_path / "home" / "logs" / "claude" / "orchestrator-20260912T020000Z.log"
    with log.open("ab") as f:
        f.write(b'{"type":"assistant","message":{"role":"assistant","content":[{"type":"tex')
    result = CliRunner().invoke(main, ["--config", str(config), "tail", "--no-follow"])
    assert result.exit_code == 0
    assert "message  Checking the board first." in result.output
    assert 'raw      {"type":"assistant"' in result.output


class _ServerStandIn:
    server_address = ("127.0.0.1", 4321)

    def __init__(self):
        self.served = self.closed = False

    def serve_forever(self):
        self.served = True

    def server_close(self):
        self.closed = True


def _watch(tmp_path, monkeypatch, args, env_extra=None, bind_error: OSError | None = None):
    config = tmp_path / ".meld.toml"
    config.write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    monkeypatch.setattr(cli, "RUN", fake)
    seen = {}

    def fake_serve(viewer, *, host="127.0.0.1", port=0):
        seen["viewer"], seen["port"] = viewer, port
        if bind_error is not None:
            raise bind_error
        seen["server"] = _ServerStandIn()
        viewer.port = 4321
        return seen["server"]

    monkeypatch.setattr(cli, "SERVE", fake_serve)
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda link: opened.append(link) or True)
    env = {**env_for(tmp_path, group=None), **(env_extra or {})}
    result = CliRunner().invoke(main, ["--config", str(config), "watch", *args], env=env)
    return result, seen, opened


def test_watch_prints_exactly_the_url_with_a_fresh_token_and_serves_until_stopped(tmp_path, monkeypatch):
    result, seen, opened = _watch(tmp_path, monkeypatch, [])
    assert result.exit_code == 0, result.output
    token = seen["viewer"].token
    assert len(token) >= 43 and result.output == f"http://127.0.0.1:4321/?t={token}\n"
    assert seen["port"] == 0                         # [viewer] port's default: the OS chooses
    assert seen["server"].served and seen["server"].closed and opened == []


def test_watch_honours_port_and_open_flags(tmp_path, monkeypatch):
    result, seen, opened = _watch(tmp_path, monkeypatch, ["--port", "4321", "--open"])
    assert result.exit_code == 0, result.output
    assert seen["port"] == 4321 and opened == [result.output.strip()]


def test_watch_hands_the_viewer_the_config_file_it_loaded(tmp_path, monkeypatch):
    result, seen, _ = _watch(tmp_path, monkeypatch, [])
    assert result.exit_code == 0, result.output
    assert seen["viewer"].config_path == tmp_path / ".meld.toml"


def test_watch_is_refused_to_a_worker_like_every_other_verb(tmp_path, monkeypatch):
    result, seen, _ = _watch(tmp_path, monkeypatch, [], env_extra={"MELD_WORKER": "1"})
    assert result.exit_code != 0 and "viewer" not in seen


def test_watch_is_the_humans_console_and_refuses_a_group_or_pm(tmp_path, monkeypatch):
    """The page is the human's console end to end; a group party would read its own status and post as itself,
    which section 22 forbids outright -- refused before a socket is ever bound."""
    result, seen, _ = _watch(tmp_path, monkeypatch, [], env_extra={"MELD_GROUP": "claude", "MELD_ROLE": "orchestrator"})
    assert result.exit_code != 0 and "human's console" in result.output and "viewer" not in seen


def test_watch_reports_a_port_it_cannot_bind_as_a_click_error(tmp_path, monkeypatch):
    """A port already taken is something the person fixes by naming another one, not a traceback to read."""
    result, seen, opened = _watch(tmp_path, monkeypatch, ["--port", "4321"],
                                  bind_error=OSError(48, "Address already in use"))
    assert result.exit_code != 0
    assert "cannot bind 127.0.0.1:4321" in result.output and "Address already in use" in result.output
    assert "Traceback" not in result.output
    assert "server" not in seen and opened == []       # nothing served, no browser opened


def test_tail_with_follow_withholds_the_unterminated_last_line(tmp_path, monkeypatch):
    """The same bytes while following are a line the CLI has not finished writing, so they stay held."""
    config = _tail_home(tmp_path, monkeypatch)
    log = tmp_path / "home" / "logs" / "claude" / "orchestrator-20260912T020000Z.log"
    with log.open("ab") as f:
        f.write(b'{"type":"assistant","message":{"role":"assistant","content":[{"type":"tex')

    def fake_sleep(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "SLEEP", fake_sleep)
    result = CliRunner().invoke(main, ["--config", str(config), "tail", "--follow"])
    assert result.exit_code == 0
    assert "message  Checking the board first." in result.output
    assert "raw" not in result.output


def test_tail_refuses_to_run_inside_a_worker(tmp_path, monkeypatch):
    """`tail` needs no board and so never calls load_context, where the refusal lives -- but spec section 18
    says `meld` itself refuses to run with MELD_WORKER=1, and a worker reading the *other* group's
    orchestrator transcript through a framework verb is exactly what that sentence forbids."""
    config = _tail_home(tmp_path, monkeypatch)
    monkeypatch.setenv("MELD_WORKER", "1")
    result = CliRunner().invoke(main, ["--config", str(config), "tail", "--no-follow"])
    assert result.exit_code != 0
    assert "meld may not be run by a worker" in result.output
    assert "Checking the board first." not in result.output


def test_tail_follows_until_the_sleep_hook_stops_it(tmp_path, monkeypatch):
    """--follow loops; the suite never sleeps, so SLEEP raises to end the loop deterministically."""
    config = _tail_home(tmp_path, monkeypatch)
    calls = []

    def fake_sleep(seconds):
        calls.append(seconds)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "SLEEP", fake_sleep)
    result = CliRunner().invoke(main, ["--config", str(config), "tail", "--follow", "--interval", "0.5"])
    assert result.exit_code == 0
    assert calls == [0.5]
    assert "Checking the board first." in result.output


def test_bare_meld_is_start_which_is_watch_here_and_the_wizard_elsewhere(tmp_path, monkeypatch):
    """Plan 32: `meld` with no subcommand opens the board when a .meld.toml is found and the setup wizard when it is not."""
    import os
    from tests.fakeadapter import FakeAdapter
    monkeypatch.setattr(cli, "ADAPTER_FOR", lambda name: FakeAdapter())
    # with a configuration: start is watch
    result, seen, opened = _watch(tmp_path, monkeypatch, [])
    assert result.exit_code == 0 and seen["viewer"].ctx is not None
    # the board carries a setup session too, so /setup/app/<group>/ works after the wizard is gone
    board = seen["viewer"]
    assert board.setup is not None and board.setup.created["groups"] == list(board.ctx.cfg.groups) and board.setup.base_url == "http://127.0.0.1:4321"
    # without one: a Viewer in setup mode over a SetupSession, served and opened the same way
    fake = FakeGh()
    fake.route("GET", "user", {"login": "empowerScott"})
    monkeypatch.setattr(cli, "RUN", fake)
    seen2 = {}

    def fake_serve(viewer, *, host="127.0.0.1", port=0):
        seen2["viewer"], seen2["port"] = viewer, port
        viewer.port = 4321
        return _ServerStandIn()

    monkeypatch.setattr(cli, "SERVE", fake_serve)
    opened2 = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda link: opened2.append(link) or True)
    empty = tmp_path.with_name(tmp_path.name + "-empty")   # a sibling, not a child: find_config searches upward and would find _watch's file
    empty.mkdir()
    monkeypatch.chdir(empty)
    env = {k: v for k, v in env_for(tmp_path, group=None).items() if k != "MELD_HOME"}
    env["MELD_HOME"] = str(tmp_path / "home")
    result = CliRunner().invoke(main, [], env=env)
    assert result.exit_code == 0, result.output
    v = seen2["viewer"]
    assert v.ctx is None and v.setup is not None and v.setup.target_dir == empty and seen2["port"] == 8765
    assert v.setup.base_url == "http://127.0.0.1:4321" and v.setup.token == v.token
    assert result.output.strip() == f"http://127.0.0.1:4321/?t={v.token}" and opened2 == [result.output.strip()]
