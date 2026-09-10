import os
from pathlib import Path

import pytest

from meld.adapters import ADAPTER_NAMES, UnknownAdapter, get_adapter
from meld.adapters.base import LaunchSpec, SKILL_NAME_RE, parse_frontmatter, scan_skills, scrub_env
from tests.fakeadapter import FakeAdapter


def test_scrub_env_drops_credentials_and_party_and_marks_worker():
    env = {"PATH": "/bin", "HOME": "/Users/x", "GH_TOKEN": "a", "GITHUB_TOKEN": "b", "MELD_TOKEN_CLAUDE": "c",
           "MELD_TOKEN_CODEX": "d", "MELD_GROUP": "claude", "MELD_ROLE": "pm", "MELD_PUSH_TOKEN": "e", "MELD_HOME": "/h",
           "GH_ENTERPRISE_TOKEN": "f", "GITHUB_ENTERPRISE_TOKEN": "g"}
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


def test_scan_skills_skips_a_symlinked_skill_file(tmp_path: Path):
    """A `SKILL.md` that is a symlink points wherever its author aimed it -- reading it would put a file from
    outside the skills tree into the roster, the brief, and (through materialise_skills) a worktree. Skipped,
    the same way an unreadable one is; a real `SKILL.md` beside it is unaffected."""
    outside = tmp_path / "outside.md"
    outside.write_text("---\nname: borrowed\ndescription: Somewhere else entirely.\n---\n")
    skills = tmp_path / "skills"
    (skills / "borrowed").mkdir(parents=True)
    (skills / "borrowed" / "SKILL.md").symlink_to(outside)
    (skills / "real").mkdir()
    (skills / "real" / "SKILL.md").write_text("---\nname: real\ndescription: An actual file.\n---\n")
    assert [s.name for s in scan_skills([skills])] == ["real"]


def test_scan_skills_skips_a_symlinked_skill_directory(tmp_path: Path):
    """Final-review follow-up: a skill *directory* that is a symlink is skipped like a symlinked SKILL.md -- otherwise
    every file under the link's target would be read into the roster and copied into worktrees by materialise_skills."""
    outside = tmp_path / "outside-skill"
    outside.mkdir()
    (outside / "SKILL.md").write_text("---\nname: borrowed\ndescription: Somewhere else entirely.\n---\n")
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "borrowed").symlink_to(outside, target_is_directory=True)
    (skills / "real").mkdir()
    (skills / "real" / "SKILL.md").write_text("---\nname: real\ndescription: An actual directory.\n---\n")
    assert [s.name for s in scan_skills([skills])] == ["real"]


def test_parse_frontmatter_requires_a_closing_fence():
    assert parse_frontmatter("---\nname: brand\ndescription: never closed\nbody text") == {}


def test_project_skills_dirs():
    from meld.adapters.claude import ADAPTER as claude
    from meld.adapters.codex import ADAPTER as codex
    assert claude.project_skills_dir() == ".claude/skills" and codex.project_skills_dir() == ".agents/skills"


def test_scan_skills_falls_back_to_the_directory_name_for_an_unsafe_frontmatter_name(tmp_path: Path):
    """C1: `name:` is free text in a product-authored file; a value shaped like a path (`../../src`) must never
    reach a consumer that turns a skill name into a filesystem path component (materialise_skills)."""
    escaping = tmp_path / "deploy-checklist"
    escaping.mkdir()
    (escaping / "SKILL.md").write_text("---\nname: ../../src\ndescription: path traversal attempt\n---\n")
    assert [s.name for s in scan_skills([tmp_path])] == ["deploy-checklist"]
    # I3: the rule requires an alnum first character, so a manifest-lookalike name is rejected outright -- a
    # directory literally named ".meld-materialised.json" would also be skipped upstream (scan_skills excludes
    # any dot-prefixed directory before frontmatter is even read), but the name rule itself must reject the
    # string too, since a name reaches materialise_skills without going through that directory-listing filter.
    assert SKILL_NAME_RE.match(".meld-materialised.json") is None


def test_scan_skills_skips_a_directory_when_neither_name_is_valid(tmp_path: Path):
    """Frontmatter carries an unsafe name AND the directory name itself is unsafe (an underscore is not in the
    rule's alnum-first-character set): the directory is skipped entirely, never included under an invalid
    name (there is no third fallback)."""
    unsafe_dir = tmp_path / "_unsafe"
    unsafe_dir.mkdir()
    (unsafe_dir / "SKILL.md").write_text("---\nname: ../../also-bad\ndescription: x\n---\n")
    assert scan_skills([tmp_path]) == []


def test_app_key_env_is_scrubbed_from_workers():
    from meld.adapters.base import SCRUBBED_PREFIXES, scrub_env
    assert "MELD_APP_KEY_" in SCRUBBED_PREFIXES
    out = scrub_env({"MELD_APP_KEY_CLAUDE": "pem", "MELD_TOKEN_CODEX": "t", "PATH": "/bin"})
    assert "MELD_APP_KEY_CLAUDE" not in out and "MELD_TOKEN_CODEX" not in out and out["PATH"] == "/bin"


def test_popen_detached_never_warns_when_collected_before_the_child_exits(tmp_path):
    """Popen.__del__ warns ResourceWarning: subprocess N is still running for a still-alive child that gets garbage
    collected; a detached worker process is deliberately never waited on by meld (spec 21 item 6), so DetachedPopen's
    no-op __del__ must keep that warning from firing even while the child is still running at collection time."""
    import gc, sys, warnings
    from meld.adapters.base import DetachedPopen, LaunchSpec, open_log, popen_detached
    spec = LaunchSpec(role="worker", model="m", effort="e", cwd=tmp_path, prompt="", log_path=tmp_path / "log",
                      timeout_seconds=5, env={}, max_turns=1)
    with open_log(spec.log_path) as log:
        proc = popen_detached([sys.executable, "-c", "import time; time.sleep(0.5)"], spec, log)
    assert isinstance(proc, DetachedPopen)
    pid = proc.pid
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        del proc
        gc.collect()
    import os
    # Reap the child ourselves: a finished-but-unreaped child still answers kill(pid, 0), so polling that could never
    # observe the exit; waitpid is what meld's state.pid_alive uses too, and it leaves no zombie under pytest.
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
