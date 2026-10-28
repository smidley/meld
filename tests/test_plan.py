import pytest

from meld.actions import Refused
from meld.context import load_context
from meld.github import GitHubError
from meld.plan import PlanItem, parse_plan_items, plan_ack, plan_approve, plan_issues
from tests.conftest import env_for
from tests.fakeadapter import no_skills
from tests.productfixtures import R, fixture, make_product

PLAN_TEXT = """Milestone 1 decomposition.

- id: login-form | title: Login form | worker: frontend | model: claude-sonnet-5 | effort: medium | size: M
  - renders email and password fields
  - shows server errors inline
- id: health | title: Add health endpoint | worker: backend | model: claude-sonnet-5 | effort: low | size: S
  - GET /health returns 200
- id: broken line without the fields
- id: bad-size | title: X | worker: backend | model: m | effort: low | size: XL

verified-by: meld roster; meld models"""

PLAN_THREAD = [
    {"id": 301, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:00:00Z", "body": "[claude] PROPOSE plan-3\n\n" + PLAN_TEXT},
    {"id": 302, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:01:00Z", "body": "[codex] AGREE plan-3\n\nagreed"},
    {"id": 303, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:02:00Z", "body": "[joint] PLAN plan-3\n\n" + PLAN_TEXT},
]


def test_parse_plan_items():
    items, errors = parse_plan_items(PLAN_TEXT)
    assert items[0] == PlanItem("login-form", "Login form", "frontend", "claude-sonnet-5", "medium", "M",
                                ("renders email and password fields", "shows server errors inline"))
    assert items[1].id == "health" and items[1].criteria == ("GET /health returns 200",)
    assert len(items) == 2 and len(errors) == 2 and "broken line" in errors[0] and "bad-size" in errors[1]


def planning_board(fake, extra_issues=()):
    issues = fixture("issues.json")
    for i in issues:
        if i["number"] == 10:
            i["labels"] = [l for l in i["labels"] if l["name"] != "meld:plan-approved"]      # back to the planning phase
    issues += list(extra_issues)
    fake.route("GET", R + "issues", issues)
    return issues


def test_plan_issues_creates_planned_issues_once(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    planning_board(fake)   # a base PLAN's issues may only be created before the human approves the plan (F1)
    fake.route("GET", R + "issues/10/comments", fixture("comments_10.json") + PLAN_THREAD)
    fake.route("POST", R + "issues", {"number": 31, "html_url": "u31"})
    fake.route("PATCH", R + "milestones/3", {"number": 3})
    out = plan_issues(ctx, "plan-3")
    assert out["created"] == [31, 31] and out["skipped"] == [] and len(out["errors"]) == 2
    bodies = [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues"]
    assert bodies[0]["title"] == "Login form" and bodies[0]["milestone"] == 3
    assert bodies[0]["labels"] == ["meld:planned", "worker:frontend", "model:claude-sonnet-5", "effort:medium", "size:M"]
    assert bodies[0]["body"].startswith("plan-item: login-form\nplan-ref: plan-3\n") and "- renders email and password fields" in bodies[0]["body"]
    assert [c.body for c in fake.calls if c.method == "PATCH" and c.endpoint == R + "milestones/3"] == [{"description": PLAN_TEXT}]
    # a second run skips items whose plan-item marker already exists on the board (still planning: planning_board
    # rebuilds #10 without meld:plan-approved every time, since a raw fixture() re-read would put it back)
    planning_board(fake, [{"number": 31, "title": "Login form", "body": "plan-item: login-form\nplan-ref: plan-3\n", "state": "open",
                          "labels": [{"name": "meld:planned"}], "milestone": {"number": 3}}])
    again = plan_issues(ctx, "plan-3")
    assert again["skipped"] == ["login-form"] and again["created"] == [31]
    # only the proposer (claude) may create the issues for plan-3: read the SAME board (same fake/repo) as a codex
    # orchestrator, so the check that fires is the proposer mismatch, not "no verified [joint]" for a thread codex
    # never saw. See task-5-report.md Deviations for why the brief's disconnected fixture couldn't reach this check.
    codex = load_context(repo / ".meld.toml", env=env_for(tmp_path, group="codex", role="orchestrator"), run=fake, adapter_for=no_skills)
    with pytest.raises(Refused, match="only the proposer"):
        plan_issues(codex, "plan-3")


def test_plan_issues_refuses_a_base_plan_once_already_approved(tmp_path):
    # default board: #10 keeps meld:plan-approved from fixture (phase == executing) -- no planning_board() call
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    fake.route("GET", R + "issues/10/comments", fixture("comments_10.json") + PLAN_THREAD)
    with pytest.raises(Refused, match="already approved"):
        plan_issues(ctx, "plan-3")
    # Plan 54: once every item already exists, the same call after approval is a no-op, not a violation -- the orchestrator
    # read a planning board and the person approved before its call landed (the rehearsal hit this one run in three)
    issues = fixture("issues.json") + [
        {"number": 31, "title": "Login form", "body": "plan-item: login-form\nplan-ref: plan-3\n", "state": "open", "labels": [{"name": "meld:ready"}], "milestone": {"number": 3}},
        {"number": 32, "title": "Add health endpoint", "body": "plan-item: health\nplan-ref: plan-3\n", "state": "open", "labels": [{"name": "meld:ready"}], "milestone": {"number": 3}}]
    fake.route("GET", R + "issues", issues)
    for n in (31, 32):
        fake.route("GET", R + f"issues/{n}/events", [])
    before = len(fake.calls)
    out = plan_issues(ctx, "plan-3")
    assert out["already"] is True and out["created"] == [] and out["skipped"] == ["login-form", "health"]
    assert not [c for c in fake.calls[before:] if c.method == "POST"]   # no PROTOCOL-VIOLATION, nothing created


def test_plan_issues_dry_run_reports_without_creating(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    planning_board(fake)
    fake.route("GET", R + "issues/10/comments", fixture("comments_10.json") + PLAN_THREAD)
    out = plan_issues(ctx, "plan-3", dry_run=True)
    assert out["would_create"] == ["login-form", "health"] and out["created"] == [] and out["dry_run"] is True
    assert not any(c.method == "POST" and c.endpoint == R + "issues" for c in fake.calls)
    fake.route("POST", R + "issues", {"number": 31, "html_url": "u31"})
    fake.route("PATCH", R + "milestones/3", {"number": 3})
    again = plan_issues(ctx, "plan-3")
    assert again["created"] == [31, 31] and again["would_create"] == ["login-form", "health"]


def test_plan_issues_refuses_without_a_verified_joint_plan(tmp_path):
    fake, ctx, repo, origin = make_product(tmp_path, role="orchestrator")
    with pytest.raises(Refused, match="no verified \\[joint\\] PLAN"):
        plan_issues(ctx, "plan-3")
    fake2, pm, *_ = make_product(tmp_path / "p")
    fake2.route("GET", R + "issues/10/comments", fixture("comments_10.json") + PLAN_THREAD)
    with pytest.raises(Refused, match="orchestrator"):
        plan_issues(pm, "plan-3")


def test_plan_approve_flips_planned_issues_to_ready(tmp_path):
    fake, human, repo, origin = make_product(tmp_path, group=None)
    planned = [{"number": n, "title": f"P{n}", "body": f"plan-item: p{n}\nplan-ref: plan-3\n", "state": "open",
                "labels": [{"name": "meld:planned"}, {"name": "worker:backend"}], "milestone": {"number": 3}} for n in (31, 32)]
    planning_board(fake, planned)
    fake.route("GET", R + "issues/10/comments", fixture("comments_10.json") + PLAN_THREAD)
    for n in (10, 31, 32):
        fake.route("POST", R + f"issues/{n}/labels", [])
        fake.route("DELETE", R + f"issues/{n}/labels/meld%3Aplanned", [])
    out = plan_approve(human)
    assert out == {"approved": "plan-3", "ready": [31, 32]}
    assert [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/labels"] == [{"labels": ["meld:plan-approved"]}]
    assert [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/31/labels"] == [{"labels": ["meld:ready"]}]
    assert any(c.method == "DELETE" and c.endpoint.endswith("issues/32/labels/meld%3Aplanned") for c in fake.calls)
    with pytest.raises(Refused, match="only a human"):
        plan_approve(make_product(tmp_path / "o", role="orchestrator")[1])


def test_plan_approve_adds_the_label_last_so_a_mid_flip_failure_can_be_retried(tmp_path):
    # F5: the meld:plan-approved label is what makes the milestone "executing" (and plan approve refuse as already approved),
    # so it must be the LAST call -- a failure while flipping planned issues then leaves a retryable, still-planning board.
    fake, human, repo, origin = make_product(tmp_path, group=None)
    planned = [{"number": n, "title": f"P{n}", "body": f"plan-item: p{n}\nplan-ref: plan-3\n", "state": "open",
                "labels": [{"name": "meld:planned"}, {"name": "worker:backend"}], "milestone": {"number": 3}} for n in (31, 32)]
    planning_board(fake, planned)
    fake.route("GET", R + "issues/10/comments", fixture("comments_10.json") + PLAN_THREAD)
    for n in (10, 31):
        fake.route("POST", R + f"issues/{n}/labels", [])
    for n in (31, 32):
        fake.route("DELETE", R + f"issues/{n}/labels/meld%3Aplanned", [])
    fake.route("POST", R + "issues/32/labels", {"message": "boom"}, status=500)     # #32's flip fails the first time round
    with pytest.raises(GitHubError):
        plan_approve(human)
    coordination_labels = lambda: [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/labels"]  # noqa: E731
    assert coordination_labels() == []                                                # not approved: nothing was labelled on #10
    assert [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/31/labels"] == [{"labels": ["meld:ready"]}]
    fake.route("POST", R + "issues/32/labels", [])                                    # the transient failure is over
    assert plan_approve(human) == {"approved": "plan-3", "ready": [31, 32]}
    assert coordination_labels() == [{"labels": ["meld:plan-approved"]}]
    assert [c.body for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/31/labels"] == [{"labels": ["meld:ready"]}] * 2   # idempotent re-flip


def test_plan_ack_posts_ack_and_readies_the_additions(tmp_path):
    fake, human, repo, origin = make_product(tmp_path, group=None)
    add_text = "- id: audit-log | title: Audit log | worker: backend | model: claude-sonnet-5 | effort: medium | size: M\n  - every write is logged\n\nverified-by: meld roster"
    thread = fixture("comments_10.json") + [
        {"id": 311, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T11:00:00Z", "body": "[codex] PROPOSE plan-add-1\n\n" + add_text},
        {"id": 312, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T11:01:00Z", "body": "[claude] AGREE plan-add-1\n\nok"},
        {"id": 313, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T11:02:00Z", "body": "[joint] PLAN-ADD plan-add-1\n\n" + add_text}]
    fake.route("GET", R + "issues/10/comments", thread)
    issues = fixture("issues.json") + [{"number": 33, "title": "Audit log", "body": "plan-item: audit-log\nplan-ref: plan-add-1\n", "state": "open",
                                        "labels": [{"name": "meld:planned"}], "milestone": {"number": 3}}]
    fake.route("GET", R + "issues", issues)
    fake.route("POST", R + "issues/33/labels", [])
    fake.route("DELETE", R + "issues/33/labels/meld%3Aplanned", [])
    out = plan_ack(human, "plan-add-1")
    assert out == {"acked": "plan-add-1", "ready": [33]}
    posted = [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues/10/comments"]
    assert posted[-1].startswith("[human] ACK plan-add-1\n")


def test_plan_ack_is_idempotent_when_already_acked(tmp_path):
    fake, human, repo, origin = make_product(tmp_path, group=None)
    add_text = "- id: audit-log | title: Audit log | worker: backend | model: claude-sonnet-5 | effort: medium | size: M\n  - every write is logged\n\nverified-by: meld roster"
    thread = fixture("comments_10.json") + [
        {"id": 311, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T11:00:00Z", "body": "[codex] PROPOSE plan-add-1\n\n" + add_text},
        {"id": 312, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T11:01:00Z", "body": "[claude] AGREE plan-add-1\n\nok"},
        {"id": 313, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T11:02:00Z", "body": "[joint] PLAN-ADD plan-add-1\n\n" + add_text},
        {"id": 314, "user": {"login": "empowerScott"}, "created_at": "2026-09-03T11:03:00Z", "body": "[human] ACK plan-add-1\n\nplan-add-1 acknowledged; its issues are ready"}]
    fake.route("GET", R + "issues/10/comments", thread)
    issues = fixture("issues.json") + [{"number": 33, "title": "Audit log", "body": "plan-item: audit-log\nplan-ref: plan-add-1\n", "state": "open",
                                        "labels": [{"name": "meld:planned"}], "milestone": {"number": 3}}]
    fake.route("GET", R + "issues", issues)
    fake.route("POST", R + "issues/33/labels", [])
    fake.route("DELETE", R + "issues/33/labels/meld%3Aplanned", [])
    out = plan_ack(human, "plan-add-1")
    assert out == {"acked": "plan-add-1", "ready": [33]}
    assert not any(c.method == "POST" and c.endpoint == R + "issues/10/comments" for c in fake.calls)
    assert any(c.method == "POST" and c.endpoint == R + "issues/33/labels" for c in fake.calls)   # still readied
