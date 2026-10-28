import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.actions import Refused
from meld.review import merge
from meld.status import build_status
from tests.conftest import env_for
from tests.productfixtures import R, fixture, make_product


def _merge_ready(tmp_path):
    """A claude PM whose PR #20 (codex's head, approved by claude on abc123) is ready to merge, with the merge PUT routed to succeed."""
    fake, ctx, repo, origin = make_product(tmp_path)
    fake.route("PUT", R + "pulls/20/merge", {"sha": "m123", "merged": True})
    return fake, ctx


def test_merge_squashes_with_the_head_sha_guard(tmp_path):
    fake, ctx = _merge_ready(tmp_path)
    assert merge(ctx, 20) == {"merged": 20, "sha": "m123", "issue": 13, "approvals": ["claude"]}
    (body,) = [c.body for c in fake.calls if c.method == "PUT"]
    assert body == {"sha": "abc123", "merge_method": "squash", "commit_title": "CI pipeline",
                    "commit_message": "Closes #13\n\nverified-by: act -j ci (passed)"}


def test_merge_sends_the_pr_body_as_the_squash_message(tmp_path):
    # The squash-merge message becomes the PR body, not the worker's own commit history (spec 21 item 5).
    fake, ctx = _merge_ready(tmp_path)
    _, board = build_status(ctx)
    out = merge(ctx, 20)
    assert out["merged"] == 20
    put = [c for c in fake.calls if c.method == "PUT" and c.endpoint.endswith("pulls/20/merge")][0]
    assert put.body["commit_message"] == board.pulls[20].body and put.body["merge_method"] == "squash"


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


def test_merge_of_an_already_merged_pr_is_a_no_op_success(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)   # PR #19 merged long ago (claude authored it; no approvals on record)
    pulls = fixture("pulls.json")
    for p in pulls:
        if p["number"] == 19:
            p["merge_commit_sha"] = "m019"
    fake.route("GET", R + "pulls", pulls)
    assert merge(ctx, 19) == {"merged": 19, "sha": "m019", "issue": 9, "approvals": [], "already_merged": True}
    assert not [c for c in fake.calls if c.method in ("PUT", "POST")]     # no merge call, no PROTOCOL-VIOLATION


def test_cli_merge(tmp_path, monkeypatch):
    fake, ctx, repo, origin = make_product(tmp_path)
    fake.route("PUT", R + "pulls/20/merge", {"sha": "m123", "merged": True})
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(repo / ".meld.toml"), "--compact", "merge", "20"], env=env_for(tmp_path))
    assert result.exit_code == 0, result.output
    assert '"merged": 20' in result.output


def test_a_merge_refused_for_conflicts_sends_the_issue_back_to_build(tmp_path):
    """Plan 40: GitHub's 405 on a dirty branch is a request for changes nobody posted -- handled like CI red: a comment on
    the issue, in-review -> in-progress, and `merge` reports what to do next instead of raising. The scratch product's
    PMs retried the 405 for hours and posted NEEDS-HUMAN nine times."""
    fake, ctx, repo, origin = make_product(tmp_path)
    fake.route("PUT", R + "pulls/20/merge", {"message": "Pull Request is not mergeable"}, status=405)
    fake.route("GET", R + "pulls/20", {"number": 20, "mergeable": False, "mergeable_state": "dirty"})
    fake.route("POST", R + "issues/13/labels", [])
    fake.route("DELETE", R + "issues/13/labels/meld%3Ain-review", [])
    out = merge(ctx, 20)
    assert out["merged"] is False and out["conflict"] == 20 and out["issue"] == 13 and out["head"] == "abc123" and "dispatch #13" in out["next"]
    (comment,) = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/comments"]
    assert comment.startswith("Meld: PR #20 (head `abc123`) has merge conflicts with `main`") and "next build run" in comment
    assert [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/labels"] == [{"labels": ["meld:in-progress"]}]
    assert [c.endpoint for c in fake.calls if c.method == "DELETE"] == [R + "issues/13/labels/meld%3Ain-review"]


def test_a_405_that_is_not_a_conflict_still_raises(tmp_path):
    from meld.github import GitHubError
    fake, ctx, repo, origin = make_product(tmp_path)
    fake.route("PUT", R + "pulls/20/merge", {"message": "Base branch was modified"}, status=405)
    fake.route("GET", R + "pulls/20", {"number": 20, "mergeable": True, "mergeable_state": "clean"})
    with pytest.raises(GitHubError, match="405"):
        merge(ctx, 20)
    assert not [c for c in fake.calls if c.method in ("POST", "DELETE", "PATCH")]
