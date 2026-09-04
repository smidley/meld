"""Real temporary git repositories for gitops and dispatch tests. No network."""
from __future__ import annotations

import subprocess
from pathlib import Path


def sh(args: list[str], cwd: Path) -> str:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, check=True).stdout.strip()


def make_origin_and_clone(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    sh(["git", "init", "--bare", "-b", "main", str(origin)], tmp_path)
    sh(["git", "init", "-b", "main", str(repo)], tmp_path)
    sh(["git", "config", "user.name", "Fixture"], repo)
    sh(["git", "config", "user.email", "fixture@example.com"], repo)
    (repo / "README.md").write_text("# product\n")
    sh(["git", "add", "README.md"], repo)
    sh(["git", "commit", "-q", "-m", "initial"], repo)
    sh(["git", "remote", "add", "origin", str(origin)], repo)
    sh(["git", "push", "-q", "-u", "origin", "main"], repo)
    return origin, repo
