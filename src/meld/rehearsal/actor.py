"""The scripted AI of the rehearsal: a policy over `meld status` (plan 48).

An orchestrator, a PM and a worker that do what the board says is legal, in a fixed order, and nothing the board does
not list. That is the point: a stand-in that drives the whole protocol from `legal` alone proves the board tells a role
everything it needs, which is exactly what the first real run's codex orchestrator found it did not (plan 47). Every
`meld` call goes through the real CLI on PATH; the transcript the stand-in writes is the real adapter's format, so the
viewer, the limits and the log parsing see what they would see from the real CLI.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

Say = Callable[[str], None]          # narrate one line into the transcript
Tool = Callable[[list[str], str, int], None]   # record one command and its result

ROLE_BUDGET_SECONDS = float(os.environ.get("MELD_REHEARSAL_ROLE_SECONDS", "240"))
WAIT_SECONDS = os.environ.get("MELD_REHEARSAL_WAIT_SECONDS", "3")


class Meld:
    """`meld` as the role runs it: a subprocess per command, JSON back, refusals as refusals."""

    def __init__(self, cwd: Path, say: Say, tool: Tool):
        self.cwd, self.say, self.tool = cwd, say, tool

    def run(self, *args: str, check: bool = False) -> tuple[int, object]:
        proc = subprocess.run(["meld", "--compact", *args], cwd=str(self.cwd), capture_output=True, text=True)
        out = (proc.stdout or "").strip()
        self.tool(["meld", *args], out[:2000] + (("\n" + proc.stderr.strip()) if proc.returncode and proc.stderr else ""), proc.returncode)
        try:
            data = json.loads(out) if out else None
        except ValueError:
            data = out
        if check and proc.returncode != 0:
            raise RuntimeError(f"meld {' '.join(args)} exited {proc.returncode}: {out[:300]} {proc.stderr[:300]}")
        return proc.returncode, data

    def status(self) -> dict:
        code, data = self.run("status")
        if code != 0 or not isinstance(data, dict):
            raise RuntimeError(f"meld status failed: {data}")
        return data


# ---- the roles ----------------------------------------------------------------------------------------------------

def _plan_text(status: dict, model: str) -> str:
    body = (status.get("goal") or {}).get("body") or ""
    goal = next((ln.strip("# ").strip() for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("#")), "") or (status.get("goal") or {}).get("title") or "the goal"
    goal = goal[:60]
    slug = re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")[:30] or "work"
    return (f"One item does it.\n\n- id: {slug} | title: {goal} | worker: backend | model: {model} | effort: medium | size: S\n"
            f"  - a file `{slug}.py` exists on the default branch and its test passes\n\nverified-by: meld status")


def _my_model(meld: Meld, group: str, status: dict) -> str:
    code, data = meld.run("models")
    if code == 0 and isinstance(data, dict):
        for m in data.get("models") or []:
            if isinstance(m, dict) and m.get("id"):
                return str(m["id"])
    return os.environ.get("MELD_REHEARSAL_MODEL", "claude-sonnet-5")


def orchestrator(meld: Meld, *, launch_no: int, reason: str, clock=time.monotonic) -> str:
    group = os.environ.get("MELD_GROUP", "")
    status = meld.status()
    model = _my_model(meld, group, status)
    if launch_no == 1 and reason == "start":
        meld.run("post", "CONFIG", "-m", f"Model: {model}; effort: medium; rehearsal stand-in.")
        _, roster = meld.run("roster")
        meld.run("post", "ROSTER", "-m", json.dumps(roster) if roster else "[]")
    elif reason in ("crash", "recycle", "limit"):
        meld.run("post", "RESUMED", "-m", f"back after {reason}")
    started = clock()
    idle = 0
    while clock() - started < ROLE_BUDGET_SECONDS:
        status = meld.status()
        if status.get("halted"):
            return "halted"
        acted = _orchestrate_once(meld, status, group, model)
        if _milestone_done(status):
            meld.say("every issue is closed; this session is done")
            return "done"
        if acted:
            idle = 0
            continue
        idle += 1
        meld.run("wait", "--timeout", WAIT_SECONDS, "--filter", "comments,challenges,issue,phase,halted")
    return "budget"


def _milestone_done(status: dict) -> bool:
    issues = status.get("issues") or []
    return bool(issues) and all(i.get("status") == "closed" for i in issues)


def _orchestrate_once(meld: Meld, status: dict, group: str, model: str) -> bool:
    ref = f"plan-{status.get('milestone')}"
    agreements = (status.get("coordination") or {}).get("agreements") or {}
    if status.get("phase") == "planning":
        ag = agreements.get(ref)
        legal = list(ag.get("legal") or []) if ag else ["propose"]
        proposer = os.environ.get("MELD_REHEARSAL_PROPOSER")
        if "propose" in legal and (ag is None or not ag.get("summary")) and (not proposer or proposer == group):
            meld.say(f"proposing {ref}")
            code, _ = meld.run("post", "PROPOSE", ref, "-m", _plan_text(status, model))
            return code == 0
        if "agree" in legal:
            meld.say(f"agreeing to {ref}")
            code, _ = meld.run("post", "AGREE", ref, "-m", "The proposal is right as it stands.")
            return code == 0
        if "joint" in legal:
            meld.say(f"posting the joint plan for {ref}")
            code, _ = meld.run("post", "PLAN", ref, "--joint", "-m", "verified-by: meld status (agreed)")
            return code == 0
        if "plan-issues" in legal:
            code, _ = meld.run("plan", "issues", ref)
            return code == 0
        return False
    # executing: the PM claims (claim is the PM's verb); the orchestrator tags what its group holds so the PM can dispatch it,
    # with this group's own model when the plan named another group's
    models = _my_models(meld)
    for issue in status.get("issues") or []:
        legal = issue.get("legal") or []
        if issue.get("claimed_by") != group or "tag" not in legal or issue.get("status") != "in_progress":
            continue
        if any(r.get("issue") == issue["number"] and r.get("state") == "running" for r in status.get("runs") or []):
            continue
        missing = not (issue.get("model") and issue.get("effort") and issue.get("size"))
        foreign = issue.get("model") not in models if models else False
        if missing or foreign:
            meld.say(f"tagging #{issue['number']} for {group}")
            meld.run("tag", str(issue["number"]), "--worker", issue.get("worker") or "backend", "--model", model, "--effort", issue.get("effort") or "medium", "--size", issue.get("size") or "S")
            return True
    return False


def _my_models(meld: Meld) -> set[str]:
    code, data = meld.run("models")
    return {str(m["id"]) for m in (data.get("models") or [])} if code == 0 and isinstance(data, dict) else set()


def pm(meld: Meld, *, launch_no: int, reason: str, clock=time.monotonic) -> str:
    group = os.environ.get("MELD_GROUP", "")
    if reason in ("crash", "recycle", "limit"):
        meld.run("post", "RESUMED", "-m", f"back after {reason}")
    started = clock()
    code, data = meld.run("models")
    models = {str(m["id"]) for m in (data.get("models") or [])} if code == 0 and isinstance(data, dict) else set()
    while clock() - started < ROLE_BUDGET_SECONDS:
        meld.run("tick")
        status = meld.status()
        if status.get("halted"):
            return "halted"
        if _milestone_done(status) and not _unfinished_runs(status, group):
            meld.say("the milestone is complete; this session is done")
            return "done"
        if _manage_once(meld, status, group, models):
            continue
        meld.run("wait", "--timeout", WAIT_SECONDS, "--filter", "comments,issue,pr,run,phase,halted")
    return "budget"


def _unfinished_runs(status: dict, group: str) -> list[dict]:
    return [r for r in status.get("runs") or [] if r.get("group") == group and r.get("state") == "finished"]


def _manage_once(meld: Meld, status: dict, group: str, models: set[str] | None = None) -> bool:
    for issue in status.get("issues") or []:
        legal = issue.get("legal") or []
        if "claim" in legal and issue.get("status") == "ready" and not issue.get("claimed_by"):
            meld.say(f"claiming #{issue['number']}")
            meld.run("claim", str(issue["number"]))
            return True
        if "collect" in legal:
            meld.say(f"collecting #{issue['number']}")
            meld.run("collect", str(issue["number"]))
            return True
        if "dispatch" in legal and issue.get("claimed_by") == group and (not models or issue.get("model") in models):
            meld.say(f"dispatching a build on #{issue['number']}")
            meld.run("dispatch", str(issue["number"]))
            return True
    for pr in status.get("pulls") or []:
        legal = pr.get("legal") or []
        n = str(pr["number"])
        run = next((r for r in status.get("runs") or [] if r.get("group") == group and r.get("pr") == pr["number"]), None)
        if run is not None and run.get("state") == "finished":
            meld.say(f"collecting the {run.get('kind')} of PR #{n}")
            meld.run("collect", n)
            return True
        if run is not None and run.get("state") == "collected" and run.get("kind") == "review" and ("approve" in legal or "request-changes" in legal):
            meld.say(f"posting the review of PR #{n}")
            meld.run("review", n)
            return True
        if run is not None and run.get("state") == "collected" and run.get("kind") == "audit":
            code, out = meld.run("audit", n)
            if code == 0:
                return True
        if "merge" in legal:
            meld.say(f"merging PR #{n}")
            meld.run("merge", n)
            return True
        if "review" in legal and run is None:
            meld.say(f"dispatching a review of PR #{n}")
            meld.run("dispatch", n, "--kind", "review")
            return True
        if "audit" in legal and (run is None or run.get("kind") != "audit"):
            meld.say(f"dispatching an audit of PR #{n}")
            meld.run("dispatch", n, "--kind", "audit")
            return True
    return False


# ---- the worker -------------------------------------------------------------------------------------------------------

def worker(cwd: Path, say: Say, tool: Tool) -> str:
    """A build writes one file and one test and commits; a reviewer approves; an auditor passes. The result file is the
    contract every real worker signs (`prompts/_worker_contract.md`)."""
    brief = (cwd / ".meld-brief.md").read_text() if (cwd / ".meld-brief.md").exists() else ""
    first = brief.splitlines()[0] if brief else ""
    m = re.search(r"for (PR )?#(\d+)", first)
    number = m.group(2) if m else "0"
    is_pr = bool(m and m.group(1))
    kind = ("audit" if "auditor" in first or "merge commit" in brief[:2000] else "review") if is_pr else "build"
    if kind == "build":
        slug = re.search(r"\*\*#\d+: ([^*]+)\*\*", brief)
        name = re.sub(r"[^a-z0-9]+", "-", (slug.group(1) if slug else "work").lower()).strip("-")[:30] or "work"
        module = name.replace("-", "_")
        (cwd / f"{module}.py").write_text(f'"""{name}: made by the rehearsal\'s stand-in worker for #{number}."""\n\n\ndef answer() -> str:\n    return "{name}"\n')
        (cwd / f"test_{module}.py").write_text(f"from {module} import answer\n\n\ndef test_answer():\n    assert answer() == \"{name}\"\n")
        say(f"wrote {module}.py and its test")
        _git(cwd, tool, "add", f"{module}.py", f"test_{module}.py")
        _git(cwd, tool, "commit", "-q", "-m", f"feat: {name} (#{number})")
        proc = subprocess.run(["python3", "-c", f"import {module}; assert {module}.answer() == '{name}'"], cwd=str(cwd), capture_output=True, text=True)
        tool(["python3", "-c", "..."], proc.stdout + proc.stderr, proc.returncode)
        result = {"outcome": "done", "summary": f"Added {module}.py with answer() and test_{module}.py; the test passes.",
                  "verified_by": f"python3 -c 'import {module}' -> exit {proc.returncode}", "verdict": None, "comments": [], "needs_human": None, "skills_used": []}
    elif kind == "review":
        say(f"reviewing PR #{number}: the change matches its issue and the test passes")
        result = {"outcome": "done", "summary": "The change does what the issue asks and its test passes; nothing to request.",
                  "verified_by": "read the diff; python3 -m pytest -q -> passed", "verdict": "approve", "comments": [], "needs_human": None, "skills_used": []}
    else:
        say(f"auditing PR #{number} at the merge commit: the test still passes")
        result = {"outcome": "done", "summary": "The merged code matches what was reviewed and its test passes.",
                  "verified_by": "python3 -m pytest -q at the merge commit -> passed", "verdict": "pass", "comments": [], "needs_human": None, "skills_used": []}
    (cwd / ".meld-result.json").write_text(json.dumps(result, indent=2))
    tool(["write", ".meld-result.json"], json.dumps(result)[:300], 0)
    return "done"


def _git(cwd: Path, tool: Tool, *args: str) -> None:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    tool(["git", *args], (proc.stdout + proc.stderr).strip(), proc.returncode)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
