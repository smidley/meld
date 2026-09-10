"""Render the worker brief and read the worker result file."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Sequence

from meld.board import Issue, PullRequest
from meld.gitops import GitError, branch_name, git
from meld.roster import WorkerType

BRIEF_FILE = ".meld-brief.md"
RESULT_FILE = ".meld-result.json"
OUTCOMES = ("done", "failed", "blocked")
VERDICTS = ("approve", "request-changes", "push-fix", "pass", "fail")
_AGENT_FILES = ("CLAUDE.md", "AGENTS.md", ".github/copilot-instructions.md")
_PATH_TOKEN_RE = re.compile(r"`([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)`")
_FILES_HEADING_RE = re.compile(r"^\s*(?:#+\s*)?files:?\s*$", re.IGNORECASE)
# How memory records that it has been overtaken. A superseded decision file carries the status trailer
# `meld decide --supersedes` appended to it; a superseded lesson is named by the FIRST BODY LINE of the later entry
# that replaced it (lessons.md is append-only, so the newer entry is the only place the link can be written).
# Both live here rather than in memory.py, which imports this module: `_decision_hit`, `_lesson_hits` and
# `decision_titles` are what read them, and memory.py re-exports both names for its own callers.
# `(.+?)` rather than `(\S+)`: a hand-authored decision file may have a space in its name, and a capture that
# stopped at the first space would not match the trailer at all -- the file would read as `active` after being
# superseded (and could be superseded a second time), which is the one direction this must never fail in.
STATUS_RE = re.compile(r"^Status: superseded by (.+?) on (\d{4}-\d{2}-\d{2})$", re.M)
SUPERSEDES_RE = re.compile(r"^Supersedes: (.+)$", re.M)
STANDARDS_FILE = ".meld/STANDARDS.md"
MAX_SKILLS = 30
MAX_SKILLS_USED = 30
SKILL_NAME_LIMIT = 100
DEFAULT_STANDARDS = """Build for real users, not for a demo. Visual design, performance, and security are acceptance criteria with the same weight as correctness: anything a user sees must look intentional, consistent, and accessible and respond quickly; every input, secret, and dependency is handled as if the product were already public.
Decide with industry standards, current documentation, and your own research rather than asking. Surface only decisions that need a human's approval (money, credentials, product direction, irreversible changes) through the framework's needs-human path.
Where a skill is available for the task at hand (design, security review, performance, a framework's conventions), use it before improvising, and say which one you used."""


def product_standards(repo_root: Path) -> str:
    """The product's own quality bar (.meld/STANDARDS.md) when it has one, else the framework default; an empty file counts as none."""
    f = repo_root / STANDARDS_FILE
    text = f.read_text().strip() if f.is_file() else ""
    return text or DEFAULT_STANDARDS


class ResultError(ValueError):
    pass


@dataclass(frozen=True)
class WorkerResult:
    outcome: str
    summary: str
    verified_by: str
    verdict: str | None
    comments: tuple[dict, ...]
    needs_human: str | None
    skills_used: tuple[str, ...] = ()


def worker_contract() -> str:
    return resources.files("meld").joinpath("prompts", "_worker_contract.md").read_text()


def plan_excerpt(plan_text: str, issue_number: int) -> str:
    plan_text = (plan_text or "").replace("\r\n", "\n").replace("\r", "\n")
    pattern = re.compile(rf"#{issue_number}(?!\d)")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", plan_text) if p.strip()]
    return "\n\n".join(p for p in paragraphs if pattern.search(p))


def file_hints(issue_body: str) -> list[str]:
    lines = (issue_body or "").splitlines()
    for i, line in enumerate(lines):
        if _FILES_HEADING_RE.match(line):
            hints: list[str] = []
            for follow in lines[i + 1:]:
                if not follow.strip():
                    break
                # Strip leading bullets and whitespace
                stripped = follow.strip().lstrip("-*").strip()
                # Extract first whitespace-delimited token
                token = stripped.split()[0] if stripped else ""
                # Strip trailing colons/commas first, then surrounding backticks
                token = token.rstrip(":,").strip("`")
                if token:
                    hints.append(token)
            if hints:
                return hints
    return [m for m in _PATH_TOKEN_RE.findall(issue_body or "") if "/" in m or m.endswith((".py", ".md", ".toml", ".ts", ".js"))]


def decision_titles(repo_root: Path) -> list[str]:
    titles: list[str] = []
    d = repo_root / ".meld" / "decisions"
    if d.is_dir():
        for f in sorted(d.glob("*.md")):
            text = f.read_text()
            if STATUS_RE.search(text):
                # Superseded, and memory is superseded rather than edited, so the file is still there: only decisions
                # in force belong under a brief's "## Decisions in force", or a worker is bound by two contradictory ones.
                continue
            for line in text.splitlines():
                if line.startswith("# "):
                    titles.append(line[2:].strip())
                    break
    return titles


def review_bar(repo_root: Path) -> str:
    f = repo_root / ".meld" / "REVIEW.md"
    return f.read_text() if f.is_file() else ""


def _terms(query: str) -> list[str]:
    return [t for t in re.split(r"\W+", query.lower()) if len(t) > 2]


def _term_match(a: str, b: str) -> bool:
    """Deliberate stemmer stand-in: two terms match when equal, or when one is a prefix of the other and both are
    at least 4 characters (invoice/invoices, deploy/deployment, test/tests). Short terms (pdf/pdfs) stay exact-only
    -- below 4 characters a prefix relationship is too likely to be coincidental to count as a real match."""
    if a == b:
        return True
    return len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a))


def select_skills(text: str, skills: Sequence[WorkerType], pinned: Sequence[str] = (), limit: int = MAX_SKILLS) -> list[WorkerType]:
    """Skills worth naming in a brief: the orchestrator's pins first (skill:<name> labels), then every skill whose name or
    description shares a term with the task text, by score. Unmatched skills stay out: a 30-item list nobody reads is noise."""
    by_name = {s.name: s for s in skills}
    chosen = [by_name[n] for n in pinned if n in by_name]
    terms = set(_terms(text))
    scored = []
    for s in skills:
        if s.name in pinned:
            continue
        hay = set(_terms(f"{s.name.replace('-', ' ')} {s.description}"))
        score = sum(1 for t in terms if any(_term_match(t, h) for h in hay))
        if score:
            scored.append((-score, s.name, s))
    chosen += [s for _, _, s in sorted(scored)]
    return chosen[:limit]


def _decision_hit(path: str, text: str, terms: list[str]) -> dict | None:
    score = sum(1 for t in terms if t in text.lower())
    if not score:
        return None
    title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), Path(path).stem)
    m = STATUS_RE.search(text)
    return {"kind": "decision", "path": path, "title": title, "score": score, "snippet": text.strip()[:300],
            "status": "superseded" if m else "active", "superseded_by": m.group(1) if m else None}


def _first_body_line(block: str) -> str:
    """The first non-blank line of a lessons entry after its `## ` heading -- where a supersession is recorded."""
    return next((l.strip() for l in block.splitlines()[1:] if l.strip()), "")


def _lesson_hits(path: str, text: str, terms: list[str]) -> list[dict]:
    blocks = [b for b in re.split(r"\n(?=## )", text) if b.startswith("## ")]
    headings = [b.splitlines()[0][3:].strip() for b in blocks]
    # A lesson is superseded by a LATER entry whose first body line names its heading; an earlier entry can never
    # supersede a later one, the first later superseder wins, and the relation is read from the whole file rather
    # than only from the entries that scored. One forward pass: `first_index` (built in reverse, so the first
    # occurrence of a repeated heading wins) says where the named entry is, and `replaced` keeps the first claim.
    first_index = {heading: i for i, heading in reversed(list(enumerate(headings)))}
    replaced: dict[str, str] = {}
    for j, block in enumerate(blocks):
        m = SUPERSEDES_RE.match(_first_body_line(block))
        if m:
            named = m.group(1).strip()
            if first_index.get(named, j) < j and named not in replaced:
                replaced[named] = headings[j]
    hits: list[dict] = []
    for block, heading in zip(blocks, headings):
        score = sum(1 for t in terms if t in block.lower())
        if score:
            replaced_by = replaced.get(heading)
            hits.append({"kind": "lesson", "path": path, "title": heading, "score": score, "snippet": block.strip()[:300],
                         "status": "superseded" if replaced_by else "active", "superseded_by": replaced_by})
    return hits


# The one note a caller shows when reading memory at `origin/<default>` was not possible. Fixed words, shared by
# `memory.recall` and `dispatch._recall_for_brief`: a GitError's text is git's stderr, which for a failed fetch
# names the remote -- a URL that can carry a token or an internal hostname -- and this note is printed to humans
# and folded into dispatch results.
FETCH_FAILED_NOTE = "origin fetch failed; recall used the local checkout"


def _local_hits(repo_root: Path, terms: list[str], ref: str | None = None) -> list[dict]:
    """Decisions and lessons whose text contains any of `terms`, each scored by how many terms matched.
    Lives here (not in memory.py, which owns the DECISIONS_DIR/LESSONS_FILE constants) so that dispatch.py
    can import recall_snippets without memory.py's own dispatch import becoming a cycle (see memory.py).

    With `ref` (typically `origin/<default branch>`), reads that ref through git (ls-tree to list, show to read)
    instead of `repo_root`'s own working copy, which nothing ever pulls forward -- see memory.recall."""
    if ref is not None:
        hits: list[dict] = []
        try:
            # -c core.quotePath=false: by default ls-tree C-quotes any path with a non-ASCII byte
            # (".meld/decisions/0002-caf\303\251.md"), which matches neither the .md filter below nor a later
            # `git show` -- a hand-authored decision file with an accent in its name would just vanish at a ref.
            out = git(["-c", "core.quotePath=false", "ls-tree", "-r", "--name-only", ref, "--", ".meld/decisions", ".meld/memory"], repo_root)
        except GitError:
            return []
        for path in (out.splitlines() if out else []):
            # Filter to the two shapes we ever read BEFORE `git show`: ls-tree can list anything under these two
            # directories (a stray non-.md file, a binary someone committed by mistake), and there is no reason to
            # spend a `git show` -- or risk decoding one -- on a file this function was never going to score anyway.
            is_decision = path.startswith(".meld/decisions/") and path.endswith(".md")
            is_lessons = path == ".meld/memory/lessons.md"
            if not (is_decision or is_lessons):
                continue
            try:
                text = git(["show", f"{ref}:{path}"], repo_root)
            except (GitError, UnicodeDecodeError):
                # GitError: the show itself failed (rare once ls-tree just listed it). UnicodeDecodeError: gitops.git
                # runs with text=True, so a file that is not valid UTF-8 at this ref (a binary, or a mis-encoded
                # decision file) raises here rather than returning garbage -- either way, skip just this one file;
                # a neighbouring, well-formed decision or lesson must still be found.
                continue
            if is_decision:
                hit = _decision_hit(path, text, terms)
                if hit:
                    hits.append(hit)
            else:
                hits.extend(_lesson_hits(path, text, terms))
        return hits
    hits = []
    d = repo_root / ".meld" / "decisions"
    for f in sorted(d.glob("*.md")) if d.is_dir() else []:
        hit = _decision_hit(str(f.relative_to(repo_root)), f.read_text(), terms)
        if hit:
            hits.append(hit)
    lessons = repo_root / ".meld" / "memory" / "lessons.md"
    if lessons.is_file():
        hits.extend(_lesson_hits(".meld/memory/lessons.md", lessons.read_text(), terms))
    return hits


def recall_snippets(repo_root: Path, text: str, limit: int = 3, ref: str | None = None) -> list[str]:
    """The local (non-GitHub) part of `memory.recall`, without a coordination-issue search: used by briefs.
    Superseded notes are dropped BEFORE the limit applies, so an overtaken decision or lesson can never crowd the
    one that replaced it out of a brief's `## Relevant memory`."""
    hits = [h for h in _local_hits(repo_root, _terms(text), ref=ref) if h.get("status") != "superseded"]
    hits.sort(key=lambda h: (-h["score"], h["title"]))
    return [h["snippet"] for h in hits[:limit]]


def agent_instruction_files(worktree: Path) -> list[str]:
    return [name for name in _AGENT_FILES if (worktree / name).is_file()]


def _skill_path(skill: WorkerType, worktree: Path, project_skills_dir: str | None) -> Path | None:
    """The `SKILL.md` this worker can actually open. For a product skill that is the copy `materialise_skills`
    wrote into this worktree under the adapter's project skill directory (it runs before every `render_brief`),
    not the file in the PM's own clone, which is not the worker's checkout and may not even be on its machine.
    An installed skill, or an adapter with no project skill directory, keeps the path the roster reported."""
    if skill.source == "product-skill" and project_skills_dir:
        return worktree / project_skills_dir / skill.name / "SKILL.md"
    return skill.path


def render_brief(*, role: WorkerType, kind: str, group: str, model: str, effort: str, issue: Issue | None, pr: PullRequest | None,
                 plan_text: str, repo_root: Path, worktree: Path, base_branch: str, concern: str | None = None,
                 skills: Sequence[WorkerType] = (), recall: Sequence[str] = (), project_skills_dir: str | None = None,
                 scope: Sequence[str] = (), may_delete: Sequence[str] = (), authorized: Sequence[str] = ()) -> str:
    if issue is not None:
        title = f"# Meld brief: {role.name} for #{issue.number}"
        task = f"## Your task\n\n**#{issue.number}: {issue.title}**\n\n{issue.body.strip() or '(no description)'}\n"
        task += f"\nYou are on branch `{branch_name(issue.number, issue.title)}`, created from `{base_branch}`.\n"
        excerpt = plan_excerpt(plan_text, issue.number)
        hints = file_hints(issue.body)
    else:
        assert pr is not None
        title = f"# Meld brief: {role.name} for PR #{pr.number}"
        if kind == "audit" and pr.merge_commit_sha:
            where = f"The checkout is the merge commit `{pr.merge_commit_sha[:7]}` that landed this PR on `{base_branch}`."
        else:
            where = "The checkout is the PR head."
        task = (f"## Your task\n\n**PR #{pr.number}: {pr.title}** (branch `{pr.head_ref}`, head `{pr.head_sha[:7]}`, "
                f"authored by `{pr.author}`, last commit by `{pr.head_committer or 'unknown'}`).\n\n{pr.body.strip() or '(no description)'}\n"
                f"\n{where} Your verdict goes in the `verdict` field of the result file.\n")
        excerpt = plan_excerpt(plan_text, pr.issue_number) if pr.issue_number else ""
        hints = []
    role_text = (role.text or role.description).strip()
    # Drop a leading # heading line from role.text to avoid duplicate H1
    if role_text.startswith("# "):
        lines = role_text.split("\n", 1)
        role_text = (lines[1] if len(lines) > 1 else "").lstrip("\n")
    parts = [title, "", f"You are the **{role.name}** worker of group **{group}**, running as `{model}` at effort: {effort}.", "",
             "## Who you are", "", role_text, "", task]
    if concern:
        parts += ["## Your primary concern", "",
                  f"Other groups review this PR too; yours is **{concern}**. Report anything else you notice, but spend your effort there first.", ""]
    if excerpt:
        parts += ["## Plan excerpt", "", excerpt, ""]
    if hints:
        parts += ["## Files to start from", ""] + [f"- `{h}`" for h in hints] + [""]
    if scope or may_delete:
        heading = "## Scope" if issue is not None else "## Scope declared by the issue"
        parts += [heading, ""]
        if scope:
            parts += ["You may change only files matching these globs; merge will refuse changes to any other file:", ""] + [f"- `{g}`" for g in scope] + [""]
        parts += (["Files you may delete (merge refuses any other deletion):", ""] + [f"- `{p}`" for p in may_delete] + [""]
                  if may_delete else
                  ["No deletions are allowed: merge refuses a PR that deletes a file this issue does not list under `## May delete`.", ""])
    if authorized:
        parts += ["## Authorized by a human", "", "A listed human has authorized these held categories for this task and no others:", ""] + [f"- {c}" for c in authorized] + [""]
    decisions = decision_titles(repo_root)
    if decisions:
        parts += ["## Decisions in force", ""] + [f"- {t}" for t in decisions] + ["", "Read the matching file under `.meld/decisions/` before departing from any of them.", ""]
    bar = review_bar(repo_root)
    if bar:
        parts += ["## Verification bar for this repository", "", bar.strip(), ""]
    if recall:
        parts += ["## Relevant memory", ""] + [f"- {s[:300].strip()}" for s in recall] + ["", "Decisions and lessons from earlier work that mention what this task touches; read the files under `.meld/` before departing from them.", ""]
    parts += ["## Product standards", "", product_standards(repo_root), ""]
    if skills:
        ordered = sorted(skills, key=lambda s: s.name)
        shown = ordered[:MAX_SKILLS]
        paths = [(s, _skill_path(s, worktree, project_skills_dir)) for s in shown]
        bullets = [f"- `{s.name}` ({p}): {s.description}" if p else f"- `{s.name}`: {s.description}" for s, p in paths]
        parts += ["## Skills available", ""] + bullets
        overflow = len(ordered) - len(shown)
        if overflow:
            parts += [f"… and {overflow} more installed skills; ask your PM to narrow the roster if you need one of them."]
        parts += ["", "Invoke a skill by name when it matches the task; do not reinvent what a skill already does well.", ""]
    agent_files = agent_instruction_files(worktree)
    if agent_files:
        parts += ["## Repository instructions", ""] + [f"- `{f}`" for f in agent_files] + ["", "These win over anything generic below.", ""]
    parts += [worker_contract().strip(), ""]
    return "\n".join(parts)


def read_result(worktree: Path) -> WorkerResult | None:
    f = worktree / RESULT_FILE
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ResultError(f"{RESULT_FILE} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ResultError(f"{RESULT_FILE} must be a JSON object")
    outcome = data.get("outcome")
    if outcome not in OUTCOMES:
        raise ResultError(f"{RESULT_FILE}: outcome must be one of {', '.join(OUTCOMES)}, got {outcome!r}")
    verdict = data.get("verdict")
    if verdict is not None and verdict not in VERDICTS:
        raise ResultError(f"{RESULT_FILE}: verdict must be one of {', '.join(VERDICTS)} or null, got {verdict!r}")
    comments = data.get("comments") or []
    if not isinstance(comments, list) or not all(isinstance(c, dict) for c in comments):
        raise ResultError(f"{RESULT_FILE}: comments must be a list of objects")
    raw_skills = data.get("skills_used") or []
    if not isinstance(raw_skills, list) or not all(isinstance(s, str) for s in raw_skills):
        raise ResultError(f"{RESULT_FILE}: skills_used must be a list of strings")
    # Telemetry, not a task result: never let a verbose or careless worker fail a finished, verified build over this.
    # Silently strip, truncate each name, and drop empties before capping the count -- so a few blanks ahead of the
    # limit do not crowd out real names that follow them.
    cleaned_skills = [c for s in raw_skills if (c := s.strip()[:SKILL_NAME_LIMIT])][:MAX_SKILLS_USED]
    needs_human = data.get("needs_human")
    return WorkerResult(outcome=outcome, summary=str(data.get("summary", "")), verified_by=str(data.get("verified_by", "")),
                        verdict=verdict, comments=tuple(comments), needs_human=str(needs_human) if needs_human else None,
                        skills_used=tuple(cleaned_skills))
