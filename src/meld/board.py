"""Typed board model. Pure data and derivations; no GitHub access here."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatch
from typing import Iterable, Mapping, Sequence

from meld.grammar import ParsedComment

STATUS_LABELS = {
    "meld:planned": "planned",
    "meld:ready": "ready",
    "meld:in-progress": "in_progress",
    "meld:in-review": "in_review",
    "meld:blocked": "blocked",
    "meld:needs-human": "needs_human",
}
_STATUS_PRECEDENCE = ("needs_human", "blocked", "in_review", "in_progress", "ready", "planned")
CHALLENGED_LABEL = "meld:challenged"
COORDINATION_LABEL = "meld:coordination"
PLAN_APPROVED_LABEL = "meld:plan-approved"


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
    head_committer: str | None      # None when the last commit maps to no GitHub account
    issue_number: int | None
    reviews: tuple[Review, ...]
    checks: str
    labels: frozenset[str]
    state: str
    merged: bool
    files: tuple[str, ...] = ()
    merge_commit_sha: str | None = None
    merged_at: str = ""

    def touches(self, patterns: Iterable[str]) -> bool:
        """True when any changed file matches any glob (fnmatch: `*` also crosses `/`)."""
        return any(fnmatch(f, p) for f in self.files for p in patterns)

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
    deadline: str = ""
    branch: str = ""
    lease_comment: int | None = None
    timed_out: bool = False
    head_sha: str = ""

    @property
    def finished(self) -> bool:
        return not self.alive and not self.collected


# Strict ISO-8601 UTC (`Z`) shape only: lease_expired compares `until` to `board.now`
# lexicographically, which is only a valid stand-in for chronological order when both
# strings share this exact zero-padded, zone-suffixed shape. Anything looser (an offset
# like "+00:00", a missing "Z", free text) would make a malformed lease compare as
# "less than" nothing and so never expire, so it is rejected here rather than accepted
# and mis-ordered later.
_LEASE_UNTIL_RE = re.compile(r"^\s*until:\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Lease:
    issue: int
    group: str
    until: str
    comment_id: int


@dataclass(frozen=True)
class Challenge:
    ref: str
    challenger: str        # a group name, or "human:<login>"
    comment_id: int
    opened_at: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_lease_until(text: str) -> str | None:
    m = _LEASE_UNTIL_RE.search(text or "")
    return m.group(1) if m else None


def derive_leases(comments: Sequence[ParsedComment]) -> dict[int, Lease]:
    """The lease each LEASE comment still asserts, before `assigned_leases` checks it against the board."""
    leases: dict[int, Lease] = {}
    for c in _ordered(comments):
        if not c.ok or c.header is None or not c.header.ref or not c.header.ref.startswith("#") or not c.header.ref[1:].isdigit():
            continue
        n = int(c.header.ref[1:])
        if c.header.verb == "LEASE" and c.header.party.kind == "pm" and c.header.party.group:
            until = parse_lease_until(c.text)
            if until:
                leases[n] = Lease(n, c.header.party.group, until, c.id)
        elif (c.header.verb == "EXPIRED" and c.header.party.kind == "pm") or (c.header.verb == "RELEASE" and c.header.party.kind == "human"):
            leases.pop(n, None)
    return leases


# A lease is valid only while the leasing group's bot is assigned to the issue. The LEASE
# comment outlives the claim it describes: `meld release` posts nothing, so once the leasing
# group lets go and another group claims the issue, the old comment would still read as a
# lease on the new claim, and once its `until` passed any PM could release the new owner
# (a LEASE forged by hand would do the same on purpose). Assignment, not the thread, is the
# claim lock (spec section 6), so it decides validity too. The check lives here, next to the
# derivation, but is applied by the board assembler, which knows each group's bot; Board
# itself stays free of Config.
def assigned_leases(leases: Mapping[int, Lease], issues: Mapping[int, Issue], bot_of_group: Mapping[str, str]) -> dict[int, Lease]:
    """Keep only the leases whose group's bot is currently assigned to the leased issue."""
    out: dict[int, Lease] = {}
    for n, lease in leases.items():
        issue = issues.get(n)
        bot = bot_of_group.get(lease.group)
        if issue is not None and bot is not None and bot in issue.assignees:
            out[n] = lease
    return out


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
    leases: dict[int, Lease] = field(default_factory=dict)
    plan_text: str = ""
    now: str = ""
    challenges: dict[str, Challenge] = field(default_factory=dict)
    audits: dict[int, dict[str, str]] = field(default_factory=dict)   # PR number -> group -> "pass" | "fail"

    def lease_expired(self, issue: int) -> bool:
        lease = self.leases.get(issue)
        return bool(lease and self.now and lease.until < self.now)

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


def _challenger(c: ParsedComment) -> str:
    return c.header.party.group if c.header is not None and c.header.party.group else f"human:{c.author}"


def derive_challenges(comments: Sequence[ParsedComment]) -> dict[str, Challenge]:
    """Open challenges by ref: CHALLENGE opens (a later CHALLENGE on the same ref replaces it), UPHELD/DISMISSED/RETRACT close."""
    open_: dict[str, Challenge] = {}
    for c in _ordered(comments):
        if not c.ok or c.header is None or not c.header.ref:
            continue
        if c.header.verb == "CHALLENGE":
            open_[c.header.ref] = Challenge(c.header.ref, _challenger(c), c.id, c.created_at)
        elif c.header.verb in ("UPHELD", "DISMISSED", "RETRACT"):
            open_.pop(c.header.ref, None)
    return open_


def derive_open_challenges(comments: Sequence[ParsedComment]) -> frozenset[str]:
    return frozenset(derive_challenges(comments))


def derive_audits(comments: Sequence[ParsedComment]) -> dict[int, dict[str, str]]:
    """AUDIT #<pr> PASS|FAIL by a group (orchestrator or PM): the latest verdict per group per PR."""
    audits: dict[int, dict[str, str]] = {}
    for c in _ordered(comments):
        h = c.header
        if not c.ok or h is None or h.verb != "AUDIT" or not h.party.group or not h.ref or not h.ref.startswith("#") or not h.ref[1:].isdigit():
            continue
        if h.qualifier in ("PASS", "FAIL"):
            audits.setdefault(int(h.ref[1:]), {})[h.party.group] = h.qualifier.lower()
    return audits
