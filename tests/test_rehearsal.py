"""The dress rehearsal (plan 48): the whole of meld end to end on this machine, with no network and no money.

The real wizard builds a product from an empty folder against a stand-in GitHub; the real detached supervisors launch
the real adapters, which run stand-in `claude` and `codex` binaries that play orchestrator, PM and worker by policy
from `meld status` alone; the plan is proposed, agreed, approved by the person, turned into an issue, claimed, built,
reviewed and merged. Everything a role does goes through `meld` on PATH. A protocol violation or a NEEDS-HUMAN along
the way is a failure: a stand-in that only does what `legal` lists must never be refused and never need a person.
"""
from __future__ import annotations

import html as htmlmod
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.rehearsal import cli as standin
from meld.rehearsal import ghshim
from meld.rehearsal.github import FakeGitHub
from meld.viewer.server import Request

REPO = "rehearsal/greeter"
HOST = "127.0.0.1:8765"
DEADLINE = float(os.environ.get("MELD_REHEARSAL_DEADLINE", "240"))


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    fake = FakeGitHub(REPO, tmp_path / "github" / "greeter.git", human="rehearsal", human_token="tok-human")
    url = fake.start()
    bin_dir = tmp_path / "bin"
    ghshim.install(bin_dir)
    standin.install(bin_dir)
    venv_bin = Path(sys.executable).parent
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), str(venv_bin), "/usr/bin", "/bin", "/usr/local/bin", "/opt/homebrew/bin"]))
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(f'[user]\n\tname = rehearsal\n\temail = rehearsal@example.com\n[init]\n\tdefaultBranch = main\n'
                         f'[url "{fake.bare}"]\n\tinsteadOf = https://github.com/{REPO}.git\n\tinsteadOf = https://github.com/{REPO}\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("MELD_REHEARSAL_GITHUB", url)
    monkeypatch.setenv("MELD_REHEARSAL_HUMAN_TOKEN", "tok-human")
    monkeypatch.setenv("MELD_REHEARSAL_PROPOSER", "claude")
    monkeypatch.setenv("MELD_REHEARSAL_WAIT_SECONDS", "2")
    monkeypatch.setenv("MELD_REHEARSAL_CAPACITY_MARK", str(tmp_path / "capacity-once"))   # the first worker launch dies of capacity (plan 50)
    for k in ("MELD_HOME", "MELD_GROUP", "MELD_ROLE", "MELD_WORKER", "GH_TOKEN", "GITHUB_TOKEN", "MELD_TOKEN_CLAUDE", "MELD_TOKEN_CODEX",
              "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "MELD_SLACK_DM_WEBHOOK"):
        monkeypatch.delenv(k, raising=False)
    product = tmp_path / "greeter"
    product.mkdir()
    yield fake, product, home
    _stop_everything(product / ".meld.toml", home / ".meld" / "products" / REPO)
    fake.stop()


def _stop_everything(config: Path, product_home: Path) -> None:
    runner = CliRunner()
    for g in ("claude", "codex"):
        if config.exists():
            runner.invoke(cli.main, ["--config", str(config), "--compact", "stop", g, "--grace", "2"])
    pids = []
    for rec in product_home.glob("state/*/supervisor.json"):
        pids.append(json.loads(rec.read_text()).get("pid"))
    for rec in product_home.glob("state/*/*/current.json"):
        pids.append(json.loads(rec.read_text()).get("pid"))
    for rec in product_home.glob("runs/*/*.json"):
        pids.append(json.loads(rec.read_text()).get("pid"))
    for pid in [p for p in pids if isinstance(p, int) and p > 0]:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass


def _request(v, method: str, path: str, payload: dict | None = None):
    target = path + ("&" if "?" in path else "?") + f"t={v.token}"
    headers = {"Host": HOST}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    return v.handle(Request.parse(method, target, headers, data))


def _json(resp) -> dict:
    assert resp.status < 300 and resp.body, (resp.status, resp.body[:500], getattr(resp, "headers", None))
    return json.loads(resp.body)


def _human(config: Path, *args: str) -> dict:
    out = CliRunner().invoke(cli.main, ["--config", str(config), "--compact", *args])
    assert out.exit_code == 0, out.output
    return json.loads(out.output)


def _comments(fake: FakeGitHub, issue: int) -> list[dict]:
    return [c for c in fake.comments.values() if c["issue"] == issue]


def test_the_wizard_the_supervisors_the_roles_and_the_workers_run_a_milestone_end_to_end(world):
    fake, product, home = world
    v, session = cli.setup_viewer(product)
    v.port = 8765                                               # what SERVE sets once it binds; the test speaks to the viewer directly
    session.base_url, session.token = f"http://{HOST}", v.token
    get = lambda path: _request(v, "GET", path)   # noqa: E731
    post = lambda path, payload: _request(v, "POST", path, payload)   # noqa: E731

    # The wizard's facts: the person, the two stand-in CLIs found and logged in, no repository yet
    facts = _json(get("/api/setup"))
    assert facts["mode"] == "setup" and facts["gh_login"] == "rehearsal" and facts["repo"] is None
    clis = {c["name"]: c for c in facts["clis"]}
    assert clis["claude"]["found"] and clis["claude"]["logged_in"] and clis["codex"]["found"] and clis["codex"]["logged_in"]

    # Step 1: the repository is created, the folder pushed, the board set up, the file written
    created = _json(post("/api/setup", {"step": "create", "repo": REPO, "goal": "A greeter that says hello", "milestone": "M1", "groups": ["claude", "codex"]}))
    assert "error" not in created, created
    c = created["created"]
    assert c["repository"]["created"] and c["groups"] == ["claude", "codex"] and c["setup_commit"]["pushed"], c
    assert fake.sha_of("main") and fake.items[1]["labels"] == ["meld:coordination"] and fake.milestones[1]["title"] == "M1"
    config = product / ".meld.toml"

    # Step 2: each AI's App, GitHub's part played by the stand-in: the manifest the page posts becomes a code, the code an App,
    # the install redirect checks and starts the group
    def quick_polling():
        # the rehearsal waits seconds, not minutes; every `meld` call reads the file fresh, so the roles see it at once
        text = config.read_text()
        text = re.sub(r"wait_timeout_seconds = \d+", "wait_timeout_seconds = 2", text)
        text = re.sub(r"interval_seconds = \d+", "interval_seconds = 1", text)
        config.write_text(text)

    for g in ("claude", "codex"):
        quick_polling()
        page = get(f"/setup/app/{g}/").body.decode()
        manifest = json.loads(htmlmod.unescape(re.search(r'name="manifest" value="([^"]*)"', page).group(1)))
        assert manifest["name"] == f"greeter-{g}"
        code = fake.manifest_code(manifest)
        flow = session.app_flow(g)
        assert get(f"/setup/app/{g}/callback?state={flow.state}&code={code}").status == 200
        inst = fake.install(flow.app["id"])
        page = get(f"/setup/app/{g}/installed?installation_id={inst}").body.decode()
        assert "Checked and started" in page, page
    quick_polling()
    assert "greeter-claude[bot]" in config.read_text() and "wait_timeout_seconds = 2" in config.read_text()

    # Step 3: with both groups started the next poll is the board (plan 45)
    assert _json(get("/api/setup"))["mode"] == "board"
    state = _json(get("/api/state"))
    assert set(state["processes"]) == {"claude", "codex"}

    # The groups plan; the person approves when the joint plan is in
    started = time.monotonic()
    approved = False
    while time.monotonic() - started < DEADLINE and not approved:
        st = _human(config, "status")
        legal = (st["coordination"]["agreements"].get("plan-1") or {}).get("legal") or []
        if "plan-approve" in legal and st["issues"]:                      # the proposer has turned the joint plan into issues
            out = _human(config, "plan", "approve")
            assert out["approved"] == "plan-1" and out["ready"], out
            approved = True
        else:
            time.sleep(1)
    assert approved, [c["body"].splitlines()[0] for c in _comments(fake, 1)]

    # The groups build, review and merge; the issue closes
    while time.monotonic() - started < DEADLINE:
        work = [it for n, it in fake.items.items() if n != 1 and not it.get("pull")]
        if work and all(it["state"] == "closed" for it in work):
            break
        time.sleep(2)
    work = [it for n, it in fake.items.items() if n != 1 and not it.get("pull")]
    headers = [c["body"].splitlines()[0] for c in sorted(_comments(fake, 1), key=lambda c: c["id"])]
    assert work and all(it["state"] == "closed" for it in work), (headers, [(it["number"], it["state"], it["labels"]) for it in work])
    pulls = [it for it in fake.items.values() if it.get("pull")]
    assert pulls and all(it["pull"]["merged_at"] for it in pulls), [(it["number"], it["state"]) for it in pulls]
    assert fake.git(["ls-tree", "--name-only", "main"]).splitlines() and any(n.endswith(".py") for n in fake.git(["ls-tree", "--name-only", "main"]).splitlines())
    # The first build died of "model at capacity" and was retried with the same tags: the issue was never blocked (plan 50)
    assert (home.parent / "capacity-once").exists()
    notes = [c["body"] for c in sorted(fake.comments.values(), key=lambda c: c["id"]) if c["issue"] == work[0]["number"]]
    assert any("stopped because the model was at capacity" in n for n in notes), notes
    assert not any(e["event"] == "labeled" and e["label"]["name"] == "meld:blocked" for e in work[0]["events"]), work[0]["events"]
    assert sum(1 for n in notes if n.startswith("Meld: `claude` dispatched") or n.startswith("Meld: `codex` dispatched")) == 2, notes
    # Nothing a stand-in did was refused, and nothing needed a person
    violations = [c["body"] for c in sorted(fake.comments.values(), key=lambda c: c["id"]) if c["issue"] == 1 and "PROTOCOL-VIOLATION" in c["body"].splitlines()[0]]
    assert not violations, violations
    assert not [h for h in headers if "NEEDS-HUMAN" in h], headers
    assert sum(1 for h in headers if h == "[claude] CONFIG") == 1 and sum(1 for h in headers if h == "[codex] CONFIG") == 1, headers
    assert any(h.startswith("[joint] PLAN plan-1") for h in headers) and any(h.startswith("[claude:pm] REVIEW-REQUEST") or h.startswith("[codex:pm] REVIEW-REQUEST") for h in headers)
