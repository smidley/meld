import os
from pathlib import Path

import pytest

from meld.adapters import ADAPTER_NAMES, UnknownAdapter, get_adapter
from meld.adapters.base import LaunchSpec, parse_frontmatter, scan_skills, scrub_env
from tests.fakeadapter import FakeAdapter


def test_scrub_env_drops_credentials_and_party_and_marks_worker():
    env = {"PATH": "/bin", "HOME": "/Users/x", "GH_TOKEN": "a", "GITHUB_TOKEN": "b", "MELD_TOKEN_CLAUDE": "c",
           "MELD_TOKEN_CODEX": "d", "MELD_GROUP": "claude", "MELD_ROLE": "pm", "MELD_PUSH_TOKEN": "e", "MELD_HOME": "/h"}
    out = scrub_env(env)
    assert out == {"PATH": "/bin", "HOME": "/Users/x", "MELD_HOME": "/h", "MELD_WORKER": "1"}


def test_parse_frontmatter_and_scan_skills(tmp_path: Path):
    assert parse_frontmatter("no frontmatter") == {}
    text = "---\nname: brand\ndescription: Brand voice and assets.\nother: x\n---\n# Brand\nbody"
    assert parse_frontmatter(text) == {"name": "brand", "description": "Brand voice and assets.", "other": "x"}
    a, b = tmp_path / "a", tmp_path / "b"
    (a / "brand").mkdir(parents=True)
    (a / "brand" / "SKILL.md").write_text(text)
    (a / "noskill").mkdir()
    (b / "zeta").mkdir(parents=True)
    (b / "zeta" / "SKILL.md").write_text("# zeta\n\nno frontmatter here\n")
    (b / "brand").mkdir()
    (b / "brand" / "SKILL.md").write_text("---\nname: brand\ndescription: duplicate\n---\n")
    (b / ".system" / "hidden").mkdir(parents=True)
    (b / ".system" / "hidden" / "SKILL.md").write_text("---\nname: hidden\n---\n")
    skills = scan_skills([a, b, tmp_path / "missing"])
    assert [(s.name, s.description, s.source) for s in skills] == [("brand", "Brand voice and assets.", "skill"), ("zeta", "", "skill")]
    assert skills[0].path == a / "brand" / "SKILL.md" and skills[0].text.startswith("---")


@pytest.mark.skipif(not (Path(__file__).parents[1] / "src" / "meld" / "adapters" / "codex.py").exists(), reason="adapters land in Tasks 8-9")
def test_registry_names_and_unknown():
    assert ADAPTER_NAMES == ("claude", "codex")
    with pytest.raises(UnknownAdapter):
        get_adapter("gemini")
    assert get_adapter("claude").name == "claude" and get_adapter("codex").name == "codex"


def test_fake_adapter_launches_detached_process_and_logs(tmp_path: Path):
    adapter = FakeAdapter(script="import sys; print('hello from worker'); sys.exit(3)")
    spec = LaunchSpec(role="worker", model="m", effort="low", cwd=tmp_path, prompt="Read .meld-brief.md",
                      log_path=tmp_path / "logs" / "w.log", timeout_seconds=60, env=scrub_env(dict(os.environ)), max_turns=5)
    proc = adapter.launch(spec)
    assert proc.pid > 0 and os.getpgid(proc.pid) == proc.pid  # its own session/process group
    assert proc.wait(timeout=10) == 3
    assert "hello from worker" in (tmp_path / "logs" / "w.log").read_text()
    assert adapter.launches == [spec]


def test_scan_skills_skips_unreadable_and_undecodable_files(tmp_path: Path):
    good = tmp_path / "good"
    (good / "SKILL.md").parent.mkdir(parents=True)
    (good / "SKILL.md").write_text("---\nname: good\ndescription: Fine.\n---\n")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_bytes(b"\xff\xfe not utf-8")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "SKILL.md").write_text("---\nname: locked\n---\n")
    (locked / "SKILL.md").chmod(0)
    try:
        if os.geteuid() == 0:
            assert [s.name for s in scan_skills([tmp_path])] == ["good", "locked"]
        else:
            assert [s.name for s in scan_skills([tmp_path])] == ["good"]
    finally:
        (locked / "SKILL.md").chmod(0o644)


def test_parse_frontmatter_requires_a_closing_fence():
    assert parse_frontmatter("---\nname: brand\ndescription: never closed\nbody text") == {}
