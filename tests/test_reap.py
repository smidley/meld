import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from meld.actions import Refused
from meld.dispatch import collect, dispatch, reap
from meld.gitops import worktree_exists
from tests.fakeadapter import FakeAdapter
from tests.productfixtures import DONE_SCRIPT, R, claim_issue_11, fixture, make_product, wait_for_exit
from tests.test_collect import routes_for_labels

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def test_reap_after_merge_removes_worktree_and_archives(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    routes_for_labels(fake, 11)
    fake.route("POST", R + "pulls", {"number": 21})
    out = dispatch(ctx, 11, adapter=FakeAdapter(script=DONE_SCRIPT), now=NOW)
    wait_for_exit(out["pid"])
    collect(ctx, 11, now=NOW)
    with pytest.raises(Refused, match="still open"):
        reap(ctx, 11)
    issues = fixture("issues.json")
    for i in issues:
        if i["number"] == 11:
            i["state"] = "closed"
    fake.route("GET", R + "issues", issues)
    result = reap(ctx, 11)
    assert result["reaped"] == 11 and result["worktree_removed"] is True
    assert not worktree_exists(repo, Path(out["worktree"])) and not Path(out["worktree"]).exists()
    assert Path(result["archived"]).parent == tmp_path / "home" / "runs" / "claude" / "archive"
    assert not (tmp_path / "home" / "runs" / "claude" / "11.json").exists()
    assert ctx.store.load_spend(3) == {"claude": {"medium": 1}}
    with pytest.raises(Refused, match="no run record"):
        reap(ctx, 11)


def test_reap_refuses_live_or_uncollected(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path)
    claim_issue_11(fake)
    out = dispatch(ctx, 11, adapter=FakeAdapter(), now=NOW)
    with pytest.raises(Refused, match="still running|collect first"):
        reap(ctx, 11)
    wait_for_exit(out["pid"])
    with pytest.raises(Refused, match="collect first"):
        reap(ctx, 11)
