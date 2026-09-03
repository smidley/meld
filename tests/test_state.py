import json

from moot.state import StateStore, board_digest, diff_digest, moot_home
from tests.factories import CFG2, mk_board, mk_issue, mk_pr, mk_run


def test_moot_home_default_and_override(tmp_path):
    assert moot_home({}).name == ".moot"
    assert moot_home({"MOOT_HOME": str(tmp_path)}) == tmp_path


def test_write_and_load_runs_with_alive_probe(tmp_path):
    store = StateStore(tmp_path, "claude")
    rec = mk_run("claude", 12)
    path = store.write_run(rec)
    assert path == tmp_path / "runs" / "claude" / "12.json"
    data = json.loads(path.read_text())
    assert data["pid"] == 4242 and "alive" not in data
    runs = store.load_runs(alive=lambda pid: False)
    assert runs["claude"][12].alive is False and runs["claude"][12].finished
    runs = store.load_runs(alive=lambda pid: True)
    assert runs["claude"][12].alive is True


def test_load_spend_counts_active_and_archived_by_tier(tmp_path):
    store = StateStore(tmp_path, "claude")
    store.write_run(mk_run("claude", 1, tier="small"))
    store.write_run(mk_run("claude", 2, tier="medium"))
    archive = tmp_path / "runs" / "claude" / "archive"
    archive.mkdir(parents=True)
    (archive / "3.json").write_text(json.dumps({**json.loads((tmp_path / "runs/claude/1.json").read_text()), "issue": 3}))
    (archive / "4.json").write_text(json.dumps({**json.loads((tmp_path / "runs/claude/1.json").read_text()), "issue": 4, "milestone": 99}))
    assert store.load_spend(3) == {"claude": {"small": 2, "medium": 1}}


def test_snapshot_round_trip(tmp_path):
    store = StateStore(tmp_path, "claude")
    assert store.load_snapshot() is None
    store.save_snapshot({"a": 1})
    assert store.load_snapshot() == {"a": 1}


def test_digest_and_diff_name_what_changed():
    b1 = mk_board(CFG2, issues=[mk_issue(11)], pulls=[mk_pr(20, 13)], runs={"claude": {12: mk_run("claude", 12)}, "codex": {}})
    b2 = mk_board(CFG2, issues=[mk_issue(11, status="in_progress", assignees=("moot-claude-bot",))],
                  pulls=[mk_pr(20, 13, approvals=("moot-codex-bot",))],
                  runs={"claude": {12: mk_run("claude", 12, alive=False)}, "codex": {}}, halted=True)
    d1, d2 = board_digest(b1), board_digest(b2)
    assert diff_digest(d1, d1) == []
    changes = diff_digest(d1, d2)
    assert "issue #11: ready -> in_progress" in changes
    assert "pr #20: approvals [] -> ['moot-codex-bot']" in changes
    assert "run claude/#12: running -> finished" in changes
    assert "halted: False -> True" in changes
    assert diff_digest(None, d2) == ["initial snapshot"]
