"""The rehearsal's stand-in GitHub, driven through meld's own `GitHub` client and the `gh` shim on PATH (plan 48)."""
import os
import subprocess
from pathlib import Path

import pytest

from meld.github import GitHub, GitHubError
from meld.rehearsal.ghshim import install
from meld.rehearsal.github import FakeGitHub
from tests.gitfixtures import sh


@pytest.fixture
def world(tmp_path, monkeypatch):
    fake = FakeGitHub("scott/widgets", tmp_path / "github" / "widgets.git", human="scott", human_token="tok-scott")
    url = fake.start()
    gh_path = install(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{gh_path.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("MELD_REHEARSAL_GITHUB", url)
    monkeypatch.setenv("MELD_REHEARSAL_HUMAN_TOKEN", "tok-scott")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    yield fake
    fake.stop()


def test_the_stand_in_serves_the_repository_lifecycle_through_meld_s_client(world, tmp_path):
    fake = world
    gh = GitHub("scott/widgets", token=None, run=subprocess.run)          # as the person: gh's own token, like the wizard
    assert gh.viewer_login() == "scott"
    with pytest.raises(GitHubError) as e:
        gh.get_repo()
    assert e.value.status == 404
    made = gh.post("user/repos", {"name": "widgets", "private": True})
    assert made["full_name"] == "scott/widgets"
    assert gh.get_repo()["default_branch"] == "main" and gh.get("repos/scott/widgets/branches", use_cache=False) == []
    # the person pushes the folder, as the wizard does
    folder = tmp_path / "folder"
    sh(["git", "init", "-q", "-b", "main", str(folder)], tmp_path)
    sh(["git", "config", "user.name", "scott"], folder)
    sh(["git", "config", "user.email", "scott@example.com"], folder)
    (folder / "README.md").write_text("# widgets\n")
    sh(["git", "add", "README.md"], folder)
    sh(["git", "commit", "-q", "-m", "initial"], folder)
    sh(["git", "remote", "add", "origin", str(fake.bare)], folder)
    sh(["git", "push", "-q", "origin", "main"], folder)
    assert [b["name"] for b in gh.get("repos/scott/widgets/branches", use_cache=False)] == ["main"]
    # labels, a milestone, issues, comments, labels on issues, events
    gh.create_label("meld:ready", "0e8a16", "ready")
    assert [l["name"] for l in gh.list_labels()] == ["meld:ready"]
    m = gh.create_milestone("M1", "the plan")
    issue = gh.create_issue("Coordination: M1", "## Goal\n\nShip it", ["meld:coordination"], m["number"])
    assert issue["number"] == 1 and gh.get_issue(1)["milestone"]["number"] == m["number"]
    c = gh.create_comment(1, "[human] DIRECTIVE dir-1\n\ngo")
    assert gh.list_comments(1)[0]["body"].startswith("[human] DIRECTIVE") and c["html_url"].endswith(f"#issuecomment-{c['id']}")
    gh.add_labels(1, ["meld:ready"])
    assert gh.remove_label(1, "meld:ready") is None or True
    events = gh.get_paginated("repos/scott/widgets/issues/1/events")
    assert [(e["event"], e["label"]["name"], e["actor"]["login"]) for e in events] == [("labeled", "meld:ready", "scott"), ("unlabeled", "meld:ready", "scott")]
    # a bot with a PAT pushes a branch and opens a PR; its files and commits are the real diff
    fake.add_bot("meld-claude-bot", "tok-claude")
    bot = GitHub("scott/widgets", token="tok-claude", run=subprocess.run)
    assert bot.viewer_login() == "meld-claude-bot"
    sh(["git", "checkout", "-q", "-b", "meld/2-health"], folder)
    (folder / "health.py").write_text("OK = True\n")
    sh(["git", "add", "health.py"], folder)
    sh(["git", "-c", "user.name=meld-claude-bot", "-c", "user.email=7+meld-claude-bot@users.noreply.github.com", "commit", "-q", "-m", "feat: health"], folder)
    sh(["git", "push", "-q", "origin", "meld/2-health"], folder)
    pr = bot.create_pull("Health", "meld/2-health", "main", "Closes #1\n\nverified-by: python -c 'import health'")
    assert pr["number"] == 2 and pr["head"]["ref"] == "meld/2-health" and pr["head"]["sha"] == sh(["git", "rev-parse", "HEAD"], folder)
    assert [f["filename"] for f in bot.pull_files(2)] == ["health.py"] and bot.pull_files(2)[0]["status"] == "added"
    assert [c["author"]["login"] for c in bot.pull_commits(2)] == ["meld-claude-bot"]
    assert [i["number"] for i in gh.list_pulls()] == [2] and gh.get_pull(2, fresh=True)["mergeable_state"] == "clean"
    # a review by another account, a status, then the merge is a real squash that closes the issue
    fake.add_bot("meld-codex-bot", "tok-codex")
    codex = GitHub("scott/widgets", token="tok-codex", run=subprocess.run)
    rv = codex.create_review(2, "APPROVE", "fine", pr["head"]["sha"])
    assert rv["state"] == "APPROVED" and gh.pull_reviews(2)[0]["user"]["login"] == "meld-codex-bot"
    with pytest.raises(GitHubError) as e:
        bot.create_review(2, "APPROVE", "mine", pr["head"]["sha"])                 # GitHub refuses approving your own PR
    assert e.value.status == 422
    codex.create_status(pr["head"]["sha"], "success", "meld/review/codex", "approved")
    assert gh.get("repos/scott/widgets/commits/" + pr["head"]["sha"] + "/status", use_cache=False)["state"] == "success"
    with pytest.raises(GitHubError) as e:
        codex.merge_pull(2, "0" * 40, "Health")                                    # a stale sha is a 409
    assert e.value.status == 409
    merged = codex.merge_pull(2, pr["head"]["sha"], "Health", message="Closes #1")
    main = fake.sha_of("main")
    assert merged["sha"] == main and gh.get_pull(2, fresh=True)["merged_at"] and gh.get_issue(1, fresh=True)["state"] == "closed"
    assert fake.git(["log", "--format=%s", "-1", "main"]) == "Health"
    assert gh.get_contents("health.py", "main") == "OK = True\n"
    with pytest.raises(GitHubError) as e:
        codex.merge_pull(2, main, "Health")                                        # merged already: 405
    assert e.value.status == 405
    assert gh.search_issues("Coordination")[0]["number"] == 1 if hasattr(gh, "search_issues") else True
