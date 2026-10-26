"""Real-CLI smoke tests. Opt in with MELD_SMOKE=1; each test needs the CLI on PATH and a logged-in account."""
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from meld.adapters import get_adapter
from meld.adapters.base import LaunchSpec, scrub_env

pytestmark = pytest.mark.skipif(os.environ.get("MELD_SMOKE") != "1", reason="set MELD_SMOKE=1 to run real CLI smoke tests")
PROMPT = "Create a file named hello.txt containing exactly the word hello, then stop."


def _repo(tmp_path: Path) -> Path:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    git = lambda *args: subprocess.run(["git", "-C", str(tmp_path), *args], check=True, env=env)  # noqa: E731
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True, env=env)
    git("config", "user.name", "Smoke")
    git("config", "user.email", "smoke@example.com")
    git("commit", "-q", "--allow-empty", "-m", "init")
    return tmp_path


@pytest.mark.parametrize("name,model", [("claude", "claude-sonnet-5"), ("codex", "gpt-5.6-terra"),
                                        ("grok", "grok-4.7"), ("antigravity", "gemini-3.8-flash-high"), ("gemini", "gemini-2.5-flash")])
def test_adapter_creates_a_file(tmp_path: Path, name: str, model: str):
    adapter = get_adapter(name)
    binary = adapter.version_argv()[0]          # the adapter's binary, which is not always its name (`agy` for antigravity)
    if shutil.which(binary) is None:
        pytest.skip(f"{binary} not on PATH")
    spec = LaunchSpec(role="worker", model=model, effort="low", cwd=_repo(tmp_path), prompt=PROMPT, log_path=tmp_path / "log.txt",
                      timeout_seconds=300, env=scrub_env(dict(os.environ)), max_turns=10)
    proc = adapter.launch(spec)
    deadline = time.monotonic() + 300
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(1)
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        pytest.fail(f"{name} did not finish within 300s; log:\n{(tmp_path / 'log.txt').read_text()[-2000:]}")
    assert (tmp_path / "hello.txt").read_text().strip() == "hello", (tmp_path / "log.txt").read_text()[-2000:]
