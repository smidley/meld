"""Claude Code adapter: one unattended `claude -p` run per launch."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from meld.adapters.base import (SCRUBBED_EXACT, LaunchSpec, capacity_event, is_capacity, is_limit, limit_event, open_log, parse_doc,
                                popen_detached, raw_event, scan_skills)
from meld.roster import WorkerType
from meld.transcript import Event, cap

# `--setting-sources user,project` loads the user's and the product's settings.json, whose `env` maps are applied to
# the session; `--settings` is merged last, so blanking a scrubbed variable name here means neither file can hand the
# session a value worker_env just removed. Credentials are blanked for every launch. The party identity
# (MELD_GROUP/MELD_ROLE) is blanked for WORKERS only: an orchestrator or PM session IS its party -- runners.role_env
# sets those two names on purpose, and blanking them turned every `meld` call the role made into a `[human]` one
# (found on the first live run). `MELD_TOKEN_*` names are unknown to the adapter (the token_env is per group, in
# .meld.toml), so a settings file naming one is a `meld doctor` warning (plan 4).
_SETTINGS_BASE = {"disableAllHooks": True, "autoMemoryEnabled": False}
_IDENTITY = ("MELD_GROUP", "MELD_ROLE")
TRUSTED_ROLES = ("orchestrator", "pm")


def settings_for(role: str, env: Mapping[str, str]) -> dict:
    """The `--settings` document for one launch. Credentials are blanked for every launch. For an orchestrator or PM the
    party identity is PINNED to the value runners.role_env set (a settings.json `env` map is applied to the session and
    `--settings` is merged last, so pinning -- not omitting -- is what stops a user or product settings file from
    re-pointing a credentialed session at another group); any other role is treated as a worker and the identity is
    blanked. Unknown roles fail closed."""
    blanks = {name: "" for name in SCRUBBED_EXACT}
    if role in TRUSTED_ROLES:
        blanks.update({name: env.get(name, "") for name in _IDENTITY})
    return {**_SETTINGS_BASE, "env": blanks}


# The flags below that `claude --help` lists; `meld doctor` checks each is still known to the installed CLI. `--max-turns`
# is passed too but is undocumented in `--help` (it works; anthropics/claude-code#16963), so it cannot be verified this way.
REQUIRED_FLAGS = ("-p", "--model", "--output-format", "--verbose", "--permission-mode", "--strict-mcp-config", "--mcp-config", "--settings", "--setting-sources")
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
            "--output-format", "stream-json",
            "--verbose",                       # claude -p refuses stream-json without it
            "--permission-mode", "bypassPermissions",
            "--strict-mcp-config", "--mcp-config", json.dumps(_MCP_CONFIG),
            # The group's own settings go UNDER the framework's: hooks stay disabled, memory off, credentials blanked.
            "--settings", json.dumps({**spec.cli_config, **settings_for(spec.role, spec.env)}),
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

    def login_argv(self) -> list[str]:
        return [self.binary, "auth", "status", "--json"]

    def login_status(self, stdout: str, stderr: str, returncode: int) -> tuple[bool | None, str]:
        """`claude auth status --json` answers `{"loggedIn": bool, "authMethod": ..., ...}`; that one key is the oracle."""
        try:
            doc = json.loads(stdout or "")
        except ValueError:
            doc = None
        if not isinstance(doc, dict) or not isinstance(doc.get("loggedIn"), bool):
            return None, f"unrecognised `auth status --json` answer (exit {returncode})"
        if doc["loggedIn"]:
            return True, f"logged in ({doc.get('authMethod') or 'unknown method'})"
        return False, "not logged in"

    def parse_events(self, line: str, now: str) -> list[Event]:
        """One `stream-json` line as events. A content array is many events, which is why this is plural."""
        doc, stripped = parse_doc(line)
        if doc is None:
            return limit_event(now, stripped) if is_limit(stripped) else raw_event(now, stripped)
        kind = doc.get("type")
        if kind == "system":
            if doc.get("subtype") != "init":
                return []
            return [Event(seq=0, at=now, kind="run_started", text=cap(doc.get("model", "")),
                          meta={"session_id": str(doc.get("session_id", "")), "model": str(doc.get("model", ""))})]
        if kind == "stream_event":
            return []                                  # only present with --include-partial-messages
        if kind in ("assistant", "user"):
            message = doc.get("message")
            blocks = message.get("content") if isinstance(message, dict) else None
            if isinstance(blocks, str):                # a plain-string content field
                return [Event(seq=0, at=now, kind="message", text=cap(blocks))]
            if not isinstance(blocks, list):
                return raw_event(now, stripped)
            events = [e for b in blocks if isinstance(b, dict) for e in _block(b, now)]
            # A new content block type inside an existing `assistant` line is how every Claude Code addition
            # has arrived (`thinking`, `redacted_thinking`, `server_tool_use`, `mcp_tool_use`). If NOTHING on
            # the line was understood, the line is unrecognized and falls back to `raw` -- Codex's unknown
            # `item_type` does the same. Without this the line produced no event at all and a person watching
            # saw an unexplained gap. A line with one known block and one unknown keeps only the known event:
            # the line WAS understood, so rendering the whole of it as `raw` would duplicate that.
            return events or raw_event(now, stripped)
        if kind == "result":
            usage = doc.get("usage") if isinstance(doc.get("usage"), dict) else {}
            # The model's own narration never triggers a limit -- only what the CLI itself reports as an
            # error may. A successful result's text is the model talking about its own work (it can say
            # "429" or "the rate limit for GitHub's API" while doing so), so is_limit is consulted only when
            # the CLI marked the run an error; a clean result is always an ordinary run_finished.
            if doc.get("is_error", False):
                said = str(doc.get("result", "")) + " " + str(doc.get("subtype", ""))
                if is_limit(said):
                    return limit_event(now, str(doc.get("result", "")) or str(doc.get("subtype", "")))
                if is_capacity(said):
                    return capacity_event(now, str(doc.get("result", "")) or str(doc.get("subtype", "")))   # 529 overloaded (plan 50)
            return [Event(seq=0, at=now, kind="run_finished", ok=not doc.get("is_error", False),
                          text=cap(doc.get("result", doc.get("subtype", ""))),
                          meta={"subtype": str(doc.get("subtype", "")), "num_turns": doc.get("num_turns"),
                                "cost_usd": doc.get("total_cost_usd"), "usage": usage})]
        return limit_event(now, stripped) if is_limit(stripped) else raw_event(now, stripped)


def _block(b: dict, now: str) -> list[Event]:
    """One content block as zero or one event. An unknown block type yields nothing here; the caller renders
    the line as `raw` when that leaves the whole line empty, and otherwise keeps only what it understood."""
    kind = b.get("type")
    if kind == "text":
        return [Event(seq=0, at=now, kind="message", text=cap(b.get("text", "")))]
    if kind == "thinking":
        return [Event(seq=0, at=now, kind="thinking", text=cap(b.get("thinking", "")))]
    if kind == "tool_use":
        args = b.get("input") if isinstance(b.get("input"), dict) else {}
        summary = args.get("command") or args.get("file_path") or args.get("pattern") or args.get("description") or ""
        return [Event(seq=0, at=now, kind="tool_call", tool=cap(b.get("name", "tool")), text=cap(summary),
                      meta={"id": str(b.get("id", ""))})]
    if kind == "tool_result":
        content = b.get("content")
        if isinstance(content, list):
            content = " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
        return [Event(seq=0, at=now, kind="tool_result", ok=not b.get("is_error", False), text=cap(content),
                      meta={"id": str(b.get("tool_use_id", ""))})]
    return []


ADAPTER = ClaudeAdapter()
