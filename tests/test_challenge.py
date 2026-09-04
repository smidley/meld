import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.actions import Refused
from meld.challenge import challenge
from meld.posting import PostError
from tests.conftest import env_for
from tests.productfixtures import R, fixture, make_product

OPENED = fixture("comments_10.json") + [{"id": 150, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T11:00:00Z",
                                          "body": "[claude] CHALLENGE #13\n\nthe CI claim names no command"}]


def coord_posts(fake):
    return [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]


def test_open_challenge_posts_and_labels(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")   # claude doubts codex's in-review #13
    fake.route("POST", R + "issues/13/labels", [])
    out = challenge(ctx, "open", "#13", "the CI claim names no command; show the run")
    assert out == {"challenge": "open", "ref": "#13", "verb": "CHALLENGE", "comment_id": 310, "label": "added", "challenged_party": "codex"}
    assert coord_posts(fake)[-1].startswith("[claude] CHALLENGE #13\n") and "show the run" in coord_posts(fake)[-1]
    assert [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/labels"] == [{"labels": ["meld:challenged"]}]
    with pytest.raises(PostError, match="say what is doubted"):
        challenge(ctx, "open", "#11", "   ")
    fake_pm, pm, *_ = make_product(tmp_path / "pm")
    with pytest.raises(Refused, match="only an orchestrator or a human"):
        challenge(pm, "open", "#11", "doubt")


def test_challenge_closers_follow_the_rules(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    fake.route("GET", R + "issues/10/comments", OPENED)
    with pytest.raises(Refused, match="challenger cannot decide"):
        challenge(ctx, "uphold", "#13", "I say so")
    fake_c, codex, *_ = make_product(tmp_path / "c", group="codex", role="orchestrator")
    fake_c.route("GET", R + "issues/10/comments", OPENED)
    with pytest.raises(PostError, match="verified-by"):
        challenge(codex, "evidence", "#13", "trust me")
    out = challenge(codex, "evidence", "#13", "act -j ci passed locally\n\nverified-by: act -j ci -> passed")
    assert out["verb"] == "EVIDENCE" and out["label"] is None and coord_posts(fake_c)[-1].startswith("[codex] EVIDENCE #13\n")
    with pytest.raises(Refused, match="challenged party cannot decide"):
        challenge(codex, "dismiss", "#13", "wrong")
    fake_h, human, *_ = make_product(tmp_path / "h", group=None)
    fake_h.route("GET", R + "issues/10/comments", OPENED)
    fake_h.route("DELETE", R + "issues/13/labels/meld%3Achallenged", [])
    out = challenge(human, "dismiss", "#13", "the evidence holds")
    assert out["verb"] == "DISMISSED" and out["label"] == "removed" and coord_posts(fake_h)[-1].startswith("[human] DISMISSED #13\n")
    fake_r, claude2, *_ = make_product(tmp_path / "r", role="orchestrator")
    fake_r.route("GET", R + "issues/10/comments", OPENED)
    fake_r.route("DELETE", R + "issues/13/labels/meld%3Achallenged", [])
    assert challenge(claude2, "retract", "#13", "my mistake")["label"] == "removed"


def test_challenge_on_an_agreement_ref_labels_nothing(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    fake.route("GET", R + "issues/10/comments", fixture("comments_10.json") + [
        {"id": 151, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T11:00:00Z", "body": "[codex] PROPOSE plan-3\n\nsplit the login work"}])
    out = challenge(ctx, "open", "plan-3", "the split ignores the shared session store")
    assert out["label"] is None and out["challenged_party"] == "codex"
    assert not [c for c in fake.calls if c.method == "POST" and "labels" in c.endpoint]


def test_cli_challenge(tmp_path, monkeypatch):
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    fake.route("POST", R + "issues/13/labels", [])
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(repo / ".meld.toml"), "--compact", "challenge", "open", "#13", "-m", "show the run"],
                                env=env_for(tmp_path, role="orchestrator"))
    assert result.exit_code == 0, result.output
    assert '"verb": "CHALLENGE"' in result.output
    bad = CliRunner().invoke(cli.main, ["--config", str(repo / ".meld.toml"), "--compact", "challenge", "uphold", "#13", "-m", "x"],
                             env=env_for(tmp_path, role="orchestrator"))
    assert bad.exit_code == 2 and '"refused"' in bad.output
