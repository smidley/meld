"""Assemble a Board from GitHub. The only reader of GitHub in the coordination core."""
from __future__ import annotations

import re

from meld.board import (
    COORDINATION_LABEL,
    Board,
    Issue,
    PullRequest,
    Review,
    RunRecord,
    assigned_leases,
    derive_halted,
    derive_leases,
    derive_open_challenges,
    derive_phase,
    utc_now_iso,
)
from meld.config import Config
from meld.github import GitHub
from meld.grammar import parse_comment

BRANCH_PREFIX = "meld/"
_CLOSES_RE = re.compile(r"\b(?:closes|fixes|resolves)\s+#(\d+)", re.IGNORECASE)
_BRANCH_RE = re.compile(r"^meld/(\d+)-")


class BoardError(RuntimeError):
    pass


def _labels(raw: dict) -> frozenset[str]:
    return frozenset(l["name"] for l in raw.get("labels", []))


def _locate_active(gh: GitHub) -> tuple[int, int, list[dict], str]:
    """Find the active milestone with coordination issue. Returns (milestone_number, coordination_number, raw_issues, description)."""
    for m in sorted(gh.list_open_milestones(), key=lambda m: m["number"]):
        raw_issues = gh.list_issues(m["number"])
        for issue in raw_issues:
            if COORDINATION_LABEL in _labels(issue):
                return m["number"], issue["number"], raw_issues, m.get("description") or ""
    raise BoardError("no open milestone has an issue labelled meld:coordination; run `meld setup` first")


def find_active_milestone(gh: GitHub) -> tuple[int, int]:
    milestone, coordination, _, _ = _locate_active(gh)
    return milestone, coordination


def checks_state(check_runs: dict) -> str:
    runs = check_runs.get("check_runs", [])
    if not runs:
        return "none"
    if any(r.get("status") != "completed" for r in runs):
        return "pending"
    if all(r.get("conclusion") in ("success", "neutral", "skipped") for r in runs):
        return "green"
    return "red"


def issue_number_from_pr(body: str, head_ref: str) -> int | None:
    m = _CLOSES_RE.search(body or "")
    if m:
        return int(m.group(1))
    b = _BRANCH_RE.match(head_ref or "")
    return int(b.group(1)) if b else None


def _issue(raw: dict) -> Issue:
    return Issue(
        number=raw["number"], title=raw.get("title", ""), body=raw.get("body") or "", labels=_labels(raw),
        assignees=tuple(a["login"] for a in raw.get("assignees", [])), state=raw.get("state", "open"),
        milestone=(raw.get("milestone") or {}).get("number"), created_at=raw.get("created_at", ""),
        updated_at=raw.get("updated_at", ""),
    )


def _pull(gh: GitHub, raw: dict) -> PullRequest:
    number = raw["number"]
    head_sha = raw["head"]["sha"]
    author = (raw.get("user") or {}).get("login", "")
    merged = raw.get("merged_at") is not None
    state = raw.get("state", "open")
    reviews: tuple[Review, ...] = ()
    # Unknown until the commit list says otherwise. Falling back to the PR author would
    # let a group approve its own head whenever a commit has no linked GitHub account;
    # closed and merged PRs are never read, and accept no verbs anyway.
    head_committer: str | None = None
    checks = "none"
    if state == "open":
        reviews = tuple(
            Review(author=(r.get("user") or {}).get("login", ""), state=r.get("state", ""), commit_id=r.get("commit_id", ""),
                   submitted_at=r.get("submitted_at", ""))
            for r in gh.pull_reviews(number)
        )
        commits = gh.pull_commits(number)
        if commits:
            last = commits[-1]
            head_committer = (last.get("author") or {}).get("login") or (last.get("committer") or {}).get("login") or None
        checks = checks_state(gh.check_runs(head_sha))
    return PullRequest(
        number=number, title=raw.get("title", ""), body=raw.get("body") or "", head_ref=raw["head"]["ref"], head_sha=head_sha,
        author=author, head_committer=head_committer, issue_number=issue_number_from_pr(raw.get("body") or "", raw["head"]["ref"]),
        reviews=reviews, checks=checks, labels=_labels(raw), state=state, merged=merged,
    )


def build_board(gh: GitHub, cfg: Config, runs: dict[str, dict[int, RunRecord]], spend: dict[str, dict[str, int]],
                milestone: int | None = None, now: str | None = None) -> Board:
    if milestone is None:
        milestone, coordination, raw_issues, description = _locate_active(gh)
    else:
        raw_issues = gh.list_issues(milestone)
        coord = [i for i in raw_issues if COORDINATION_LABEL in _labels(i)]
        if not coord:
            raise BoardError(f"milestone {milestone} has no issue labelled meld:coordination")
        coordination = coord[0]["number"]
        description = gh.get_milestone(milestone).get("description") or ""
    coord_raw_list = [i for i in raw_issues if i["number"] == coordination]
    if not coord_raw_list:
        raise BoardError(f"coordination issue {coordination} not found in milestone {milestone} issues")
    coord_raw = coord_raw_list[0]
    comments = tuple(parse_comment(c, cfg) for c in gh.list_comments(coordination))
    known = {g.bot for g in cfg.groups.values()} | set(cfg.humans)
    ignored = sum(1 for c in comments if c.author not in known)
    issues = {i["number"]: _issue(i) for i in raw_issues if i["number"] != coordination}
    pulls: dict[int, PullRequest] = {}
    for raw in gh.list_pulls(state="all"):
        if not raw["head"]["ref"].startswith(BRANCH_PREFIX):
            continue
        if raw.get("state") == "closed" and raw.get("merged_at") is None:
            continue
        pulls[raw["number"]] = _pull(gh, raw)
    return Board(
        repo=gh.repo, milestone=milestone, coordination_issue=coordination, phase=derive_phase(_labels(coord_raw)),
        halted=derive_halted(comments), issues=issues, pulls=pulls, comments=comments, runs=runs, spend=spend,
        open_challenges=derive_open_challenges(comments), ignored_comments=ignored,
        leases=assigned_leases(derive_leases(comments), issues, {g.name: g.bot for g in cfg.groups.values()}),
        plan_text=description, now=now or utc_now_iso(),
    )
