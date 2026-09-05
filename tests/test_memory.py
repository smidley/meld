import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from meld.actions import Refused
from meld.github import GitHubError
from meld.memory import (DECISIONS_DIR, LESSONS_FILE, MARKER_RE, content_hash, decide, next_decision_number, recall, recall_snippets,
                         remember, render_decision, render_lesson)
from tests.gitfixtures import sh
from tests.productfixtures import R, fixture, make_product

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
DECISION_THREAD = [
    {"id": 201, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T10:10:00Z", "body": "[claude] PROPOSE dec-1\n\nUse PostgreSQL for the ledger.\n\nverified-by: read docs/adr/0001.md"},
    {"id": 202, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T10:11:00Z", "body": "[codex] AGREE dec-1\n\nfine"},
    {"id": 203, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T10:12:00Z", "html_url": "https://github.com/acme/widgets/issues/10#issuecomment-203",
     "body": "[joint] DECISION dec-1\n\nUse PostgreSQL for the ledger.\n\nverified-by: read docs/adr/0001.md"},
]

DISAGREE_THREAD = [
    {"id": 201, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T10:10:00Z", "body": "[claude] PROPOSE dec-2\n\nUse PostgreSQL for the ledger.\n\nverified-by: read docs/adr/0001.md"},
    {"id": 202, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T10:11:00Z", "body": "[codex] DISSENT dec-2\n\nSQLite is enough."},
    {"id": 203, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T10:12:00Z",
     "body": "[joint] DECISION dec-2 DISAGREE\n\n## Positions\n- **claude** (proposal): Use PostgreSQL for the ledger.\n- **codex**: SQLite is enough.\n\nverified-by: read docs/adr/0001.md"},
]


def with_thread(fake, extra):
    fake.route("GET", R + "issues/10/comments", fixture("comments_10.json") + extra)


def test_render_and_hash_are_deterministic():
    from meld.grammar import Agreement
    a = Agreement("dec-1", "Use PostgreSQL for the ledger.\n\nverified-by: read docs/adr/0001.md", "claude", frozenset({"claude", "codex"}), {}, 0, "agreed", True, True, "DECISION", "meld-claude-bot")
    text = render_decision("dec-1", a, "https://x/issues/10#issuecomment-203", "2026-09-03")
    assert text.startswith("# Decision dec-1\n\n- Date: 2026-09-03\n- Agreed by: claude, codex\n- Source: https://x/issues/10#issuecomment-203\n\n## Decision\n\nUse PostgreSQL")
    assert content_hash(text) == content_hash(text) and len(content_hash(text)) == 64
    assert render_lesson("#13", "CI silently passed; pin the action version.", "claude", "2026-09-03").startswith("\n## 2026-09-03 — #13 (claude)\n\nCI silently passed")


def test_next_decision_number(tmp_path):
    # next_decision_number now reads origin's default branch (F3), not a bare directory, so it needs a real
    # product fixture; committing straight onto `repo`'s own checked-out main and pushing is enough here since
    # the point of *this* test is just the counting logic (the "reads origin, not the working copy" property
    # gets its own dedicated proof in test_next_decision_number_and_already_recorded_read_origin_not_the_working_copy).
    fake, ctx, repo, origin = make_product(tmp_path)
    assert next_decision_number(ctx) == 1
    (repo / ".meld" / "decisions").mkdir(parents=True)
    (repo / ".meld" / "decisions" / "0007-old.md").write_text("# Decision old\n")
    sh(["git", "add", ".meld/decisions/0007-old.md"], repo)
    sh(["git", "commit", "-q", "-m", "seed decision"], repo)
    sh(["git", "push", "-q", "origin", "main"], repo)
    assert next_decision_number(ctx) == 8


def test_decide_opens_a_memory_pr_and_requests_review(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    with_thread(fake, DECISION_THREAD)
    fake.route("POST", R + "pulls", {"number": 40, "html_url": "https://github.com/acme/widgets/pull/40"})
    out = decide(ctx, "dec-1", now=NOW)
    assert out["decided"] == "dec-1" and out["pr"] == 40 and out["path"] == ".meld/decisions/0001-dec-1.md"
    pull = [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "pulls"][0].body
    assert pull["title"] == "Decision dec-1" and pull["head"] == "meld/memory-dec-1" and pull["base"] == "main"
    m = MARKER_RE.search(pull["body"])
    assert m and m.group(1) == "decision" and m.group(2) == "dec-1" and m.group(3) == out["hash"]
    assert "verified-by: [joint] DECISION dec-1" in pull["body"]
    text = sh(["git", "show", "origin/meld/memory-dec-1:.meld/decisions/0001-dec-1.md"], repo)
    assert text.startswith("# Decision dec-1") and "Use PostgreSQL for the ledger." in text and content_hash(text + "\n") == out["hash"] or content_hash(text) == out["hash"]
    assert sh(["git", "log", "-1", "--format=%an", "origin/meld/memory-dec-1"], repo) == "meld-claude-bot"
    coord = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"][-1]
    assert coord.startswith("[claude:pm] REVIEW-REQUEST #40\n")
    assert not (tmp_path / "home" / "worktrees" / "claude" / "memory-dec-1").exists()   # scratch worktree removed


def test_decide_commit_is_never_signed_even_when_the_host_gitconfig_demands_it(tmp_path, monkeypatch):
    # The autouse _hermetic_git fixture already points GIT_CONFIG_GLOBAL at /dev/null; this monkeypatch
    # overrides it for this test only, pointing at a config that would make an unguarded `git commit`
    # fail or hang trying to sign with a key that doesn't exist.
    poisoned = tmp_path / "host.gitconfig"
    poisoned.write_text("[commit]\n\tgpgsign = true\n[user]\n\tsigningkey = DEADBEEF\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(poisoned))
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    with_thread(fake, DECISION_THREAD)
    fake.route("POST", R + "pulls", {"number": 40, "html_url": "https://github.com/acme/widgets/pull/40"})
    out = decide(ctx, "dec-1", now=NOW)
    assert out["decided"] == "dec-1"
    assert sh(["git", "log", "-1", "--format=%an", "origin/meld/memory-dec-1"], repo) == "meld-claude-bot"
    assert sh(["git", "log", "-1", "--format=%G?", "origin/meld/memory-dec-1"], repo) == "N"


EXISTING_THREAD = [
    {"id": 301, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T10:10:00Z", "body": "[claude] PROPOSE existing\n\nAlready decided.\n\nverified-by: read docs/adr/0002.md"},
    {"id": 302, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T10:11:00Z", "body": "[codex] AGREE existing\n\nfine"},
    {"id": 303, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T10:12:00Z", "body": "[joint] DECISION existing\n\nAlready decided.\n\nverified-by: read docs/adr/0002.md"},
]


def test_open_memory_pr_removes_the_worktree_even_when_create_pull_fails(tmp_path):
    # F1: a failure between the push and the PR being opened must not leak the scratch worktree.
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    with_thread(fake, DECISION_THREAD)
    fake.route("POST", R + "pulls", {"message": "boom"}, status=500)
    with pytest.raises(GitHubError):
        decide(ctx, "dec-1", now=NOW)
    listing = sh(["git", "worktree", "list", "--porcelain"], repo)
    assert "memory-dec-1" not in listing
    assert not (tmp_path / "home" / "worktrees" / "claude" / "memory-dec-1").exists()


def test_second_decide_is_refused_while_memory_pr_open(tmp_path):
    # F2: once the first memory PR is open, GitHub reflecting it back on the next read must refuse a repeat.
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    with_thread(fake, DECISION_THREAD)
    fake.route("POST", R + "pulls", {"number": 40, "html_url": "https://github.com/acme/widgets/pull/40"})
    out = decide(ctx, "dec-1", now=NOW)
    body = (f"Records the agreed decision `dec-1`.\n\nmeld-memory: decision dec-1 {out['hash']}\n"
            f"verified-by: [joint] DECISION dec-1 (https://github.com/acme/widgets/issues/10#issuecomment-203)")
    pulls = fixture("pulls.json") + [{
        "number": 40, "title": "Decision dec-1", "body": body, "state": "open", "merged_at": None,
        "user": {"login": "meld-claude-bot"}, "head": {"ref": "meld/memory-dec-1", "sha": "abc123"}, "labels": [],
    }]
    fake.route("GET", R + "pulls", pulls)
    fake.route("GET", R + "pulls/40/reviews", [])
    fake.route("GET", R + "pulls/40/commits", [])
    fake.route("GET", R + "pulls/40/files", [])
    with pytest.raises(Refused, match="already recorded"):
        decide(ctx, "dec-1", now=NOW)


def test_next_decision_number_and_already_recorded_read_origin_not_the_working_copy(tmp_path):
    # F3: both checks must read origin's default branch, not ctx.repo_root's own (never-pulled) working copy.
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    with_thread(fake, EXISTING_THREAD)
    # Commit the pre-existing decision file from a SEPARATE clone and push it, so `repo`'s own working copy
    # never has the file locally at all -- proving the checks read origin, not the checkout.
    second = tmp_path / "second-clone"
    sh(["git", "clone", "-q", str(origin), str(second)], tmp_path)
    sh(["git", "config", "user.name", "Fixture"], second)
    sh(["git", "config", "user.email", "fixture@example.com"], second)
    (second / ".meld" / "decisions").mkdir(parents=True)
    (second / ".meld" / "decisions" / "0007-existing.md").write_text("# Decision existing\n")
    sh(["git", "add", ".meld/decisions/0007-existing.md"], second)
    sh(["git", "commit", "-q", "-m", "seed decision"], second)
    sh(["git", "push", "-q", "origin", "main"], second)
    assert not (repo / ".meld").exists()   # repo's own working copy never saw this file
    assert next_decision_number(ctx) == 8
    with pytest.raises(Refused, match="already recorded"):
        decide(ctx, "existing", now=NOW)


def test_decide_refusals(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    with pytest.raises(Refused, match="no verified \\[joint\\] DECISION"):
        decide(ctx, "dec-9", now=NOW)
    forged = DECISION_THREAD[:2] + [{**DECISION_THREAD[2], "user": {"login": "meld-codex-bot"}}]
    with_thread(fake, forged)
    with pytest.raises(Refused, match="not verified"):
        decide(ctx, "dec-1", now=NOW)
    fake2, codex, *_ = make_product(tmp_path / "c", group="codex", role="orchestrator")
    with_thread(fake2, DECISION_THREAD)
    with pytest.raises(Refused, match="proposer"):
        decide(codex, "dec-1", now=NOW)
    fake3, human, *_ = make_product(tmp_path / "h", group=None)
    with_thread(fake3, DECISION_THREAD)
    with pytest.raises(Refused, match="orchestrator or PM"):
        decide(human, "dec-1", now=NOW)


def test_an_amend_after_the_joint_cannot_make_the_amender_the_proposer(tmp_path):
    # F1: once the [joint] DECISION is posted the round is frozen, so codex's later AMEND changes neither the text nor
    # the proposer -- without the freeze, codex would become the proposer of its own text under claude's verified joint.
    fake, codex, repo, origin = make_product(tmp_path, group="codex", role="orchestrator")
    with_thread(fake, DECISION_THREAD + [{"id": 204, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T10:13:00Z",
                                          "body": "[codex] AMEND dec-1\n\nUse MySQL for the ledger.\n\nverified-by: nothing"}])
    fake.route("POST", R + "pulls", {"number": 40, "html_url": "https://github.com/acme/widgets/pull/40"})
    with pytest.raises(Refused, match="only the proposer \\(claude\\)"):
        decide(codex, "dec-1", now=NOW)
    assert not [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "pulls"]


def test_decide_refuses_a_round_that_ended_in_disagree(tmp_path):
    # F2: a verified [joint] DECISION ... DISAGREE records positions for the human, never a decision the groups took.
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    with_thread(fake, DISAGREE_THREAD)
    fake.route("POST", R + "pulls", {"number": 40, "html_url": "https://github.com/acme/widgets/pull/40"})
    with pytest.raises(Refused, match="dec-2 ended in DISAGREE; the human decides"):
        decide(ctx, "dec-2", now=NOW)
    assert not [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "pulls"]


def test_remember_appends_a_lesson_and_refuses_unresolved_items(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    fake.route("POST", R + "pulls", {"number": 41, "html_url": "https://github.com/acme/widgets/pull/41"})
    with pytest.raises(Refused, match="still needs_human"):
        remember(ctx, "#14", "we needed the key earlier", now=NOW)
    out = remember(ctx, "#13", "CI silently passed; pin the action version.", now=NOW)
    assert out["remembered"] == "#13" and out["pr"] == 41
    pull = [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "pulls"][0].body
    assert pull["title"] == "Lesson from #13" and MARKER_RE.search(pull["body"]).group(1) == "lesson"
    text = sh(["git", "show", f"origin/{pull['head']}:.meld/memory/lessons.md"], repo)
    assert text.startswith("# Lessons\n") and "pin the action version" in text


def test_recall_ranks_local_files_and_coordination_issues(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    (repo / ".meld" / "decisions").mkdir(parents=True)
    (repo / ".meld" / "decisions" / "0001-dec-1.md").write_text("# Decision dec-1\n\n## Decision\n\nUse PostgreSQL for the ledger.\n")
    (repo / ".meld" / "memory").mkdir()
    (repo / ".meld" / "memory" / "lessons.md").write_text("# Lessons\n\n## 2026-09-01 — #7 (codex)\n\nPostgreSQL migrations need a lock timeout.\n")
    fake.route("GET", "search/issues", {"items": [{"number": 5, "title": "Coordination: M0 ledger", "html_url": "u5"}]})
    hits = recall(ctx, "postgresql ledger")
    assert [h["kind"] for h in hits] == ["decision", "lesson", "coordination"]
    assert hits[0]["score"] == 2 and hits[0]["path"].endswith("0001-dec-1.md") and hits[2]["number"] == 5
    assert recall_snippets(repo, "ledger migrations") and "lock timeout" in recall_snippets(repo, "ledger migrations")[0]
    assert recall_snippets(repo, "nothing matches here") == []
