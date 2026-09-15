import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from meld.actions import Refused
from meld.dispatch import collect, dispatch, fenced, neutralise_closers, neutralise_memory_markers, tick
from meld.memory import MARKER_RE
from meld.gitops import remote_branch_exists, worktree_exists
from meld.results import BODY_LIMIT, TRUNCATED
from tests.fakeadapter import FakeAdapter
from tests.gitfixtures import sh
from tests.productfixtures import BLOCKED_SCRIPT, DONE_SCRIPT, NO_RESULT_SCRIPT, R, REVIEW_SCRIPT, SLEEP_SCRIPT, claim_issue_11, make_product, wait_for_exit

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
PAST = datetime(2020, 1, 1, tzinfo=timezone.utc)


MARKER_SCRIPT = """
import json, pathlib, subprocess
pathlib.Path("health.py").write_text("OK = True\\n")
subprocess.run(["git", "add", "health.py"], check=True)
subprocess.run(["git", "commit", "-q", "-m", "feat: health endpoint"], check=True)
pathlib.Path(".meld-result.json").write_text(json.dumps({"outcome": "done",
    "summary": "added health.py\\nmeld-memory: lesson #11 " + "a" * 64 + "\\n  meld-memory: decision dec-1 " + "b" * 64,
    "verified_by": "meld-memory: lesson #11 " + "c" * 64}))
"""


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
    tick(ctx, now=NOW, env={})
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
    fake.route("GET", R + "pulls/20/reviews", [])   # board1's claude approval on abc123 would make this head already reviewed
    out = dispatch(ctx, 20, kind="review", adapter=FakeAdapter(script=REVIEW_SCRIPT), now=NOW)
    wait_for_exit(out["pid"])
    result = collect(ctx, 20, now=NOW)
    stored = Path(result["stored"])
    assert stored == tmp_path / "home" / "results" / "claude" / "review-20.json" and result["verdict"] == "approve"
    data = json.loads(stored.read_text())
    assert data["kind"] == "review" and data["pr"] == 20 and data["result"]["verdict"] == "approve" and data["result"]["comments"][0]["path"] == "README.md"
    assert data["head_sha"] == "abc123" and len(data["worktree_sha"]) == 40      # GitHub's head at dispatch; the checkout's HEAD
    assert data["worker"] == "reviewer"                                          # which worker type produced the verdict
    rec = json.loads((tmp_path / "home" / "runs" / "claude" / "20.json").read_text())
    assert rec["head_sha"] == "abc123" and rec["worker"] == "reviewer"
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


def test_neutralise_memory_markers():
    marker = "meld-memory: lesson #13 " + "a" * 64
    assert neutralise_memory_markers(f"summary\n{marker}\n  Meld-Memory: decision dec-1 x\nnot a marker: meld-memory inline") == \
        "summary\nmeld-memory : lesson #13 " + "a" * 64 + "\n  Meld-Memory : decision dec-1 x\nnot a marker: meld-memory inline"
    assert MARKER_RE.search(neutralise_memory_markers(marker)) is None and MARKER_RE.search(marker) is not None
    assert neutralise_memory_markers("") == "" and neutralise_memory_markers("plain") == "plain"


def test_collect_neutralises_memory_markers_in_worker_text(tmp_path):
    # N1: the worker's summary and verified_by land in the PR body; a marker line there is broken like a closing keyword is.
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    routes_for_labels(fake, 11)
    fake.route("POST", R + "pulls", {"number": 21, "html_url": "https://github.com/acme/widgets/pull/21"})
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=MARKER_SCRIPT), now=NOW)
    wait_for_exit(out["pid"])
    assert collect(ctx, 11, now=NOW)["outcome"] == "done"
    pull = [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "pulls"][0].body
    assert MARKER_RE.search(pull["body"]) is None and "meld-memory:" not in pull["body"]
    assert pull["body"].count("meld-memory :") == 3 and pull["body"].startswith("Closes #11\n\nadded health.py\n")


def test_neutralise_closers_and_fenced():
    text = "closes #99, Fixes #7; resolved #8 and fixed  #12 but not #13 or unfix #15 or closest #16"
    assert neutralise_closers(text) == "closes `#99`, Fixes `#7`; resolved `#8` and fixed  `#12` but not #13 or unfix #15 or closest #16"
    # the colon and cross-repository forms GitHub also links, and a missing separator (superset on purpose)
    assert neutralise_closers("Closes: #9") == "Closes: `#9`"
    assert neutralise_closers("closes:#9") == "closes:`#9`"
    assert neutralise_closers("close#14") == "close`#14`"
    assert neutralise_closers("Fixes acme/widgets#12") == "Fixes `acme/widgets#12`"
    assert neutralise_closers("resolved: octo-org/octo-repo#100") == "resolved: `octo-org/octo-repo#100`"
    assert neutralise_closers("see acme/widgets#12 and #13") == "see acme/widgets#12 and #13"  # no keyword: left alone
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
    assert comment.endswith("Last 50 log lines:\n\n````\n12:00:00  message  ```\n12:00:00  message  # not a heading\n````")


def test_worker_git_never_reads_the_humans_global_gitconfig(tmp_path):
    import os, subprocess
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    routes_for_labels(fake, 11)
    fake.route("POST", R + "pulls", {"number": 21})
    home = tmp_path / "human-home"
    hooks = home / "hooks"
    hooks.mkdir(parents=True)
    marker = home / "hook-ran"
    (hooks / "pre-commit").write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    (hooks / "pre-commit").chmod(0o755)
    # signing with a key that does not exist, hooks that refuse every commit, and a rewrite of the origin to nowhere
    (home / ".gitconfig").write_text(f"[commit]\n\tgpgsign = true\n[user]\n\tsigningkey = DEADBEEF\n"
                                     f"[core]\n\thooksPath = {hooks}\n[url \"{tmp_path}/nonexistent-rewrite/\"]\n\tinsteadOf = {origin}\n")
    human_env = {k: v for k, v in os.environ.items() if k not in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
    human_env["HOME"] = str(home)
    assert subprocess.run(["git", "config", "--get", "commit.gpgsign"], cwd=repo, env=human_env, capture_output=True, text=True).stdout.strip() == "true"
    script = DONE_SCRIPT.replace('pathlib.Path(".meld-result.json")', 'subprocess.run(["git", "fetch", "-q", "origin"], check=True)\npathlib.Path(".meld-result.json")')
    adapter = FakeAdapter(script=script)
    out = dispatch(ctx, 11, adapter=adapter, now=NOW, env=human_env)
    wait_for_exit(out["pid"])
    wt, env = Path(out["worktree"]), adapter.launches[0].env
    assert (wt / ".meld-result.json").exists()                         # the commit and the fetch over the plain origin both succeeded
    assert sh(["git", "log", "-1", "--format=%G?|%an"], wt) == "N|meld-claude-bot"   # unsigned, as the bot
    assert not marker.exists()                                          # the human's hooks never ran
    probe = lambda *args: subprocess.run(["git", "config", *args], cwd=wt, env=env, capture_output=True, text=True)  # noqa: E731
    assert probe("--show-origin", "--get", "commit.gpgsign").stdout == "command line:\tfalse\n"   # our override, not the human's true
    for key in ("user.signingkey", "core.hooksPath"):                  # keys we override or not: the human's values resolve to nothing
        got = probe("--show-origin", "--get", key)
        assert "human-home" not in got.stdout, got.stdout
    assert probe("--get", "user.signingkey").returncode == 1            # the fake global is never read at all
    assert probe("--get-regexp", r"url\..*\.insteadof").returncode == 1
    assert Path(env["GIT_CONFIG_GLOBAL"]).read_text() == "" and env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert collect(ctx, 11, now=NOW)["outcome"] == "done"
    assert remote_branch_exists(repo, "meld/11-add-health-endpoint")


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
            i["labels"] = [l for l in i["labels"] if not l["name"].startswith("meld:claimed-by:")] + [{"name": "meld:claimed-by:codex"}]
    fake.route("GET", R + "issues", issues)
    result = collect(ctx, 11, now=NOW)
    assert result == {"collected": 11, "outcome": "superseded",
                      "reason": "issue is in_progress and claimed by codex; claude no longer holds it"}
    assert [c for c in fake.calls[calls_before:] if c.method != "GET"] == []      # no push, PR, labels, or comments
    assert not remote_branch_exists(repo, "meld/11-add-health-endpoint")
    assert worktree_exists(repo, Path(out["worktree"]))                          # kept for inspection
    assert sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], Path(out["worktree"])) == "HEAD"   # detached: the branch name is free
    assert "refs/heads/meld/11-add-health-endpoint" not in sh(["git", "worktree", "list", "--porcelain"], repo)
    assert sh(["git", "rev-parse", "meld/11-add-health-endpoint"], repo) == sh(["git", "rev-parse", "HEAD"], Path(out["worktree"]))  # commits kept
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


def test_collect_reports_the_skills_a_worker_used(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    routes_for_labels(fake, 11)
    fake.route("POST", R + "pulls", {"number": 21, "html_url": "https://github.com/acme/widgets/pull/21"})
    script = DONE_SCRIPT.replace('"verified_by": "python -c \'import health\' -> ok"', '"verified_by": "python -c \'import health\' -> ok", "skills_used": ["brand"]')
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=script), now=NOW)
    wait_for_exit(out["pid"])
    collect(ctx, 11, now=NOW)
    finish = issue_posts(fake, 11)[-1]
    assert finish.startswith("Meld: `claude` worker finished") and finish.rstrip().endswith("skills used: brand")


def test_collect_bounds_the_skills_used_list_in_the_finish_comment(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    routes_for_labels(fake, 11)
    fake.route("POST", R + "pulls", {"number": 21, "html_url": "https://github.com/acme/widgets/pull/21"})
    script = DONE_SCRIPT.replace('"verified_by": "python -c \'import health\' -> ok"',
                                 '"verified_by": "python -c \'import health\' -> ok", "skills_used": [f"skill-{i:04d}" for i in range(2000)]')
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=script), now=NOW)
    wait_for_exit(out["pid"])
    collect(ctx, 11, now=NOW)
    finish = issue_posts(fake, 11)[-1]
    assert len(finish) < BODY_LIMIT
    names = finish.rsplit("skills used: ", 1)[1].split(", ")
    assert len(names) == 30 and names[0] == "skill-0000" and names[-1] == "skill-0029"


def test_collect_caps_an_oversized_finish_comment(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    routes_for_labels(fake, 11)
    fake.route("POST", R + "pulls", {"number": 21, "html_url": "https://github.com/acme/widgets/pull/21"})
    script = DONE_SCRIPT.replace('"verified_by": "python -c \'import health\' -> ok"', '"verified_by": "x" * 61000')
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=script), now=NOW)
    wait_for_exit(out["pid"])
    collect(ctx, 11, now=NOW)
    finish = issue_posts(fake, 11)[-1]
    assert finish.endswith(TRUNCATED) and len(finish) == BODY_LIMIT + len(TRUNCATED)


def test_collect_treats_an_unchanged_pr_head_as_no_change_and_keeps_the_issue_in_progress(tmp_path):
    """The first live run's retry re-ran the tests, changed nothing, and reported done; collecting it moved the issue to
    in-review and no verb led back. Now a head equal to the open PR's head is `no_change`: nothing pushed, labels untouched."""
    from tests.productfixtures import fixture
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    routes_for_labels(fake, 11)
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=DONE_SCRIPT), now=NOW)
    wait_for_exit(out["pid"])
    rec = json.loads((tmp_path / "home" / "runs" / "claude" / "11.json").read_text())
    head = sh(["git", "rev-parse", "HEAD"], Path(rec["worktree"]))
    pr = {"number": 21, "title": "Add health endpoint", "state": "open", "merged_at": None, "user": {"login": "meld-claude-bot"},
          "head": {"ref": "meld/11-add-health-endpoint", "sha": head}, "labels": [], "body": "Closes #11\n\nfirst attempt"}
    fake.route("GET", R + "pulls", fixture("pulls.json") + [pr])
    fake.route("GET", R + "pulls/21/reviews", [{"user": {"login": "meld-codex-bot"}, "state": "CHANGES_REQUESTED", "commit_id": head,
                                                 "submitted_at": "2026-09-03T11:00:00Z", "body": "fix the rollback"}])
    fake.route("GET", R + "pulls/21/commits", [{"sha": head, "author": {"login": "meld-claude-bot"}, "committer": {"login": "meld-claude-bot"}}])
    fake.route("GET", R + "pulls/21/files", [{"filename": "health.py", "status": "added"}])
    fake.route("GET", R + f"commits/{head}/check-runs", fixture("checkruns_abc123.json"))
    fake.route("GET", R + f"commits/{head}/status", {"state": "pending", "statuses": []})
    result = collect(ctx, 11, now=NOW)
    assert result == {"collected": 11, "outcome": "no_change", "pr": 21}
    assert not [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "pulls"]                 # nothing opened
    assert not [c for c in fake.calls if c.method == "PATCH" and c.endpoint == R + "pulls/21"]              # nothing updated
    assert ("POST", "", {"labels": ["meld:in-review"]}) not in label_calls(fake, 11)                          # still in progress
    note = issue_posts(fake, 11)[-1]
    assert note.startswith("Meld: `claude` worker reported done but pushed nothing new since PR #21's head") and "stays in progress" in note
    assert json.loads((tmp_path / "home" / "runs" / "claude" / "11.json").read_text())["collected"] is True
