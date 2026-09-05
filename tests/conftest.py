import json
import os
from pathlib import Path

import pytest

from meld.github import GitHub
from tests.fakegh import FakeGh

FIXTURES = Path(__file__).parent / "fixtures"


def load_board1_routes(fake: FakeGh, repo: str = "acme/widgets") -> None:
    d = FIXTURES / "board1"
    r = lambda p: f"repos/{repo}/{p}"  # noqa: E731
    fake.route("GET", r("milestones"), json.loads((d / "milestones.json").read_text()))
    fake.route("GET", r("milestones/3"), json.loads((d / "milestones.json").read_text())[0])
    fake.route("GET", r("issues"), json.loads((d / "issues.json").read_text()))
    fake.route("GET", r("issues/10/comments"), json.loads((d / "comments_10.json").read_text()))
    fake.route("GET", r("pulls"), json.loads((d / "pulls.json").read_text()))
    fake.route("GET", r("pulls/20/reviews"), json.loads((d / "reviews_20.json").read_text()))
    fake.route("GET", r("pulls/20/commits"), json.loads((d / "commits_20.json").read_text()))
    fake.route("GET", r("commits/abc123/check-runs"), json.loads((d / "checkruns_abc123.json").read_text()))
    fake.route("GET", r("commits/abc123/status"), json.loads((d / "status_abc123.json").read_text()))
    fake.route("GET", r("pulls/20/files"), json.loads((d / "files_20.json").read_text()))
    fake.route("GET", "user", {"login": "empowerScott"})


@pytest.fixture(autouse=True)
def _hermetic_cli_adapter(monkeypatch):
    """CLI tests never scan the host's ~/.claude or ~/.codex: the CLI resolves adapters through a fake with no skills."""
    import meld.cli as cli  # imported here, not at module load, so conftest never forces the CLI's import graph early
    from tests.fakeadapter import no_skills
    monkeypatch.setattr(cli, "ADAPTER_FOR", no_skills)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """No operator secret reaches the code under test through the process environment. `notify` (and so `tick`) falls
    back to os.environ for the Slack webhook when no `env=` is injected, and the fixture board carries a needs_human
    issue -- so a developer's real webhook would be posted to. The webhook and every MELD_TOKEN_* are removed for each
    test; tests inject what they need through `env=` (see env_for)."""
    monkeypatch.delenv("MELD_SLACK_DM_WEBHOOK", raising=False)
    for key in [k for k in os.environ if k.startswith("MELD_TOKEN_")]:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _hermetic_git(monkeypatch):
    """No host gitconfig (gpgsign, hooks, credential helpers, identity) reaches any git the suite or the code under
    test runs; fixtures set a local identity wherever they commit."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


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
bot = "meld-claude-bot"
token_env = "MELD_TOKEN_CLAUDE"
[groups.claude.orchestrator]
model = "claude-opus-5"
effort = "high"
[groups.claude.pm]
model = "claude-sonnet-5"
effort = "medium"
[groups.codex]
cli = "codex"
bot = "meld-codex-bot"
token_env = "MELD_TOKEN_CODEX"
[groups.codex.orchestrator]
model = "gpt-x"
effort = "high"
[groups.codex.pm]
model = "gpt-x"
effort = "medium"
[notify]
slack_dm_webhook_env = "MELD_SLACK_DM_WEBHOOK"
repeat_after_minutes = 120
"""


def env_for(tmp_path: Path, group: str | None = "claude", role: str = "pm") -> dict[str, str]:
    e = {"MELD_HOME": str(tmp_path / "home"), "MELD_TOKEN_CLAUDE": "tok-c", "MELD_TOKEN_CODEX": "tok-x"}
    if group:
        e["MELD_GROUP"] = group
        e["MELD_ROLE"] = role
    return e
