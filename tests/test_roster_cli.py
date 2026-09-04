import json
from pathlib import Path

from click.testing import CliRunner

import meld.cli as cli
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
