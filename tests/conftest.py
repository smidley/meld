import json
from pathlib import Path

import pytest

from moot.github import GitHub
from tests.fakegh import FakeGh

FIXTURES = Path(__file__).parent / "fixtures"


def load_board1_routes(fake: FakeGh, repo: str = "acme/widgets") -> None:
    d = FIXTURES / "board1"
    r = lambda p: f"repos/{repo}/{p}"  # noqa: E731
    fake.route("GET", r("milestones"), json.loads((d / "milestones.json").read_text()))
    fake.route("GET", r("issues"), json.loads((d / "issues.json").read_text()))
    fake.route("GET", r("issues/10/comments"), json.loads((d / "comments_10.json").read_text()))
    fake.route("GET", r("pulls"), json.loads((d / "pulls.json").read_text()))
    fake.route("GET", r("pulls/20/reviews"), json.loads((d / "reviews_20.json").read_text()))
    fake.route("GET", r("pulls/20/commits"), json.loads((d / "commits_20.json").read_text()))
    fake.route("GET", r("commits/abc123/check-runs"), json.loads((d / "checkruns_abc123.json").read_text()))
    fake.route("GET", "user", {"login": "empowerScott"})


@pytest.fixture
def board1_gh(tmp_path):
    fake = FakeGh()
    load_board1_routes(fake)
    gh = GitHub("acme/widgets", token="tok", run=fake, cache_dir=tmp_path / "cache")
    return fake, gh


PROJECT_TOML = """
[repo]
name = "acme/widgets"
[humans]
accounts = ["empowerScott"]
[groups.claude]
cli = "claude"
bot = "moot-claude-bot"
token_env = "MOOT_TOKEN_CLAUDE"
[groups.claude.orchestrator]
model = "claude-opus-5"
effort = "high"
[groups.claude.pm]
model = "claude-sonnet-5"
effort = "medium"
[groups.codex]
cli = "codex"
bot = "moot-codex-bot"
token_env = "MOOT_TOKEN_CODEX"
[groups.codex.orchestrator]
model = "gpt-x"
effort = "high"
[groups.codex.pm]
model = "gpt-x"
effort = "medium"
"""


def env_for(tmp_path: Path, group: str | None = "claude", role: str = "pm") -> dict[str, str]:
    e = {"MOOT_HOME": str(tmp_path / "home"), "MOOT_TOKEN_CLAUDE": "tok-c", "MOOT_TOKEN_CODEX": "tok-x"}
    if group:
        e["MOOT_GROUP"] = group
        e["MOOT_ROLE"] = role
    return e
