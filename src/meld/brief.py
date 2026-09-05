"""Render the worker brief and read the worker result file."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Sequence

from meld.board import Issue, PullRequest
from meld.gitops import branch_name
from meld.roster import WorkerType

BRIEF_FILE = ".meld-brief.md"
RESULT_FILE = ".meld-result.json"
OUTCOMES = ("done", "failed", "blocked")
VERDICTS = ("approve", "request-changes", "push-fix", "pass", "fail")
_AGENT_FILES = ("CLAUDE.md", "AGENTS.md", ".github/copilot-instructions.md")
_PATH_TOKEN_RE = re.compile(r"`([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)`")
_FILES_HEADING_RE = re.compile(r"^\s*(?:#+\s*)?files:?\s*$", re.IGNORECASE)
STANDARDS_FILE = ".meld/STANDARDS.md"
MAX_SKILLS = 30
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
    d = repo_root / ".meld" / "decisions"
    titles: list[str] = []
    if d.is_dir():
        for f in sorted(d.glob("*.md")):
            for line in f.read_text().splitlines():
                if line.startswith("# "):
                    titles.append(line[2:].strip())
                    break
    return titles


def review_bar(repo_root: Path) -> str:
    f = repo_root / ".meld" / "REVIEW.md"
    return f.read_text() if f.is_file() else ""


def _terms(query: str) -> list[str]:
    return [t for t in re.split(r"\W+", query.lower()) if len(t) > 2]


def _local_hits(repo_root: Path, terms: list[str]) -> list[dict]:
    """Decisions and lessons whose text contains any of `terms`, each scored by how many terms matched.
    Lives here (not in memory.py, which owns the DECISIONS_DIR/LESSONS_FILE constants) so that dispatch.py
    can import recall_snippets without memory.py's own dispatch import becoming a cycle (see memory.py)."""
    hits: list[dict] = []
    d = repo_root / ".meld" / "decisions"
    for f in sorted(d.glob("*.md")) if d.is_dir() else []:
        text = f.read_text()
        score = sum(1 for t in terms if t in text.lower())
        if score:
            title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), f.stem)
            hits.append({"kind": "decision", "path": str(f.relative_to(repo_root)), "title": title, "score": score, "snippet": text.strip()[:300]})
    lessons = repo_root / ".meld" / "memory" / "lessons.md"
    if lessons.is_file():
        for block in re.split(r"\n(?=## )", lessons.read_text()):
            if not block.startswith("## "):
                continue
            score = sum(1 for t in terms if t in block.lower())
            if score:
                hits.append({"kind": "lesson", "path": ".meld/memory/lessons.md", "title": block.splitlines()[0][3:].strip(), "score": score, "snippet": block.strip()[:300]})
    return hits


def recall_snippets(repo_root: Path, text: str, limit: int = 3) -> list[str]:
    """The local (non-GitHub) part of `memory.recall`, without a coordination-issue search: used by briefs."""
    hits = sorted(_local_hits(repo_root, _terms(text)), key=lambda h: (-h["score"], h["title"]))
    return [h["snippet"] for h in hits[:limit]]


def agent_instruction_files(worktree: Path) -> list[str]:
    return [name for name in _AGENT_FILES if (worktree / name).is_file()]


def render_brief(*, role: WorkerType, kind: str, group: str, model: str, effort: str, issue: Issue | None, pr: PullRequest | None,
                 plan_text: str, repo_root: Path, worktree: Path, base_branch: str, concern: str | None = None,
                 skills: Sequence[WorkerType] = (), recall: Sequence[str] = ()) -> str:
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
        bullets = [f"- `{s.name}` ({s.path}): {s.description}" if s.path else f"- `{s.name}`: {s.description}" for s in shown]
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
    raw_skills = data.get("skills_used", [])
    if not isinstance(raw_skills, list) or not all(isinstance(s, str) for s in raw_skills):
        raise ResultError(f"{RESULT_FILE}: skills_used must be a list of strings")
    needs_human = data.get("needs_human")
    return WorkerResult(outcome=outcome, summary=str(data.get("summary", "")), verified_by=str(data.get("verified_by", "")),
                        verdict=verdict, comments=tuple(comments), needs_human=str(needs_human) if needs_human else None,
                        skills_used=tuple(raw_skills))
