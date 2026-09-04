"""The suite must not depend on the host: no ~/.claude scan through the CLI, no host gitconfig in any git call."""
from pathlib import Path

from meld.gitops import create_worktree, git
from tests.gitfixtures import make_origin_and_clone, sh


def test_cli_resolves_adapters_through_the_skill_less_fake():
    import meld.cli as cli
    from tests.fakeadapter import no_skills
    assert cli.ADAPTER_FOR is no_skills


def test_host_gitconfig_cannot_reach_fixture_or_gitops_git(tmp_path: Path, monkeypatch):
    home = tmp_path / "host-home"
    home.mkdir()
    # a host config that would break or relabel every commit: signing with no key, and a different identity
    (home / ".gitconfig").write_text("[commit]\n\tgpgsign = true\n[user]\n\tname = Host\n\temail = host@example.com\n")
    monkeypatch.setenv("HOME", str(home))
    origin, repo = make_origin_and_clone(tmp_path)                       # commits through sh()
    assert sh(["git", "log", "-1", "--format=%an <%ae>"], repo) == "Fixture <fixture@example.com>"
    wt = tmp_path / "wt"
    create_worktree(repo, wt, "meld/1-x", "main")                         # gitops.git inherits the same environment
    (wt / "f.txt").write_text("x\n")
    git(["add", "f.txt"], wt)
    git(["commit", "-q", "-m", "x"], wt)
    assert git(["log", "-1", "--format=%an"], wt) == "Fixture"
    assert git(["config", "--show-origin", "--get", "user.name"], wt).split("\t")[0].endswith("/.git/config")
