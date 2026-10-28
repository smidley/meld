from pathlib import Path

import pytest

from meld.roster import WorkerType, builtin_workers, parse_worker_file, product_workers, roster


def test_builtin_roster_has_the_seven_day_one_types():
    names = sorted(w.name for w in builtin_workers())
    assert names == ["auditor", "backend", "docs", "frontend", "qa", "reviewer", "security"]
    backend = next(w for w in builtin_workers() if w.name == "backend")
    assert backend.source == "builtin" and backend.description.startswith("Implements server-side")
    assert backend.path is None
    assert backend.text.startswith("# backend")


def test_parse_worker_file_uses_stem_and_first_paragraph(tmp_path: Path):
    f = tmp_path / "marketing.md"
    f.write_text("# marketing\n\nDrafts launch copy and positioning.\n\nMore detail below.\n")
    w = parse_worker_file(f, "product")
    assert w == WorkerType("marketing", "Drafts launch copy and positioning.", "product", f, f.read_text())
    empty = tmp_path / "blank.md"
    empty.write_text("# blank\n")
    assert parse_worker_file(empty, "product").description == ""


def test_product_workers_override_builtins_and_add_new(tmp_path: Path):
    wd = tmp_path / ".meld" / "workers"
    wd.mkdir(parents=True)
    (wd / "marketing.md").write_text("# marketing\n\nDrafts launch copy.\n")
    (wd / "backend.md").write_text("# backend\n\nProduct-specific backend rules.\n")
    assert [w.name for w in product_workers(tmp_path)] == ["backend", "marketing"]
    full = roster(tmp_path)
    by_name = {w.name: w for w in full}
    assert by_name["backend"].source == "product" and by_name["backend"].description == "Product-specific backend rules."
    assert by_name["marketing"].source == "product"
    assert by_name["frontend"].source == "builtin"
    assert len(full) == 8
    assert {w.name for w in full} == {"auditor", "backend", "docs", "frontend", "qa", "reviewer", "security", "marketing"}


def test_roster_without_repo_root_and_with_extra():
    extra = [WorkerType("data", "Notebook and pipeline work.", "skill", None)]
    names = [w.name for w in roster(None, extra)]
    assert names == ["auditor", "backend", "data", "docs", "frontend", "qa", "reviewer", "security"]


def test_parse_worker_file_requires_heading_and_skips_directories(tmp_path: Path):
    bad = tmp_path / "bad.md"
    bad.write_text("no heading here\n\nDescription.\n")
    with pytest.raises(ValueError, match="must start with a '# <name>' heading"):
        parse_worker_file(bad, "product")
    wd = tmp_path / ".meld" / "workers"
    wd.mkdir(parents=True)
    (wd / "dir.md").mkdir()
    (wd / "ok.md").write_text("# ok\n\nFine.\n")
    assert [w.name for w in product_workers(tmp_path)] == ["ok"]


def test_product_skills_join_the_roster_as_product_skill(tmp_path):
    from meld.roster import roster, WorkerType
    d = tmp_path / ".meld" / "skills" / "deploy-checklist"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: deploy-checklist\ndescription: Pre-deploy steps for this product\n---\n# Deploy\n")
    entries = {w.name: w for w in roster(tmp_path, [WorkerType("deploy-checklist", "host version", "skill", None)])}
    # scan_skills (adapters/base.py) sets WorkerType.path to the skill's SKILL.md file, not its directory -- confirmed
    # by reading scan_skills itself, so this asserts against the file path rather than the brief's original `d`.
    assert entries["deploy-checklist"].source == "product-skill" and entries["deploy-checklist"].path == d / "SKILL.md"


def test_product_skill_cannot_displace_a_builtin_or_product_worker(tmp_path):
    from meld.roster import roster
    d = tmp_path / ".meld" / "skills" / "notes"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: security\ndescription: Meeting notes skill\n---\n# Notes\n")
    entries = {w.name: w for w in roster(tmp_path)}
    # The skill's declared frontmatter name ("security") collides with the builtin "security" reviewer worker, so
    # the skill is dropped entirely: the worker survives untouched under "security", and the skill does not appear
    # under any other key either (not under its directory name "notes" -- scan_skills already resolved its name to
    # "security" from frontmatter, so there is no "notes" entry to fall back to).
    assert entries["security"].source == "builtin"
    assert "notes" not in entries


def test_product_skills_skip_a_symlinked_skills_root(tmp_path):
    """Final-review follow-up: `.meld/skills` itself being a symlink is skipped like a symlinked skill directory or a
    symlinked SKILL.md -- otherwise everything under the link's target would be read into the roster and materialised."""
    from meld.roster import product_skills
    outside = tmp_path / "outside"
    (outside / "borrowed").mkdir(parents=True)
    (outside / "borrowed" / "SKILL.md").write_text("---\nname: borrowed\ndescription: Somewhere else entirely.\n---\n")
    repo = tmp_path / "repo"
    (repo / ".meld").mkdir(parents=True)
    (repo / ".meld" / "skills").symlink_to(outside, target_is_directory=True)
    assert product_skills(repo) == []
    real = tmp_path / "repo2"
    (real / ".meld" / "skills" / "real").mkdir(parents=True)
    (real / ".meld" / "skills" / "real" / "SKILL.md").write_text("---\nname: real\ndescription: An actual directory.\n---\n")
    assert [s.name for s in product_skills(real)] == ["real"]


def test_brief_is_one_sentence_of_at_most_120_characters():
    """Plan 55: the role prompt carried 24k characters of skill descriptions on every launch; one line each now."""
    from meld.roster import BRIEF_CHARS, brief
    assert brief("Launch copy and positioning.") == "Launch copy and positioning."
    assert brief("Set up AI Runway on AKS from bare cluster to running model. Covers cluster verification, controller install and more.") == "Set up AI Runway on AKS from bare cluster to running model."
    long = "word " * 60
    assert len(brief(long)) == BRIEF_CHARS and brief(long).endswith("…")
    assert brief("  spaced\n\nout   text  ") == "spaced out text" and brief("") == ""
