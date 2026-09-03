"""Coordination-comment grammar: `[<party>] <VERB> [<ref>] [<qualifier>]`."""
from __future__ import annotations

import re
from dataclasses import dataclass

from moot.config import Config, Party

ORCHESTRATOR_VERBS = frozenset({
    "ROSTER", "ROSTER-CHANGE", "CONFIG", "PROPOSE", "AMEND", "AGREE", "DISSENT", "DRAFT", "ACK",
    "NEEDS-HUMAN", "BUDGET", "CHALLENGE", "EVIDENCE", "RETRACT", "UPHELD", "DISMISSED", "AUDIT",
    "DRIFT", "PROPOSE-WORKER", "PING", "SOLO", "RESUMED", "PROTOCOL-VIOLATION",
})
PM_VERBS = frozenset({
    "REVIEW-REQUEST", "STATUS", "NEEDS-HUMAN", "BUDGET", "PROPOSE-WORKER", "AUDIT",
    "PROTOCOL-VIOLATION", "IGNORED-COMMENT", "PING", "RESUMED",
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
