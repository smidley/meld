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


def create_worktree(repo_root: Path, path: Path, branch: str, base: str, run: RunFn = subprocess.run, token: str | None = None) -> str:
    """Create `path` on `branch`: reuse the remote branch when it exists (a retry), else branch from origin/<base>."""
    git([*_auth(token)[0], "fetch", "--quiet", "origin"], repo_root, run, env=_auth(token)[1])
    git(["worktree", "prune"], repo_root, run)  # a registration whose directory is gone would otherwise refuse the add
    path.parent.mkdir(parents=True, exist_ok=True)
    if remote_branch_exists(repo_root, branch, run):
        git(["worktree", "add", "--track", "-B", branch, str(path), f"origin/{branch}"], repo_root, run)
        return "existing"
    git(["worktree", "add", "-B", branch, str(path), f"origin/{base}"], repo_root, run)
    return "new"


def checkout_pr_head(repo_root: Path, path: Path, branch: str, run: RunFn = subprocess.run, token: str | None = None) -> None:
    git([*_auth(token)[0], "fetch", "--quiet", "origin", branch], repo_root, run, env=_auth(token)[1])
    git(["worktree", "prune"], repo_root, run)
    path.parent.mkdir(parents=True, exist_ok=True)
    git(["worktree", "add", "--detach", str(path), f"origin/{branch}"], repo_root, run)


def checkout_sha(repo_root: Path, path: Path, sha: str, run: RunFn = subprocess.run, token: str | None = None) -> None:
    """Detached worktree at an exact commit (an audit's merge commit). Fetches first so a commit that landed on the
    default branch since our last fetch is present; a sha the remote no longer reaches raises GitError."""
    git([*_auth(token)[0], "fetch", "--quiet", "origin"], repo_root, run, env=_auth(token)[1])
    git(["worktree", "prune"], repo_root, run)
    path.parent.mkdir(parents=True, exist_ok=True)
    git(["worktree", "add", "--detach", str(path), sha], repo_root, run)


def detach_head(path: Path, run: RunFn = subprocess.run) -> None:
    """Detach the worktree's HEAD at its current commit, releasing the branch name for another worktree
    (git refuses to check out a branch that is checked out elsewhere in the same clone)."""
    git(["checkout", "--quiet", "--detach"], path, run)


def head_sha(path: Path, run: RunFn = subprocess.run) -> str:
    return git(["rev-parse", "HEAD"], path, run)


def commits_ahead(path: Path, base_ref: str, run: RunFn = subprocess.run) -> list[str]:
    out = git(["rev-list", "--reverse", f"{base_ref}..HEAD"], path, run)
    return out.splitlines() if out else []


def is_dirty(path: Path, run: RunFn = subprocess.run) -> bool:
    return bool(git(["status", "--porcelain"], path, run))


def is_ancestor(path: Path, ancestor: str, descendant: str = "HEAD", run: RunFn = subprocess.run) -> bool:
    """True when `ancestor` is reachable from `descendant`; False when it is not, and when either ref does not exist."""
    try:
        git(["merge-base", "--is-ancestor", ancestor, descendant], path, run)
    except GitError:
        return False
    return True


def fast_forward_to_remote(repo_root: Path, path: Path, branch: str, run: RunFn = subprocess.run, token: str | None = None) -> str:
    """Bring a reused checkout up to origin/<branch> when it is strictly behind: fetch that branch, then fast-forward,
    so a retry starts from what a person (or a reviewer's push-fix) pushed since the last run. Returns what happened:
    "fast-forwarded", "current", "diverged" (HEAD has commits origin lacks -- left alone, the retry continues from the
    local work), "dirty" (uncommitted changes -- left alone) or "unfetched" (the fetch failed, or origin has no such
    branch yet)."""
    try:
        git([*_auth(token)[0], "fetch", "--quiet", "origin", branch], repo_root, run, env=_auth(token)[1])
    except GitError:
        return "unfetched"
    if is_dirty(path, run):
        return "dirty"
    remote = f"origin/{branch}"
    if head_sha(path, run) == git(["rev-parse", remote], path, run):
        return "current"
    if not is_ancestor(path, "HEAD", remote, run):
        return "diverged"
    git(["merge", "--ff-only", "--quiet", remote], path, run)
    return "fast-forwarded"


def _auth(token: str | None, base_env: dict | None = None) -> tuple[list[str], dict | None]:
    """git arguments and environment that authenticate as `token` through the inline helper (the token never appears in
    argv); nothing when there is no token, so git's own configured credentials apply. Used by every fetch and push a
    group makes: the first wizard-made product was private with no collaborators, and a fetch with the machine's own
    credentials answered "repository not found" while the group's token could read it fine."""
    if not token:
        return [], (dict(base_env) if base_env is not None else None)
    env = dict(os.environ if base_env is None else base_env)
    env["MELD_PUSH_TOKEN"] = token
    return ["-c", "credential.helper=", "-c", f"credential.helper={_CREDENTIAL_HELPER}"], env


def push_branch(path: Path, branch: str, token: str | None, run: RunFn = subprocess.run, base_env: dict | None = None,
                force_with_lease: bool = False) -> None:
    """Push HEAD to origin/<branch>. The refspec is HEAD:refs/heads/<branch> rather than the bare branch name so a
    detached checkout (a review worktree) pushes what it has, not whatever the clone's local branch points at;
    --set-upstream is harmless when detached (there is no branch to configure). With a token, git authenticates
    through an inline helper that reads MELD_PUSH_TOKEN from the environment (the token never appears in argv);
    without one, git's own configured credentials apply. `force_with_lease` rewrites origin/<branch> only while it is
    still at the commit this checkout's remote-tracking ref recorded, so a push nobody here has fetched (a reviewer's
    push-fix) refuses the rewrite instead of being lost."""
    args, env = _auth(token, base_env)
    env = env if env is not None else dict(os.environ)
    lease = ["--force-with-lease"] if force_with_lease else []
    git([*args, "push", *lease, "--set-upstream", "origin", f"HEAD:refs/heads/{branch}"], path, run, env=env)


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
    """Hide meld's own files from git via the clone's shared info/exclude (never the tracked .gitignore).

    `git rev-parse --git-path info/exclude` resolves to the clone's single `.git/info/exclude` from a linked
    worktree too, so every pattern written here applies to the PM's checkout and to every other worktree as
    well. Pass exact paths, one per file or directory meld itself wrote -- never a directory-wide pattern,
    which would also hide whatever the product later puts at that path. A line left behind after meld stops
    writing that path is harmless: it only ever named a file meld had created."""
    exclude = Path(git(["rev-parse", "--git-path", "info/exclude"], path, run))
    if not exclude.is_absolute():
        exclude = path / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text().splitlines() if exclude.exists() else []
    missing = [p for p in patterns if p not in existing]
    if missing:
        exclude.write_text("\n".join([*existing, *missing]) + "\n")
