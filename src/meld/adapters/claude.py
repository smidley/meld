"""Claude Code adapter: one unattended `claude -p` run per launch."""
from __future__ import annotations

import json
from pathlib import Path

from meld.adapters.base import LaunchSpec, open_log, popen_detached, scan_skills
from meld.roster import WorkerType

_SETTINGS = {"disableAllHooks": True, "autoMemoryEnabled": False}
_MCP_CONFIG = {"mcpServers": {}}
_DROP_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")
BASH_TOOL_CEILING_SECONDS = 600  # Claude Code's Bash tool caps one command at 10 minutes


class ClaudeAdapter:
    name = "claude"

    def __init__(self, binary: str = "claude", home: Path | None = None):
        self.binary = binary
        self.home = home or Path.home() / ".claude"

    def argv(self, spec: LaunchSpec) -> list[str]:
        return [
            self.binary, "-p", spec.prompt,
            "--model", spec.model,
            "--output-format", "json",
            "--permission-mode", "bypassPermissions",
            "--strict-mcp-config", "--mcp-config", json.dumps(_MCP_CONFIG),
            "--settings", json.dumps(_SETTINGS),
            # user: installed skills (~/.claude/skills, plugin caches) load; hooks stay disabled and MCP stays empty via _SETTINGS/_MCP_CONFIG
            "--setting-sources", "user,project",
            "--max-turns", str(spec.max_turns),
        ]

    def env_for(self, spec: LaunchSpec) -> dict[str, str]:
        env = {k: v for k, v in spec.env.items() if k not in _DROP_ENV}
        env["CLAUDE_CODE_EFFORT_LEVEL"] = spec.effort
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        return env

    def launch(self, spec: LaunchSpec):
        prepared = LaunchSpec(**{**spec.__dict__, "env": self.env_for(spec)})
        with open_log(spec.log_path) as log:
            return popen_detached(self.argv(prepared), prepared, log)

    def installed_skills(self) -> list[WorkerType]:
        dirs = [self.home / "skills", *sorted((self.home / "plugins" / "cache").glob("*/*/*/skills"))]
        return scan_skills(dirs)

    def command_timeout_ceiling_seconds(self) -> int:
        return BASH_TOOL_CEILING_SECONDS

    def supports_effort(self) -> bool:
        return True


ADAPTER = ClaudeAdapter()
