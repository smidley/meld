import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.actions import Refused
from meld.adapters.claude import ClaudeAdapter
from meld.brief import BRIEF_FILE
from meld.context import load_context
from meld.dispatch import DispatchError, dispatch, git_identity, worker_env
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
    (repo / ".meld" / "memory").mkdir(parents=True)
    (repo / ".meld" / "memory" / "lessons.md").write_text("# Lessons\n\n## 2026-09-01 — #7 (codex)\n\nHealth endpoints must not touch the database.\n")
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
    assert ".fake/skills/" in Path(exclude if exclude.startswith("/") else str(wt / exclude)).read_text()
    assert "deploy-checklist" in (wt / ".meld-brief.md").read_text()
    assert out["skills_materialised"] == [".fake/skills/deploy-checklist"]
    wait_for_exit(out["pid"])
