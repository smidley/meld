import json

from meld.context import load_context
from tests.conftest import load_board1_routes
from tests.fakegh import FakeGh


def test_load_context_builds_app_auth_for_an_app_group(tmp_path, keypair_pem):
    toml = (tmp_path / ".meld.toml")
    toml.write_text('''
[repo]
name = "acme/widgets"
[humans]
accounts = ["empowerScott"]
[groups.claude]
cli = "claude"
bot = "meld-claude[bot]"
auth = "app"
app_id = 1234
installation_id = 55
private_key_env = "MELD_APP_KEY_CLAUDE"
[groups.claude.orchestrator]
model = "claude-opus-5"
effort = "high"
[groups.claude.pm]
model = "claude-sonnet-5"
effort = "medium"
''')
    fake = FakeGh()
    load_board1_routes(fake)
    minted = []

    def post(url, headers, body):
        minted.append((url, body))
        return 201, {"token": "ghs_ctx", "expires_at": "2999-01-01T00:00:00Z", "permissions": {}, "repositories": [{"full_name": "acme/widgets"}]}

    env = {"MELD_HOME": str(tmp_path / "home"), "MELD_GROUP": "claude", "MELD_ROLE": "pm", "MELD_APP_KEY_CLAUDE": keypair_pem.replace("\n", "\\n")}
    ctx = load_context(toml, env=env, run=fake, adapter_for=lambda n: None, app_post=post)
    assert ctx.actor_login == "meld-claude[bot]" and ctx.app_auth is not None
    assert ctx.gh.token == "ghs_ctx" and [u for u, _ in minted] == ["https://api.github.com/app/installations/55/access_tokens"]
    # Final-review Important 2: the token is scoped to the product repo, by bare name, not owner/name.
    assert json.loads(minted[0][1]) == {"repositories": ["widgets"]}
    assert (tmp_path / "home" / "cache" / "claude" / "app-token.json").exists()


_PAT_TOML = '''
[repo]
name = "smidley/livedrop"
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
'''


def test_a_humans_client_carries_the_token_the_caller_resolved(tmp_path):
    """Plan 82: livedrop's wizard set the repository up as the personal account, turned into the board, and the board read
    GitHub as `gh`'s active work account: 404 on a private repository. `load_context` takes the person's token from the
    caller (the CLI resolves the account that owns the repository); a group's credential never comes from it."""
    toml = tmp_path / ".meld.toml"
    toml.write_text(_PAT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    asked = []

    def human_token(repo):
        asked.append(repo)
        return "tok-owner"

    home = {"MELD_HOME": str(tmp_path / "home")}
    ctx = load_context(toml, env=home, run=fake, adapter_for=lambda n: None, human_token=human_token)
    assert asked == ["smidley/livedrop"] and ctx.gh.token == "tok-owner" and fake.envs[-1]["GH_TOKEN"] == "tok-owner"
    plain = load_context(toml, env=home, run=fake, adapter_for=lambda n: None)
    assert plain.gh.token is None                                       # no resolver: `gh`'s own account, as before
    group = load_context(toml, env={**home, "MELD_GROUP": "claude", "MELD_ROLE": "pm", "MELD_TOKEN_CLAUDE": "tok-group"},
                         run=fake, adapter_for=lambda n: None, human_token=human_token)
    assert group.gh.token == "tok-group" and asked == ["smidley/livedrop"]


def test_the_cli_resolves_the_owners_account_unless_a_token_is_already_set(monkeypatch):
    """Plan 82: the account that owns the repository when `gh` is logged in as it, else nothing (so `gh` uses its active
    account); a `GH_TOKEN` or `GITHUB_TOKEN` the person exported is their word and is left alone."""
    import subprocess

    from meld import cli

    def gh(argv, **_):
        ok = argv == ["gh", "auth", "token", "--user", "smidley"]
        return subprocess.CompletedProcess(argv, 0 if ok else 1, stdout="tok-smidley\n" if ok else "", stderr="")

    monkeypatch.setattr(cli, "RUN", gh)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert cli._human_token("smidley/livedrop") == "tok-smidley" and cli._human_token("acme/widgets") is None
    monkeypatch.setenv("GH_TOKEN", "tok-exported")
    assert cli._human_token("smidley/livedrop") is None
