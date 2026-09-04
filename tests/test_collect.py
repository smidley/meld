import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from meld.actions import Refused
from meld.dispatch import collect, dispatch, tick
from meld.gitops import remote_branch_exists
from tests.fakeadapter import FakeAdapter
from tests.gitfixtures import sh
from tests.productfixtures import BLOCKED_SCRIPT, DONE_SCRIPT, NO_RESULT_SCRIPT, R, REVIEW_SCRIPT, SLEEP_SCRIPT, claim_issue_11, make_product, wait_for_exit

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
PAST = datetime(2020, 1, 1, tzinfo=timezone.utc)


def issue_posts(fake, number):
    return [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + f"issues/{number}/comments"]


def label_calls(fake, number):
    return [(c.method, c.endpoint.split(f"issues/{number}/labels", 1)[1], c.body) for c in fake.calls
            if c.method != "GET" and f"issues/{number}/labels" in c.endpoint]


def routes_for_labels(fake, number):
    fake.route("POST", R + f"issues/{number}/labels", [])
    for label in ("meld%3Ain-progress", "meld%3Aready", "meld%3Ablocked"):
        fake.route("DELETE", R + f"issues/{number}/labels/{label}", [])


def test_collect_done_pushes_opens_pr_and_requests_review(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    routes_for_labels(fake, 11)
    fake.route("POST", R + "pulls", {"number": 21, "html_url": "https://github.com/acme/widgets/pull/21"})
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=DONE_SCRIPT), now=NOW)
    wait_for_exit(out["pid"])
    result = collect(ctx, 11, now=NOW)
    assert result == {"collected": 11, "outcome": "done", "pr": 21, "commits": 1}
    assert remote_branch_exists(repo, "meld/11-add-health-endpoint")
    pull = [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "pulls"][0].body
    assert pull["head"] == "meld/11-add-health-endpoint" and pull["base"] == "main" and pull["title"] == "Add health endpoint"
    assert pull["body"].startswith("Closes #11\n\nadded health.py\n\nverified-by: python -c 'import health' -> ok")
    assert ("POST", "", {"labels": ["meld:in-review"]}) in label_calls(fake, 11) and ("DELETE", "/meld%3Ain-progress", None) in label_calls(fake, 11)
    finish = issue_posts(fake, 11)[-1]
    assert finish.startswith("Meld: `claude` worker finished: 1 commit(s)") and "PR #21" in finish and "verified-by:" in finish
    coord = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"][-1]
    assert coord.startswith("[claude:pm] REVIEW-REQUEST #21\n") and "Issue #11" in coord
    rec = json.loads((tmp_path / "home" / "runs" / "claude" / "11.json").read_text())
    assert rec["collected"] is True and rec["pr"] == 21
    with pytest.raises(Refused, match="already collected"):
        collect(ctx, 11, now=NOW)


def test_collect_without_result_blocks_with_log_tail(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    routes_for_labels(fake, 11)
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=NO_RESULT_SCRIPT), now=NOW)
    wait_for_exit(out["pid"])
    result = collect(ctx, 11, now=NOW)
    assert result["outcome"] == "failed" and result["reason"] == "exited without writing .meld-result.json"
    assert ("POST", "", {"labels": ["meld:blocked"]}) in label_calls(fake, 11)
    comment = issue_posts(fake, 11)[-1]
    assert "exited without writing" in comment and "worker crashed before writing a result" in comment and "```" in comment
    assert not remote_branch_exists(repo, "meld/11-add-health-endpoint")


def test_collect_timed_out_run(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    routes_for_labels(fake, 11)
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=SLEEP_SCRIPT), now=PAST)
    tick(ctx, now=NOW)
    wait_for_exit(out["pid"], timeout=5)
    result = collect(ctx, 11, now=NOW)
    assert result["outcome"] == "failed" and result["reason"].startswith("timed out at 2020-01-01T00:20:00Z")


def test_collect_blocked_needs_human(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    routes_for_labels(fake, 11)
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=BLOCKED_SCRIPT), now=NOW)
    wait_for_exit(out["pid"])
    result = collect(ctx, 11, now=NOW)
    assert result["outcome"] == "blocked"
    assert ("POST", "", {"labels": ["meld:needs-human"]}) in label_calls(fake, 11)
    coord = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"][-1]
    assert coord.startswith("[claude:pm] NEEDS-HUMAN #11\n") and "Stripe sandbox key" in coord


def test_collect_review_run_stores_the_verdict(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    sh(["git", "checkout", "-q", "-b", "meld/13-ci-pipeline"], repo)
    (repo / "ci.yml").write_text("ci\n")
    sh(["git", "add", "ci.yml"], repo)
    sh(["git", "commit", "-q", "-m", "ci"], repo)
    sh(["git", "push", "-q", "origin", "meld/13-ci-pipeline"], repo)
    sh(["git", "checkout", "-q", "main"], repo)
    out = dispatch(ctx, 20, kind="review", adapter=FakeAdapter(script=REVIEW_SCRIPT), now=NOW)
    wait_for_exit(out["pid"])
    result = collect(ctx, 20, now=NOW)
    stored = Path(result["stored"])
    assert stored == tmp_path / "home" / "results" / "claude" / "review-20.json" and result["verdict"] == "approve"
    data = json.loads(stored.read_text())
    assert data["kind"] == "review" and data["pr"] == 20 and data["result"]["verdict"] == "approve" and data["result"]["comments"][0]["path"] == "README.md"
    assert not [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "pulls"]  # nothing posted for verdicts yet


def test_collect_refusals(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    with pytest.raises(Refused, match="no run record"):
        collect(ctx, 11, now=NOW)
    claim_issue_11(fake)
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=SLEEP_SCRIPT), now=NOW)
    with pytest.raises(Refused, match="still running"):
        collect(ctx, 11, now=NOW)
    from meld.dispatch import kill_run
    from meld.board import RunRecord
    rec = json.loads((tmp_path / "home" / "runs" / "claude" / "11.json").read_text())
    kill_run(RunRecord(**{**rec, "alive": True}), grace_seconds=2)
