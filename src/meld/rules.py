"""Legal-action computation. Every invariant lives here and has a test."""
from __future__ import annotations

import re

from dataclasses import dataclass
from enum import Enum

from meld.board import HUMAN_CLAIMANT, Board, Issue, PullRequest
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
    # Decision 11: the caller's own stale claim label does not block its re-claim. A PM killed between claim's label
    # add and its status swap leaves meld:ready plus its own label; on the next loop the add is a no-op, the re-read
    # shows exactly its own label, and the swap proceeds, so the crashed PM self-heals. Any other party's label refuses.
    own = HUMAN_CLAIMANT if actor.is_human else actor.group
    if any(g != own for g in issue.claimants):
        return f"already claimed by {', '.join(issue.claimants)}"
    waits = board.waits_for(issue)
    if waits:
        return f"waits for {', '.join(f'#{n}' for n in waits)} to close first (the plan's `after`)"
    if actor.is_human:
        return None
    group = cfg.groups[actor.group]  # type: ignore[index]
    if issue.worker is None:
        return "issue has no worker: label"
    if issue.worker not in actor.roster:
        return f"worker type '{issue.worker}' is not in {group.name}'s roster"
    n = board.in_progress_count(group.name)
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


CAPACITY_RETRY_SECONDS = 60


def _plus_seconds(iso: str, seconds: int) -> str:
    from datetime import datetime, timedelta, timezone
    try:
        then = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return iso
    return (then + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dispatch(board: Board, cfg: Config, actor: Actor, issue: Issue, models: ModelRegistry) -> str | None:
    if board.halted:
        return "HALT is in effect"
    if not actor.is_pm:
        return "only the PM may dispatch"
    group = cfg.groups[actor.group]  # type: ignore[index]
    if issue.status != "in_progress":
        return f"issue is {issue.status}, not in-progress"
    if issue.claimed_by != group.name:
        return (f"issue is claimed by {', '.join(issue.claimants)}, not {group.name}" if issue.claimants
                else f"issue is not claimed by {group.name}")
    if issue.held and not board.needs_human_answered(issue.number):
        return f"held ({', '.join(issue.held)}): a listed human must post RESOLVED #{issue.number} (or PROVIDED #{issue.number}) first"
    run = board.run_for(group.name, issue.number)
    if run is not None and (run.alive or not run.collected):
        return "a worker run already exists for this issue (running or uncollected)"
    waits = board.waits_for(issue)
    if waits:
        return f"waits for {', '.join(f'#{n}' for n in waits)} to close first (the plan's `after`)"
    if run is not None and run.kind == "build" and run.outcome == "capacity" and run.ended_at and board.now:
        # Plan 50: the model was at capacity; the same build is retried after a short pause, never re-tagged for it.
        retry_at = _plus_seconds(run.ended_at, CAPACITY_RETRY_SECONDS)
        if board.now < retry_at:
            return f"the model was at capacity at {run.ended_at}; dispatch the same tags again from {retry_at}"
    if (run is not None and run.kind == "build" and run.outcome in ("failed", "blocked") and run.attempt >= cfg.budget_attempts_per_issue
            and not board.answered_since(issue.number, run.ended_at or run.started_at)):
        # Plan 55: the maze run ran seven builds on one issue, the last three at max effort on the largest model. After the
        # cap a person decides what changes; a retry is not the orchestrator's to spend. Plan 60: the person's RESOLVED or
        # PROVIDED on the issue after that build is the decision, and the count starts again from it.
        return (f"{run.attempt} builds have run on #{issue.number} (budget.attempts_per_issue is {cfg.budget_attempts_per_issue}); "
                f"a person decides what changes: post NEEDS-HUMAN #{issue.number} and wait")
    if run is not None and run.kind == "build" and run.outcome == "failed" and run.model == issue.model and run.effort == issue.effort:
        # The rubric (section 9) says a retry after a failure raises effort a level or moves up a tier; the first live run's
        # PMs re-dispatched at the unchanged tag before the orchestrator had re-tagged (handoff residual 5). A request-changes
        # retry is not refused: the review may call for a different approach rather than more effort.
        return (f"retry at the unchanged tag ({issue.model}/{issue.effort}) after a failed run: the orchestrator re-tags per the rubric "
                f"first (raise effort a level or move up a tier)")
    pr = board.pr_for_issue(issue.number)
    if pr is not None and pr.state == "open" and pr.checks == "red" and pr.failures and not any(f.get("started", True) for f in pr.failures):
        # Plan 68: the checks did not run at all (billing, a spending limit, a runner that never came). The maze's claude PM
        # rebuilt #4 three times against a red PR whose jobs GitHub had refused to start.
        note = next((n for f in pr.failures for n in f.get("notes", [])), "the job was not started")
        return (f"PR #{pr.number}'s checks did not run ({note[:140]}); a rebuild cannot change that: "
                f"post NEEDS-HUMAN #{issue.number} quoting it and wait")
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
    # Decision 11: a `ready` issue carrying a claim label is an orphan claim that never reached in-progress (a crash
    # between claim's label add and its status swap). Release reaches it so it is recoverable through meld: `actions.
    # release` sheds every claim label, re-adds meld:ready as a no-op, and finds no in-progress/blocked label to remove.
    stale = issue.status == "ready" and bool(issue.claimants)
    if issue.status not in ("in_progress", "blocked") and not stale:
        return f"issue is {issue.status}; release is only legal from in-progress or blocked, or from ready with a stale claim label"
    if actor.is_human:
        return None
    group = cfg.groups[actor.group]  # type: ignore[index]
    if group.name in issue.claimants:
        return None
    if actor.is_pm and lease_rule(board, issue):
        return None
    return f"issue is not claimed by {group.name}"


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
    newest = audits[-1]
    if issue.closed_at and newest.created_at and newest.created_at <= issue.closed_at:
        # The first live run reopened two issues whose fixes had already landed: the FAIL was real once, the issue
        # was reopened on it, fixed and closed again, and the same FAIL kept `reopen` legal. A FAIL speaks about the
        # close it followed; a later close is work it never judged (handoff residuals 2 and 15).
        return (f"the newest AUDIT {ref} FAIL by {actor.group} ({newest.created_at}) predates the issue's last close "
                f"({issue.closed_at}); audit the work closed since")
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


def issue_verb_rules(board: Board, cfg: Config, actor: Actor, issue: Issue, models: ModelRegistry) -> dict[str, str | None]:
    """Every issue verb and why `actor` may not run it on `issue` right now -- None when it may. One table, read both
    by `legal` and by `meld status`'s `refused` map, so a verb absent from `legal` always says why."""
    return {v.value: (r.rule if (r := issue_refusal(board, cfg, actor, v, issue, models)) else None) for v in ISSUE_VERBS}


def legal_issue_verbs(board: Board, cfg: Config, actor: Actor, issue: Issue, models: ModelRegistry) -> list[str]:
    return [v for v, rule in issue_verb_rules(board, cfg, actor, issue, models).items() if rule is None]


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


def participating_groups(board: Board, cfg: Config) -> frozenset[str]:
    """The groups that have spoken on this milestone's coordination thread. A group in `.meld.toml` that has never posted
    (added through the form, not started, no bot yet) is not a reviewer anyone can wait for: the second live run's owner
    configured two such groups and the quorum climbed to two with only one non-committer group running, so no pull request
    could merge. With nothing on the thread yet, every configured group counts."""
    spoken = frozenset(c.header.party.group for c in board.comments
                       if c.ok and c.header is not None and c.header.party.group in cfg.groups)
    return spoken or frozenset(cfg.groups)


def needed_approvals(board: Board, cfg: Config) -> int:
    """`review.quorum`, never more than the participating groups less the one that committed the head (spec 13)."""
    return max(1, min(cfg.review_quorum, len(participating_groups(board, cfg)) - 1))


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
    need = needed_approvals(board, cfg)
    if len(have) < need:
        return f"{len(have)}/{need} approvals from non-committer parties (have: {sorted(have)})"
    outside = board.scope_violations(pr)
    if outside:
        return f"PR changes files outside #{pr.issue_number}'s declared scope: {', '.join(outside[:5])}"
    blocked = board.blocked_deletions(pr)
    if blocked:
        return f"PR deletes files not listed under '## May delete' in #{pr.issue_number}: {', '.join(blocked[:5])}"
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
    spent = _reviews_on_head(cfg, actor.group, pr)  # type: ignore[arg-type]
    allowed = 2 if pr.touches(cfg.security_paths) else 1   # spec 13 step 2: one reviewer, plus one security reviewer
    if spent >= allowed:
        what = "already reviewed" if allowed == 1 else "already used both its review slots (reviewer and security) on"
        return f"{actor.group} {what} head {pr.head_sha[:7]}; a new review needs a new head"
    return _run_slot(board, cfg, actor, pr.number)


def _reviews_on_head(cfg: Config, group: str, pr: PullRequest) -> int:
    """Verdicts `group` has already posted on the current head: GitHub reviews by its bot, the author group's marked
    COMMENT verdicts included (assemble flags both its approve and its request-changes `as_author`; the approve stays
    COMMENTED, so the state filter alone would miss it). The first live run's codex PM reviewed one unchanged
    head four times in 47 minutes -- every dispatch was legal because only the run slot was checked -- spending four
    large-tier runs and tripping the ping-pong cap on one substantive verdict (spec 21 item 31). A DISMISSED review
    frees the slot: a human who dismisses a stale verdict is asking for a fresh look. GitHub dismisses only APPROVED
    and CHANGES_REQUESTED reviews, so the author group's marked COMMENT verdicts wait for a new head instead."""
    return sum(1 for r in pr.reviews
               if r.commit_id == pr.head_sha and (r.state in ("APPROVED", "CHANGES_REQUESTED") or r.as_author)
               and party_of_login(cfg, r.author) == group)


def outside_milestone(board: Board, pr: PullRequest) -> bool:
    """A merged PR that closed an issue of an earlier milestone. `board.audits` is derived from the active coordination
    thread, so the AUDIT it received in its own milestone is invisible here and every group would owe it again: the second
    live run's PMs re-audited milestone 1's PRs at large tier until the caps bounded them. With no issues on the board yet
    (planning) nothing can be told apart, and nothing is in scope either."""
    return pr.issue_number is not None and bool(board.issues) and pr.issue_number not in board.issues


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
    if cfg.group_for_bot(pr.author) is None:
        # A human's PR (setup, configuration, a budget change) is the operator acting on their own repository: it is not
        # worker output, no group is accountable for it, and the first live run spent large-tier lines auditing seven of
        # them -- once each per group -- and reopened nothing useful. An unknown login is not a group's output either.
        return "PR was not authored by a group; audits cover worker output"
    if actor.group in board.audits.get(pr.number, {}):
        return f"{actor.group} already audited #{pr.number}"
    if outside_milestone(board, pr):
        return "PR closes an issue outside the active milestone; it was audited in its own"
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


def pr_verb_rules(board: Board, cfg: Config, actor: Actor, pr: PullRequest) -> dict[str, str | None]:
    """Every PR verb and why `actor` may not run it on `pr` right now -- None when it may. approve/request-changes are
    judged through the path `meld review` really takes: the author group's verdict is legal as a COMMENT review
    (review_verdict_rule), and a memory PR's verdict follows memory_review_rule -- so the table never says approve is
    illegal at the moment `meld review` would post one. Read both by `legal` and by `meld status`'s `refused` map."""
    out: dict[str, str | None] = {}
    for v in PR_VERBS:
        if v in (Verb.APPROVE, Verb.REQUEST_CHANGES):
            verdict = "approve" if v == Verb.APPROVE else "request-changes"
            rule = memory_review_rule(board, cfg, actor, pr) if pr.memory else review_verdict_rule(board, cfg, actor, pr, verdict)
        else:
            refusal = pr_refusal(board, cfg, actor, v, pr)
            rule = None if refusal is None else refusal.rule
        out[v.value] = rule
    return out


def legal_pr_verbs(board: Board, cfg: Config, actor: Actor, pr: PullRequest) -> list[str]:
    """What `actor` may do to `pr` now."""
    return [v for v, rule in pr_verb_rules(board, cfg, actor, pr).items() if rule is None]


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
    """Who must answer a challenge on `ref`: a PR's head committer's party (else its author's), an issue's
    claim holder's group (never a human holder or a contested issue), or an agreement ref's proposer. None
    when nobody owns the claim."""
    if ref.startswith("#") and ref[1:].isdigit():
        n = int(ref[1:])
        if n in board.pulls:
            pr = board.pulls[n]
            return committer_party(cfg, pr) or party_of_login(cfg, pr.author)
        if n in board.issues:
            holder = board.issues[n].claimed_by
            return holder if holder in cfg.groups else None
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
    # An open memory PR is in flight; a merged one has landed the decision file, which memory.decide otherwise discovers
    # only by reading origin -- so `legal` used to list `decide` for a ref whose file was already on the default branch.
    if any(p.memory is not None and p.memory[:2] == ("decision", ref) for p in board.pulls.values() if p.state == "open" or p.merged):
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


def recorded_lesson(board: Board, ref: str) -> PullRequest | None:
    """The memory PR that recorded (or is recording) a lesson from `ref`: open or merged, newest first."""
    prs = [p for p in board.pulls.values() if p.memory is not None and p.memory[:2] == ("lesson", ref) and (p.state == "open" or p.merged)]
    return max(prs, key=lambda p: p.number) if prs else None


def remember_rule(board: Board, cfg: Config, actor: Actor, ref: str, supersedes: str | None = None) -> str | None:
    ref = f"#{ref}" if ref.isdigit() else ref   # plan 71: `4` and `#4` are one ref here as everywhere else
    if actor.party.kind not in ("group", "pm"):
        return "only an orchestrator or PM may record a lesson"
    # One lesson per ref (plan 44): a later `remember` on the same ref must supersede the entry it improves on, so
    # lessons.md carries one current entry per item instead of five takes on it, each a PR, a review and a merge (the
    # scratch product's claude PM recorded five lessons from #4 in a day).
    earlier = recorded_lesson(board, ref)
    if earlier is not None and not supersedes:
        state = "an open" if earlier.state == "open" else "a merged"
        return (f"{ref} already has a lesson ({state} memory PR #{earlier.number}); fold what is new into it with "
                f"--supersedes '<that entry's heading>' (meld recall finds it) instead of a second entry")
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


def ask_rule(board: Board, actor: Actor, ref: str | None, text: str = "") -> str | None:
    """Why `actor` may not post `NEEDS-HUMAN` on `ref` now, or None. One open ask per ref: while the newest ask on it has
    no listed human's answer, a group's or PM's repeat is refused -- the person already has the card, and a second one
    from another party says the same thing in other words (plan 41). A human's own is never refused: that is how a
    person re-gates an issue they answered. One open ask per problem (plan 66): an ask on a fresh ref whose text names an
    issue or PR that already has an open ask is the same card under a new name -- the maze's claude PM, refused on `#4`,
    posted the same words as `ask-2`."""
    if actor.is_human:
        return None
    standing = board.open_ask(ref)
    if standing is None:
        for n in sorted({int(m) for m in re.findall(r"#(\d+)", text or "")}):
            other = board.open_ask(f"#{n}")
            if other is not None:
                return (f"#{n} already has an open NEEDS-HUMAN (comment {other.id} by {other.header.party.label}); a new ref does not "
                        f"make it a new question -- wait for a listed human's PROVIDED or RESOLVED on #{n}")
        return None
    return (f"{standing.header.ref} already has an open NEEDS-HUMAN (comment {standing.id} by {standing.header.party.label}); "
            f"wait for a listed human's PROVIDED or RESOLVED on it instead of asking again")


def agreement_post_verbs(board: Board, cfg: Config, actor: Actor, ref: str) -> list[str]:
    """The `meld post` verbs `actor` may use on agreement ref `ref` now, lowercase: propose while there is no text, amend
    under the cap, agree and dissent on text the actor has not agreed to, and `joint` for the proposer once the round
    has agreed and no verified [joint] closes it. The same conditions `posting.post` enforces, so `legal` and the
    refusal agree. Plan 47: `legal` listed only decide/plan actions, and the first real run's codex orchestrator read
    the empty list as "I may not answer this amendment" and asked the owner why."""
    from meld.grammar import AGREEMENT_VERBS, agreement_for, allowed_verbs
    if actor.party.kind not in ("group", "pm"):
        return []
    allowed = allowed_verbs(actor.party)
    agreement = agreement_for(board.comments, ref, cfg)
    if agreement.joint_posted:
        return []
    out = []
    if "PROPOSE" in allowed and "PROPOSE" in AGREEMENT_VERBS and agreement.text is None:
        out.append("propose")
    if agreement.text is not None:
        if "AMEND" in allowed and agreement.can_amend(cfg):
            out.append("amend")
        if actor.group not in agreement.agreed:
            out.extend(v.lower() for v in ("AGREE", "DISSENT") if v in allowed)
    if (actor.party.kind == "group" and agreement.proposer == actor.group and agreement.status in ("agreed", "majority")
            and not agreement.joint_verified):
        out.append("joint")
    return out


def withdraw_rule(board: Board, actor: Actor, ref: str | None) -> str | None:
    """Why a group may not post `RESOLVED` on `ref`, or None: a group's `RESOLVED` withdraws its own open `NEEDS-HUMAN`
    (plan 52: the agents answered it among themselves, so it no longer waits on a person), and nothing else. A person's
    own ask, another group's ask, or a ref with no open ask is refused."""
    if actor.is_human:
        return None
    standing = board.open_ask(ref)
    if standing is None:
        return f"{ref or 'this'} has no open NEEDS-HUMAN to withdraw"
    if standing.header.party.kind == "human":
        return f"the open NEEDS-HUMAN on {standing.header.ref} is a person's own; only a person clears it"
    if standing.header.party.group != actor.group:
        return (f"the open NEEDS-HUMAN on {standing.header.ref} is {standing.header.party.label}'s; only that group may withdraw it "
                f"(tell them, or wait for a person)")
    return None


def legal_agreement_verbs(board: Board, cfg: Config, actor: Actor, ref: str) -> list[str]:
    """The agreement-ref actions `actor` may take on `ref` right now, in the CLI's spelling: the post verbs of the round
    first, then the actions that close it."""
    out = agreement_post_verbs(board, cfg, actor, ref)
    if decide_rule(board, cfg, actor, ref) is None:
        out.append("decide")
    if plan_rule(board, cfg, actor, "issues", ref) is None:
        out.append("plan-issues")
    if ref == f"plan-{board.milestone}" and plan_rule(board, cfg, actor, "approve", ref) is None:
        out.append("plan-approve")
    if plan_rule(board, cfg, actor, "ack", ref) is None:
        out.append("plan-ack")
    return out
