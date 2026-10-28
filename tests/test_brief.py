import json
from dataclasses import replace
from pathlib import Path

import pytest

from meld.brief import (
    BRIEF_FILE,
    DEFAULT_STANDARDS,
    RESULT_FILE,
    ResultError,
    STATUS_RE,
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
from meld.memory import recall_snippets
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

BACKEND = next(w for w in builtin_workers() if w.name == "backend")
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


def test_status_re_reads_a_superseded_by_name_that_contains_spaces():
    # A `(\S+)` capture would stop at the first space and match nothing at all, so a hand-authored decision file
    # with a space in its name would read as `active` after supersession -- and could then be superseded twice.
    from meld.memory import superseded_trailer
    assert STATUS_RE.search("# Decision x\n" + superseded_trailer("0002 dec 1.md", "2026-09-05")).group(1) == "0002 dec 1.md"


QUOTING_DECISION = ("# Decision dec-9\n\n- Date: 2026-09-05\n- Agreed by: claude, codex\n- Source: https://x/1\n\n## Decision\n\n"
                    "Cache reads in Redis. The note it replaces was retired by hand with the line\n\n"
                    "Status: superseded by 0001-old.md on 2026-01-01\n\nwhich this decision records properly instead.\n")


def test_status_re_matches_only_the_trailer_at_the_end_of_the_file(tmp_path: Path):
    """I2: an unanchored `Status: superseded by ...` match made a decision that quotes the line read as superseded at
    birth -- dropped from `## Decisions in force` and from `## Relevant memory`, and refused as a supersession target."""
    from meld.brief import _decision_hit
    from meld.memory import superseded_trailer
    assert STATUS_RE.search(QUOTING_DECISION) is None
    hit = _decision_hit(".meld/decisions/0009-dec-9.md", QUOTING_DECISION, ["redis"])
    assert hit["status"] == "active" and hit["superseded_by"] is None
    real = QUOTING_DECISION + superseded_trailer("0010-dec-10.md", "2026-09-06")
    superseded = _decision_hit(".meld/decisions/0009-dec-9.md", real, ["redis"])
    assert superseded["status"] == "superseded" and superseded["superseded_by"] == "0010-dec-10.md"
    d = tmp_path / ".meld" / "decisions"
    d.mkdir(parents=True)
    (d / "0009-dec-9.md").write_text(QUOTING_DECISION)
    (d / "0010-dec-10.md").write_text(real.replace("# Decision dec-9", "# Decision dec-10"))
    assert decision_titles(tmp_path) == ["Decision dec-9"]     # only the file that really carries the trailer is dropped


def test_lesson_supersession_is_keyed_by_index_not_by_heading_text():
    """I6: two entries can share a heading (same day, same ref, same group). Keying the superseder map by heading text
    retired both when a later entry named one of them; the link belongs to the entry the name resolves to."""
    from meld.brief import _lesson_hits
    same = "## 2026-09-01 — #9 (codex)"
    text = ("# Lessons\n"
            f"\n{same}\n\nthe first widgets lesson\n"
            f"\n{same}\n\nthe second widgets lesson\n"
            "\n## 2026-09-03 — #13 (claude)\n\nSupersedes: 2026-09-01 — #9 (codex)\n\nthe widgets replacement\n")
    hits = _lesson_hits(".meld/memory/lessons.md", text, ["widgets"])
    assert [(h["status"], h["superseded_by"]) for h in hits] == [
        ("superseded", "2026-09-03 — #13 (claude)"), ("active", None), ("active", None)]
    assert [h["snippet"].splitlines()[-1] for h in hits] == ["the first widgets lesson", "the second widgets lesson", "the widgets replacement"]


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


def test_render_brief_carries_scope_and_authorized_categories(tmp_path: Path):
    backend = next(w for w in builtin_workers() if w.name == "backend")
    issue = mk_issue(11, title="Add health endpoint", size="S")
    text = render_brief(role=backend, kind="build", group="claude", model="claude-sonnet-5", effort="medium", issue=issue, pr=None,
                        plan_text="", repo_root=tmp_path, worktree=tmp_path, base_branch="main",
                        scope=("src/meld/*.py",), may_delete=("src/meld/legacy.py",), authorized=("delete",))
    assert "## Scope" in text and "src/meld/*.py" in text and "src/meld/legacy.py" in text
    assert "merge will refuse changes to any other file" in text
    assert "## Authorized by a human" in text and "delete" in text
    bare = render_brief(role=backend, kind="build", group="claude", model="claude-sonnet-5", effort="medium", issue=issue, pr=None,
                        plan_text="", repo_root=tmp_path, worktree=tmp_path, base_branch="main")
    # Neither dynamic section is added (the contract's own fixed rule still names "## Authorized by a human"
    # generically, so the section's own heading/body text -- not that substring alone -- is what must be absent).
    assert not [l for l in bare.splitlines() if l.startswith("## Scope")]
    assert "A listed human has authorized these held categories" not in bare
    # M7: the deletion gate is on whether or not a scope is declared, so an undeclared issue's brief still says so.
    assert "No `## Scope` is declared; merge still refuses a deletion the issue does not list under `## May delete`." in bare
    assert "No `## Scope` is declared" not in text

    reviewer = next(w for w in builtin_workers() if w.name == "reviewer")
    pr = mk_pr(20, 13, committer="meld-codex-bot")
    review_text = render_brief(role=reviewer, kind="review", group="claude", model="claude-opus-5", effort="high", issue=None, pr=pr,
                               plan_text="", repo_root=tmp_path, worktree=tmp_path, base_branch="main", scope=("src/meld/*.py",))
    assert "## Scope declared by the issue" in review_text
    bare_review = render_brief(role=reviewer, kind="review", group="claude", model="claude-opus-5", effort="high", issue=None, pr=pr,
                               plan_text="", repo_root=tmp_path, worktree=tmp_path, base_branch="main")
    assert "No `## Scope` is declared" not in bare_review     # the line is the build brief's; a memory PR closes no issue


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


def test_brief_carries_relevant_memory_and_result_reads_skills_used(tmp_path):
    (tmp_path / ".meld" / "memory").mkdir(parents=True)
    (tmp_path / ".meld" / "memory" / "lessons.md").write_text("# Lessons\n\n## 2026-09-01 — #7 (codex)\n\nHealth endpoints must not touch the database.\n")
    issue = mk_issue(11, title="Add health endpoint")
    text = render_brief(role=BACKEND, kind="build", group="claude", model="m", effort="medium", issue=issue, pr=None, plan_text="",
                        repo_root=tmp_path, worktree=tmp_path, base_branch="main", recall=recall_snippets(tmp_path, "Add health endpoint"))
    assert "## Relevant memory" in text and "must not touch the database" in text and text.index("## Relevant memory") < text.index("## Product standards")
    plain = render_brief(role=BACKEND, kind="build", group="claude", model="m", effort="medium", issue=issue, pr=None, plan_text="",
                         repo_root=tmp_path, worktree=tmp_path, base_branch="main")
    assert "## Relevant memory" not in plain
    (tmp_path / ".meld-result.json").write_text(json.dumps({"outcome": "done", "summary": "s", "verified_by": "v", "skills_used": ["brand", "security-review"]}))
    assert read_result(tmp_path).skills_used == ("brand", "security-review")
    (tmp_path / ".meld-result.json").write_text(json.dumps({"outcome": "done", "summary": "s", "verified_by": "v"}))
    assert read_result(tmp_path).skills_used == ()
    (tmp_path / ".meld-result.json").write_text(json.dumps({"outcome": "done", "summary": "s", "verified_by": "v", "skills_used": "brand"}))
    with pytest.raises(ResultError, match="skills_used"):
        read_result(tmp_path)


def test_recall_snippets_at_a_ref_skips_files_it_cannot_decode(tmp_path):
    from tests.gitfixtures import make_origin_and_clone, sh
    origin, repo = make_origin_and_clone(tmp_path)
    d = repo / ".meld" / "decisions"
    d.mkdir(parents=True)
    (d / "0001-good.md").write_text("# Decision good\n\n## Decision\n\nUse Postgres for the ledger.\n")
    # Not valid UTF-8 either way: junk.dat is filtered out by name before `git show` ever runs on it; bad.md passes
    # the .meld/decisions/*.md filter and must be caught at the `git show`/decode step instead.
    (d / "bad.md").write_bytes(b"\xff\xfe not valid utf-8")
    (d / "junk.dat").write_bytes(b"\xff\xfe not valid utf-8 either")
    sh(["git", "add", ".meld"], repo)
    sh(["git", "commit", "-q", "-m", "seed decisions, two unreadable"], repo)
    sh(["git", "push", "-q", "origin", "main"], repo)
    hits = recall_snippets(repo, "postgres", ref="origin/main")
    assert any("Postgres" in h for h in hits)


def test_brief_prints_a_product_skills_worktree_copy_not_the_pms_checkout(tmp_path):
    """The worker contract's item 10 tells a worker to open the `SKILL.md` at the path the brief prints, and the
    copy it can actually open is the one materialise_skills wrote into its own worktree under the adapter's
    project skill directory -- not `.meld/skills/<name>/SKILL.md` in the PM's clone. An installed (non-product)
    skill still prints the path the adapter reported for it."""
    issue = mk_issue(11)
    backend = next(w for w in builtin_workers() if w.name == "backend")
    worktree = tmp_path / "wt"
    pm_copy = tmp_path / "repo" / ".meld" / "skills" / "deploy-checklist" / "SKILL.md"
    installed = Path("/Users/x/.claude/skills/security-review/SKILL.md")
    skills = [WorkerType("deploy-checklist", "Pre-deploy steps.", "product-skill", pm_copy, ""),
              WorkerType("security-review", "OWASP-style review of a change.", "skill", installed, "")]
    text = render_brief(role=backend, kind="build", group="claude", model="m", effort="medium", issue=issue, pr=None, plan_text="",
                        repo_root=tmp_path, worktree=worktree, base_branch="main", skills=skills, project_skills_dir=".fake/skills")
    assert f"- `deploy-checklist` ({worktree / '.fake/skills/deploy-checklist/SKILL.md'}): Pre-deploy steps." in text
    assert str(pm_copy) not in text
    assert f"- `security-review` ({installed}): OWASP-style review of a change." in text
    # An adapter with no project skill directory at all has nowhere to have copied it: the source path stands.
    plain = render_brief(role=backend, kind="build", group="claude", model="m", effort="medium", issue=issue, pr=None, plan_text="",
                         repo_root=tmp_path, worktree=worktree, base_branch="main", skills=skills)
    assert f"- `deploy-checklist` ({pm_copy}): Pre-deploy steps." in plain


def test_recall_snippets_at_a_ref_finds_a_decision_with_a_non_ascii_filename(tmp_path):
    """`git ls-tree --name-only` quotes any path with a non-ASCII byte under the default `core.quotePath`
    (`".meld/decisions/0002-caf\\303\\251.md"`), and a quoted path matches neither the `.md` filter nor a
    later `git show` -- so a hand-authored decision whose filename carries an accent would be silently
    invisible at a ref while being found perfectly well in the working copy."""
    from tests.gitfixtures import make_origin_and_clone, sh
    origin, repo = make_origin_and_clone(tmp_path)
    d = repo / ".meld" / "decisions"
    d.mkdir(parents=True)
    (d / "0002-caf\u00e9-ledger.md").write_text("# Decision caf\u00e9\n\n## Decision\n\nUse Postgres for the ledger.\n")
    sh(["git", "add", ".meld"], repo)
    sh(["git", "commit", "-q", "-m", "a decision with an accented filename"], repo)
    sh(["git", "push", "-q", "origin", "main"], repo)
    assert any("Postgres" in h for h in recall_snippets(repo, "postgres", ref="origin/main"))


def test_read_result_skills_used_tolerates_null_and_is_bounded(tmp_path):
    (tmp_path / RESULT_FILE).write_text(json.dumps({"outcome": "done", "summary": "s", "verified_by": "v", "skills_used": None}))
    assert read_result(tmp_path).skills_used == ()
    long_skills = [f"skill-{i:04d}-{'x' * 150}" for i in range(2000)]
    (tmp_path / RESULT_FILE).write_text(json.dumps({"outcome": "done", "summary": "s", "verified_by": "v", "skills_used": long_skills}))
    result = read_result(tmp_path)
    assert len(result.skills_used) == 30 and all(len(s) <= 100 for s in result.skills_used)
    assert result.skills_used[0] == f"skill-0000-{'x' * 89}" and result.skills_used[-1] == f"skill-0029-{'x' * 89}"
    mixed = ["", "  ", "brand", "   security-review   ", ""]
    (tmp_path / RESULT_FILE).write_text(json.dumps({"outcome": "done", "summary": "s", "verified_by": "v", "skills_used": mixed}))
    assert read_result(tmp_path).skills_used == ("brand", "security-review")
    (tmp_path / RESULT_FILE).write_text(json.dumps({"outcome": "done", "summary": "s", "verified_by": "v", "skills_used": ["brand", 5]}))
    with pytest.raises(ResultError, match="skills_used"):
        read_result(tmp_path)


def test_select_skills_matches_the_task_and_keeps_pinned_first():
    from meld.brief import select_skills
    from meld.roster import WorkerType
    skills = [WorkerType("pdf", "Read and write PDF files", "skill", None), WorkerType("stripe-billing", "Stripe payments and invoices", "skill", None),
              WorkerType("frontend-design", "Distinctive UI design", "skill", None)]
    # "invoice" (singular) still matches stripe-billing's "invoices" (plural) via _term_match's prefix rule
    # (both >= 4 chars) -- this is the deliberate stemmer stand-in, not exact-term overlap.
    chosen = select_skills("Add invoice download as PDF", skills, pinned=("frontend-design",))
    assert [s.name for s in chosen] == ["frontend-design", "pdf", "stripe-billing"]
    assert select_skills("Fix the CI cache key", skills) == []
    assert [s.name for s in select_skills("anything", skills, pinned=("nope", "pdf"))] == ["pdf"]


def test_brief_lists_only_selected_skills(tmp_path):
    from meld.brief import render_brief
    from meld.roster import WorkerType
    from tests.factories import mk_issue
    issue = mk_issue(11, title="Add invoice download as PDF")
    text = render_brief(role=BACKEND, kind="build", group="claude", model="m", effort="e", issue=issue, pr=None, plan_text="",
                        repo_root=tmp_path, worktree=tmp_path, base_branch="main",
                        skills=[WorkerType("pdf", "Read and write PDF files", "skill", None)])
    assert "`pdf`" in text and "## Skills available" in text


def test_build_brief_for_a_retry_quotes_the_reviewers_request_changes(tmp_path: Path):
    from dataclasses import replace
    from meld.board import Review
    from meld.brief import review_feedback
    from tests.factories import mk_issue, mk_pr
    issue = mk_issue(6, status="in_progress", claimed_by="claude")
    pr = replace(mk_pr(11, 6, head_sha="06aa7ff0", changes_requested=("meld-codex-bot",)),
                 reviews=(Review("meld-codex-bot", "CHANGES_REQUESTED", "06aa7ff0", "2026-09-11T17:06:38Z", body="Roll back partial tokens on a read error."),))
    role = next(w for w in builtin_workers() if w.name == "backend")
    text = render_brief(role=role, kind="build", group="claude", model="m", effort="high", issue=issue, pr=pr, plan_text="", repo_root=tmp_path,
                        worktree=tmp_path, base_branch="main")
    assert "## Review feedback to address" in text and "PR #11 (branch `" in text and "head `06aa7ff`" in text
    assert "### meld-codex-bot requested changes on `06aa7ff`" in text and "Roll back partial tokens on a read error." in text
    assert "A result that adds no commit is rejected" in text
    fresh = render_brief(role=role, kind="build", group="claude", model="m", effort="high", issue=issue, pr=None, plan_text="", repo_root=tmp_path,
                         worktree=tmp_path, base_branch="main")
    assert "## Review feedback to address" not in fresh
    assert "No reviewer has requested changes yet" in review_feedback(mk_pr(11, 6))


def test_product_context_reaches_the_brief_capped_and_the_role_prompt_whole(tmp_path):
    """Plan 35: .meld/CONTEXT.md -- existing docs, partial work, files to read first -- is a section of every worker brief
    (capped, with a pointer to the file) and of every role prompt (whole); a product without one has no such section."""
    from meld.brief import CONTEXT_FILE, MAX_CONTEXT_CHARS, context_for_brief, product_context
    assert product_context(tmp_path) == "" and context_for_brief(tmp_path) == ""
    (tmp_path / ".meld").mkdir(exist_ok=True)
    (tmp_path / CONTEXT_FILE).write_text("# Product context\n\nThe API is half built in src/api; read docs/DESIGN.md first.\n")
    assert product_context(tmp_path).startswith("# Product context") and "docs/DESIGN.md" in context_for_brief(tmp_path)
    (tmp_path / CONTEXT_FILE).write_text("x" * (MAX_CONTEXT_CHARS + 500))
    capped = context_for_brief(tmp_path)
    assert len(capped) < MAX_CONTEXT_CHARS + 100 and capped.endswith("for the rest)") and CONTEXT_FILE in capped


def test_build_brief_names_the_files_to_resolve_when_the_branch_conflicts(tmp_path: Path):
    """Plan 40."""
    from meld.brief import merge_conflicts
    from tests.factories import mk_issue, mk_pr
    issue = mk_issue(6, status="in_progress", claimed_by="claude")
    role = next(w for w in builtin_workers() if w.name == "backend")
    text = render_brief(role=role, kind="build", group="claude", model="m", effort="high", issue=issue, pr=mk_pr(11, 6), plan_text="",
                        repo_root=tmp_path, worktree=tmp_path, base_branch="main", conflicts=["src/a.py", "tests/test_a.py"])
    assert "## Merge conflicts to resolve first" in text and "- `src/a.py`" in text and "- `tests/test_a.py`" in text
    assert "git merge origin/main" in text and text.index("## Review feedback") < text.index("## Merge conflicts")
    assert "## Merge conflicts" not in render_brief(role=role, kind="build", group="claude", model="m", effort="high", issue=issue, pr=None,
                                                    plan_text="", repo_root=tmp_path, worktree=tmp_path, base_branch="main")
    assert merge_conflicts("main", ["x"]).startswith("\n## Merge conflicts to resolve first")
