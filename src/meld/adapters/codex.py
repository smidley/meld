"""OpenAI Codex CLI adapter: one unattended `codex exec` run per launch."""
from __future__ import annotations

import json
from pathlib import Path

from meld.adapters.base import LaunchSpec, is_limit, limit_event, open_log, parse_doc, popen_detached, raw_event, scan_skills
from meld.roster import WorkerType
from meld.transcript import Event, cap

LAST_MESSAGE_FILE = ".meld-last-message.txt"
COMMAND_CEILING_SECONDS = 600  # conservative; the exact Codex shell-command ceiling is verified by `meld doctor`
# `codex exec` never prompts, so it takes no approval flag (`-a` was removed from exec and made the first live run
# crash-loop). An orchestrator or PM runs `meld` itself -- GitHub over the network, git fetch and push for memory
# PRs -- so it gets the full-access sandbox; a worker keeps the workspace-write sandbox (its isolation is the
# worktree and the absence of credentials, spec 18) with network on, since `uv sync` and `pip` need it.
REQUIRED_FLAGS = ("-m", "-c", "-s", "-C", "--json", "-o", "--ignore-user-config", "--ephemeral", "--add-dir")


TRUSTED_ROLES = ("orchestrator", "pm")


def git_dirs(cwd: Path) -> list[Path]:
    """The git directories a linked worktree writes to. `cwd/.git` is a FILE reading `gitdir: <main>/.git/worktrees/<name>`
    for a worktree, and every `git add`/`commit` there writes index.lock and refs under that path and the common
    `<main>/.git` -- both outside the workspace-write sandbox, which is how the first live run's CLI worker built the
    whole feature and then could not commit it."""
    marker = cwd / ".git"
    if not marker.is_file():
        return []
    text = marker.read_text(errors="replace").strip()
    if not text.startswith("gitdir:"):
        return []
    gitdir = Path(text[len("gitdir:"):].strip())
    if not gitdir.is_absolute():
        gitdir = (cwd / gitdir).resolve()
    common = gitdir.parent.parent if gitdir.parent.name == "worktrees" else gitdir
    return [gitdir, common] if common != gitdir else [gitdir]


def uv_cache_dir(env: dict[str, str], home: Path) -> Path:
    return Path(env["UV_CACHE_DIR"]).expanduser() if env.get("UV_CACHE_DIR") else home / ".cache" / "uv"


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value))   # a JSON string is a TOML basic string for every character Codex's -c will meet


def override_args(cli_config: dict) -> list[str]:
    """`-c key=value` per [groups.<g>.cli_config] entry, in key order: a custom `model_providers.<id>` block plus a
    `model_provider` is how an ephemeral `codex exec` (which reads no config file) reaches an OpenAI-compatible
    endpoint such as Ollama's."""
    return [arg for key, value in sorted(cli_config.items()) for arg in ("-c", f"{key}={_toml_value(value)}")]


def sandbox_args(role: str, cwd: Path, env: dict[str, str], home: Path) -> list[str]:
    if role in TRUSTED_ROLES:
        return ["-s", "danger-full-access"]
    # Workers, and anything unknown: workspace-write with network on, plus the directories a worker legitimately
    # writes outside its worktree -- its git metadata and uv's package cache (a denied cache makes `uv run pytest` fail).
    extra = [*git_dirs(cwd), uv_cache_dir(env, home)]
    add = [arg for d in extra for arg in ("--add-dir", str(d))]
    return ["-s", "workspace-write", "-c", "sandbox_workspace_write.network_access=true", *add]


class CodexAdapter:
    name = "codex"

    def __init__(self, binary: str = "codex", home: Path | None = None):
        self.binary = binary
        self.home = home or Path.home()

    def argv(self, spec: LaunchSpec) -> list[str]:
        return [
            self.binary, "exec",
            "-m", spec.model,
            "-c", f'model_reasoning_effort="{spec.effort}"',
            *override_args(spec.cli_config),
            *sandbox_args(spec.role, spec.cwd, spec.env, self.home),
            "-C", str(spec.cwd),
            "--json",
            "-o", str(spec.cwd / LAST_MESSAGE_FILE),
            "--ignore-user-config",
            "--ephemeral",
            spec.prompt,
        ]

    def env_for(self, spec: LaunchSpec) -> dict[str, str]:
        return dict(spec.env)

    def launch(self, spec: LaunchSpec):
        prepared = LaunchSpec(**{**spec.__dict__, "env": self.env_for(spec)})
        if spec.role not in TRUSTED_ROLES:
            uv_cache_dir(prepared.env, self.home).mkdir(parents=True, exist_ok=True)   # --add-dir needs the directory to exist
        with open_log(spec.log_path) as log:
            return popen_detached(self.argv(prepared), prepared, log)

    def installed_skills(self) -> list[WorkerType]:
        return scan_skills([self.home / ".agents" / "skills", self.home / ".codex" / "skills"])

    def project_skills_dir(self) -> str | None:
        return ".agents/skills"

    def command_timeout_ceiling_seconds(self) -> int:
        return COMMAND_CEILING_SECONDS

    def supports_effort(self) -> bool:
        return True

    def version_argv(self) -> list[str]:
        return [self.binary, "--version"]

    def help_argv(self) -> list[str]:
        return [self.binary, "exec", "--help"]

    def required_flags(self) -> tuple[str, ...]:
        return REQUIRED_FLAGS

    def login_argv(self) -> list[str]:
        return [self.binary, "login", "status"]

    def login_status(self, stdout: str, stderr: str, returncode: int) -> tuple[bool | None, str]:
        """`codex login status` prints `Logged in using ...` or `Not logged in`; the words are the oracle, not the exit code."""
        text = ((stdout or "") + "\n" + (stderr or "")).strip()
        first = text.splitlines()[0].strip() if text else ""
        if first.lower().startswith("not logged in"):
            return False, "not logged in"
        if first.lower().startswith("logged in"):
            return True, first
        return None, f"unrecognised `login status` answer (exit {returncode})"

    def parse_events(self, line: str, now: str) -> list[Event]:
        """One `--json` line as events. `turn.started` and an in-progress message carry nothing."""
        doc, stripped = parse_doc(line)
        if doc is None:
            return limit_event(now, stripped) if is_limit(stripped) else raw_event(now, stripped)
        kind = doc.get("type")
        if kind == "thread.started":
            thread = str(doc.get("thread_id", ""))
            return [Event(seq=0, at=now, kind="run_started", text=cap(thread), meta={"thread_id": thread})]
        if kind == "turn.started":
            return []
        if kind == "turn.completed":
            usage = doc.get("usage") if isinstance(doc.get("usage"), dict) else {}
            return [Event(seq=0, at=now, kind="usage",
                          text=cap(", ".join(f"{k}={v}" for k, v in sorted(usage.items()))), meta={"usage": usage})]
        if kind == "turn.failed":
            error = doc.get("error") if isinstance(doc.get("error"), dict) else {}
            message = str(error.get("message", "turn failed"))
            if is_limit(message):
                return limit_event(now, message)
            return [Event(seq=0, at=now, kind="cli_error", ok=False, text=cap(message))]
        if kind in ("item.started", "item.completed", "item.updated"):
            item = doc.get("item") if isinstance(doc.get("item"), dict) else {}
            return _item(kind, item, now, stripped)
        return limit_event(now, stripped) if is_limit(stripped) else raw_event(now, stripped)


def _item(kind: str, item: dict, now: str, stripped: str) -> list[Event]:
    """One `item.*` event. Only the completed form of a message or a command carries its content."""
    item_type = item.get("type") or item.get("item_type")
    if item_type in ("agent_message", "reasoning"):
        if kind != "item.completed":
            return []
        event_kind = "message" if item_type == "agent_message" else "thinking"
        return [Event(seq=0, at=now, kind=event_kind, text=cap(item.get("text", "")))]
    if item_type == "command_execution":
        command = cap(item.get("command", ""))
        if kind == "item.started":
            return [Event(seq=0, at=now, kind="tool_call", tool="command", text=command,
                          meta={"id": str(item.get("id", ""))})]
        if kind == "item.updated":
            return []
        code = item.get("exit_code")
        return [Event(seq=0, at=now, kind="tool_result", tool="command", ok=(code == 0),
                      text=cap(item.get("aggregated_output", "")),
                      meta={"exit_code": code, "command": command, "id": str(item.get("id", ""))})]
    return raw_event(now, stripped)


ADAPTER = CodexAdapter()
