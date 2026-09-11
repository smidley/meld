"""OpenAI Codex CLI adapter: one unattended `codex exec` run per launch."""
from __future__ import annotations

from pathlib import Path

from meld.adapters.base import LaunchSpec, open_log, popen_detached, scan_skills
from meld.roster import WorkerType

LAST_MESSAGE_FILE = ".meld-last-message.txt"
COMMAND_CEILING_SECONDS = 600  # conservative; the exact Codex shell-command ceiling is verified by `meld doctor`
# `codex exec` never prompts, so it takes no approval flag (`-a` was removed from exec and made the first live run
# crash-loop). An orchestrator or PM runs `meld` itself -- GitHub over the network, git fetch and push for memory
# PRs -- so it gets the full-access sandbox; a worker keeps the workspace-write sandbox (its isolation is the
# worktree and the absence of credentials, spec 18) with network on, since `uv sync` and `pip` need it.
REQUIRED_FLAGS = ("-m", "-c", "-s", "-C", "--json", "-o", "--ignore-user-config", "--ephemeral")


def sandbox_args(role: str) -> list[str]:
    if role == "worker":
        return ["-s", "workspace-write", "-c", "sandbox_workspace_write.network_access=true"]
    return ["-s", "danger-full-access"]


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
            *sandbox_args(spec.role),
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


ADAPTER = CodexAdapter()
