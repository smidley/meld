import json
from pathlib import Path

from click.testing import CliRunner

import meld.cli as cli
from meld.adapters import UnknownAdapter
from meld.context import load_context
from meld.roster import WorkerType
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.fakeadapter import FakeAdapter
from tests.fakegh import FakeGh

SKILL = WorkerType("marketing", "Launch copy and positioning.", "skill", None, "---\nname: marketing\n---\n")


def test_context_roster_includes_adapter_skills_for_groups_only(tmp_path: Path):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    factory = lambda cli_name: FakeAdapter(skills=[SKILL])  # noqa: E731
    ctx = load_context(tmp_path / ".meld.toml", env=env_for(tmp_path), run=fake, adapter_for=factory)
    assert ctx.adapter() is not None and "marketing" in ctx.actor().roster
    assert [w.name for w in ctx.roster_entries()][:2] == ["auditor", "backend"] and "marketing" in [w.name for w in ctx.roster_entries()]
    human = load_context(tmp_path / ".meld.toml", env=env_for(tmp_path, group=None), run=fake, adapter_for=factory)
    assert human.adapter() is None and human.actor().roster == frozenset()


def test_roster_command(tmp_path: Path, monkeypatch):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    monkeypatch.setattr(cli, "RUN", fake)
    monkeypatch.setattr(cli, "ADAPTER_FOR", lambda cli_name: FakeAdapter(skills=[SKILL]))
    result = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "roster"], env=env_for(tmp_path))
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert {"name": "marketing", "description": "Launch copy and positioning.", "source": "skill"} in data
    assert [d["name"] for d in data][:2] == ["auditor", "backend"]


def test_adapter_and_roster_memoized(tmp_path: Path):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    call_count = [0]
    def counting_factory(cli_name: str):
        call_count[0] += 1
        return FakeAdapter(skills=[SKILL])
    ctx = load_context(tmp_path / ".meld.toml", env=env_for(tmp_path), run=fake, adapter_for=counting_factory)
    assert ctx.adapter() is ctx.adapter()
    assert ctx.roster_entries() is ctx.roster_entries()
    assert ctx.actor()
    assert ctx.actor()
    assert call_count[0] == 1


def test_roster_command_catches_unknown_adapter(tmp_path: Path, monkeypatch):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    monkeypatch.setattr(cli, "RUN", fake)
    def raise_unknown(cli_name: str):
        raise UnknownAdapter("unknown adapter 'nope'")
    monkeypatch.setattr(cli, "ADAPTER_FOR", raise_unknown)
    result = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "roster"], env=env_for(tmp_path))
    assert result.exit_code == 1
    assert "unknown adapter" in result.output


def test_roster_command_is_brief_unless_asked_for_the_full_descriptions(tmp_path: Path, monkeypatch):
    """Plan 55: the ROSTER post is `meld roster`'s output, so that output is one line per entry by default."""
    from meld.roster import WorkerType
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    monkeypatch.setattr(cli, "RUN", fake)
    wordy = WorkerType(name="wordy", description="First sentence here. " + "Then a great deal more. " * 20, source="skill", path=None)
    monkeypatch.setattr(cli, "ADAPTER_FOR", lambda cli_name: FakeAdapter(skills=[wordy]))
    brief = json.loads(CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "roster"], env=env_for(tmp_path)).output)
    assert next(d for d in brief if d["name"] == "wordy")["description"] == "First sentence here."
    full = json.loads(CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "roster", "--full"], env=env_for(tmp_path)).output)
    assert next(d for d in full if d["name"] == "wordy")["description"].startswith("First sentence here. Then a great deal more.")
