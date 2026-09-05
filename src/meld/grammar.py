"""Coordination-comment grammar: `[<party>] <VERB> [<ref>] [<qualifier>]`."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from meld.config import Config, Party

ORCHESTRATOR_VERBS = frozenset({
    "ROSTER", "ROSTER-CHANGE", "CONFIG", "PROPOSE", "AMEND", "AGREE", "DISSENT", "DRAFT", "ACK",
    "NEEDS-HUMAN", "BUDGET", "CHALLENGE", "EVIDENCE", "RETRACT", "UPHELD", "DISMISSED", "AUDIT",
    "DRIFT", "PROPOSE-WORKER", "PING", "SOLO", "RESUMED", "PROTOCOL-VIOLATION",
})
PM_VERBS = frozenset({
    "REVIEW-REQUEST", "STATUS", "NEEDS-HUMAN", "BUDGET", "PROPOSE-WORKER", "AUDIT",
    "PROTOCOL-VIOLATION", "IGNORED-COMMENT", "PING", "RESUMED", "LEASE", "EXPIRED",
})
HUMAN_VERBS = frozenset({
    "ASK", "DIRECTIVE", "AMEND", "APPROVE", "ACK", "PROVIDED", "RESOLVED", "HALT", "RESUME", "REVERT",
    "RELEASE", "CHALLENGE", "EVIDENCE", "RETRACT", "UPHELD", "DISMISSED",
})
JOINT_VERBS = frozenset({"PLAN", "PLAN-ADD", "ANSWER", "DECISION", "WORKER"})
AGREEMENT_VERBS = frozenset({"PROPOSE", "DRAFT", "AMEND", "AGREE", "DISSENT"})

REQUIRES_REF = frozenset({
    "PROPOSE", "AMEND", "AGREE", "DISSENT", "DRAFT", "ANSWER", "ASK", "DIRECTIVE", "REVIEW-REQUEST",
    "NEEDS-HUMAN", "CHALLENGE", "EVIDENCE", "RETRACT", "UPHELD", "DISMISSED", "AUDIT", "DECISION",
    "PROPOSE-WORKER", "PROVIDED", "RESOLVED", "REVERT", "RELEASE", "PLAN-ADD", "ACK", "WORKER",
    "LEASE", "EXPIRED",
})
REQUIRES_VERIFIED_BY = frozenset({"PLAN", "DECISION", "ANSWER", "EVIDENCE"})

_HEADER_RE = re.compile(
    r"^\s*\[(?P<party>[A-Za-z0-9_-]+(?::pm)?)\]\s+(?P<verb>[A-Z][A-Z-]*)"
    r"(?:\s+(?P<ref>(?![A-Z][A-Z-]*\s*$)\S+))?(?:\s+(?P<qualifier>[A-Z][A-Z-]*))?\s*$"
)
_VERIFIED_RE = re.compile(r"^\s*verified-by:\s*\S.*$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class Header:
    party: Party
    verb: str
    ref: str | None
    qualifier: str | None


@dataclass(frozen=True)
class ParsedComment:
    id: int
    author: str
    created_at: str
    body: str
    header: Header | None
    authorized: bool
    errors: tuple[str, ...]

    @property
    def text(self) -> str:
        if self.header is None:
            return self.body.strip()
        first_nl = self.body.find("\n")
        return "" if first_nl == -1 else self.body[first_nl + 1:].strip()

    @property
    def ok(self) -> bool:
        return self.header is not None and self.authorized and not self.errors


def allowed_verbs(party: Party) -> frozenset[str]:
    return {"group": ORCHESTRATOR_VERBS, "pm": PM_VERBS, "human": HUMAN_VERBS, "joint": JOINT_VERBS}[party.kind]


def parse_header(line: str, cfg: Config) -> Header | None:
    m = _HEADER_RE.match(line)
    if not m:
        return None
    party = Party.parse(m.group("party"), cfg)
    if party is None:
        return None
    verb = m.group("verb")
    if verb not in allowed_verbs(party):
        return None
    return Header(party, verb, m.group("ref"), m.group("qualifier"))


def has_verified_by(body: str) -> bool:
    return _VERIFIED_RE.search(body) is not None


def validate(header: Header, body: str) -> list[str]:
    errors: list[str] = []
    if header.verb in REQUIRES_REF and not header.ref:
        errors.append(f"{header.verb} requires a ref")
    if header.verb in REQUIRES_VERIFIED_BY and not has_verified_by(body):
        errors.append(f"{header.verb} requires a verified-by: line")
    return errors


def is_authorized(author: str, header: Header, cfg: Config) -> bool:
    p = header.party
    if p.kind == "human":
        return cfg.is_human(author)
    if p.kind == "joint":
        return cfg.group_for_bot(author) is not None
    group = cfg.groups.get(p.group or "")
    return group is not None and group.bot == author


def parse_comment(raw: dict, cfg: Config) -> ParsedComment:
    body = raw.get("body") or ""
    author = (raw.get("user") or {}).get("login", "")
    first_line = body.split("\n", 1)[0]
    header = parse_header(first_line, cfg)
    authorized = header is not None and is_authorized(author, header, cfg)
    errors = tuple(validate(header, body)) if header is not None else ()
    return ParsedComment(
        id=int(raw["id"]), author=author, created_at=str(raw.get("created_at", "")),
        body=body, header=header, authorized=authorized, errors=errors,
    )


def compose(header: Header, text: str) -> str:
    parts = [header.party.label, header.verb]
    if header.ref:
        parts.append(header.ref)
    if header.qualifier:
        parts.append(header.qualifier)
    return " ".join(parts) + "\n\n" + text.strip() + "\n"


@dataclass(frozen=True)
class Agreement:
    ref: str
    text: str | None
    proposer: str | None
    agreed: frozenset[str]
    dissent: dict[str, str]
    rounds: int
    status: str
    joint_posted: bool
    joint_verified: bool = False
    joint_verb: str | None = None
    joint_author: str | None = None

    def can_amend(self, cfg: Config) -> bool:
        return self.rounds < cfg.amend_cap


def agreement_for(comments: Sequence[ParsedComment], ref: str, cfg: Config) -> Agreement:
    text: str | None = None
    proposer: str | None = None
    agreed: set[str] = set()
    dissent: dict[str, str] = {}
    rounds = 0
    joint_posted = False
    joint_verb: str | None = None
    joint_author: str | None = None
    joint_verified = False
    for cmt in sorted(comments, key=lambda x: (x.created_at, x.id)):
        if not cmt.ok or cmt.header is None or cmt.header.ref != ref:
            continue
        h = cmt.header
        if h.party.kind == "joint":
            joint_posted, joint_verb, joint_author = True, h.verb, cmt.author
            closed = text is not None and ((agreed >= set(cfg.group_names)) or ((agreed | set(dissent)) >= set(cfg.group_names)))
            joint_verified = closed and proposer is not None and cmt.author == cfg.groups[proposer].bot
            continue
        if h.party.kind not in ("group", "pm") or h.party.group is None:
            continue
        g = h.party.group
        if h.verb in ("PROPOSE", "DRAFT", "AMEND"):
            # A re-proposal (PROPOSE/DRAFT when text is not None) counts as an amend round
            if text is not None:
                rounds += 1
            text, proposer, agreed, dissent = cmt.text, g, {g}, {}
        elif h.verb == "AGREE" and text is not None:
            agreed.add(g)
            dissent.pop(g, None)
        elif h.verb == "DISSENT" and text is not None:
            dissent[g] = cmt.text
            agreed.discard(g)
    groups = set(cfg.group_names)
    if text is None:
        status = "none"
    elif agreed >= groups:
        status = "agreed"
    elif (agreed | set(dissent)) < groups:
        status = "open"
    elif len(agreed) * 2 > len(groups):
        status = "majority"
    else:
        status = "disagree"
    return Agreement(ref, text, proposer, frozenset(agreed), dissent, rounds, status, joint_posted,
                      joint_verified, joint_verb, joint_author)
