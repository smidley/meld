import json
from dataclasses import replace
from pathlib import Path

import pytest

from meld.brief import (
    BRIEF_FILE,
    DEFAULT_STANDARDS,
    RESULT_FILE,
    ResultError,
    WorkerResult,
    agent_instruction_files,
    decision_titles,
    file_hints,
    plan_excerpt,
    product_standards,
    read_result,
    render_brief,
    review_bar,
    worker_contract,
)
from meld.roster import WorkerType, builtin_workers
from tests.factories import mk_issue, mk_pr

PLAN = """# Plan
Intro paragraph.

## Milestone 1
- #11 Add the health endpoint at GET /healthz returning 200.
  Keep it dependency-free.

- #12 Login form with OAuth.

Closing notes mention #11 again.
"""

REVIEWER = next(w for w in builtin_workers() if w.name == "reviewer")
AUDITOR = next(w for w in builtin_workers() if w.name == "auditor")


def test_plan_excerpt_collects_paragraphs_mentioning_the_issue():
    excerpt = plan_excerpt(PLAN, 11)
    assert "GET /healthz" in excerpt and "dependency-free" in excerpt and "mention #11 again" in excerpt
    assert "OAuth" not in excerpt
    assert plan_excerpt(PLAN, 99) == ""
    assert "#111" not in plan_excerpt("- #111 other\n\n- #11 mine", 11)


def test_file_hints_prefers_files_section_then_backticked_paths():
    body = "Do the thing.\n\nFiles:\n- src/app/health.py\n- tests/test_health.py\n\nMore prose with `src/other.py`."
    assert file_hints(body) == ["src/app/health.py", "tests/test_health.py"]
    assert file_hints("Touch `src/a.py` and `docs/b.md` but not `not-a-path`.") == ["src/a.py", "docs/b.md"]
    assert file_hints("nothing here") == []


def test_repo_local_context_files(tmp_path: Path):
    assert decision_titles(tmp_path) == [] and review_bar(tmp_path) == "" and agent_instruction_files(tmp_path) == []
    (tmp_path / ".meld" / "decisions").mkdir(parents=True)
    (tmp_path / ".meld" / "decisions" / "0001-db.md").write_text("# 0001 Use SQLite for v1\n\ncontext\n")
    (tmp_path / ".meld" / "REVIEW.md").write_text("Behavior claims need a file:line citation.\n")
    (tmp_path / "CLAUDE.md").write_text("# rules\n")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "copilot-instructions.md").write_text("x")
    assert decision_titles(tmp_path) == ["0001 Use SQLite for v1"]
    assert review_bar(tmp_path).startswith("Behavior claims")
    assert agent_instruction_files(tmp_path) == ["CLAUDE.md", ".github/copilot-instructions.md"]


def test_render_brief_for_a_builder_and_a_reviewer(tmp_path: Path):
    (tmp_path / ".meld" / "decisions").mkdir(parents=True)
    (tmp_path / ".meld" / "decisions" / "0001-db.md").write_text("# 0001 Use SQLite for v1\n")
    (tmp_path / ".meld" / "REVIEW.md").write_text("Cite file:line for every claim.\n")
    (tmp_path / "AGENTS.md").write_text("x")
    backend = next(w for w in builtin_workers() if w.name == "backend")
    issue = mk_issue(11, title="Add health endpoint", size="S")
    text = render_brief(role=backend, kind="build", group="claude", model="claude-sonnet-5", effort="medium", issue=issue, pr=None,
                        plan_text=PLAN, repo_root=tmp_path, worktree=tmp_path, base_branch="main")
    assert text.startswith("# Meld brief: backend for #11")
    for needle in ("Add health endpoint", "GET /healthz", "0001 Use SQLite for v1", "Cite file:line", "AGENTS.md",
                   "claude-sonnet-5", "effort: medium", "## The worker contract", RESULT_FILE, "branch `meld/11-"):
        assert needle in text, needle
    reviewer = next(w for w in builtin_workers() if w.name == "reviewer")
    pr = mk_pr(20, 13, committer="meld-codex-bot")
    text = render_brief(role=reviewer, kind="review", group="claude", model="claude-opus-5", effort="high", issue=None, pr=pr,
                        plan_text=PLAN, repo_root=tmp_path, worktree=tmp_path, base_branch="main")
    assert text.startswith("# Meld brief: reviewer for PR #20") and "codex" in text and "verdict" in text


def test_read_result_missing_valid_and_malformed(tmp_path: Path):
    assert read_result(tmp_path) is None
    (tmp_path / RESULT_FILE).write_text(json.dumps({"outcome": "done", "summary": "did it", "verified_by": "pytest -> 3 passed"}))
    assert read_result(tmp_path) == WorkerResult("done", "did it", "pytest -> 3 passed", None, (), None)
    (tmp_path / RESULT_FILE).write_text(json.dumps({"outcome": "done", "summary": "r", "verified_by": "x", "verdict": "approve",
                                                    "comments": [{"path": "a.py", "line": 3, "body": "nit"}]}))
    assert read_result(tmp_path).verdict == "approve" and read_result(tmp_path).comments[0]["line"] == 3
    (tmp_path / RESULT_FILE).write_text("{not json")
    with pytest.raises(ResultError, match="not valid JSON"):
        read_result(tmp_path)
    (tmp_path / RESULT_FILE).write_text(json.dumps({"outcome": "maybe", "summary": "", "verified_by": ""}))
    with pytest.raises(ResultError, match="outcome"):
        read_result(tmp_path)
    (tmp_path / RESULT_FILE).write_text(json.dumps({"outcome": "done", "summary": "s", "verified_by": "v", "verdict": "meh"}))
    with pytest.raises(ResultError, match="verdict"):
        read_result(tmp_path)
    assert BRIEF_FILE == ".meld-brief.md" and "needs_human" in worker_contract()


def test_file_hints_extracts_paths_from_annotated_entries():
    body = "Files:\n- `src/app/health.py`:\n- src/app/routes.py: wire it up\n* tests/test_health.py, new\n"
    assert file_hints(body) == ["src/app/health.py", "src/app/routes.py", "tests/test_health.py"]


def test_plan_excerpt_normalizes_crlf():
    assert "\r" not in plan_excerpt("- #11 first\r\n\r\n- #12 other\r\n", 11)
    assert plan_excerpt("- #11 first\r\n\r\n- #12 other\r\n", 11) == "- #11 first"


def test_render_brief_wires_file_hints_and_keeps_one_h1(tmp_path: Path):
    backend = next(w for w in builtin_workers() if w.name == "backend")
    issue = mk_issue(11, title="Add health endpoint")
    issue = replace(issue, body="Add it.\n\nFiles:\n- `src/app/health.py`: new module\n- tests/test_health.py\n")
    text = render_brief(role=backend, kind="build", group="claude", model="claude-sonnet-5", effort="medium", issue=issue, pr=None,
                        plan_text="", repo_root=tmp_path, worktree=tmp_path, base_branch="main")
    assert "- `src/app/health.py`\n- `tests/test_health.py`" in text
    assert text.count("\n# ") == 0 and text.startswith("# Meld brief")
    assert "## Who you are\n\nImplements server-side code" in text


def test_pr_brief_names_the_primary_concern_and_the_checkout(tmp_path):
    pr = replace(mk_pr(20, 13), merge_commit_sha="feedfacefeedfacefeedfacefeedfacefeedface", state="closed", merged=True)
    text = render_brief(role=REVIEWER, kind="review", group="claude", model="m", effort="high", issue=None, pr=pr, plan_text="",
                        repo_root=tmp_path, worktree=tmp_path, base_branch="main", concern="tests and product conventions")
    assert "## Your primary concern" in text and "**tests and product conventions**" in text and "The checkout is the PR head." in text
    audit = render_brief(role=AUDITOR, kind="audit", group="claude", model="m", effort="high", issue=None, pr=pr, plan_text="",
                         repo_root=tmp_path, worktree=tmp_path, base_branch="main")
    assert "## Your primary concern" not in audit and "The checkout is the merge commit `feedfac` that landed this PR on `main`." in audit


def test_brief_carries_product_standards_and_skills(tmp_path):
    issue = mk_issue(11)
    backend = next(w for w in builtin_workers() if w.name == "backend")
    security_review_path = Path("/Users/x/.claude/skills/security-review/SKILL.md")
    skills = [WorkerType("brand", "House brand voice and visual identity.", "skill", None, ""),
              WorkerType("security-review", "OWASP-style review of a change.", "skill", security_review_path, "")]
    text = render_brief(role=backend, kind="build", group="claude", model="m", effort="medium", issue=issue, pr=None, plan_text="",
                        repo_root=tmp_path, worktree=tmp_path, base_branch="main", skills=skills)
    assert "## Product standards" in text and DEFAULT_STANDARDS.splitlines()[0] in text
    assert "## Skills available" in text and "- `brand`: House brand voice and visual identity." in text and "Invoke a skill by name" in text
    assert f"- `security-review` ({security_review_path}): OWASP-style review of a change." in text
    (tmp_path / ".meld").mkdir()
    (tmp_path / ".meld" / "STANDARDS.md").write_text("# Widgets standards\n\nEvery screen ships with a Lighthouse score above 90.\n")
    custom = render_brief(role=backend, kind="build", group="claude", model="m", effort="medium", issue=issue, pr=None, plan_text="",
                          repo_root=tmp_path, worktree=tmp_path, base_branch="main")
    # a bare substring check would false-positive: the worker contract's own item 10 names the heading in backticks
    # ("`## Skills available`") even when the section itself is absent, so this checks for the rendered heading line.
    assert "Lighthouse score above 90" in custom and DEFAULT_STANDARDS.splitlines()[0] not in custom and "\n## Skills available\n" not in custom
    assert product_standards(tmp_path).startswith("# Widgets standards")


def test_empty_standards_file_falls_back_to_the_default(tmp_path):
    (tmp_path / ".meld").mkdir()
    (tmp_path / ".meld" / "STANDARDS.md").write_text("   \n\n\t\n")
    assert product_standards(tmp_path) == DEFAULT_STANDARDS


def test_render_brief_caps_the_skills_section(tmp_path):
    issue = mk_issue(11)
    backend = next(w for w in builtin_workers() if w.name == "backend")
    many = [WorkerType(f"skill-{i:02d}", f"Does thing {i}.", "skill", None, "") for i in range(35)]
    text = render_brief(role=backend, kind="build", group="claude", model="m", effort="medium", issue=issue, pr=None, plan_text="",
                        repo_root=tmp_path, worktree=tmp_path, base_branch="main", skills=many)
    bullets = [line for line in text.splitlines() if line.startswith("- `skill-")]
    assert len(bullets) == 30 and "`skill-00`" in bullets[0] and "`skill-29`" in bullets[-1]
    assert "… and 5 more installed skills; ask your PM to narrow the roster if you need one of them." in text
    few_text = render_brief(role=backend, kind="build", group="claude", model="m", effort="medium", issue=issue, pr=None, plan_text="",
                            repo_root=tmp_path, worktree=tmp_path, base_branch="main", skills=many[:3])
    assert "more installed skills" not in few_text
