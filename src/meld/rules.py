"""Legal-action computation. Every invariant lives here and has a test."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from meld.board import Board, Issue, PullRequest
from meld.config import Config, Party
from meld.grammar import agreement_for
from meld.models import SIZES, ModelRegistry, size_fits


class Verb(str, Enum):
    CLAIM = "claim"
    RELEASE = "release"
    DISPATCH = "dispatch"
    COLLECT = "collect"
    REOPEN = "reopen"
    APPROVE = "approve"
    REQUEST_CHANGES = "request-changes"
    PUSH_FIX = "push-fix"
    MERGE = "merge"
    POST = "post"
    REAP = "reap"
    TAG = "tag"
    REVIEW = "review"        # dispatch a review run on a PR
    AUDIT = "audit"          # dispatch an audit run on a merged PR
    CHALLENGE = "challenge"  # open or act on a CHALLENGE (see challenge_rule)
    DECIDE = "decide"        # record a verified [joint] DECISION as a memory PR
    REMEMBER = "remember"    # append a lesson about a resolved issue or PR as a memory PR
    PLAN = "plan"            # the plan gate: create planned issues, approve the plan, or ack an addition


# POST is deliberately absent from both: it is refusable but not a per-issue or per-PR verb,
# so it never appears in an issue's or a PR's legal-action list.
ISSUE_VERBS = (Verb.CLAIM, Verb.RELEASE, Verb.DISPATCH, Verb.COLLECT, Verb.REOPEN, Verb.TAG, Verb.REAP, Verb.REMEMBER)
PR_VERBS = (Verb.APPROVE, Verb.REQUEST_CHANGES, Verb.PUSH_FIX, Verb.MERGE, Verb.REVIEW, Verb.AUDIT, Verb.REAP, Verb.REMEMBER)


@dataclass(frozen=True)
class Actor:
    party: Party
    login: str
    roster: frozenset[str] = frozenset()

    @property
    def is_human(self) -> bool:
        return self.party.kind == "human"

    @property
    def is_pm(self) -> bool:
        return self.party.kind == "pm"

    @property
    def group(self) -> str | None:
        return self.party.group

    @property
    def name(self) -> str:
        return self.group or f"human:{self.login}"


@dataclass(frozen=True)
class Refusal:
    verb: Verb
    target: str
    rule: str

    def __str__(self) -> str:
        return f"{self.verb.value} {self.target} refused: {self.rule}"


def party_of_login(cfg: Config, login: str) -> str | None:
    g = cfg.group_for_bot(login)
    if g is not None:
        return g.name
    if cfg.is_human(login):
        return f"human:{login}"
    return None


# ---- issue verbs -------------------------------------------------------------

def _claim(board: Board, cfg: Config, actor: Actor, issue: Issue, models: ModelRegistry) -> str | None:
    if board.halted:
        return "HALT is in effect"
    if board.phase != "executing":
        return "milestone is still in planning (no meld:plan-approved label)"
    if not (actor.is_pm or actor.is_human):
        return "only a PM or a human may claim"
    if issue.status != "ready":
        return f"issue is {issue.status}, not ready"
    if issue.assignees:
        return f"already claimed by {', '.join(issue.assignees)}"
    if actor.is_human:
        return None
    group = cfg.groups[actor.group]  # type: ignore[index]
    if issue.worker is None:
        return "issue has no worker: label"
    if issue.worker not in actor.roster:
        return f"worker type '{issue.worker}' is not in {group.name}'s roster"
    n = board.in_progress_count(group.bot)
    if n >= group.concurrency:
        return f"{group.name} is at its concurrency cap ({n}/{group.concurrency})"
    if issue.model is None:
        return "issue has no model: label"
    tier = models.tier_of(group.cli, issue.model)
    if tier is None:
        return f"model '{issue.model}' is unknown for adapter '{group.cli}'"
    used = board.spend.get(group.name, {}).get(tier, 0)
    cap = cfg.budget.get(tier, 0)
    if used >= cap:
        return f"{group.name} has exhausted its {tier}-tier budget ({used}/{cap})"
    return None


def _dispatch(board: Board, cfg: Config, actor: Actor, issue: Issue, models: ModelRegistry) -> str | None:
    if board.halted:
        return "HALT is in effect"
    if not actor.is_pm:
        return "only the PM may dispatch"
    group = cfg.groups[actor.group]  # type: ignore[index]
    if issue.status != "in_progress":
        return f"issue is {issue.status}, not in-progress"
    if group.bot not in issue.assignees:
        return f"issue is not assigned to {group.bot}"
    run = board.run_for(group.name, issue.number)
    if run is not None and (run.alive or not run.collected):
        return "a worker run already exists for this issue (running or uncollected)"
    if board.is_challenged(f"#{issue.number}"):
        return "issue has an open CHALLENGE"
    missing = [name for name, val in (("model", issue.model), ("effort", issue.effort), ("size", issue.size)) if val is None]
    if missing:
        return f"issue is missing labels: {', '.join(missing)}"
    errors = models.validate(group.cli, issue.model, issue.effort)  # type: ignore[arg-type]
    if errors:
        return errors[0]
    if issue.size not in SIZES:
        return f"size '{issue.size}' is not one of S, M, L"
    spec = models.get(group.cli, issue.model)  # type: ignore[arg-type]
    assert spec is not None
    if not size_fits(issue.size, spec.context_class):  # type: ignore[arg-type]
        return f"size {issue.size} does not fit model {issue.model} (context class {spec.context_class})"
    return None


def _collect(board: Board, cfg: Config, actor: Actor, issue: Issue) -> str | None:
    if not actor.is_pm:
        return "only the PM may collect"
    run = board.run_for(actor.group or "", issue.number)
    if run is None:
        return f"no run record for #{issue.number} in {actor.group}"
    if run.alive:
        return "worker is still running"
    if run.collected:
        return "run already collected"
    return None


def lease_rule(board: Board, issue: Issue) -> str | None:
    """Why a non-owner may still release: the owner's lease has expired."""
    lease = board.leases.get(issue.number)
    if lease is None:
        return None
    return f"lease by {lease.group} expired at {lease.until}" if board.lease_expired(issue.number) else None


def _release(board: Board, cfg: Config, actor: Actor, issue: Issue) -> str | None:
    if issue.status not in ("in_progress", "blocked"):
        return f"issue is {issue.status}; release is only legal from in-progress or blocked"
    if actor.is_human:
        return None
    group = cfg.groups[actor.group]  # type: ignore[index]
    if group.bot in issue.assignees:
        return None
    if actor.is_pm and lease_rule(board, issue):
        return None
    return f"issue is not assigned to {group.bot}"


def _reopen(board: Board, cfg: Config, actor: Actor, issue: Issue) -> str | None:
    if issue.state != "closed":
        return "issue is not closed"
    if actor.is_human:
        return None
    ref = f"#{issue.number}"
    pr = board.pr_for_issue(issue.number)
    # spec 8 names the audited work by the issue it closes; spec 14 posts AUDIT per PR. A FAIL
    # verdict may therefore land under either ref -- accept an AUDIT FAIL keyed on the issue's own
    # number or on the number of the PR that closed it.
    refs = {ref} | ({f"#{pr.number}"} if pr is not None else set())
    audits = [
        c for c in board.comments
        if c.ok and c.header is not None and c.header.verb == "AUDIT" and c.header.ref in refs
        and c.header.qualifier == "FAIL" and c.header.party.group == actor.group
    ]
    if not audits:
        return f"no AUDIT {ref} FAIL posted by {actor.group}"
    if pr is not None and party_of_login(cfg, pr.author) == actor.group:
        return f"{actor.group} authored the PR that closed {ref}"
    return None


def _tag(board: Board, cfg: Config, actor: Actor, issue: Issue) -> str | None:
    if not (actor.is_human or actor.party.kind == "group"):
        return "only the orchestrator or a human may tag"
    if issue.state != "open":
        return "issue is closed"
    return None


def reap_rule(board: Board, cfg: Config, actor: Actor, number: int, issue: Issue | None, pr: PullRequest | None) -> str | None:
    """A collected, dead run may be reaped once its work is over: a build once its issue is closed or its PR merged/closed;
    a review or audit run as soon as it is collected."""
    if not actor.is_pm:
        return "only the PM may reap"
    run = board.run_for(actor.group or "", number)
    if run is None:
        return f"no run record for #{number} in {actor.group}"
    if run.alive:
        return "worker is still running"
    if not run.collected:
        return "run has not been collected; collect first"
    if run.kind != "build":
        return None
    linked = board.pr_for_issue(number) if issue is not None else pr
    done = (issue is not None and issue.state == "closed") or (linked is not None and (linked.merged or linked.state == "closed"))
    return None if done else "issue is still open and its PR is not merged"


def issue_refusal(board: Board, cfg: Config, actor: Actor, verb: Verb, issue: Issue, models: ModelRegistry) -> Refusal | None:
    if verb == Verb.CLAIM:
        rule = _claim(board, cfg, actor, issue, models)
    elif verb == Verb.DISPATCH:
        rule = _dispatch(board, cfg, actor, issue, models)
    elif verb == Verb.COLLECT:
        rule = _collect(board, cfg, actor, issue)
    elif verb == Verb.RELEASE:
        rule = _release(board, cfg, actor, issue)
    elif verb == Verb.REOPEN:
        rule = _reopen(board, cfg, actor, issue)
    elif verb == Verb.TAG:
        rule = _tag(board, cfg, actor, issue)
    elif verb == Verb.REAP:
        rule = reap_rule(board, cfg, actor, issue.number, issue, None)
    elif verb == Verb.REMEMBER:
        rule = remember_rule(board, cfg, actor, f"#{issue.number}")
    else:
        rule = f"{verb.value} is not an issue verb"
    return Refusal(verb, f"#{issue.number}", rule) if rule else None


def legal_issue_verbs(board: Board, cfg: Config, actor: Actor, issue: Issue, models: ModelRegistry) -> list[str]:
    return [v.value for v in ISSUE_VERBS if issue_refusal(board, cfg, actor, v, issue, models) is None]


# ---- PR verbs ----------------------------------------------------------------

UNKNOWN_COMMITTER = "head committer unknown; cannot verify who made the last commit"
# GitHub returns 422 when the account that opened a PR submits an APPROVE or REQUEST_CHANGES review on it. Meld's PR
# author is the author group's bot (collect opens the PR with the group's token), so after another group push-fixes
# the head (spec 13 step 3) the author group's verdict can only reach GitHub as a COMMENT review; `meld review` does
# that and records the verdict in the group's commit status, which is what the quorum then counts.
AUTHOR_RULE = ("GitHub does not let a PR author's account approve or request changes on its own PR; "
               "post the verdict as a comment review (meld review does this for you)")
VERB_FOR = {"approve": Verb.APPROVE, "request-changes": Verb.REQUEST_CHANGES, "push-fix": Verb.PUSH_FIX}


def status_context(group: str) -> str:
    """The commit-status context `meld review` writes for a group's verdict on a head."""
    return f"meld/review/{group}"


def committer_party(cfg: Config, pr: PullRequest) -> str | None:
    return None if pr.head_committer is None else party_of_login(cfg, pr.head_committer)


def author_party(cfg: Config, pr: PullRequest) -> str | None:
    return party_of_login(cfg, pr.author)


def approving_parties(cfg: Config, pr: PullRequest) -> frozenset[str]:
    committer = committer_party(cfg, pr)
    parties = {party_of_login(cfg, login) for login in pr.approvals_on_head()}
    have = {p for p in parties if p is not None and p != committer}
    # The author group's approval never appears in GitHub's review list (see AUTHOR_RULE): it reaches GitHub as a
    # COMMENT review, and its `meld/review/<group>` status on this exact head is the record of it. Only `meld review`,
    # running with that group's token, writes the context, and a status is per-sha, so a later push leaves it behind.
    # Humans are not counted this way: a human's own PR is reviewed by every group, and a human approves in the UI.
    author = author_party(cfg, pr)
    if author is not None and author in cfg.groups and author != committer and pr.statuses.get(status_context(author)) == "success":
        have.add(author)
    return frozenset(have)


def pingpong_exceeded(cfg: Config, pr: PullRequest) -> bool:
    return pr.changes_requested_total() >= cfg.pingpong_cap


def _approve(board: Board, cfg: Config, actor: Actor, pr: PullRequest) -> str | None:
    if actor.name == committer_party(cfg, pr):
        return f"{actor.name} made the last commit on this PR and cannot approve its own head"
    if actor.name == author_party(cfg, pr):
        return AUTHOR_RULE
    if board.is_challenged(f"#{pr.number}"):
        return "PR has an open CHALLENGE"
    if pr.head_committer is None:
        return UNKNOWN_COMMITTER
    return None


def _other_party_only(cfg: Config, actor: Actor, pr: PullRequest, verb: Verb) -> str | None:
    if actor.name == committer_party(cfg, pr):
        return f"{actor.name} made the last commit on this PR; only other parties may {verb.value}"
    return None


def _merge(board: Board, cfg: Config, actor: Actor, pr: PullRequest) -> str | None:
    if board.halted:
        return "HALT is in effect"
    if not (actor.is_pm or actor.is_human):
        return "only a PM or a human may merge"
    if board.is_challenged(f"#{pr.number}"):
        return "PR has an open CHALLENGE"
    if pr.head_committer is None:
        return UNKNOWN_COMMITTER
    if pr.checks != "green":
        return f"required checks are {pr.checks}, not green"
    have = approving_parties(cfg, pr)
    need = cfg.effective_quorum
    if len(have) < need:
        return f"{len(have)}/{need} approvals from non-committer parties (have: {sorted(have)})"
    return None


def _run_slot(board: Board, cfg: Config, actor: Actor, number: int) -> str | None:
    group = cfg.groups[actor.group]  # type: ignore[index]
    live = [r for r in board.runs.get(group.name, {}).values() if r.alive and r.kind in ("review", "audit")]
    if len(live) >= group.review_concurrency:
        return f"{group.name} is at its review concurrency cap ({len(live)}/{group.review_concurrency})"
    existing = board.run_for(group.name, number)
    if existing is not None and (existing.alive or not existing.collected):
        return "a run already exists for this PR (running or uncollected)"
    return None


def _review(board: Board, cfg: Config, actor: Actor, pr: PullRequest) -> str | None:
    if board.halted:
        return "HALT is in effect"
    if not actor.is_pm:
        return "only the PM may dispatch a review"
    blocker = _other_party_only(cfg, actor, pr, Verb.REVIEW)   # the head committer's group never reviews its own head
    if blocker:
        return blocker
    if board.is_challenged(f"#{pr.number}"):
        return "PR has an open CHALLENGE"
    if pr.memory:
        return f"memory PR: run `meld review {pr.number}` directly, no worker needed"
    return _run_slot(board, cfg, actor, pr.number)


def _audit(board: Board, cfg: Config, actor: Actor, pr: PullRequest) -> str | None:
    if board.halted:
        return "HALT is in effect"
    if not actor.is_pm:
        return "only the PM may dispatch an audit"
    if not pr.merged:
        return "audit targets a merged PR"
    if pr.memory:
        # Its content was checked against the board (the verified [joint] DECISION, or the item's resolution) when it was
        # reviewed; there is no worker output to audit, and no auditor could judge it better than that check did.
        return "memory PR: verified against the board at review, never audited"
    if party_of_login(cfg, pr.author) == actor.group:
        return "cannot audit a PR authored by own group"
    if actor.group in board.audits.get(pr.number, {}):
        return f"{actor.group} already audited #{pr.number}"
    return _run_slot(board, cfg, actor, pr.number)


CONCERNS = ("correctness and security", "tests and product conventions", "architecture, performance, and visual design")


def review_concern(cfg: Config, pr: PullRequest, group: str) -> str | None:
    """Role-matched review (spec 21.3): with three or more groups, each reviewing group takes a different primary concern.
    Only the head committer's own party is excluded from reviewing (matching `_review`'s eligibility rule) — the PR's
    author remains eligible, since spec 13 step 3 has group A's PM dispatch its own reviewer on a head that group B
    push-fixed onto A's PR."""
    excluded = {committer_party(cfg, pr)} - {None}
    reviewers = sorted(g.name for g in cfg.groups.values() if g.name not in excluded)
    if len(reviewers) < 2 or group not in reviewers:
        return None
    return CONCERNS[reviewers.index(group) % len(CONCERNS)]


def pr_refusal(board: Board, cfg: Config, actor: Actor, verb: Verb, pr: PullRequest) -> Refusal | None:
    if verb == Verb.REAP:
        rule: str | None = reap_rule(board, cfg, actor, pr.number, None, pr)
    elif verb == Verb.AUDIT:
        rule = _audit(board, cfg, actor, pr)
    elif verb == Verb.REMEMBER:
        rule = remember_rule(board, cfg, actor, f"#{pr.number}")
    elif pr.state != "open":
        rule = "PR is not open"
    elif verb == Verb.APPROVE:
        rule = _approve(board, cfg, actor, pr)
    elif verb == Verb.REQUEST_CHANGES:
        rule = _other_party_only(cfg, actor, pr, verb) or (AUTHOR_RULE if actor.name == author_party(cfg, pr) else None)
    elif verb == Verb.PUSH_FIX:
        rule = _other_party_only(cfg, actor, pr, verb)   # committer-only: a push approves nothing, and GitHub allows an author's COMMENT review
    elif verb == Verb.MERGE:
        rule = _merge(board, cfg, actor, pr)
    elif verb == Verb.REVIEW:
        rule = _review(board, cfg, actor, pr)
    else:
        rule = f"{verb.value} is not a PR verb"
    return Refusal(verb, f"#{pr.number}", rule) if rule else None


def legal_pr_verbs(board: Board, cfg: Config, actor: Actor, pr: PullRequest) -> list[str]:
    """What `actor` may do to `pr` now. approve/request-changes are reported through the path `meld review` really
    takes: the author group's verdict is legal as a COMMENT review (review_verdict_rule), and a memory PR's verdict
    follows memory_review_rule -- so the array never says approve is illegal at the moment `meld review` would post one."""
    out = []
    for v in PR_VERBS:
        if v in (Verb.APPROVE, Verb.REQUEST_CHANGES):
            verdict = "approve" if v == Verb.APPROVE else "request-changes"
            rule = memory_review_rule(board, cfg, actor, pr) if pr.memory else review_verdict_rule(board, cfg, actor, pr, verdict)
        else:
            refusal = pr_refusal(board, cfg, actor, v, pr)
            rule = None if refusal is None else refusal.rule
        if rule is None:
            out.append(v.value)
    return out


def review_verdict_rule(board: Board, cfg: Config, actor: Actor, pr: PullRequest, verdict: str) -> str | None:
    """Why `meld review` may not submit `verdict` on `pr`, or None. The author party's approve and request-changes are
    refused as GitHub verbs (AUTHOR_RULE) but legal here as a COMMENT review plus status, under the same conditions
    every other approver meets: the PR is open, the author is not the head committer, the head committer is known,
    and (for approve) the PR carries no open CHALLENGE. Every other case defers to `pr_refusal`."""
    if actor.name == author_party(cfg, pr) and verdict in ("approve", "request-changes"):
        if pr.state != "open":
            return "PR is not open"
        if actor.name == committer_party(cfg, pr):
            return f"{actor.name} made the last commit on this PR and cannot review its own head"
        if pr.head_committer is None:
            return UNKNOWN_COMMITTER
        if verdict == "approve" and board.is_challenged(f"#{pr.number}"):
            return "PR has an open CHALLENGE"
        return None
    refusal = pr_refusal(board, cfg, actor, VERB_FOR[verdict], pr)
    return None if refusal is None else refusal.rule


def memory_review_rule(board: Board, cfg: Config, actor: Actor, pr: PullRequest) -> str | None:
    """Legality of `meld review`'s auto-approval/request-changes on a memory PR: PM-only, then exactly `_approve`'s rules."""
    if not actor.is_pm:
        return "only the PM may approve a memory PR"
    refusal = pr_refusal(board, cfg, actor, Verb.APPROVE, pr)
    return None if refusal is None else refusal.rule


# ---- challenges ------------------------------------------------------------------

CHALLENGE_ACTIONS = ("open", "evidence", "retract", "uphold", "dismiss")


def challenged_party(board: Board, cfg: Config, ref: str) -> str | None:
    """Who must answer a challenge on `ref`: a PR's head committer's party (else its author's), an issue's first
    configured assignee's party, or an agreement ref's proposer. None when nobody owns the claim."""
    if ref.startswith("#") and ref[1:].isdigit():
        n = int(ref[1:])
        if n in board.pulls:
            pr = board.pulls[n]
            return committer_party(cfg, pr) or party_of_login(cfg, pr.author)
        if n in board.issues:
            for login in board.issues[n].assignees:
                party = party_of_login(cfg, login)
                if party:
                    return party
            return None
    return agreement_for(board.comments, ref, cfg).proposer


def challenge_rule(board: Board, cfg: Config, actor: Actor, action: str, ref: str) -> str | None:
    if action not in CHALLENGE_ACTIONS:
        return f"unknown challenge action '{action}'"
    if not (actor.is_human or actor.party.kind == "group"):
        return "only an orchestrator or a human may act on challenges"
    current = board.challenges.get(ref)
    target = challenged_party(board, cfg, ref)
    if action == "open":
        if current is not None:
            return f"{ref} already has an open CHALLENGE by {current.challenger}"
        if target is not None and target == actor.name:
            return "a party cannot challenge its own claim"
        return None
    if current is None:
        return f"{ref} has no open CHALLENGE"
    if action == "evidence":
        if actor.is_human or target is None or actor.name == target:
            return None
        return f"only the challenged party ({target}) or a human may post EVIDENCE"
    if action == "retract":
        if actor.is_human or actor.name == current.challenger or (target is not None and actor.name == target):
            return None
        return f"only the challenger ({current.challenger}) or the challenged party ({target}) or a human may RETRACT"
    if actor.name == current.challenger:
        return "the challenger cannot decide its own challenge"
    if target is not None and actor.name == target:
        return "the challenged party cannot decide the challenge against it"
    return None


# ---- Agreement-ref verbs (plan 5, spec 21 item 18): the legality plan.py and memory.py used to compute inline ----------

def decide_rule(board: Board, cfg: Config, actor: Actor, ref: str) -> str | None:
    """Why `meld decide REF` is refused for `actor`, or None. Everything here is board-derived; memory.decide adds the
    one check that needs git (the decision file already on origin's default branch) with the same message."""
    if actor.party.kind not in ("group", "pm"):
        return "only the proposer group's orchestrator or PM may record a decision"
    agreement = agreement_for(board.comments, ref, cfg)
    if not agreement.joint_posted or agreement.joint_verb != "DECISION":
        return f"no verified [joint] DECISION {ref} on the coordination issue"
    if not agreement.joint_verified:
        return f"the [joint] DECISION {ref} comment is not verified (wrong author or posted before the round closed)"
    if agreement.status not in ("agreed", "majority"):
        return f"{ref} ended in DISAGREE; the human decides"
    if agreement.proposer != actor.group:
        return f"only the proposer ({agreement.proposer}) may record {ref}"
    if any(p.memory is not None and p.memory[:2] == ("decision", ref) for p in board.pulls.values() if p.state == "open"):
        return f"{ref} is already recorded or has an open memory PR"
    return None


def _verified_plan_joint(board: Board, cfg: Config, ref: str, verbs: tuple[str, ...]) -> str | None:
    agreement = agreement_for(board.comments, ref, cfg)
    if not agreement.joint_posted or agreement.joint_verb not in verbs:
        return f"no verified [joint] {'/'.join(verbs)} {ref} on the coordination issue"
    if not agreement.joint_verified:
        return f"the [joint] {agreement.joint_verb} {ref} comment is not verified (wrong author or posted before the round closed)"
    return None


def plan_rule(board: Board, cfg: Config, actor: Actor, action: str, ref: str) -> str | None:
    """Why `meld plan <action> REF` is refused, or None. `action` is issues, approve, or ack."""
    if action == "issues":
        if actor.party.kind != "group":
            return "only the proposer group's orchestrator may create plan issues"
        rule = _verified_plan_joint(board, cfg, ref, ("PLAN", "PLAN-ADD"))
        if rule:
            return rule
        agreement = agreement_for(board.comments, ref, cfg)
        if agreement.joint_verb == "PLAN" and board.phase != "planning":
            return f"{ref} was already approved; its issues can no longer be created"
        if agreement.proposer != actor.group:
            return f"only the proposer ({agreement.proposer}) may create the issues for {ref}"
        return None
    if action == "approve":
        if not actor.is_human:
            return "only a human may approve the plan"
        if board.phase != "planning":
            return "the plan is already approved (milestone is executing)"
        return _verified_plan_joint(board, cfg, ref, ("PLAN",))
    if action == "ack":
        if not actor.is_human:
            return "only a human may ack a plan addition"
        return _verified_plan_joint(board, cfg, ref, ("PLAN-ADD",))
    return f"{action} is not a plan action"


def remember_rule(board: Board, cfg: Config, actor: Actor, ref: str) -> str | None:
    if actor.party.kind not in ("group", "pm"):
        return "only an orchestrator or PM may record a lesson"
    n = int(ref[1:]) if ref.startswith("#") and ref[1:].isdigit() else None
    issue = board.issues.get(n) if n is not None else None
    pr = board.pulls.get(n) if n is not None else None
    if issue is None and pr is None:
        return f"{ref} is not an issue or PR on the board"
    if issue is not None and issue.status in ("blocked", "needs_human"):
        return f"{ref} is still {issue.status}; a lesson is recorded once the item resolves"
    if board.is_challenged(ref):
        return f"{ref} has an open CHALLENGE; resolve it first"
    return None


def legal_agreement_verbs(board: Board, cfg: Config, actor: Actor, ref: str) -> list[str]:
    """The agreement-ref actions `actor` may take on `ref` right now, in the CLI's spelling."""
    out = []
    if decide_rule(board, cfg, actor, ref) is None:
        out.append("decide")
    if plan_rule(board, cfg, actor, "issues", ref) is None:
        out.append("plan-issues")
    if ref == f"plan-{board.milestone}" and plan_rule(board, cfg, actor, "approve", ref) is None:
        out.append("plan-approve")
    if plan_rule(board, cfg, actor, "ack", ref) is None:
        out.append("plan-ack")
    return out
