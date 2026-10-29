"""Assemble a Board from GitHub. The only reader of GitHub in the coordination core."""
from __future__ import annotations

import re

from meld.board import (
    AUTHOR_VERDICT_MARKER,
    COORDINATION_LABEL,
    Board,
    Issue,
    PullRequest,
    Review,
    RunRecord,
    derive_audits,
    derive_challenges,
    derive_halted,
    derive_leases,
    derive_phase,
    held_leases,
    utc_now_iso,
)
from meld.config import Config
from meld.github import GitHub, GitHubError, RateLimited
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


FAILED_CONCLUSIONS = ("failure", "timed_out", "cancelled", "action_required", "startup_failure")


def check_failures(gh: GitHub, check_runs: dict) -> tuple[dict, ...]:
    """Why the head's checks are red (plan 68): one entry per failed check run with its name, its link and the messages of
    its annotations, and whether the job ran at all. The maze's PR #20 stayed red through three rebuilds because GitHub
    Actions never started a job ("recent account payments have failed"), which the annotations said and nobody could read."""
    out = []
    for run in check_runs.get("check_runs", []):
        if run.get("status") != "completed" or run.get("conclusion") not in FAILED_CONCLUSIONS:
            continue
        notes: list[str] = []
        try:
            for a in gh.check_run_annotations(int(run.get("id") or 0)):
                if a.get("annotation_level") in ("failure", "warning") and a.get("message"):
                    notes.append(str(a["message"]).strip()[:300])
        except GitHubError:
            pass
        started = not any("was not started" in n for n in notes)
        out.append({"name": run.get("name") or "", "conclusion": run.get("conclusion"), "url": run.get("details_url") or run.get("html_url") or "",
                    "notes": notes[:5], "started": started})
    return tuple(out)


def issue_number_from_pr(body: str, head_ref: str) -> int | None:
    m = _CLOSES_RE.search(body or "")
    if m:
        return int(m.group(1))
    b = _BRANCH_RE.match(head_ref or "")
    return int(b.group(1)) if b else None


def _issue(raw: dict) -> Issue:
    return Issue(
        number=raw["number"], title=raw.get("title", ""), body=raw.get("body") or "", labels=_labels(raw),
        state=raw.get("state", "open"),
        milestone=(raw.get("milestone") or {}).get("number"), created_at=raw.get("created_at", ""),
        updated_at=raw.get("updated_at", ""), closed_at=raw.get("closed_at") or "",
    )


def _review(r: dict) -> Review:
    """GitHub's review dict -> board.Review. The author group's approve/request-changes verdict cannot reach GitHub as
    an APPROVE/REQUEST_CHANGES (its own account authored the PR), so `meld review` posts it as a COMMENT review whose
    first line is the AUTHOR_VERDICT_MARKER (review.py). A request-changes marker is promoted here to a synthetic
    CHANGES_REQUESTED with as_author=True, so it counts toward the ping-pong cap like any other; an approve marker
    stays COMMENTED -- that verdict is carried by the group's `meld/review/<group>` commit status instead
    (rules.approving_parties) -- but is flagged as_author too, so rules._reviews_on_head knows the group has reviewed
    that head and refuses a second review run of it."""
    body = r.get("body") or ""
    first_line = body.split("\n", 1)[0]
    author = (r.get("user") or {}).get("login", "")
    state = r.get("state", "")
    commit_id = r.get("commit_id") or ""    # GitHub nulls commit_id once a review's commit is gone; keep it a string
    submitted_at = r.get("submitted_at", "")
    if state == "COMMENTED" and first_line == f"{AUTHOR_VERDICT_MARKER} request-changes":
        return Review(author=author, state="CHANGES_REQUESTED", commit_id=commit_id, submitted_at=submitted_at, as_author=True, body=body)
    if state == "COMMENTED" and first_line == f"{AUTHOR_VERDICT_MARKER} approve":
        return Review(author=author, state=state, commit_id=commit_id, submitted_at=submitted_at, as_author=True, body=body)
    return Review(author=author, state=state, commit_id=commit_id, submitted_at=submitted_at, body=body)


def _pull(gh: GitHub, raw: dict, errors: list[str] | None = None) -> PullRequest:
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
    files: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    statuses: dict[str, str] = {}
    failures: tuple[dict, ...] = ()
    if state == "open":
        reviews = tuple(_review(r) for r in gh.pull_reviews(number))
        commits = gh.pull_commits(number)
        if commits:
            last = commits[-1]
            head_committer = (last.get("author") or {}).get("login") or (last.get("committer") or {}).get("login") or None
        # Check runs need the Checks permission, which an App created by hand may lack: the first Antigravity group's
        # every `meld status` died on HTTP 403 here. Degrade to "unreadable" (so rules._merge refuses to merge on what it
        # cannot see, and the card says so) and record the gap; a rate limit still propagates.
        try:
            raw_runs = gh.check_runs(head_sha)
            checks = checks_state(raw_runs)
            if checks == "red":
                failures = check_failures(gh, raw_runs)
        except RateLimited:
            raise
        except GitHubError as e:
            checks = "unreadable"
            if errors is not None:
                note = f"PR #{number}: check runs unavailable ({e}); grant the token or App Checks: read"
                if note not in errors:
                    errors.append(note)
        entries = gh.pull_files(number)
        files = tuple(e["filename"] for e in entries)
        removed = tuple(e["filename"] for e in entries if e["status"] == "removed")
        # The `meld/review/<group>` contexts feed rules.approving_parties. Statuses only ever ADD approvals, so a
        # token minted without "Commit statuses: read" degrades to an empty map (and a recorded error) instead of
        # failing every command the moment one meld/ PR is open. A rate limit is not a permissions problem: it
        # propagates, as it does from every other call here.
        try:
            statuses = gh.combined_status(head_sha)
        except RateLimited:
            raise
        except GitHubError as e:
            statuses = {}
            if errors is not None:
                note = f"PR #{number}: commit statuses unavailable ({e}); check the token's Commit statuses permission"
                if note not in errors:   # build_status runs in loops (meld wait); one line per PR per process is enough
                    errors.append(note)
    # `merge_commit_sha` is only the real merge commit once the PR is merged: on an open PR GitHub fills it with the
    # sha of a speculative test merge (or null while it recomputes one), so consumers must gate on `pr.merged`
    # before treating it as a commit that exists on the default branch (dispatch does, for audit checkouts).
    return PullRequest(
        number=number, title=raw.get("title", ""), body=raw.get("body") or "", head_ref=raw["head"]["ref"], head_sha=head_sha,
        author=author, head_committer=head_committer, issue_number=issue_number_from_pr(raw.get("body") or "", raw["head"]["ref"]),
        reviews=reviews, checks=checks, labels=_labels(raw), state=state, merged=merged,
        files=files, removed=removed, merge_commit_sha=raw.get("merge_commit_sha"), merged_at=raw.get("merged_at") or "", statuses=statuses,
        failures=failures,
    )


def build_board(gh: GitHub, cfg: Config, runs: dict[str, dict[int, RunRecord]], spend: dict[str, dict[str, int]],
                milestone: int | None = None, now: str | None = None, errors: list[str] | None = None) -> Board:
    """Assemble the board. `errors` (optional) collects non-fatal read problems -- the caller's `StateStore.errors`
    in `status.build_status`, which the status JSON prints as `state_errors`."""
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
    ignored = sum(1 for c in comments if not cfg.is_party(c.author))
    issues = {i["number"]: _issue(i) for i in raw_issues if i["number"] != coordination}
    pulls: dict[int, PullRequest] = {}
    for raw in gh.list_pulls(state="all"):
        if not raw["head"]["ref"].startswith(BRANCH_PREFIX):
            continue
        if raw.get("state") == "closed" and raw.get("merged_at") is None:
            continue
        pulls[raw["number"]] = _pull(gh, raw, errors)
    challenges = derive_challenges(comments)
    return Board(
        repo=gh.repo, milestone=milestone, coordination_issue=coordination, phase=derive_phase(_labels(coord_raw)),
        halted=derive_halted(comments), issues=issues, pulls=pulls, comments=comments, runs=runs, spend=spend,
        open_challenges=frozenset(challenges), ignored_comments=ignored,
        leases=held_leases(derive_leases(comments), issues),
        plan_text=description, now=now or utc_now_iso(),
        challenges=challenges, audits=derive_audits(comments),
        goal=(coord_raw_list[0].get("body") or ""), goal_title=str(coord_raw_list[0].get("title") or ""), bots={g.name: g.bot for g in cfg.groups.values()},
    )
