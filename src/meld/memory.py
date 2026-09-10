"""Decision memory: agreed decisions and lessons become versioned files through PRs; `recall` finds them again."""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime
from pathlib import Path

from meld.actions import refuse
from meld.board import MEMORY_BRANCH_PREFIX, MEMORY_MARKER_RE as MARKER_RE
from meld.brief import FETCH_FAILED_NOTE, STATUS_RE, SUPERSEDES_RE, _local_hits, _terms, recall_snippets
from meld.config import Party
from meld.dispatch import DispatchError, _iso, _now, git_identity, neutralise_closers
from meld.gitops import GitError, create_worktree, git, push_branch, remove_worktree, slugify
from meld.grammar import Agreement, Header, agreement_for, compose, verified_joint
from meld.rules import Refusal, Verb, decide_rule, remember_rule
from meld.status import build_status

# STATUS_RE / SUPERSEDES_RE are imported above rather than defined here: brief.py is what reads them back out of
# the files (and it cannot import this module -- memory.py imports it), while re-exporting them keeps
# `memory.STATUS_RE` available where a reader of the memory verbs would look for it.
DECISIONS_DIR = ".meld/decisions"
LESSONS_FILE = ".meld/memory/lessons.md"
LESSONS_HEADER = "# Lessons\n"     # what a fresh lessons.md starts with; the verifier assumes the same when the default branch has none
_NUMBER_RE = re.compile(r"^(\d{4})-")


def superseded_trailer(new_file: str, date: str) -> str:
    """The one thing `meld decide --supersedes` appends to the decision it replaces. Memory is superseded, never
    edited: the old file keeps every byte it had and gains exactly this, which is what the other groups' PMs verify."""
    return f"\n---\nStatus: superseded by {new_file} on {date}\n"


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


def render_decision(ref: str, agreement: Agreement, url: str, date: str, supersedes: str | None = None) -> str:
    agreed = ", ".join(sorted(agreement.agreed))
    sup = f"- Supersedes: {supersedes}\n" if supersedes else ""
    return f"# Decision {ref}\n\n- Date: {date}\n- Agreed by: {agreed}\n- Source: {url}\n{sup}\n## Decision\n\n{(agreement.text or '').strip()}\n"


def escape_headings(text: str) -> str:
    """Neutralise any line in a lesson's own text that starts with `#`: unescaped, it would read as a markdown
    heading once appended into lessons.md, confusing both a human skimming the file and `_local_hits`' entry
    splitter (which finds the next entry by looking for a line starting `## `)."""
    return "\n".join(("\\" + line) if line.startswith("#") else line for line in text.splitlines())


def render_lesson(ref: str, text: str, group: str, date: str, supersedes: str | None = None) -> str:
    """`supersedes` is the heading of the entry this one replaces, and it goes on the entry's first body line:
    lessons.md is append-only, so the newer entry is the only place the link can be recorded."""
    sup = f"Supersedes: {supersedes}\n\n" if supersedes else ""
    return f"\n## {date} — {ref} ({group})\n\n{sup}{text.strip()}\n"


def comment_url(board, comment_id: int) -> str:
    """The html_url GitHub gives a coordination-issue comment; `decide` writes it into the file, the verifier re-renders from it."""
    return f"https://github.com/{board.repo}/issues/{board.coordination_issue}#issuecomment-{comment_id}"


def _open_memory_pr(ctx, board, group, *, branch: str, writes: list[tuple[str, str, str]], title: str, body: str) -> tuple[int, str]:
    """Apply `writes` -- `(path, text, mode)` triples, mode 'w' or 'a' -- on a fresh branch from the default branch as
    ONE commit by the bot, push, open the PR. A supersession writes two files (the new decision and the file it
    replaces) and they must land together, or a reviewer would see a decision naming a file that nothing marked.
    Everything from here through `create_pull` runs inside `try`/`finally`: the worktree is scratch space only, so
    any failure (a rejected push, a GitHub 5xx on create_pull) still removes it before the exception propagates --
    the tuple is returned only once every step has actually succeeded."""
    wt = ctx.store.home / "worktrees" / group.name / branch.removeprefix("meld/")
    if wt.exists():
        remove_worktree(ctx.repo_root, wt)
    create_worktree(ctx.repo_root, wt, branch, ctx.cfg.default_branch)
    try:
        for path, text, mode in writes:
            target = wt / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if mode == "a" and target.exists():
                target.write_text(target.read_text() + text)
            elif mode == "a":
                target.write_text(LESSONS_HEADER + text)
            else:
                target.write_text(text)
        name, email = git_identity(ctx, group)
        # Overlay the bot identity onto the inherited environment (never replace it outright): this worktree's
        # commit must keep PATH/HOME and the process's git hermeticity guards (GIT_CONFIG_GLOBAL, GIT_CONFIG_NOSYSTEM)
        # so it behaves the same whether the caller is a test fixture or a real PM process. `-c commit.gpgsign=false`
        # additionally overrides this one commit regardless of what a human's own global gitconfig says, so a PM's
        # memory commit can never hang or fail on a machine where `commit.gpgsign = true` is set but no key is loaded.
        env = {**os.environ, "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}
        git(["add", *[path for path, _, _ in writes]], wt)
        git(["-c", "commit.gpgsign=false", "commit", "-q", "-m",
            title.lower().replace("decision ", "docs: record decision ").replace("lesson from ", "docs: record lesson from ")], wt, env=env)
        push_branch(wt, branch, ctx.gh.token)
        pr = ctx.gh.create_pull(title, branch, ctx.cfg.default_branch, body)
    finally:
        try:
            remove_worktree(ctx.repo_root, wt)
        except GitError as exc:
            # A stuck removal (a lingering lock, a reaped-but-not-unregistered worktree) must never mask whatever
            # exception is already propagating from the try block above, nor stop a clean return below -- the
            # worktree is disposable scratch space, so its own cleanup failure is only ever logged.
            ctx.store.errors.append(f"cleanup of worktree {wt} failed: {exc}")
    return int(pr["number"]), str(pr.get("html_url", ""))


def _request_review(ctx, board, group, pr_number: int, what: str) -> None:
    ctx.gh.create_comment(board.coordination_issue, compose(Header(Party("pm", group.name), "REVIEW-REQUEST", f"#{pr_number}", None),
                                                            f"{what}; run `meld review {pr_number}` (memory PR: hash-verified, no worker needed)."))


def _superseded_file(ctx, board, ref: str, supersedes: str) -> tuple[str, str]:
    """Validate `--supersedes` and return `(path, the file's text on the default branch)`. Argument validation, not
    legality (rules.decide_rule already said who may decide `ref`): the named file must be a bare decision filename
    that actually landed on the default branch, and must not already be superseded -- a chain records one step at a
    time, so a second supersession of the same file would leave two trailers claiming the same predecessor."""
    old_path = f"{DECISIONS_DIR}/{supersedes}"
    bare = "/" not in supersedes and supersedes.endswith(".md")
    old = ctx.gh.get_contents(old_path, ctx.cfg.default_branch) if bare and old_path in _default_branch_files(ctx, DECISIONS_DIR) else None
    if old is None:
        refuse(ctx, board, Refusal(Verb.DECIDE, ref, f"{supersedes} is not a decision file on {ctx.cfg.default_branch}"))
    m = STATUS_RE.search(old)
    if m:
        refuse(ctx, board, Refusal(Verb.DECIDE, ref, f"{supersedes} is already superseded by {m.group(1)} on {m.group(2)}"))
    return old_path, old


def decide(ctx, ref: str, *, now: datetime | None = None, supersedes: str | None = None) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    rule = decide_rule(board, ctx.cfg, actor, ref)
    if rule:
        if rule == f"{ref} is already recorded or has an open memory PR":
            # decide_rule's own check (board-derived: an open PR whose marker matches) -- the git-based "a decision
            # file already landed on origin's default branch" case below has not run yet at this point, so this
            # branch is reached only for the open-memory-PR half of the (deliberately shared) refusal message.
            open_prs = [p for p in board.pulls.values() if p.memory and p.memory[:2] == ("decision", ref) and p.state == "open"]
            if open_prs:
                pr = open_prs[0]
                requested = any(c.ok and c.header.verb == "REVIEW-REQUEST" and c.header.ref == f"#{pr.number}" for c in board.comments)
                if not requested:
                    # The PM's earlier REVIEW-REQUEST never landed (or never posted): re-post it instead of
                    # refusing outright, so an open memory PR is never left with nobody told to review it.
                    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
                    what = f"Decision {ref} recorded as {pr.files[0]}" if pr.files else f"Decision {ref} already recorded (PR #{pr.number})"
                    _request_review(ctx, board, group, pr.number, what)
                    return {"decided": ref, "pr": pr.number, "review_requested": True}
        refuse(ctx, board, Refusal(Verb.DECIDE, ref, rule))
    joint = verified_joint(board.comments, ref, ctx.cfg)
    assert joint is not None  # decide_rule verified it
    agreement = agreement_for(board.comments, ref, ctx.cfg)
    slug = slugify(ref)
    if any(p.endswith(f"-{slug}.md") for p in _default_branch_files(ctx, DECISIONS_DIR)):
        refuse(ctx, board, Refusal(Verb.DECIDE, ref, f"{ref} is already recorded or has an open memory PR"))
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    when = _now(now)
    date = when.strftime("%Y-%m-%d")
    url = comment_url(board, joint.id)
    old_path, old_text = _superseded_file(ctx, board, ref, supersedes) if supersedes else (None, "")
    text = render_decision(ref, agreement, url, date, supersedes)
    digest = content_hash(text)
    name = f"{next_decision_number(ctx):04d}-{slug}.md"
    path = f"{DECISIONS_DIR}/{name}"
    body = f"Records the agreed decision `{ref}`.\n\nmeld-memory: decision {ref} {digest}\nverified-by: [joint] DECISION {ref} ({url})"
    writes = [(path, text, "w")]
    if old_path is not None:
        # The old file gains the status trailer and nothing else, in the same commit: superseded, never edited.
        # Its base text comes from GitHub at the default branch, which is exactly what the verifier compares against.
        writes.append((old_path, old_text + superseded_trailer(name, date), "w"))
        body += f"\nsupersedes: {supersedes}"
    pr_number, _ = _open_memory_pr(ctx, board, group, branch=f"{MEMORY_BRANCH_PREFIX}{slug}", writes=writes,
                                   title=f"Decision {ref}", body=body)
    _request_review(ctx, board, group, pr_number, f"Decision {ref} recorded as {path}")
    return {"decided": ref, "path": path, "pr": pr_number, "hash": digest, "supersedes": supersedes}


def remember(ctx, ref: str, text: str, *, now: datetime | None = None, supersedes: str | None = None) -> dict:
    if not text.strip():
        raise DispatchError("a lesson needs text: what went wrong, what fixed it, what to check next time")
    _, board = build_status(ctx)
    actor = ctx.actor()
    n = int(ref[1:]) if ref.startswith("#") and ref[1:].isdigit() else None
    issue = board.issues.get(n) if n is not None else None
    pr = board.pulls.get(n) if n is not None else None
    rule = remember_rule(board, ctx.cfg, actor, ref)
    if rule:
        refuse(ctx, board, Refusal(Verb.REMEMBER, ref, rule))
    group = ctx.cfg.groups[actor.group]  # type: ignore[index]
    when = _now(now)
    # `supersedes` comes from the flag, never from the lesson text, so it is rendered as the entry's own first body
    # line while the worker-authored text stays neutralised and escaped -- a `Supersedes:` line inside `text` is text.
    entry = render_lesson(ref, escape_headings(neutralise_closers(text)), group.name, when.strftime("%Y-%m-%d"), supersedes)
    digest = content_hash(entry)
    stamp = when.strftime("%Y%m%dT%H%M%SZ")
    body = f"Records a lesson from {ref}.\n\nmeld-memory: lesson {ref} {digest}\nverified-by: {ref} resolved on the board at {_iso(when)}"
    pr_number, _ = _open_memory_pr(ctx, board, group, branch=f"{MEMORY_BRANCH_PREFIX}lesson-{n}-{stamp}",
                                   writes=[(LESSONS_FILE, entry, "a")], title=f"Lesson from {ref}", body=body)
    _request_review(ctx, board, group, pr_number, f"Lesson from {ref} appended to {LESSONS_FILE}")
    return {"remembered": ref, "pr": pr_number, "hash": digest, "supersedes": supersedes}


def recall(ctx, query: str, *, limit: int = 10) -> list[dict]:
    """Like `decide`'s already-recorded check, the local half here (`_local_hits`) now reads origin's default
    branch (a fetch, then `ref=origin/<default>`), not `ctx.repo_root`'s own working copy -- so it no longer lags
    behind origin by however stale that checkout is. When the fetch itself fails (e.g. offline), this falls back
    to the working copy instead of raising, and the result carries a "note" entry saying so."""
    terms = _terms(query)
    ref: str | None = f"origin/{ctx.cfg.default_branch}"
    note: str | None = None
    try:
        git(["fetch", "--quiet", "origin"], ctx.repo_root)
    except GitError:
        # Fixed wording, never the GitError's own text: that is git's stderr, and a failed fetch's stderr names
        # the remote URL (which can carry a token or an internal hostname) in something a human is shown.
        ref = None
        note = FETCH_FAILED_NOTE
    hits = _local_hits(ctx.repo_root, terms, ref=ref)
    for item in ctx.gh.search_issues(f"repo:{ctx.cfg.repo_name} label:meld:coordination {query}"):
        hits.append({"kind": "coordination", "number": item.get("number"), "title": item.get("title", ""), "url": item.get("html_url", ""), "score": 1})
    hits.sort(key=lambda h: (-h["score"], h["title"]))
    result = hits[:limit]
    if note is not None:
        result.append({"kind": "note", "text": note})
    return result
