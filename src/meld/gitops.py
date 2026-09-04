"""Git plumbing for worktrees and pushes. The only module that runs git."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable

RunFn = Callable[..., subprocess.CompletedProcess]
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_CREDENTIAL_HELPER = '!f() { echo username=x-access-token; echo "password=$MELD_PUSH_TOKEN"; }; f'


class GitError(RuntimeError):
    pass


def git(args: list[str], cwd: Path, run: RunFn = subprocess.run, env: dict | None = None) -> str:
    proc = run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {cwd}: {(proc.stderr or '').strip()}")
    return (proc.stdout or "").strip()


def slugify(title: str, limit: int = 40) -> str:
    slug = _SLUG_RE.sub("-", title.lower()).strip("-")
    return slug[:limit].rstrip("-") or "task"


def branch_name(issue: int, title: str) -> str:
    return f"meld/{issue}-{slugify(title)}"


def noreply_email(user_id: int, login: str) -> str:
    return f"{user_id}+{login}@users.noreply.github.com"


def remote_branch_exists(repo_root: Path, branch: str, run: RunFn = subprocess.run) -> bool:
    return bool(git(["ls-remote", "--heads", "origin", branch], repo_root, run))


def worktree_exists(repo_root: Path, path: Path, run: RunFn = subprocess.run) -> bool:
    """True only when `path` is registered as a worktree and its directory is present. A registration whose
    directory was deleted ($MELD_HOME is disposable) is stale; the next create prunes it."""
    if not path.is_dir():
        return False
    listing = git(["worktree", "list", "--porcelain"], repo_root, run)
    target = path.resolve()
    return any(line.startswith("worktree ") and Path(line[len("worktree "):]).resolve() == target for line in listing.splitlines())


def create_worktree(repo_root: Path, path: Path, branch: str, base: str, run: RunFn = subprocess.run) -> str:
    """Create `path` on `branch`: reuse the remote branch when it exists (a retry), else branch from origin/<base>."""
    git(["fetch", "--quiet", "origin"], repo_root, run)
    git(["worktree", "prune"], repo_root, run)  # a registration whose directory is gone would otherwise refuse the add
    path.parent.mkdir(parents=True, exist_ok=True)
    if remote_branch_exists(repo_root, branch, run):
        git(["worktree", "add", "--track", "-B", branch, str(path), f"origin/{branch}"], repo_root, run)
        return "existing"
    git(["worktree", "add", "-B", branch, str(path), f"origin/{base}"], repo_root, run)
    return "new"


def checkout_pr_head(repo_root: Path, path: Path, branch: str, run: RunFn = subprocess.run) -> None:
    git(["fetch", "--quiet", "origin", branch], repo_root, run)
    git(["worktree", "prune"], repo_root, run)
    path.parent.mkdir(parents=True, exist_ok=True)
    git(["worktree", "add", "--detach", str(path), f"origin/{branch}"], repo_root, run)


def head_sha(path: Path, run: RunFn = subprocess.run) -> str:
    return git(["rev-parse", "HEAD"], path, run)


def commits_ahead(path: Path, base_ref: str, run: RunFn = subprocess.run) -> list[str]:
    out = git(["rev-list", "--reverse", f"{base_ref}..HEAD"], path, run)
    return out.splitlines() if out else []


def is_dirty(path: Path, run: RunFn = subprocess.run) -> bool:
    return bool(git(["status", "--porcelain"], path, run))


def push_branch(path: Path, branch: str, token: str | None, run: RunFn = subprocess.run, base_env: dict | None = None) -> None:
    """Push HEAD to origin/<branch>. The refspec is HEAD:refs/heads/<branch> rather than the bare branch name so a
    detached checkout (a review worktree) pushes what it has, not whatever the clone's local branch points at;
    --set-upstream is harmless when detached (there is no branch to configure). With a token, git authenticates
    through an inline helper that reads MELD_PUSH_TOKEN from the environment (the token never appears in argv);
    without one, git's own configured credentials apply."""
    env = dict(os.environ if base_env is None else base_env)
    args: list[str] = []
    if token:
        env["MELD_PUSH_TOKEN"] = token
        args += ["-c", "credential.helper=", "-c", f"credential.helper={_CREDENTIAL_HELPER}"]
    git([*args, "push", "--set-upstream", "origin", f"HEAD:refs/heads/{branch}"], path, run, env=env)


def remove_worktree(repo_root: Path, path: Path, run: RunFn = subprocess.run) -> None:
    git(["worktree", "remove", "--force", str(path)], repo_root, run)
    git(["worktree", "prune"], repo_root, run)


def delete_local_branch(repo_root: Path, branch: str, run: RunFn = subprocess.run) -> None:
    try:
        git(["branch", "-D", branch], repo_root, run)
    except GitError as e:
        if "not found" not in str(e):
            raise


MELD_SCRATCH_FILES = (".meld-brief.md", ".meld-result.json", ".meld-last-message.txt")


def exclude_paths(path: Path, patterns: tuple[str, ...] = MELD_SCRATCH_FILES, run: RunFn = subprocess.run) -> None:
    """Hide meld's scratch files from git in this worktree via its private info/exclude (never the shared .gitignore)."""
    exclude = Path(git(["rev-parse", "--git-path", "info/exclude"], path, run))
    if not exclude.is_absolute():
        exclude = path / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text().splitlines() if exclude.exists() else []
    missing = [p for p in patterns if p not in existing]
    if missing:
        exclude.write_text("\n".join([*existing, *missing]) + "\n")
