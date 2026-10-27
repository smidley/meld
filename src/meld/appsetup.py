"""`meld create-app`: a group's GitHub identity made by Meld, not by hand (plan 30).

GitHub lets nobody create a user account by API, but it lets an App be created from a manifest with one click and
installed with a second, and an App group (spec 18, plan 6a) already mints its own tokens. So: Meld writes the manifest
(name, the repository, the four permissions a group needs, no webhook), serves one page on the loopback that submits it
to GitHub, catches the redirect with the one-time code, converts the code into the App (id, slug, private key) with
`POST /app-manifests/{code}/conversions` -- the one unauthenticated call -- stores the key 0600 under `$MELD_HOME/keys`,
sends the person on to install the App on the repository, catches GitHub's `setup_url` redirect with the installation
id, and writes `[groups.<g>]` as an App group. No token is ever typed, pasted or exported.
"""
from __future__ import annotations

import html
import json
import os
import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

from meld.appauth import API, AppAuthError, _bearer, _http, jwt_for_app
from meld.atomic import write_atomic
from meld.config import Config, ConfigError, dumps

# The four a group needs plus checks (a review's verdict is also a check run; doctor's app-installation check asks for it) and metadata.
PERMISSIONS = {"contents": "write", "issues": "write", "pull_requests": "write", "statuses": "write", "checks": "write", "metadata": "read"}


def manifest(name: str, repo: str, redirect_url: str, setup_url: str) -> dict:
    return {"name": name, "url": f"https://github.com/{repo}", "public": False, "redirect_url": redirect_url, "setup_url": setup_url,
            "setup_on_update": False, "hook_attributes": {"active": False, "url": f"https://github.com/{repo}"},
            "default_permissions": PERMISSIONS, "default_events": [], "description": f"Meld group identity for {repo}"}


def convert_manifest(code: str, post: Callable[[str, dict[str, str], bytes | None], tuple[int, dict]] | None = None) -> dict:
    """The one-time code GitHub redirects back with becomes the App: id, slug, pem (and secrets this never stores)."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "meld"}
    status, data = (post or (lambda u, h, b: _http("POST", u, h, b)))(f"{API}/app-manifests/{code}/conversions", headers, None)
    if status != 201 or not isinstance(data, dict) or not data.get("id") or not data.get("pem") or not data.get("slug"):
        raise AppAuthError(f"manifest conversion failed with HTTP {status}: {data.get('message', '') if isinstance(data, dict) else ''}")
    return {"id": int(data["id"]), "slug": str(data["slug"]), "pem": str(data["pem"]), "html_url": str(data.get("html_url", ""))}


def list_installations(app_id: int, pem: str, get=None, sign=None, now: int | None = None) -> list[dict]:
    jwt = jwt_for_app(app_id, pem, now=now, sign=sign)
    status, data = (get or (lambda u, h: _http("GET", u, h, None)))(f"{API}/app/installations", _bearer(jwt))
    if status != 200 or not isinstance(data, list):
        raise AppAuthError(f"GET /app/installations failed with HTTP {status}")
    return [d for d in data if isinstance(d, dict)]


@dataclass
class AppSetup:
    """The flow as plain calls the loopback server (or a test) drives: `start_page()` is what the browser opens,
    `handle(path, query)` answers each redirect, `done` says when the group is written."""
    config_path: Path
    group: str
    repo: str
    owner_is_org: bool
    base_url: str                      # this process's loopback origin, e.g. http://127.0.0.1:51234
    keys_dir: Path
    name: str | None = None
    suffix: str = ""                   # appended to the redirect URLs, e.g. the viewer's `?t=<token>` so GitHub's redirects pass its gate
    convert: Callable[[str], dict] = convert_manifest
    installations: Callable[[int, str], list[dict]] = list_installations
    on_installed: Callable[[str], dict] | None = None   # what to do once the group is written: the session starts it
    state: str = field(default_factory=lambda: secrets.token_urlsafe(16))
    app: dict | None = None
    pem_path: Path | None = None
    installation_id: int | None = None
    done: bool = False
    error: str | None = None

    @property
    def app_name(self) -> str:
        """`<repository>-<group>` unless named: App names are global on GitHub, and `meld-claude` was taken the first time
        the wizard ran for real. GitHub caps a name at 34 characters."""
        return self.name or f"{self.repo.partition('/')[2]}-{self.group}"[:34]

    def github_form_url(self) -> str:
        owner = self.repo.partition("/")[0]
        path = f"/organizations/{owner}/settings/apps/new" if self.owner_is_org else "/settings/apps/new"
        return f"https://github.com{path}?" + urlencode({"state": self.state})

    def manifest(self) -> dict:
        return manifest(self.app_name, self.repo, f"{self.base_url}/callback{self.suffix}", f"{self.base_url}/installed{self.suffix}")

    def start_page(self) -> str:
        """A page that submits the manifest to GitHub the moment it loads (GitHub takes the manifest as a POSTed form field)."""
        m = html.escape(json.dumps(self.manifest()), quote=True)
        return ("<!doctype html><meta charset=\"utf-8\"><title>meld create-app</title>"
                f"<p>Sending GitHub the App manifest for <b>{html.escape(self.app_name)}</b> on {html.escape(self.repo)}. "
                "If the page does not move on by itself, press the button.</p>"
                f"<form id=\"f\" method=\"post\" action=\"{html.escape(self.github_form_url(), quote=True)}\">"
                f"<input type=\"hidden\" name=\"manifest\" value=\"{m}\"><button>Create the GitHub App</button></form>"
                "<script>document.getElementById('f').submit()</script>")

    def handle(self, path: str, query: dict[str, str]) -> tuple[int, str]:
        try:
            if path == "/":
                return 200, self.start_page()
            if path == "/callback":
                return self._callback(query)
            if path == "/installed":
                return self._installed(query)
            return 404, "<p>not a meld page</p>"
        except (AppAuthError, ConfigError, OSError, ValueError) as e:
            self.error = str(e)
            return 500, f"<p>meld could not finish: {html.escape(str(e))}</p>"

    def _callback(self, query: dict[str, str]) -> tuple[int, str]:
        if query.get("state") != self.state:
            return 400, "<p>this redirect does not belong to this meld create-app run</p>"
        code = query.get("code", "")
        if not code:
            return 400, "<p>GitHub sent no code</p>"
        self.app = self.convert(code)
        self.keys_dir.mkdir(parents=True, exist_ok=True)
        self.pem_path = self.keys_dir / f"{self.group}.pem"
        write_atomic(self.pem_path, self.app["pem"])
        os.chmod(self.pem_path, 0o600)
        install = f"https://github.com/apps/{self.app['slug']}/installations/new"
        return 200, ("<!doctype html><meta charset=\"utf-8\"><title>meld create-app</title>"
                     f"<p>Created the GitHub App <b>{html.escape(self.app['slug'])}</b> (id {self.app['id']}); its key is at "
                     f"<code>{html.escape(str(self.pem_path))}</code>.</p><p>Now install it on <b>{html.escape(self.repo)}</b>: "
                     f"<a href=\"{html.escape(install, quote=True)}\">install {html.escape(self.app['slug'])}</a> (choose only that repository).</p>"
                     f"<script>location.href={json.dumps(install)}</script>")

    def _installed(self, query: dict[str, str]) -> tuple[int, str]:
        if self.app is None:
            return 400, "<p>the App has not been created yet; start from the first page</p>"
        raw = query.get("installation_id", "")
        if raw.isdigit():
            self.installation_id = int(raw)
        else:
            # GitHub's setup redirect normally carries it; if not, ask the App itself which installations it has.
            found = [i for i in self.installations(self.app["id"], self.app["pem"]) if isinstance(i.get("id"), int)]
            if not found:
                return 200, "<p>GitHub reports no installation yet; install the App on the repository and reload this page.</p>"
            self.installation_id = int(found[-1]["id"])
        self.write_group()
        self.done = True
        then = ""
        if self.on_installed is not None:
            result = self.on_installed(self.group) or {}
            then = (f"<p>Checked and started: <b>{html.escape(self.group)}</b> is running (pid {result.get('pid')}). You can close this tab.</p>"
                    if result.get("pid") else
                    f"<p>Not started: {html.escape(str(result.get('error') or 'unknown'))}. The board says what to do; you can close this tab.</p>")
        else:
            then = f"<p>Next: <code>meld doctor</code>, then <code>meld run {html.escape(self.group)} --detach</code>. You can close this tab.</p>"
        return 200, ("<!doctype html><meta charset=\"utf-8\"><title>meld create-app</title>"
                     f"<p>Done. <b>{html.escape(self.group)}</b> now posts as <code>{html.escape(self.app['slug'])}[bot]</code> through installation "
                     f"{self.installation_id}; <code>{html.escape(str(self.config_path))}</code> is written.</p>" + then)

    def write_group(self) -> None:
        """`[groups.<g>]` as an App group: the PAT fields go, the App fields come, everything else stays. Validated by
        `Config.from_dict` and rendered by `dumps` like every other write of the file."""
        assert self.app is not None and self.installation_id is not None and self.pem_path is not None
        data = tomllib.loads(self.config_path.read_text())
        groups = data.setdefault("groups", {})
        g = dict(groups.get(self.group) or {})
        if not g:
            raise ConfigError(f"no [groups.{self.group}] in {self.config_path}; add the group first (the page's Add a group, or by hand)")
        g.pop("token_env", None)
        g.pop("private_key_env", None)
        g.update({"auth": "app", "app_id": self.app["id"], "installation_id": self.installation_id,
                  "private_key_file": str(self.pem_path), "bot": f"{self.app['slug']}[bot]"})
        groups[self.group] = g
        cfg = Config.from_dict(data)
        write_atomic(self.config_path, dumps(cfg))
