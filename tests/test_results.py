import json

import pytest

from meld.results import BODY_LIMIT, TRUNCATED, ResultsError, cap_body, format_findings, load_result, mark_submitted, result_path, store_result, update_result


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


def test_update_result_merges_into_the_unconsumed_verdict_in_place(tmp_path):
    store_result(tmp_path, "claude", "review", 20, {"head_sha": "abc123", "result": {"verdict": "push-fix"}}, "2026-09-04T10:00:00Z")
    path = update_result(tmp_path, "claude", "review", 20, {"pushed": "def456", "pushed_at": "2026-09-04T10:05:00Z"})
    assert path == tmp_path / "results" / "claude" / "review-20.json"
    data = json.loads(path.read_text())
    assert data["pushed"] == "def456" and data["pushed_at"] == "2026-09-04T10:05:00Z"
    assert data["head_sha"] == "abc123" and data["result"]["verdict"] == "push-fix"   # original fields survive
    assert load_result(tmp_path, "claude", "review", 20) == data                     # still unconsumed, updated in place
    with pytest.raises(ResultsError, match="no collected review result"):
        update_result(tmp_path, "claude", "review", 21, {"pushed": "x"})


def test_load_result_rejects_malformed_json(tmp_path):
    p = result_path(tmp_path, "claude", "audit", 19)
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    with pytest.raises(ResultsError):
        load_result(tmp_path, "claude", "audit", 19)
    assert load_result(tmp_path, "claude", "audit", 18) is None


def test_format_findings_keeps_worker_backticks_out_of_the_code_span():
    findings = format_findings([{"path": "src/a.py", "line": 12, "body": "nit"},
                                {"path": "weird`file.py", "line": "7` **bold**", "body": "a `code` word stays in the body"}])
    assert findings == "- `src/a.py:12` nit\n- `weirdfile.py:7 **bold**` a `code` word stays in the body"


def test_cap_body_truncates_long_bodies_with_a_marker():
    assert cap_body("short") == "short"
    exact = "x" * BODY_LIMIT
    assert cap_body(exact) == exact
    capped = cap_body("y" * (BODY_LIMIT + 5000))
    assert len(capped) == BODY_LIMIT + len(TRUNCATED) and capped.endswith(TRUNCATED) and capped.startswith("y" * BODY_LIMIT)
    assert cap_body("abcdef", limit=3) == "abc" + TRUNCATED
