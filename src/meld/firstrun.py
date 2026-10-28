"""`meld start` (plan 32): open Meld in a repository and say what you want built.

One session behind the page's wizard. `facts` says what this machine has (the repository from `origin`, who `gh` is
logged in as, which CLIs are installed and logged in and their default models); `create` runs `setup.setup` for the
chosen AIs (labels, milestone, coordination issue, CODEOWNERS, STANDARDS, branch protection, a starter `.meld.toml`);
`app_flow` gives each group its GitHub App through `appsetup.AppSetup`, served by the viewer itself; `start` launches
each group's supervisor. Every seam is injectable so the whole flow runs in tests without a socket, a browser, a
CLI or GitHub.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from meld.adapters import ADAPTER_NAMES, UnknownAdapter, get_adapter
from meld.appsetup import AppSetup
from meld.config import GROUP_NAME_RE, RESERVED_PARTY_NAMES
from meld.github import GitHub, GitHubError
from meld.gitops import _CREDENTIAL_HELPER
from meld.models import ModelRegistry
from meld.setup import SetupError, setup as setup_repo

_REMOTE_RE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")

#: What each adapter is, for a person choosing: adapters are named after their binaries, and two of them are Google's
#: Gemini (the Antigravity CLI replaced the older `gemini` binary for most accounts in June 2026).
TITLES = {"claude": "Claude Code (Anthropic)", "codex": "Codex CLI (OpenAI)", "grok": "Grok Build (xAI)",
          "antigravity": "Gemini, through Google's Antigravity CLI (agy)", "gemini": "Gemini CLI (the older `gemini` binary)"}
#: The model a group of this adapter starts with: the vendor's own first, then the first large-tier entry.
PREFERRED_MODEL_PREFIX = {"antigravity": "gemini-"}


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
    doctor: Callable[[str], list[dict]] | None = None    # group -> that group's doctor checks, as dicts; None skips the check
    base_url: str = ""                                  # the viewer's origin, for the App redirects
    token: str = ""                                     # the viewer's token, carried through those redirects
    home_for: Callable[[str], Path] | None = None      # repo -> that product's own state directory (plan 42); None keeps `home`
    created: dict | None = None
    flows: dict[str, AppSetup] = field(default_factory=dict)
    started: dict[str, dict] = field(default_factory=dict)
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)   # one start at a time (plan 46)

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
        repo_exists: bool | None = None
        if repo and login:
            try:
                self.gh_factory(repo).get_repo()
                repo_exists = True
            except GitHubError as e:
                repo_exists = False if e.status == 404 else None
        clis = [self._cli(name) for name in ADAPTER_NAMES]
        return {"repo": repo, "repo_exists": repo_exists, "git": self._git_state(), "target_dir": str(self.target_dir),
                "gh_login": login, "gh_error": gh_error, "clis": clis,
                "created": self.created, "apps": {g: self._app_state(f) for g, f in self.flows.items()}, "started": self.started,
                "config_exists": (self.target_dir / ".meld.toml").exists()}

    def _cli(self, name: str) -> dict:
        try:
            adapter = self.adapter_for(name)
        except UnknownAdapter:
            return {"name": name, "title": TITLES.get(name, name), "found": False, "logged_in": None, "detail": "no adapter in this build", "models": []}
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
        prefix = PREFERRED_MODEL_PREFIX.get(name)
        own = [s for s in specs if prefix and s.id.startswith(prefix)]
        pick = (next((s for s in own if s.tier != "small"), own[0] if own else None)
                or next((s for s in specs if s.tier == "large"), specs[0] if specs else None))
        return {"name": name, "title": TITLES.get(name, name), "binary": binary, "found": found, "logged_in": logged_in, "detail": detail,
                "models": [s.id for s in specs], "default_model": pick.id if pick else None}

    @staticmethod
    def _app_state(flow: AppSetup) -> dict:
        slug = flow.app["slug"] if flow.app else None
        return {"created": flow.app is not None, "installed": flow.done, "slug": slug, "error": flow.error,
                "url": f"https://github.com/apps/{slug}" if slug else None}   # where the page sends a click once the App exists

    # ---- the steps ------------------------------------------------------------------------------------------

    # ---- the repository itself (plan 34) ---------------------------------------------------------------------

    def _git(self, *args: str):
        return self.run(["git", "-C", str(self.target_dir), *args], capture_output=True, text=True, timeout=120)

    def _git_state(self) -> dict:
        try:
            is_repo = self._git("rev-parse", "--git-dir").returncode == 0
            commits = is_repo and self._git("rev-parse", "--verify", "HEAD").returncode == 0
            remote = is_repo and self._git("remote", "get-url", "origin").returncode == 0
        except (OSError, subprocess.SubprocessError):
            return {"initialised": None, "commits": None, "remote": None}
        return {"initialised": is_repo, "commits": bool(commits), "remote": bool(remote)}

    def ensure_repository(self, repo: str) -> dict:
        """The repository on GitHub, created private when it is not there: under the person's own account when the owner
        is their login, else under that organisation. A 404 is the one answer that means "create"; anything else is
        reported as the error it is."""
        gh = self.gh_factory(repo)
        try:
            info = gh.get_repo()
        except GitHubError as e:
            if e.status != 404:
                raise
        else:
            # Created earlier but never pushed (a wizard run that failed at the push, a repository made by hand): empty is
            # as good as new for the push step. Branches that cannot be read are taken as present, so nothing is pushed over.
            try:
                branches = gh.get(f"repos/{repo}/branches", use_cache=False)
                empty = isinstance(branches, list) and not branches
            except GitHubError:
                empty = False
            return {"created": False, "empty": empty, "private": bool(info.get("private", False)), "default_branch": str(info.get("default_branch") or "main")}
        owner, _, name = repo.partition("/")
        login = gh.viewer_login()
        body = {"name": name, "private": True, "auto_init": False, "description": "coordinated by meld"}
        made = gh.post("user/repos", body) if owner.lower() == login.lower() else gh.post(f"orgs/{owner}/repos", body)
        return {"created": True, "empty": True, "private": True, "default_branch": str((made or {}).get("default_branch") or "main")}

    def _gh_token(self) -> str:
        """The token `gh` is logged in with -- the same login the wizard reads GitHub as -- for the one push the wizard makes."""
        try:
            proc = self.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return ""
        return (proc.stdout or "").strip() if proc.returncode == 0 else ""

    def ensure_pushed(self, repo: str, default_branch: str) -> dict:
        """This folder on that repository's default branch: `git init` when it is not a repository, an initial commit when
        it has none (a README when it is empty), `origin` set to the repository's HTTPS URL (added, or rewritten from an
        SSH spelling of the same repository, because Meld's own roles fetch through `origin` and an SSH remote needs a
        key this machine may not have -- the first real run failed exactly there), then one push over HTTPS with the
        token `gh` is logged in with, through the same inline credential helper every Meld push uses, never in argv.
        A push that fails is reported, never retried."""
        out = {"initialised": False, "committed": False, "remote_added": False, "remote_rewritten": False, "pushed": False}
        https = f"https://github.com/{repo}.git"
        if self._git("rev-parse", "--git-dir").returncode != 0:
            self._git("init", "-q", "-b", default_branch)
            out["initialised"] = True
        if self._git("rev-parse", "--verify", "HEAD").returncode != 0:
            readme = self.target_dir / "README.md"
            if not readme.exists():
                readme.write_text(f"# {repo.partition('/')[2]}\n")
            self._git("add", "-A")
            out["committed"] = self._git("commit", "-q", "-m", "Initial commit").returncode == 0
        current = self._git("remote", "get-url", "origin")
        if current.returncode != 0:
            self._git("remote", "add", "origin", https)
            out["remote_added"] = True
        elif (current.stdout or "").strip() != https:
            self._git("remote", "set-url", "origin", https)
            out["remote_rewritten"] = True
        out.update(self._push(default_branch))
        return out

    def _push(self, default_branch: str) -> dict:
        """HEAD to origin's default branch over HTTPS, with the token `gh` is logged in with through the inline helper."""
        token = self._gh_token()
        creds = ["-c", "credential.helper=", "-c", f"credential.helper={_CREDENTIAL_HELPER}"] if token else []
        env = {**os.environ, "MELD_PUSH_TOKEN": token} if token else None
        pushed = self.run(["git", "-C", str(self.target_dir), *creds, "push", "-q", "--set-upstream", "origin", f"HEAD:refs/heads/{default_branch}"],
                          capture_output=True, text=True, timeout=120, env=env)
        out = {"pushed": pushed.returncode == 0}
        if not out["pushed"]:
            out["push_error"] = (pushed.stderr or "").strip()[-300:]
        return out

    def commit_setup_files(self, files: list[str], default_branch: str) -> dict:
        """The files `setup.setup` wrote (`.meld.toml`, CODEOWNERS, STANDARDS) committed on the default branch and pushed, as
        the person: the roles read `.meld.toml` from the clone and fetch through `origin`, so a file that exists only in
        this folder is a board only this machine can see. Nothing to commit (a second run) is not an error."""
        self._git("add", "--", *files)
        committed = self._git("commit", "-q", "-m", "meld: set up the board").returncode == 0
        out = {"committed": committed, **self._push(default_branch)}
        return out

    def create(self, *, repo: str, goal: str, milestone_title: str, groups: list[str], allow_public: bool = False, context: str = "") -> dict:
        """The repository on GitHub (created when it is not there, and this folder pushed to it), then labels, milestone,
        coordination issue, the repo files and a starter `.meld.toml` naming one group per chosen CLI (PAT form, which
        the App step then converts group by group)."""
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
        if self.home_for is not None:
            self.home = self.home_for(repo)   # keys, logs and runs for this product go under its own home from here on
        repository = self.ensure_repository(repo)
        if repository["created"] or repository.get("empty"):
            repository["push"] = self.ensure_pushed(repo, repository["default_branch"])
            if not repository["push"]["pushed"]:
                raise SetupError(f"created {repo} on GitHub but could not push this folder to it: {repository['push'].get('push_error') or 'git push failed'}")
        gh = self.gh_factory(repo)
        human = gh.viewer_login()
        # setup.setup asks `which` about each real adapter's binary (`setup.installed_adapters` uses the registry, not this
        # session's injectable adapters), so the chosen set is spelled in those binaries.
        chosen = {get_adapter(g).version_argv()[0] for g in groups}
        which = lambda binary: self.which(binary) if binary in chosen else None   # noqa: E731
        self.created = setup_repo(gh, repo=repo, goal=goal, milestone_title=milestone_title or "M1", human=human, target_dir=self.target_dir,
                                  registry=self.registry, allow_public=allow_public, which=which, force=False, protect=True, pr=False, run=self.run,
                                  context=context)
        self.created["repository"] = repository
        files = [".meld.toml", *(self.created.get("files") or [])]
        self.created["setup_commit"] = self.commit_setup_files(files, repository["default_branch"])
        if not self.created["setup_commit"]["pushed"]:
            self.created["warnings"] = self.created.get("warnings", []) + [
                f"the setup files are written here but not pushed: {self.created['setup_commit'].get('push_error') or 'git push failed'}; commit and push them yourself"]
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
                                         base_url=f"{self.base_url}/setup/app/{group}", keys_dir=self.home / "keys", suffix=f"?t={self.token}" if self.token else "",
                                         on_installed=lambda g: self.start([g]).get(g, {}))
        return self.flows[group]

    def _has_identity(self, group: str) -> bool:
        """The group can act as itself: its App flow finished here, or the file already names an App or a token this
        process can see (a group given its identity by an earlier run, or by hand)."""
        flow = self.flows.get(group)
        if flow is not None and flow.done:
            return True
        try:
            from meld.config import load_config
            g = load_config(self.target_dir / ".meld.toml").groups.get(group)
        except Exception:   # noqa: BLE001 -- an unloadable file is "no identity", and doctor will say why
            return False
        if g is None:
            return False
        return (g.is_app and bool(g.app_id)) or bool(g.token_env and os.environ.get(g.token_env))

    def start(self, groups: list[str] | None = None) -> dict:
        """Launch a supervisor per group that has its identity, after that group's doctor checks pass (plan 36: nothing
        after the two clicks is a command). A group that failed doctor is reported with the failing checks and not
        started; the launcher is what `meld run --detach` does."""
        if self.created is None:
            raise SetupError("create the repository setup first")
        # Plan 46: the install redirect and the page's own start both reached here within two seconds on the first real
        # run and launched two codex supervisors. One caller at a time; the second finds the group started and skips it.
        with self.lock:
            return self._start(groups)

    def _start(self, groups: list[str] | None) -> dict:
        assert self.created is not None
        for g in groups or self.created.get("groups", []):
            if g in self.started and not self.started[g].get("error"):
                continue
            if not self._has_identity(g):
                self.started[g] = {"error": "its GitHub App is not installed yet"}
                continue
            if self.doctor is not None:
                try:
                    checks = self.doctor(g)
                except Exception as e:   # noqa: BLE001
                    checks = [{"name": f"doctor:{g}", "ok": False, "detail": str(e), "fix": ""}]
                failed = [c for c in checks if c.get("ok") is False]
                if failed:
                    self.started[g] = {"error": "doctor: " + "; ".join(f"{c['name']}: {c.get('detail', '')}" + (f" ({c['fix']})" if c.get("fix") else "") for c in failed),
                                       "checks": checks}
                    continue
                self.started[g] = {"checks": checks}
            try:
                kept = {k: v for k, v in self.started.get(g, {}).items() if k != "error"}   # a launch clears an earlier refusal
                self.started[g] = {**kept, **self.launcher(g)}
            except Exception as e:   # noqa: BLE001 -- one group's launch failing must not hide the others
                self.started[g] = {"error": str(e)}
        return self.started
