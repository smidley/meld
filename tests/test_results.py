import json

import pytest

from meld.results import ResultsError, load_result, mark_submitted, result_path, store_result


def test_store_result_never_overwrites_an_unconsumed_verdict(tmp_path):
    p1 = store_result(tmp_path, "claude", "review", 20, {"result": {"verdict": "approve"}}, "2026-09-04T10:00:00Z")
    p2 = store_result(tmp_path, "claude", "review", 20, {"result": {"verdict": "request-changes"}}, "2026-09-04T11:00:00Z")
    assert p1 == p2 == tmp_path / "results" / "claude" / "review-20.json"
    assert load_result(tmp_path, "claude", "review", 20)["result"]["verdict"] == "request-changes"
    aside = sorted((tmp_path / "results" / "claude" / "superseded").glob("review-20-*.json"))
    assert [a.name for a in aside] == ["review-20-20260904T110000Z.json"]
    assert json.loads(aside[0].read_text())["result"]["verdict"] == "approve"
    store_result(tmp_path, "claude", "review", 20, {"result": {"verdict": "approve"}}, "2026-09-04T11:00:00Z")
    assert sorted(a.name for a in (tmp_path / "results" / "claude" / "superseded").iterdir()) == \
        ["review-20-20260904T110000Z-1.json", "review-20-20260904T110000Z.json"]


def test_mark_submitted_moves_the_file_and_records_what_happened(tmp_path):
    store_result(tmp_path, "claude", "review", 20, {"head_sha": "abc1234567", "result": {"verdict": "approve"}}, "2026-09-04T10:00:00Z")
    moved = mark_submitted(tmp_path, "claude", "review", 20, {"event": "APPROVE", "review_id": 501}, "2026-09-04T10:05:00Z")
    assert moved == tmp_path / "results" / "claude" / "submitted" / "review-20-abc1234-20260904T100500Z.json"
    assert load_result(tmp_path, "claude", "review", 20) is None
    data = json.loads(moved.read_text())
    assert data["event"] == "APPROVE" and data["review_id"] == 501 and data["submitted_at"] == "2026-09-04T10:05:00Z"
    assert data["result"]["verdict"] == "approve"
    with pytest.raises(ResultsError, match="no collected review result"):
        mark_submitted(tmp_path, "claude", "review", 20, {}, "2026-09-04T10:06:00Z")


def test_load_result_rejects_malformed_json(tmp_path):
    p = result_path(tmp_path, "claude", "audit", 19)
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    with pytest.raises(ResultsError):
        load_result(tmp_path, "claude", "audit", 19)
    assert load_result(tmp_path, "claude", "audit", 18) is None
