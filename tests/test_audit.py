import json
from datetime import datetime, timezone

import pytest

from meld.actions import Refused
from meld.audit import audit, reopen
from meld.dispatch import collect, dispatch, neutralise_closers
from meld.github import GitHubError
from meld.results import BODY_LIMIT, TRUNCATED, load_result, store_result
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
    fake.route("DELETE", R + "issues/13/labels/meld%3Aclaimed-by%3Acodex", {})
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
    assert any(c.method == "DELETE" and c.endpoint == R + "issues/13/labels/meld%3Aclaimed-by%3Acodex" for c in fake.calls)
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


AUDIT_FAIL_COMMENT = {"id": 140, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T12:30:00Z",
                      "body": "[claude:pm] AUDIT #20 FAIL\n\ncriterion 2 is not implemented\n\n## Findings\n- `ci.yml:1` no cache step, criterion 2 unmet\n\nverified-by: pytest -q -> 3 failed"}


def coord_posts(fake):
    return [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]


def stored_audit(tmp_path, verdict="fail", **result):
    payload = {"kind": "audit", "pr": 20, "head_sha": "abc123", "result": {"verdict": verdict, "summary": "criterion 2 is not implemented",
                                                                             "verified_by": "pytest -q -> 3 failed", "comments": [], **result}}
    store_result(tmp_path / "home", "claude", "audit", 20, payload, "2026-09-03T12:00:00Z")


def test_audit_retry_does_not_repost_a_verdict_already_on_the_thread(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    fake.route("GET", R + "issues/10/comments", fixture("comments_10.json") + [AUDIT_FAIL_COMMENT])   # an earlier attempt posted it, then failed
    stored_audit(tmp_path)
    assert audit(ctx, 20, now=NOW) == {"audited": 20, "verdict": "fail", "issue": 13, "comment_id": None, "already_posted": True}
    assert coord_posts(fake) == [] and not [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/comments"]
    assert load_result(tmp_path / "home", "claude", "audit", 20) is None                    # consumed all the same
    (submitted,) = (tmp_path / "home" / "results" / "claude" / "submitted").glob("audit-20-*.json")
    assert json.loads(submitted.read_text())["outcome"] == "already-posted"
    # a different verdict than the one on record is not "already posted"
    stored_audit(tmp_path, verdict="pass")
    assert audit(ctx, 20, now=NOW)["comment_id"] == 310 and coord_posts(fake)[-1].startswith("[claude:pm] AUDIT #20 PASS\n")


def test_audit_drops_non_numeric_lines_and_escapes_backticks_in_paths(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    stored_audit(tmp_path, comments=[{"path": "ci.yml", "line": "abc", "body": "dropped: no line"},
                                     {"path": "c`i.yml", "line": "2", "body": "kept"},
                                     {"path": "ci.yml", "line": 3, "body": "kept too"}])
    audit(ctx, 20, now=NOW)
    coord = coord_posts(fake)[-1]
    assert "dropped: no line" not in coord and "- `ci.yml:2` kept\n- `ci.yml:3` kept too" in coord


def test_audit_body_is_capped(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    stored_audit(tmp_path, summary="s" * (BODY_LIMIT + 1000))
    audit(ctx, 20, now=NOW)
    coord = coord_posts(fake)[-1]
    header, _, body = coord.partition("\n\n")
    body = body.rstrip("\n")   # compose() ends the comment with a newline
    assert header == "[claude:pm] AUDIT #20 FAIL" and body.endswith(TRUNCATED) and len(body) == BODY_LIMIT + len(TRUNCATED)


def test_reopen_opens_the_issue_last_so_a_failed_step_leaves_a_legal_retry(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    routes_for_labels(fake, 13)
    fake.route("DELETE", R + "issues/13/labels/meld%3Ain-review", [])
    fake.route("PATCH", R + "issues/13", {"number": 13, "state": "open"})
    fake.route("DELETE", R + "issues/13/labels/meld%3Aclaimed-by%3Acodex", {})
    fake.route("GET", R + "issues/10/comments", fixture("comments_10.json") + [AUDIT_FAIL_COMMENT])
    issues = fixture("issues.json")
    for i in issues:
        if i["number"] == 13:
            i["state"] = "closed"
    fake.route("GET", R + "issues", issues)
    fake.route("POST", R + "issues/13/comments", {"message": "boom"}, status=500)
    with pytest.raises(GitHubError):
        reopen(ctx, 13)
    assert not [c for c in fake.calls if c.method == "PATCH"]                         # the issue was never opened
    assert {"labels": ["meld:ready"]} in [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/labels"]
    fake.route("POST", R + "issues/13/comments", {"id": 313})
    assert reopen(ctx, 13) == {"reopened": 13, "by": "claude"}                       # still closed, so still legal
    writes = [(c.method, c.endpoint) for c in fake.calls if c.method != "GET"]
    patch = writes.index(("PATCH", R + "issues/13"))
    assert writes.count(("PATCH", R + "issues/13")) == 1
    assert all(i < patch for i, w in enumerate(writes) if w in (("POST", R + "issues/13/labels"), ("DELETE", R + "issues/13/labels/meld%3Aclaimed-by%3Acodex"),
                                                                 ("POST", R + "issues/13/comments")))
    assert any("no cache step, criterion 2 unmet" in c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/comments")
