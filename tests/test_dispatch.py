import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

import meld.cli as cli
import meld.dispatch as dispatch_module
from meld.actions import Refused
from meld.adapters.claude import ClaudeAdapter
from meld.adapters.codex import ADAPTER as CODEX
from meld.brief import BRIEF_FILE
from meld.context import load_context
from meld.dispatch import DispatchError, dispatch, git_identity, materialise_skills, render_tail, worker_env
from meld.gitops import head_sha, worktree_exists
from meld.roster import WorkerType
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
    gitconfig = Path(spec.env["GIT_CONFIG_GLOBAL"])
    assert gitconfig == tmp_path / "home" / "runs" / "claude" / "11-gitconfig" and gitconfig.read_text() == "" and spec.env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert spec.env["GIT_CONFIG_COUNT"] == "2"
    assert [(spec.env[f"GIT_CONFIG_KEY_{i}"], spec.env[f"GIT_CONFIG_VALUE_{i}"]) for i in range(2)] == \
        [("credential.helper", ""), ("commit.gpgsign", "false")]
    rec = json.loads((tmp_path / "home" / "runs" / "claude" / "11.json").read_text())
    assert rec["pid"] == out["pid"] and rec["deadline"] == "2026-09-03T12:20:00Z" and rec["branch"] == out["branch"] and rec["lease_comment"] == 310 and rec["kind"] == "build"
    assert rec["worker"] == "backend"
    lease = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"][0]
    assert lease.startswith("[claude:pm] LEASE #11\n") and "until: 2026-09-03T12:30:00Z" in lease
    assert posted(fake, 11)[0].startswith("Meld: `claude` dispatched a `backend` worker")
    wait_for_exit(out["pid"])
    assert sh(["git", "log", "-1", "--format=%an <%ae>"], wt) == "meld-claude-bot <7+meld-claude-bot@users.noreply.github.com>"
    assert (wt / ".meld-result.json").exists()
    # a bare substring check would false-positive: the worker contract's own item 10 names the heading in backticks
    # ("`## Skills available`") even when the section itself is absent, so this checks for the rendered heading line.
    assert "\n## Skills available\n" not in (wt / BRIEF_FILE).read_text()
    # a group whose adapter reports an installed skill gets a "## Skills available" section in its brief. Skills reach
    # render_brief through ctx.roster_entries() (bound to the context's own adapter_for), not the adapter= override
    # dispatch() takes purely to stand in for the real launch -- so the skill is injected via a fresh context's
    # adapter_for, the same mechanism launch_role/role_prompt use to pick up installed skills.
    # Description shares "health" with issue 11's title ("Add health endpoint") so it survives select_skills'
    # relevance filter -- an unmatched, unpinned skill is now left out of the brief entirely (task 8).
    rec_path = tmp_path / "home" / "runs" / "claude" / "11.json"
    rec = json.loads(rec_path.read_text())
    rec["collected"] = True
    rec_path.write_text(json.dumps(rec))
    skilled = load_context(repo / ".meld.toml", env=env_for(tmp_path, group="claude", role="pm"), run=fake,
                           adapter_for=lambda name: FakeAdapter(skills=[WorkerType("brand", "Brand voice for the health team.", "skill", None, "")]))
    second = dispatch(skilled, 11, adapter=FakeAdapter(), now=NOW)
    text2 = (Path(second["worktree"]) / BRIEF_FILE).read_text()
    assert "## Skills available" in text2 and "- `brand`: Brand voice for the health team." in text2
    wait_for_exit(second["pid"])


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


def test_dispatch_refreshes_origin_before_recall_on_a_reused_worktree(tmp_path):
    # A reused build worktree skips create_worktree entirely (see _dispatch_build's `mode` ternary), so it never
    # fetches on its own; recall must still see what landed on origin's default branch since the first dispatch.
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    first = dispatch(ctx, 11, adapter=FakeAdapter(), now=NOW)
    wait_for_exit(first["pid"])
    # pretend the first run was collected so a second dispatch is legal (same trick as test_dispatch_reuses_an_existing_worktree)
    rec_path = tmp_path / "home" / "runs" / "claude" / "11.json"
    rec = json.loads(rec_path.read_text())
    rec["collected"] = True
    rec_path.write_text(json.dumps(rec))
    # A second clone pushes a new decision straight to origin's default branch; repo's own checkout never pulls it.
    second_clone = tmp_path / "second-clone"
    sh(["git", "clone", "-q", str(origin), str(second_clone)], tmp_path)
    sh(["git", "config", "user.name", "Fixture"], second_clone)
    sh(["git", "config", "user.email", "fixture@example.com"], second_clone)
    (second_clone / ".meld" / "decisions").mkdir(parents=True)
    (second_clone / ".meld" / "decisions" / "0001-health.md").write_text(
        "# Decision health-1\n\n## Decision\n\nHealth endpoints must never touch the database.\n")
    sh(["git", "add", ".meld"], second_clone)
    sh(["git", "commit", "-q", "-m", "record decision"], second_clone)
    sh(["git", "push", "-q", "origin", "main"], second_clone)
    second = dispatch(ctx, 11, adapter=FakeAdapter(), now=NOW)
    assert second["worktree_mode"] == "reused" and "recall_note" not in second
    text2 = (Path(second["worktree"]) / BRIEF_FILE).read_text()
    assert "## Relevant memory" in text2 and "Health endpoints must never touch the database." in text2
    wait_for_exit(second["pid"])


def test_dispatch_falls_back_to_the_working_copy_when_the_origin_fetch_fails(tmp_path, monkeypatch):
    from meld.gitops import GitError
    from meld.gitops import git as real_git
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    # Seeded straight into the working copy, never committed or pushed -- reachable only via _recall_for_brief's
    # ref=None fallback, never via ref="origin/main", so finding it in the brief proves the fallback path ran.
    (repo / ".meld" / "memory").mkdir(parents=True)
    (repo / ".meld" / "memory" / "lessons.md").write_text("# Lessons\n\n## 2026-09-01 — #7 (codex)\n\nHealth endpoints must not touch the database.\n")

    def failing_fetch(args, cwd, *a, **kw):
        # meld.dispatch.git has exactly two direct callers: _tracked_in_worktree's `ls-files` (unaffected here --
        # this dispatch has no product skills to materialise) and _recall_for_brief's `fetch`, which is the one
        # this test needs to fail. create_worktree's own earlier fetch runs through gitops.py's own `git` name,
        # a separate binding this patch never touches, so the worktree itself is still created normally.
        if args[:1] == ["fetch"]:
            raise GitError("simulated offline")
        return real_git(args, cwd, *a, **kw)

    monkeypatch.setattr("meld.dispatch.git", failing_fetch)
    out = dispatch(ctx, 11, adapter=FakeAdapter(), now=NOW)
    assert out["dispatched"] == 11 and out["recall_note"] == "origin fetch failed; recall used the local checkout"
    text = (Path(out["worktree"]) / BRIEF_FILE).read_text()
    assert "## Relevant memory" in text and "Health endpoints must not touch the database." in text
    wait_for_exit(out["pid"])


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
    env = worker_env(base, identity=("Bot", "1+bot@users.noreply.github.com"), gh_config_dir=tmp_path / "gh", git_config_global=tmp_path / "gc")
    assert env["GIT_CONFIG_COUNT"] == "3" and (env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]) == ("core.autocrlf", "false")
    assert [(env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"]) for i in (1, 2)] == \
        [("credential.helper", ""), ("commit.gpgsign", "false")]
    assert "GH_TOKEN" not in env and env["MELD_WORKER"] == "1" and env["GH_CONFIG_DIR"] == str(tmp_path / "gh")
    assert env["GIT_CONFIG_GLOBAL"] == str(tmp_path / "gc") and env["GIT_CONFIG_NOSYSTEM"] == "1"
    bogus = worker_env({"GIT_CONFIG_COUNT": "many"}, identity=("Bot", "b@e"), gh_config_dir=tmp_path / "gh", git_config_global=tmp_path / "gc")
    assert bogus["GIT_CONFIG_COUNT"] == "2" and bogus["GIT_CONFIG_KEY_0"] == "credential.helper"


def test_git_identity_prefers_config_then_noreply(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    assert git_identity(ctx, ctx.cfg.groups["claude"]) == ("meld-claude-bot", "7+meld-claude-bot@users.noreply.github.com")
    from dataclasses import replace
    g = replace(ctx.cfg.groups["claude"], git_name="Meld Claude", git_email="me@example.com")
    assert git_identity(ctx, g) == ("Meld Claude", "me@example.com")


def test_dispatch_review_checks_out_pr_head(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    publish_pr_branch(repo)  # the fixture PR #20 lives on meld/13-ci-pipeline
    fake.route("GET", R + "pulls/20/reviews", [])   # board1's claude approval on abc123 would make this head already reviewed
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


def publish_pr_branch(repo):
    """Publish fixture PR #20's branch (meld/13-ci-pipeline) to the fake origin."""
    sh(["git", "checkout", "-q", "-b", "meld/13-ci-pipeline"], repo)
    (repo / "ci.yml").write_text("ci\n")
    sh(["git", "add", "ci.yml"], repo)
    sh(["git", "commit", "-q", "-m", "ci"], repo)
    sh(["git", "push", "-q", "origin", "meld/13-ci-pipeline"], repo)
    sh(["git", "checkout", "-q", "main"], repo)


def mark_collected(tmp_path, number):
    rec_path = tmp_path / "home" / "runs" / "claude" / f"{number}.json"
    rec = json.loads(rec_path.read_text())
    rec["collected"] = True
    rec_path.write_text(json.dumps(rec))


def test_dispatch_recovers_after_the_worktrees_directory_is_deleted(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    publish_pr_branch(repo)
    fake.route("GET", R + "pulls/20/reviews", [])   # board1's claude approval on abc123 would make this head already reviewed
    first = dispatch(ctx, 11, adapter=FakeAdapter(), now=NOW)
    review = dispatch(ctx, 20, kind="review", adapter=FakeAdapter(), now=NOW)
    wait_for_exit(first["pid"])
    wait_for_exit(review["pid"])
    mark_collected(tmp_path, 11)
    mark_collected(tmp_path, 20)
    shutil.rmtree(tmp_path / "home" / "worktrees")  # disposable state, per spec section 18
    second = dispatch(ctx, 11, adapter=FakeAdapter(), now=NOW)
    assert second["worktree_mode"] == "new" and worktree_exists(repo, Path(second["worktree"]))
    again = dispatch(ctx, 20, kind="review", adapter=FakeAdapter(), now=NOW)
    assert worktree_exists(repo, Path(again["worktree"]))
    wait_for_exit(second["pid"])
    wait_for_exit(again["pid"])


def test_dispatch_review_is_refused_under_halt(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    comments = fixture("comments_10.json") + [{"id": 130, "user": {"login": "empowerScott"}, "created_at": "2026-09-03T09:40:00Z",
                                               "body": "[human] HALT\n\nstop everything"}]
    fake.route("GET", R + "issues/10/comments", comments)
    with pytest.raises(Refused) as ei:
        dispatch(ctx, 20, kind="review", adapter=FakeAdapter(), now=NOW)
    assert ei.value.refusal.rule == "cannot review: HALT is in effect" and ei.value.violation_posted is True
    assert not (tmp_path / "home" / "runs" / "claude" / "20.json").exists()


def test_dispatch_security_reviewer_and_worker_validation(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    publish_pr_branch(repo)
    fake.route("GET", R + "pulls/20/reviews", [])   # board1's claude approval on abc123 would make this head already reviewed
    adapter = FakeAdapter()
    out = dispatch(ctx, 20, kind="review", worker="security", adapter=adapter, now=NOW)
    assert (Path(out["worktree"]) / BRIEF_FILE).read_text().startswith("# Meld brief: security for PR #20")
    assert posted(fake, 20)[-1].startswith("Meld: `claude` dispatched a `security` worker")
    assert json.loads((tmp_path / "home" / "runs" / "claude" / "20.json").read_text())["worker"] == "security"
    wait_for_exit(out["pid"])
    mark_collected(tmp_path, 20)
    with pytest.raises(Refused, match="worker type 'backend' cannot run a review; use one of: reviewer, security"):
        dispatch(ctx, 20, kind="review", worker="backend", adapter=FakeAdapter(), now=NOW)
    with pytest.raises(DispatchError, match="--worker applies to review runs only"):
        dispatch(ctx, 11, worker="security", adapter=FakeAdapter(), now=NOW)


def test_dispatch_audit_checks_out_the_merge_commit(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)          # claude audits codex's merged PR #20
    publish_pr_branch(repo)
    sh(["git", "merge", "-q", "--no-ff", "-m", "CI pipeline (#20)", "meld/13-ci-pipeline"], repo)
    merge_sha = sh(["git", "rev-parse", "HEAD"], repo)
    sh(["git", "push", "-q", "origin", "main"], repo)
    pulls = fixture("pulls.json")
    for p in pulls:
        if p["number"] == 20:
            p.update({"state": "closed", "merged_at": "2026-09-03T11:00:00Z", "merge_commit_sha": merge_sha})
    fake.route("GET", R + "pulls", pulls)
    out = dispatch(ctx, 20, kind="audit", adapter=FakeAdapter(), now=NOW)
    wt = Path(out["worktree"])
    assert head_sha(wt) == merge_sha and out["branch"] == "meld/13-ci-pipeline"
    assert f"The checkout is the merge commit `{merge_sha[:7]}`" in (wt / BRIEF_FILE).read_text()
    assert posted(fake, 20)[-1].startswith("Meld: `claude` dispatched an `auditor` worker") and f"merge commit `{merge_sha[:7]}`" in posted(fake, 20)[-1]
    wait_for_exit(out["pid"])


def test_dispatch_audit_refusals_come_from_the_rules(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    with pytest.raises(Refused, match="cannot audit: audit targets a merged PR"):
        dispatch(ctx, 20, kind="audit", adapter=FakeAdapter(), now=NOW)          # PR #20 is open in the fixture
    with pytest.raises(Refused, match="cannot audit: cannot audit a PR authored by own group"):
        dispatch(ctx, 19, kind="audit", adapter=FakeAdapter(), now=NOW)          # PR #19 was merged by claude
    fake2, codex, repo2, _ = make_product(tmp_path / "codex", group="codex")
    publish_pr_branch(repo2)
    sh(["git", "merge", "-q", "--no-ff", "-m", "Old merged (#19)", "meld/13-ci-pipeline"], repo2)
    sh(["git", "push", "-q", "origin", "main"], repo2)
    with pytest.raises(Refused, match="worker type 'reviewer' cannot run an audit; use one of: auditor"):
        dispatch(codex, 19, kind="audit", worker="reviewer", adapter=FakeAdapter(), now=NOW)


def test_missing_cli_binary_is_a_clean_dispatch_error(tmp_path, monkeypatch):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    absent = ClaudeAdapter(binary=str(tmp_path / "no-such-claude"), home=tmp_path / "no-such-home")
    posts_before = len([c for c in fake.calls if c.method == "POST"])
    with pytest.raises(DispatchError, match="cannot start claude: .*no-such-claude"):
        dispatch(ctx, 11, adapter=absent, now=NOW)
    assert not (tmp_path / "home" / "runs" / "claude" / "11.json").exists()    # no run record left behind
    assert len([c for c in fake.calls if c.method == "POST"]) == posts_before    # no lease, no start comment
    monkeypatch.setattr(cli, "RUN", fake)
    monkeypatch.setattr(cli, "ADAPTER_FOR", lambda name: absent)
    result = CliRunner().invoke(cli.main, ["--config", str(repo / ".meld.toml"), "--compact", "dispatch", "11"], env=env_for(tmp_path))
    assert result.exit_code == 1 and "Traceback" not in result.output and "cannot start claude" in result.output


def test_dispatch_renders_relevant_memory_into_the_brief(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    # dispatch's brief now reads recall_snippets at origin/<default> (F3-style), not repo's own working copy, so
    # the seeded lesson must be committed and pushed here for the brief to pick it up.
    (repo / ".meld" / "memory").mkdir(parents=True)
    (repo / ".meld" / "memory" / "lessons.md").write_text("# Lessons\n\n## 2026-09-01 — #7 (codex)\n\nHealth endpoints must not touch the database.\n")
    sh(["git", "add", ".meld"], repo)
    sh(["git", "commit", "-q", "-m", "seed memory"], repo)
    sh(["git", "push", "-q", "origin", "main"], repo)
    claim_issue_11(fake)
    out = dispatch(ctx, 11, adapter=FakeAdapter(), now=NOW)
    text = (Path(out["worktree"]) / BRIEF_FILE).read_text()
    assert "## Relevant memory" in text and "must not touch the database" in text


def test_dispatch_build_brief_lists_pinned_and_matched_skills_only(tmp_path):
    """Issue 11's title/body ("Add health endpoint" / "GET /healthz returns 200") is unrelated to PDFs, but a
    skill:pdf label pins `pdf` into the brief regardless; an installed-but-unrelated skill (stripe-billing) is
    left out because select_skills only keeps pins plus task-text matches. Skills reach the brief through
    ctx.roster_entries() (ctx.adapter_for), not the `adapter=` dispatch() takes for the launch -- see the
    happy-path test above -- so the skill roster is injected the same way, via ctx.adapter_for + refresh_roster()."""
    fake, ctx, repo, origin = make_product(tmp_path)
    issues = claim_issue_11(fake)
    for i in issues:
        if i["number"] == 11:
            i["labels"].append({"name": "skill:pdf"})
    fake.route("GET", R + "issues", issues)
    ctx.adapter_for = lambda name: FakeAdapter(skills=[WorkerType("pdf", "Read and write PDF files", "skill", None),
                                                       WorkerType("stripe-billing", "Stripe payments and invoices", "skill", None)])
    ctx.refresh_roster()
    out = dispatch(ctx, 11, adapter=FakeAdapter(), now=NOW)
    text = (Path(out["worktree"]) / BRIEF_FILE).read_text()
    assert "## Skills available" in text and "- `pdf`: Read and write PDF files" in text
    assert "stripe-billing" not in text
    wait_for_exit(out["pid"])


def test_dispatch_materialises_product_skills_into_the_adapters_project_dir(tmp_path):
    """A product skill under .meld/skills/<name>/ is copied into the worktree under the launch adapter's own
    project skill directory (FakeAdapter's is `.fake/skills`) and excluded from git via info/exclude, so a
    CLI that discovers project skills natively finds it without the worker ever being able to commit the copy."""
    fake, ctx, repo, origin = make_product(tmp_path)
    d = repo / ".meld" / "skills" / "deploy-checklist"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: deploy-checklist\ndescription: Pre-deploy steps\n---\n# Deploy\n")
    sh(["git", "add", "."], repo)
    sh(["git", "commit", "-q", "-m", "skills"], repo)
    sh(["git", "push", "-q", "origin", "main"], repo)
    issues = claim_issue_11(fake)
    for i in issues:
        # Issue 11's own title/body ("Add health endpoint" / "GET /healthz returns 200") shares no term with this
        # skill's description, so pin it explicitly -- the same `skill:<name>` label mechanism as task 8's dispatch test.
        if i["number"] == 11:
            i["labels"].append({"name": "skill:deploy-checklist"})
    fake.route("GET", R + "issues", issues)
    out = dispatch(ctx, 11, adapter=FakeAdapter())
    wt = Path(out["worktree"])
    assert (wt / ".fake" / "skills" / "deploy-checklist" / "SKILL.md").read_text().startswith("---")
    exclude = sh(["git", "rev-parse", "--git-path", "info/exclude"], wt)
    exclude_text = Path(exclude if exclude.startswith("/") else str(wt / exclude)).read_text()
    # Per materialised path, never the directory itself: info/exclude is shared with the whole clone (a linked
    # worktree has no private one), so a `.fake/skills/` line would hide the directory in the PM's own checkout
    # and in every other worktree -- including a worker's brand-new, deliberately-committed project skill.
    lines = exclude_text.splitlines()
    assert ".fake/skills/deploy-checklist/" in lines and ".fake/skills/.meld-materialised.json" in lines
    assert ".fake/skills/" not in lines
    # The brief names the copy in this worktree (worker contract item 10), never the PM checkout's own file.
    brief_text = (wt / ".meld-brief.md").read_text()
    assert f"- `deploy-checklist` ({wt / '.fake/skills/deploy-checklist/SKILL.md'}): Pre-deploy steps" in brief_text
    assert str(repo / ".meld" / "skills") not in brief_text
    assert out["skills_materialised"] == {"made": [".fake/skills/deploy-checklist"], "skipped": []}
    manifest = json.loads((wt / ".fake" / "skills" / ".meld-materialised.json").read_text())
    assert manifest == ["deploy-checklist"]
    wait_for_exit(out["pid"])


def test_materialising_a_skill_does_not_hide_anything_else_under_the_project_skills_directory(tmp_path):
    """Important 1: `info/exclude` belongs to the whole clone, not to one worktree (`git rev-parse --git-path
    info/exclude` resolves to the shared `.git/info/exclude` from a linked worktree too). Excluding the skills
    directory itself would therefore make a worker's own new project skill invisible to `git add -A`, in this
    worktree and in the PM's checkout alike -- only the paths meld actually wrote may be excluded."""
    fake, ctx, repo, origin = make_product(tmp_path)
    d = repo / ".meld" / "skills" / "deploy-checklist"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: deploy-checklist\ndescription: Pre-deploy steps\n---\n# Deploy\n")
    sh(["git", "add", "."], repo)
    sh(["git", "commit", "-q", "-m", "skills"], repo)
    sh(["git", "push", "-q", "origin", "main"], repo)
    issues = claim_issue_11(fake)
    for i in issues:
        if i["number"] == 11:
            i["labels"].append({"name": "skill:deploy-checklist"})
    fake.route("GET", R + "issues", issues)
    out = dispatch(ctx, 11, adapter=FakeAdapter())
    wt = Path(out["worktree"])
    # A skill the worker itself authors, right next to the materialised copy, is the product's file to commit.
    new = wt / ".fake" / "skills" / "worker-authored"
    new.mkdir(parents=True, exist_ok=True)
    (new / "SKILL.md").write_text("---\nname: worker-authored\ndescription: Written by the worker\n---\n")
    status = sh(["git", "status", "--porcelain", "-uall"], wt)     # -uall: per file, not the collapsed `?? .fake/`
    assert ".fake/skills/worker-authored/SKILL.md" in status
    assert ".fake/skills/deploy-checklist" not in status              # meld's own copy stays hidden
    assert ".meld-materialised.json" not in status
    # And the PM's own checkout, which shares that exclude file, is equally unaffected.
    pm_side = repo / ".fake" / "skills" / "human-authored"
    pm_side.mkdir(parents=True, exist_ok=True)
    (pm_side / "SKILL.md").write_text("---\nname: human-authored\ndescription: Written by the human\n---\n")
    assert ".fake/skills/human-authored/SKILL.md" in sh(["git", "status", "--porcelain", "-uall"], repo)
    wait_for_exit(out["pid"])


def test_dispatch_prunes_a_stale_skill_copy_on_a_reused_worktree(tmp_path):
    """A skill copied on an earlier dispatch into this same worktree, then removed from .meld/skills/, does not
    linger once a later dispatch reuses the worktree: only a name materialise_skills itself wrote to the
    manifest is ever pruned, and the manifest is kept current with what is actually there now."""
    fake, ctx, repo, origin = make_product(tmp_path)
    a = repo / ".meld" / "skills" / "skill-a"
    a.mkdir(parents=True)
    (a / "SKILL.md").write_text("---\nname: skill-a\ndescription: First skill\n---\n# A\n")
    sh(["git", "add", "."], repo)
    sh(["git", "commit", "-q", "-m", "skill a"], repo)
    sh(["git", "push", "-q", "origin", "main"], repo)
    issues = claim_issue_11(fake)
    for i in issues:
        if i["number"] == 11:
            i["labels"].append({"name": "skill:skill-a"})
    fake.route("GET", R + "issues", issues)
    first = dispatch(ctx, 11, adapter=FakeAdapter())
    wait_for_exit(first["pid"])
    wt = Path(first["worktree"])
    assert (wt / ".fake" / "skills" / "skill-a" / "SKILL.md").exists()
    assert first["skills_materialised"] == {"made": [".fake/skills/skill-a"], "skipped": []}
    mark_collected(tmp_path, 11)  # the first run must be collected before a second dispatch on #11 is legal
    shutil.rmtree(a)
    b = repo / ".meld" / "skills" / "skill-b"
    b.mkdir(parents=True)
    (b / "SKILL.md").write_text("---\nname: skill-b\ndescription: Second skill\n---\n# B\n")
    sh(["git", "add", "-A"], repo)
    sh(["git", "commit", "-q", "-m", "swap skill-a for skill-b"], repo)
    sh(["git", "push", "-q", "origin", "main"], repo)
    issues2 = claim_issue_11(fake)
    for i in issues2:
        if i["number"] == 11:
            i["labels"].append({"name": "skill:skill-b"})
    fake.route("GET", R + "issues", issues2)
    ctx.refresh_roster()  # the roster was cached by the first dispatch; it must re-scan .meld/skills/ to see the swap
    second = dispatch(ctx, 11, adapter=FakeAdapter())
    assert second["worktree"] == first["worktree"]
    assert not (wt / ".fake" / "skills" / "skill-a").exists()
    assert (wt / ".fake" / "skills" / "skill-b" / "SKILL.md").exists()
    assert second["skills_materialised"] == {"made": [".fake/skills/skill-b"], "skipped": []}
    manifest = json.loads((wt / ".fake" / "skills" / ".meld-materialised.json").read_text())
    assert manifest == ["skill-b"]
    wait_for_exit(second["pid"])


def test_dispatch_never_replaces_a_skill_destination_tracked_in_the_product_repo(tmp_path):
    """A file the product itself committed at the exact destination materialise_skills would otherwise write is
    the product's file, not meld's copy to make: it is left completely untouched and named in `skipped`."""
    fake, ctx, repo, origin = make_product(tmp_path)
    tracked = repo / ".fake" / "skills" / "deploy-checklist"
    tracked.mkdir(parents=True)
    (tracked / "SKILL.md").write_text("Committed by the product, not meld.\n")
    d = repo / ".meld" / "skills" / "deploy-checklist"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: deploy-checklist\ndescription: Pre-deploy steps\n---\n# Deploy\n")
    sh(["git", "add", "."], repo)
    sh(["git", "commit", "-q", "-m", "tracked skill destination plus the product skill"], repo)
    sh(["git", "push", "-q", "origin", "main"], repo)
    issues = claim_issue_11(fake)
    for i in issues:
        if i["number"] == 11:
            i["labels"].append({"name": "skill:deploy-checklist"})
    fake.route("GET", R + "issues", issues)
    out = dispatch(ctx, 11, adapter=FakeAdapter())
    wt = Path(out["worktree"])
    assert (wt / ".fake" / "skills" / "deploy-checklist" / "SKILL.md").read_text() == "Committed by the product, not meld.\n"
    assert out["skills_materialised"] == {"made": [],
                                          "skipped": [".fake/skills/deploy-checklist (tracked in the product repo; left alone)"]}
    assert not (wt / ".fake" / "skills" / ".meld-materialised.json").exists()
    wait_for_exit(out["pid"])


def test_materialise_skills_rejects_an_unsafe_skill_name(tmp_path):
    """C1: skill.name is free text (frontmatter, or in principle any caller) and is used as a path component --
    a hand-built WorkerType whose name is a traversal attempt must be skipped, never used to build a path, and
    must touch nothing that happens to sit at the path the traversal would otherwise have reached."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "x").mkdir()  # stands in for real product content two levels above target_dir (".fake/skills")
    (worktree / "x" / "keep.txt").write_text("do not delete me\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "SKILL.md").write_text("---\nname: y\n---\n")
    bad = WorkerType(name="../../x", description="", source="product-skill", path=elsewhere / "SKILL.md")
    out = materialise_skills(worktree, FakeAdapter(), [bad])
    assert out == {"made": [], "skipped": ["../../x: not a valid skill name"]}
    assert (worktree / "x" / "keep.txt").read_text() == "do not delete me\n"


def test_materialise_skills_does_not_follow_a_symlink_out_of_the_skill_directory(tmp_path):
    """`shutil.copytree` copies a symlink's *target* by default, so a link inside a product skill directory
    would have its content written into the worktree, where a worker reads it and could commit it. Links are
    dropped from the copy; the skill's real files still land."""
    from tests.gitfixtures import make_origin_and_clone
    origin, worktree = make_origin_and_clone(tmp_path)
    secret = tmp_path / "id_rsa"
    secret.write_text("PRIVATE KEY MATERIAL\n")
    src = tmp_path / "skills" / "deploy-checklist"
    (src / "reference").mkdir(parents=True)
    (src / "SKILL.md").write_text("---\nname: deploy-checklist\ndescription: Pre-deploy steps\n---\n# Deploy\n")
    (src / "reference" / "steps.md").write_text("1. Check the dashboard\n")
    (src / "borrowed-key").symlink_to(secret)                        # a file link, beside the real files
    (src / "reference" / "elsewhere").symlink_to(tmp_path / "skills")  # and a directory link, one level down
    skill = WorkerType(name="deploy-checklist", description="", source="product-skill", path=src / "SKILL.md")
    out = materialise_skills(worktree, FakeAdapter(), [skill])
    assert out == {"made": [".fake/skills/deploy-checklist"], "skipped": []}
    dest = worktree / ".fake" / "skills" / "deploy-checklist"
    assert (dest / "SKILL.md").read_text().startswith("---") and (dest / "reference" / "steps.md").exists()
    assert not (dest / "borrowed-key").exists() and not (dest / "borrowed-key").is_symlink()
    assert not (dest / "reference" / "elsewhere").exists() and not (dest / "reference" / "elsewhere").is_symlink()
    copied = [f for f in dest.rglob("*") if f.is_file()]
    assert copied and all("PRIVATE KEY MATERIAL" not in f.read_text() for f in copied)


def test_dispatch_never_prunes_a_stale_copy_that_became_tracked(tmp_path):
    """C2: a name in the previous manifest that is no longer a current skill is normally pruned as stale -- but
    if the product has since committed a real file at that exact path on the worktree's own branch, it is the
    product's file now, and pruning must leave it alone (and say so), never delete it."""
    fake, ctx, repo, origin = make_product(tmp_path)
    a = repo / ".meld" / "skills" / "skill-a"
    a.mkdir(parents=True)
    (a / "SKILL.md").write_text("---\nname: skill-a\ndescription: First skill\n---\n# A\n")
    sh(["git", "add", "."], repo)
    sh(["git", "commit", "-q", "-m", "skill a"], repo)
    sh(["git", "push", "-q", "origin", "main"], repo)
    issues = claim_issue_11(fake)
    for i in issues:
        if i["number"] == 11:
            i["labels"].append({"name": "skill:skill-a"})
    fake.route("GET", R + "issues", issues)
    first = dispatch(ctx, 11, adapter=FakeAdapter())
    wait_for_exit(first["pid"])
    wt = Path(first["worktree"])
    assert (wt / ".fake" / "skills" / "skill-a" / "SKILL.md").exists()
    mark_collected(tmp_path, 11)  # the first run must be collected before a second dispatch on #11 is legal
    # The product adopts this exact path inside the worktree's own branch: a real commit, force-added past the
    # info/exclude entry materialise_skills wrote for it -- this must survive the next dispatch's pruning pass.
    (wt / ".fake" / "skills" / "skill-a" / "SKILL.md").write_text("adopted by the product\n")
    sh(["git", "add", "-f", ".fake/skills/skill-a"], wt)
    sh(["git", "commit", "-q", "-m", "adopt skill-a"], wt)
    shutil.rmtree(a)  # skill-a is no longer a current product skill
    fake.route("GET", R + "issues", claim_issue_11(fake))
    ctx.refresh_roster()  # the roster was cached by the first dispatch; it must re-scan .meld/skills/ to see the removal
    second = dispatch(ctx, 11, adapter=FakeAdapter())
    assert (wt / ".fake" / "skills" / "skill-a" / "SKILL.md").read_text() == "adopted by the product\n"
    assert second["skills_materialised"] == {"made": [],
                                             "skipped": [".fake/skills/skill-a (tracked in the product repo; left alone)"]}
    manifest = json.loads((wt / ".fake" / "skills" / ".meld-materialised.json").read_text())
    assert manifest == []
    wait_for_exit(second["pid"])


def test_dispatch_survives_a_tracked_file_blocking_the_project_skills_directory(tmp_path):
    """I4: if the product has committed a plain FILE at the adapter's project_skills_dir()'s first path
    component (here FakeAdapter's `.fake`), materialise_skills must not crash the dispatch trying to create a
    directory where a file already sits -- it reports the collision and makes nothing, and the dispatch as a
    whole still succeeds (a missing skill copy must never fail a dispatch)."""
    fake, ctx, repo, origin = make_product(tmp_path)
    (repo / ".fake").write_text("not a directory\n")
    d = repo / ".meld" / "skills" / "deploy-checklist"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: deploy-checklist\ndescription: Pre-deploy steps\n---\n# Deploy\n")
    sh(["git", "add", "."], repo)
    sh(["git", "commit", "-q", "-m", "a file named .fake plus the product skill"], repo)
    sh(["git", "push", "-q", "origin", "main"], repo)
    issues = claim_issue_11(fake)
    for i in issues:
        if i["number"] == 11:
            i["labels"].append({"name": "skill:deploy-checklist"})
    fake.route("GET", R + "issues", issues)
    out = dispatch(ctx, 11, adapter=FakeAdapter())
    wt = Path(out["worktree"])
    assert (wt / ".fake").is_file()
    assert out["skills_materialised"] == {"made": [], "skipped": [".fake/skills is a file in this repo; skills not materialised"]}
    wait_for_exit(out["pid"])


# A separate ISO string, not the module's `NOW` datetime: render_tail's `now` parameter is the string
# `_iso(when)` produces at the call site, not a `datetime` -- these tests exercise it directly.
TAIL_NOW = "2026-09-12T02:11:19Z"


def test_render_tail_renders_events_rather_than_json(tmp_path):
    log = tmp_path / "audit-14.log"
    log.write_text("\n".join([
        '{"type": "thread.started", "thread_id": "t1"}',
        '{"type": "item.completed", "item": {"type": "agent_message", "text": "Running the suite."}}',
        '{"type": "item.completed", "item": {"id": "i1", "type": "command_execution", '
        '"command": "uv run pytest -q", "aggregated_output": "1 failed", "exit_code": 1}}',
    ]) + "\n")
    rendered = render_tail(log, CODEX, lines=50, now=TAIL_NOW)
    assert "message  Running the suite." in rendered
    assert "result   FAILED · 1 failed" in rendered
    assert "thread.started" not in rendered          # the JSON itself never reaches the comment


def test_render_tail_keeps_only_the_last_n_events(tmp_path):
    log = tmp_path / "audit-14.log"
    log.write_text("".join(
        '{"type": "item.completed", "item": {"type": "agent_message", "text": "line %d"}}\n' % i
        for i in range(10)))
    rendered = render_tail(log, CODEX, lines=3, now=TAIL_NOW)
    assert rendered.count("\n") == 2
    assert "line 9" in rendered and "line 6" not in rendered


def test_render_tail_keeps_the_final_unterminated_line(tmp_path):
    """The one path this function serves is a worker that died without writing a result. A process killed
    mid-write leaves its last line with no newline, and that line is the one saying why the run died."""
    log = tmp_path / "audit-14.log"
    log.write_bytes(b'{"type": "item.completed", "item": {"type": "agent_message", "text": "working"}}\n'
                    b'Traceback (most recent call last): fatal, out of memory')
    rendered = render_tail(log, CODEX, lines=50, now=TAIL_NOW)
    assert "message  working" in rendered
    assert rendered.endswith("raw      Traceback (most recent call last): fatal, out of memory")


def test_render_tail_of_a_one_blob_log_with_no_trailing_newline_is_not_empty(tmp_path):
    """A pre-plan-8 Claude log is one JSON object with no trailing newline. It rendered as the empty string,
    so the posted comment was a header above an empty code fence."""
    log = tmp_path / "audit-14.log"
    log.write_bytes(b'{"type":"result","subtype":"success","is_error":false,"result":"PR 15 merged."}')
    assert "done     PR 15 merged." in render_tail(log, ClaudeAdapter(), lines=50, now=TAIL_NOW)


def test_render_tail_of_a_missing_or_empty_log_is_empty(tmp_path):
    assert render_tail(tmp_path / "absent.log", CODEX, lines=50, now=TAIL_NOW) == ""
    (tmp_path / "empty.log").write_text("")
    assert render_tail(tmp_path / "empty.log", CODEX, lines=50, now=TAIL_NOW) == ""


def test_render_tail_of_a_none_adapter_is_empty(tmp_path):
    """A human party with no group has no adapter (Context.adapter() returns None); render_tail degrades to
    empty rather than raising, the same as a missing log -- there is nothing meld can translate the log with."""
    log = tmp_path / "audit-14.log"
    log.write_text('{"type": "item.completed", "item": {"type": "agent_message", "text": "hi"}}\n')
    assert render_tail(log, None, lines=50, now=TAIL_NOW) == ""


def test_dispatch_records_the_process_start_time_beside_the_pid(tmp_path, monkeypatch):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    monkeypatch.setattr(dispatch_module, "proc_start_of", lambda pid: f"started-{pid}")
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=DONE_SCRIPT), now=NOW)
    rec = json.loads((tmp_path / "home" / "runs" / "claude" / "11.json").read_text())
    assert rec["proc_start"] == f"started-{out['pid']}"


def test_dispatch_fast_forwards_a_reused_worktree_to_what_a_person_pushed(tmp_path):
    """Handoff residual 10: a reused worktree never fetched, so a retry built over a person's push to the PR branch and
    its own push was then refused. The worktree now fast-forwards to origin/<branch> when it is strictly behind, and
    reports "current" when it is not."""
    from meld.dispatch import collect
    from tests.test_collect import routes_for_labels
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    routes_for_labels(fake, 11)
    fake.route("POST", R + "pulls", {"number": 21, "html_url": "https://github.com/acme/widgets/pull/21"})
    first = dispatch(ctx, 11, adapter=FakeAdapter(script=DONE_SCRIPT), now=NOW)
    wait_for_exit(first["pid"])
    assert collect(ctx, 11, now=NOW)["outcome"] == "done"          # pushes the branch and opens PR #21
    branch = "meld/11-add-health-endpoint"
    person = tmp_path / "person"
    sh(["git", "clone", "-q", "-b", branch, str(origin), str(person)], tmp_path)
    sh(["git", "config", "user.name", "Person"], person)
    sh(["git", "config", "user.email", "person@example.com"], person)
    (person / "health.py").write_text("OK = True\nPORT = 8080\n")
    sh(["git", "commit", "-q", "-am", "fix: port"], person)
    sh(["git", "push", "-q", "origin", branch], person)
    pushed = sh(["git", "rev-parse", "HEAD"], person)
    second = dispatch(ctx, 11, adapter=FakeAdapter(), now=NOW)
    assert second["worktree_mode"] == "reused" and second["branch_refresh"] == "fast-forwarded"
    assert head_sha(Path(second["worktree"])) == pushed
    wait_for_exit(second["pid"])
    rec_path = tmp_path / "home" / "runs" / "claude" / "11.json"
    rec = json.loads(rec_path.read_text())
    rec["collected"] = True
    rec_path.write_text(json.dumps(rec))
    third = dispatch(ctx, 11, adapter=FakeAdapter(), now=NOW)
    assert third["branch_refresh"] == "current"
    wait_for_exit(third["pid"])
