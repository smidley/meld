"""Plan 32: `meld start` -- open Meld in a repository and say what you want built -- driven without a socket, a browser,
a CLI or GitHub."""
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from meld.firstrun import SetupSession, repo_from_remote
from meld.github import GitHub
from meld.models import ModelRegistry
from meld.setup import SetupError
from tests.fakegh import FakeGh
from tests.test_setup import R, route_repo

PEM = "-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n"


class ProbeRun:
    """A subprocess.run stand-in: git's origin URL, and each CLI's login answer."""
    def __init__(self, remote="git@github.com:acme/widgets.git", logged={"claude": True, "codex": False}):
        self.remote, self.logged, self.calls = remote, logged, []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        if argv[:2] == ["git", "-C"]:
            return SimpleNamespace(returncode=0 if self.remote else 2, stdout=self.remote or "", stderr="")
        binary = argv[0]
        if binary == "claude":
            return SimpleNamespace(returncode=0, stdout=json.dumps({"loggedIn": self.logged.get("claude", False), "authMethod": "oauth"}), stderr="")
        if binary == "codex":
            return SimpleNamespace(returncode=0, stdout="Not logged in" if not self.logged.get("codex") else "Logged in using ChatGPT", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unknown")


def routes_for_create(fake: FakeGh) -> None:
    route_repo(fake)
    fake.route("GET", R + "labels", [])
    fake.route("POST", R + "labels", {"name": "x"}, status=201)
    fake.route("GET", R + "milestones", [])
    fake.route("POST", R + "milestones", {"number": 1, "title": "M1"}, status=201)
    fake.route("GET", R + "issues", [])
    fake.route("POST", R + "issues", {"number": 2}, status=201)
    fake.route("GET", "users/acme", {"login": "acme", "type": "Organization"})


def session(tmp_path: Path, fake: FakeGh | None = None, which=None, run=None, launcher=None) -> SetupSession:
    fake = fake or FakeGh()
    launched = []
    return SetupSession(target_dir=tmp_path, home=tmp_path / "home", registry=ModelRegistry.load(),
                        gh_factory=lambda repo: GitHub(repo, token=None, run=fake),
                        adapter_for=__import__("meld.adapters", fromlist=["get_adapter"]).get_adapter,
                        launcher=launcher or (lambda g: launched.append(g) or {"group": g, "pid": 100 + len(launched), "detached": True}),
                        which=which or (lambda b: "/bin/" + b if b in ("claude", "codex") else None), run=run or ProbeRun(),
                        base_url="http://127.0.0.1:5", token="tok")


def test_repo_from_remote_reads_ssh_and_https_origins(tmp_path):
    assert repo_from_remote(tmp_path, ProbeRun("git@github.com:acme/widgets.git")) == "acme/widgets"
    assert repo_from_remote(tmp_path, ProbeRun("https://github.com/acme/widgets")) == "acme/widgets"
    assert repo_from_remote(tmp_path, ProbeRun("https://github.com/acme/widgets.git/")) == "acme/widgets"
    assert repo_from_remote(tmp_path, ProbeRun("")) is None and repo_from_remote(tmp_path, ProbeRun("https://gitlab.com/a/b")) is None


def test_facts_say_what_this_machine_has(tmp_path):
    fake = FakeGh()
    fake.route("GET", "user", {"login": "empowerScott"})
    f = session(tmp_path, fake).facts()
    assert f["repo"] == "acme/widgets" and f["gh_login"] == "empowerScott" and f["config_exists"] is False and f["created"] is None
    by = {c["name"]: c for c in f["clis"]}
    assert set(by) == {"claude", "codex", "gemini", "grok", "antigravity"}
    assert by["claude"]["found"] and by["claude"]["logged_in"] is True and by["claude"]["default_model"] and by["claude"]["models"]
    assert by["codex"]["found"] and by["codex"]["logged_in"] is False and "not logged in" in by["codex"]["detail"]
    assert by["grok"]["found"] is False and by["grok"]["logged_in"] is None and "not on PATH" in by["grok"]["detail"]
    # two of the five are Google's Gemini, said so by title; an Antigravity group starts on a Gemini model, not the first large one
    assert by["antigravity"]["title"].startswith("Gemini, through Google's Antigravity") and by["gemini"]["title"].startswith("Gemini CLI")
    assert by["antigravity"]["default_model"].startswith("gemini-") and by["claude"]["title"] == "Claude Code (Anthropic)"


def test_create_runs_the_repository_setup_for_the_chosen_ais_only(tmp_path):
    fake = FakeGh()
    routes_for_create(fake)
    s = session(tmp_path, fake)
    with pytest.raises(SetupError, match="say what you want built"):
        s.create(repo="acme/widgets", goal="  ", milestone_title="M1", groups=["claude"])
    with pytest.raises(SetupError, match="choose at least one AI"):
        s.create(repo="acme/widgets", goal="Ship it", milestone_title="M1", groups=[])
    with pytest.raises(SetupError, match="owner/name"):
        s.create(repo="widgets", goal="Ship it", milestone_title="M1", groups=["claude"])
    fake.route("GET", R + "branches", [{"name": "main"}])
    out = s.create(repo="acme/widgets", goal="Ship a widget catalogue", milestone_title="M1", groups=["claude"], context="Read docs/DESIGN.md first.")
    assert out["milestone"] == 1 and out["coordination_issue"] == 2 and out["groups"] == ["claude"]      # codex is installed but was not chosen
    assert (tmp_path / ".meld" / "CONTEXT.md").read_text().endswith("Read docs/DESIGN.md first.\n") and ".meld/CONTEXT.md" in out["files"]
    assert "## Context\n\nRead docs/DESIGN.md first." in [c for c in fake.calls if c.method == "POST" and c.endpoint == R + "issues"][-1].body["body"]
    assert "repository" in out and out["repository"]["created"] is False and "setup_commit" in out
    text = (tmp_path / ".meld.toml").read_text()
    assert "[groups.claude]" in text and "[groups.codex]" not in text.replace("# [groups.codex]", "")
    assert s.facts()["created"]["groups"] == ["claude"] and s.facts()["config_exists"]


def test_the_app_flow_per_group_carries_the_viewers_token_and_writes_the_group(tmp_path):
    fake = FakeGh()
    routes_for_create(fake)
    s = session(tmp_path, fake)
    with pytest.raises(SetupError, match="create the repository setup first"):
        s.app_flow("claude")
    s.create(repo="acme/widgets", goal="Ship it", milestone_title="M1", groups=["claude"])
    with pytest.raises(SetupError, match="not one of the configured groups"):
        s.app_flow("codex")
    flow = s.app_flow("claude")
    assert flow is s.app_flow("claude") and flow.owner_is_org is True
    m = flow.manifest()
    assert m["redirect_url"] == "http://127.0.0.1:5/setup/app/claude/callback?t=tok" and m["setup_url"] == "http://127.0.0.1:5/setup/app/claude/installed?t=tok"
    flow.convert = lambda code: {"id": 7, "slug": "meld-claude", "pem": PEM, "html_url": "u"}
    assert flow.handle("/callback", {"state": flow.state, "code": "c"})[0] == 200
    assert flow.handle("/installed", {"installation_id": "99"})[0] == 200 and flow.done
    assert s.facts()["apps"] == {"claude": {"created": True, "installed": True, "slug": "meld-claude", "error": None, "url": "https://github.com/apps/meld-claude"}}
    assert stat.S_IMODE((tmp_path / "home" / "keys" / "claude.pem").stat().st_mode) == 0o600


def test_start_launches_only_groups_whose_app_is_installed(tmp_path):
    fake = FakeGh()
    routes_for_create(fake)
    launched = []
    s = session(tmp_path, fake, launcher=lambda g: launched.append(g) or {"group": g, "pid": 42, "detached": True})
    with pytest.raises(SetupError):
        s.start()
    s.create(repo="acme/widgets", goal="Ship it", milestone_title="M1", groups=["claude", "codex"])
    assert s.start() == {"claude": {"error": "its GitHub App is not installed yet"}, "codex": {"error": "its GitHub App is not installed yet"}} and launched == []
    for g in ("claude", "codex"):
        flow = s.app_flow(g)
        flow.convert = lambda code, g=g: {"id": 7, "slug": f"meld-{g}", "pem": PEM, "html_url": "u"}
        flow.handle("/callback", {"state": flow.state, "code": "c"})
        flow.handle("/installed", {"installation_id": "99"})
    out = s.start()
    assert launched == ["claude", "codex"] and out["claude"]["pid"] == 42 and out["codex"]["pid"] == 42
    assert s.start() == out and launched == ["claude", "codex"]                      # started once; a second start launches nothing twice


class GitRun(ProbeRun):
    """ProbeRun plus a git that remembers: no repository, then a commit, a remote and a push, as the session asks for them."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.git_state = {"repo": False, "head": False, "origin": False}
        self.git = []

    def __call__(self, argv, **kw):
        if argv[:3] == ["gh", "auth", "token"]:
            return SimpleNamespace(returncode=0, stdout="ghp_token\n", stderr="")
        if argv[:2] == ["git", "-C"]:
            sub = [a for a in argv[3:] if not a.startswith("credential.helper")]
            sub = [a for a in sub if a != "-c"]
            self.git.append(sub)
            self.envs = getattr(self, "envs", []) + [kw.get("env")]
            s = self.git_state
            if sub[:2] == ["rev-parse", "--git-dir"]: return SimpleNamespace(returncode=0 if s["repo"] else 128, stdout="", stderr="")
            if sub[:2] == ["rev-parse", "--verify"]: return SimpleNamespace(returncode=0 if s["head"] else 128, stdout="", stderr="")
            if sub[:2] == ["remote", "get-url"]: return SimpleNamespace(returncode=0 if s["origin"] else 2, stdout=self.remote if s["origin"] else "", stderr="")
            if sub[0] == "init": s["repo"] = True
            if sub[0] == "commit": s["head"] = True
            if sub[:2] == ["remote", "add"]: s["origin"] = True; self.remote = sub[3]
            if sub[:2] == ["remote", "set-url"]: self.remote = sub[3]
            if sub[0] == "push": return SimpleNamespace(returncode=0 if s["origin"] and s["head"] else 1, stdout="", stderr="no upstream")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return super().__call__(argv, **kw)


class CreatingFake(FakeGh):
    """A fake GitHub on which the repository appears once it has been created."""
    def __call__(self, cmd, input=None, **kw):
        out = super().__call__(cmd, input=input, **kw)
        last = self.calls[-1]
        if last.method == "POST" and last.endpoint in ("user/repos", "orgs/acme/repos"):
            self.route("GET", "repos/acme/widgets", {"private": True, "default_branch": "main", "full_name": "acme/widgets"})
        return out


def test_the_repository_is_created_private_when_it_is_not_on_github(tmp_path):
    """Plan 34: "if a repo isn't already created, meld should create it" -- under the person's account when they own it,
    else under the organisation, private, and this folder pushed to its default branch."""
    fake = CreatingFake()
    fake.route("GET", "user", {"login": "acme"})                                  # the owner is the person
    fake.route("POST", "user/repos", {"full_name": "acme/widgets", "default_branch": "main"}, status=201)
    run = GitRun(remote="")
    s = session(tmp_path, fake, run=run)
    assert s.facts()["repo_exists"] is None and s.facts()["git"] == {"initialised": False, "commits": False, "remote": False}   # no origin: no repo to ask about
    made = s.ensure_repository("acme/widgets")
    assert made == {"created": True, "empty": True, "private": True, "default_branch": "main"}
    assert [c for c in fake.calls if c.method == "POST"][0].body == {"name": "widgets", "private": True, "auto_init": False, "description": "coordinated by meld"}
    assert s.ensure_repository("acme/widgets")["created"] is False                 # there now
    push = s.ensure_pushed("acme/widgets", "main")
    assert push == {"initialised": True, "committed": True, "remote_added": True, "remote_rewritten": False, "pushed": True}
    assert (tmp_path / "README.md").read_text() == "# widgets\n" and ["push", "-q", "--set-upstream", "origin", "HEAD:refs/heads/main"] in run.git
    assert ["remote", "add", "origin", "https://github.com/acme/widgets.git"] in run.git and ["init", "-q", "-b", "main"] in run.git
    assert run.envs[-1]["MELD_PUSH_TOKEN"] == "ghp_token"                        # the push authenticates with gh's token, never in argv
    # an SSH origin naming the same repository is rewritten to HTTPS: Meld's roles fetch through origin and need no key
    ssh = GitRun(remote="git@github.com:acme/widgets.git")
    ssh.git_state.update({"repo": True, "head": True, "origin": True})
    s2 = session(tmp_path, fake, run=ssh)
    push2 = s2.ensure_pushed("acme/widgets", "main")
    assert push2["remote_rewritten"] is True and push2["pushed"] is True and ["remote", "set-url", "origin", "https://github.com/acme/widgets.git"] in ssh.git
    # an existing, empty repository is pushed to as if just created
    fake4 = FakeGh()
    fake4.route("GET", "repos/acme/widgets", {"private": True, "default_branch": "main"})
    fake4.route("GET", "repos/acme/widgets/branches", [])
    assert session(tmp_path, fake4).ensure_repository("acme/widgets") == {"created": False, "empty": True, "private": True, "default_branch": "main"}
    fake5 = FakeGh()
    fake5.route("GET", "repos/acme/widgets", {"private": True, "default_branch": "main"})
    fake5.route("GET", "repos/acme/widgets/branches", [{"name": "main"}])
    assert session(tmp_path, fake5).ensure_repository("acme/widgets")["empty"] is False


def test_create_makes_the_repository_first_and_reports_it(tmp_path):
    fake = CreatingFake()
    fake.route("GET", "user", {"login": "empowerScott"})                          # acme is an organisation to this person
    fake.route("POST", "orgs/acme/repos", {"full_name": "acme/widgets", "default_branch": "main"}, status=201)
    fake.route("GET", "users/acme", {"login": "acme", "type": "Organization"})
    for method, endpoint, body, status in (("GET", R + "labels", [], 200), ("POST", R + "labels", {"name": "x"}, 201), ("GET", R + "milestones", [], 200),
                                           ("POST", R + "milestones", {"number": 1, "title": "M1"}, 201), ("GET", R + "issues", [], 200), ("POST", R + "issues", {"number": 2}, 201)):
        fake.route(method, endpoint, body, status=status)
    run = GitRun(remote="")
    s = session(tmp_path, fake, run=run)
    s.home_for = lambda repo: tmp_path / "homes" / repo          # plan 42: the product's own state directory follows the repository
    out = s.create(repo="acme/widgets", goal="Ship it", milestone_title="M1", groups=["claude"])
    assert out["repository"]["created"] is True and out["repository"]["push"]["pushed"] is True and out["groups"] == ["claude"]
    assert s.home == tmp_path / "homes" / "acme" / "widgets"
    assert [c.endpoint for c in fake.calls if c.method == "POST"][0] == "orgs/acme/repos"
    # the setup files are committed on the default branch and pushed, as the person, so the roles' clones can read them
    assert out["setup_commit"] == {"committed": True, "pushed": True}
    adds = [g for g in run.git if g[:2] == ["add", "--"]]
    assert adds and ".meld.toml" in adds[-1] and any(".github/CODEOWNERS" in a for a in adds[-1]) and ["commit", "-q", "-m", "meld: set up the board"] in run.git
    # a push that fails is the error, before anything else is set up
    fake2 = CreatingFake()
    fake2.route("GET", "user", {"login": "acme"})
    fake2.route("POST", "user/repos", {"full_name": "acme/widgets"}, status=201)
    broken = GitRun(remote="")
    original = broken.__call__
    def no_push(argv, **kw):
        if argv[:2] == ["git", "-C"] and "push" in argv:
            return SimpleNamespace(returncode=1, stdout="", stderr="fatal: could not read Username")
        return original(argv, **kw)
    broken.__call__ = no_push
    s2 = SetupSession(target_dir=tmp_path / "two", home=tmp_path / "home", registry=ModelRegistry.load(), gh_factory=lambda repo: GitHub(repo, token=None, run=fake2),
                      adapter_for=s.adapter_for, launcher=s.launcher, which=s.which, run=no_push)
    (tmp_path / "two").mkdir()
    with pytest.raises(SetupError, match="could not push this folder"):
        s2.create(repo="acme/widgets", goal="Ship it", milestone_title="M1", groups=["claude"])
    # an answer other than 404 is never taken as "create"
    fake3 = FakeGh()
    fake3.route("GET", "repos/acme/widgets", {"message": "bad credentials"}, status=401)
    from meld.github import GitHubError
    with pytest.raises(GitHubError):
        session(tmp_path, fake3).ensure_repository("acme/widgets")


def test_an_installed_app_checks_and_starts_its_group_by_itself_and_doctor_can_refuse(tmp_path):
    """Plan 36: nothing after the two clicks is a command. The install redirect runs the group's doctor checks and, when
    they pass, its supervisor; failing checks are reported on the page and the group is not started."""
    fake = FakeGh()
    routes_for_create(fake)
    launched = []
    verdict = {"ok": True}
    def doctor(group):
        return [{"name": f"app:{group}", "ok": verdict["ok"], "detail": "fine" if verdict["ok"] else "App 7 is not the configured bot", "fix": "" if verdict["ok"] else "run meld create-app"}]
    s = session(tmp_path, fake, launcher=lambda g: launched.append(g) or {"group": g, "pid": 5, "detached": True})
    s.doctor = doctor
    s.create(repo="acme/widgets", goal="Ship it", milestone_title="M1", groups=["claude", "codex"])
    flow = s.app_flow("claude")
    flow.convert = lambda code: {"id": 7, "slug": "meld-claude", "pem": PEM, "html_url": "u"}
    flow.handle("/callback", {"state": flow.state, "code": "c"})
    status, page = flow.handle("/installed", {"installation_id": "99"})
    assert status == 200 and "Checked and started" in page and "pid 5" in page and launched == ["claude"]
    assert s.started["claude"]["pid"] == 5 and s.started["claude"]["checks"][0]["ok"] is True
    # codex: doctor fails -> reported, not started
    verdict["ok"] = False
    flow2 = s.app_flow("codex")
    flow2.convert = lambda code: {"id": 8, "slug": "meld-codex", "pem": PEM, "html_url": "u"}
    flow2.handle("/callback", {"state": flow2.state, "code": "c"})
    status, page = flow2.handle("/installed", {"installation_id": "100"})
    assert status == 200 and "Not started: doctor: app:codex: App 7 is not the configured bot (run meld create-app)" in page and launched == ["claude"]
    assert s.started["codex"]["error"].startswith("doctor:")
    # the board's own start, one group at a time, retries a group that failed and skips one already running
    verdict["ok"] = True
    out = s.start(["codex"])
    assert launched == ["claude", "codex"] and out["codex"]["pid"] == 5 and out["claude"]["pid"] == 5
    # a group whose identity is in the file (an earlier run) is ready even when no flow ran in this process
    fresh = session(tmp_path, fake, launcher=lambda g: launched.append(g) or {"group": g, "pid": 6, "detached": True})
    fresh.created = {"repo": "acme/widgets", "groups": ["claude", "codex"]}
    assert fresh._has_identity("claude") is True and fresh.start(["claude"])["claude"]["pid"] == 6
    blank = session(tmp_path / "nowhere", fake)
    blank.created = {"repo": "acme/widgets", "groups": ["claude"]}
    assert blank.start()["claude"]["error"] == "its GitHub App is not installed yet"


def test_two_concurrent_starts_launch_a_group_once(tmp_path):
    """Plan 46: the install redirect and the page's own start reached `start` within two seconds on the first real run and
    launched two codex supervisors. One caller at a time; the second finds the group started."""
    import threading
    import time
    fake = FakeGh()
    routes_for_create(fake)
    launched = []
    def slow_launch(g):
        time.sleep(0.2)
        launched.append(g)
        return {"group": g, "pid": 5, "detached": True}
    s = session(tmp_path, fake, launcher=slow_launch)
    s.create(repo="acme/widgets", goal="Ship it", milestone_title="M1", groups=["codex"])
    flow = s.app_flow("codex")
    flow.convert = lambda code: {"id": 8, "slug": "meld-codex", "pem": PEM, "html_url": "u"}
    flow.handle("/callback", {"state": flow.state, "code": "c"})
    flow.done = True                       # installed, without running the redirect's own start
    threads = [threading.Thread(target=lambda: s.start(["codex"])) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert launched == ["codex"] and s.started["codex"]["pid"] == 5
    assert s.facts()["apps"]["codex"]["url"] == "https://github.com/apps/meld-codex"


def test_the_wizard_uses_the_gh_account_that_owns_the_repository(tmp_path):
    """Plan 45: the owner ran the wizard as their work account and named a repository under their personal one; the wizard
    read GitHub as the work account, took the personal owner for an organisation, and GitHub said 404. `gh_token_for`
    asks `gh` for the owner's own account first, and the create step says what would work when neither account owns it."""
    from meld.firstrun import gh_token_for

    def gh(argv, **kw):
        if argv == ["gh", "auth", "token", "--user", "smidley"]:
            return SimpleNamespace(returncode=0, stdout="tok-smidley\n", stderr="")
        if argv == ["gh", "auth", "token", "--user", "nobody"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="no such account")
        if argv == ["gh", "auth", "token"]:
            return SimpleNamespace(returncode=0, stdout="tok-active\n", stderr="")
        raise AssertionError(argv)

    assert gh_token_for("smidley", gh) == "tok-smidley" and gh_token_for("nobody", gh) == "tok-active" and gh_token_for(None, gh) == "tok-active"
    assert gh_token_for("smidley", lambda *a, **k: (_ for _ in ()).throw(OSError("no gh"))) == ""
    # neither account owns the repository: the org create answers 404 and the error names the login and the two ways out
    fake = CreatingFake()
    fake.route("GET", "user", {"login": "tiltScott"})
    fake.route("POST", "orgs/smidley/repos", {"message": "Not Found"}, status=404)
    s = session(tmp_path, fake, run=GitRun(remote=""))
    with pytest.raises(SetupError, match="GitHub reads you as tiltScott, and smidley is neither you nor an organisation") as e:
        s.ensure_repository("smidley/widgets")
    assert "tiltScott/widgets" in str(e.value) and "gh auth login" in str(e.value)
