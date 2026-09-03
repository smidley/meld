"""Typed board model. Pure data and derivations; no GitHub access here."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from moot.grammar import ParsedComment

STATUS_LABELS = {
    "moot:planned": "planned",
    "moot:ready": "ready",
    "moot:in-progress": "in_progress",
    "moot:in-review": "in_review",
    "moot:blocked": "blocked",
    "moot:needs-human": "needs_human",
}
_STATUS_PRECEDENCE = ("needs_human", "blocked", "in_review", "in_progress", "ready", "planned")
CHALLENGED_LABEL = "moot:challenged"
COORDINATION_LABEL = "moot:coordination"
PLAN_APPROVED_LABEL = "moot:plan-approved"


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    labels: frozenset[str]
    assignees: tuple[str, ...]
    state: str
    milestone: int | None
    created_at: str = ""
    updated_at: str = ""

    @property
    def status(self) -> str:
        if self.state == "closed":
            return "closed"
        present = {STATUS_LABELS[l] for l in self.labels if l in STATUS_LABELS}
        for s in _STATUS_PRECEDENCE:
            if s in present:
                return s
        return "unlabeled"

    def label_value(self, prefix: str) -> str | None:
        for l in sorted(self.labels):
            if l.startswith(prefix):
                return l[len(prefix):]
        return None

    @property
    def worker(self) -> str | None:
        return self.label_value("worker:")

    @property
    def model(self) -> str | None:
        return self.label_value("model:")

    @property
    def effort(self) -> str | None:
        return self.label_value("effort:")

    @property
    def size(self) -> str | None:
        return self.label_value("size:")

    @property
    def challenged(self) -> bool:
        return CHALLENGED_LABEL in self.labels


@dataclass(frozen=True)
class Review:
    author: str
    state: str
    commit_id: str
    submitted_at: str = ""


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    body: str
    head_ref: str
    head_sha: str
    author: str
    head_committer: str
    issue_number: int | None
    reviews: tuple[Review, ...]
    checks: str
    labels: frozenset[str]
    state: str
    merged: bool

    def latest_reviews(self) -> dict[str, Review]:
        latest: dict[str, Review] = {}
        for r in sorted(self.reviews, key=lambda r: r.submitted_at):
            if r.state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                latest[r.author] = r
        return latest

    def approvals_on_head(self) -> frozenset[str]:
        return frozenset(a for a, r in self.latest_reviews().items() if r.state == "APPROVED" and r.commit_id == self.head_sha)

    def changes_requested_on_head(self) -> frozenset[str]:
        return frozenset(a for a, r in self.latest_reviews().items() if r.state == "CHANGES_REQUESTED" and r.commit_id == self.head_sha)

    def changes_requested_total(self) -> int:
        return sum(1 for r in self.reviews if r.state == "CHANGES_REQUESTED")

    @property
    def challenged(self) -> bool:
        return CHALLENGED_LABEL in self.labels


@dataclass(frozen=True)
class RunRecord:
    group: str
    issue: int
    kind: str
    pr: int | None
    pid: int
    started_at: str
    worktree: str
    log_path: str
    model: str
    effort: str
    tier: str
    milestone: int
    alive: bool
    collected: bool

    @property
    def finished(self) -> bool:
        return not self.alive and not self.collected


@dataclass(frozen=True)
class Board:
    repo: str
    milestone: int
    coordination_issue: int
    phase: str
    halted: bool
    issues: dict[int, Issue]
    pulls: dict[int, PullRequest]
    comments: tuple[ParsedComment, ...]
    runs: dict[str, dict[int, RunRecord]]
    spend: dict[str, dict[str, int]]
    open_challenges: frozenset[str]
    ignored_comments: int

    def in_progress_count(self, bot: str) -> int:
        return sum(1 for i in self.issues.values() if i.status == "in_progress" and bot in i.assignees)

    def pr_for_issue(self, issue: int) -> PullRequest | None:
        candidates = [p for p in self.pulls.values() if p.issue_number == issue]
        candidates.sort(key=lambda p: (p.state != "open", -p.number))
        return candidates[0] if candidates else None

    def run_for(self, group: str, issue: int) -> RunRecord | None:
        return self.runs.get(group, {}).get(issue)

    def is_challenged(self, ref: str) -> bool:
        if ref in self.open_challenges:
            return True
        if ref.startswith("#") and ref[1:].isdigit():
            n = int(ref[1:])
            if n in self.issues and self.issues[n].challenged:
                return True
            if n in self.pulls and self.pulls[n].challenged:
                return True
        return False


def derive_phase(coordination_labels: frozenset[str]) -> str:
    return "executing" if PLAN_APPROVED_LABEL in coordination_labels else "planning"


def _ordered(comments: Iterable[ParsedComment]) -> list[ParsedComment]:
    return sorted(comments, key=lambda c: (c.created_at, c.id))


def derive_halted(comments: Sequence[ParsedComment]) -> bool:
    halted = False
    for c in _ordered(comments):
        if not c.ok or c.header is None or c.header.party.kind != "human":
            continue
        if c.header.verb == "HALT":
            halted = True
        elif c.header.verb == "RESUME":
            halted = False
    return halted


def derive_open_challenges(comments: Sequence[ParsedComment]) -> frozenset[str]:
    open_refs: set[str] = set()
    for c in _ordered(comments):
        if not c.ok or c.header is None or not c.header.ref:
            continue
        if c.header.verb == "CHALLENGE":
            open_refs.add(c.header.ref)
        elif c.header.verb in ("UPHELD", "DISMISSED", "RETRACT"):
            open_refs.discard(c.header.ref)
    return frozenset(open_refs)
