"""Real-CLI smoke tests. Opt in with MELD_SMOKE=1; each test needs the CLI on PATH and a logged-in account."""
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from meld.adapters import get_adapter
from meld.adapters.base import LaunchSpec, scrub_env

pytestmark = pytest.mark.skipif(os.environ.get("MELD_SMOKE") != "1", reason="set MELD_SMOKE=1 to run real CLI smoke tests")
PROMPT = "Create a file named hello.txt containing exactly the word hello, then stop."


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    return tmp_path


@pytest.mark.parametrize("name,model", [("claude", "claude-sonnet-5"), ("codex", "gpt-5.6-terra")])
def test_adapter_creates_a_file(tmp_path: Path, name: str, model: str):
    if shutil.which(name) is None:
        pytest.skip(f"{name} not on PATH")
    adapter = get_adapter(name)
    spec = LaunchSpec(role="worker", model=model, effort="low", cwd=_repo(tmp_path), prompt=PROMPT, log_path=tmp_path / "log.txt",
                      timeout_seconds=300, env=scrub_env(dict(os.environ)), max_turns=10)
    proc = adapter.launch(spec)
    deadline = time.monotonic() + 300
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(1)
    if proc.poll() is None:
        proc.kill()
        pytest.fail(f"{name} did not finish within 300s; log:\n{(tmp_path / 'log.txt').read_text()[-2000:]}")
    assert (tmp_path / "hello.txt").read_text().strip() == "hello", (tmp_path / "log.txt").read_text()[-2000:]
