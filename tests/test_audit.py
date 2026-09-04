from datetime import datetime, timezone

import pytest

from meld.actions import Refused
from meld.audit import audit, reopen
from meld.dispatch import collect, dispatch, neutralise_closers
from tests.fakeadapter import FakeAdapter
from tests.gitfixtures import sh
from tests.productfixtures import R, fixture, make_product, wait_for_exit
from tests.test_collect import routes_for_labels
from tests.test_dispatch import publish_pr_branch

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
AUDIT_FAIL_SCRIPT = """
import json, pathlib
pathlib.Path(".meld-result.json").write_text(json.dumps({"outcome": "done", "summary": "criterion 2 is not implemented", "verified_by": "pytest -q -> 3 failed",
    "verdict": "fail", "comments": [{"path": "ci.yml", "line": 1, "body": "no cache step, criterion 2 unmet"}]}))
"""


def merged_pr_20(fake, repo):
    publish_pr_branch(repo)
    sh(["git", "merge", "-q", "--no-ff", "-m", "CI pipeline (#20)", "meld/13-ci-pipeline"], repo)
    merge_sha = sh(["git", "rev-parse", "HEAD"], repo)
    sh(["git", "push", "-q", "origin", "main"], repo)
    pulls = fixture("pulls.json")
    for p in pulls:
        if p["number"] == 20:
            p.update({"state": "closed", "merged_at": "2026-09-03T11:00:00Z", "merge_commit_sha": merge_sha})
    fake.route("GET", R + "pulls", pulls)
    return merge_sha


def test_audit_posts_the_verdict_and_reopen_returns_the_issue_to_ready(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)   # claude PM audits codex's merged PR #20 (issue #13)
    merged_pr_20(fake, repo)
    routes_for_labels(fake, 13)
    fake.route("DELETE", R + "issues/13/labels/meld%3Ain-review", [])
    fake.route("PATCH", R + "issues/13", {"number": 13, "state": "open"})
    fake.route("DELETE", R + "issues/13/assignees", {})
    out = dispatch(ctx, 20, kind="audit", adapter=FakeAdapter(script=AUDIT_FAIL_SCRIPT), now=NOW)
    wait_for_exit(out["pid"])
    collect(ctx, 20, now=NOW)
    assert audit(ctx, 20, now=NOW) == {"audited": 20, "verdict": "fail", "issue": 13, "comment_id": 310}
    coord = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"][-1]
    assert coord.startswith("[claude:pm] AUDIT #20 FAIL\n") and "- `ci.yml:1` no cache step" in coord and "verified-by: pytest -q -> 3 failed" in coord
    assert any("meld reopen 13" in c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/comments")
    with pytest.raises(Refused, match="issue is not closed"):
        reopen(ctx, 13)
    fake.route("GET", R + "issues/10/comments", fixture("comments_10.json") + [
        {"id": 140, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T12:30:00Z", "body": coord}])
    issues = fixture("issues.json")
    for i in issues:
        if i["number"] == 13:
            i["state"] = "closed"
    fake.route("GET", R + "issues", issues)
    assert reopen(ctx, 13) == {"reopened": 13, "by": "claude"}
    assert [c.body for c in fake.calls if c.method == "PATCH" and c.endpoint == R + "issues/13"] == [{"state": "open"}]
    assert {"labels": ["meld:ready"]} in [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/labels"]
    assert any(c.method == "DELETE" and c.endpoint == R + "issues/13/assignees" for c in fake.calls)
    assert any("reopened by `claude` after a failed audit" in c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/comments")
    assert any("no cache step, criterion 2 unmet" in c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/comments")


def test_audit_pass_posts_no_reopen_hint_and_refusals(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    merged_pr_20(fake, repo)
    script = AUDIT_FAIL_SCRIPT.replace('"verdict": "fail"', '"verdict": "pass"')
    out = dispatch(ctx, 20, kind="audit", adapter=FakeAdapter(script=script), now=NOW)
    wait_for_exit(out["pid"])
    collect(ctx, 20, now=NOW)
    assert audit(ctx, 20, now=NOW)["verdict"] == "pass"
    coord = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"][-1]
    assert coord.startswith("[claude:pm] AUDIT #20 PASS\n")
    assert not [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/comments"]
    fake2, orch, *_ = make_product(tmp_path / "o", role="orchestrator")
    with pytest.raises(Refused, match="only the PM may post an audit"):
        audit(orch, 20, now=NOW)


def test_audit_neutralises_closing_keywords_in_finding_bodies(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    merged_pr_20(fake, repo)
    script = AUDIT_FAIL_SCRIPT.replace("no cache step, criterion 2 unmet", "no cache step, fixes #3")
    out = dispatch(ctx, 20, kind="audit", adapter=FakeAdapter(script=script), now=NOW)
    wait_for_exit(out["pid"])
    collect(ctx, 20, now=NOW)
    audit(ctx, 20, now=NOW)
    coord = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"][-1]
    assert neutralise_closers("fixes #3") in coord
    assert "fixes #3" not in coord          # the raw, un-neutralised form must not survive
    assert "`ci.yml:1`" in coord            # path/line are untouched by neutralisation
