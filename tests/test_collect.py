import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from meld.actions import Refused
from meld.dispatch import collect, dispatch, fenced, neutralise_closers, tick
from meld.gitops import remote_branch_exists, worktree_exists
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


def test_neutralise_closers_and_fenced():
    text = "closes #99, Fixes #7; resolved #8 and fixed  #12 but not #13 or close#14 or unfix #15"
    assert neutralise_closers(text) == "closes `#99`, Fixes `#7`; resolved `#8` and fixed  `#12` but not #13 or close#14 or unfix #15"
    assert neutralise_closers("") == ""
    assert fenced("plain") == "```\nplain\n```"
    assert fenced("```\nescaped\n```") == "````\n```\nescaped\n```\n````"
    assert fenced("`````five") == "``````\n`````five\n``````"


def test_collect_neutralises_closing_keywords_and_fences_the_log_tail(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    routes_for_labels(fake, 11)
    fake.route("POST", R + "pulls", {"number": 21})
    script = DONE_SCRIPT.replace('"summary": "added health.py"', '"summary": "added health.py. Closes #99 and fixes #7"') \
                        .replace("python -c 'import health' -> ok", "pytest; resolves #8")
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=script), now=NOW)
    wait_for_exit(out["pid"])
    collect(ctx, 11, now=NOW)
    body = [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "pulls"][0].body["body"]
    assert body == "Closes #11\n\nadded health.py. Closes `#99` and fixes `#7`\n\nverified-by: pytest; resolves `#8`"
    # a worker whose output contains ``` cannot break out of the log-tail fence
    fake2, ctx2, *_ = make_product(tmp_path / "b")
    claim_issue_11(fake2)
    routes_for_labels(fake2, 11)
    out2 = dispatch(ctx2, 11, adapter=FakeAdapter(script="print('```'); print('# not a heading'); raise SystemExit(1)"), now=NOW)
    wait_for_exit(out2["pid"])
    collect(ctx2, 11, now=NOW)
    comment = issue_posts(fake2, 11)[-1]
    assert comment.endswith("Last 50 log lines:\n\n````\n```\n# not a heading\n````")


def test_collect_is_superseded_when_another_group_now_holds_the_issue(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    issues = claim_issue_11(fake)
    routes_for_labels(fake, 11)
    fake.route("POST", R + "pulls", {"number": 21})
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=DONE_SCRIPT), now=NOW)
    wait_for_exit(out["pid"])
    calls_before = len(fake.calls)
    for i in issues:  # our lease expired and codex re-claimed #11 while the worker ran
        if i["number"] == 11:
            i["assignees"] = [{"login": "meld-codex-bot"}]
    fake.route("GET", R + "issues", issues)
    result = collect(ctx, 11, now=NOW)
    assert result == {"collected": 11, "outcome": "superseded",
                      "reason": "issue is in_progress and assigned to meld-codex-bot; claude no longer holds it"}
    assert [c for c in fake.calls[calls_before:] if c.method != "GET"] == []      # no push, PR, labels, or comments
    assert not remote_branch_exists(repo, "meld/11-add-health-endpoint")
    assert worktree_exists(repo, Path(out["worktree"]))                          # kept for inspection
    assert json.loads((tmp_path / "home" / "runs" / "claude" / "11.json").read_text())["collected"] is True
    with pytest.raises(Refused, match="already collected"):
        collect(ctx, 11, now=NOW)


def test_budget_counts_a_retry_on_the_same_issue(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    issues = claim_issue_11(fake)
    routes_for_labels(fake, 11)
    fake.route("POST", R + "pulls", {"number": 21})
    first = dispatch(ctx, 11, adapter=FakeAdapter(script=DONE_SCRIPT), now=NOW)
    wait_for_exit(first["pid"])
    assert collect(ctx, 11, now=NOW)["outcome"] == "done"
    assert ctx.store.load_spend(3) == {"claude": {"medium": 1}}
    for i in issues:  # the orchestrator re-tags the retry to the large tier
        if i["number"] == 11:
            i["labels"] = [l for l in i["labels"] if not l["name"].startswith("model:")] + [{"name": "model:claude-opus-5"}]
    fake.route("GET", R + "issues", issues)
    second = dispatch(ctx, 11, adapter=FakeAdapter(script=DONE_SCRIPT), now=NOW)
    wait_for_exit(second["pid"])
    assert second["worktree_mode"] == "reused"
    assert ctx.store.load_spend(3) == {"claude": {"medium": 1, "large": 1}}
    archive = tmp_path / "home" / "runs" / "claude" / "archive"
    assert len(list(archive.glob("11-*.json"))) == 1 and json.loads((tmp_path / "home" / "runs" / "claude" / "11.json").read_text())["tier"] == "large"
