import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.github import GitHub
from meld.models import ModelRegistry
from meld.setup import LABELS, SetupError, ensure_coordination_issue, ensure_labels, ensure_milestone, installed_adapters, setup, starter_toml
from tests.fakegh import FakeGh

R = "repos/acme/widgets/"


def gh_for(fake):
    return GitHub("acme/widgets", token=None, run=fake)


def route_repo(fake, private=True, default_branch="main"):
    fake.route("GET", "repos/acme/widgets", {"private": private, "default_branch": default_branch, "full_name": "acme/widgets"})
    fake.route("GET", "user", {"login": "empowerScott"})


def test_ensure_labels_creates_only_the_missing_ones():
    fake = FakeGh()
    fake.route("GET", R + "labels", [{"name": "meld:ready", "color": "0e8a16"}])
    fake.route("POST", R + "labels", {"name": "x"}, status=201)
    out = ensure_labels(gh_for(fake))
    posted = [c.body["name"] for c in fake.calls if c.method == "POST" and c.endpoint.endswith("labels")]
    assert "meld:ready" not in posted and set(posted) == set(LABELS) - {"meld:ready"}
    assert out == {"created": sorted(set(LABELS) - {"meld:ready"}), "existing": ["meld:ready"]}


def test_ensure_milestone_and_coordination_issue_are_idempotent():
    fake = FakeGh()
    fake.route("GET", R + "milestones", [{"number": 3, "title": "M1: Skeleton", "state": "open"}])
    fake.route("GET", R + "issues", [{"number": 10, "title": "Coordination: M1", "labels": [{"name": "meld:coordination"}], "milestone": {"number": 3}}])
    gh = gh_for(fake)
    assert ensure_milestone(gh, "M1: Skeleton") == (3, "existing")
    assert ensure_coordination_issue(gh, 3, "M1: Skeleton", "Build the skeleton") == (10, "existing")
    assert not [c for c in fake.calls if c.method == "POST"]
    fake2 = FakeGh()
    fake2.route("GET", R + "milestones", [])
    fake2.route("POST", R + "milestones", {"number": 4, "title": "M1: Skeleton"}, status=201)
    fake2.route("GET", R + "issues", [])
    fake2.route("POST", R + "issues", {"number": 12}, status=201)
    gh2 = gh_for(fake2)
    assert ensure_milestone(gh2, "M1: Skeleton") == (4, "created")
    number, how = ensure_coordination_issue(gh2, 4, "M1: Skeleton", "Build the skeleton")
    assert (number, how) == (12, "created")
    body = [c.body for c in fake2.calls if c.method == "POST" and c.endpoint.endswith("issues")][0]
    assert body["labels"] == ["meld:coordination"] and body["milestone"] == 4 and "Build the skeleton" in body["body"]


def test_starter_toml_names_installed_adapters_with_registry_models():
    registry = ModelRegistry.load()
    text = starter_toml("acme/widgets", "main", "empowerScott", ["claude", "codex"], registry)
    data = tomllib.loads(text)
    assert data["repo"] == {"name": "acme/widgets", "default_branch": "main", "private": True}
    assert data["humans"]["accounts"] == ["empowerScott"]
    for name in ("claude", "codex"):
        g = data["groups"][name]
        assert g["bot"] == f"meld-{name}-bot" and g["token_env"] == f"MELD_TOKEN_{name.upper()}"
        for role in ("orchestrator", "pm"):
            spec = registry.get(name, g[role]["model"])
            assert spec is not None and g[role]["effort"] in spec.efforts
        assert registry.tier_of(name, g["orchestrator"]["model"]) == "large"
    assert data["notify"]["slack_dm_webhook_env"] == "MELD_SLACK_DM_WEBHOOK"


def test_installed_adapters_uses_which():
    assert installed_adapters(which=lambda b: "/usr/bin/" + b if b == "claude" else None) == ["claude"]


def test_setup_refuses_a_public_repo_without_allow_public(tmp_path):
    fake = FakeGh()
    route_repo(fake, private=False)
    with pytest.raises(SetupError, match="public"):
        setup(gh_for(fake), repo="acme/widgets", goal="g", milestone_title="M1", human="empowerScott", target_dir=tmp_path,
              registry=ModelRegistry.load(), which=lambda b: "/bin/" + b)
    assert not (tmp_path / ".meld.toml").exists()


def test_setup_writes_the_starter_and_never_overwrites_without_force(tmp_path):
    fake = FakeGh()
    route_repo(fake)
    fake.route("GET", R + "labels", [])
    fake.route("POST", R + "labels", {"name": "x"}, status=201)
    fake.route("GET", R + "milestones", [])
    fake.route("POST", R + "milestones", {"number": 1, "title": "M1"}, status=201)
    fake.route("GET", R + "issues", [])
    fake.route("POST", R + "issues", {"number": 2}, status=201)
    out = setup(gh_for(fake), repo="acme/widgets", goal="Ship a widget catalogue", milestone_title="M1", human="empowerScott",
                target_dir=tmp_path, registry=ModelRegistry.load(), which=lambda b: "/bin/" + b)
    assert out["milestone"] == 1 and out["coordination_issue"] == 2 and out["config"] == str(tmp_path / ".meld.toml")
    assert out["labels"]["created"] and out["groups"] == ["claude", "codex"]
    text = (tmp_path / ".meld.toml").read_text()
    assert 'name = "acme/widgets"' in text
    with pytest.raises(SetupError, match="exists"):
        setup(gh_for(fake), repo="acme/widgets", goal="g", milestone_title="M1", human="empowerScott", target_dir=tmp_path,
              registry=ModelRegistry.load(), which=lambda b: "/bin/" + b)
    out2 = setup(gh_for(fake), repo="acme/widgets", goal="g", milestone_title="M1", human="empowerScott", target_dir=tmp_path,
                 registry=ModelRegistry.load(), which=lambda b: "/bin/" + b, force=True)
    assert out2["config_written"] is True


def test_setup_with_no_installed_cli_writes_commented_groups(tmp_path):
    fake = FakeGh()
    route_repo(fake)
    fake.route("GET", R + "labels", [])
    fake.route("POST", R + "labels", {"name": "x"}, status=201)
    fake.route("GET", R + "milestones", [{"number": 1, "title": "M1", "state": "open"}])
    fake.route("GET", R + "issues", [{"number": 2, "title": "c", "labels": [{"name": "meld:coordination"}], "milestone": {"number": 1}}])
    out = setup(gh_for(fake), repo="acme/widgets", goal="g", milestone_title="M1", human="empowerScott", target_dir=tmp_path,
                registry=ModelRegistry.load(), which=lambda b: None)
    assert out["groups"] == [] and "no CLI found" in " ".join(out["warnings"])
    text = (tmp_path / ".meld.toml").read_text()
    assert "# [groups.claude]" in text and tomllib.loads(text)["repo"]["name"] == "acme/widgets"


def test_cli_setup_command_writes_config(tmp_path, monkeypatch):
    fake = FakeGh()
    route_repo(fake)
    fake.route("GET", R + "labels", [])
    fake.route("POST", R + "labels", {"name": "x"}, status=201)
    fake.route("GET", R + "milestones", [])
    fake.route("POST", R + "milestones", {"number": 1, "title": "M1"}, status=201)
    fake.route("GET", R + "issues", [])
    fake.route("POST", R + "issues", {"number": 2}, status=201)
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["setup", "acme/widgets", "--goal", "g", "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".meld.toml").exists()
