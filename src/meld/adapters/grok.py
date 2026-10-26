"""xAI Grok Build adapter: one headless `grok -p` run per launch, read as `--output-format streaming-messages-json`.

Verified against grok 1.0.70 on the owner's machine (2026-10-26): `streaming-messages-json` is Claude Code's stream-json
wire format line for line -- a `system`/`init` line, `assistant` and `user` messages carrying `thinking`, `text`,
`tool_use` and `tool_result` blocks, a `result` with `is_error`, `subtype`, `num_turns`, `total_cost_usd` and `usage`
-- so this adapter parses with the Claude adapter's own parser rather than a second copy of it. (`streaming-json` is
ACP session updates with one token per line, which no stateless line parser can turn into a readable transcript.)
`--no-auto-update` is accepted but undocumented in `--help`, so it is passed and not among the flags doctor checks.
"""
from __future__ import annotations

import json
from pathlib import Path

from meld.adapters.base import LaunchSpec, open_log, popen_detached, scan_skills
from meld.adapters.claude import ClaudeAdapter
from meld.roster import WorkerType
from meld.transcript import Event, cap

COMMAND_CEILING_SECONDS = 600   # conservative; not documented
REQUIRED_FLAGS = ("-p", "-m", "--effort", "--output-format", "--always-approve", "--cwd", "--max-turns")


class GrokAdapter:
    name = "grok"

    def __init__(self, binary: str = "grok", home: Path | None = None):
        self.binary = binary
        self.home = home or Path.home() / ".grok"
        self._parser = ClaudeAdapter()

    def argv(self, spec: LaunchSpec) -> list[str]:
        return [self.binary, "--no-auto-update", "-p", spec.prompt, "-m", spec.model, "--effort", spec.effort,
                "--output-format", "streaming-messages-json", "--always-approve", "--cwd", str(spec.cwd), "--max-turns", str(spec.max_turns)]

    def env_for(self, spec: LaunchSpec) -> dict[str, str]:
        return dict(spec.env)

    def launch(self, spec: LaunchSpec):
        prepared = LaunchSpec(**{**spec.__dict__, "env": self.env_for(spec)})
        with open_log(spec.log_path) as log:
            return popen_detached(self.argv(prepared), prepared, log)

    def installed_skills(self) -> list[WorkerType]:
        return scan_skills([self.home / "skills"])   # Grok Build's plugins are not skills; a skills directory, if one exists, is

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
        return [self.binary, "models"]   # "You are logged in with grok.com." then the list; nothing else reports the state

    def login_status(self, stdout: str, stderr: str, returncode: int) -> tuple[bool | None, str]:
        text = ((stdout or "") + "\n" + (stderr or "")).strip()
        first = text.splitlines()[0].strip() if text else ""
        if first.lower().startswith("you are logged in"):
            return True, first
        if "not logged in" in text.lower() or "log in" in first.lower():
            return False, "not logged in"
        return None, f"unrecognised `models` answer (exit {returncode})"

    def parse_events(self, line: str, now: str) -> list[Event]:
        return [_humanise(e) for e in self._parser.parse_events(line, now)]


def _humanise(event: Event) -> Event:
    """Grok wraps a tool's result in its own envelope -- `{"type":"Bash","output":[69,114,...]}` with the output as a byte
    array, `{"type":"ReadFile","FileContent":{"content":...}}`, `{"type":"ListDir","Content":{"content":...}}` -- which the
    Claude parser keeps as JSON text, so a transcript showed numbers where a command's output should be. Unwrap the
    shapes seen on the owner's machine; anything else stays as it came."""
    if event.kind != "tool_result" or not event.text.startswith("{"):
        return event
    try:
        doc = json.loads(event.text)
    except ValueError:
        return event
    if not isinstance(doc, dict):
        return event
    text: str | None = None
    out = doc.get("output")
    if isinstance(out, list) and all(isinstance(b, int) and 0 <= b < 256 for b in out):
        text = bytes(out).decode("utf-8", errors="replace")
    elif isinstance(out, str):
        text = out
    else:
        for key in ("FileContent", "Content", "EditsApplied"):
            inner = doc.get(key)
            if isinstance(inner, dict):
                text = next((str(inner[k]) for k in ("content", "tool_output_for_prompt", "new_string") if inner.get(k)), None)
                break
    if text is None:
        return event
    return Event(seq=event.seq, at=event.at, kind=event.kind, text=cap(text), tool=event.tool, ok=event.ok, meta={**event.meta, "envelope": str(doc.get("type", ""))})


ADAPTER = GrokAdapter()
