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
        minted.append(url)
        return 201, {"token": "ghs_ctx", "expires_at": "2999-01-01T00:00:00Z", "permissions": {}, "repositories": []}

    env = {"MELD_HOME": str(tmp_path / "home"), "MELD_GROUP": "claude", "MELD_ROLE": "pm", "MELD_APP_KEY_CLAUDE": keypair_pem.replace("\n", "\\n")}
    ctx = load_context(toml, env=env, run=fake, adapter_for=lambda n: None, app_post=post)
    assert ctx.actor_login == "meld-claude[bot]" and ctx.app_auth is not None
    assert ctx.gh.token == "ghs_ctx" and minted == ["https://api.github.com/app/installations/55/access_tokens"]
    assert (tmp_path / "home" / "cache" / "claude" / "app-token.json").exists()
