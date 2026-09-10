import json
from urllib.parse import quote

import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.actions import Refused, claim, release
from meld.context import load_context
from meld.github import GitHubError
from tests.conftest import FIXTURES, PROJECT_TOML, env_for, load_board1_routes
from tests.fakeadapter import no_skills
from tests.fakegh import FakeGh

R = "repos/acme/widgets/"
CLAIM = "meld:claimed-by:claude"


def label_ep(number: int, label: str) -> str:
    """The label endpoint as GitHub sees it: the label is one URL-quoted path segment."""
    return R + f"issues/{number}/labels/{quote(label, safe='')}"


def fixture_comments() -> list[dict]:
    return json.loads((FIXTURES / "board1" / "comments_10.json").read_text())


def fixture_issue(n: int) -> dict:
    return next(i for i in json.loads((FIXTURES / "board1" / "issues.json").read_text()) if i["number"] == n)


def make(tmp_path, group="claude", role="pm"):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    ctx = load_context(tmp_path / ".meld.toml", env=env_for(tmp_path, group=group, role=role), run=fake, adapter_for=no_skills)
    return fake, ctx


def posted_headers(fake):
    return [c.body["body"].splitlines()[0] for c in fake.calls if c.method == "POST" and c.endpoint.endswith("/comments")]


def test_claim_adds_the_label_verifies_it_and_swaps_status_labels(tmp_path):
    fake, ctx = make(tmp_path)
    fake.route("POST", R + "labels", {"name": CLAIM}, status=201)                       # ensure_label creates it
    fake.route("POST", R + "issues/11/labels", [])
    fake.route("GET", R + "issues/11", {**fixture_issue(11), "labels": fixture_issue(11)["labels"] + [{"name": CLAIM}]})
    fake.route("DELETE", label_ep(11, "meld:ready"), {}, status=200)
    out = claim(ctx, 11)
    assert out == {"claimed": 11, "claimed_by": "claude"}
    mutations = [(c.method, c.endpoint) for c in fake.calls if c.method != "GET"]
    assert mutations[:2] == [("POST", R + "labels"), ("POST", R + "issues/11/labels")]      # ensure, then claim
    assert [c.body["labels"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/11/labels"] == [[CLAIM], ["meld:in-progress"]]
    assert ("DELETE", label_ep(11, "meld:ready")) in mutations
    assert posted_headers(fake) == []
    # no stale own lease in the fixture, so a successful claim posts no EXPIRED comment
    assert not any(h.startswith("EXPIRED") for h in posted_headers(fake))


def test_claim_treats_a_422_from_label_creation_as_already_exists(tmp_path):
    fake, ctx = make(tmp_path)
    fake.route("POST", R + "labels", {"message": "Validation Failed", "errors": [{"code": "already_exists"}]}, status=422)
    fake.route("POST", R + "issues/11/labels", [])
    fake.route("GET", R + "issues/11", {**fixture_issue(11), "labels": fixture_issue(11)["labels"] + [{"name": CLAIM}]})
    fake.route("DELETE", label_ep(11, "meld:ready"), {}, status=200)
    assert claim(ctx, 11)["claimed"] == 11


def test_claim_loses_the_race_when_another_claim_label_is_present_after_the_re_read(tmp_path):
    fake, ctx = make(tmp_path)
    fake.route("POST", R + "labels", {}, status=422)
    fake.route("POST", R + "issues/11/labels", [])
    fake.route("GET", R + "issues/11", {**fixture_issue(11), "labels": fixture_issue(11)["labels"] + [{"name": CLAIM}, {"name": "meld:claimed-by:codex"}]})
    fake.route("DELETE", label_ep(11, CLAIM), {}, status=200)
    with pytest.raises(Refused) as ei:
        claim(ctx, 11)
    assert "lost the claim race to codex" in ei.value.refusal.rule
    assert ("DELETE", label_ep(11, CLAIM)) in [(c.method, c.endpoint) for c in fake.calls]
    # first-label-wins working as designed is not an illegal call: no violation comment
    assert posted_headers(fake) == []
    assert ei.value.violation_posted is None


def test_claim_refusal_posts_protocol_violation_for_groups_only(tmp_path):
    fake, ctx = make(tmp_path, role="orchestrator")
    fake.route("POST", R + "issues/10/comments", {"id": 1})
    with pytest.raises(Refused) as ei:
        claim(ctx, 11)
    assert ei.value.refusal.rule == "only a PM or a human may claim"
    assert posted_headers(fake) == ["[claude] PROTOCOL-VIOLATION #11"]
    assert not any(c.endpoint.endswith("/labels") for c in fake.calls)

    fake2, human = make(tmp_path, group=None)
    with pytest.raises(Refused):
        claim(human, 12)  # already in progress
    assert posted_headers(fake2) == []


def test_claim_unknown_issue(tmp_path):
    fake, ctx = make(tmp_path)
    fake.route("POST", R + "issues/10/comments", {"id": 1})
    with pytest.raises(Refused, match="not in the active milestone"):
        claim(ctx, 999)


def test_claim_compensates_when_reread_fails(tmp_path):
    fake, ctx = make(tmp_path)
    fake.route("POST", R + "labels", {}, status=422)
    fake.route("POST", R + "issues/11/labels", [])
    fake.route("DELETE", label_ep(11, CLAIM), {}, status=200)
    # no GET issues/11 route -> 404 -> GitHubError on the fresh re-read
    with pytest.raises(GitHubError):
        claim(ctx, 11)
    assert ("DELETE", label_ep(11, CLAIM)) in [(c.method, c.endpoint) for c in fake.calls]


def test_refusal_survives_a_failed_violation_post(tmp_path):
    fake, ctx = make(tmp_path, role="orchestrator")  # no comments route -> post fails with 404
    with pytest.raises(Refused) as ei:
        claim(ctx, 11)
    assert ei.value.refusal.rule == "only a PM or a human may claim"
    assert ei.value.post_error is not None


def test_release_by_owner_and_forced_by_human(tmp_path):
    fake, ctx = make(tmp_path)
    fake.route("DELETE", label_ep(12, CLAIM), {}, status=200)
    fake.route("POST", R + "issues/12/labels", [{"name": "meld:ready"}])
    fake.route("DELETE", label_ep(12, "meld:in-progress"), [])
    fake.route("DELETE", label_ep(12, "meld:blocked"), [])
    fake.route("POST", R + "issues/10/comments", {"id": 900})
    assert release(ctx, 12) == {"released": 12, "previous_claimants": ["claude"]}
    mutations = [(c.method, c.endpoint) for c in fake.calls if c.method != "GET"]
    # plan 5 task 2 (Decision 2): a non-human release now also posts a framework RELEASED comment, so any earlier
    # LEASE on the issue reads as void immediately rather than waiting for a PM's tick to notice and post EXPIRED.
    assert mutations == [("DELETE", label_ep(12, CLAIM)), ("POST", R + "issues/12/labels"),
                         ("DELETE", label_ep(12, "meld:in-progress")), ("POST", R + "issues/10/comments")]
    assert posted_headers(fake) == ["[claude:pm] RELEASED #12"]
    released_body = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"][-1]
    assert released_body == "[claude:pm] RELEASED #12\n\nReleased #12; any earlier LEASE on it is void.\n"

    fake2, human = make(tmp_path, group=None)
    fake2.route("DELETE", label_ep(12, CLAIM), {}, status=200)
    fake2.route("DELETE", label_ep(12, "meld:in-progress"), [])
    fake2.route("POST", R + "issues/12/labels", [{"name": "meld:ready"}])
    fake2.route("POST", R + "issues/10/comments", {"id": 2})
    release(human, 12, force=True)
    assert posted_headers(fake2) == ["[human] RELEASE #12"]

    fake3, pm = make(tmp_path)
    fake3.route("POST", R + "issues/10/comments", {"id": 3})
    with pytest.raises(Refused, match="human-only"):
        release(pm, 12, force=True)


def test_release_removes_every_claim_label_and_reports_the_previous_claimants(tmp_path):
    fake, ctx = make(tmp_path)
    fake.route("DELETE", label_ep(12, CLAIM), {}, status=200)
    fake.route("POST", R + "issues/12/labels", [])
    fake.route("DELETE", label_ep(12, "meld:in-progress"), {}, status=200)
    fake.route("POST", R + "issues/10/comments", {"id": 900})            # RELEASED on the coordination issue
    out = release(ctx, 12)
    assert out == {"released": 12, "previous_claimants": ["claude"]}
    mutations = [(c.method, c.endpoint) for c in fake.calls if c.method != "GET"]
    assert mutations[0] == ("DELETE", label_ep(12, CLAIM))


def test_cli_claim_refusal_exit_code(tmp_path, monkeypatch):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    fake.route("POST", R + "issues/10/comments", {"id": 1})
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "claim", "12"], env=env_for(tmp_path))
    assert result.exit_code == 2
    data = json.loads(result.output)
    assert data["verb"] == "claim" and data["target"] == "#12" and "not ready" in data["rule"]
    assert data["violation_posted"] is True


def test_claim_compensates_both_halves_when_the_label_swap_fails(tmp_path):
    fake, ctx = make(tmp_path)
    fake.route("POST", R + "labels", {}, status=422)
    fake.route("POST", R + "issues/11/labels", [])
    fake.route("GET", R + "issues/11", {**fixture_issue(11), "labels": fixture_issue(11)["labels"] + [{"name": CLAIM}]})
    fake.route("DELETE", label_ep(11, "meld:ready"), {"message": "boom"}, status=500)
    fake.route("DELETE", label_ep(11, CLAIM), {}, status=200)
    fake.route("DELETE", label_ep(11, "meld:in-progress"), [])
    with pytest.raises(GitHubError, match="500"):
        claim(ctx, 11)
    mutations = [(c.method, c.endpoint) for c in fake.calls if c.method != "GET"]
    assert ("DELETE", label_ep(11, CLAIM)) in mutations
    assert ("DELETE", label_ep(11, "meld:in-progress")) in mutations


def test_cli_human_refusal_reports_no_violation_post(tmp_path, monkeypatch):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "claim", "12"],
                                env=env_for(tmp_path, group=None))
    assert result.exit_code == 2
    data = json.loads(result.output)
    assert data["verb"] == "claim" and "not ready" in data["rule"]
    assert data["violation_posted"] is None
    assert posted_headers(fake) == []


def test_pm_claim_clears_its_own_stale_lease(tmp_path):
    fake, ctx = make(tmp_path)   # claude PM
    stale = {"id": 105, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:00:00Z",
             "body": "[claude:pm] LEASE #11\n\nuntil: 2020-01-01T00:00:00Z\nrun: claude/#11\nkind: build"}
    fake.route("GET", R + "issues/10/comments", fixture_comments() + [stale])
    # Copy, verbatim, the label routes and the fresh-issue re-read route from the existing successful-claim test
    # in this file so that claim(ctx, 11) succeeds here exactly as it does there.
    fake.route("POST", R + "labels", {"name": CLAIM}, status=201)
    fake.route("POST", R + "issues/11/labels", [])
    fake.route("GET", R + "issues/11", {**fixture_issue(11), "labels": fixture_issue(11)["labels"] + [{"name": CLAIM}]})
    fake.route("DELETE", label_ep(11, "meld:ready"), [])
    fake.route("POST", R + "issues/10/comments", {"id": 900})
    claim(ctx, 11)
    posted = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]
    assert posted and posted[-1].startswith("[claude:pm] EXPIRED #11\n")


def test_human_claim_posts_no_expired_comment(tmp_path):
    # A human actor is never a PM (actor.is_pm is False), so the EXPIRED-lease check in claim() is
    # skipped unconditionally -- this holds even with the same stale claude PM lease on the thread
    # that triggers an EXPIRED post for a PM claimant in test_pm_claim_clears_its_own_stale_lease.
    fake, human = make(tmp_path, group=None)
    stale = {"id": 105, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:00:00Z",
             "body": "[claude:pm] LEASE #11\n\nuntil: 2020-01-01T00:00:00Z\nrun: claude/#11\nkind: build"}
    fake.route("GET", R + "issues/10/comments", fixture_comments() + [stale])
    # Mirror the PM happy-path routes with the shared human claim label instead of claude's.
    fake.route("POST", R + "labels", {}, status=422)
    fake.route("POST", R + "issues/11/labels", [])
    fake.route("GET", R + "issues/11", {**fixture_issue(11), "labels": fixture_issue(11)["labels"] + [{"name": "meld:claimed-by:human"}]})
    fake.route("DELETE", label_ep(11, "meld:ready"), [])
    assert claim(human, 11) == {"claimed": 11, "claimed_by": "human"}
    assert not any(c.method == "POST" and c.endpoint == R + "issues/10/comments" for c in fake.calls)


def stale_claim_board(fake, number: int = 11, label: str = CLAIM) -> None:
    """Re-route the issue list so `number` is `meld:ready` *and* carries `label`: the orphan state a PM killed
    between claim's label add and its status swap leaves behind (decision 11)."""
    issues = json.loads((FIXTURES / "board1" / "issues.json").read_text())
    for i in issues:
        if i["number"] == number:
            i["labels"] = i["labels"] + [{"name": label}]
    fake.route("GET", R + "issues", issues)


def test_release_clears_a_stale_claim_label_on_a_ready_issue(tmp_path):
    fake, ctx = make(tmp_path)
    stale_claim_board(fake)
    fake.route("DELETE", label_ep(11, CLAIM), {}, status=200)
    fake.route("POST", R + "issues/11/labels", [{"name": "meld:ready"}])
    fake.route("POST", R + "issues/10/comments", {"id": 901})
    assert release(ctx, 11) == {"released": 11, "previous_claimants": ["claude"]}
    mutations = [(c.method, c.endpoint) for c in fake.calls if c.method != "GET"]
    # the claim label goes, meld:ready is re-added as a no-op, and no in-progress/blocked label exists to remove
    assert mutations == [("DELETE", label_ep(11, CLAIM)), ("POST", R + "issues/11/labels"),
                         ("POST", R + "issues/10/comments")]
    assert posted_headers(fake) == ["[claude:pm] RELEASED #11"]


def test_claim_proceeds_past_its_own_stale_claim_label_on_a_ready_issue(tmp_path):
    fake, ctx = make(tmp_path)
    stale_claim_board(fake)
    fake.route("POST", R + "labels", {}, status=422)                                    # the label already exists
    fake.route("POST", R + "issues/11/labels", [])
    fake.route("GET", R + "issues/11", {**fixture_issue(11), "labels": fixture_issue(11)["labels"] + [{"name": CLAIM}]})
    fake.route("DELETE", label_ep(11, "meld:ready"), {}, status=200)
    assert claim(ctx, 11) == {"claimed": 11, "claimed_by": "claude"}
    # the add is a no-op, the re-read shows exactly its own label, and the status swap completes: self-healed
    assert [c.body["labels"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/11/labels"] == \
        [[CLAIM], ["meld:in-progress"]]
    assert ("DELETE", label_ep(11, "meld:ready")) in [(c.method, c.endpoint) for c in fake.calls]
    assert posted_headers(fake) == []


def test_another_groups_stale_claim_label_still_refuses_a_claim(tmp_path):
    fake, ctx = make(tmp_path)
    stale_claim_board(fake, label="meld:claimed-by:codex")
    fake.route("POST", R + "issues/10/comments", {"id": 902})
    with pytest.raises(Refused) as ei:
        claim(ctx, 11)
    assert ei.value.refusal.rule == "already claimed by codex"
    assert not any(c.endpoint.endswith("issues/11/labels") for c in fake.calls)


def test_a_human_claims_with_the_shared_human_label(tmp_path):
    fake, human = make(tmp_path, group=None)
    fake.route("POST", R + "labels", {}, status=422)
    fake.route("POST", R + "issues/11/labels", [])
    fake.route("GET", R + "issues/11", {**fixture_issue(11), "labels": fixture_issue(11)["labels"] + [{"name": "meld:claimed-by:human"}]})
    fake.route("DELETE", label_ep(11, "meld:ready"), {}, status=200)
    assert claim(human, 11) == {"claimed": 11, "claimed_by": "human"}
