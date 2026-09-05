import json
from datetime import datetime, timezone

from meld.housekeeping import CI_RED_STATE, IGNORED_STATE, OFF_PROTOCOL_STATE, ci_red_sweep, flag_ignored_comments, off_protocol_sweep
from meld.status import build_status
from tests.productfixtures import R, fixture, make_product
from tests.test_collect import routes_for_labels

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
RED = {"total_count": 1, "check_runs": [{"name": "ci", "status": "completed", "conclusion": "failure"}]}


def no_events(fake):
    for n in (11, 12, 13, 14):
        fake.route("GET", R + f"issues/{n}/events", [])


def test_ci_red_returns_the_authors_issue_to_in_progress_once_per_head(tmp_path):
    fake, codex, repo, origin = make_product(tmp_path, group="codex")      # PR #20 (issue #13, in review) is codex's
    fake.route("GET", R + "commits/abc123/check-runs", RED)
    routes_for_labels(fake, 13)
    fake.route("DELETE", R + "issues/13/labels/meld%3Ain-review", [])
    _, board = build_status(codex)
    group = codex.cfg.groups["codex"]
    out = ci_red_sweep(codex, board, group)
    assert out == [{"pr": 20, "issue": 13, "head": "abc123"}]
    assert [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/labels"] == [{"labels": ["meld:in-progress"]}]
    comment = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/comments"][-1]
    assert "CI is red on PR #20" in comment and "abc123"[:7] in comment
    assert json.loads((codex.store.state_dir / CI_RED_STATE).read_text()) == {"20": "abc123"}
    assert ci_red_sweep(codex, board, group) == []                               # same head: nothing more
    fake2, claude, *_ = make_product(tmp_path / "c")                            # claude did not author #20: not its job
    fake2.route("GET", R + "commits/abc123/check-runs", RED)
    _, board2 = build_status(claude)
    assert ci_red_sweep(claude, board2, claude.cfg.groups["claude"]) == []


def test_ignored_comments_are_flagged_once_by_the_first_group(tmp_path):
    fake, claude, repo, origin = make_product(tmp_path)                      # fixture comment 103 is by randouser (unauthorized)
    _, board = build_status(claude)
    out = flag_ignored_comments(claude, board, claude.cfg.groups["claude"])
    assert out == {"flagged": [103], "posted": True}
    coord = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"][-1]
    assert coord.startswith("[claude:pm] IGNORED-COMMENT\n") and "- comment 103 by `randouser`" in coord
    assert json.loads((claude.store.state_dir / IGNORED_STATE).read_text()) == {"last_id": 103}
    assert flag_ignored_comments(claude, board, claude.cfg.groups["claude"]) == {"flagged": [], "posted": False}
    fake2, codex, *_ = make_product(tmp_path / "x", group="codex")
    _, board2 = build_status(codex)
    assert flag_ignored_comments(codex, board2, codex.cfg.groups["codex"]) == {"flagged": [103], "posted": False}   # codex only advances its cursor
    assert not [c for c in fake2.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]


def test_off_protocol_changes_by_unknown_actors_are_reported_once(tmp_path):
    fake, claude, repo, origin = make_product(tmp_path)
    no_events(fake)
    fake.route("GET", R + "issues/13/events", [
        {"id": 9001, "event": "labeled", "actor": {"login": "randouser"}, "label": {"name": "meld:ready"}},
        {"id": 9002, "event": "assigned", "actor": {"login": "meld-codex-bot"}, "assignee": {"login": "meld-codex-bot"}},
        {"id": 9003, "event": "unassigned", "actor": {"login": "someone-else"}, "assignee": {"login": "meld-codex-bot"}},
        {"id": 9004, "event": "commented", "actor": {"login": "someone-else"}}])
    _, board = build_status(claude)
    out = off_protocol_sweep(claude, board, claude.cfg.groups["claude"])
    assert [(o["event_id"], o["actor"], o["event"]) for o in out] == [(9001, "randouser", "labeled"), (9003, "someone-else", "unassigned")]
    posts = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]
    assert len(posts) == 2 and posts[0].startswith("[claude:pm] PROTOCOL-VIOLATION\n") and "off-protocol change on #13: randouser labeled meld:ready" in posts[0]
    assert json.loads((claude.store.state_dir / OFF_PROTOCOL_STATE).read_text()) == {"13": 9004}
    assert off_protocol_sweep(claude, board, claude.cfg.groups["claude"]) == []


def test_state_file_tolerates_bad_json_and_contains_write_failures(tmp_path):
    """Ruling: a missing/corrupt/non-dict state file reads as empty rather than raising, and a write failure
    (state_dir occupied by a plain file, so `_save`'s mkdir cannot create it) is contained -- the sweep still
    returns its findings and nothing raises out of it."""
    def flagged(ctx, board):
        return flag_ignored_comments(ctx, board, ctx.cfg.groups["claude"])

    fake, claude, *_ = make_product(tmp_path / "corrupt")
    _, board = build_status(claude)
    claude.store.state_dir.mkdir(parents=True, exist_ok=True)
    (claude.store.state_dir / IGNORED_STATE).write_text("{not json at all")
    assert flagged(claude, board) == {"flagged": [103], "posted": True}          # corrupt JSON read as empty

    fake2, claude2, *_ = make_product(tmp_path / "nondict")
    _, board2 = build_status(claude2)
    claude2.store.state_dir.mkdir(parents=True, exist_ok=True)
    (claude2.store.state_dir / IGNORED_STATE).write_text("[1, 2, 3]")
    assert flagged(claude2, board2) == {"flagged": [103], "posted": True}        # valid but non-dict JSON read as empty

    fake3, claude3, *_ = make_product(tmp_path / "unwritable")
    _, board3 = build_status(claude3)
    claude3.store.state_dir.parent.mkdir(parents=True, exist_ok=True)
    claude3.store.state_dir.write_text("a file occupying the state directory's path")
    assert flagged(claude3, board3) == {"flagged": [103], "posted": True}        # posts fine; the write fails silently
    assert not (claude3.store.state_dir / IGNORED_STATE).exists()                # nothing was persisted


def test_off_protocol_sweep_skips_an_issue_whose_events_call_errors(tmp_path):
    """Ruling: a GitHubError fetching one issue's events (e.g. a transient 5xx) is contained to that issue --
    it must not abort the sweep for the rest of the open issues in the milestone."""
    fake, claude, repo, origin = make_product(tmp_path)
    fake.route("GET", R + "issues/11/events", {"message": "server error"}, status=500)
    fake.route("GET", R + "issues/13/events", [
        {"id": 9001, "event": "labeled", "actor": {"login": "randouser"}, "label": {"name": "meld:ready"}}])
    _, board = build_status(claude)
    out = off_protocol_sweep(claude, board, claude.cfg.groups["claude"])
    assert [(o["event_id"], o["actor"], o["event"]) for o in out] == [(9001, "randouser", "labeled")]
    assert json.loads((claude.store.state_dir / OFF_PROTOCOL_STATE).read_text()) == {"13": 9001}   # #11's failure left no cursor
