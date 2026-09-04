import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.actions import Refused
from meld.review import merge
from tests.conftest import env_for
from tests.productfixtures import R, fixture, make_product


def test_merge_squashes_with_the_head_sha_guard(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)   # claude PM; PR #20's head is codex's and claude approved it on abc123
    fake.route("PUT", R + "pulls/20/merge", {"sha": "m123", "merged": True})
    assert merge(ctx, 20) == {"merged": 20, "sha": "m123", "issue": 13, "approvals": ["claude"]}
    (body,) = [c.body for c in fake.calls if c.method == "PUT"]
    assert body == {"sha": "abc123", "merge_method": "squash", "commit_title": "CI pipeline"}


def test_human_approval_counts_toward_the_quorum(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, group="codex")   # codex PM merges its own group's PR on a human approval
    fake.route("GET", R + "pulls/20/reviews", [{"id": 9, "user": {"login": "empowerScott"}, "state": "APPROVED", "commit_id": "abc123",
                                                "submitted_at": "2026-09-03T11:00:00Z"}])
    fake.route("PUT", R + "pulls/20/merge", {"sha": "m124", "merged": True})
    assert merge(ctx, 20)["approvals"] == ["human:empowerScott"]


def test_merge_refusals(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    fake.route("GET", R + "pulls/20/reviews", [])
    with pytest.raises(Refused, match="0/1 approvals from non-committer parties"):
        merge(ctx, 20)
    fake2, orch, *_ = make_product(tmp_path / "o", role="orchestrator")
    with pytest.raises(Refused, match="only a PM or a human may merge"):
        merge(orch, 20)
    fake3, challenged, *_ = make_product(tmp_path / "c")
    fake3.route("GET", R + "issues/10/comments", fixture("comments_10.json") + [
        {"id": 131, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:50:00Z", "body": "[claude] CHALLENGE #20\n\nthe CI claim is unverified"}])
    with pytest.raises(Refused, match="PR has an open CHALLENGE"):
        merge(challenged, 20)
    fake4, red, *_ = make_product(tmp_path / "r")
    fake4.route("GET", R + "commits/abc123/check-runs", {"total_count": 1, "check_runs": [{"name": "ci", "status": "completed", "conclusion": "failure"}]})
    with pytest.raises(Refused, match="required checks are red"):
        merge(red, 20)
    with pytest.raises(Refused, match="PR is not on a meld/ branch"):
        merge(ctx, 18)


def test_cli_merge(tmp_path, monkeypatch):
    fake, ctx, repo, origin = make_product(tmp_path)
    fake.route("PUT", R + "pulls/20/merge", {"sha": "m123", "merged": True})
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(repo / ".meld.toml"), "--compact", "merge", "20"], env=env_for(tmp_path))
    assert result.exit_code == 0, result.output
    assert '"merged": 20' in result.output
