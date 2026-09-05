"""Decision memory: agreed decisions and lessons become versioned files through PRs; `recall` finds them again."""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime
from pathlib import Path

from meld.actions import refuse
from meld.brief import _local_hits, _terms, recall_snippets
from meld.config import Party
from meld.dispatch import DispatchError, _iso, _now, git_identity, neutralise_closers
from meld.gitops import GitError, create_worktree, git, push_branch, remove_worktree, slugify
from meld.grammar import Agreement, Header, agreement_for, compose
from meld.rules import Refusal, Verb
from meld.status import build_status

DECISIONS_DIR = ".meld/decisions"
LESSONS_FILE = ".meld/memory/lessons.md"
MEMORY_BRANCH_PREFIX = "meld/memory-"
MARKER_RE = re.compile(r"^meld-memory: (decision|lesson) (\S+) ([0-9a-f]{64})$", re.MULTILINE)
_NUMBER_RE = re.compile(r"^(\d{4})-")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _default_branch_files(ctx, subdir: str) -> list[str]:
    """Paths under `subdir` as they exist on origin's default branch right now, not in `ctx.repo_root`'s own
    working copy (which nothing ever pulls forward). A fetch keeps this current; ls-tree normally just prints
    nothing for a subdir that doesn't exist yet on that ref, but GitError is still caught defensively so a
    genuinely missing branch degrades to "nothing recorded" rather than raising."""
    git(["fetch", "--quiet", "origin"], ctx.repo_root)
    try:
        out = git(["ls-tree", "-r", "--name-only", f"origin/{ctx.cfg.default_branch}", "--", subdir], ctx.repo_root)
    except GitError:
        return []
    return out.splitlines() if out else []


def next_decision_number(ctx) -> int:
    numbers = [int(m.group(1)) for p in _default_branch_files(ctx, DECISIONS_DIR) if (m := _NUMBER_RE.match(Path(p).name))]
    return max(numbers, default=0) + 1


def render_decision(ref: str, agreement: Agreement, url: str, date: str) -> str:
    agreed = ", ".join(sorted(agreement.agreed))
    return f"# Decision {ref}\n\n- Date: {date}\n- Agreed by: {agreed}\n- Source: {url}\n\n## Decision\n\n{(agreement.text or '').strip()}\n"


def render_lesson(ref: str, text: str, group: str, date: str) -> str:
    return f"\n## {date} — {ref} ({group})\n\n{text.strip()}\n"


def _comment_url(board, comment_id: int) -> str:
    return f"https://github.com/{board.repo}/issues/{board.coordination_issue}#issuecomment-{comment_id}"


def _memory_pr_open(board, kind: str, ref: str) -> bool:
    return any(m and m.group(1) == kind and m.group(2) == ref for m in (MARKER_RE.search(p.body) for p in board.pulls.values() if p.state == "open"))


def _already_recorded(ctx, board, ref: str, slug: str) -> bool:
    """A decision is already handled if a memory PR for it is open, or its file already landed on origin's default
    branch (via `_default_branch_files`, not `ctx.repo_root`'s own working copy)."""
    if _memory_pr_open(board, "decision", ref):
        return True
    return any(p.endswith(f"-{slug}.md") for p in _default_branch_files(ctx, DECISIONS_DIR))


def _open_memory_pr(ctx, board, group, *, branch: str, path: str, text: str, mode: str, title: str, body: str) -> tuple[int, str]:
    """Write `text` at `path` (mode 'w' or 'a') on a fresh branch from the default branch, commit as the bot, push, open the PR.
    Everything from here through `create_pull` runs inside `try`/`finally`: the worktree is scratch space only, so
    any failure (a rejected push, a GitHub 5xx on create_pull) still removes it before the exception propagates --
    the tuple is returned only once every step has actually succeeded."""
    wt = ctx.store.home / "worktrees" / group.name / branch.removeprefix("meld/")
    if wt.exists():
        remove_worktree(ctx.repo_root, wt)
    create_worktree(ctx.repo_root, wt, branch, ctx.cfg.default_branch)
    try:
        target = wt / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == "a" and target.exists():
            target.write_text(target.read_text() + text)
        elif mode == "a":
            target.write_text("# Lessons\n" + text)
        else:
            target.write_text(text)
        name, email = git_identity(ctx, group)
        # Overlay the bot identity onto the inherited environment (never replace it outright): this worktree's
        # commit must keep PATH/HOME and the process's git hermeticity guards (GIT_CONFIG_GLOBAL, GIT_CONFIG_NOSYSTEM)
        # so it behaves the same whether the caller is a test fixture or a real PM process. `-c commit.gpgsign=false`
        # additionally overrides this one commit regardless of what a human's own global gitconfig says, so a PM's
        # memory commit can never hang or fail on a machine where `commit.gpgsign = true` is set but no key is loaded.
        env = {**os.environ, "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}
        git(["add", path], wt)
        git(["-c", "commit.gpgsign=false", "commit", "-q", "-m",
            title.lower().replace("decision ", "docs: record decision ").replace("lesson from ", "docs: record lesson from ")], wt, env=env)
        push_branch(wt, branch, ctx.gh.token)
        pr = ctx.gh.create_pull(title, branch, ctx.cfg.default_branch, body)
    finally:
        remove_worktree(ctx.repo_root, wt)
    return int(pr["number"]), str(pr.get("html_url", ""))


def _request_review(ctx, board, group, pr_number: int, what: str) -> None:
    ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "REVIEW-REQUEST", f"#{pr_number}", None),
                                                            f"{what}; run `meld review {pr_number}` (memory PR: hash-verified, no worker needed)."))


def decide(ctx, ref: str, *, now: datetime | None = None) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    if actor.party.kind not in ("group", "pm"):
        refuse(ctx, board, Refusal(Verb.DECIDE, ref, "only the proposer group's orchestrator or PM may record a decision"))
    agreement = agreement_for(board.comments, ref, ctx.cfg)
    if not agreement.joint_posted or agreement.joint_verb != "DECISION":
        refuse(ctx, board, Refusal(Verb.DECIDE, ref, f"no verified [joint] DECISION {ref} on the coordination issue"))
    if not agreement.joint_verified:
        refuse(ctx, board, Refusal(Verb.DECIDE, ref, f"the [joint] DECISION {ref} comment is not verified (wrong author or posted before the round closed)"))
    if agreement.status not in ("agreed", "majority"):
        refuse(ctx, board, Refusal(Verb.DECIDE, ref, f"{ref} ended in DISAGREE; the human decides"))
    if agreement.proposer != actor.group:
        refuse(ctx, board, Refusal(Verb.DECIDE, ref, f"only the proposer ({agreement.proposer}) may record {ref}"))
    slug = slugify(ref)
    if _already_recorded(ctx, board, ref, slug):
        refuse(ctx, board, Refusal(Verb.DECIDE, ref, f"{ref} is already recorded or has an open memory PR"))
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    when = _now(now)
    joint = next(c for c in board.comments if c.ok and c.header and c.header.party.kind == "joint" and c.header.ref == ref)
    url = _comment_url(board, joint.id)
    text = render_decision(ref, agreement, url, when.strftime("%Y-%m-%d"))
    digest = content_hash(text)
    path = f"{DECISIONS_DIR}/{next_decision_number(ctx):04d}-{slug}.md"
    body = f"Records the agreed decision `{ref}`.\n\nmeld-memory: decision {ref} {digest}\nverified-by: [joint] DECISION {ref} ({url})"
    pr_number, _ = _open_memory_pr(ctx, board, group, branch=f"{MEMORY_BRANCH_PREFIX}{slug}", path=path, text=text, mode="w",
                                   title=f"Decision {ref}", body=body)
    _request_review(ctx, board, group, pr_number, f"Decision {ref} recorded as {path}")
    return {"decided": ref, "path": path, "pr": pr_number, "hash": digest}


def remember(ctx, ref: str, text: str, *, now: datetime | None = None) -> dict:
    if not text.strip():
        raise DispatchError("a lesson needs text: what went wrong, what fixed it, what to check next time")
    _, board = build_status(ctx)
    actor = ctx.actor()
    if actor.party.kind not in ("group", "pm"):
        refuse(ctx, board, Refusal(Verb.REMEMBER, ref, "only an orchestrator or PM may record a lesson"))
    n = int(ref[1:]) if ref.startswith("#") and ref[1:].isdigit() else None
    issue = board.issues.get(n) if n is not None else None
    pr = board.pulls.get(n) if n is not None else None
    if issue is None and pr is None:
        refuse(ctx, board, Refusal(Verb.REMEMBER, ref, f"{ref} is not an issue or PR on the board"))
    if issue is not None and issue.status in ("blocked", "needs_human"):
        refuse(ctx, board, Refusal(Verb.REMEMBER, ref, f"{ref} is still {issue.status}; a lesson is recorded once the item resolves"))
    if board.is_challenged(ref):
        refuse(ctx, board, Refusal(Verb.REMEMBER, ref, f"{ref} has an open CHALLENGE; resolve it first"))
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    when = _now(now)
    entry = render_lesson(ref, neutralise_closers(text), group.name, when.strftime("%Y-%m-%d"))
    digest = content_hash(entry)
    stamp = when.strftime("%Y%m%dT%H%M%SZ")
    body = f"Records a lesson from {ref}.\n\nmeld-memory: lesson {ref} {digest}\nverified-by: {ref} resolved on the board at {_iso(when)}"
    pr_number, _ = _open_memory_pr(ctx, board, group, branch=f"{MEMORY_BRANCH_PREFIX}lesson-{n}-{stamp}", path=LESSONS_FILE, text=entry, mode="a",
                                   title=f"Lesson from {ref}", body=body)
    _request_review(ctx, board, group, pr_number, f"Lesson from {ref} appended to {LESSONS_FILE}")
    return {"remembered": ref, "pr": pr_number, "hash": digest}


def recall(ctx, query: str, *, limit: int = 10) -> list[dict]:
    """Unlike `decide`'s already-recorded check, the local half here (`_local_hits`, via `ctx.repo_root`) reads the
    working copy directly and so can lag behind origin's default branch by however stale that checkout is."""
    terms = _terms(query)
    hits = _local_hits(ctx.repo_root, terms)
    for item in ctx.gh.search_issues(f"repo:{ctx.cfg.repo_name} label:meld:coordination {query}"):
        hits.append({"kind": "coordination", "number": item.get("number"), "title": item.get("title", ""), "url": item.get("html_url", ""), "score": 1})
    hits.sort(key=lambda h: (-h["score"], h["title"]))
    return hits[:limit]
