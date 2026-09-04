import subprocess
from pathlib import Path

import pytest

from meld.gitops import (
    GitError,
    branch_name,
    checkout_pr_head,
    commits_ahead,
    create_worktree,
    delete_local_branch,
    git,
    head_sha,
    is_dirty,
    noreply_email,
    push_branch,
    remote_branch_exists,
    remove_worktree,
    set_identity,
    slugify,
    worktree_exists,
)
from tests.gitfixtures import make_origin_and_clone, sh


def test_slug_and_branch_and_noreply():
    assert slugify("Add Health Endpoint!!") == "add-health-endpoint"
    assert slugify("x" * 60, limit=10) == "xxxxxxxxxx"
    assert slugify("///") == "task"
    assert branch_name(42, "Add Health Endpoint!!") == "meld/42-add-health-endpoint"
    assert noreply_email(421573, "smidley") == "421573+smidley@users.noreply.github.com"


def test_git_raises_gerror_with_stderr(tmp_path):
    with pytest.raises(GitError, match="not a git repository"):
        git(["status"], tmp_path)


def test_worktree_lifecycle_new_push_existing(tmp_path):
    origin, repo = make_origin_and_clone(tmp_path)
    wt = tmp_path / "wt" / "claude" / "42-add-health"
    branch = "meld/42-add-health"
    assert not worktree_exists(repo, wt)
    assert create_worktree(repo, wt, branch, "main") == "new"
    assert worktree_exists(repo, wt)
    set_identity(wt, "Meld Claude", "1+meld-claude-bot@users.noreply.github.com")
    assert not is_dirty(wt)
    (wt / "health.py").write_text("OK = True\n")
    assert is_dirty(wt)
    sh(["git", "add", "health.py"], wt)
    sh(["git", "commit", "-q", "-m", "feat: health"], wt)
    assert sh(["git", "log", "-1", "--format=%an <%ae>"], wt) == "Meld Claude <1+meld-claude-bot@users.noreply.github.com>"
    ahead = commits_ahead(wt, "origin/main")
    assert ahead == [head_sha(wt)]
    assert not remote_branch_exists(repo, branch)
    push_branch(wt, branch, token=None)
    assert remote_branch_exists(repo, branch)
    remove_worktree(repo, wt)
    assert not wt.exists() and not worktree_exists(repo, wt)
    delete_local_branch(repo, branch)
    delete_local_branch(repo, branch)  # idempotent
    assert create_worktree(repo, wt, branch, "main") == "existing"
    assert head_sha(wt) == ahead[0]
    other = tmp_path / "wt" / "codex" / "review-42"
    checkout_pr_head(repo, other, branch)
    assert head_sha(other) == ahead[0]


def test_push_keeps_token_out_of_argv(tmp_path):
    origin, repo = make_origin_and_clone(tmp_path)
    wt = tmp_path / "wt" / "x"
    create_worktree(repo, wt, "meld/7-x", "main")
    (wt / "a.txt").write_text("a\n")
    sh(["git", "add", "a.txt"], wt)
    sh(["git", "commit", "-q", "-m", "a"], wt)
    seen: list[tuple[list[str], dict]] = []

    def recording_run(cmd, **kwargs):
        seen.append((list(cmd), dict(kwargs.get("env") or {})))
        return subprocess.run(cmd, **kwargs)

    push_branch(wt, "meld/7-x", token="ghp_secret", run=recording_run, base_env={"PATH": "/usr/bin:/bin"})
    cmd, env = seen[-1]
    assert "ghp_secret" not in " ".join(cmd)
    assert env["MELD_PUSH_TOKEN"] == "ghp_secret"
    assert any(a.startswith("credential.helper=!f()") for a in cmd)
    assert remote_branch_exists(repo, "meld/7-x")


def test_exclude_paths_hides_scratch_files_from_status(tmp_path):
    from meld.gitops import exclude_paths
    origin, repo = make_origin_and_clone(tmp_path)
    wt = tmp_path / "wt" / "z"
    create_worktree(repo, wt, "meld/9-z", "main")
    exclude_paths(wt)
    for name in (".meld-brief.md", ".meld-result.json", ".meld-last-message.txt"):
        (wt / name).write_text("scratch\n")
    (wt / "real.py").write_text("x = 1\n")
    assert git(["status", "--porcelain"], wt).splitlines() == ["?? real.py"]
    exclude_paths(wt)  # idempotent
    exclude_file = Path(git(["rev-parse", "--git-path", "info/exclude"], wt))
    assert exclude_file.read_text().count(".meld-brief.md") == 1


def test_push_without_token_leaves_git_credentials_alone(tmp_path):
    origin, repo = make_origin_and_clone(tmp_path)
    wt = tmp_path / "wt" / "y"
    create_worktree(repo, wt, "meld/8-y", "main")
    (wt / "b.txt").write_text("b\n")
    sh(["git", "add", "b.txt"], wt)
    sh(["git", "commit", "-q", "-m", "b"], wt)
    seen: list[list[str]] = []

    def recording_run(cmd, **kwargs):
        seen.append(list(cmd))
        return subprocess.run(cmd, **kwargs)

    push_branch(wt, "meld/8-y", token=None, run=recording_run)
    assert not any("credential.helper" in a for a in seen[-1])
    assert remote_branch_exists(repo, "meld/8-y")
