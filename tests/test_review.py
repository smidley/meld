import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from meld.actions import Refused
from meld.appauth import AppAuth
from meld.context import load_context
from meld.dispatch import DispatchError, collect, dispatch
from meld.github import GitHubError
from meld.results import BODY_LIMIT, TRUNCATED, load_result, store_result, update_result
from meld.review import merge, review, submit_review
from tests.conftest import env_for
from tests.fakeadapter import FakeAdapter, no_skills
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
NO_COMMIT_PUSH_FIX_SCRIPT = """
import json, pathlib
pathlib.Path(".meld-result.json").write_text(json.dumps({"outcome": "done", "summary": "meant to add the guard", "verified_by": "n/a",
    "verdict": "push-fix", "comments": []}))
"""


def reviewed_pr(tmp_path, script):
    fake, ctx, repo, origin = make_product(tmp_path)   # claude PM reviews codex's PR #20 (issue #13)
    publish_pr_branch(repo)
    routes_for_labels(fake, 13)
    fake.route("DELETE", R + "issues/13/labels/meld%3Ain-review", [])
    fake.route("GET", R + "pulls/20/reviews", [])   # no prior review on this head (board1's fixture approval belongs to other scenarios)
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
    assert out == {"reviewed": 20, "verdict": "approve", "event": "APPROVE", "review_id": 501, "issue": 13, "issue_status": None,
                   "head": "abc123", "status_error": None}
    (body,) = calls(fake, "POST", "pulls/20/reviews")
    assert body["event"] == "APPROVE" and body["commit_id"] == "abc123" and body["body"].startswith("looks right\n\nverified-by: pytest -q -> ok")
    assert body["comments"] == [{"path": "README.md", "line": 1, "side": "RIGHT", "body": "nit: title"}]
    (status,) = calls(fake, "POST", "statuses/abc123")
    assert status == {"state": "success", "context": "meld/review/claude", "description": "claude review: approve",
                      "target_url": "https://github.com/acme/widgets/pull/20#pullrequestreview-501"}
    assert not calls(fake, "POST", "issues/13/labels")                               # approve moves no labels
    assert not calls(fake, "POST", "check-runs")                                     # a PAT group never gets a check run
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


def test_review_request_changes_retry_does_not_double_count_ping_pong(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, CHANGES_SCRIPT)
    # A prior attempt already posted our CHANGES_REQUESTED on abc123 (recorded in the verdict file the moment it landed)
    # but didn't reach mark_submitted (e.g. a later step failed); another account's older round is also on record. Cap is 3.
    update_result(tmp_path / "home", "claude", "review", 20, {"review_id": 777, "review_url": "u777", "review_event": "REQUEST_CHANGES"})
    fake.route("GET", R + "pulls/20/reviews",
               [{"user": {"login": "meld-codex-bot"}, "state": "CHANGES_REQUESTED", "commit_id": "old000", "submitted_at": "2026-09-03T09:00:00Z"},
                {"user": {"login": "meld-claude-bot"}, "state": "CHANGES_REQUESTED", "commit_id": "abc123", "submitted_at": "2026-09-03T11:30:00Z"}])
    out = review(ctx, 20, now=NOW)
    assert out["issue_status"] == "in_progress" and out["review_id"] == 777   # not blocked: our own already-posted review must not be double-counted
    assert not calls(fake, "POST", "pulls/20/reviews")    # no duplicate review filed on the retry
    assert calls(fake, "POST", "issues/13/labels") == [{"labels": ["meld:in-progress"]}]


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
    fake.route("GET", R + "commits/def456/status", fixture("status_abc123.json"))
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
    fake, ctx, repo, origin = make_product(tmp_path, group="codex")   # codex authored PR #20 and made its last commit
    store_result(tmp_path / "home", "codex", "review", 20,
                 {"head_sha": "abc123", "result": {"verdict": "approve", "summary": "x", "verified_by": "y", "comments": []}}, "2026-09-03T12:00:00Z")
    with pytest.raises(Refused, match="codex made the last commit on this PR and cannot review its own head"):
        review(ctx, 20, now=NOW)
    assert load_result(tmp_path / "home", "codex", "review", 20) is not None   # refused, not consumed
    # a non-author group that holds the head is refused by the ordinary committer rule
    fake_c, claude, *_ = make_product(tmp_path / "c")
    fake_c.route("GET", R + "pulls/20/commits", [{"sha": "abc123", "author": {"login": "meld-claude-bot"}, "committer": {"login": "meld-claude-bot"}}])
    store_result(tmp_path / "c" / "home", "claude", "review", 20,
                 {"head_sha": "abc123", "result": {"verdict": "approve", "summary": "x", "verified_by": "y", "comments": []}}, "2026-09-03T12:00:00Z")
    with pytest.raises(Refused, match="claude made the last commit on this PR and cannot approve its own head"):
        review(claude, 20, now=NOW)


def test_review_retry_after_a_failure_downstream_of_the_post_files_no_second_review(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, CHANGES_SCRIPT)
    fake.route("POST", R + "issues/13/labels", {"message": "boom"}, status=500)   # the label swap right after the review post fails
    with pytest.raises(GitHubError):
        review(ctx, 20, now=NOW)
    assert len(calls(fake, "POST", "pulls/20/reviews")) == 1
    stored = load_result(tmp_path / "home", "claude", "review", 20)
    assert stored["review_id"] == 501 and stored["review_event"] == "REQUEST_CHANGES"   # recorded the moment the review landed
    fake.route("POST", R + "issues/13/labels", [])
    fake.route("GET", R + "pulls/20/reviews", [{"id": 501, "user": {"login": "meld-claude-bot"}, "state": "CHANGES_REQUESTED", "commit_id": "abc123",
                                                "submitted_at": "2026-09-03T12:00:00Z"}])   # as GitHub shows it now
    out = review(ctx, 20, now=NOW)
    assert out["event"] == "REQUEST_CHANGES" and out["review_id"] == 501 and out["issue_status"] == "in_progress"
    assert len(calls(fake, "POST", "pulls/20/reviews")) == 1                    # no duplicate review filed on the retry
    assert len(calls(fake, "POST", "statuses/abc123")) == 2                     # the status is re-posted (harmless: same context, same state)
    assert calls(fake, "POST", "issues/13/labels") == [{"labels": ["meld:in-progress"]}] * 2   # the failed attempt and the retry
    (submitted,) = (tmp_path / "home" / "results" / "claude" / "submitted").glob("review-20-abc123-*.json")
    assert json.loads(submitted.read_text())["outcome"] == "already-submitted"


def test_a_second_verdict_from_another_worker_posts_its_own_review(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, REVIEW_SCRIPT)
    assert review(ctx, 20, now=NOW)["review_id"] == 501
    # GitHub now shows our APPROVED on abc123; the sequential `security` review is a new verdict on the same head
    fake.route("GET", R + "pulls/20/reviews", fixture("reviews_20.json"))
    fake.route("POST", R + "pulls/20/reviews", {"id": 502, "html_url": "https://github.com/acme/widgets/pull/20#pullrequestreview-502"})
    out = dispatch(ctx, 20, kind="review", worker="security", adapter=FakeAdapter(script=REVIEW_SCRIPT), now=NOW)
    wait_for_exit(out["pid"])
    collect(ctx, 20, now=NOW)
    assert load_result(tmp_path / "home", "claude", "review", 20)["worker"] == "security"
    out = review(ctx, 20, now=NOW)
    assert out["review_id"] == 502 and len(calls(fake, "POST", "pulls/20/reviews")) == 2
    submitted = (tmp_path / "home" / "results" / "claude" / "submitted").glob("review-20-abc123-*.json")
    assert sorted(json.loads(f.read_text())["worker"] for f in submitted) == ["reviewer", "security"]


def test_review_approve_survives_a_failing_status_post(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, REVIEW_SCRIPT)
    fake.route("POST", R + "statuses/abc123", {"message": "Internal Server Error"}, status=500)
    out = review(ctx, 20, now=NOW)
    assert out["status_error"] is not None
    assert len(calls(fake, "POST", "pulls/20/reviews")) == 1      # the review still went out despite the status failure
    submitted = list((tmp_path / "home" / "results" / "claude" / "submitted").glob("review-20-abc123-*.json"))
    assert json.loads(submitted[0].read_text())["status_error"] is not None
    with pytest.raises(DispatchError, match="no collected review result"):    # the verdict was consumed, not stuck
        review(ctx, 20, now=NOW)


def test_app_groups_publish_a_neutral_check_run_alongside_the_status(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, REVIEW_SCRIPT)
    ctx.app_auth = AppAuth(1, 1, "unused", tmp_path / "t.json",
                           post=lambda u, h, b: (201, {"token": "ghs", "expires_at": "2999-01-01T00:00:00Z", "permissions": {}, "repositories": []}))
    fake.route("POST", R + "check-runs", {"id": 9}, status=201)
    out = review(ctx, 20, now=NOW)
    (run,) = calls(fake, "POST", "check-runs")
    assert run["name"] == "meld/review/claude" and run["conclusion"] == "neutral" and run["head_sha"] == out["head"]
    assert "check_run_error" not in out


def test_review_approve_survives_a_failing_check_run_post(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, REVIEW_SCRIPT)
    ctx.app_auth = AppAuth(1, 1, "unused", tmp_path / "t.json",
                           post=lambda u, h, b: (201, {"token": "ghs", "expires_at": "2999-01-01T00:00:00Z", "permissions": {}, "repositories": []}))
    fake.route("POST", R + "check-runs", {"message": "Internal Server Error"}, status=500)
    out = review(ctx, 20, now=NOW)
    assert out["status_error"] is None and out["check_run_error"] is not None    # the review and status still land
    assert len(calls(fake, "POST", "pulls/20/reviews")) == 1
    assert len(calls(fake, "POST", "statuses/abc123")) == 1


def test_review_push_fix_resumes_after_a_failed_review_post(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, PUSH_FIX_SCRIPT)
    new_head = sh(["git", "rev-parse", "HEAD"], wt)
    fake.route("POST", R + "pulls/20/reviews", {"message": "boom"}, status=500)
    with pytest.raises(GitHubError):
        review(ctx, 20, now=NOW)
    assert sh(["git", "rev-parse", "origin/meld/13-ci-pipeline"], repo) == new_head    # the push landed despite the later failure
    stored = load_result(tmp_path / "home", "claude", "review", 20)
    assert stored is not None and stored["pushed"] == new_head                        # so a retry can see it happened

    fake.route("POST", R + "pulls/20/reviews", {"id": 502, "html_url": "https://github.com/acme/widgets/pull/20#pullrequestreview-502"})
    pulls = fixture("pulls.json")
    for p in pulls:
        if p["number"] == 20:
            p["head"]["sha"] = new_head
    fake.route("GET", R + "pulls", pulls)
    fake.route("GET", R + f"commits/{new_head}/check-runs", fixture("checkruns_abc123.json"))
    fake.route("GET", R + f"commits/{new_head}/status", fixture("status_abc123.json"))
    fake.route("GET", R + "pulls/20/reviews", [])
    # As real GitHub would show after our push: the PR's last commit is now ours, not codex's. Without the
    # resuming_push carve-out this would make pr_refusal's own-head check wrongly refuse the resume.
    fake.route("GET", R + "pulls/20/commits",
               [{"sha": "abc122", "author": {"login": "meld-codex-bot"}, "committer": {"login": "meld-codex-bot"}},
                {"sha": "abc123", "author": {"login": "meld-codex-bot"}, "committer": {"login": "meld-codex-bot"}},
                {"sha": new_head, "author": {"login": "meld-claude-bot"}, "committer": {"login": "meld-claude-bot"}}])
    fake.route("POST", R + f"statuses/{new_head}", {"id": 79})

    out = review(ctx, 20, now=NOW)
    assert out["event"] == "COMMENT" and out["head"] == new_head              # the COMMENT review is posted, once
    assert sh(["git", "rev-parse", "origin/meld/13-ci-pipeline"], repo) == new_head    # exactly one push across both attempts
    coord = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]
    assert coord[-1].startswith("[claude:pm] REVIEW-REQUEST #20\n")           # the REVIEW-REQUEST is posted


def test_review_push_fix_without_commits_is_a_no_action_outcome(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, NO_COMMIT_PUSH_FIX_SCRIPT)
    before = sh(["git", "rev-parse", "origin/meld/13-ci-pipeline"], repo)
    out = review(ctx, 20, now=NOW)
    assert out == {"reviewed": 20, "verdict": "push-fix", "event": None, "reason": "no commits"}
    assert not calls(fake, "POST", "pulls/20/reviews") and not calls(fake, "POST", "statuses/abc123")
    assert any("push-fix verdict but no commits were found in the review worktree; re-dispatch" in c.body["body"]
               for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/20/comments")
    assert sh(["git", "rev-parse", "origin/meld/13-ci-pipeline"], repo) == before          # nothing was pushed
    (submitted,) = (tmp_path / "home" / "results" / "claude" / "submitted").glob("review-20-abc123-*.json")
    assert json.loads(submitted.read_text())["outcome"] == "no-commits"
    with pytest.raises(DispatchError, match="no collected review result"):                  # consumed, like a missing verdict
        review(ctx, 20, now=NOW)


def test_review_push_fix_from_a_reaped_worktree_is_a_no_action_outcome(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, PUSH_FIX_SCRIPT)
    shutil.rmtree(wt)          # $MELD_HOME is disposable: the worktree, fix commit included, vanished before meld review ran
    out = review(ctx, 20, now=NOW)
    assert out["event"] is None and out["reason"] == "no commits"
    assert not calls(fake, "POST", "pulls/20/reviews")


def test_review_push_fix_resume_refuses_a_worktree_that_moved_after_the_push(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, PUSH_FIX_SCRIPT)
    update_result(tmp_path / "home", "claude", "review", 20, {"pushed": "0" * 40, "pushed_at": "2026-09-03T11:59:00Z"})   # a recorded push this worktree does not match
    with pytest.raises(DispatchError, match="the worktree moved after the push"):
        review(ctx, 20, now=NOW)
    assert not calls(fake, "POST", "pulls/20/reviews")
    assert load_result(tmp_path / "home", "claude", "review", 20) is not None     # kept for inspection, not consumed


def test_review_body_is_capped(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)   # claude PM approves codex's PR #20 with a verbose summary
    fake.route("POST", R + "pulls/20/reviews", {"id": 503, "html_url": "u503"})
    fake.route("POST", R + "statuses/abc123", {"id": 77})
    store_result(tmp_path / "home", "claude", "review", 20,
                 {"head_sha": "abc123", "result": {"verdict": "approve", "summary": "s" * (BODY_LIMIT + 1000), "verified_by": "y", "comments": []}},
                 "2026-09-03T12:00:00Z")
    assert review(ctx, 20, now=NOW)["review_id"] == 503
    (body,) = calls(fake, "POST", "pulls/20/reviews")
    assert body["body"].endswith(TRUNCATED) and len(body["body"]) == BODY_LIMIT + len(TRUNCATED)


def test_author_group_re_review_after_a_push_fix_is_a_comment_review_whose_status_clears_the_quorum(tmp_path):
    fake, ctx, repo, wt = reviewed_pr(tmp_path, PUSH_FIX_SCRIPT)          # claude push-fixes codex's PR #20 ...
    new_head = sh(["git", "rev-parse", "HEAD"], wt)
    fake.route("POST", R + f"statuses/{new_head}", {"id": 78})
    assert review(ctx, 20, now=NOW)["event"] == "COMMENT"                 # ... and asks codex to review the new head
    # GitHub after the push: the head is claude's commit on a PR codex's bot opened
    pulls = fixture("pulls.json")
    for p in pulls:
        if p["number"] == 20:
            p["head"]["sha"] = new_head
    fake.route("GET", R + "pulls", pulls)
    fake.route("GET", R + "pulls/20/commits",
               [{"sha": "abc123", "author": {"login": "meld-codex-bot"}, "committer": {"login": "meld-codex-bot"}},
                {"sha": new_head, "author": {"login": "meld-claude-bot"}, "committer": {"login": "meld-claude-bot"}}])
    fake.route("GET", R + "pulls/20/reviews", [{"id": 501, "user": {"login": "meld-claude-bot"}, "state": "COMMENTED", "commit_id": new_head,
                                                "submitted_at": "2026-09-03T12:00:00Z"}])
    fake.route("GET", R + f"commits/{new_head}/check-runs", fixture("checkruns_abc123.json"))
    fake.route("GET", R + f"commits/{new_head}/status", {"state": "pending", "statuses": [{"context": "meld/review/claude", "state": "pending"}]})
    codex = load_context(repo / ".meld.toml", env=env_for(tmp_path, group="codex", role="pm"), run=fake, adapter_for=no_skills)
    # codex authored the PR but no longer holds the head, so dispatching its own reviewer is legal (spec 13 step 3)
    out = dispatch(codex, 20, kind="review", adapter=FakeAdapter(script=REVIEW_SCRIPT), now=NOW)
    wait_for_exit(out["pid"])
    collect(codex, 20, now=NOW)
    fake.route("POST", R + "pulls/20/reviews", {"id": 601, "html_url": "https://github.com/acme/widgets/pull/20#pullrequestreview-601"})
    out = review(codex, 20, now=NOW)
    assert out["event"] == "COMMENT" and out["as_author"] is True and out["review_id"] == 601 and out["head"] == new_head and out["verdict"] == "approve"
    posted = calls(fake, "POST", "pulls/20/reviews")[-1]
    assert posted["event"] == "COMMENT" and posted["commit_id"] == new_head            # never an APPROVE: GitHub would 422 the author's account
    # plan 5 task 2: the body now opens with the AUTHOR_VERDICT_MARKER line (assemble._pull keys off it for the
    # request-changes case; an approve marker is inert there, but the line is written for every as_author verdict).
    from meld.review import AUTHOR_VERDICT_MARKER
    assert posted["body"].startswith(f"{AUTHOR_VERDICT_MARKER} approve\n\n"
                                     "Verdict: approve — posted as a comment review because GitHub forbids a PR author's account from approving its own PR; "
                                     "the `meld/review/codex` commit status carries this verdict.\n\nlooks right")
    status = calls(fake, "POST", f"statuses/{new_head}")[-1]
    assert status["context"] == "meld/review/codex" and status["state"] == "success" and status["description"] == "codex review: approve"
    assert not [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments" and "PROTOCOL-VIOLATION" in c.body["body"]]
    # merge: no GitHub approval exists on the head, but codex's `success` status on it is the author group's approval
    fake.route("GET", R + f"commits/{new_head}/status", {"state": "success", "statuses": [{"context": "meld/review/claude", "state": "pending"},
                                                                                           {"context": "meld/review/codex", "state": "success"}]})
    fake.route("PUT", R + "pulls/20/merge", {"sha": "m200", "merged": True})
    assert merge(ctx, 20) == {"merged": 20, "sha": "m200", "issue": 13, "approvals": ["codex"]}
    (put,) = [c.body for c in fake.calls if c.method == "PUT"]
    assert put["sha"] == new_head


def test_submit_review_folds_findings_into_the_body_on_422():
    class StubGh:
        def __init__(self):
            self.calls: list[dict] = []

        def create_review(self, number, event, body, commit_id, comments):
            self.calls.append({"number": number, "event": event, "body": body, "commit_id": commit_id, "comments": comments})
            if len(self.calls) == 1:
                raise GitHubError("422 Unprocessable Entity", status=422)
            return {"id": 9}

    stub = StubGh()
    comments = [{"path": "a.py", "line": 500, "body": "out of diff"}]
    resp = submit_review(stub, 1, "REQUEST_CHANGES", "the summary", "sha1", comments)
    assert resp == {"id": 9}
    assert len(stub.calls) == 2
    assert "## Findings" in stub.calls[1]["body"]
    assert stub.calls[1]["comments"] is None


from meld.memory import LESSONS_FILE, content_hash, decide
from tests.test_memory import DECISION_THREAD, DISAGREE_THREAD, with_thread


def _contents(text):
    import base64
    return {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}


def memory_pr_on_board(fake, repo, path, text, number=40, head="mem0001", kind="decision", ref="dec-1", digest=None, base=None):
    """A memory PR whose changed file reads `text` at `head`; on the default branch the same path reads `base` (absent
    when None). Contents are routed per `ref` query parameter, never by path alone, so a verifier that fetched the
    wrong ref could not be served the right text by accident."""
    pulls = fixture("pulls.json") + [{"number": number, "title": f"Decision {ref}", "state": "open", "merged_at": None,
                                      "user": {"login": "meld-claude-bot"}, "head": {"ref": f"meld/memory-{ref}", "sha": head}, "labels": [],
                                      "body": f"Records.\n\nmeld-memory: {kind} {ref} {digest or content_hash(text)}\nverified-by: x"}]
    fake.route("GET", R + "pulls", pulls)
    fake.route("GET", R + f"pulls/{number}/reviews", [])
    fake.route("GET", R + f"pulls/{number}/commits", [{"sha": head, "author": {"login": "meld-claude-bot"}, "committer": {"login": "meld-claude-bot"}}])
    fake.route("GET", R + f"pulls/{number}/files", [{"filename": path}])
    fake.route("GET", R + f"commits/{head}/check-runs", fixture("checkruns_abc123.json"))
    fake.route("GET", R + f"commits/{head}/status", {"state": "pending", "statuses": []})
    fake.route("GET", R + f"contents/{path}", _contents(text), query={"ref": head})
    if base is None:
        fake.route("GET", R + f"contents/{path}", {"message": "Not Found"}, status=404, query={"ref": "main"})
    else:
        fake.route("GET", R + f"contents/{path}", _contents(base), query={"ref": "main"})
    fake.route("POST", R + f"pulls/{number}/reviews", {"id": 601, "html_url": "r601"})
    fake.route("POST", R + f"statuses/{head}", {"id": 79})


def test_review_auto_approves_a_verified_decision_pr(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, group="codex")          # codex reviews claude's memory PR
    with_thread(fake, DECISION_THREAD)
    from meld.grammar import agreement_for
    from meld.memory import render_decision
    board_comments = None
    text = render_decision("dec-1", agreement_for([__import__("meld.grammar", fromlist=["parse_comment"]).parse_comment(c, ctx.cfg) for c in fixture("comments_10.json") + DECISION_THREAD], "dec-1", ctx.cfg),
                           "https://github.com/acme/widgets/issues/10#issuecomment-203", "2026-09-03")
    memory_pr_on_board(fake, repo, ".meld/decisions/0001-dec-1.md", text)
    out = review(ctx, 40, now=NOW)
    assert out == {"reviewed": 40, "memory": "decision", "event": "APPROVE", "review_id": 601}
    (body,) = [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "pulls/40/reviews"]
    assert body["event"] == "APPROVE" and "hash" in body["body"]
    assert [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "statuses/mem0001"][0]["state"] == "success"


def test_review_requests_changes_on_a_tampered_memory_pr(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, group="codex")
    with_thread(fake, DECISION_THREAD)
    text = ("# Decision dec-1\n\n- Date: 2026-09-03\n- Agreed by: claude, codex\n"
            "- Source: https://github.com/acme/widgets/issues/10#issuecomment-203\n\n## Decision\n\nUse MySQL instead.\n")
    memory_pr_on_board(fake, repo, ".meld/decisions/0001-dec-1.md", text)             # hash matches the file, text does not match the agreement
    out = review(ctx, 40, now=NOW)
    assert out["event"] == "REQUEST_CHANGES" and "does not match" in out["reason"]
    memory_pr_on_board(fake, repo, ".meld/decisions/0001-dec-1.md", text, digest="b" * 64)   # hash mismatch
    assert "hash" in review(ctx, 40, now=NOW)["reason"]


def genuine_decision(ctx):
    from meld.grammar import agreement_for, parse_comment
    from meld.memory import render_decision
    parsed = [parse_comment(c, ctx.cfg) for c in fixture("comments_10.json") + DECISION_THREAD]
    return render_decision("dec-1", agreement_for(parsed, "dec-1", ctx.cfg), "https://github.com/acme/widgets/issues/10#issuecomment-203", "2026-09-03")


def test_review_rejects_a_decision_file_that_is_not_the_rendering_byte_for_byte(tmp_path):
    # F4: the hash is self-computed and the `## Decision` text matches word for word; only the unverified preamble differs.
    fake, ctx, repo, origin = make_product(tmp_path, group="codex")
    with_thread(fake, DECISION_THREAD)
    genuine = genuine_decision(ctx)
    padded = genuine.replace("\n## Decision\n", "\n- Note: PostgreSQL 16 or later, managed by the platform team\n\n## Decision\n")
    assert padded != genuine and padded.split("## Decision\n\n", 1)[1] == genuine.split("## Decision\n\n", 1)[1]
    memory_pr_on_board(fake, repo, ".meld/decisions/0001-dec-1.md", padded)
    out = review(ctx, 40, now=NOW)
    assert out["event"] == "REQUEST_CHANGES" and "byte for byte" in out["reason"]
    # the Source link must be the verified joint comment, not some other comment
    memory_pr_on_board(fake, repo, ".meld/decisions/0001-dec-1.md", genuine.replace("issuecomment-203", "issuecomment-999"), head="mem0002")
    assert "byte for byte" in review(ctx, 40, now=NOW)["reason"]
    # the Date line must be an ISO date (it is the one input the verifier takes from the file itself)
    for i, bad in enumerate(("- Date: yesterday", "- Date: 2026-13-45", "- Date: 2026-9-3")):
        memory_pr_on_board(fake, repo, ".meld/decisions/0001-dec-1.md", genuine.replace("- Date: 2026-09-03", bad), head=f"mem000{5 + i}")
        assert "- Date:" in review(ctx, 40, now=NOW)["reason"], bad
    memory_pr_on_board(fake, repo, ".meld/decisions/0001-dec-1.md", genuine.replace("- Date: 2026-09-03\n", ""), head="mem0009")
    assert "- Date:" in review(ctx, 40, now=NOW)["reason"]


def test_review_approves_exactly_what_decide_wrote(tmp_path):
    fake, claude, repo, origin = make_product(tmp_path, role="orchestrator")
    with_thread(fake, DECISION_THREAD)
    fake.route("POST", R + "pulls", {"number": 40, "html_url": "https://github.com/acme/widgets/pull/40"})
    out = decide(claude, "dec-1", now=NOW)
    text = sh(["git", "show", f"origin/meld/memory-dec-1:{out['path']}"], repo) + "\n"
    assert content_hash(text) == out["hash"]
    codex = load_context(repo / ".meld.toml", env=env_for(tmp_path, group="codex", role="pm"), run=fake, adapter_for=no_skills)
    memory_pr_on_board(fake, repo, out["path"], text)
    assert review(codex, 40, now=NOW)["event"] == "APPROVE"


def test_review_requests_changes_on_a_decision_pr_from_a_disagree_round(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, group="codex")
    with_thread(fake, DISAGREE_THREAD)
    from meld.grammar import agreement_for, parse_comment
    from meld.memory import render_decision
    parsed = [parse_comment(c, ctx.cfg) for c in fixture("comments_10.json") + DISAGREE_THREAD]
    agreement = agreement_for(parsed, "dec-2", ctx.cfg)
    assert agreement.status == "disagree" and agreement.joint_verified
    text = render_decision("dec-2", agreement, "https://github.com/acme/widgets/issues/10#issuecomment-203", "2026-09-03")
    memory_pr_on_board(fake, repo, ".meld/decisions/0001-dec-2.md", text, ref="dec-2")    # hash and rendering both check out
    out = review(ctx, 40, now=NOW)
    assert out["event"] == "REQUEST_CHANGES" and out["reason"] == "dec-2 ended in DISAGREE; the human decides"


def test_review_auto_approves_a_lesson_pr_for_a_resolved_item(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, group="codex")
    entry = "\n## 2026-09-03 — #13 (claude)\n\nPin the action version.\n"
    memory_pr_on_board(fake, repo, ".meld/memory/lessons.md", "# Lessons\n" + entry, number=41, head="mem0002", kind="lesson", ref="#13", digest=content_hash(entry))
    out = review(ctx, 41, now=NOW)
    assert out["event"] == "APPROVE" and out["memory"] == "lesson"


OLD_LESSONS = "# Lessons\n\n## 2026-09-01 — #7 (codex)\n\nMigrations need a lock timeout.\n"
NEW_ENTRY = "\n## 2026-09-03 — #13 (claude)\n\nPin the action version.\n"


def lesson_pr(fake, repo, text, head, base):
    memory_pr_on_board(fake, repo, LESSONS_FILE, text, number=41, head=head, kind="lesson", ref="#13", digest=content_hash(NEW_ENTRY), base=base)


def test_review_rejects_a_lesson_pr_that_rewrites_an_earlier_lesson(tmp_path):
    # F3: the appended entry hashes to the marker either way; what distinguishes these is whether the head is the default
    # branch's file plus that one entry -- a lesson PR is append-only by verification, not by convention.
    fake, ctx, repo, origin = make_product(tmp_path, group="codex")
    rewritten = OLD_LESSONS.replace("need a lock timeout", "never need a lock timeout") + NEW_ENTRY
    lesson_pr(fake, repo, rewritten, "mem0002", OLD_LESSONS)
    out = review(ctx, 41, now=NOW)
    assert out["event"] == "REQUEST_CHANGES" and "one appended entry" in out["reason"]
    lesson_pr(fake, repo, "# Lessons\n" + NEW_ENTRY, "mem0003", OLD_LESSONS)              # dropping the earlier lesson is a rewrite too
    assert review(ctx, 41, now=NOW)["event"] == "REQUEST_CHANGES"
    lesson_pr(fake, repo, OLD_LESSONS + NEW_ENTRY, "mem0004", OLD_LESSONS)                  # a pure append onto that base verifies
    assert review(ctx, 41, now=NOW)["event"] == "APPROVE"
    # exactly one entry: appending two at once is refused (the marker names one hash; the head must be base + that entry)
    lesson_pr(fake, repo, OLD_LESSONS + "\n## 2026-09-02 — #9 (codex)\n\nSmuggled.\n" + NEW_ENTRY, "mem0005", OLD_LESSONS)
    assert review(ctx, 41, now=NOW)["event"] == "REQUEST_CHANGES"


def test_review_rejects_a_decision_pr_whose_file_is_outside_the_decisions_dir(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, group="codex")
    with_thread(fake, DECISION_THREAD)
    # hash matches (self-computed) and the agreed text matches exactly -- only the changed file's path is wrong.
    # Without the decisions-dir gate this would verify clean and get auto-APPROVEd despite being arbitrary code.
    text = "print('pwned')\n\n## Decision\n\nUse PostgreSQL for the ledger.\n\nverified-by: read docs/adr/0001.md\n"
    memory_pr_on_board(fake, repo, "src/meld/rules.py", text)
    out = review(ctx, 40, now=NOW)
    assert out["event"] == "REQUEST_CHANGES" and ".meld/decisions" in out["reason"]


def test_review_on_a_memory_pr_never_posts_twice(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, group="codex")
    with_thread(fake, DECISION_THREAD)
    from meld.grammar import agreement_for, parse_comment
    from meld.memory import render_decision
    parsed = [parse_comment(c, ctx.cfg) for c in fixture("comments_10.json") + DECISION_THREAD]
    text = render_decision("dec-1", agreement_for(parsed, "dec-1", ctx.cfg),
                           "https://github.com/acme/widgets/issues/10#issuecomment-203", "2026-09-03")
    memory_pr_on_board(fake, repo, ".meld/decisions/0001-dec-1.md", text)
    first = review(ctx, 40, now=NOW)
    second = review(ctx, 40, now=NOW)
    assert first == second == {"reviewed": 40, "memory": "decision", "event": "APPROVE", "review_id": 601}
    assert len(calls(fake, "POST", "pulls/40/reviews")) == 1
    assert len(calls(fake, "POST", "statuses/mem0001")) == 1


def test_review_on_an_unchanged_tampered_memory_pr_posts_once(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, group="codex")
    with_thread(fake, DECISION_THREAD)
    text = "# Decision dec-1\n\n## Decision\n\nUse MySQL instead.\n"          # hash matches, agreed text doesn't -- same reason both times
    memory_pr_on_board(fake, repo, ".meld/decisions/0001-dec-1.md", text)
    first = review(ctx, 40, now=NOW)
    second = review(ctx, 40, now=NOW)
    assert first["event"] == "REQUEST_CHANGES" and second["event"] == "REQUEST_CHANGES"
    assert first["reason"] == second["reason"]
    assert len(calls(fake, "POST", "pulls/40/reviews")) == 1
    assert len(calls(fake, "POST", "statuses/mem0001")) == 1


def test_review_on_a_memory_pr_re_verifies_when_the_head_moves(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, group="codex")
    with_thread(fake, DECISION_THREAD)
    from meld.grammar import agreement_for, parse_comment
    from meld.memory import render_decision
    parsed = [parse_comment(c, ctx.cfg) for c in fixture("comments_10.json") + DECISION_THREAD]
    text = render_decision("dec-1", agreement_for(parsed, "dec-1", ctx.cfg),
                           "https://github.com/acme/widgets/issues/10#issuecomment-203", "2026-09-03")
    memory_pr_on_board(fake, repo, ".meld/decisions/0001-dec-1.md", text)                      # head H1 = mem0001
    first = review(ctx, 40, now=NOW)
    assert first["event"] == "APPROVE"
    memory_pr_on_board(fake, repo, ".meld/decisions/0001-dec-1.md", text, head="mem0002")      # a human pushed a new head H2
    second = review(ctx, 40, now=NOW)
    assert second["event"] == "APPROVE"
    posts = calls(fake, "POST", "pulls/40/reviews")
    assert len(posts) == 2 and posts[1]["commit_id"] == "mem0002"          # re-verified and re-posted against the new head
    assert len(calls(fake, "POST", "statuses/mem0002")) == 1


def test_review_on_a_memory_pr_survives_a_failing_status_post(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, group="codex")
    with_thread(fake, DECISION_THREAD)
    from meld.grammar import agreement_for, parse_comment
    from meld.memory import render_decision
    parsed = [parse_comment(c, ctx.cfg) for c in fixture("comments_10.json") + DECISION_THREAD]
    text = render_decision("dec-1", agreement_for(parsed, "dec-1", ctx.cfg),
                           "https://github.com/acme/widgets/issues/10#issuecomment-203", "2026-09-03")
    memory_pr_on_board(fake, repo, ".meld/decisions/0001-dec-1.md", text)
    fake.route("POST", R + "statuses/mem0001", {"message": "Internal Server Error"}, status=500)
    first = review(ctx, 40, now=NOW)
    assert first["event"] == "APPROVE" and first.get("status_error") is not None    # does not raise; the review still landed
    assert len(calls(fake, "POST", "pulls/40/reviews")) == 1
    fake.route("POST", R + "statuses/mem0001", {"id": 79})     # status endpoint recovers
    second = review(ctx, 40, now=NOW)
    assert second["event"] == "APPROVE"
    assert len(calls(fake, "POST", "pulls/40/reviews")) == 1   # no duplicate review on the retry
    assert len(calls(fake, "POST", "statuses/mem0001")) == 1   # the earlier failed attempt was not retried


def test_author_groups_request_changes_counts_toward_the_pingpong_cap():
    from meld.assemble import _pull
    from meld.review import AUTHOR_VERDICT_MARKER
    from tests.fakegh import FakeGh
    from meld.github import GitHub
    fake = FakeGh()
    raw = {"number": 20, "title": "t", "body": "Closes #13", "head": {"ref": "meld/13-x", "sha": "abc123"}, "user": {"login": "meld-claude-bot"}, "state": "open", "labels": []}
    fake.route("GET", "repos/acme/widgets/pulls/20/reviews", [
        {"user": {"login": "meld-claude-bot"}, "state": "COMMENTED", "commit_id": "abc123", "submitted_at": "2026-09-03T10:00:00Z",
         "body": f"{AUTHOR_VERDICT_MARKER} request-changes\n\nVerdict: request-changes — posted as a comment review ..."}])
    fake.route("GET", "repos/acme/widgets/pulls/20/commits", [{"author": {"login": "meld-codex-bot"}, "committer": {"login": "meld-codex-bot"}}])
    fake.route("GET", "repos/acme/widgets/commits/abc123/check-runs", {"check_runs": []})
    fake.route("GET", "repos/acme/widgets/pulls/20/files", [])
    fake.route("GET", "repos/acme/widgets/commits/abc123/status", {"state": "pending", "statuses": []})
    gh = GitHub("acme/widgets", token="t", run=fake)
    pr = _pull(gh, raw)
    assert pr.reviews[0].state == "CHANGES_REQUESTED" and pr.reviews[0].as_author
    assert pr.changes_requested_total() == 1
