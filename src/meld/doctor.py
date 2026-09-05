"""meld doctor: verify the machine, the tokens, the config, and the product repo before running a group (spec 20 step 2, 21 item 2).

Read-only, apart from the opt-in status probe. Every check is a Check(ok=True|False|None): None is a warning or a check that
could not run. Nothing here prints or stores a token value -- only variable names and account logins.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping

from meld.adapters.base import SCRUBBED_EXACT, SCRUBBED_PREFIXES
from meld.config import Config
from meld.github import GitHub, GitHubError
from meld.gitops import GitError, git, noreply_email
from meld.models import ModelRegistry

MEMORY_WARN_BYTES = 2 * 1024 * 1024
PROBE_CONTEXT = "meld/doctor"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool | None
    detail: str
    fix: str = ""


def _version(argv: list[str], run=subprocess.run) -> str | None:
    try:
        proc = run(argv, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or proc.stderr or "").strip().splitlines()[0] if (proc.stdout or proc.stderr) else "ok"


def _settings_env_names(paths: list[Path]) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for p in paths:
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        env = data.get("env") if isinstance(data, dict) else None
        if not isinstance(env, dict):
            continue
        bad = sorted(k for k in env if k in SCRUBBED_EXACT or any(k.startswith(pre) for pre in SCRUBBED_PREFIXES))
        if bad:
            hits[str(p)] = bad
    return hits


def _hidden_pth(repo_root: Path) -> list[Path]:
    out = []
    for p in (repo_root / ".venv" / "lib").glob("python*/site-packages/*.pth"):
        try:
            flags = getattr(os.stat(p), "st_flags", 0)
        except OSError:
            continue
        if flags & getattr(stat, "UF_HIDDEN", 0):
            out.append(p)
    return out


def _memory_bytes(repo_root: Path) -> int:
    total = 0
    for p in list((repo_root / ".meld" / "decisions").glob("*.md")) + [repo_root / ".meld" / "memory" / "lessons.md"]:
        if p.is_file():
            total += p.stat().st_size
    return total


def run_checks(cfg: Config, repo_root: Path, *, env: Mapping[str, str], gh_factory: Callable[[str | None], GitHub], registry: ModelRegistry,
               adapter_for, which: Callable[[str], str | None] = shutil.which, home: Path, probe_status: bool = False,
               user_home: Path = Path.home(), run=subprocess.run) -> list[Check]:
    checks: list[Check] = []
    # 1. gh's own login is a listed human
    try:
        me = gh_factory(None).viewer_login()
        checks.append(Check("gh-login", me in cfg.humans, f"gh is logged in as {me}; humans: {', '.join(cfg.humans)}",
                            "" if me in cfg.humans else "add the login to [humans].accounts or `gh auth switch`"))
    except GitHubError as e:
        checks.append(Check("gh-login", False, f"gh api user failed: {e}", "run `gh auth login`"))
    head_sha: str | None = None
    try:
        head_sha = gh_factory(None).get(f"repos/{cfg.repo_name}/branches/{cfg.default_branch}", use_cache=False)["commit"]["sha"]
    except (GitHubError, KeyError, TypeError):
        pass
    for group in cfg.groups.values():
        adapter = adapter_for(group.cli)
        # 2. CLI installed
        binary = adapter.version_argv()[0]
        found = which(binary)
        version = _version(adapter.version_argv(), run) if found else None
        checks.append(Check(f"cli:{group.name}", bool(found and version), f"{binary}: {version or 'not found on PATH'}",
                            "" if found and version else f"install the {group.cli} CLI and log in"))
        # 3. token present and owned by the configured bot
        token = env.get(group.token_env)
        if not token:
            checks.append(Check(f"token:{group.name}", False, f"{group.token_env} is not set", f"export {group.token_env} (from 1Password) for the orchestrator and PM processes"))
            login = None
        else:
            try:
                login = gh_factory(token).viewer_login()
                checks.append(Check(f"token:{group.name}", login == group.bot,
                                    f"{group.token_env} belongs to {login}; configured bot is {group.bot}",
                                    "" if login == group.bot else "mint the token from the bot account, or fix [groups.%s].bot" % group.name))
            except GitHubError as e:
                login = None
                checks.append(Check(f"token:{group.name}", False, f"{group.token_env}: gh api user failed: {e}", "check the token is valid and not expired"))
        # 4. git identity for worktree commits
        if group.git_email:
            checks.append(Check(f"git-identity:{group.name}", True, f"{group.git_name or group.bot} <{group.git_email}> (configured)"))
        else:
            try:
                user = gh_factory(None).get(f"users/{group.bot}", use_cache=False)
                checks.append(Check(f"git-identity:{group.name}", True, f"{group.bot} <{noreply_email(int(user['id']), group.bot)}> (derived)"))
            except (GitHubError, KeyError, TypeError, ValueError) as e:
                checks.append(Check(f"git-identity:{group.name}", False, f"cannot resolve {group.bot}: {e}", "set git_name/git_email under [groups.%s]" % group.name))
        # 5. models
        problems = []
        for role_name, role in (("orchestrator", group.orchestrator), ("pm", group.pm)):
            problems += [f"{role_name}: {p}" for p in registry.validate(group.cli, role.model, role.effort)]
        checks.append(Check(f"models:{group.name}", not problems, "; ".join(problems) or f"{group.orchestrator.model}/{group.orchestrator.effort}, {group.pm.model}/{group.pm.effort}",
                            "" if not problems else "pick models from `meld models`"))
        # 6. command ceiling vs wait timeout
        ceiling = adapter.command_timeout_ceiling_seconds()
        ok = ceiling > cfg.wait_timeout_seconds
        checks.append(Check(f"timeout-ceiling:{group.name}", ok, f"command ceiling {ceiling}s vs polling.wait_timeout_seconds {cfg.wait_timeout_seconds}s",
                            "" if ok else "lower polling.wait_timeout_seconds below the CLI's command ceiling"))
        # 7. token permissions (read probes; write only with --probe-status)
        if token and login == group.bot and head_sha:
            gh = gh_factory(token)
            missing = []
            for perm, call in (("Issues", lambda: gh.get(f"repos/{cfg.repo_name}/issues", {"per_page": 1}, use_cache=False)),
                               ("Pull requests", lambda: gh.get(f"repos/{cfg.repo_name}/pulls", {"per_page": 1}, use_cache=False)),
                               ("Commit statuses", lambda: gh.combined_status(head_sha))):
                try:
                    call()
                except GitHubError:
                    missing.append(perm)
            detail = "read: Issues, Pull requests, Commit statuses" if not missing else f"cannot read: {', '.join(missing)}"
            if probe_status and "Commit statuses" not in missing:
                try:
                    gh.create_status(head_sha, "success", PROBE_CONTEXT, f"meld doctor probe by {group.name}")
                    detail += "; write: Commit statuses (probed)"
                except GitHubError:
                    missing.append("Commit statuses (write)")
                    detail += "; cannot write Commit statuses"
            elif not missing:
                detail += "; write not probed (pass --probe-status)"
            checks.append(Check(f"token-permissions:{group.name}", not missing, detail,
                                "" if not missing else "re-mint the token with Contents, Issues, Pull requests, Commit statuses: read and write"))
        else:
            checks.append(Check(f"token-permissions:{group.name}", None, "skipped (token or default-branch head unavailable)"))
    # 8. settings.json env blocks that would re-inject a token or party into a worker
    hits = _settings_env_names([user_home / ".claude" / "settings.json", repo_root / ".claude" / "settings.json", repo_root / ".claude" / "settings.local.json"])
    checks.append(Check("settings-env", None if hits else True,
                        "; ".join(f"{p} env names {', '.join(names)}" for p, names in hits.items()) or "no scrubbed names in any settings.json env block",
                        "remove those names from the env block; workers must not receive tokens or party identity" if hits else ""))
    # 9. hidden .pth files (README)
    hidden = _hidden_pth(repo_root)
    checks.append(Check("pth-hidden", None if hidden else True, "; ".join(str(p) for p in hidden) or "no hidden .pth files under .venv",
                        "run `chflags nohidden <file>` (see README)" if hidden else ""))
    # 10. memory store size (decision 11)
    size = _memory_bytes(repo_root)
    big = size > MEMORY_WARN_BYTES
    checks.append(Check("memory-size", None if big else True, f"{size} bytes of decisions and lessons" + (" exceeds the 2 MB mark: recall scans grow with it (spec 21 item 13)" if big else ""),
                        "prune or split .meld/memory/lessons.md, or build the recall index" if big else ""))
    # 11. webhook
    if cfg.notify_webhook_env:
        set_ = bool(env.get(cfg.notify_webhook_env))
        checks.append(Check("webhook", None if not set_ else True, f"{cfg.notify_webhook_env} is {'set' if set_ else 'not set'}", "" if set_ else "export it, or remove [notify] to silence this"))
    # 12. origin over HTTPS (spec 18: host SSH keys are not blocked for workers)
    try:
        url = git(["remote", "get-url", "origin"], repo_root)
        checks.append(Check("origin-https", True if url.startswith("https://") else None, url, "" if url.startswith("https://") else "use an HTTPS origin so workers cannot push with the host's SSH keys"))
    except GitError as e:
        checks.append(Check("origin-https", None, f"cannot read origin: {e}"))
    return checks


def summary(checks: list[Check]) -> dict:
    return {"checks": [asdict(c) for c in checks], "failed": [c.name for c in checks if c.ok is False],
            "warnings": [c.name for c in checks if c.ok is None], "ok": all(c.ok is not False for c in checks)}
