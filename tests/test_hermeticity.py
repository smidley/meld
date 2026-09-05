"""The suite must not depend on the host: no ~/.claude scan through the CLI, no host gitconfig in any git call, and no
Slack webhook or group token read from the operator's shell."""
import os
import subprocess
import sys
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


def test_process_env_carries_no_webhook_or_group_tokens():
    # the autouse _hermetic_env fixture scrubbed them: a tick or notify that reaches os.environ finds nothing to post to
    assert "MELD_SLACK_DM_WEBHOOK" not in os.environ
    assert not [k for k in os.environ if k.startswith("MELD_TOKEN_")]


def test_the_suite_scrubs_the_webhook_and_tokens_from_the_process_environment(tmp_path):
    """A child pytest loads tests/conftest.py as a plugin and runs one probe with the operator's webhook and a group token set
    in ITS environment: the autouse fixture must have removed both before the probe body runs (this is the shell every
    developer runs the suite from, so it is the one that matters)."""
    probe = tmp_path / "test_probe.py"
    probe.write_text("import os\n\n\ndef test_probe():\n    assert 'MELD_SLACK_DM_WEBHOOK' not in os.environ\n"
                     "    assert not [k for k in os.environ if k.startswith('MELD_TOKEN_')]\n")
    root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "MELD_SLACK_DM_WEBHOOK": "https://hooks.slack.test/never", "MELD_TOKEN_CLAUDE": "leaked-from-the-shell"}
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "tests.conftest",
                           "-c", str(root / "pyproject.toml"), "--rootdir", str(root), str(probe)],
                          cwd=root, env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
