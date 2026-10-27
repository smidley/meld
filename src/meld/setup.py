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
from meld.board import CHALLENGED_LABEL, COORDINATION_LABEL, HELD_CATEGORIES, NUDGED_LABEL, PLAN_APPROVED_LABEL, STATUS_LABELS, held_label
from meld.brief import DEFAULT_STANDARDS
from meld.github import GitHub, GitHubError
from meld.gitops import git
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
    **{held_label(c): ("f9d0c4", f"Held: {c} — a listed human must answer RESOLVED before dispatch") for c in HELD_CATEGORIES},
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


def ensure_coordination_issue(gh: GitHub, milestone: int, milestone_title: str, goal: str, context: str = "") -> tuple[int, str]:
    for issue in gh.list_issues(milestone):
        if COORDINATION_LABEL in {lab.get("name") for lab in issue.get("labels", [])}:
            return int(issue["number"]), "existing"
    body = (f"# Coordination: {milestone_title}\n\n## Goal\n\n{goal.strip()}\n\n"
            + (f"## Context\n\n{context.strip()}\n\n" if context.strip() else "")
            + "Orchestrators post here in the `[party] VERB` grammar; humans speak as `[human]`. "
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


def _group_block(name: str, registry: ModelRegistry, commented: bool, app: bool = False) -> tuple[str, bool]:
    """Returns the rendered block plus whether it ended up commented -- the single source of truth for "does this
    adapter have registry models", so callers (setup's groups/warnings) derive it from here instead of re-querying
    the registry themselves. `app` selects the App-form block (auth = "app", app_id/installation_id/private_key_env)
    instead of the PAT form (bot/token_env)."""
    specs = registry.for_adapter(name)
    commented = commented or not specs  # a CLI with no models in the registry is as unusable as no CLI at all
    orch = _pick(specs, "large")
    pm = _pick(specs, "medium") or orch
    prefix = "# " if commented else ""
    if app:
        lines = [f"{prefix}[groups.{name}]", f'{prefix}cli = "{name}"', f'{prefix}bot = "meld-{name}[bot]"',
                 f'{prefix}auth = "app"', f"{prefix}app_id = 0", f"{prefix}installation_id = 0",
                 f'{prefix}private_key_env = "MELD_APP_KEY_{name.upper()}"', f"{prefix}concurrency = 8", f"{prefix}review_concurrency = 4"]
    else:
        lines = [f"{prefix}[groups.{name}]", f'{prefix}cli = "{name}"', f'{prefix}bot = "meld-{name}-bot"',
                 f'{prefix}token_env = "MELD_TOKEN_{name.upper()}"', f"{prefix}concurrency = 8", f"{prefix}review_concurrency = 4"]
    for role, spec in (("orchestrator", orch), ("pm", pm)):
        model = spec.id if spec else "<model id from meld models>"
        effort = _effort(spec, role) if spec else "medium"
        lines += [f"{prefix}[groups.{name}.{role}]", f'{prefix}model = "{model}"', f'{prefix}effort = "{effort}"']
    if not app:
        lines += ["# To run this group as a GitHub App instead of a machine user (no extra GitHub account), replace bot/token_env with:",
                  f'# bot = "meld-{name}[bot]"', '# auth = "app"', "# app_id = 0", "# installation_id = 0", f'# private_key_env = "MELD_APP_KEY_{name.upper()}"']
    return "\n".join(lines) + "\n", commented


def starter_toml(repo: str, default_branch: str, human: str, installed: list[str], registry: ModelRegistry, app: bool = False) -> str:
    parts = [f'[repo]\nname = "{repo}"\ndefault_branch = "{default_branch}"\nprivate = true\n',
             f'[humans]\naccounts = ["{human}"]\n']
    for name in ADAPTER_NAMES:
        text, _ = _group_block(name, registry, commented=name not in installed, app=app)
        parts.append(text)
    parts.append('[review]\nquorum = 2\npingpong_cap = 3\nsecurity_paths = ["auth/**", "**/secrets*", "infra/**"]\n')
    parts.append('[notify]\nslack_dm_webhook_env = "MELD_SLACK_DM_WEBHOOK"\nrepeat_after_minutes = 120\n')
    return "\n".join(parts)


def setup(gh: GitHub, *, repo: str, goal: str, milestone_title: str, human: str, target_dir: Path, registry: ModelRegistry,
          allow_public: bool = False, which: Callable[[str], str | None] = shutil.which, force: bool = False,
          protect: bool = True, pr: bool = False, run=subprocess.run, app: bool = False, context: str = "") -> dict:
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
    coordination, issue_how = ensure_coordination_issue(gh, milestone, milestone_title, goal, context)
    installed = installed_adapters(which)
    if not installed:
        warnings.append(f"no CLI found on PATH for any adapter ({', '.join(ADAPTER_NAMES)}); every [groups.*] block is commented out -- install a CLI and uncomment its block")
    groups = []
    for name in installed:
        _, commented = _group_block(name, registry, commented=False, app=app)
        if commented:
            warnings.append(f"{name} is installed but models.toml lists no models for it; its [groups.{name}] block is commented out")
        else:
            groups.append(name)
    default_branch = info.get("default_branch", "main")
    target_dir.mkdir(parents=True, exist_ok=True)
    if pr:
        # meld/setup must branch from the remote default branch, not from whatever the human happens to have
        # checked out -- a WIP feature branch's local-only history must never ride into the setup PR. This has to
        # run before any setup file is written below: switching branches after writing would risk the checkout
        # overwriting or conflicting with those just-written files.
        _checkout_setup_branch(target_dir, default_branch, run)
    write_atomic(config_path, starter_toml(repo, default_branch, human, installed, registry, app=app))
    files = repo_files(target_dir, human, context) + [".meld.toml"]
    ci = ci_paths_warnings(target_dir)
    warnings.extend(ci)
    protection = protect_branch(gh, default_branch) if protect else {"protected": False, "skipped": True}
    pr_info = open_setup_pr(gh, target_dir, default_branch, files, run) if pr else None
    if app:
        next_steps = [
            "for each group, create a GitHub App at https://github.com/settings/apps/new named meld-<group> -- App names are global on GitHub, "
            "so if meld-<group> is taken pick another; `meld doctor` prints the resulting bot login. Homepage any URL, Webhook OFF, "
            "Repository permissions: Contents, Issues, Pull requests, Commit statuses (read & write), Checks (read & write, recommended -- "
            "without it each review records check_run_error); Where can it be installed: only this account",
            "generate a private key on the App page and store the .pem in 1Password; note the App ID",
            f"install the App on {repo} (Install App → only select repositories); the installation_id is the number at the end of the installation URL",
            f"fill app_id and installation_id in {config_path.name} and export MELD_APP_KEY_<GROUP> with the PEM contents (op run injects it); "
            f"or set private_key_file = \"<path>\" in {config_path.name} instead of private_key_env",
            "run `meld doctor` to verify: it prints the App's bot login to put in [groups.<g>].bot if it differs",
            "run `meld run <group>` for each group, then `meld chat`",
        ]
    else:
        next_steps = [f"create one GitHub machine account per group named in {config_path.name} and add each as a collaborator on {repo}",
                      "mint one fine-grained token per bot (Contents, Issues, Pull requests, Commit statuses: read and write) and export MELD_TOKEN_<GROUP>",
                      "run `meld doctor` to verify tokens, CLIs, and models", "run `meld run <group>` for each group, then `meld chat`"]
    return {"repo": repo, "labels": labels, "milestone": milestone, "milestone_how": milestone_how,
            "coordination_issue": coordination, "coordination_how": issue_how, "config": str(config_path), "config_written": True,
            "groups": groups, "warnings": warnings, "files": files, "ci_warnings": ci, "protection": protection, "pr": pr_info,
            "next": next_steps}


_CODEOWNERS_LINE = "/.meld/workers/ @{human}"


def repo_files(target_dir: Path, human: str, context: str = "") -> list[str]:
    """Write the repo-side files the framework relies on; return the relative paths written or extended (idempotent).
    `context` (plan 35) becomes `.meld/CONTEXT.md` when given and the file does not exist; an existing one is the product's
    and is left alone."""
    written: list[str] = []
    if context.strip():
        ctx_file = target_dir / ".meld" / "CONTEXT.md"
        if not ctx_file.exists():
            ctx_file.parent.mkdir(parents=True, exist_ok=True)
            ctx_file.write_text("# Product context\n\n" + context.strip() + "\n")
            written.append(".meld/CONTEXT.md")
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


_ON_RE = re.compile(r"^on:\s*$", re.MULTILINE)
_TRIGGER_RE = re.compile(r"^(\s+)(push|pull_request):\s*$", re.MULTILINE)
_PATHS_RE = re.compile(r"^(\s*)(paths|paths-ignore):\s*(?:\[(.*?)\])?\s*$", re.MULTILINE)


def _indented_block(text: str, start: int, indent: int) -> str:
    """The lines after `start` (the position right after a `key:` line, before its own newline) that belong to that
    key's nested block: every line more indented than `indent`, skipping blank lines, until the first non-blank
    line at indent <= `indent` (or end of text). Used to bound a YAML mapping/sequence without a real YAML parser."""
    out = []
    for line in text[start:].splitlines(keepends=True)[1:]:
        if not line.strip():
            out.append(line)
            continue
        if len(line) - len(line.lstrip()) <= indent:
            break
        out.append(line)
    return "".join(out)


def _list_items(block: str) -> list[str]:
    """`- item` lines in a block-style YAML sequence, quotes stripped."""
    items = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            items.append(stripped[2:].strip("'\""))
    return items


def _flow_items(raw: str) -> list[str]:
    """Comma-separated items from inside a flow-style `[a, b]` list, quotes stripped."""
    return [p.strip().strip("'\"") for p in raw.split(",") if p.strip()]


def ci_paths_warnings(target_dir: Path) -> list[str]:
    """Workflows that can never run on a memory PR (which touches only .meld/), scoped to the `on:` mapping's
    `pull_request` trigger only -- never `jobs:` (so a job step's own `with: paths: ...` input is never mistaken for
    a trigger filter) and never `push:` (a push-only filter doesn't gate PR runs, so it doesn't affect memory PRs
    even when it excludes .meld/**; `push` and `pull_request` are inspected independently). A `paths:` allow-list
    that never mentions .meld/**, or a `paths-ignore:` deny-list that does (block- or flow-style), both mean the
    workflow never triggers on a memory PR; if such a check is ever made required, decision and lesson PRs become
    unmergeable (spec 15)."""
    out: list[str] = []
    for wf in sorted((target_dir / ".github" / "workflows").glob("*.y*ml")):
        text = wf.read_text()
        on_match = _ON_RE.search(text)
        if not on_match:
            continue
        on_block = _indented_block(text, on_match.end(), indent=0)
        for trig in _TRIGGER_RE.finditer(on_block):
            if trig.group(2) != "pull_request":
                continue  # a push-only filter doesn't gate PR runs
            trig_block = _indented_block(on_block, trig.end(), len(trig.group(1)))
            m = _PATHS_RE.search(trig_block)
            if not m:
                continue
            key = m.group(2)
            if m.group(3) is not None:
                items = _flow_items(m.group(3))
            else:
                items = _list_items(_indented_block(trig_block, m.end(), len(m.group(1))))
            if not items:
                continue
            mentions_meld = any(item == "**" or item.startswith(".meld") for item in items)
            if key == "paths" and not mentions_meld:
                out.append(f".github/workflows/{wf.name}: its `pull_request` `paths:` filter excludes .meld/**, so memory PRs trigger no run from it; "
                           "do not make its checks required, or add '.meld/**' to the filter")
                break
            if key == "paths-ignore" and mentions_meld:
                out.append(f".github/workflows/{wf.name}: its `pull_request` `paths-ignore:` filter excludes .meld/**, so memory PRs trigger no run from it; "
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


_SETUP_BRANCH = "meld/setup"


def _checkout_setup_branch(target_dir: Path, default_branch: str, run=subprocess.run) -> None:
    """Branch meld/setup from the remote default branch, not from whatever the human happens to have checked out --
    so a WIP feature branch's local-only history never rides into the setup PR. Must be called before any setup
    file is written to target_dir (repo_files, the .meld.toml write)."""
    git(["fetch", "-q", "origin", default_branch], target_dir, run)
    git(["checkout", "-q", "-B", _SETUP_BRANCH, f"origin/{default_branch}"], target_dir, run)


def open_setup_pr(gh: GitHub, target_dir: Path, default_branch: str, files: list[str], run=subprocess.run) -> dict:
    """Commit `files` on branch meld/setup with the human's own git identity and credentials, push, open the PR.

    Expects meld/setup already checked out (via _checkout_setup_branch, branched from the remote default branch) --
    this function never itself changes branch, so it never risks branching from whatever the human had checked out.

    Commits and checks dirtiness scoped to `files` only: an unrelated change already staged in the index (the
    human's own WIP) is never swept into the setup commit, and is left staged exactly as it was.

    Re-runnable: meld/setup is meld's own branch, regenerated from the same files each run. A rerun with nothing
    new to commit (files unchanged since the last run) skips the commit -- `git commit` would otherwise fail with
    "nothing to commit" -- and the push force-updates the branch, since a regenerated commit does not share history
    with whatever it replaces. If GitHub already has an open PR for the branch, create_pull's 422 is caught and
    that PR is looked up and reused instead of erroring; a 422 whose lookup finds no open PR is re-raised, since
    422 can mean other things (e.g. no diff between branches)."""
    current = git(["branch", "--show-current"], target_dir, run)
    assert current == _SETUP_BRANCH, f"open_setup_pr expects {_SETUP_BRANCH} checked out (got {current!r}); call _checkout_setup_branch first"
    git(["add", "--", *files], target_dir, run)
    if git(["status", "--porcelain", "--", *files], target_dir, run):
        git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "chore: meld setup (labels, coordination issue, starter config)", "--", *files],
            target_dir, run)
    git(["push", "-q", "--force", "--set-upstream", "origin", f"HEAD:refs/heads/{_SETUP_BRANCH}"], target_dir, run)
    body = "Files written by `meld setup`:\n\n" + "\n".join(f"- `{f}`" for f in files) + \
           "\n\nReview the starter `.meld.toml` (bot names, token env vars, models) before merging; `meld doctor` verifies it."
    try:
        pr = gh.create_pull("chore: meld setup", _SETUP_BRANCH, default_branch, body)
    except GitHubError as e:
        if e.status != 422:
            raise
        existing = gh.get(f"repos/{gh.repo}/pulls", {"head": f"{gh.repo.split('/')[0]}:{_SETUP_BRANCH}", "state": "open"}, use_cache=False)
        if not existing:
            raise
        found = existing[0]
        return {"pr": int(found["number"]), "url": str(found.get("html_url", "")), "branch": _SETUP_BRANCH, "existing": True}
    return {"pr": int(pr["number"]), "url": str(pr.get("html_url", "")), "branch": _SETUP_BRANCH}
