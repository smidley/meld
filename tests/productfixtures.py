"""A product repository plus FakeGh and a PM context, for dispatch/tick/collect/reap tests."""
from __future__ import annotations

import json
import time
from pathlib import Path

from meld.context import load_context
from meld.state import pid_alive
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.fakeadapter import no_skills
from tests.fakegh import FakeGh
from tests.gitfixtures import make_origin_and_clone

FIXTURES = Path(__file__).parent / "fixtures" / "board1"
R = "repos/acme/widgets/"

DONE_SCRIPT = """
import json, pathlib, subprocess
pathlib.Path("health.py").write_text("OK = True\\n")
subprocess.run(["git", "add", "health.py"], check=True)
subprocess.run(["git", "commit", "-q", "-m", "feat: health endpoint"], check=True)
pathlib.Path(".meld-result.json").write_text(json.dumps({"outcome": "done", "summary": "added health.py",
    "verified_by": "python -c 'import health' -> ok"}))
"""
NO_RESULT_SCRIPT = "print('worker crashed before writing a result'); raise SystemExit(1)"
BLOCKED_SCRIPT = """
import json, pathlib
pathlib.Path(".meld-result.json").write_text(json.dumps({"outcome": "blocked", "summary": "need a key",
    "verified_by": "n/a", "needs_human": "Create a Stripe sandbox key and put it in STRIPE_KEY"}))
"""
SLEEP_SCRIPT = "import time; time.sleep(30)"
REVIEW_SCRIPT = """
import json, pathlib
pathlib.Path(".meld-result.json").write_text(json.dumps({"outcome": "done", "summary": "looks right", "verified_by": "pytest -q -> ok",
    "verdict": "approve", "comments": [{"path": "README.md", "line": 1, "body": "nit: title"}]}))
"""


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


def claim_issue_11(fake: FakeGh, size: str = "S") -> list[dict]:
    issues = fixture("issues.json")
    for i in issues:
        if i["number"] == 11:
            i["labels"] = [l for l in i["labels"] if not l["name"].startswith(("meld:ready", "size:"))]
            i["labels"] += [{"name": "meld:in-progress"}, {"name": f"size:{size}"}]
            i["assignees"] = [{"login": "meld-claude-bot"}]
            i["labels"].append({"name": "meld:claimed-by:claude"})
    fake.route("GET", R + "issues", issues)
    return issues


def make_product(tmp_path: Path, group: str = "claude", role: str = "pm"):
    tmp_path.mkdir(parents=True, exist_ok=True)  # tests pass sibling dirs (tmp_path / "b") that don't exist yet
    origin, repo = make_origin_and_clone(tmp_path)
    (repo / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    fake.route("GET", "users/meld-claude-bot", {"id": 7, "login": "meld-claude-bot"})
    fake.route("GET", "users/meld-codex-bot", {"id": 8, "login": "meld-codex-bot"})
    for n in (10, 11, 12, 13, 20):
        fake.route("POST", R + f"issues/{n}/comments", {"id": 300 + n, "html_url": f"u{n}"})
    for n in (11, 12, 13, 14):  # housekeeping's off_protocol_sweep reads events for every open issue
        fake.route("GET", R + f"issues/{n}/events", [])
    ctx = load_context(repo / ".meld.toml", env=env_for(tmp_path, group=group, role=role), run=fake, adapter_for=no_skills)
    return fake, ctx, repo, origin


def wait_for_exit(pid: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not pid_alive(pid), f"pid {pid} still alive after {timeout}s"
