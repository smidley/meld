"""Claude Code adapter: one unattended `claude -p` run per launch."""
from __future__ import annotations

import json
from pathlib import Path

from meld.adapters.base import SCRUBBED_EXACT, LaunchSpec, open_log, popen_detached, scan_skills
from meld.roster import WorkerType

# `--setting-sources user,project` loads the user's and the product's settings.json, whose `env` maps are applied to
# the session; `--settings` is merged last, so blanking a scrubbed variable name here means neither file can hand the
# session a value worker_env just removed. Credentials are blanked for every launch. The party identity
# (MELD_GROUP/MELD_ROLE) is blanked for WORKERS only: an orchestrator or PM session IS its party -- runners.role_env
# sets those two names on purpose, and blanking them turned every `meld` call the role made into a `[human]` one
# (found on the first live run). `MELD_TOKEN_*` names are unknown to the adapter (the token_env is per group, in
# .meld.toml), so a settings file naming one is a `meld doctor` warning (plan 4).
_SETTINGS_BASE = {"disableAllHooks": True, "autoMemoryEnabled": False}
_IDENTITY = ("MELD_GROUP", "MELD_ROLE")


def settings_for(role: str) -> dict:
    blank = SCRUBBED_EXACT if role == "worker" else tuple(name for name in SCRUBBED_EXACT if name not in _IDENTITY)
    return {**_SETTINGS_BASE, "env": {name: "" for name in blank}}


# The long flags below that `claude --help` lists; `meld doctor` checks each is still known to the installed CLI.
REQUIRED_FLAGS = ("--model", "--output-format", "--permission-mode", "--strict-mcp-config", "--mcp-config", "--settings", "--setting-sources")
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
            "--settings", json.dumps(settings_for(spec.role)),
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

    def project_skills_dir(self) -> str | None:
        return ".claude/skills"

    def command_timeout_ceiling_seconds(self) -> int:
        return BASH_TOOL_CEILING_SECONDS

    def supports_effort(self) -> bool:
        return True

    def version_argv(self) -> list[str]:
        return [self.binary, "--version"]

    def help_argv(self) -> list[str]:
        return [self.binary, "--help"]

    def required_flags(self) -> tuple[str, ...]:
        return REQUIRED_FLAGS


ADAPTER = ClaudeAdapter()
