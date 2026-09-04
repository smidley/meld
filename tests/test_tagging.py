import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.actions import Refused
from meld.context import load_context
from meld.tagging import tag
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.fakeadapter import no_skills
from tests.fakegh import FakeGh

R = "repos/acme/widgets/"


def make(tmp_path, group="claude", role="orchestrator"):
    tmp_path.mkdir(parents=True, exist_ok=True)  # tests pass sibling dirs (tmp_path / "pm") that don't exist yet
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    fake.route("POST", R + "issues/11/labels", [])
    for label in ("model%3Aclaude-sonnet-5", "effort%3Amedium", "size%3AS", "worker%3Abackend"):
        fake.route("DELETE", R + f"issues/11/labels/{label}", [])
    fake.route("POST", R + "issues/10/comments", {"id": 1})
    return fake, load_context(tmp_path / ".meld.toml", env=env_for(tmp_path, group=group, role=role), run=fake, adapter_for=no_skills)


def label_calls(fake):
    return [(c.method, c.endpoint.split("issues/11/labels", 1)[1], c.body) for c in fake.calls if c.method != "GET" and "issues/11/labels" in c.endpoint]


def test_tag_replaces_labels_and_validates(tmp_path):
    fake, ctx = make(tmp_path)
    out = tag(ctx, 11, model="claude-opus-5", effort="high", size="M")
    assert out == {"tagged": 11, "labels": {"worker": "backend", "model": "claude-opus-5", "effort": "high", "size": "M"}}
    calls = label_calls(fake)
    assert ("POST", "", {"labels": ["model:claude-opus-5", "effort:high", "size:M"]}) in calls
    assert ("DELETE", "/model%3Aclaude-sonnet-5", None) in calls and ("DELETE", "/effort%3Amedium", None) in calls and ("DELETE", "/size%3AS", None) in calls
    assert not any(e.startswith("/worker") for _, e, _ in calls)


def test_tag_refusals(tmp_path):
    fake, ctx = make(tmp_path)
    with pytest.raises(Refused, match="at least one"):
        tag(ctx, 11)
    with pytest.raises(Refused, match="not valid"):
        tag(ctx, 11, effort="turbo")
    with pytest.raises(Refused, match="unknown for adapter"):
        tag(ctx, 11, model="gpt-5.6-sol")
    with pytest.raises(Refused, match="size"):
        tag(ctx, 11, size="XL")
    with pytest.raises(Refused, match="roster"):
        tag(ctx, 11, worker="marketing")
    with pytest.raises(Refused, match="not in the active milestone"):
        tag(ctx, 999, size="S")
    _, pm = make(tmp_path / "pm", role="pm")
    with pytest.raises(Refused, match="orchestrator or a human"):
        tag(pm, 11, size="S")


def test_human_may_tag_any_model(tmp_path):
    fake, human = make(tmp_path, group=None)
    out = tag(human, 11, model="gpt-5.6-sol", effort="xhigh")
    assert out["labels"]["model"] == "gpt-5.6-sol"


def test_cli_tag(tmp_path, monkeypatch):
    fake, _ = make(tmp_path)
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "tag", "11", "--size", "M"],
                                env=env_for(tmp_path, role="orchestrator"))
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["labels"]["size"] == "M"
