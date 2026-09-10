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
from tests.productfixtures import fixture

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


def make_untagged(tmp_path, group="claude", role="orchestrator"):
    """Like make(), but issue 11 starts with only meld:ready -- no worker/model/effort/size labels."""
    fake, ctx = make(tmp_path, group=group, role=role)
    issues = fixture("issues.json")
    for i in issues:
        if i["number"] == 11:
            i["labels"] = [{"name": "meld:ready"}]
    fake.route("GET", R + "issues", issues)
    return fake, ctx


def fixture_issue(n):
    return next(i for i in fixture("issues.json") if i["number"] == n)


def test_tag_replaces_labels_and_validates(tmp_path):
    fake, ctx = make(tmp_path)
    out = tag(ctx, 11, model="claude-opus-5", effort="high", size="M")
    assert out == {"tagged": 11, "labels": {"worker": "backend", "model": "claude-opus-5", "effort": "high", "size": "M", "skills": []},
                   "scope": [], "may_delete": [], "held": []}
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


def test_tag_refuses_an_effort_it_cannot_validate_without_a_model(tmp_path):
    fake, ctx = make_untagged(tmp_path)
    with pytest.raises(Refused, match="without a model"):
        tag(ctx, 11, effort="high")
    out = tag(ctx, 11, worker="backend", model="claude-opus-5", effort="high", size="S")
    assert out == {"tagged": 11, "labels": {"worker": "backend", "model": "claude-opus-5", "effort": "high", "size": "S", "skills": []},
                   "scope": [], "may_delete": [], "held": []}
    assert label_calls(fake) == [("POST", "", {"labels": ["worker:backend", "model:claude-opus-5", "effort:high", "size:S"]})]
    with pytest.raises(Refused, match="not valid"):
        tag(ctx, 11, model="claude-opus-5", effort="turbo")


def test_tag_skill_adds_labels_from_the_roster(tmp_path):
    from meld.tagging import tag
    from tests.productfixtures import make_product
    from tests.fakeadapter import FakeAdapter
    from meld.roster import WorkerType
    fake, ctx, repo, origin = make_product(tmp_path, group="claude", role="orchestrator")
    ctx.adapter_for = lambda name: FakeAdapter(skills=[WorkerType("pdf", "PDF files", "skill", None)])
    ctx.refresh_roster()
    fake.route("POST", "repos/acme/widgets/issues/11/labels", [], status=200)
    out = tag(ctx, 11, skills=["pdf"])
    assert out["labels"]["skills"] == ["pdf"]
    posted = [c.body for c in fake.calls if c.method == "POST" and c.endpoint.endswith("issues/11/labels")][0]
    assert "skill:pdf" in posted["labels"]
    import pytest
    from meld.actions import Refused
    with pytest.raises(Refused, match="skill 'nope'"):
        tag(ctx, 11, skills=["nope"])


def test_tag_writes_scope_and_may_delete_sections_into_the_body(tmp_path):
    fake, ctx = make(tmp_path, role="orchestrator")
    fake.route("PATCH", R + "issues/11", {"number": 11})
    out = tag(ctx, 11, scope=["src/meld/*.py", "tests/test_x.py"], may_delete=["src/meld/legacy.py"])
    patch = [c for c in fake.calls if c.method == "PATCH" and c.endpoint == R + "issues/11"][0]
    body = patch.body["body"]
    assert "## Scope\n- src/meld/*.py\n- tests/test_x.py\n" in body and "## May delete\n- src/meld/legacy.py\n" in body
    assert body.startswith(fixture_issue(11)["body"].rstrip("\n"))                 # the existing body text is kept
    assert out["scope"] == ["src/meld/*.py", "tests/test_x.py"] and out["may_delete"] == ["src/meld/legacy.py"]
    fake.route("GET", R + "issues", [{**i, "body": body} if i["number"] == 11 else i for i in fixture("issues.json")])
    out2 = tag(ctx, 11, scope=["docs/**"])                                         # a second call replaces the section, keeps the other
    body2 = [c for c in fake.calls if c.method == "PATCH"][-1].body["body"]
    assert "## Scope\n- docs/**\n" in body2 and "src/meld/*.py" not in body2 and "## May delete\n- src/meld/legacy.py\n" in body2


def test_tag_held_labels_the_issue_needs_human_and_posts_the_ask(tmp_path):
    fake, ctx = make(tmp_path, role="orchestrator")
    fake.route("POST", R + "issues/11/labels", [])
    fake.route("POST", R + "issues/10/comments", {"id": 900})
    out = tag(ctx, 11, held=["delete", "public-release"])
    labels = [c.body["labels"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/11/labels"]
    assert labels == [["meld:held:delete", "meld:held:public-release", "meld:needs-human"]]
    (ask,) = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]
    assert ask.startswith("[claude] NEEDS-HUMAN #11") and "delete, public-release" in ask and "RESOLVED #11" in ask
    assert out["held"] == ["delete", "public-release"]
    with pytest.raises(Refused, match="held category"):
        tag(ctx, 11, held=["nuke"])


def test_tag_held_by_a_human_labels_without_an_ask_and_a_repeat_posts_nothing(tmp_path):
    fake, human = make(tmp_path, group=None)
    out = tag(human, 11, held=["delete"])
    assert out["held"] == ["delete"]
    labels = [c.body["labels"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/11/labels"]
    assert labels == [["meld:held:delete", "meld:needs-human"]]
    assert not [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]   # the human is the authority
    issues = fixture("issues.json")
    for i in issues:
        if i["number"] == 11:
            i["labels"] = i["labels"] + [{"name": "meld:held:delete"}]
    fake.route("GET", R + "issues", issues)
    fake2, ctx = make(tmp_path / "orch", role="orchestrator")
    fake2.route("GET", R + "issues", issues)
    out = tag(ctx, 11, held=["delete"], size="M")                              # already held: no label POST for it, no ask
    assert out["held"] == ["delete"]
    posts = [c for c in fake2.calls if c.method == "POST"]
    assert [c.body for c in posts if c.endpoint == R + "issues/11/labels"] == [{"labels": ["size:M"]}]
    assert not [c for c in posts if c.endpoint == R + "issues/10/comments"]
    out = tag(ctx, 11, held=["delete", "cleanup"])                              # one new category: labels and an ask naming both
    assert out["held"] == ["cleanup", "delete"]
    assert [c.body for c in fake2.calls if c.method == "POST" and c.endpoint == R + "issues/11/labels"][-1] == {"labels": ["meld:held:cleanup", "meld:needs-human"]}
    (ask,) = [c.body["body"] for c in fake2.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]
    assert "Held categories on #11: cleanup, delete." in ask


def test_tag_refuses_empty_declaration_items(tmp_path):
    fake, ctx = make(tmp_path, role="orchestrator")
    with pytest.raises(Refused, match="non-empty paths"):
        tag(ctx, 11, scope=["src/*.py", " "])
    with pytest.raises(Refused, match="non-empty paths"):
        tag(ctx, 11, may_delete=[""])
    assert not [c for c in fake.calls if c.method == "PATCH" or (c.method == "POST" and c.endpoint == R + "issues/11/labels")]
