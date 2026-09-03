"""Assemble a Board from GitHub. The only reader of GitHub in the coordination core."""
from __future__ import annotations

import re

from moot.board import (
    COORDINATION_LABEL,
    Board,
    Issue,
    PullRequest,
    Review,
    RunRecord,
    derive_halted,
    derive_open_challenges,
    derive_phase,
)
from moot.config import Config
from moot.github import GitHub
from moot.grammar import parse_comment

BRANCH_PREFIX = "moot/"
_CLOSES_RE = re.compile(r"\b(?:closes|fixes|resolves)\s+#(\d+)", re.IGNORECASE)
_BRANCH_RE = re.compile(r"^moot/(\d+)-")


class BoardError(RuntimeError):
    pass


def _labels(raw: dict) -> frozenset[str]:
    return frozenset(l["name"] for l in raw.get("labels", []))


def find_active_milestone(gh: GitHub) -> tuple[int, int]:
    for m in sorted(gh.list_open_milestones(), key=lambda m: m["number"]):
        for issue in gh.list_issues(m["number"]):
            if COORDINATION_LABEL in _labels(issue):
                return m["number"], issue["number"]
    raise BoardError("no open milestone has an issue labelled moot:coordination; run `moot setup` first")


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
    head_committer = author
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
            head_committer = ((last.get("author") or {}).get("login") or (last.get("committer") or {}).get("login") or author)
        checks = checks_state(gh.check_runs(head_sha))
    return PullRequest(
        number=number, title=raw.get("title", ""), body=raw.get("body") or "", head_ref=raw["head"]["ref"], head_sha=head_sha,
        author=author, head_committer=head_committer, issue_number=issue_number_from_pr(raw.get("body") or "", raw["head"]["ref"]),
        reviews=reviews, checks=checks, labels=_labels(raw), state=state, merged=merged,
    )


def build_board(gh: GitHub, cfg: Config, runs: dict[str, dict[int, RunRecord]], spend: dict[str, dict[str, int]],
                milestone: int | None = None) -> Board:
    if milestone is None:
        milestone, coordination = find_active_milestone(gh)
        raw_issues = gh.list_issues(milestone)
    else:
        raw_issues = gh.list_issues(milestone)
        coord = [i for i in raw_issues if COORDINATION_LABEL in _labels(i)]
        if not coord:
            raise BoardError(f"milestone {milestone} has no issue labelled moot:coordination")
        coordination = coord[0]["number"]
    coord_raw = next(i for i in raw_issues if i["number"] == coordination)
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
    )
