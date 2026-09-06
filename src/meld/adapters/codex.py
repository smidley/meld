"""OpenAI Codex CLI adapter: one unattended `codex exec` run per launch."""
from __future__ import annotations

from pathlib import Path

from meld.adapters.base import LaunchSpec, open_log, popen_detached, scan_skills
from meld.roster import WorkerType

LAST_MESSAGE_FILE = ".meld-last-message.txt"
COMMAND_CEILING_SECONDS = 600  # conservative; the exact Codex shell-command ceiling is verified by `meld doctor`


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
            "-s", "workspace-write",
            "-a", "never",
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


ADAPTER = CodexAdapter()
