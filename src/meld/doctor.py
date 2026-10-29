"""meld doctor: verify the machine, the tokens, the config, and the product repo before running a group (spec 20 step 2, 21 item 2).

Read-only, apart from the opt-in status probe. Every check is a Check(ok=True|False|None): None is a warning, or -- with
measured=False -- a check that could not run, which `summary` lists under `not_measured` rather than `warnings` so that a
green doctor never passes off "nothing was read" as "verified" (spec 21 item 14). Nothing here prints or stores a token
value -- only variable names and account logins.

For a PAT group (spec 18), `token:<g>` and `token-permissions:<g>` verify the token is set, owned by the configured bot,
and carries the needed permissions. For a GitHub App group (plan 6a), those two checks are replaced by `app:<g>` (the
private key loads, the App JWT signs, and `GET /app` resolves to the configured bot) and `app-installation:<g>` (an
installation access token mints, its granted permissions cover contents/issues/pull_requests/statuses, and the
installation includes the product repo) -- App permissions come from the mint response, not a probe. `openssl` (needed
to sign an App's JWT) is checked once per run, on PATH, regardless of whether any group uses App auth.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence, Callable, Mapping

from meld.adapters import UnknownAdapter
from meld.adapters.base import SCRUBBED_EXACT, SCRUBBED_PREFIXES
from meld.appauth import AppAuthError, app_slug, load_pem, mint_installation_token
from meld.atomic import write_atomic
from meld.config import Config, GroupConfig, same_login
from meld.github import GitHub, GitHubError
from meld.gitops import GitError, git, noreply_email, remove_worktree
from meld.housekeeping import CI_RED_STATE
from meld.models import ModelRegistry
from meld.transcript import total_bytes

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


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool | None
    detail: str
    fix: str = ""
    measured: bool = True   # False: the check could not run or could not read its answer, so `ok` is None and means nothing


def _run(argv: list[str], run=subprocess.run) -> subprocess.CompletedProcess | None:
    try:
        return run(argv, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None


def _version(argv: list[str], run=subprocess.run) -> str | None:
    proc = _run(argv, run)
    if proc is None or proc.returncode != 0:
        return None
    return (proc.stdout or proc.stderr or "").strip().splitlines()[0] if (proc.stdout or proc.stderr) else "ok"


def _help_text(argv: list[str], run=subprocess.run) -> str | None:
    proc = _run(argv, run)
    if proc is None or proc.returncode != 0:
        return None
    return (proc.stdout or "") + "\n" + (proc.stderr or "")


def _login_check(group: GroupConfig, adapter, run=subprocess.run) -> Check:
    """`cli-login:<g>`: the CLI's own answer to "are you logged in" (spec 20 step 2, 21 item 2). The first live run began with
    `claude` logged out while doctor read green, because `cli:<g>` proves only that the binary answers --version. A probe
    that cannot run, or an answer the adapter cannot read, is NOT MEASURED -- never a pass (item 14)."""
    name, binary, argv = f"cli-login:{group.name}", adapter.version_argv()[0], adapter.login_argv()
    if not argv:
        return Check(name, None, f"{binary}: this adapter has no login probe", measured=False)
    proc = _run(argv, run)
    if proc is None:
        return Check(name, None, f"{binary}: `{' '.join(argv[1:])}` could not run", f"run `{' '.join(argv)}` by hand", measured=False)
    ok, detail = adapter.login_status(proc.stdout or "", proc.stderr or "", proc.returncode)
    fix = "" if ok else (f"log in with the {group.cli} CLI as the user who runs `meld run`" if ok is False else f"run `{' '.join(argv)}` by hand")
    return Check(name, ok, f"{binary}: {detail}", fix, measured=ok is not None)


def missing_flags(help_text: str, flags: Sequence[str]) -> list[str]:
    """The flags not mentioned in `help_text` as whole tokens (`-s` matches `-s, --sandbox`, not `--settings`)."""
    return [f for f in flags if not re.search(r"(?<![\w-])" + re.escape(f) + r"(?![\w-])", help_text)]


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


def _group_config_checks(cfg: Config, group: GroupConfig, registry: ModelRegistry, adapter) -> list[Check]:
    """The two checks that are a function of the configuration and the model catalogue alone: the models and
    efforts this group asks for, and whether its CLI would even let a worker wait as long as polling does.

    Split out because `meld watch`'s configuration form runs exactly these against a candidate file before it is
    written (spec section 22), and a form that could pass a check the CLI fails would be a second opinion about a
    settled question. `adapter` is None when this build has no adapter for the group's `cli` -- that is a `cli:`
    problem for `run_checks` to report, so the ceiling is skipped rather than failed.
    """
    problems = []
    for role_name, role in (("orchestrator", group.orchestrator), ("pm", group.pm)):
        problems += [f"{role_name}: {p}" for p in registry.validate(group.cli, role.model, role.effort)]
    checks = [Check(f"models:{group.name}", not problems,
                    "; ".join(problems) or f"{group.orchestrator.model}/{group.orchestrator.effort}, {group.pm.model}/{group.pm.effort}",
                    "" if not problems else "pick models from `meld models`")]
    if adapter is None:
        checks.append(Check(f"timeout-ceiling:{group.name}", None, "skipped (unknown cli)", measured=False))
    else:
        ceiling = adapter.command_timeout_ceiling_seconds()
        ok = ceiling > cfg.wait_timeout_seconds
        checks.append(Check(f"timeout-ceiling:{group.name}", ok,
                            f"command ceiling {ceiling}s vs polling.wait_timeout_seconds {cfg.wait_timeout_seconds}s",
                            "" if ok else "lower polling.wait_timeout_seconds below the CLI's command ceiling"))
    return checks


def config_checks(cfg: Config, registry: ModelRegistry, adapter_for) -> list[Check]:
    """Everything `meld doctor` can say about a configuration without the network, a binary, a token or the
    machine. `meld watch`'s form runs these against a candidate before writing it."""
    out: list[Check] = []
    for group in cfg.groups.values():
        try:
            adapter = adapter_for(group.cli)
        except UnknownAdapter:
            adapter = None
        out += _group_config_checks(cfg, group, registry, adapter)
    return out


def run_checks(cfg: Config, repo_root: Path, *, env: Mapping[str, str], gh_factory: Callable[[str | None], GitHub], registry: ModelRegistry,
               adapter_for, which: Callable[[str], str | None] = shutil.which, home: Path, probe_status: bool = False,
               user_home: Path = Path.home(), run=subprocess.run, app_post=None, app_get=None,
               disk_usage: Callable[[Path], object] = shutil.disk_usage) -> list[Check]:
    checks: list[Check] = []
    # 1. gh's own login is a listed human
    try:
        me = gh_factory(None).viewer_login()
        checks.append(Check("gh-login", cfg.is_human(me), f"gh is logged in as {me}; humans: {', '.join(cfg.humans)}",
                            "" if cfg.is_human(me) else "add the login to [humans].accounts or `gh auth switch`"))
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
            # 2b. the flags the adapter passes are still known to the installed CLI: a renamed or removed flag makes every
            # launch exit at once and the supervisor relaunch it forever (the first live run hit `codex exec -a`)
            help_argv, flags = adapter.help_argv(), list(adapter.required_flags())
            if found and version and help_argv and flags:
                text = _help_text(help_argv, run)
                if text is None:
                    checks.append(Check(f"cli-flags:{group.name}", False, f"{binary}: {' '.join(help_argv[1:])} failed",
                                        f"run `{' '.join(help_argv)}` by hand"))
                else:
                    missing = missing_flags(text, flags)
                    checks.append(Check(f"cli-flags:{group.name}", not missing,
                                        f"{binary}: all {len(flags)} flags Meld passes are listed by {' '.join(help_argv[1:])}" if not missing
                                        else f"{binary} no longer lists {', '.join(missing)}; every launch would fail at once",
                                        "" if not missing else f"upgrade Meld's {group.cli} adapter (or pin the CLI version it was written for)"))
            # 2c. the CLI is logged in -- the CLI's own answer, read by its adapter; see _login_check
            if found and version:
                checks.append(_login_check(group, adapter, run))
        # 3. token present and owned by the configured bot (PAT groups); key/JWT/slug and installation token (App groups)
        if group.is_app:
            login = None
            pem = None
            try:
                pem = load_pem(env, group.private_key_env, group.private_key_file)
                slug = app_slug(group.app_id, pem, get=app_get)  # type: ignore[arg-type]
                login = f"{slug}[bot]"
                checks.append(Check(f"app:{group.name}", same_login(login, group.bot), f"App {group.app_id} is {login}; configured bot is {group.bot}",
                                    "" if same_login(login, group.bot) else f"set [groups.{group.name}].bot = \"{login}\""))
            except AppAuthError as e:
                checks.append(Check(f"app:{group.name}", False, f"App auth failed: {e}", "check private_key_env/private_key_file and app_id"))
            if pem is not None:
                try:
                    tok = mint_installation_token(group.app_id, group.installation_id, pem,  # type: ignore[arg-type]
                                                  repositories=[cfg.repo_name.rpartition("/")[2] or cfg.repo_name], post=app_post)
                    need = {"contents": "write", "issues": "write", "pull_requests": "write", "statuses": "write"}
                    wrong = {k: tok.permissions.get(k, "none") for k, v in need.items() if tok.permissions.get(k) != v}
                    detail = "permissions: " + ", ".join(f"{k}: {tok.permissions.get(k, 'none')}" for k in (*need, "checks", "workflows"))
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
                        detail += "; this group cannot read check runs (every open PR shows checks unreadable to it, so it can never merge) and each review it posts will record check_run_error"
                    # A worker's branch that touches .github/workflows/* is refused by GitHub without Workflows: write, and collect
                    # fails on the push (the maze run's #4). A warning: most work never touches a workflow file.
                    no_workflows = tok.permissions.get("workflows") != "write"
                    if no_workflows:
                        detail += "; without Workflows: write a worker branch that edits .github/workflows/* cannot be pushed, so collect fails on it"
                    bad = bool(wrong) or (not unverified and not covers)
                    fix = ""
                    if bad:
                        fix = ("edit the App's permissions (Contents, Issues, Pull requests, Commit statuses: read & write; "
                               "Checks: read & write recommended) and install it on the repo")
                    elif no_checks or no_workflows:
                        missing = " and ".join(n for n, m in (("Checks", no_checks), ("Workflows", no_workflows)) if m)
                        fix = f"grant the App {missing}: read & write (GitHub > Settings > Developer settings > GitHub Apps > the App > Permissions), then accept the change on its installation"
                    checks.append(Check(f"app-installation:{group.name}", False if bad else (None if unverified or no_checks or no_workflows else True), detail, fix,
                                        measured=not unverified))
                except AppAuthError as e:
                    checks.append(Check(f"app-installation:{group.name}", False, f"installation token failed: {e}", "check installation_id (from the installation URL) and that the App is installed on the repo"))
        else:
            token = env.get(group.token_env)
            if not token:
                checks.append(Check(f"token:{group.name}", False, f"{group.token_env} is not set",
                                    f"export {group.token_env} (from 1Password) for the orchestrator and PM processes, or run `meld create-app {group.name}` to give the group a GitHub App instead"))
                login = None
            else:
                try:
                    login = gh_factory(token).viewer_login()
                    checks.append(Check(f"token:{group.name}", same_login(login, group.bot),
                                        f"{group.token_env} belongs to {login}; configured bot is {group.bot}",
                                        "" if same_login(login, group.bot) else "mint the token from the bot account, or fix [groups.%s].bot" % group.name))
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
        # 5. models, 6. command ceiling vs wait timeout -- shared with doctor.config_checks (spec 22), so the
        # CLI and meld watch's configuration form can never drift into two opinions about a settled question.
        checks += _group_config_checks(cfg, group, registry, adapter)
        # 7. token permissions (read probes; write only with --probe-status) -- App permissions come from the token response (step 3)
        if not group.is_app:
            if token and login is not None and same_login(login, group.bot) and head_sha:
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
                checks.append(Check(f"token-permissions:{group.name}", None, "skipped (token or default-branch head unavailable)", measured=False))
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
    # 10b. logs total size (plan 8a): a streamed transcript can be huge; warn only, since nothing is broken by it
    logs_mb = total_bytes(home / "logs") // (1024 * 1024)
    over = logs_mb > cfg.logs_warn_total_mb
    checks.append(Check("logs-size", None if over else True, f"{logs_mb} MB of run logs under {home / 'logs'}",
                        f"streamed transcripts accumulate; `meld tick` caps a single run at "
                        f"{cfg.logs_max_bytes_per_run // (1024 * 1024)} MB, but old runs are yours to delete" if over else ""))
    # 10c. free space on the volume holding $MELD_HOME (plan 9): the log cap above bounds one run's log, nothing bounds
    # the volume, and a full disk killed both supervisors on 2026-09-12. Warn only -- low disk is a machine condition a
    # person fixes, never a misconfiguration, so this check is never `False`.
    try:
        free_mb = disk_usage(home).free // (1024 * 1024)
    except OSError as e:
        checks.append(Check("disk-free", None, f"cannot read free space on {home}: {e}", measured=False))
    else:
        low = free_mb < cfg.logs_warn_free_mb
        checks.append(Check("disk-free", None if low else True, f"{free_mb} MB free on the volume holding {home}",
                            "a supervisor that cannot create a log file backs off rather than exiting, but every "
                            "launch will keep failing until there is room" if low else ""))
    # 11. webhook
    if cfg.notify_webhook_env:
        set_ = bool(env.get(cfg.notify_webhook_env))
        checks.append(Check("webhook", None if not set_ else True, f"{cfg.notify_webhook_env} is {'set' if set_ else 'not set'}", "" if set_ else "export it, or remove [notify] to silence this"))
    # 12. origin over HTTPS (spec 18: host SSH keys are not blocked for workers)
    try:
        url = git(["remote", "get-url", "origin"], repo_root)
        checks.append(Check("origin-https", True if url.startswith("https://") else None, url, "" if url.startswith("https://") else "use an HTTPS origin so workers cannot push with the host's SSH keys"))
    except GitError as e:
        checks.append(Check("origin-https", None, f"cannot read origin: {e}", measured=False))
    return checks


def summary(checks: list[Check]) -> dict:
    """`ok` is "nothing failed"; `not_measured` names the checks whose answer could not be read, which a green `ok` has
    therefore not verified. They are kept out of `warnings`, which is for a measured condition that does not block."""
    return {"checks": [asdict(c) for c in checks], "failed": [c.name for c in checks if c.ok is False],
            "warnings": [c.name for c in checks if c.ok is None and c.measured],
            "not_measured": [c.name for c in checks if not c.measured], "ok": all(c.ok is not False for c in checks)}


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
    # Plan 4's per-party nudge throttle (`state/<who>[/<role>]/notify.json`, notifier.NOTIFY_STATE). Plan 5's shared
    # `meld:nudged` label replaced it and nothing reads it any more, so an upgraded install carries one dead file per
    # party. Listed for every party found, not just the configured groups: a group that has since been renamed or
    # removed from `.meld.toml` left one behind too. No age threshold -- nothing writes these any more.
    state = home / "state"
    for p in sorted([*state.glob("*/notify.json"), *state.glob("*/*/notify.json")]):
        candidates.append({"kind": "legacy-notify-state", "path": str(p),
                           "reason": "plan-4 per-party nudge throttle, no longer read (the shared meld:nudged label replaced it)"})
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
