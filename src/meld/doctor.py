"""meld doctor: verify the machine, the tokens, the config, and the product repo before running a group (spec 20 step 2, 21 item 2).

Read-only, apart from the opt-in status probe. Every check is a Check(ok=True|False|None): None is a warning or a check that
could not run. Nothing here prints or stores a token value -- only variable names and account logins.

For a PAT group (spec 18), `token:<g>` and `token-permissions:<g>` verify the token is set, owned by the configured bot,
and carries the needed permissions. For a GitHub App group (plan 6a), those two checks are replaced by `app:<g>` (the
private key loads, the App JWT signs, and `GET /app` resolves to the configured bot) and `app-installation:<g>` (an
installation access token mints, its granted permissions cover contents/issues/pull_requests/statuses, and the
installation includes the product repo) -- App permissions come from the mint response, not a probe. `openssl` (needed
to sign an App's JWT) is checked once per run, on PATH, regardless of whether any group uses App auth. Every group,
PAT and App alike, also gets `assignable:<g>`: the claim lock is the issue assignee, so a bot login the product repo
will not accept as an assignee cannot claim (see APP_ASSIGNEE_FIX).
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import quote

from meld.adapters import UnknownAdapter
from meld.adapters.base import SCRUBBED_EXACT, SCRUBBED_PREFIXES
from meld.appauth import AppAuthError, app_slug, load_pem, mint_installation_token
from meld.atomic import write_atomic
from meld.config import Config
from meld.github import GitHub, GitHubError
from meld.gitops import GitError, git, noreply_email, remove_worktree
from meld.housekeeping import CI_RED_STATE
from meld.models import ModelRegistry

MEMORY_WARN_BYTES = 2 * 1024 * 1024
PROBE_CONTEXT = "meld/doctor"
# Shared minimum age for two doctor.sweep candidate kinds that can otherwise look identical to scratch still in
# active use: dispatch creates a run's <n>-gh/<n>-gitconfig (dispatch._worker_scratch) before it writes <n>.json
# (the live run record), and memory._open_memory_pr's worktrees/<group>/memory-* worktree exists for the full
# commit/push/create_pull sequence before that function's own `finally` removes it -- `git worktree remove --force`
# will happily tear down that worktree mid-commit (reproduced in review). Either kind younger than this may belong
# to something still running right now, not an orphan or crash; only flag one old enough that such a race is
# implausible.
RUN_SCRATCH_MIN_AGE_SECONDS = 3600
# The claim lock is the issue assignee (spec 8), and GitHub validates assignees against the repository's assignable
# users. An App's bot user is never assignable (probe 2026-09-09: `GET repos/<repo>/assignees/<login>` answers 204 for
# a collaborator and 404 for github-actions[bot], dependabot[bot] and copilot-swe-agent[bot] alike), so an App group
# cannot take the lock today. This is a report, never a legality decision -- rules.py stays the only such authority.
APP_ASSIGNEE_FIX = ("GitHub does not let an App's bot user be an issue assignee; with the assignee claim lock this group "
                    "can review, audit and merge but cannot claim and build until plan 6b's App-compatible claim lock")
PAT_ASSIGNEE_FIX = "add the machine user as a collaborator"


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
               user_home: Path = Path.home(), run=subprocess.run, app_post=None, app_get=None) -> list[Check]:
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
    checks.append(Check("openssl", bool(which("openssl")), "openssl on PATH" if which("openssl") else "openssl not found; App groups cannot sign their JWT",
                        "" if which("openssl") else "install openssl (brew install openssl) or use PAT groups"))
    for group in cfg.groups.values():
        try:
            adapter = adapter_for(group.cli)
        except UnknownAdapter:
            adapter = None
            checks.append(Check(f"cli:{group.name}", False, f"unknown cli {group.cli!r} in .meld.toml", "set [groups.<g>].cli to claude or codex"))
        else:
            # 2. CLI installed
            binary = adapter.version_argv()[0]
            found = which(binary)
            version = _version(adapter.version_argv(), run) if found else None
            if found and version:
                detail = f"{binary}: {version}"
            elif found:
                detail = f"{binary}: found but --version failed"
            else:
                detail = f"{binary}: not found on PATH"
            checks.append(Check(f"cli:{group.name}", bool(found and version), detail,
                                "" if found and version else f"install the {group.cli} CLI and log in"))
        # 3. token present and owned by the configured bot (PAT groups); key/JWT/slug and installation token (App groups)
        if group.is_app:
            login = None
            pem = None
            try:
                pem = load_pem(env, group.private_key_env, group.private_key_file)
                slug = app_slug(group.app_id, pem, get=app_get)  # type: ignore[arg-type]
                login = f"{slug}[bot]"
                checks.append(Check(f"app:{group.name}", login == group.bot, f"App {group.app_id} is {login}; configured bot is {group.bot}",
                                    "" if login == group.bot else f"set [groups.{group.name}].bot = \"{login}\""))
            except AppAuthError as e:
                checks.append(Check(f"app:{group.name}", False, f"App auth failed: {e}", "check private_key_env/private_key_file and app_id"))
            if pem is not None:
                try:
                    tok = mint_installation_token(group.app_id, group.installation_id, pem,  # type: ignore[arg-type]
                                                  repositories=[cfg.repo_name.rpartition("/")[2] or cfg.repo_name], post=app_post)
                    need = {"contents": "write", "issues": "write", "pull_requests": "write", "statuses": "write"}
                    wrong = {k: tok.permissions.get(k, "none") for k, v in need.items() if tok.permissions.get(k) != v}
                    detail = "permissions: " + ", ".join(f"{k}: {tok.permissions.get(k, 'none')}" for k in (*need, "checks"))
                    # The mint above asked for the product repo by name, so a 422 (handled below) is the "not covered"
                    # answer and a listed repo is the "covered" one. An empty list is neither: absent evidence warns.
                    unverified = not tok.repositories
                    covers = cfg.repo_name in tok.repositories
                    if unverified:
                        detail += f"; the mint returned no repository list, so coverage of {cfg.repo_name} is unverified"
                    elif not covers:
                        detail += f"; installation does not include {cfg.repo_name} (has: {', '.join(tok.repositories)})"
                    # Checks is not required to review, but review.py publishes its neutral check run only for App
                    # groups: without the permission every review this group submits records check_run_error.
                    no_checks = tok.permissions.get("checks") != "write"
                    if no_checks:
                        detail += "; each review by this group will record check_run_error"
                    bad = bool(wrong) or (not unverified and not covers)
                    fix = ""
                    if bad:
                        fix = ("edit the App's permissions (Contents, Issues, Pull requests, Commit statuses: read & write; "
                               "Checks: read & write recommended) and install it on the repo")
                    elif no_checks:
                        fix = "grant the App Checks: read & write"
                    checks.append(Check(f"app-installation:{group.name}", False if bad else (None if unverified or no_checks else True), detail, fix))
                except AppAuthError as e:
                    checks.append(Check(f"app-installation:{group.name}", False, f"installation token failed: {e}", "check installation_id (from the installation URL) and that the App is installed on the repo"))
        else:
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
        # 3b. the bot login can actually hold the claim lock. Asked with the human's own gh login, never the group's
        # credential: assignability is a property of the repository, and use_cache=False so a stale cached 404 from an
        # earlier run cannot mislead.
        try:
            gh_factory(None).get(f"repos/{cfg.repo_name}/assignees/{quote(group.bot, safe='')}", use_cache=False)
            checks.append(Check(f"assignable:{group.name}", True, f"{group.bot} can be assigned issues on {cfg.repo_name}"))
        except GitHubError as e:
            checks.append(Check(f"assignable:{group.name}", False, f"{group.bot} cannot be assigned issues on {cfg.repo_name}: {e}",
                                APP_ASSIGNEE_FIX if group.is_app else PAT_ASSIGNEE_FIX))
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
        if adapter is None:
            checks.append(Check(f"timeout-ceiling:{group.name}", None, "skipped (unknown cli)"))
        else:
            ceiling = adapter.command_timeout_ceiling_seconds()
            ok = ceiling > cfg.wait_timeout_seconds
            checks.append(Check(f"timeout-ceiling:{group.name}", ok, f"command ceiling {ceiling}s vs polling.wait_timeout_seconds {cfg.wait_timeout_seconds}s",
                                "" if ok else "lower polling.wait_timeout_seconds below the CLI's command ceiling"))
        # 7. token permissions (read probes; write only with --probe-status) -- App permissions come from the token response (step 3)
        if not group.is_app:
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


def sweep(home: Path, cfg: Config, *, repo_root: Path, pull_states: Mapping[int, str] | None = None, older_than_days: int = 7,
          apply: bool = False, now: float | None = None) -> dict:
    """Scratch that meld leaves behind on purpose (verdict archives, per-run gh/git config, crashed memory worktrees, stale
    per-PR records). Lists every candidate with its reason; deletes only when `apply` is true, and only what it listed."""
    now = time.time() if now is None else now
    cutoff = now - older_than_days * 86400
    run_scratch_cutoff = now - RUN_SCRATCH_MIN_AGE_SECONDS
    candidates: list[dict] = []
    skipped: list[str] = []
    if pull_states is None:
        skipped.append("memory-review-record and ci-red-record need the board (pull states); run from a product clone with gh logged in")
    for group in cfg.groups.values():
        results = home / "results" / group.name
        for kind in ("superseded", "submitted"):
            for p in sorted((results / kind).glob("*.json")):
                if p.stat().st_mtime < cutoff:
                    candidates.append({"kind": kind, "path": str(p), "reason": f"consumed verdict older than {older_than_days} days"})
        if pull_states is not None:
            for p in sorted(results.glob("review-*.json")):
                number = p.stem.removeprefix("review-")
                if number.isdigit() and pull_states.get(int(number)) in ("merged", "closed"):
                    candidates.append({"kind": "memory-review-record", "path": str(p), "reason": f"PR #{number} is {pull_states[int(number)]}; the idempotency record is no longer needed"})
        runs = home / "runs" / group.name
        if runs.is_dir():
            for p in sorted(runs.iterdir()):
                stem = p.name.removesuffix("-gh").removesuffix("-gitconfig")
                if (p.name in (f"{stem}-gh", f"{stem}-gitconfig") and stem.isdigit() and not (runs / f"{stem}.json").exists()
                        and p.stat().st_mtime < run_scratch_cutoff):
                    candidates.append({"kind": "run-scratch", "path": str(p), "reason": f"no live run record {stem}.json"})
        for p in sorted((home / "worktrees" / group.name).glob("memory-*")):
            if p.is_dir() and p.stat().st_mtime < run_scratch_cutoff:
                candidates.append({"kind": "memory-worktree", "path": str(p), "reason": "memory-PR scratch worktree left by a crash"})
        if pull_states is not None:
            for state_file in sorted((home / "state" / group.name).glob(f"*/{CI_RED_STATE}")):
                try:
                    data = json.loads(state_file.read_text())
                except (OSError, ValueError):
                    continue
                if not isinstance(data, dict):
                    continue
                for key in sorted(data):
                    if key.isdigit() and pull_states.get(int(key)) in ("merged", "closed"):
                        # "/"-joined (not "#") so Path(path).name is the bare key: a fragment-style "#" join collapses
                        # into the preceding path segment (ci-red.json#20).name == "ci-red.json#20", not "20".
                        candidates.append({"kind": "ci-red-record", "path": f"{state_file}/{key}", "reason": f"PR #{key} is {pull_states[int(key)]}"})
    removed: list[str] = []
    if apply:
        for c in candidates:
            target = c["path"]
            if c["kind"] == "ci-red-record":
                try:
                    file, key = target.rsplit("/", 1)
                    data = json.loads(Path(file).read_text())
                    data.pop(key, None)
                    write_atomic(Path(file), json.dumps(data, indent=2, sort_keys=True))
                except (OSError, ValueError, AttributeError, TypeError) as e:
                    # As tolerant as the listing phase (OSError: missing/unreadable; ValueError: not valid JSON,
                    # e.g. json.JSONDecodeError) plus AttributeError/TypeError for a file that changed shape between
                    # listing and here -- e.g. replaced with a bare list: .pop(key, None) then either doesn't exist
                    # (a str/int/None) or exists with an incompatible signature (a list only takes an index).
                    skipped.append(f"{target}: {type(e).__name__}")
                    continue
                removed.append(target)
                continue
            try:
                if c["kind"] == "memory-worktree":
                    try:
                        remove_worktree(repo_root, Path(target))
                    except Exception:  # not registered as a worktree of this clone: plain directory debris
                        shutil.rmtree(target, ignore_errors=True)
                        if Path(target).exists():
                            # ignore_errors=True never raises, even when it refuses to act at all (e.g. target is
                            # a symlink, which rmtree will not follow) -- confirm it is actually gone before this
                            # falls through to removed.
                            skipped.append(f"{target}: still present after removal attempt")
                            continue
                elif Path(target).is_dir():
                    shutil.rmtree(target)
                else:
                    Path(target).unlink()
            except OSError as e:
                skipped.append(f"{target}: {type(e).__name__}")
                continue
            removed.append(target)
    return {"candidates": candidates, "removed": removed, "dry_run": not apply, "skipped": skipped}
