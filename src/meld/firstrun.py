"""`meld start` (plan 32): open Meld in a repository and say what you want built.

One session behind the page's wizard. `facts` says what this machine has (the repository from `origin`, who `gh` is
logged in as, which CLIs are installed and logged in and their default models); `create` runs `setup.setup` for the
chosen AIs (labels, milestone, coordination issue, CODEOWNERS, STANDARDS, branch protection, a starter `.meld.toml`);
`app_flow` gives each group its GitHub App through `appsetup.AppSetup`, served by the viewer itself; `start` launches
each group's supervisor. Every seam is injectable so the whole flow runs in tests without a socket, a browser, a
CLI or GitHub.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from meld.adapters import ADAPTER_NAMES, UnknownAdapter, get_adapter
from meld.appsetup import AppSetup
from meld.config import GROUP_NAME_RE, RESERVED_PARTY_NAMES
from meld.github import GitHub, GitHubError
from meld.models import ModelRegistry
from meld.setup import SetupError, setup as setup_repo

_REMOTE_RE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


def repo_from_remote(target_dir: Path, run=subprocess.run) -> str | None:
    """`owner/name` from `git remote get-url origin`, or None when there is no git remote to GitHub here."""
    try:
        proc = run(["git", "-C", str(target_dir), "remote", "get-url", "origin"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    m = _REMOTE_RE.search((proc.stdout or "").strip()) if proc.returncode == 0 else None
    return f"{m.group(1)}/{m.group(2)}" if m else None


@dataclass
class SetupSession:
    target_dir: Path
    home: Path
    registry: ModelRegistry
    gh_factory: Callable[[str], GitHub]                 # repo -> the human's GitHub client
    adapter_for: Callable[[str], object]
    launcher: Callable[[str], dict]                     # group -> what `meld run <group> --detach` returns
    which: Callable[[str], str | None] = shutil.which
    run: Callable = subprocess.run                      # for the CLIs' login probes and git
    base_url: str = ""                                  # the viewer's origin, for the App redirects
    token: str = ""                                     # the viewer's token, carried through those redirects
    created: dict | None = None
    flows: dict[str, AppSetup] = field(default_factory=dict)
    started: dict[str, dict] = field(default_factory=dict)
    error: str | None = None

    # ---- what this machine has ------------------------------------------------------------------------------

    def facts(self) -> dict:
        repo = repo_from_remote(self.target_dir, self.run)
        try:
            login: str | None = self.gh_factory(repo or "octocat/hello").viewer_login()
        except (GitHubError, OSError) as e:
            login = None
            gh_error = str(e)
        else:
            gh_error = None
        clis = [self._cli(name) for name in ADAPTER_NAMES]
        return {"repo": repo, "target_dir": str(self.target_dir), "gh_login": login, "gh_error": gh_error, "clis": clis,
                "created": self.created, "apps": {g: self._app_state(f) for g, f in self.flows.items()}, "started": self.started,
                "config_exists": (self.target_dir / ".meld.toml").exists()}

    def _cli(self, name: str) -> dict:
        try:
            adapter = self.adapter_for(name)
        except UnknownAdapter:
            return {"name": name, "found": False, "logged_in": None, "detail": "no adapter in this build", "models": []}
        binary = adapter.version_argv()[0]
        found = bool(self.which(binary))
        logged_in: bool | None = None
        detail = f"{binary} is not on PATH" if not found else "installed"
        if found and adapter.login_argv():
            try:
                proc = self.run(adapter.login_argv(), capture_output=True, text=True, timeout=20)
                logged_in, detail = adapter.login_status(proc.stdout or "", proc.stderr or "", proc.returncode)
            except (OSError, subprocess.SubprocessError) as e:
                logged_in, detail = None, f"login probe could not run: {e}"
        specs = self.registry.for_adapter(name)
        large = next((s for s in specs if s.tier == "large"), specs[0] if specs else None)
        return {"name": name, "binary": binary, "found": found, "logged_in": logged_in, "detail": detail,
                "models": [s.id for s in specs], "default_model": large.id if large else None}

    @staticmethod
    def _app_state(flow: AppSetup) -> dict:
        return {"created": flow.app is not None, "installed": flow.done, "slug": flow.app["slug"] if flow.app else None, "error": flow.error}

    # ---- the steps ------------------------------------------------------------------------------------------

    def create(self, *, repo: str, goal: str, milestone_title: str, groups: list[str], allow_public: bool = False) -> dict:
        """Labels, milestone, coordination issue, the repo files and a starter `.meld.toml` naming one group per chosen
        CLI (PAT form, which the App step then converts group by group)."""
        if not repo or "/" not in repo:
            raise SetupError("the repository must be owner/name")
        if not goal.strip():
            raise SetupError("say what you want built: the goal becomes the coordination issue")
        bad = [g for g in groups if g not in ADAPTER_NAMES]
        if bad or not groups:
            raise SetupError(f"choose at least one AI from {', '.join(ADAPTER_NAMES)}" + (f"; unknown: {', '.join(bad)}" if bad else ""))
        for g in groups:
            if not GROUP_NAME_RE.match(g) or g in RESERVED_PARTY_NAMES:
                raise SetupError(f"{g!r} cannot name a group")
        gh = self.gh_factory(repo)
        human = gh.viewer_login()
        # setup.setup asks `which` about each real adapter's binary (`setup.installed_adapters` uses the registry, not this
        # session's injectable adapters), so the chosen set is spelled in those binaries.
        chosen = {get_adapter(g).version_argv()[0] for g in groups}
        which = lambda binary: self.which(binary) if binary in chosen else None   # noqa: E731
        self.created = setup_repo(gh, repo=repo, goal=goal, milestone_title=milestone_title or "M1", human=human, target_dir=self.target_dir,
                                  registry=self.registry, allow_public=allow_public, which=which, force=False, protect=True, pr=False, run=self.run)
        missing = [g for g in groups if g not in self.created.get("groups", [])]
        if missing:
            self.created["warnings"] = self.created.get("warnings", []) + [f"not configured (CLI not found or no models): {', '.join(missing)}"]
        return self.created

    def app_flow(self, group: str) -> AppSetup:
        """The App flow for one configured group, served under `<base_url>/setup/app/<group>`; GitHub's redirects carry
        the viewer's token so they pass its gate."""
        if self.created is None:
            raise SetupError("create the repository setup first")
        if group not in self.created.get("groups", []):
            raise SetupError(f"{group} is not one of the configured groups")
        if group not in self.flows:
            repo = self.created["repo"]
            owner = repo.partition("/")[0]
            try:
                owner_is_org = str(self.gh_factory(repo).get(f"users/{owner}").get("type", "")) == "Organization"
            except GitHubError:
                owner_is_org = False
            self.flows[group] = AppSetup(config_path=self.target_dir / ".meld.toml", group=group, repo=repo, owner_is_org=owner_is_org,
                                         base_url=f"{self.base_url}/setup/app/{group}", keys_dir=self.home / "keys", suffix=f"?t={self.token}" if self.token else "")
        return self.flows[group]

    def start(self) -> dict:
        """Launch a supervisor per group whose App is installed (or whose token is set); the launcher is `meld run --detach`."""
        if self.created is None:
            raise SetupError("create the repository setup first")
        for g in self.created.get("groups", []):
            if g in self.started and not self.started[g].get("error"):
                continue
            flow = self.flows.get(g)
            if flow is None or not flow.done:
                self.started[g] = {"error": "its GitHub App is not installed yet"}
                continue
            try:
                self.started[g] = self.launcher(g)
            except Exception as e:   # noqa: BLE001 -- one group's launch failing must not hide the others
                self.started[g] = {"error": str(e)}
        return self.started
