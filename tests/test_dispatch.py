import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from meld.actions import Refused
from meld.brief import BRIEF_FILE
from meld.context import load_context
from meld.dispatch import DispatchError, dispatch, git_identity, worker_env
from meld.gitops import head_sha, worktree_exists
from tests.conftest import env_for
from tests.fakeadapter import FakeAdapter, no_skills
from tests.gitfixtures import sh
from tests.productfixtures import DONE_SCRIPT, R, claim_issue_11, fixture, make_product, wait_for_exit

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def posted(fake, number):
    return [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + f"issues/{number}/comments"]


def test_dispatch_build_happy_path(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    adapter = FakeAdapter(script=DONE_SCRIPT)
    out = dispatch(ctx, 11, adapter=adapter, now=NOW)
    assert out["dispatched"] == 11 and out["kind"] == "build" and out["worktree_mode"] == "new"
    assert out["branch"] == "meld/11-add-health-endpoint" and out["deadline"] == "2026-09-03T12:20:00Z" and out["lease_until"] == "2026-09-03T12:30:00Z"
    wt = Path(out["worktree"])
    assert wt == tmp_path / "home" / "worktrees" / "claude" / "11-add-health-endpoint" and worktree_exists(repo, wt)
    assert (wt / BRIEF_FILE).read_text().startswith("# Meld brief: backend for #11")
    assert ".meld-brief.md" not in sh(["git", "status", "--porcelain"], wt)
    spec = adapter.launches[0]
    assert spec.role == "worker" and spec.model == "claude-sonnet-5" and spec.effort == "medium" and spec.cwd == wt and spec.timeout_seconds == 1200
    assert "MELD_TOKEN_CLAUDE" not in spec.env and "GH_TOKEN" not in spec.env and spec.env["MELD_WORKER"] == "1" and spec.max_turns == 200
    # the worker commits as the group's bot through the environment, never through the shared clone config
    assert spec.env["GIT_AUTHOR_NAME"] == spec.env["GIT_COMMITTER_NAME"] == "meld-claude-bot"
    assert spec.env["GIT_AUTHOR_EMAIL"] == spec.env["GIT_COMMITTER_EMAIL"] == "7+meld-claude-bot@users.noreply.github.com"
    # and it cannot borrow the human's GitHub or git credentials
    gh_dir = Path(spec.env["GH_CONFIG_DIR"])
    assert gh_dir == tmp_path / "home" / "runs" / "claude" / "11-gh" and gh_dir.is_dir() and not any(gh_dir.iterdir())
    assert not gh_dir.is_relative_to(wt)
    assert spec.env["GIT_TERMINAL_PROMPT"] == "0"
    assert (spec.env["GIT_CONFIG_COUNT"], spec.env["GIT_CONFIG_KEY_0"], spec.env["GIT_CONFIG_VALUE_0"]) == ("1", "credential.helper", "")
    rec = json.loads((tmp_path / "home" / "runs" / "claude" / "11.json").read_text())
    assert rec["pid"] == out["pid"] and rec["deadline"] == "2026-09-03T12:20:00Z" and rec["branch"] == out["branch"] and rec["lease_comment"] == 310 and rec["kind"] == "build"
    lease = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"][0]
    assert lease.startswith("[claude:pm] LEASE #11\n") and "until: 2026-09-03T12:30:00Z" in lease
    assert posted(fake, 11)[0].startswith("Meld: `claude` dispatched a `backend` worker")
    wait_for_exit(out["pid"])
    assert sh(["git", "log", "-1", "--format=%an <%ae>"], wt) == "meld-claude-bot <7+meld-claude-bot@users.noreply.github.com>"
    assert (wt / ".meld-result.json").exists()


def test_dispatch_reuses_an_existing_worktree(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    first = dispatch(ctx, 11, adapter=FakeAdapter(), now=NOW)
    wait_for_exit(first["pid"])
    # pretend the first run was collected so a second dispatch is legal
    rec_path = tmp_path / "home" / "runs" / "claude" / "11.json"
    rec = json.loads(rec_path.read_text())
    rec["collected"] = True
    rec_path.write_text(json.dumps(rec))
    second = dispatch(ctx, 11, adapter=FakeAdapter(), now=NOW)
    assert second["worktree_mode"] == "reused" and second["worktree"] == first["worktree"]
    wait_for_exit(second["pid"])


def test_dispatch_refusals(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    with pytest.raises(Refused) as ei:
        dispatch(ctx, 11, adapter=FakeAdapter(), now=NOW)  # issue 11 is ready, not in-progress
    assert ei.value.refusal.rule == "issue is ready, not in-progress" and ei.value.violation_posted is True
    with pytest.raises(Refused) as ei:
        dispatch(ctx, 12, adapter=FakeAdapter(), now=NOW)  # challenged in the fixtures
    assert ei.value.refusal.rule == "issue has an open CHALLENGE"
    fake2, orch, *_ = make_product(tmp_path / "b", role="orchestrator")
    claim_issue_11(fake2)
    with pytest.raises(Refused) as ei:
        dispatch(orch, 11, adapter=FakeAdapter(), now=NOW)
    assert ei.value.refusal.rule == "only the PM may dispatch"
    with pytest.raises(DispatchError, match="kind"):
        dispatch(ctx, 11, kind="bogus", adapter=FakeAdapter(), now=NOW)


def test_dispatch_refuses_worker_type_missing_from_roster(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    issues = claim_issue_11(fake)
    for i in issues:
        if i["number"] == 11:
            i["labels"] = [l for l in i["labels"] if not l["name"].startswith("worker:")] + [{"name": "worker:marketing"}]
    fake.route("GET", R + "issues", issues)
    with pytest.raises(Refused) as ei:
        dispatch(ctx, 11, adapter=FakeAdapter(), now=NOW)
    assert "not in claude's roster" in ei.value.refusal.rule


def codex_claims_13(fake, issues):
    """Turn fixture #13 (in-review, assigned to meld-codex-bot) into codex's in-progress claim on a codex model."""
    for i in issues:
        if i["number"] == 13:
            i["labels"] = [l for l in i["labels"] if not l["name"].startswith(("meld:in-review", "model:"))]
            i["labels"] += [{"name": "meld:in-progress"}, {"name": "model:gpt-5.6-terra"}]
    fake.route("GET", R + "issues", issues)
    return issues


def test_two_groups_commit_as_their_own_bots_without_touching_the_clone_config(tmp_path):
    fake, claude, repo, origin = make_product(tmp_path)
    codex_claims_13(fake, claim_issue_11(fake))
    codex = load_context(repo / ".meld.toml", env=env_for(tmp_path, group="codex", role="pm"), run=fake, adapter_for=no_skills)
    before = (sh(["git", "config", "user.name"], repo), sh(["git", "config", "user.email"], repo))
    a = dispatch(claude, 11, adapter=FakeAdapter(script=DONE_SCRIPT), now=NOW)
    b = dispatch(codex, 13, adapter=FakeAdapter(script=DONE_SCRIPT), now=NOW)
    wait_for_exit(a["pid"])
    wait_for_exit(b["pid"])
    fmt = "--format=%an <%ae> / %cn <%ce>"
    assert sh(["git", "log", "-1", fmt], Path(a["worktree"])) == ("meld-claude-bot <7+meld-claude-bot@users.noreply.github.com> / "
                                                                  "meld-claude-bot <7+meld-claude-bot@users.noreply.github.com>")
    assert sh(["git", "log", "-1", fmt], Path(b["worktree"])) == ("meld-codex-bot <8+meld-codex-bot@users.noreply.github.com> / "
                                                                  "meld-codex-bot <8+meld-codex-bot@users.noreply.github.com>")
    # a linked worktree shares the clone's .git/config: neither dispatch may have written an identity there
    assert (sh(["git", "config", "user.name"], repo), sh(["git", "config", "user.email"], repo)) == before == ("Fixture", "fixture@example.com")
    for wt in (a["worktree"], b["worktree"]):
        assert sh(["git", "config", "--show-origin", "user.name"], Path(wt)).split("\t")[0].endswith("repo/.git/config")


def test_worker_env_appends_to_an_existing_git_config_list(tmp_path):
    base = {"PATH": "/bin", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.autocrlf", "GIT_CONFIG_VALUE_0": "false", "GH_TOKEN": "x"}
    env = worker_env(base, identity=("Bot", "1+bot@users.noreply.github.com"), gh_config_dir=tmp_path / "gh")
    assert env["GIT_CONFIG_COUNT"] == "2" and (env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]) == ("core.autocrlf", "false")
    assert (env["GIT_CONFIG_KEY_1"], env["GIT_CONFIG_VALUE_1"]) == ("credential.helper", "")
    assert "GH_TOKEN" not in env and env["MELD_WORKER"] == "1" and env["GH_CONFIG_DIR"] == str(tmp_path / "gh")
    bogus = worker_env({"GIT_CONFIG_COUNT": "many"}, identity=("Bot", "b@e"), gh_config_dir=tmp_path / "gh")
    assert bogus["GIT_CONFIG_COUNT"] == "1" and bogus["GIT_CONFIG_KEY_0"] == "credential.helper"


def test_git_identity_prefers_config_then_noreply(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    assert git_identity(ctx, ctx.cfg.groups["claude"]) == ("meld-claude-bot", "7+meld-claude-bot@users.noreply.github.com")
    from dataclasses import replace
    g = replace(ctx.cfg.groups["claude"], git_name="Meld Claude", git_email="me@example.com")
    assert git_identity(ctx, g) == ("Meld Claude", "me@example.com")


def test_dispatch_review_checks_out_pr_head(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    # the fixture PR #20 lives on meld/13-ci-pipeline; publish that branch to the fake origin
    sh(["git", "checkout", "-q", "-b", "meld/13-ci-pipeline"], repo)
    (repo / "ci.yml").write_text("ci\n")
    sh(["git", "add", "ci.yml"], repo)
    sh(["git", "commit", "-q", "-m", "ci"], repo)
    sh(["git", "push", "-q", "origin", "meld/13-ci-pipeline"], repo)
    sh(["git", "checkout", "-q", "main"], repo)
    expected = sh(["git", "rev-parse", "meld/13-ci-pipeline"], repo)
    adapter = FakeAdapter()
    out = dispatch(ctx, 20, kind="review", adapter=adapter, now=NOW)
    wt = Path(out["worktree"])
    assert wt.name == "review-20" and head_sha(wt) == expected and out["lease_until"] is None
    assert (wt / BRIEF_FILE).read_text().startswith("# Meld brief: reviewer for PR #20")
    spec = adapter.launches[0]
    assert (spec.model, spec.effort) == ("claude-opus-5", "high")  # the group's orchestrator RoleConfig
    rec = json.loads((tmp_path / "home" / "runs" / "claude" / "20.json").read_text())
    assert rec["kind"] == "review" and rec["pr"] == 20 and rec["lease_comment"] is None
    assert posted(fake, 20)[0].startswith("Meld: `claude` dispatched a `reviewer` worker")
    wait_for_exit(out["pid"])
    # codex may not review its own head (PR #20's last commit is by meld-codex-bot)
    fake2, codex, *_ = make_product(tmp_path / "c", group="codex")
    with pytest.raises(Refused, match="cannot review"):
        dispatch(codex, 20, kind="review", adapter=FakeAdapter(), now=NOW)
