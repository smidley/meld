"""meld setup: bootstrap a product repository -- labels, milestone, coordination issue, a starter .meld.toml.

Idempotent: every GitHub object that already exists is reused, and the config file is never overwritten without
--force. Runs as the human (gh's own login), before any .meld.toml exists, so it does not go through load_context.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from meld.adapters import ADAPTER_NAMES, get_adapter
from meld.board import CHALLENGED_LABEL, COORDINATION_LABEL, NUDGED_LABEL, PLAN_APPROVED_LABEL, STATUS_LABELS
from meld.github import GitHub
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


def _group_block(name: str, registry: ModelRegistry, commented: bool) -> str:
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
    return "\n".join(lines) + "\n"


def starter_toml(repo: str, default_branch: str, human: str, installed: list[str], registry: ModelRegistry) -> str:
    parts = [f'[repo]\nname = "{repo}"\ndefault_branch = "{default_branch}"\nprivate = true\n',
             f'[humans]\naccounts = ["{human}"]\n']
    for name in ADAPTER_NAMES:
        parts.append(_group_block(name, registry, commented=name not in installed))
    parts.append('[review]\nquorum = 2\npingpong_cap = 3\nsecurity_paths = ["auth/**", "**/secrets*", "infra/**"]\n')
    parts.append('[notify]\nslack_dm_webhook_env = "MELD_SLACK_DM_WEBHOOK"\nrepeat_after_minutes = 120\n')
    return "\n".join(parts)


def setup(gh: GitHub, *, repo: str, goal: str, milestone_title: str, human: str, target_dir: Path, registry: ModelRegistry,
          allow_public: bool = False, which: Callable[[str], str | None] = shutil.which, force: bool = False) -> dict:
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
        if registry.for_adapter(name):
            groups.append(name)
        else:
            warnings.append(f"{name} is installed but models.toml lists no models for it; its [groups.{name}] block is commented out")
    target_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(starter_toml(repo, info.get("default_branch", "main"), human, installed, registry))
    return {"repo": repo, "labels": labels, "milestone": milestone, "milestone_how": milestone_how,
            "coordination_issue": coordination, "coordination_how": issue_how, "config": str(config_path), "config_written": True,
            "groups": groups, "warnings": warnings,
            "next": [f"create one GitHub machine account per group named in {config_path.name} and add each as a collaborator on {repo}",
                     "mint one fine-grained token per bot (Contents, Issues, Pull requests, Commit statuses: read and write) and export MELD_TOKEN_<GROUP>",
                     "run `meld doctor` to verify tokens, CLIs, and models", "run `meld run <group>` for each group, then `meld chat`"]}
