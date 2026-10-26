"""Google Antigravity CLI adapter (`agy`): one `agy -p` print-mode run per launch, read as `--output-format stream-json`.

Verified against agy 1.4.7 on the owner's machine (2026-10-26). One JSON object per line with an `event`: `init`
(`conversation_id`, the cwd and tool list), `step_update` (a `step_update` with `step_index`, `state` ACTIVE/DONE/ERROR
and `step_type` -- `user_input`, `agent_response` carrying `text_delta` chunks, `tool` carrying `tool_name` and
`tool_info.parameters`, with `tool_info.error` on a failure -- plus `usage` and `duration_seconds` when a step ends)
and `result` (`status`, `response`, `num_turns`, `usage`, `denied_actions`). An agent's text arrives as deltas, so this
adapter joins them per (conversation, step) and emits one message when the step is DONE; that is the one piece of
state an adapter here keeps, keyed so two transcripts read by the same instance never mix.
"""
from __future__ import annotations

from pathlib import Path

from meld.adapters.base import LaunchSpec, is_limit, limit_event, open_log, parse_doc, popen_detached, raw_event, scan_skills
from meld.roster import WorkerType
from meld.transcript import Event, cap

COMMAND_CEILING_SECONDS = 600   # conservative; not documented
REQUIRED_FLAGS = ("-p", "--model", "--effort", "--output-format", "--dangerously-skip-permissions")


def _summary(params: dict) -> str:
    for key in ("CommandLine", "command", "AbsolutePath", "TargetFile", "file_path", "path", "Query", "query", "pattern"):
        if isinstance(params.get(key), str) and params[key]:
            return params[key]
    return next((v for v in params.values() if isinstance(v, str) and v), "")


class AntigravityAdapter:
    name = "antigravity"

    def __init__(self, binary: str = "agy", home: Path | None = None):
        self.binary = binary
        self.home = home or Path.home() / ".gemini" / "antigravity-cli"
        self._partial: dict[tuple[str, int], list[str]] = {}   # (conversation_id, step_index) -> text deltas so far
        # ponytail: a step that never reaches DONE stays buffered; bounded by one entry per abandoned step, cleared on DONE

    def argv(self, spec: LaunchSpec) -> list[str]:
        # Print mode cannot prompt, so a worker needs every tool approved up front; `--dangerously-skip-permissions` is
        # the CLI's own name for that, and the environment scrub (section 18) is the isolation, as for Claude Code.
        return [self.binary, "-p", spec.prompt, "--model", spec.model, "--effort", spec.effort,
                "--output-format", "stream-json", "--dangerously-skip-permissions"]

    def env_for(self, spec: LaunchSpec) -> dict[str, str]:
        return dict(spec.env)

    def launch(self, spec: LaunchSpec):
        prepared = LaunchSpec(**{**spec.__dict__, "env": self.env_for(spec)})
        with open_log(spec.log_path) as log:
            return popen_detached(self.argv(prepared), prepared, log)

    def installed_skills(self) -> list[WorkerType]:
        return scan_skills([self.home / "skills"])

    def project_skills_dir(self) -> str | None:
        return None

    def command_timeout_ceiling_seconds(self) -> int:
        return COMMAND_CEILING_SECONDS

    def supports_effort(self) -> bool:
        return True

    def version_argv(self) -> list[str]:
        return [self.binary, "--version"]

    def help_argv(self) -> list[str]:
        return [self.binary, "--help"]

    def required_flags(self) -> tuple[str, ...]:
        return REQUIRED_FLAGS

    def login_argv(self) -> list[str]:
        return [self.binary, "models"]   # lists models only when signed in

    def login_status(self, stdout: str, stderr: str, returncode: int) -> tuple[bool | None, str]:
        rows = [l for l in (stdout or "").splitlines() if "\t" in l]
        if returncode == 0 and rows:
            return True, f"logged in ({len(rows)} models offered)"
        text = ((stdout or "") + "\n" + (stderr or "")).lower()
        if "log in" in text or "login" in text or "sign in" in text or "unauthenticated" in text:
            return False, "not logged in"
        return None, f"unrecognised `models` answer (exit {returncode})"

    def parse_events(self, line: str, now: str) -> list[Event]:
        doc, stripped = parse_doc(line)
        if doc is None:
            return limit_event(now, stripped) if is_limit(stripped) else raw_event(now, stripped)
        event = doc.get("event")
        if event == "init":
            cid = str(doc.get("conversation_id", ""))
            return [Event(seq=0, at=now, kind="run_started", text=cap(cid), meta={"conversation_id": cid})]
        if event == "step_update":
            return self._step(doc.get("step_update") if isinstance(doc.get("step_update"), dict) else {}, now, stripped)
        if event == "result":
            r = doc.get("result") if isinstance(doc.get("result"), dict) else {}
            status = str(r.get("status", ""))
            text = str(r.get("response", "")) or status
            if status != "SUCCESS" and is_limit(text):
                return limit_event(now, text)
            return [Event(seq=0, at=now, kind="run_finished", ok=status == "SUCCESS", text=cap(text),
                          meta={"status": status, "num_turns": r.get("num_turns"), "usage": r.get("usage") if isinstance(r.get("usage"), dict) else {},
                                "denied_actions": r.get("denied_actions") or []})]
        return limit_event(now, stripped) if is_limit(stripped) else raw_event(now, stripped)

    def _step(self, s: dict, now: str, stripped: str) -> list[Event]:
        kind, state = str(s.get("step_type", "")), str(s.get("state", ""))
        key = (str(s.get("conversation_id", "")), int(s.get("step_index", 0) or 0))
        if kind == "user_input":
            return []
        if kind == "agent_response":
            if isinstance(s.get("text_delta"), str):
                self._partial.setdefault(key, []).append(s["text_delta"])
            if state != "DONE":
                return []
            text = "".join(self._partial.pop(key, []))
            usage = s.get("usage") if isinstance(s.get("usage"), dict) else {}
            events = [Event(seq=0, at=now, kind="message", text=cap(text))] if text.strip() else []
            if usage:
                events.append(Event(seq=0, at=now, kind="usage", text=cap(", ".join(f"{k}={v}" for k, v in sorted(usage.items()))), meta={"usage": usage}))
            return events
        if kind == "tool":
            info = s.get("tool_info") if isinstance(s.get("tool_info"), dict) else {}
            params = info.get("parameters") if isinstance(info.get("parameters"), dict) else {}
            tool = cap(str(s.get("tool_name") or info.get("name") or "tool"))
            if state == "ACTIVE":
                return [Event(seq=0, at=now, kind="tool_call", tool=tool, text=cap(_summary(params)), meta={"step": key[1]})]
            error = info.get("error") if isinstance(info.get("error"), dict) else None
            output = error.get("message", "") if error else next((str(info[k]) for k in ("result", "output", "content") if info.get(k)), "")
            return [Event(seq=0, at=now, kind="tool_result", tool=tool, ok=state != "ERROR" and not error, text=cap(str(output)), meta={"step": key[1]})]
        return raw_event(now, stripped)


ADAPTER = AntigravityAdapter()
