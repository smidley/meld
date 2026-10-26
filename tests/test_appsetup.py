"""Plan 30: a group's GitHub App made by Meld from a manifest, driven here without a socket or a browser."""
import json
import os
import stat
import tomllib
from pathlib import Path

import pytest

from meld.appauth import AppAuthError
from meld.appsetup import PERMISSIONS, AppSetup, convert_manifest, manifest
from meld.config import load_config
from tests.test_config import MINIMAL

PEM = "-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n"


def flow(tmp_path: Path, **over) -> AppSetup:
    (tmp_path / ".meld.toml").write_text(MINIMAL)
    kw = dict(config_path=tmp_path / ".meld.toml", group="claude", repo="acme/widgets", owner_is_org=True, base_url="http://127.0.0.1:5",
              keys_dir=tmp_path / "home" / "keys", convert=lambda code: {"id": 4911515, "slug": "meld-claude", "pem": PEM, "html_url": "https://github.com/apps/meld-claude"},
              installations=lambda app_id, pem: [{"id": 160922345, "account": {"login": "acme"}}])
    kw.update(over)
    return AppSetup(**kw)


def test_the_manifest_asks_for_exactly_what_a_group_needs_and_no_webhook():
    m = manifest("meld-claude", "acme/widgets", "http://127.0.0.1:5/callback", "http://127.0.0.1:5/installed")
    assert m["default_permissions"] == PERMISSIONS == {"contents": "write", "issues": "write", "pull_requests": "write", "statuses": "write", "metadata": "read"}
    assert m["public"] is False and m["default_events"] == [] and m["hook_attributes"]["active"] is False
    assert m["redirect_url"].endswith("/callback") and m["setup_url"].endswith("/installed") and m["url"] == "https://github.com/acme/widgets"


def test_the_start_page_posts_the_manifest_to_github_for_an_org_or_a_user(tmp_path):
    f = flow(tmp_path)
    status, page = f.handle("/", {})
    assert status == 200 and f"state={f.state}" in page and "/organizations/acme/settings/apps/new" in page
    assert 'name="manifest"' in page and "meld-claude" in page and "document.getElementById('f').submit()" in page
    assert "/settings/apps/new?" in flow(tmp_path, owner_is_org=False).github_form_url() and "/organizations/" not in flow(tmp_path, owner_is_org=False).github_form_url()


def test_the_callback_converts_the_code_stores_the_key_0600_and_sends_the_person_to_install(tmp_path):
    f = flow(tmp_path)
    assert f.handle("/callback", {"state": "wrong", "code": "abc"})[0] == 400          # not this run's redirect
    assert f.handle("/callback", {"state": f.state})[0] == 400                         # no code
    status, page = f.handle("/callback", {"state": f.state, "code": "abc"})
    assert status == 200 and "github.com/apps/meld-claude/installations/new" in page and f.app["id"] == 4911515
    pem_path = tmp_path / "home" / "keys" / "claude.pem"
    assert pem_path.read_text() == PEM and stat.S_IMODE(pem_path.stat().st_mode) == 0o600
    assert not f.done and not load_config(tmp_path / ".meld.toml").groups["claude"].is_app      # nothing written until installed


def test_the_install_redirect_writes_the_group_as_an_app_group(tmp_path):
    f = flow(tmp_path)
    assert f.handle("/installed", {})[0] == 400                                         # not created yet
    f.handle("/callback", {"state": f.state, "code": "abc"})
    status, page = f.handle("/installed", {"installation_id": "160922345", "setup_action": "install"})
    assert status == 200 and f.done and "meld run claude --detach" in page
    cfg = load_config(tmp_path / ".meld.toml")
    g = cfg.groups["claude"]
    assert g.is_app and g.app_id == 4911515 and g.installation_id == 160922345 and g.bot == "meld-claude[bot]"
    assert g.private_key_file == str(tmp_path / "home" / "keys" / "claude.pem") and g.token_env is None and g.private_key_env is None
    assert g.cli == "claude" and g.orchestrator.model                                   # everything else untouched
    raw = tomllib.loads((tmp_path / ".meld.toml").read_text())["groups"]["claude"]
    assert "token_env" not in raw and raw["auth"] == "app"


def test_a_setup_redirect_without_the_id_asks_the_app_for_its_installations(tmp_path):
    f = flow(tmp_path)
    f.handle("/callback", {"state": f.state, "code": "abc"})
    status, page = f.handle("/installed", {"setup_action": "install"})
    assert status == 200 and f.installation_id == 160922345 and f.done
    g = flow(tmp_path, installations=lambda app_id, pem: [])
    g.handle("/callback", {"state": g.state, "code": "abc"})
    status, page = g.handle("/installed", {})
    assert status == 200 and "no installation yet" in page and not g.done


def test_a_conversion_failure_is_reported_on_the_page_and_the_flow(tmp_path):
    def boom(code):
        raise AppAuthError("manifest conversion failed with HTTP 404: Not Found")
    f = flow(tmp_path, convert=boom)
    status, page = f.handle("/callback", {"state": f.state, "code": "abc"})
    assert status == 500 and "HTTP 404" in page and f.error and not f.done
    assert f.handle("/nowhere", {})[0] == 404


def test_convert_manifest_reads_githubs_answer_and_refuses_a_bad_one():
    calls = []
    def post(url, headers, body):
        calls.append((url, headers, body))
        return 201, {"id": 7, "slug": "meld-x", "pem": PEM, "html_url": "u", "client_secret": "never-stored"}
    out = convert_manifest("c0de", post=post)
    assert out == {"id": 7, "slug": "meld-x", "pem": PEM, "html_url": "u"} and calls[0][0].endswith("/app-manifests/c0de/conversions")
    assert "Authorization" not in calls[0][1] and calls[0][2] is None                  # the one unauthenticated call
    with pytest.raises(AppAuthError, match="HTTP 404"):
        convert_manifest("c0de", post=lambda u, h, b: (404, {"message": "Not Found"}))


def test_a_group_the_file_does_not_have_is_refused_with_the_way_to_add_it(tmp_path):
    f = flow(tmp_path, group="gemini")
    f.handle("/callback", {"state": f.state, "code": "abc"})
    status, page = f.handle("/installed", {"installation_id": "1"})
    assert status == 500 and "Add a group" in page and f.error


def test_the_command_refuses_a_group_the_file_does_not_have_before_opening_anything(tmp_path, monkeypatch):
    from click.testing import CliRunner
    import meld.cli as cli
    from tests.conftest import env_for
    from tests.fakegh import FakeGh
    (tmp_path / ".meld.toml").write_text(MINIMAL)
    fake = FakeGh()
    fake.route("GET", "user", {"login": "empowerScott"})
    monkeypatch.setattr(cli, "RUN", fake)
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url))
    result = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "create-app", "nope"], env=env_for(tmp_path, group=None))
    assert result.exit_code != 0 and "no [groups.nope]" in result.output and "Add a group" in result.output and opened == []
    assert "two clicks" in CliRunner().invoke(cli.main, ["create-app", "--help"]).output
