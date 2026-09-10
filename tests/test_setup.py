import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.github import GitHub
from meld.models import ModelRegistry
from meld.setup import LABELS, SetupError, ensure_coordination_issue, ensure_labels, ensure_milestone, installed_adapters, setup, starter_toml
from tests.fakegh import FakeGh

R = "repos/acme/widgets/"


def gh_for(fake):
    return GitHub("acme/widgets", token=None, run=fake)


def route_repo(fake, private=True, default_branch="main"):
    fake.route("GET", "repos/acme/widgets", {"private": private, "default_branch": default_branch, "full_name": "acme/widgets"})
    fake.route("GET", "user", {"login": "empowerScott"})


def test_ensure_labels_creates_only_the_missing_ones():
    fake = FakeGh()
    fake.route("GET", R + "labels", [{"name": "meld:ready", "color": "0e8a16"}])
    fake.route("POST", R + "labels", {"name": "x"}, status=201)
    out = ensure_labels(gh_for(fake))
    posted = [c.body["name"] for c in fake.calls if c.method == "POST" and c.endpoint.endswith("labels")]
    assert "meld:ready" not in posted and set(posted) == set(LABELS) - {"meld:ready"}
    assert out == {"created": sorted(set(LABELS) - {"meld:ready"}), "existing": ["meld:ready"]}


def test_ensure_milestone_and_coordination_issue_are_idempotent():
    fake = FakeGh()
    fake.route("GET", R + "milestones", [{"number": 3, "title": "M1: Skeleton", "state": "open"}])
    fake.route("GET", R + "issues", [{"number": 10, "title": "Coordination: M1", "labels": [{"name": "meld:coordination"}], "milestone": {"number": 3}}])
    gh = gh_for(fake)
    assert ensure_milestone(gh, "M1: Skeleton") == (3, "existing")
    assert ensure_coordination_issue(gh, 3, "M1: Skeleton", "Build the skeleton") == (10, "existing")
    assert not [c for c in fake.calls if c.method == "POST"]
    fake2 = FakeGh()
    fake2.route("GET", R + "milestones", [])
    fake2.route("POST", R + "milestones", {"number": 4, "title": "M1: Skeleton"}, status=201)
    fake2.route("GET", R + "issues", [])
    fake2.route("POST", R + "issues", {"number": 12}, status=201)
    gh2 = gh_for(fake2)
    assert ensure_milestone(gh2, "M1: Skeleton") == (4, "created")
    number, how = ensure_coordination_issue(gh2, 4, "M1: Skeleton", "Build the skeleton")
    assert (number, how) == (12, "created")
    body = [c.body for c in fake2.calls if c.method == "POST" and c.endpoint.endswith("issues")][0]
    assert body["labels"] == ["meld:coordination"] and body["milestone"] == 4 and "Build the skeleton" in body["body"]


def test_ensure_milestone_reopens_a_closed_milestone_with_the_same_title():
    fake = FakeGh()
    fake.route("GET", R + "milestones", [{"number": 5, "title": "M1: Skeleton", "state": "closed"}])
    fake.route("PATCH", R + "milestones/5", {"number": 5, "title": "M1: Skeleton", "state": "open"})
    gh = gh_for(fake)
    assert ensure_milestone(gh, "M1: Skeleton") == (5, "reopened")
    patches = [c for c in fake.calls if c.method == "PATCH"]
    assert len(patches) == 1 and patches[0].endpoint.endswith("milestones/5") and patches[0].body == {"state": "open"}
    assert not [c for c in fake.calls if c.method == "POST"]


def test_starter_toml_names_installed_adapters_with_registry_models():
    registry = ModelRegistry.load()
    text = starter_toml("acme/widgets", "main", "empowerScott", ["claude", "codex"], registry)
    data = tomllib.loads(text)
    assert data["repo"] == {"name": "acme/widgets", "default_branch": "main", "private": True}
    assert data["humans"]["accounts"] == ["empowerScott"]
    for name in ("claude", "codex"):
        g = data["groups"][name]
        assert g["bot"] == f"meld-{name}-bot" and g["token_env"] == f"MELD_TOKEN_{name.upper()}"
        for role in ("orchestrator", "pm"):
            spec = registry.get(name, g[role]["model"])
            assert spec is not None and g[role]["effort"] in spec.efforts
        assert registry.tier_of(name, g["orchestrator"]["model"]) == "large"
    assert data["notify"]["slack_dm_webhook_env"] == "MELD_SLACK_DM_WEBHOOK"


def test_installed_adapters_uses_which():
    assert installed_adapters(which=lambda b: "/usr/bin/" + b if b == "claude" else None) == ["claude"]


def test_setup_refuses_a_public_repo_without_allow_public(tmp_path):
    fake = FakeGh()
    route_repo(fake, private=False)
    with pytest.raises(SetupError, match="public"):
        setup(gh_for(fake), repo="acme/widgets", goal="g", milestone_title="M1", human="empowerScott", target_dir=tmp_path,
              registry=ModelRegistry.load(), which=lambda b: "/bin/" + b)
    assert not (tmp_path / ".meld.toml").exists()


def test_setup_writes_the_starter_and_never_overwrites_without_force(tmp_path):
    fake = FakeGh()
    route_repo(fake)
    fake.route("GET", R + "labels", [])
    fake.route("POST", R + "labels", {"name": "x"}, status=201)
    fake.route("GET", R + "milestones", [])
    fake.route("POST", R + "milestones", {"number": 1, "title": "M1"}, status=201)
    fake.route("GET", R + "issues", [])
    fake.route("POST", R + "issues", {"number": 2}, status=201)
    out = setup(gh_for(fake), repo="acme/widgets", goal="Ship a widget catalogue", milestone_title="M1", human="empowerScott",
                target_dir=tmp_path, registry=ModelRegistry.load(), which=lambda b: "/bin/" + b)
    assert out["milestone"] == 1 and out["coordination_issue"] == 2 and out["config"] == str(tmp_path / ".meld.toml")
    assert out["labels"]["created"] and out["groups"] == ["claude", "codex"]
    text = (tmp_path / ".meld.toml").read_text()
    assert 'name = "acme/widgets"' in text
    with pytest.raises(SetupError, match="exists"):
        setup(gh_for(fake), repo="acme/widgets", goal="g", milestone_title="M1", human="empowerScott", target_dir=tmp_path,
              registry=ModelRegistry.load(), which=lambda b: "/bin/" + b)
    out2 = setup(gh_for(fake), repo="acme/widgets", goal="g", milestone_title="M1", human="empowerScott", target_dir=tmp_path,
                 registry=ModelRegistry.load(), which=lambda b: "/bin/" + b, force=True)
    assert out2["config_written"] is True


def test_setup_with_no_installed_cli_writes_commented_groups(tmp_path):
    fake = FakeGh()
    route_repo(fake)
    fake.route("GET", R + "labels", [])
    fake.route("POST", R + "labels", {"name": "x"}, status=201)
    fake.route("GET", R + "milestones", [{"number": 1, "title": "M1", "state": "open"}])
    fake.route("GET", R + "issues", [{"number": 2, "title": "c", "labels": [{"name": "meld:coordination"}], "milestone": {"number": 1}}])
    out = setup(gh_for(fake), repo="acme/widgets", goal="g", milestone_title="M1", human="empowerScott", target_dir=tmp_path,
                registry=ModelRegistry.load(), which=lambda b: None)
    assert out["groups"] == [] and "no CLI found" in " ".join(out["warnings"])
    text = (tmp_path / ".meld.toml").read_text()
    assert "# [groups.claude]" in text and tomllib.loads(text)["repo"]["name"] == "acme/widgets"


def test_group_block_and_setup_comment_out_an_installed_adapter_with_no_models(tmp_path):
    models_toml = tmp_path / "models.toml"
    models_toml.write_text(
        '[claude.claude-opus-5]\n'
        'context_class = "large"\n'
        'efforts = ["low", "medium", "high", "max"]\n'
        'tier = "large"\n'
    )
    registry = ModelRegistry.load(models_toml)
    text = starter_toml("acme/widgets", "main", "empowerScott", ["claude", "codex"], registry)
    assert "# [groups.codex]" in text and "# [groups.claude]" not in text

    fake = FakeGh()
    route_repo(fake)
    fake.route("GET", R + "labels", [])
    fake.route("POST", R + "labels", {"name": "x"}, status=201)
    fake.route("GET", R + "milestones", [])
    fake.route("POST", R + "milestones", {"number": 1, "title": "M1"}, status=201)
    fake.route("GET", R + "issues", [])
    fake.route("POST", R + "issues", {"number": 2}, status=201)
    out = setup(gh_for(fake), repo="acme/widgets", goal="g", milestone_title="M1", human="empowerScott", target_dir=tmp_path,
                registry=registry, which=lambda b: "/bin/" + b)
    assert out["groups"] == ["claude"]
    assert any("codex is installed but models.toml lists no models for it" in w for w in out["warnings"])


def test_cli_setup_command_writes_config(tmp_path, monkeypatch):
    fake = FakeGh()
    route_repo(fake)
    fake.route("GET", R + "labels", [])
    fake.route("POST", R + "labels", {"name": "x"}, status=201)
    fake.route("GET", R + "milestones", [])
    fake.route("POST", R + "milestones", {"number": 1, "title": "M1"}, status=201)
    fake.route("GET", R + "issues", [])
    fake.route("POST", R + "issues", {"number": 2}, status=201)
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["setup", "acme/widgets", "--goal", "g", "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".meld.toml").exists()


def test_repo_files_write_codeowners_and_standards_once(tmp_path):
    from meld.setup import repo_files
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "CODEOWNERS").write_text("* @someone\n")
    written = repo_files(tmp_path, "empowerScott")
    assert set(written) == {".github/CODEOWNERS", ".meld/STANDARDS.md"}
    assert (tmp_path / ".github" / "CODEOWNERS").read_text() == "* @someone\n/.meld/workers/ @empowerScott\n"
    assert "visual design" in (tmp_path / ".meld" / "STANDARDS.md").read_text().lower()
    assert repo_files(tmp_path, "empowerScott") == []   # second run changes nothing


def test_ci_paths_warning_fires_when_workflows_filter_out_meld(tmp_path):
    from meld.setup import ci_paths_warnings
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("on:\n  pull_request:\n    paths:\n      - 'src/**'\n      - 'tests/**'\njobs: {}\n")
    (wf / "docs.yml").write_text("on:\n  pull_request:\n    paths:\n      - '.meld/**'\njobs: {}\n")
    warnings = ci_paths_warnings(tmp_path)
    assert len(warnings) == 1 and "ci.yml" in warnings[0] and ".meld" in warnings[0]
    (wf / "ci.yml").write_text("on: [pull_request]\njobs: {}\n")
    assert ci_paths_warnings(tmp_path) == []


def test_protect_branch_lists_review_statuses_as_informational_and_tolerates_403():
    from meld.setup import protect_branch
    fake = FakeGh()
    fake.route("PUT", R + "branches/main/protection", {"url": "x"})
    out = protect_branch(gh_for(fake), "main")
    call = [c for c in fake.calls if c.method == "PUT"][0]
    assert call.body["required_status_checks"] is None and call.body["required_pull_request_reviews"]["required_approving_review_count"] == 1
    assert out == {"protected": True, "branch": "main", "note": "meld/review/<group> statuses are informational; add them to required checks only if you want them blocking"}
    fake2 = FakeGh()
    fake2.route("PUT", R + "branches/main/protection", {"message": "Upgrade to GitHub Pro"}, status=403)
    out2 = protect_branch(gh_for(fake2), "main")
    assert out2["protected"] is False and "403" in out2["error"]


def test_open_setup_pr_commits_pushes_and_opens(tmp_path):
    from meld.setup import _checkout_setup_branch, open_setup_pr, repo_files
    from tests.gitfixtures import make_origin_and_clone, sh
    origin, repo = make_origin_and_clone(tmp_path)
    _checkout_setup_branch(repo, "main")
    (repo / ".meld.toml").write_text('[repo]\nname = "acme/widgets"\n')
    files = repo_files(repo, "empowerScott") + [".meld.toml"]
    fake = FakeGh()
    fake.route("POST", R + "pulls", {"number": 7, "html_url": "u7"}, status=201)
    out = open_setup_pr(gh_for(fake), repo, "main", files)
    assert out == {"pr": 7, "url": "u7", "branch": "meld/setup"}
    assert sh(["git", "ls-remote", "--heads", "origin", "meld/setup"], repo)
    assert sh(["git", "branch", "--show-current"], repo) == "meld/setup"
    body = [c.body for c in fake.calls if c.method == "POST"][0]
    assert body["head"] == "meld/setup" and body["base"] == "main" and ".meld.toml" in body["body"]


def test_ci_paths_warning_fires_when_workflow_uses_paths_ignore(tmp_path):
    from meld.setup import ci_paths_warnings
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("on:\n  pull_request:\n    paths-ignore:\n      - '.meld/**'\njobs: {}\n")
    warnings = ci_paths_warnings(tmp_path)
    assert len(warnings) == 1 and "ci.yml" in warnings[0] and "paths-ignore" in warnings[0]
    (wf / "ci.yml").write_text("on:\n  pull_request:\n    paths-ignore:\n      - 'docs/**'\njobs: {}\n")
    assert ci_paths_warnings(tmp_path) == []


def test_open_setup_pr_is_rerunnable_and_reuses_an_existing_open_pr(tmp_path):
    from meld.setup import _checkout_setup_branch, open_setup_pr, repo_files
    from tests.gitfixtures import make_origin_and_clone
    origin, repo = make_origin_and_clone(tmp_path)
    _checkout_setup_branch(repo, "main")
    (repo / ".meld.toml").write_text('[repo]\nname = "acme/widgets"\n')
    files = repo_files(repo, "empowerScott") + [".meld.toml"]
    fake = FakeGh()
    fake.route("POST", R + "pulls", {"message": "A pull request already exists for acme:meld/setup."}, status=422)
    fake.route("GET", R + "pulls", [{"number": 7, "html_url": "u7"}])
    gh = gh_for(fake)
    first = open_setup_pr(gh, repo, "main", files)
    assert first == {"pr": 7, "url": "u7", "branch": "meld/setup", "existing": True}
    second = open_setup_pr(gh, repo, "main", files)  # unchanged files: nothing new to commit, but the push must not fail
    assert second == {"pr": 7, "url": "u7", "branch": "meld/setup", "existing": True}


def test_open_setup_pr_branches_from_origin_default_not_the_current_checkout(tmp_path):
    from tests.gitfixtures import make_origin_and_clone, sh
    origin, repo = make_origin_and_clone(tmp_path)
    sh(["git", "checkout", "-q", "-b", "feature"], repo)
    (repo / "wip.txt").write_text("wip\n")
    sh(["git", "add", "wip.txt"], repo)
    sh(["git", "commit", "-q", "-m", "wip: local only, never pushed"], repo)
    fake = FakeGh()
    route_repo(fake)
    fake.route("GET", R + "labels", [])
    fake.route("POST", R + "labels", {"name": "x"}, status=201)
    fake.route("GET", R + "milestones", [])
    fake.route("POST", R + "milestones", {"number": 1, "title": "M1"}, status=201)
    fake.route("GET", R + "issues", [])
    fake.route("POST", R + "issues", {"number": 2}, status=201)
    fake.route("POST", R + "pulls", {"number": 7, "html_url": "u7"}, status=201)
    setup(gh_for(fake), repo="acme/widgets", goal="g", milestone_title="M1", human="empowerScott", target_dir=repo,
          registry=ModelRegistry.load(), which=lambda b: None, pr=True)
    log = sh(["git", "log", "--oneline", "origin/main..meld/setup"], repo)
    assert len(log.splitlines()) == 1
    assert "wip" not in log


def test_open_setup_pr_commits_only_its_own_files(tmp_path):
    from meld.setup import _checkout_setup_branch, open_setup_pr, repo_files
    from tests.gitfixtures import make_origin_and_clone, sh
    origin, repo = make_origin_and_clone(tmp_path)
    _checkout_setup_branch(repo, "main")
    (repo / ".meld.toml").write_text('[repo]\nname = "acme/widgets"\n')
    files = repo_files(repo, "empowerScott") + [".meld.toml"]
    (repo / "unrelated.txt").write_text("unrelated\n")
    sh(["git", "add", "unrelated.txt"], repo)
    fake = FakeGh()
    fake.route("POST", R + "pulls", {"number": 7, "html_url": "u7"}, status=201)
    open_setup_pr(gh_for(fake), repo, "main", files)
    committed = sh(["git", "show", "--name-only", "--format=", "HEAD"], repo).splitlines()
    assert "unrelated.txt" not in committed
    assert set(committed) == set(files)
    status = sh(["git", "status", "--porcelain", "--", "unrelated.txt"], repo)
    assert "unrelated.txt" in status  # still staged, untouched by the setup commit


def test_open_setup_pr_reraises_422_when_no_existing_pr_is_found(tmp_path):
    from meld.github import GitHubError
    from meld.setup import _checkout_setup_branch, open_setup_pr, repo_files
    from tests.gitfixtures import make_origin_and_clone
    origin, repo = make_origin_and_clone(tmp_path)
    _checkout_setup_branch(repo, "main")
    (repo / ".meld.toml").write_text('[repo]\nname = "acme/widgets"\n')
    files = repo_files(repo, "empowerScott") + [".meld.toml"]
    fake = FakeGh()
    fake.route("POST", R + "pulls", {"message": "..."}, status=422)
    fake.route("GET", R + "pulls", [])
    with pytest.raises(GitHubError):
        open_setup_pr(gh_for(fake), repo, "main", files)


def test_ci_paths_warning_supports_flow_style_paths_ignore(tmp_path):
    from meld.setup import ci_paths_warnings
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("on:\n  pull_request:\n    paths-ignore: ['.meld/**', 'docs/**']\njobs: {}\n")
    warnings = ci_paths_warnings(tmp_path)
    assert len(warnings) == 1 and "ci.yml" in warnings[0] and "paths-ignore" in warnings[0]


def test_ci_paths_warning_ignores_a_push_only_filter(tmp_path):
    from meld.setup import ci_paths_warnings
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("on:\n  push:\n    paths:\n      - '.meld/**'\njobs: {}\n")
    assert ci_paths_warnings(tmp_path) == []


def test_ci_paths_warning_never_looks_inside_jobs(tmp_path):
    from meld.setup import ci_paths_warnings
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "on:\n  pull_request:\n    branches: ['main']\n"
        "jobs:\n  build:\n    steps:\n      - uses: some/action\n        with:\n          paths: '.meld/**'\n"
    )
    assert ci_paths_warnings(tmp_path) == []


def test_starter_toml_app_form_and_commented_alternative():
    registry = ModelRegistry.load()
    text = starter_toml("acme/widgets", "main", "empowerScott", ["claude", "codex"], registry, app=True)
    data = tomllib.loads(text)
    g = data["groups"]["claude"]
    assert g["auth"] == "app" and g["bot"] == "meld-claude[bot]" and g["private_key_env"] == "MELD_APP_KEY_CLAUDE" and "token_env" not in g
    assert g["app_id"] == 0 and g["installation_id"] == 0
    pat = starter_toml("acme/widgets", "main", "empowerScott", ["claude"], registry)
    assert tomllib.loads(pat)["groups"]["claude"]["token_env"] == "MELD_TOKEN_CLAUDE"
    assert '# auth = "app"' in pat and '# private_key_env = "MELD_APP_KEY_CLAUDE"' in pat


def test_setup_app_flag_writes_app_form_and_next_steps(tmp_path):
    fake = FakeGh()
    route_repo(fake)
    fake.route("GET", R + "labels", [])
    fake.route("POST", R + "labels", {"name": "x"}, status=201)
    fake.route("GET", R + "milestones", [])
    fake.route("POST", R + "milestones", {"number": 1, "title": "M1"}, status=201)
    fake.route("GET", R + "issues", [])
    fake.route("POST", R + "issues", {"number": 2}, status=201)
    out = setup(gh_for(fake), repo="acme/widgets", goal="g", milestone_title="M1", human="empowerScott", target_dir=tmp_path,
                registry=ModelRegistry.load(), which=lambda b: "/bin/" + b, protect=False, app=True)
    assert 'auth = "app"' in (tmp_path / ".meld.toml").read_text()
    assert any("github.com/settings/apps/new" in step for step in out["next"]) and any("installation_id" in step for step in out["next"])
    # Final-review Minor 8: Checks is recommended, not "optional" -- the printed steps must say what is lost without it.
    assert any("check_run_error" in step for step in out["next"])
