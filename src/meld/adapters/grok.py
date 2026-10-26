"""xAI Grok Build adapter: one headless `grok -p` run per launch, read as `--output-format streaming-json`.

Built from xAI's published CLI reference and headless-scripting page (docs.x.ai/build/cli) rather than from a run on
this machine. The flags are the documented ones and `meld doctor`'s `cli-flags` check proves them against the installed
`--help`. The streaming event vocabulary is only partly documented (`text`, `thought`, `tool_call`, `tool_call_update`,
`usage`, `end`, `error` are the names third-party captures report), so every field read here is read defensively and an
unrecognised line becomes a `raw` event rather than nothing: the transcript stays complete even where the guess is wrong.
"""
from __future__ import annotations

from pathlib import Path

from meld.adapters.base import LaunchSpec, is_limit, limit_event, open_log, parse_doc, popen_detached, raw_event
from meld.roster import WorkerType
from meld.transcript import Event, cap

COMMAND_CEILING_SECONDS = 600   # conservative; not documented
REQUIRED_FLAGS = ("-p", "-m", "--effort", "--output-format", "--always-approve", "--cwd", "--max-turns", "--no-auto-update")


def _first(doc: dict, *keys: str) -> str:
    for k in keys:
        v = doc.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


class GrokAdapter:
    name = "grok"

    def __init__(self, binary: str = "grok", home: Path | None = None):
        self.binary = binary
        self.home = home or Path.home() / ".grok"

    def argv(self, spec: LaunchSpec) -> list[str]:
        return [self.binary, "--no-auto-update", "-p", spec.prompt, "-m", spec.model, "--effort", spec.effort,
                "--output-format", "streaming-json", "--always-approve", "--cwd", str(spec.cwd), "--max-turns", str(spec.max_turns)]

    def env_for(self, spec: LaunchSpec) -> dict[str, str]:
        return dict(spec.env)

    def launch(self, spec: LaunchSpec):
        prepared = LaunchSpec(**{**spec.__dict__, "env": self.env_for(spec)})
        with open_log(spec.log_path) as log:
            return popen_detached(self.argv(prepared), prepared, log)

    def installed_skills(self) -> list[WorkerType]:
        return []   # Grok Build has plugins, not SKILL.md directories; nothing to scan yet

    def project_skills_dir(self) -> str | None:
        return None

    def command_timeout_ceiling_seconds(self) -> int:
        return COMMAND_CEILING_SECONDS

    def supports_effort(self) -> bool:
        return True

    def version_argv(self) -> list[str]:
        return [self.binary, "version"]

    def help_argv(self) -> list[str]:
        return [self.binary, "--help"]

    def required_flags(self) -> tuple[str, ...]:
        return REQUIRED_FLAGS

    def login_argv(self) -> list[str]:
        return []   # `grok login` signs in; nothing reports the state. XAI_API_KEY in the environment is the usual headless answer.

    def login_status(self, stdout: str, stderr: str, returncode: int) -> tuple[bool | None, str]:
        return None, "this adapter has no login probe"

    def parse_events(self, line: str, now: str) -> list[Event]:
        """One `streaming-json` line as events, read by its `type`."""
        doc, stripped = parse_doc(line)
        if doc is None:
            return limit_event(now, stripped) if is_limit(stripped) else raw_event(now, stripped)
        kind = doc.get("type")
        if kind == "text":
            return [Event(seq=0, at=now, kind="message", text=cap(_first(doc, "text", "content")))]
        if kind == "thought":
            return [Event(seq=0, at=now, kind="thinking", text=cap(_first(doc, "text", "content", "thought")))]
        if kind == "tool_call":
            args = next((doc[k] for k in ("input", "arguments", "args", "parameters") if isinstance(doc.get(k), dict)), {})
            summary = args.get("command") or args.get("file_path") or args.get("path") or args.get("pattern") or _first(doc, "title", "description")
            return [Event(seq=0, at=now, kind="tool_call", tool=cap(_first(doc, "name", "tool", "tool_name") or "tool"), text=cap(str(summary)),
                          meta={"id": _first(doc, "id", "tool_call_id", "call_id")})]
        if kind == "tool_call_update":
            status = _first(doc, "status", "state").lower()
            if status in ("", "running", "in_progress", "pending"):
                return []
            output = _first(doc, "output", "content", "result", "error")
            return [Event(seq=0, at=now, kind="tool_result", tool=cap(_first(doc, "name", "tool", "tool_name")) or None,
                          ok=status not in ("failed", "error", "cancelled"), text=cap(output),
                          meta={"id": _first(doc, "id", "tool_call_id", "call_id"), "status": status})]
        if kind == "usage":
            usage = {k: v for k, v in doc.items() if k != "type" and isinstance(v, (int, float))}
            return [Event(seq=0, at=now, kind="usage", text=cap(", ".join(f"{k}={v}" for k, v in sorted(usage.items()))), meta={"usage": usage})]
        if kind == "end":
            reason = _first(doc, "stop_reason", "stopReason", "reason")
            return [Event(seq=0, at=now, kind="run_finished", ok=reason not in ("error", "failed"), text=cap(reason), meta={"stop_reason": reason})]
        if kind == "error":
            message = _first(doc, "message", "error", "text") or "error"
            return limit_event(now, message) if is_limit(message) else [Event(seq=0, at=now, kind="cli_error", ok=False, text=cap(message))]
        return limit_event(now, stripped) if is_limit(stripped) else raw_event(now, stripped)


ADAPTER = GrokAdapter()
