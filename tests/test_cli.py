from click.testing import CliRunner

import meld.cli as cli
from meld.adapters import get_adapter
from meld.cli import main
from tests.conftest import PROJECT_TOML


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
