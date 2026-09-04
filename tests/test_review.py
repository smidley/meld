from datetime import datetime, timezone
from pathlib import Path

import pytest

from meld.actions import Refused
from meld.dispatch import DispatchError, collect, dispatch
from meld.results import load_result, store_result
from meld.review import review
from tests.fakeadapter import FakeAdapter
from tests.gitfixtures import sh
from tests.productfixtures import NO_RESULT_SCRIPT, R, REVIEW_SCRIPT, fixture, make_product, wait_for_exit
from tests.test_collect import routes_for_labels
from tests.test_dispatch import publish_pr_branch

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
CHANGES_SCRIPT = """
import json, pathlib
pathlib.Path(".meld-result.json").write_text(json.dumps({"outcome": "done", "summary": "the handler swallows errors", "verified_by": "pytest -q -> 1 failed",
    "verdict": "request-changes", "comments": [{"path": "ci.yml", "line": 1, "body": "this step never fails the build"}]}))
"""
PUSH_FIX_SCRIPT = """
import json, pathlib, subprocess
pathlib.Path("fix.py").write_text("FIXED = True\\n")
subprocess.run(["git", "add", "fix.py"], check=True)
subprocess.run(["git", "commit", "-q", "-m", "fix: guard the null case"], check=True)
pathlib.Path(".meld-result.json").write_text(json.dumps({"outcome": "done", "summary": "added the missing guard", "verified_by": "pytest -q -> ok",
    "verdict": "push-fix", "comments": []}))
"""


def reviewed_pr(tmp_path, script):
    fake, ctx, repo, origin = make_product(tmp_path)   # claude PM reviews codex's PR #20 (issue #13)
    publish_pr_branch(repo)
    routes_for_labels(fake, 13)
    fake.route("DELETE", R + "issues/13/labels/meld%3Ain-review", [])
    fake.route("POST", R + "pulls/20/reviews", {"id": 501, "html_url": "https://github.com/acme/widgets/pull/20#pullrequestreview-501"})
    fake.route("POST", R + "statuses/abc123", {"id": 77})
    out = dispatch(ctx, 20, kind="review", adapter=FakeAdapter(script=script), now=NOW)
    wait_for_exit(out["pid"])
    collect(ctx, 20, now=NOW)
    return fake, ctx, repo, Path(out["worktree"])


def calls(fake, method, endpoint):
    return [c.body for c in fake.calls if c.method == method and c.endpoint == R + endpoint]


def test_review_approve_submits_a_github_review_and_a_status(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, REVIEW_SCRIPT)
    out = review(ctx, 20, now=NOW)
    assert out == {"reviewed": 20, "verdict": "approve", "event": "APPROVE", "review_id": 501, "issue": 13, "issue_status": None, "head": "abc123"}
    (body,) = calls(fake, "POST", "pulls/20/reviews")
    assert body["event"] == "APPROVE" and body["commit_id"] == "abc123" and body["body"].startswith("looks right\n\nverified-by: pytest -q -> ok")
    assert body["comments"] == [{"path": "README.md", "line": 1, "side": "RIGHT", "body": "nit: title"}]
    (status,) = calls(fake, "POST", "statuses/abc123")
    assert status == {"state": "success", "context": "meld/review/claude", "description": "claude review: approve",
                      "target_url": "https://github.com/acme/widgets/pull/20#pullrequestreview-501"}
    assert not calls(fake, "POST", "issues/13/labels")                               # approve moves no labels
    assert load_result(tmp_path / "home", "claude", "review", 20) is None
    assert list((tmp_path / "home" / "results" / "claude" / "submitted").glob("review-20-abc123-*.json"))
    with pytest.raises(DispatchError, match="no collected review result"):
        review(ctx, 20, now=NOW)


def test_review_request_changes_returns_the_issue_to_in_progress(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, CHANGES_SCRIPT)
    out = review(ctx, 20, now=NOW)
    assert out["event"] == "REQUEST_CHANGES" and out["issue_status"] == "in_progress"
    assert calls(fake, "POST", "issues/13/labels") == [{"labels": ["meld:in-progress"]}]
    assert any(c.method == "DELETE" and c.endpoint.endswith("issues/13/labels/meld%3Ain-review") for c in fake.calls)
    assert calls(fake, "POST", "statuses/abc123")[0]["state"] == "failure"
    (body,) = calls(fake, "POST", "pulls/20/reviews")
    assert body["comments"][0]["path"] == "ci.yml"


def test_review_request_changes_at_the_pingpong_cap_blocks_the_issue(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, CHANGES_SCRIPT)
    older = [{"id": 1, "user": {"login": "meld-claude-bot"}, "state": "CHANGES_REQUESTED", "commit_id": "old000", "submitted_at": "2026-09-03T09:00:00Z"},
             {"id": 2, "user": {"login": "meld-claude-bot"}, "state": "CHANGES_REQUESTED", "commit_id": "old001", "submitted_at": "2026-09-03T09:30:00Z"}]
    fake.route("GET", R + "pulls/20/reviews", older)     # two rounds already happened; the cap is 3
    out = review(ctx, 20, now=NOW)
    assert out["issue_status"] == "blocked" and calls(fake, "POST", "issues/13/labels") == [{"labels": ["meld:blocked"]}]
    assert any("ping-pong cap (3) reached" in c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/comments")


def test_review_push_fix_pushes_and_asks_the_author_group_to_re_review(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, PUSH_FIX_SCRIPT)
    new_head = sh(["git", "rev-parse", "HEAD"], wt)
    fake.route("POST", R + f"statuses/{new_head}", {"id": 78})
    out = review(ctx, 20, now=NOW)
    assert out["event"] == "COMMENT" and out["head"] == new_head
    assert sh(["git", "rev-parse", "origin/meld/13-ci-pipeline"], repo) == new_head     # pushed from the detached review worktree
    (body,) = calls(fake, "POST", "pulls/20/reviews")
    assert body["event"] == "COMMENT" and body["body"].startswith("Pushed 1 fix commit(s)") and "may not approve this head" in body["body"]
    assert calls(fake, "POST", f"statuses/{new_head}")[0]["state"] == "pending"
    coord = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]
    assert coord[-1].startswith("[claude:pm] REVIEW-REQUEST #20\n") and new_head[:7] in coord[-1]


def test_review_of_a_moved_head_is_stale(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, REVIEW_SCRIPT)
    pulls = fixture("pulls.json")
    for p in pulls:
        if p["number"] == 20:
            p["head"]["sha"] = "def456"
    fake.route("GET", R + "pulls", pulls)
    fake.route("GET", R + "commits/def456/check-runs", fixture("checkruns_abc123.json"))
    out = review(ctx, 20, now=NOW)
    assert out["event"] is None and out["reason"] == "stale head"
    assert not calls(fake, "POST", "pulls/20/reviews")
    assert any("is stale" in c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/20/comments")
    assert load_result(tmp_path / "home", "claude", "review", 20) is None


def test_review_without_a_verdict_asks_for_a_redispatch(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, NO_RESULT_SCRIPT)
    out = review(ctx, 20, now=NOW)
    assert out["event"] is None and "exited without writing" in out["reason"]
    assert any("no usable verdict" in c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/20/comments")
    assert load_result(tmp_path / "home", "claude", "review", 20) is None


def test_review_refuses_to_approve_own_head(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, group="codex")   # codex made PR #20's last commit
    store_result(tmp_path / "home", "codex", "review", 20,
                 {"head_sha": "abc123", "result": {"verdict": "approve", "summary": "x", "verified_by": "y", "comments": []}}, "2026-09-03T12:00:00Z")
    with pytest.raises(Refused, match="cannot approve its own head"):
        review(ctx, 20, now=NOW)
