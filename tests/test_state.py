import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from meld.board import Challenge
from meld.state import CHANGE_KINDS, StateStore, board_digest, diff_digest, meld_home, diff_changes, filter_changes
from tests.factories import CFG2, mk_board, mk_issue, mk_pr, mk_run


def test_meld_home_default_and_override(tmp_path):
    assert meld_home({}).name == ".meld"
    assert meld_home({"MELD_HOME": str(tmp_path)}) == tmp_path


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
    b2 = mk_board(CFG2, issues=[mk_issue(11, status="in_progress")],
                  pulls=[mk_pr(20, 13, approvals=("meld-codex-bot",))],
                  runs={"claude": {12: mk_run("claude", 12, alive=False)}, "codex": {}}, halted=True)
    d1, d2 = board_digest(b1), board_digest(b2)
    assert diff_digest(d1, d1) == []
    changes = diff_digest(d1, d2)
    assert "issue #11: ready -> in_progress" in changes
    assert "pr #20: approvals [] -> ['meld-codex-bot']" in changes
    assert "run claude/#12: running -> finished" in changes
    assert "halted: False -> True" in changes
    assert diff_digest(None, d2) == ["initial snapshot"]


def test_corrupt_run_record_is_skipped_and_reported(tmp_path):
    store = StateStore(tmp_path, "claude")
    store.write_run(mk_run("claude", 1))
    bad = tmp_path / "runs" / "claude" / "2.json"
    bad.write_text("{not json")
    missing = tmp_path / "runs" / "claude" / "3.json"
    missing.write_text(json.dumps({"group": "claude", "issue": 3, "milestone": 3}))
    runs = store.load_runs(alive=lambda pid: False)
    assert sorted(runs["claude"]) == [1]
    assert len(store.errors) == 2 and str(bad) in store.errors[0]
    assert store.load_spend(3) == {"claude": {"medium": 1}}
    assert len(store.errors) == 4  # load_spend reports the same two files again


def test_writes_are_atomic_and_pid_guard(tmp_path):
    from meld.state import pid_alive
    store = StateStore(tmp_path, "claude")
    store.save_snapshot({"a": 1})
    assert not list((tmp_path / "state" / "claude").glob("*.tmp"))
    store.write_run(mk_run("claude", 5))
    assert not list((tmp_path / "runs" / "claude").glob("*.tmp"))
    assert pid_alive(0) is False and pid_alive(-1) is False


def test_dirs_are_per_group_cache_and_per_party_state(tmp_path):
    orch = StateStore(tmp_path, "claude", "orchestrator")
    assert orch.cache_dir == tmp_path / "cache" / "claude"
    assert orch.state_dir == tmp_path / "state" / "claude" / "orchestrator"
    assert orch.runs_root == tmp_path / "runs"
    groupless = StateStore(tmp_path, "claude")
    assert groupless.cache_dir == tmp_path / "cache" / "claude"
    assert groupless.state_dir == tmp_path / "state" / "claude"
    assert groupless.runs_root == tmp_path / "runs"
    human = StateStore(tmp_path, None)
    assert human.cache_dir == tmp_path / "cache" / "human"
    assert human.state_dir == tmp_path / "state" / "human"
    assert human.runs_root == tmp_path / "runs"


def test_two_parties_of_one_group_do_not_share_a_snapshot(tmp_path):
    orch = StateStore(tmp_path, "claude", "orchestrator")
    pm = StateStore(tmp_path, "claude", "pm")
    orch.save_snapshot({"seen": "orchestrator"})
    pm.save_snapshot({"seen": "pm"})
    assert orch.load_snapshot() == {"seen": "orchestrator"}
    assert pm.load_snapshot() == {"seen": "pm"}


def test_write_atomic_writes_a_sibling_temp_then_replaces(tmp_path, monkeypatch):
    import meld.atomic as atomic
    from meld.atomic import write_atomic

    target = tmp_path / "snapshot.json"
    target.write_text("old")
    seen: dict[str, str] = {}
    real_replace = os.replace

    def spy(src, dst):
        seen["src"], seen["dst"] = str(src), str(dst)
        seen["tmp_content"] = Path(src).read_text()
        seen["target_before"] = Path(dst).read_text()
        real_replace(src, dst)

    monkeypatch.setattr(atomic.os, "replace", spy)
    write_atomic(target, "new")
    assert seen["src"] == str(tmp_path / "snapshot.json.tmp") and seen["dst"] == str(target)
    # the whole new body reaches the temp file, and the target still holds the old bytes
    # until the replace, so no reader can ever observe a partial write
    assert seen["tmp_content"] == "new" and seen["target_before"] == "old"
    assert target.read_text() == "new" and not list(tmp_path.glob("*.tmp"))


def test_pid_alive_reaps_own_exited_child_and_archive_run(tmp_path):
    import os, subprocess, sys, time
    from meld.state import pid_alive
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:  # wait for the child to exit WITHOUT reaping it
        try:
            os.kill(proc.pid, 0)
        except ProcessLookupError:  # pragma: no cover - cannot happen for an unreaped child
            break
        if (Path(f"/proc/{proc.pid}").exists() and "Z" in Path(f"/proc/{proc.pid}/stat").read_text()) or sys.platform == "darwin":
            time.sleep(0.2)
            break
        time.sleep(0.05)
    os.kill(proc.pid, 0)  # still answers: the exited child is a zombie until someone reaps it
    assert pid_alive(proc.pid) is False  # pid_alive reaps it via waitpid and reports it dead
    with pytest.raises(ProcessLookupError):
        os.kill(proc.pid, 0)  # proof that pid_alive really reaped it
    proc.wait()  # tell the Popen object its child is gone (pid_alive reaped it), so Popen.__del__ never warns it is still running
    store = StateStore(tmp_path, "claude")
    store.write_run(mk_run("claude", 12))
    archived = store.archive_run("claude", 12)
    assert archived.parent == tmp_path / "runs" / "claude" / "archive" and archived.name.startswith("12-")
    assert not (tmp_path / "runs" / "claude" / "12.json").exists()
    assert store.load_runs(alive=lambda p: False) == {"claude": {}}
    assert store.load_spend(3) == {"claude": {"medium": 1}}


def test_run_record_extensions_round_trip(tmp_path):
    store = StateStore(tmp_path, "claude")
    rec = mk_run("claude", 12)
    from dataclasses import replace
    store.write_run(replace(rec, deadline="2026-09-03T13:00:00Z", branch="meld/12-login", lease_comment=110, timed_out=True, worker="backend"))
    loaded = store.load_runs(alive=lambda p: False)["claude"][12]
    assert (loaded.deadline, loaded.branch, loaded.lease_comment, loaded.timed_out) == ("2026-09-03T13:00:00Z", "meld/12-login", 110, True)
    assert loaded.worker == "backend"
    assert mk_run("claude", 13).worker == ""   # records written before the field existed read back as an empty worker


def test_diff_changes_are_structured_and_filterable():
    b1 = mk_board(CFG2, issues=[mk_issue(11)], pulls=[mk_pr(20, 13)])
    b2 = mk_board(CFG2, issues=[mk_issue(11, status="in_progress")],
                  pulls=[mk_pr(20, 13, approvals=("meld-codex-bot",))], halted=True)
    d1, d2 = board_digest(b1), board_digest(b2)
    changes = diff_changes(d1, d2)
    assert [c.kind for c in changes] == ["halted", "issue", "pr"]
    issue = changes[1]
    assert (issue.ref, issue.before, issue.after, issue.text) == ("#11", "ready", "in_progress", "issue #11: ready -> in_progress")
    assert [c.text for c in changes] == diff_digest(d1, d2)
    assert [c.kind for c in filter_changes(changes, {"pr"})] == ["pr"]
    assert filter_changes(changes, None) == changes
    initial = diff_changes(None, d2)
    assert [c.kind for c in initial] == ["snapshot"] and initial[0].text == "initial snapshot"
    assert filter_changes(initial, {"comments"}) == initial


def test_a_claim_move_at_the_same_status_is_reported_as_a_change():
    """The claim can move without the status moving (a lost race undone, a re-claim after a release inside one
    poll interval), so `board_digest` snapshots `claimants` and `diff_changes` falls through to its labels branch."""
    b1 = mk_board(CFG2, issues=[mk_issue(12, status="in_progress", claimed_by="claude")])
    b2 = mk_board(CFG2, issues=[mk_issue(12, status="in_progress", claimed_by="codex")])
    d1, d2 = board_digest(b1), board_digest(b2)
    assert d1["issues"]["12"]["claimants"] == ["claude"] and d2["issues"]["12"]["claimants"] == ["codex"]
    changes = diff_changes(d1, d2)
    assert [c.kind for c in changes] == ["issue"]
    assert changes[0].ref == "#12" and changes[0].text == "issue #12: labels or claim changed"
    assert changes[0].before["claimants"] == ["claude"] and changes[0].after["claimants"] == ["codex"]
    assert diff_digest(d1, d2) == ["issue #12: labels or claim changed"]
    assert diff_changes(d1, d1) == []


def test_write_run_archives_a_collected_record_before_a_new_run_and_spend_counts_both(tmp_path):
    from dataclasses import replace
    store = StateStore(tmp_path, "claude")
    first = mk_run("claude", 12, tier="medium")
    store.write_run(first)
    store.write_run(replace(first, lease_comment=5))                # the same run updated in place
    store.write_run(replace(first, alive=False, collected=True))    # and finished
    assert not (tmp_path / "runs" / "claude" / "archive").exists()
    store.write_run(mk_run("claude", 12, tier="large"))             # a retry on the same issue
    archived = list((tmp_path / "runs" / "claude" / "archive").glob("12-*.json"))
    assert len(archived) == 1 and json.loads(archived[0].read_text())["tier"] == "medium"
    assert store.load_runs(alive=lambda p: True)["claude"][12].tier == "large"
    assert store.load_spend(3) == {"claude": {"medium": 1, "large": 1}}


def test_archive_run_never_overwrites_an_earlier_archive(tmp_path):
    store = StateStore(tmp_path, "claude")
    for _ in range(3):
        store.write_run(mk_run("claude", 7))
        store.archive_run("claude", 7)
    assert len(list((tmp_path / "runs" / "claude" / "archive").glob("7-*.json"))) == 3
    assert store.load_spend(3) == {"claude": {"medium": 3}}


def test_digest_and_diff_carry_challenges_audits_and_statuses():
    pr = replace(mk_pr(20, 13), statuses={"meld/review/codex": "pending"})
    b1 = replace(mk_board(CFG2, pulls=[pr]), challenges={"#20": Challenge("#20", "claude", 500, "t")}, open_challenges=frozenset({"#20"}))
    d1 = board_digest(b1)
    assert d1["challenges"] == {"#20": "claude"} and d1["audits"] == {} and d1["pulls"]["20"]["statuses"] == {"meld/review/codex": "pending"}
    b2 = replace(b1, challenges={"#20": Challenge("#20", "codex", 501, "t")},
                 pulls={20: replace(pr, statuses={"meld/review/codex": "success"})}, audits={19: {"codex": "pass"}})
    kinds = sorted({c.kind for c in diff_changes(d1, board_digest(b2))})
    assert kinds == ["audits", "challenges", "pr"]
    texts = [c.text for c in diff_changes(d1, board_digest(b2))]
    assert any("challenger" in t for t in texts) and any("statuses" in t for t in texts) and any("audit" in t for t in texts)
    assert "audits" in CHANGE_KINDS
