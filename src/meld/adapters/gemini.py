"""Google Gemini CLI adapter: one headless `gemini -p` run per launch, read as `--output-format stream-json`.

Built from the CLI's published headless reference (geminicli.com/docs/cli/headless, cli-reference) rather than from a
run on this machine: the event names and fields are the documented ones (`init`, `message`, `tool_use`, `tool_result`,
`error`, `result`), and `meld doctor`'s `cli-flags` check proves the flags against the installed `--help` before a run.
Gemini has no per-run effort setting, so `supports_effort` is false and the catalogue lists one effort, `default`.
"""
from __future__ import annotations

from pathlib import Path

from meld.adapters.base import LaunchSpec, is_limit, limit_event, open_log, parse_doc, popen_detached, raw_event, scan_skills
from meld.roster import WorkerType
from meld.transcript import Event, cap

COMMAND_CEILING_SECONDS = 600   # conservative: the shell tool's own ceiling is not documented; `meld doctor` compares it to polling
REQUIRED_FLAGS = ("-p", "-m", "--output-format", "--approval-mode")


class GeminiAdapter:
    name = "gemini"

    def __init__(self, binary: str = "gemini", home: Path | None = None):
        self.binary = binary
        self.home = home or Path.home() / ".gemini"

    def argv(self, spec: LaunchSpec) -> list[str]:
        # `--approval-mode yolo` is the documented unattended form (`--yolo` is its deprecated alias); the workspace-trust
        # prompt that would otherwise block a fresh worktree is answered by GEMINI_CLI_TRUST_WORKSPACE in env_for.
        return [self.binary, "-p", spec.prompt, "-m", spec.model, "--output-format", "stream-json", "--approval-mode", "yolo"]

    def env_for(self, spec: LaunchSpec) -> dict[str, str]:
        return {**spec.env, "GEMINI_CLI_TRUST_WORKSPACE": "1"}

    def launch(self, spec: LaunchSpec):
        prepared = LaunchSpec(**{**spec.__dict__, "env": self.env_for(spec)})
        with open_log(spec.log_path) as log:
            return popen_detached(self.argv(prepared), prepared, log)

    def installed_skills(self) -> list[WorkerType]:
        return scan_skills([self.home / "skills"])

    def project_skills_dir(self) -> str | None:
        return ".gemini/skills"

    def command_timeout_ceiling_seconds(self) -> int:
        return COMMAND_CEILING_SECONDS

    def supports_effort(self) -> bool:
        return False

    def version_argv(self) -> list[str]:
        return [self.binary, "--version"]

    def help_argv(self) -> list[str]:
        return [self.binary, "--help"]

    def required_flags(self) -> tuple[str, ...]:
        return REQUIRED_FLAGS

    def login_argv(self) -> list[str]:
        return []   # the CLI has no "am I logged in" command; a missing key surfaces as an `error` event on the first run

    def login_status(self, stdout: str, stderr: str, returncode: int) -> tuple[bool | None, str]:
        return None, "this adapter has no login probe"

    def parse_events(self, line: str, now: str) -> list[Event]:
        """One `stream-json` line as events. The user's own message (the prompt echoed back) carries nothing."""
        doc, stripped = parse_doc(line)
        if doc is None:
            return limit_event(now, stripped) if is_limit(stripped) else raw_event(now, stripped)
        kind = doc.get("type")
        if kind == "init":
            model = str(doc.get("model", ""))
            return [Event(seq=0, at=now, kind="run_started", text=cap(model), meta={"session_id": str(doc.get("session_id", "")), "model": model})]
        if kind == "message":
            if doc.get("role") == "user":
                return []
            return [Event(seq=0, at=now, kind="message", text=cap(str(doc.get("content", ""))), meta={"delta": bool(doc.get("delta", False))})]
        if kind == "tool_use":
            params = doc.get("parameters") if isinstance(doc.get("parameters"), dict) else {}
            summary = params.get("command") or params.get("file_path") or params.get("path") or params.get("pattern") or params.get("description") or ""
            return [Event(seq=0, at=now, kind="tool_call", tool=cap(str(doc.get("tool_name", "tool"))), text=cap(str(summary)),
                          meta={"id": str(doc.get("tool_id", ""))})]
        if kind == "tool_result":
            return [Event(seq=0, at=now, kind="tool_result", ok=doc.get("status") == "success", text=cap(str(doc.get("output", ""))),
                          meta={"id": str(doc.get("tool_id", "")), "status": str(doc.get("status", ""))})]
        if kind == "error":
            message = str(doc.get("message", "error"))
            return limit_event(now, message) if is_limit(message) else [Event(seq=0, at=now, kind="cli_error", ok=False, text=cap(message))]
        if kind == "result":
            stats = doc.get("stats") if isinstance(doc.get("stats"), dict) else {}
            status = str(doc.get("status", ""))
            return [Event(seq=0, at=now, kind="run_finished", ok=status == "success", text=cap(status), meta={"status": status, "usage": stats})]
        return limit_event(now, stripped) if is_limit(stripped) else raw_event(now, stripped)


ADAPTER = GeminiAdapter()
