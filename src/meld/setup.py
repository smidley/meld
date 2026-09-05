"""meld setup: bootstrap a product repository -- labels, milestone, coordination issue, a starter .meld.toml.

Idempotent: every GitHub object that already exists is reused, and the config file is never overwritten without
--force. Runs as the human (gh's own login), before any .meld.toml exists, so it does not go through load_context.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from meld.adapters import ADAPTER_NAMES, get_adapter
from meld.atomic import write_atomic
from meld.board import CHALLENGED_LABEL, COORDINATION_LABEL, NUDGED_LABEL, PLAN_APPROVED_LABEL, STATUS_LABELS
from meld.brief import DEFAULT_STANDARDS
from meld.github import GitHub, GitHubError
from meld.gitops import git, is_dirty
from meld.models import ModelRegistry, ModelSpec


class SetupError(RuntimeError):
    pass


# name -> (color, description). Colors are GitHub's 6-hex without '#'.
LABELS: dict[str, tuple[str, str]] = {
    COORDINATION_LABEL: ("5319e7", "The milestone's coordination issue: orchestrators talk here"),
    PLAN_APPROVED_LABEL: ("0e8a16", "The human approved the plan; the milestone is executing"),
    "meld:planned": ("c2e0c6", "Created from the agreed plan; waits for plan approval"),
    "meld:ready": ("0e8a16", "Ready to be claimed by a group"),
    "meld:in-progress": ("fbca04", "Claimed; a worker is building it"),
    "meld:in-review": ("1d76db", "A PR is open and under cross-review"),
    "meld:blocked": ("b60205", "Blocked: a worker failed, or reviews reached the ping-pong cap"),
    "meld:needs-human": ("d93f0b", "A person must do something before work continues"),
    CHALLENGED_LABEL: ("e99695", "A CHALLENGE is open against this item"),
    NUDGED_LABEL: ("ededed", "The human was nudged about this needs-human item"),
}
assert set(STATUS_LABELS) <= set(LABELS)

_EFFORT_PREFERENCE = {"orchestrator": ("high", "xhigh", "medium"), "pm": ("medium", "high", "low")}


def installed_adapters(which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """Adapter names whose CLI binary is on PATH."""
    return [name for name in ADAPTER_NAMES if which(get_adapter(name).binary)]


def ensure_labels(gh: GitHub) -> dict:
    existing = {label["name"] for label in gh.list_labels()}
    created = []
    for name, (color, description) in LABELS.items():
        if name in existing:
            continue
        gh.create_label(name, color, description)
        created.append(name)
    return {"created": sorted(created), "existing": sorted(existing & set(LABELS))}


def ensure_milestone(gh: GitHub, title: str) -> tuple[int, str]:
    """Reuses a same-titled milestone regardless of state: GitHub rejects a second milestone with a title that
    already exists (even a closed one), so a closed match is reopened rather than shadowed by a failed create."""
    for m in gh.list_milestones(state="all"):
        if m.get("title") == title:
            number = int(m["number"])
            if m.get("state") == "open":
                return number, "existing"
            gh.update_milestone(number, state="open")
            return number, "reopened"
    created = gh.create_milestone(title)
    return int(created["number"]), "created"


def ensure_coordination_issue(gh: GitHub, milestone: int, milestone_title: str, goal: str) -> tuple[int, str]:
    for issue in gh.list_issues(milestone):
        if COORDINATION_LABEL in {lab.get("name") for lab in issue.get("labels", [])}:
            return int(issue["number"]), "existing"
    body = (f"# Coordination: {milestone_title}\n\n## Goal\n\n{goal.strip()}\n\n"
            "Orchestrators post here in the `[party] VERB` grammar; humans speak as `[human]`. "
            "Approve the plan by adding the `meld:plan-approved` label to this issue.\n")
    created = gh.create_issue(f"Coordination: {milestone_title}", body, [COORDINATION_LABEL], milestone)
    return int(created["number"]), "created"


def _pick(specs: list[ModelSpec], tier: str) -> ModelSpec | None:
    for spec in specs:
        if spec.tier == tier:
            return spec
    return specs[0] if specs else None


def _effort(spec: ModelSpec, role: str) -> str:
    for candidate in _EFFORT_PREFERENCE[role]:
        if candidate in spec.efforts:
            return candidate
    return spec.efforts[-1]


def _group_block(name: str, registry: ModelRegistry, commented: bool) -> tuple[str, bool]:
    """Returns the rendered block plus whether it ended up commented -- the single source of truth for "does this
    adapter have registry models", so callers (setup's groups/warnings) derive it from here instead of re-querying
    the registry themselves."""
    specs = registry.for_adapter(name)
    commented = commented or not specs  # a CLI with no models in the registry is as unusable as no CLI at all
    orch = _pick(specs, "large")
    pm = _pick(specs, "medium") or orch
    prefix = "# " if commented else ""
    lines = [f"{prefix}[groups.{name}]", f'{prefix}cli = "{name}"', f'{prefix}bot = "meld-{name}-bot"',
             f'{prefix}token_env = "MELD_TOKEN_{name.upper()}"', f"{prefix}concurrency = 8", f"{prefix}review_concurrency = 4"]
    for role, spec in (("orchestrator", orch), ("pm", pm)):
        model = spec.id if spec else "<model id from meld models>"
        effort = _effort(spec, role) if spec else "medium"
        lines += [f"{prefix}[groups.{name}.{role}]", f'{prefix}model = "{model}"', f'{prefix}effort = "{effort}"']
    return "\n".join(lines) + "\n", commented


def starter_toml(repo: str, default_branch: str, human: str, installed: list[str], registry: ModelRegistry) -> str:
    parts = [f'[repo]\nname = "{repo}"\ndefault_branch = "{default_branch}"\nprivate = true\n',
             f'[humans]\naccounts = ["{human}"]\n']
    for name in ADAPTER_NAMES:
        text, _ = _group_block(name, registry, commented=name not in installed)
        parts.append(text)
    parts.append('[review]\nquorum = 2\npingpong_cap = 3\nsecurity_paths = ["auth/**", "**/secrets*", "infra/**"]\n')
    parts.append('[notify]\nslack_dm_webhook_env = "MELD_SLACK_DM_WEBHOOK"\nrepeat_after_minutes = 120\n')
    return "\n".join(parts)


def setup(gh: GitHub, *, repo: str, goal: str, milestone_title: str, human: str, target_dir: Path, registry: ModelRegistry,
          allow_public: bool = False, which: Callable[[str], str | None] = shutil.which, force: bool = False,
          protect: bool = True, pr: bool = False, run=subprocess.run) -> dict:
    info = gh.get_repo()
    if not info.get("private", False) and not allow_public:
        raise SetupError(f"{repo} is public; meld setup refuses without --allow-public "
                         "(anyone who can comment could try to speak in the grammar; only configured bots and listed humans are heard, "
                         "but every comment is still read)")
    warnings: list[str] = []
    if not info.get("private", False):
        warnings.append(f"{repo} is public: comments from unknown accounts are ignored but visible to every worker's brief")
    config_path = target_dir / ".meld.toml"
    if config_path.exists() and not force:
        raise SetupError(f"{config_path} exists; pass --force to overwrite it")
    labels = ensure_labels(gh)
    milestone, milestone_how = ensure_milestone(gh, milestone_title)
    coordination, issue_how = ensure_coordination_issue(gh, milestone, milestone_title, goal)
    installed = installed_adapters(which)
    if not installed:
        warnings.append("no CLI found on PATH for any adapter (claude, codex); every [groups.*] block is commented out -- install a CLI and uncomment its block")
    groups = []
    for name in installed:
        _, commented = _group_block(name, registry, commented=False)
        if commented:
            warnings.append(f"{name} is installed but models.toml lists no models for it; its [groups.{name}] block is commented out")
        else:
            groups.append(name)
    target_dir.mkdir(parents=True, exist_ok=True)
    write_atomic(config_path, starter_toml(repo, info.get("default_branch", "main"), human, installed, registry))
    files = repo_files(target_dir, human) + [".meld.toml"]
    ci = ci_paths_warnings(target_dir)
    warnings.extend(ci)
    protection = protect_branch(gh, info.get("default_branch", "main")) if protect else {"protected": False, "skipped": True}
    pr_info = open_setup_pr(gh, target_dir, info.get("default_branch", "main"), files, run) if pr else None
    return {"repo": repo, "labels": labels, "milestone": milestone, "milestone_how": milestone_how,
            "coordination_issue": coordination, "coordination_how": issue_how, "config": str(config_path), "config_written": True,
            "groups": groups, "warnings": warnings, "files": files, "ci_warnings": ci, "protection": protection, "pr": pr_info,
            "next": [f"create one GitHub machine account per group named in {config_path.name} and add each as a collaborator on {repo}",
                     "mint one fine-grained token per bot (Contents, Issues, Pull requests, Commit statuses: read and write) and export MELD_TOKEN_<GROUP>",
                     "run `meld doctor` to verify tokens, CLIs, and models", "run `meld run <group>` for each group, then `meld chat`"]}


_CODEOWNERS_LINE = "/.meld/workers/ @{human}"


def repo_files(target_dir: Path, human: str) -> list[str]:
    """Write the repo-side files the framework relies on; return the relative paths written or extended (idempotent)."""
    written: list[str] = []
    codeowners = target_dir / ".github" / "CODEOWNERS"
    line = _CODEOWNERS_LINE.format(human=human)
    existing = codeowners.read_text() if codeowners.exists() else ""
    if line not in existing.splitlines():
        codeowners.parent.mkdir(parents=True, exist_ok=True)
        codeowners.write_text((existing if existing.endswith("\n") or not existing else existing + "\n") + line + "\n")
        written.append(".github/CODEOWNERS")
    standards = target_dir / ".meld" / "STANDARDS.md"
    if not standards.exists():
        standards.parent.mkdir(parents=True, exist_ok=True)
        standards.write_text("# Product standards\n\n" + DEFAULT_STANDARDS.strip() + "\n")
        written.append(".meld/STANDARDS.md")
    return written


_PATHS_RE = re.compile(r"^(\s*)(paths|paths-ignore):\s*$", re.MULTILINE)


def ci_paths_warnings(target_dir: Path) -> list[str]:
    """Workflows that can never run on a memory PR (which touches only .meld/): a `paths:` allow-list that never
    mentions .meld/**, or a `paths-ignore:` deny-list that does. If such a check is ever made required, decision and
    lesson PRs become unmergeable (spec 15)."""
    out: list[str] = []
    for wf in sorted((target_dir / ".github" / "workflows").glob("*.y*ml")):
        text = wf.read_text()
        for m in _PATHS_RE.finditer(text):
            indent = len(m.group(1))
            key = m.group(2)
            items: list[str] = []
            for line in text[m.end():].splitlines()[1:]:
                stripped = line.strip()
                if not stripped:
                    continue
                if len(line) - len(line.lstrip()) <= indent:
                    break
                if stripped.startswith("- "):
                    items.append(stripped[2:].strip("'\""))
            if not items:
                continue
            mentions_meld = any(item == "**" or item.startswith(".meld") for item in items)
            if key == "paths" and not mentions_meld:
                out.append(f".github/workflows/{wf.name}: its `paths:` filter excludes .meld/**, so memory PRs trigger no run from it; "
                           "do not make its checks required, or add '.meld/**' to the filter")
                break
            if key == "paths-ignore" and mentions_meld:
                out.append(f".github/workflows/{wf.name}: its `paths-ignore:` filter excludes .meld/**, so memory PRs trigger no run from it; "
                           "do not make its checks required, or remove '.meld/**' from the filter")
                break
    return out


def protect_branch(gh: GitHub, branch: str) -> dict:
    """Require one approving review; require no status checks (the meld/review/<group> statuses stay informational, spec 21 item 4)."""
    body = {"required_status_checks": None, "enforce_admins": False,
            "required_pull_request_reviews": {"required_approving_review_count": 1}, "restrictions": None}
    try:
        gh.put(f"repos/{gh.repo}/branches/{branch}/protection", body)
    except GitHubError as e:
        return {"protected": False, "branch": branch, "error": f"branch protection not applied ({e.status}): {e}"}
    return {"protected": True, "branch": branch,
            "note": "meld/review/<group> statuses are informational; add them to required checks only if you want them blocking"}


def open_setup_pr(gh: GitHub, target_dir: Path, default_branch: str, files: list[str], run=subprocess.run) -> dict:
    """Commit `files` on branch meld/setup with the human's own git identity and credentials, push, open the PR.

    Re-runnable: meld/setup is meld's own branch, regenerated from the same files each run. A rerun with nothing
    new to commit (files unchanged since the last run) skips the commit -- `git commit` would otherwise fail with
    "nothing to commit" -- and the push force-updates the branch, since a regenerated commit does not share history
    with whatever it replaces. If GitHub already has an open PR for the branch, create_pull's 422 is caught and
    that PR is looked up and reused instead of erroring."""
    branch = "meld/setup"
    git(["checkout", "-q", "-B", branch], target_dir, run)
    git(["add", "--", *files], target_dir, run)
    if is_dirty(target_dir, run):
        git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "chore: meld setup (labels, coordination issue, starter config)"], target_dir, run)
    git(["push", "-q", "--force", "--set-upstream", "origin", f"HEAD:refs/heads/{branch}"], target_dir, run)
    body = "Files written by `meld setup`:\n\n" + "\n".join(f"- `{f}`" for f in files) + \
           "\n\nReview the starter `.meld.toml` (bot names, token env vars, models) before merging; `meld doctor` verifies it."
    try:
        pr = gh.create_pull("chore: meld setup", branch, default_branch, body)
    except GitHubError as e:
        if e.status != 422:
            raise
        existing = gh.get(f"repos/{gh.repo}/pulls", {"head": f"{gh.repo.split('/')[0]}:{branch}", "state": "open"}, use_cache=False)
        found = existing[0]
        return {"pr": int(found["number"]), "url": str(found.get("html_url", "")), "branch": branch, "existing": True}
    return {"pr": int(pr["number"]), "url": str(pr.get("html_url", "")), "branch": branch}
