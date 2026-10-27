from types import SimpleNamespace
import shutil
import subprocess
from pathlib import Path

import pytest

from meld.gitops import (
    GitError,
    branch_name,
    checkout_pr_head,
    checkout_sha,
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
    assert not is_dirty(wt)
    (wt / "health.py").write_text("OK = True\n")
    assert is_dirty(wt)
    sh(["git", "add", "health.py"], wt)
    sh(["git", "-c", "user.name=Meld Claude", "-c", "user.email=1+meld-claude-bot@users.noreply.github.com", "commit", "-q", "-m", "feat: health"], wt)
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


def test_worktree_exists_needs_the_directory_and_create_prunes_a_stale_registration(tmp_path):
    origin, repo = make_origin_and_clone(tmp_path)
    wt = tmp_path / "wt" / "claude" / "5-x"
    assert create_worktree(repo, wt, "meld/5-x", "main") == "new"
    shutil.rmtree(wt)  # $MELD_HOME/worktrees is disposable state
    assert "meld/5-x" in git(["worktree", "list", "--porcelain"], repo)  # git still has the registration
    assert not worktree_exists(repo, wt)
    assert create_worktree(repo, wt, "meld/5-x", "main") == "new" and worktree_exists(repo, wt)
    review = tmp_path / "wt" / "claude" / "review-5"
    checkout_pr_head(repo, review, "main")
    shutil.rmtree(review)
    assert not worktree_exists(repo, review)
    checkout_pr_head(repo, review, "main")
    assert worktree_exists(repo, review) and head_sha(review) == head_sha(repo)


def test_checkout_sha_detaches_at_an_exact_commit(tmp_path):
    origin, repo = make_origin_and_clone(tmp_path)
    (repo / "b.txt").write_text("b\n")
    sh(["git", "add", "b.txt"], repo)
    sh(["git", "commit", "-q", "-m", "second"], repo)
    sh(["git", "push", "-q", "origin", "main"], repo)
    target = sh(["git", "rev-parse", "HEAD~1"], repo)
    wt = tmp_path / "wt"
    checkout_sha(repo, wt, target)
    assert head_sha(wt) == target and not (wt / "b.txt").exists()
    with pytest.raises(GitError):
        checkout_sha(repo, tmp_path / "wt2", "0000000000000000000000000000000000000000")


def test_push_branch_pushes_head_from_attached_and_detached_checkouts(tmp_path):
    origin, repo = make_origin_and_clone(tmp_path)
    wt = tmp_path / "wt" / "a"
    create_worktree(repo, wt, "meld/3-a", "main")
    (wt / "a.txt").write_text("a\n")
    sh(["git", "add", "a.txt"], wt)
    sh(["git", "commit", "-q", "-m", "a"], wt)
    push_branch(wt, "meld/3-a", token=None)
    assert sh(["git", "rev-parse", "--abbrev-ref", "meld/3-a@{upstream}"], wt) == "origin/meld/3-a"  # attached: upstream set
    detached = tmp_path / "wt" / "review-3"
    checkout_pr_head(repo, detached, "meld/3-a")
    assert sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], detached) == "HEAD"  # detached
    (detached / "fix.txt").write_text("fix\n")
    sh(["git", "add", "fix.txt"], detached)
    sh(["git", "commit", "-q", "-m", "reviewer fix"], detached)
    push_branch(detached, "meld/3-a", token=None)  # a bare branch name here would push the clone's local branch, not HEAD
    assert sh(["git", "ls-remote", "--heads", "origin", "meld/3-a"], repo).split()[0] == head_sha(detached)


def test_a_fetch_authenticates_with_the_groups_token_like_a_push(tmp_path):
    """The first wizard-made product was private with no collaborators: the PM's `git fetch origin` ran with the machine's
    own credentials and answered "repository not found", while the group's App token could read it. Every fetch a group
    makes now carries the token through the inline helper, never in argv; without a token, git's own credentials apply."""
    from meld.gitops import _auth, checkout_pr_head, checkout_sha, create_worktree, fast_forward_to_remote
    calls = []

    def run(argv, cwd=None, capture_output=True, text=True, env=None, **kw):
        calls.append((argv, env))
        return SimpleNamespace(returncode=0, stdout="abc123\n", stderr="")

    args, env = _auth("ghs_token")
    assert args == ["-c", "credential.helper=", "-c", 'credential.helper=!f() { echo username=x-access-token; echo "password=$MELD_PUSH_TOKEN"; }; f'] and env["MELD_PUSH_TOKEN"] == "ghs_token"
    assert _auth(None) == ([], None) and "ghs_token" not in " ".join(args)
    for call in (lambda: create_worktree(tmp_path, tmp_path / "wt", "meld/1-x", "main", run=run, token="ghs_token"),
                 lambda: checkout_pr_head(tmp_path, tmp_path / "wt", "meld/1-x", run=run, token="ghs_token"),
                 lambda: checkout_sha(tmp_path, tmp_path / "wt", "abc123", run=run, token="ghs_token"),
                 lambda: fast_forward_to_remote(tmp_path, tmp_path / "wt", "meld/1-x", run=run, token="ghs_token")):
        calls.clear()
        try:
            call()
        except Exception:   # noqa: BLE001 -- the fake git answers every command the same; only the fetch's shape matters here
            pass
        fetches = [(a, e) for a, e in calls if "fetch" in a]
        assert fetches, call
        argv, env = fetches[0]
        assert argv[:5] == ["git", "-c", "credential.helper=", "-c", 'credential.helper=!f() { echo username=x-access-token; echo "password=$MELD_PUSH_TOKEN"; }; f']
        assert env["MELD_PUSH_TOKEN"] == "ghs_token" and "ghs_token" not in " ".join(argv)
    from meld.gitops import fetch_origin, remote_branch_exists
    calls.clear()
    remote_branch_exists(tmp_path, "meld/1-x", run=run, token="ghs_token")
    fetch_origin(tmp_path, "ghs_token", "main", run=run)
    assert all(a[1:3] == ["-c", "credential.helper="] and e["MELD_PUSH_TOKEN"] == "ghs_token" for a, e in calls) and calls[0][0][5] == "ls-remote" and calls[1][0][-1] == "main"
    calls.clear()
    try:
        create_worktree(tmp_path, tmp_path / "wt2", "meld/2-y", "main", run=run)
    except Exception:   # noqa: BLE001
        pass
    argv, env = [(a, e) for a, e in calls if "fetch" in a][0]
    assert argv[:3] == ["git", "fetch", "--quiet"] and env is None
