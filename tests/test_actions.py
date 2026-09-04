import json
from urllib.parse import quote

import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.actions import Refused, claim, release
from meld.context import load_context
from meld.github import GitHubError
from tests.conftest import FIXTURES, PROJECT_TOML, env_for, load_board1_routes
from tests.fakeadapter import no_skills
from tests.fakegh import FakeGh

R = "repos/acme/widgets/"


def label_ep(number: int, label: str) -> str:
    """The label endpoint as GitHub sees it: the label is one URL-quoted path segment."""
    return R + f"issues/{number}/labels/{quote(label, safe='')}"


def fixture_comments() -> list[dict]:
    return json.loads((FIXTURES / "board1" / "comments_10.json").read_text())


def make(tmp_path, group="claude", role="pm"):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    ctx = load_context(tmp_path / ".meld.toml", env=env_for(tmp_path, group=group, role=role), run=fake, adapter_for=no_skills)
    return fake, ctx


def posted_headers(fake):
    return [c.body["body"].splitlines()[0] for c in fake.calls if c.method == "POST" and c.endpoint.endswith("/comments")]


def test_claim_happy_path(tmp_path):
    fake, ctx = make(tmp_path)
    fake.route("POST", R + "issues/11/assignees", {"assignees": [{"login": "meld-claude-bot"}]})
    fake.route("GET", R + "issues/11", {"number": 11, "assignees": [{"login": "meld-claude-bot"}]})
    fake.route("POST", R + "issues/11/labels", [{"name": "meld:in-progress"}])
    fake.route("DELETE", label_ep(11, "meld:ready"), [])
    assert claim(ctx, 11) == {"claimed": 11, "assignee": "meld-claude-bot"}
    mutations = [(c.method, c.endpoint) for c in fake.calls if c.method != "GET"]
    assert mutations == [("POST", R + "issues/11/assignees"), ("POST", R + "issues/11/labels"),
                         ("DELETE", label_ep(11, "meld:ready"))]
    assert posted_headers(fake) == []
    # no stale own lease in the fixture, so a successful claim posts no EXPIRED comment
    assert not any(h.startswith("EXPIRED") for h in posted_headers(fake))


def test_claim_lost_race_releases_and_refuses(tmp_path):
    fake, ctx = make(tmp_path)
    fake.route("POST", R + "issues/11/assignees", {})
    fake.route("GET", R + "issues/11", {"number": 11, "assignees": [{"login": "meld-codex-bot"}, {"login": "meld-claude-bot"}]})
    fake.route("DELETE", R + "issues/11/assignees", {})
    fake.route("POST", R + "issues/10/comments", {"id": 1})
    with pytest.raises(Refused) as ei:
        claim(ctx, 11)
    assert "lost the claim race to meld-codex-bot" in ei.value.refusal.rule
    assert ("DELETE", R + "issues/11/assignees") in [(c.method, c.endpoint) for c in fake.calls]
    # first-assignment-wins working as designed is not an illegal call: no violation comment
    assert posted_headers(fake) == []
    assert ei.value.violation_posted is None


def test_claim_refusal_posts_protocol_violation_for_groups_only(tmp_path):
    fake, ctx = make(tmp_path, role="orchestrator")
    fake.route("POST", R + "issues/10/comments", {"id": 1})
    with pytest.raises(Refused) as ei:
        claim(ctx, 11)
    assert ei.value.refusal.rule == "only a PM or a human may claim"
    assert posted_headers(fake) == ["[claude] PROTOCOL-VIOLATION #11"]
    assert not any(c.endpoint.endswith("/assignees") for c in fake.calls)

    fake2, human = make(tmp_path, group=None)
    with pytest.raises(Refused):
        claim(human, 12)  # already in progress
    assert posted_headers(fake2) == []


def test_claim_unknown_issue(tmp_path):
    fake, ctx = make(tmp_path)
    fake.route("POST", R + "issues/10/comments", {"id": 1})
    with pytest.raises(Refused, match="not in the active milestone"):
        claim(ctx, 999)


def test_claim_compensates_when_reread_fails(tmp_path):
    fake, ctx = make(tmp_path)
    fake.route("POST", R + "issues/11/assignees", {})
    fake.route("DELETE", R + "issues/11/assignees", {})
    # no GET issues/11 route -> 404 -> GitHubError on the fresh re-read
    with pytest.raises(GitHubError):
        claim(ctx, 11)
    assert ("DELETE", R + "issues/11/assignees") in [(c.method, c.endpoint) for c in fake.calls]


def test_refusal_survives_a_failed_violation_post(tmp_path):
    fake, ctx = make(tmp_path, role="orchestrator")  # no comments route -> post fails with 404
    with pytest.raises(Refused) as ei:
        claim(ctx, 11)
    assert ei.value.refusal.rule == "only a PM or a human may claim"
    assert ei.value.post_error is not None


def test_release_by_owner_and_forced_by_human(tmp_path):
    fake, ctx = make(tmp_path)
    fake.route("DELETE", R + "issues/12/assignees", {})
    fake.route("POST", R + "issues/12/labels", [{"name": "meld:ready"}])
    fake.route("DELETE", label_ep(12, "meld:in-progress"), [])
    fake.route("DELETE", label_ep(12, "meld:blocked"), [])
    assert release(ctx, 12) == {"released": 12, "previous_assignees": ["meld-claude-bot"]}
    mutations = [(c.method, c.endpoint) for c in fake.calls if c.method != "GET"]
    assert mutations == [("DELETE", R + "issues/12/assignees"), ("POST", R + "issues/12/labels"),
                         ("DELETE", label_ep(12, "meld:in-progress"))]
    assert posted_headers(fake) == []

    fake2, human = make(tmp_path, group=None)
    fake2.route("DELETE", R + "issues/12/assignees", {})
    fake2.route("DELETE", label_ep(12, "meld:in-progress"), [])
    fake2.route("POST", R + "issues/12/labels", [{"name": "meld:ready"}])
    fake2.route("POST", R + "issues/10/comments", {"id": 2})
    release(human, 12, force=True)
    assert posted_headers(fake2) == ["[human] RELEASE #12"]

    fake3, pm = make(tmp_path)
    fake3.route("POST", R + "issues/10/comments", {"id": 3})
    with pytest.raises(Refused, match="human-only"):
        release(pm, 12, force=True)


def test_cli_claim_refusal_exit_code(tmp_path, monkeypatch):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    fake.route("POST", R + "issues/10/comments", {"id": 1})
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "claim", "12"], env=env_for(tmp_path))
    assert result.exit_code == 2
    data = json.loads(result.output)
    assert data["verb"] == "claim" and data["target"] == "#12" and "not ready" in data["rule"]
    assert data["violation_posted"] is True


def test_claim_compensates_both_halves_when_the_label_swap_fails(tmp_path):
    fake, ctx = make(tmp_path)
    fake.route("POST", R + "issues/11/assignees", {})
    fake.route("GET", R + "issues/11", {"number": 11, "assignees": [{"login": "meld-claude-bot"}]})
    fake.route("POST", R + "issues/11/labels", [{"name": "meld:in-progress"}])
    fake.route("DELETE", label_ep(11, "meld:ready"), {"message": "boom"}, status=500)
    fake.route("DELETE", R + "issues/11/assignees", {})
    fake.route("DELETE", label_ep(11, "meld:in-progress"), [])
    with pytest.raises(GitHubError, match="500"):
        claim(ctx, 11)
    mutations = [(c.method, c.endpoint) for c in fake.calls if c.method != "GET"]
    assert ("DELETE", R + "issues/11/assignees") in mutations
    assert ("DELETE", label_ep(11, "meld:in-progress")) in mutations


def test_cli_human_refusal_reports_no_violation_post(tmp_path, monkeypatch):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "claim", "12"],
                                env=env_for(tmp_path, group=None))
    assert result.exit_code == 2
    data = json.loads(result.output)
    assert data["verb"] == "claim" and "not ready" in data["rule"]
    assert data["violation_posted"] is None
    assert posted_headers(fake) == []


def test_pm_claim_clears_its_own_stale_lease(tmp_path):
    fake, ctx = make(tmp_path)   # claude PM
    stale = {"id": 105, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:00:00Z",
             "body": "[claude:pm] LEASE #11\n\nuntil: 2020-01-01T00:00:00Z\nrun: claude/#11\nkind: build"}
    fake.route("GET", R + "issues/10/comments", fixture_comments() + [stale])
    # Copy, verbatim, the assignee/label routes and the fresh-issue re-read route from the existing successful-claim test
    # in this file so that claim(ctx, 11) succeeds here exactly as it does there.
    fake.route("POST", R + "issues/11/assignees", {"assignees": [{"login": "meld-claude-bot"}]})
    fake.route("GET", R + "issues/11", {"number": 11, "assignees": [{"login": "meld-claude-bot"}]})
    fake.route("POST", R + "issues/11/labels", [{"name": "meld:in-progress"}])
    fake.route("DELETE", label_ep(11, "meld:ready"), [])
    fake.route("POST", R + "issues/10/comments", {"id": 900})
    claim(ctx, 11)
    posted = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]
    assert posted and posted[-1].startswith("[claude:pm] EXPIRED #11\n")
