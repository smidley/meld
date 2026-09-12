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
NUDGED_LABEL = "meld:nudged"
CLAIM_LABEL_PREFIX = "meld:claimed-by:"
HUMAN_CLAIMANT = "human"   # the one claimant name every human shares; a reserved group name (config.GROUP_NAME_RE)


def claim_label(group: str) -> str:
    """The label that is a group's claim on an issue (spec 6): one per claiming party, PAT or App alike."""
    return CLAIM_LABEL_PREFIX + group


def claim_group(label: str) -> str | None:
    """The claiming party a label names, or None for any other label (the bare prefix names nobody). GitHub label names
    are case-insensitive for uniqueness, so a hand-made `Meld:Claimed-By:Claude` is our own label; group names are
    lowercase by rule (`config.GROUP_NAME_RE`), so the party it names is lowercased with it."""
    label = label.lower()
    return label[len(CLAIM_LABEL_PREFIX):] or None if label.startswith(CLAIM_LABEL_PREFIX) else None


HELD_LABEL_PREFIX = "meld:held:"
HELD_CATEGORIES = ("cleanup", "credential-change", "delete", "live-outreach", "paid-media", "production-mutation", "public-release")


def held_label(category: str) -> str:
    return HELD_LABEL_PREFIX + category


def fenced_lines(lines: Sequence[str]) -> frozenset[int]:
    """Indices of the lines inside fenced code, fence lines included. A fence is a MATCHED pair of lines whose first
    non-space characters are ```; an unterminated opener is inert. Both readers of an issue body use this one rule, so
    what `replace_section` writes is always what `body_section` reads -- and a truncated paste or a code block a human
    left open while editing can never swallow the declarations below it (a scope that vanished would fail OPEN:
    `Board.scope_violations` refuses nothing when no scope is declared)."""
    fences = [i for i, line in enumerate(lines) if line.lstrip().startswith("```")]
    if len(fences) % 2:
        fences = fences[:-1]
    inside: set[int] = set()
    for opener, closer in zip(fences[0::2], fences[1::2]):
        inside.update(range(opener, closer + 1))
    return frozenset(inside)


def body_section(body: str, heading: str) -> tuple[str, ...]:
    """The `- ` bullets under `## <heading>` in an issue body, up to the next heading; backticks stripped. Declarations
    live in the body because only orchestrators and humans can write it (spec 6): `## Scope`, `## May delete`.

    Fenced code is skipped (`fenced_lines`): issue bodies quote diffs and sample bodies routinely, and a quoted
    `## Scope`/`- glob` would otherwise read as the real declaration -- widening a scope, or the deletion allowlist,
    by example."""
    out: list[str] = []
    active = False
    lines = (body or "").splitlines()
    fenced = fenced_lines(lines)
    for i, line in enumerate(lines):
        if i in fenced:
            continue
        if line.startswith("## "):
            active = line[3:].strip().lower() == heading.lower()
            continue
        if active and line.lstrip().startswith("- "):
            item = line.lstrip()[2:].strip().strip("`").strip()
            if item:
                out.append(item)
    return tuple(out)


def _section_headings(lines: Sequence[str]) -> list[int]:
    """Indices of the `## ` heading lines outside fenced code -- the same reading `body_section` applies, so the section
    `replace_section` rewrites is always the one the board will read, never a heading quoted inside a fence."""
    fenced = fenced_lines(lines)
    return [i for i, line in enumerate(lines) if i not in fenced and line.startswith("## ")]


def replace_section(body: str, heading: str, items: Sequence[str]) -> str:
    """Return `body` with its `## <heading>` section written as `- ` bullets of `items`: rewritten in place when the
    section exists (everything before and after it is kept byte for byte), appended when it does not, removed when
    `items` is empty. A section runs from its heading to the next `## ` heading, including the blank lines that
    separate them (a rewrite leaves exactly one; a removal of the last section leaves no trailing blank). A non-empty
    result ends in exactly one newline; a body emptied by a removal is `""`. Headings inside fenced code are neither
    found nor treated as section ends, exactly as `body_section` skips them."""
    lines = (body or "").rstrip("\n").splitlines()
    block = [f"## {heading}"] + [f"- {item}" for item in items] if items else []
    headings = _section_headings(lines)
    start = next((i for i in headings if lines[i][3:].strip().lower() == heading.lower()), None)
    if start is not None:
        end = next((i for i in headings if i > start), len(lines))
        if block:
            block[0] = lines[start]          # keep the heading exactly as the body spells it
        lines[start:end] = block + [""] if block and end < len(lines) else block
        while lines and lines[-1] == "":
            lines.pop()
    elif block:
        if lines and lines[-1] != "":
            lines.append("")
        lines += block
    return "\n".join(lines) + "\n" if lines else ""


# Branches `meld decide`/`meld remember` push to. collect's build branches are `meld/<issue>-<slug>` (gitops.branch_name), so
# the two namespaces never overlap; memory.py imports this rather than redefining it (board.py is the leaf both can reach).
MEMORY_BRANCH_PREFIX = "meld/memory-"
# The one meld-memory marker pattern: PullRequest.memory below and memory.py both match against it (memory.py imports
# it as MARKER_RE) so a decision to change the marker's shape never needs to be made in two places at once.
MEMORY_MARKER_RE = re.compile(r"^meld-memory: (decision|lesson) (\S+) ([0-9a-f]{64})$", re.MULTILINE)


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    labels: frozenset[str]
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
    def skills(self) -> tuple[str, ...]:
        return tuple(sorted(label[len("skill:"):] for label in self.labels if label.startswith("skill:")))

    @property
    def challenged(self) -> bool:
        return CHALLENGED_LABEL in self.labels

    @property
    def claimants(self) -> tuple[str, ...]:
        """Every party holding a claim label, sorted. One is the normal case; two or more is a contested issue."""
        # A set: GitHub label names are unique case-insensitively, but claim_group lowercases, so two case variants of
        # one group's label (impossible through GitHub, possible in a hand-built board) must not read as contested.
        return tuple(sorted({g for g in (claim_group(l) for l in self.labels) if g}))

    @property
    def claimed_by(self) -> str | None:
        """The single claim holder, or None when the issue is free or contested."""
        c = self.claimants
        return c[0] if len(c) == 1 else None

    @property
    def scope(self) -> tuple[str, ...]:
        return body_section(self.body, "Scope")

    @property
    def may_delete(self) -> tuple[str, ...]:
        return body_section(self.body, "May delete")

    @property
    def held(self) -> tuple[str, ...]:
        """Held categories on this issue (spec 6): dispatch needs a listed human's answer while any is present. Matched
        case-insensitively like claim labels: GitHub label names are unique case-insensitively, and a human may type
        `Meld:Held:Delete` by hand -- it must still engage the gate."""
        lowered = (l.lower() for l in self.labels)
        return tuple(sorted(l[len(HELD_LABEL_PREFIX):] for l in lowered if l.startswith(HELD_LABEL_PREFIX) and len(l) > len(HELD_LABEL_PREFIX)))


# The first line of the author group's COMMENT-review body (review.py), so assemble._pull can tell that review apart
# from an ordinary comment: GitHub refuses an APPROVE/REQUEST_CHANGES from the PR's own author, so that group's verdict
# goes out as a COMMENT review instead, and this marker is what lets it still count as a real verdict (Review.as_author)
# rather than reading as unreviewed. Defined here, a leaf both assemble.py and review.py import, rather than in either
# of them, so neither has to import the other.
AUTHOR_VERDICT_MARKER = "meld-verdict:"


@dataclass(frozen=True)
class Review:
    author: str
    state: str
    commit_id: str
    submitted_at: str = ""
    as_author: bool = False
    body: str = ""            # the review text: a retry's build brief quotes CHANGES_REQUESTED bodies so the worker can address them   # True when this is the PR author group's verdict, posted as a COMMENT review (see AUTHOR_VERDICT_MARKER)


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
    removed: tuple[str, ...] = ()   # files the PR deletes (GitHub status "removed"; a rename is not a deletion)
    merge_commit_sha: str | None = None
    merged_at: str = ""
    statuses: dict[str, str] = field(default_factory=dict)   # commit-status context -> state, on the head

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
        """Request-changes rounds toward the ping-pong cap: distinct (reviewer, head) pairs, so a reviewer's repeated
        verdict on one unchanged head counts once and a dismissed review not at all (spec 13 step 5)."""
        return len({(r.author, r.commit_id) for r in self.reviews if r.state == "CHANGES_REQUESTED"})

    @property
    def challenged(self) -> bool:
        return CHALLENGED_LABEL in self.labels

    @property
    def memory(self) -> tuple[str, str, str] | None:
        """(kind, ref, hash) when this is a memory PR opened by `meld decide`/`meld remember`: a `meld/memory-*` branch AND
        the body marker, else None. The branch gate is what keeps a body marker harmless: a worker's summary reaches an
        ordinary `meld/<issue>-<slug>` PR's body (collect breaks marker lines too), and a body alone must never turn a
        build PR into one that is refused a reviewer, never returned to in_progress, and never audited."""
        if not (self.head_ref or "").startswith(MEMORY_BRANCH_PREFIX):
            return None
        m = MEMORY_MARKER_RE.search(self.body or "")
        return (m.group(1), m.group(2), m.group(3)) if m else None


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
    worker: str = ""            # the worker type the run was dispatched with (reviewer, security, auditor, ...)

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
    comment_id: int = 0    # informational only; no decision path reads it


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
    """The lease each LEASE comment still asserts, before `held_leases` checks it against the board."""
    leases: dict[int, Lease] = {}
    for c in _ordered(comments):
        if not c.ok or c.header is None or not c.header.ref or not c.header.ref.startswith("#") or not c.header.ref[1:].isdigit():
            continue
        n = int(c.header.ref[1:])
        if c.header.verb == "LEASE" and c.header.party.kind == "pm" and c.header.party.group:
            until = parse_lease_until(c.text)
            if until:
                leases[n] = Lease(n, c.header.party.group, until, c.id)
        elif (c.header.verb in ("EXPIRED", "RELEASED") and c.header.party.kind in ("pm", "group")) or \
                (c.header.verb == "RELEASE" and c.header.party.kind == "human"):
            leases.pop(n, None)
    return leases


# A lease is valid only while the leasing group holds the issue's claim label. The LEASE comment can outlive the
# claim it describes: a group's or PM's own `meld release` posts a framework RELEASED note and a human's `--force` a
# `[human] RELEASE`, both of which `derive_leases` drops the lease on (plan-5 decision 2) -- but a human's plain
# release posts nothing, and a claim can end without any comment at all (a label removed on GitHub). So once the
# leasing group lets go and another group claims the issue, an old LEASE could still read as a lease on the new
# claim, and once its `until` passed any PM could release the new owner (a LEASE forged by hand would do the same on
# purpose). The label, not the thread, is the claim lock (spec section 6), so it decides validity too.
def held_leases(leases: Mapping[int, Lease], issues: Mapping[int, Issue]) -> dict[int, Lease]:
    """Keep only the leases whose group currently holds a claim label on the leased issue. `claimants`, not `claimed_by`:
    on a contested issue (two labels) each claimant's lease stays valid, so the ordinary expiry path can clear it."""
    return {n: lease for n, lease in leases.items() if (issue := issues.get(n)) is not None and lease.group in issue.claimants}


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
    bots: dict[str, str] = field(default_factory=dict)   # group -> bot login, from the config (assemble)

    def lease_expired(self, issue: int) -> bool:
        lease = self.leases.get(issue)
        return bool(lease and self.now and lease.until < self.now)

    def holder_login(self, issue: int) -> str | None:
        """The bot login of the group holding `issue`'s claim, or None (free, contested, human-held, unknown group)."""
        iss = self.issues.get(issue)
        return self.bots.get(iss.claimed_by) if iss is not None and iss.claimed_by else None

    def in_progress_count(self, group: str) -> int:
        return sum(1 for i in self.issues.values() if i.status == "in_progress" and i.claimed_by == group)

    def pr_for_issue(self, issue: int) -> PullRequest | None:
        candidates = [p for p in self.pulls.values() if p.issue_number == issue]
        iss = self.issues.get(issue)
        issue_closed = iss is not None and iss.state == "closed"
        holder = self.holder_login(issue)
        # Open issue: the open PR is live, preferring one authored by the current holder's bot (a stale PR from an
        # earlier claimant loses); closed issue: the PR that merged is the record.
        if issue_closed:
            candidates.sort(key=lambda p: (not p.merged, p.state != "open", -p.number))
        else:
            candidates.sort(key=lambda p: (p.state != "open", p.author != holder, -p.number))
        return candidates[0] if candidates else None

    def _issue_for(self, pr: PullRequest) -> Issue | None:
        """The issue a PR closes, when it is on the board."""
        return self.issues.get(pr.issue_number) if pr.issue_number is not None else None

    def scope_violations(self, pr: PullRequest) -> tuple[str, ...]:
        """Changed files outside the linked issue's declared `## Scope`; empty when no scope is declared or no issue is linked."""
        issue = self._issue_for(pr)
        if issue is None or not issue.scope:
            return ()
        return tuple(f for f in pr.files if not any(fnmatch(f, g) for g in issue.scope))

    def blocked_deletions(self, pr: PullRequest) -> tuple[str, ...]:
        """Deleted files the linked issue's `## May delete` does not list; the gate is always on for a PR that closes an issue."""
        issue = self._issue_for(pr)
        if issue is None:
            return ()
        return tuple(f for f in pr.removed if f not in issue.may_delete)

    def human_answer(self, issue: int) -> ParsedComment | None:
        """The newest `PROVIDED #issue` / `RESOLVED #issue` from a listed human. A stranger's `[human]` comment still
        parses with party kind "human" (the bracket names no login, see config.Party.parse), but grammar.is_authorized
        checks `cfg.is_human(author)` at parse time, so an impostor's comment already carries `ok=False` -- the `c.ok`
        filter below is enough on its own and no separate humans list is needed on the board (see assemble.build_board,
        which parses every coordination comment with the real cfg before Board ever sees it)."""
        answers = [c for c in self.comments if c.ok and c.header is not None and c.header.party.kind == "human"
                   and c.header.verb in ("PROVIDED", "RESOLVED") and c.header.ref == f"#{issue}"]
        return max(answers, key=lambda c: c.id) if answers else None

    def needs_human_answered(self, issue: int) -> bool:
        """A listed human answered after the newest `NEEDS-HUMAN #issue` -- the condition that unlocks a needs-human or
        held issue (spec 8, 14). An ask must exist: a `meld:needs-human` label with nothing behind it never
        auto-unlocks. Answering "any answer counts when no ask was posted" instead meant that on an issue which had
        ever received a human `PROVIDED`/`RESOLVED`, a hold added later (a hand-set label, or one whose ask failed to
        post) read as already authorized -- `dispatch` was legal and the next `tick` swapped the label straight back.
        A PM's, an orchestrator's, or a human's own `NEEDS-HUMAN #issue` is what stamps a hold."""
        answer = self.human_answer(issue)
        if answer is None:
            return False
        asks = [c.id for c in self.comments if c.ok and c.header is not None and c.header.verb == "NEEDS-HUMAN" and c.header.ref == f"#{issue}"]
        return bool(asks) and answer.id > max(asks)

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
