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
