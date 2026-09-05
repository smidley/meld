import json
from datetime import datetime, timezone

from meld.github import GitHubError
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
    # Fix round 2: state tracks both sub-steps per PR (not a bare head string) so a partial failure can be retried.
    assert json.loads((codex.store.state_dir / CI_RED_STATE).read_text()) == {"20": {"head": "abc123", "commented": True, "swapped": True}}
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
    # I2 (fix round 1): actor/what are backtick-quoted in the posted body so a label or login can't break formatting.
    assert len(posts) == 2 and posts[0].startswith("[claude:pm] PROTOCOL-VIOLATION\n") and "off-protocol change on #13: `randouser` labeled `meld:ready`" in posts[0]
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


def test_off_protocol_sweep_persists_per_issue_and_retries_only_the_failed_one(tmp_path):
    """C2: a per-issue cursor advances only up to an event whose post succeeded, and is persisted as soon as that
    issue is done -- so a failure posting the second of two violations still keeps the first issue's result, and
    a retry re-processes only the issue that failed (never losing the first, never duplicating it)."""
    fake, claude, repo, origin = make_product(tmp_path)
    fake.route("GET", R + "issues/11/events", [])
    fake.route("GET", R + "issues/14/events", [])
    fake.route("GET", R + "issues/12/events", [
        {"id": 8001, "event": "labeled", "actor": {"login": "randouser"}, "label": {"name": "meld:blocked"}}])
    fake.route("GET", R + "issues/13/events", [
        {"id": 9001, "event": "labeled", "actor": {"login": "randouser"}, "label": {"name": "meld:ready"}}])
    _, board = build_status(claude)
    group = claude.cfg.groups["claude"]

    real_create_comment = claude.gh.create_comment
    calls = {"n": 0}

    def flaky(number, body):
        calls["n"] += 1
        if calls["n"] == 2:   # issue #12 is processed first (lower number) and posts fine; #13 is second and fails
            raise GitHubError("boom", status=500)
        return real_create_comment(number, body)

    claude.gh.create_comment = flaky
    out = off_protocol_sweep(claude, board, group)
    ok = next(o for o in out if o["issue"] == 12)
    failed = next(o for o in out if o["issue"] == 13)
    assert ok == {"issue": 12, "event_id": 8001, "actor": "randouser", "event": "labeled", "what": "meld:blocked"}
    assert failed["event_id"] == 9001 and failed["error"] == "GitHubError"
    assert json.loads((claude.store.state_dir / OFF_PROTOCOL_STATE).read_text()) == {"12": 8001}   # #13 not persisted

    claude.gh.create_comment = real_create_comment    # the transient failure is now "fixed"
    retry = off_protocol_sweep(claude, board, group)
    assert retry == [{"issue": 13, "event_id": 9001, "actor": "randouser", "event": "labeled", "what": "meld:ready"}]
    assert json.loads((claude.store.state_dir / OFF_PROTOCOL_STATE).read_text()) == {"12": 8001, "13": 9001}


def test_flag_ignored_comments_retries_a_failed_post_without_duplicating(tmp_path):
    """C2: the posting (first) group's cursor advances only once its post succeeds, so a failed post leaves the
    cursor unchanged and a retry posts exactly once."""
    fake, claude, repo, origin = make_product(tmp_path)
    _, board = build_status(claude)
    group = claude.cfg.groups["claude"]
    fake.route("POST", R + "issues/10/comments", {"message": "boom"}, status=500)
    out = flag_ignored_comments(claude, board, group)
    assert out["flagged"] == [103] and out["posted"] is False and out["errors"] == ["GitHubError"]
    state_path = claude.store.state_dir / IGNORED_STATE
    assert not state_path.exists()                                    # nothing persisted on a failed post

    fake.route("POST", R + "issues/10/comments", {"id": 399, "html_url": "u10"})   # fixed
    retry = flag_ignored_comments(claude, board, group)
    assert retry == {"flagged": [103], "posted": True}
    assert json.loads(state_path.read_text()) == {"last_id": 103}


def test_flag_ignored_comments_filters_by_author_not_header_validity(tmp_path):
    """I1: filter on the comment's actual author, not on whether it parsed as an authorized header -- a listed
    human's header-less comment is not flagged, but an unknown author's well-formed header still is."""
    fake, claude, repo, origin = make_product(tmp_path)
    comments = fixture("comments_10.json") + [
        {"id": 105, "user": {"login": "empowerScott"}, "created_at": "2026-09-03T09:07:00Z",
         "body": "looks fine to me, no header here"},                              # listed human, no header at all
        {"id": 106, "user": {"login": "randouser"}, "created_at": "2026-09-03T09:08:00Z",
         "body": "[claude:pm] STATUS\n\nfaking a status update"},                   # unknown author, well-formed header
    ]
    fake.route("GET", R + "issues/10/comments", comments)
    _, board = build_status(claude)
    out = flag_ignored_comments(claude, board, claude.cfg.groups["claude"])
    assert out["flagged"] == [103, 106]   # 105 (a listed human) is not flagged; 106 (unknown author) is


def test_off_protocol_violation_body_neutralises_a_leading_at_mention(tmp_path):
    """I2: `actor`/`what` are rendered in backtick code spans with a leading '@' defused, so a label like
    "@here please" can never read as a bare, auto-linkable mention in the posted comment."""
    fake, claude, repo, origin = make_product(tmp_path)
    fake.route("GET", R + "issues/13/events", [
        {"id": 9101, "event": "labeled", "actor": {"login": "randouser"}, "label": {"name": "@here please"}}])
    _, board = build_status(claude)
    off_protocol_sweep(claude, board, claude.cfg.groups["claude"])
    posts = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]
    assert posts
    body = posts[-1]
    assert "@here" not in body                  # never appears as a bare, auto-linkable mention
    assert "please" in body and "`" in body      # the label text itself still reaches the comment, in a code span


def test_ci_red_sweep_contains_a_write_failure(tmp_path, monkeypatch):
    """Minor: a failed state-file write (disk full, permissions, ...) must not stop the sweep from returning
    the findings it already computed."""
    fake, codex, repo, origin = make_product(tmp_path, group="codex")
    fake.route("GET", R + "commits/abc123/check-runs", RED)
    routes_for_labels(fake, 13)
    fake.route("DELETE", R + "issues/13/labels/meld%3Ain-review", [])
    _, board = build_status(codex)
    group = codex.cfg.groups["codex"]

    def boom(path, text):
        raise OSError("disk full")

    monkeypatch.setattr("meld.housekeeping.write_atomic", boom)
    out = ci_red_sweep(codex, board, group)
    assert out == [{"pr": 20, "issue": 13, "head": "abc123"}]           # findings returned despite the failed write
    assert not (codex.store.state_dir / CI_RED_STATE).exists()         # nothing was actually persisted


def test_off_protocol_sweep_contains_a_write_failure(tmp_path, monkeypatch):
    """Minor: a failed state-file write must not stop off_protocol_sweep from returning what it found either."""
    fake, claude, repo, origin = make_product(tmp_path)
    fake.route("GET", R + "issues/13/events", [
        {"id": 9001, "event": "labeled", "actor": {"login": "randouser"}, "label": {"name": "meld:ready"}}])
    _, board = build_status(claude)

    def boom(path, text):
        raise OSError("disk full")

    monkeypatch.setattr("meld.housekeeping.write_atomic", boom)
    out = off_protocol_sweep(claude, board, claude.cfg.groups["claude"])
    assert [(o["event_id"], o["actor"], o["event"]) for o in out] == [(9001, "randouser", "labeled")]
    assert not (claude.store.state_dir / OFF_PROTOCOL_STATE).exists()


def test_ci_red_sweep_recovers_when_the_swap_fails_after_a_successful_comment(tmp_path):
    """Fix round 2 (C2, scenario 1): the comment is posted before the label swap, so a swap failure after a
    successful comment leaves the issue still `in_review` on a freshly rebuilt board -- the retry must complete
    the swap without reposting the comment."""
    fake, codex, repo, origin = make_product(tmp_path, group="codex")
    fake.route("GET", R + "commits/abc123/check-runs", RED)
    # #13's labels endpoints are deliberately left unrouted: the swap fails the first time round.
    _, board = build_status(codex)
    group = codex.cfg.groups["codex"]
    out = ci_red_sweep(codex, board, group)
    assert out == [{"pr": 20, "issue": 13, "head": "abc123", "error": "GitHubError", "commented": True, "swapped": False}]
    comments = [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/comments"]
    assert len(comments) == 1
    assert json.loads((codex.store.state_dir / CI_RED_STATE).read_text()) == {"20": {"head": "abc123", "commented": True, "swapped": False}}

    routes_for_labels(fake, 13)
    fake.route("DELETE", R + "issues/13/labels/meld%3Ain-review", [])   # the transient failure is now "fixed"
    _, board2 = build_status(codex)                        # a genuinely fresh board -- still shows in_review
    assert board2.issues[13].status == "in_review"
    retry = ci_red_sweep(codex, board2, group)
    assert retry == [{"pr": 20, "issue": 13, "head": "abc123"}]
    comments_after = [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/comments"]
    assert len(comments_after) == 1                        # no second comment posted
    assert json.loads((codex.store.state_dir / CI_RED_STATE).read_text()) == {"20": {"head": "abc123", "commented": True, "swapped": True}}


def test_ci_red_sweep_recovers_when_the_comment_fails(tmp_path):
    """Fix round 2 (C2, scenario 2): a failed comment leaves nothing recorded (the swap is never attempted before
    the comment succeeds), so a retry against a fresh board posts the comment once and swaps once."""
    fake, codex, repo, origin = make_product(tmp_path, group="codex")
    fake.route("GET", R + "commits/abc123/check-runs", RED)
    routes_for_labels(fake, 13)
    fake.route("DELETE", R + "issues/13/labels/meld%3Ain-review", [])
    fake.route("POST", R + "issues/13/comments", {"message": "boom"}, status=500)   # comment fails first
    _, board = build_status(codex)
    group = codex.cfg.groups["codex"]
    out = ci_red_sweep(codex, board, group)
    assert out == [{"pr": 20, "issue": 13, "head": "abc123", "error": "GitHubError", "commented": False, "swapped": False}]
    assert not (codex.store.state_dir / CI_RED_STATE).exists()                                        # nothing recorded
    assert not [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/labels"]  # swap never attempted

    fake.route("POST", R + "issues/13/comments", {"id": 313, "html_url": "u13"})   # fixed
    _, board2 = build_status(codex)             # a genuinely fresh board -- unchanged, still shows in_review
    retry = ci_red_sweep(codex, board2, group)
    assert retry == [{"pr": 20, "issue": 13, "head": "abc123"}]
    comments = [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/comments"]
    assert len(comments) == 2                   # one failed attempt (first call) + one that succeeded (retry)
    labels = [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/labels"]
    assert len(labels) == 1                     # swap attempted exactly once, in the retry
    assert json.loads((codex.store.state_dir / CI_RED_STATE).read_text()) == {"20": {"head": "abc123", "commented": True, "swapped": True}}


def test_ci_red_sweep_completes_an_outstanding_comment_after_the_swap_already_happened(tmp_path):
    """Fix round 2 (C2, scenario 3): even once the swap has already moved the issue out of in_review (so the
    in_review guard alone would miss it), an incomplete record for the current exact head is still completed --
    this is the bug the reviewer found (state seeded to simulate a swap that really happened but was never
    recorded as commented)."""
    fake, codex, repo, origin = make_product(tmp_path, group="codex")
    fake.route("GET", R + "commits/abc123/check-runs", RED)
    issues = fixture("issues.json")
    for i in issues:
        if i["number"] == 13:
            i["labels"] = [l for l in i["labels"] if l["name"] != "meld:in-review"] + [{"name": "meld:in-progress"}]
    fake.route("GET", R + "issues", issues)
    state_dir = codex.store.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / CI_RED_STATE).write_text(json.dumps({"20": {"head": "abc123", "commented": False, "swapped": True}}))
    _, board = build_status(codex)
    assert board.issues[13].status == "in_progress"    # the swap really did happen
    group = codex.cfg.groups["codex"]
    out = ci_red_sweep(codex, board, group)
    assert out == [{"pr": 20, "issue": 13, "head": "abc123"}]
    comments = [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/comments"]
    assert len(comments) == 1
    assert not [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/13/labels"]  # already swapped: not re-attempted
    assert json.loads((state_dir / CI_RED_STATE).read_text()) == {"20": {"head": "abc123", "commented": True, "swapped": True}}


def test_ci_red_sweep_takes_no_action_on_a_fully_handled_head(tmp_path):
    """Fix round 2 (C2, scenario 4): a fully-handled head is left alone even once the issue has moved on to
    in_progress -- no comment, no label swap, nothing persisted again."""
    fake, codex, repo, origin = make_product(tmp_path, group="codex")
    fake.route("GET", R + "commits/abc123/check-runs", RED)
    issues = fixture("issues.json")
    for i in issues:
        if i["number"] == 13:
            i["labels"] = [l for l in i["labels"] if l["name"] != "meld:in-review"] + [{"name": "meld:in-progress"}]
    fake.route("GET", R + "issues", issues)
    state_dir = codex.store.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / CI_RED_STATE).write_text(json.dumps({"20": {"head": "abc123", "commented": True, "swapped": True}}))
    _, board = build_status(codex)
    group = codex.cfg.groups["codex"]
    out = ci_red_sweep(codex, board, group)
    assert out == []
    assert not [c for c in fake.calls if c.method == "POST" and c.endpoint in (R + "issues/13/comments", R + "issues/13/labels")]
