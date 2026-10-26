"""A fake CLI adapter: spawns tiny python processes instead of claude/codex."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

from meld.adapters.base import LaunchSpec, is_limit, limit_event, open_log, popen_detached
from meld.roster import WorkerType
from meld.transcript import Event, cap

DEFAULT_SCRIPT = "import time; time.sleep(0.2)"


@dataclass
class FakeAdapter:
    script: str | None = None
    skills: list[WorkerType] = field(default_factory=list)
    ceiling: int = 600
    name: str = "fake"
    launches: list[LaunchSpec] = field(default_factory=list)
    login: bool | None = True   # what the fake CLI answers `login_argv`: True/False, or None for an answer nobody can read

    def launch(self, spec: LaunchSpec):
        self.launches.append(spec)
        with open_log(spec.log_path) as log:
            return popen_detached([sys.executable, "-c", self.script or DEFAULT_SCRIPT], spec, log)

    def installed_skills(self) -> list[WorkerType]:
        return list(self.skills)

    def project_skills_dir(self) -> str | None:
        return ".fake/skills"

    def command_timeout_ceiling_seconds(self) -> int:
        return self.ceiling

    def supports_effort(self) -> bool:
        return True

    def help_argv(self) -> list[str]:
        return []

    def required_flags(self) -> tuple[str, ...]:
        return ()

    def version_argv(self) -> list[str]:
        return [sys.executable, "--version"]

    def login_argv(self) -> list[str]:
        return [sys.executable, "-c", f"print({self.login!r})"]

    def login_status(self, stdout: str, stderr: str, returncode: int) -> tuple[bool | None, str]:
        word = stdout.strip()
        return {"True": True, "False": False}.get(word), {"True": "logged in", "False": "not logged in"}.get(word, "unrecognised answer")

    def parse_events(self, line: str, now: str) -> list[Event]:
        """The fake CLI writes plain text, so every line is one message -- except a line saying the quota is gone,
        which every real adapter turns into `usage_limit` and so must this one, or no test could drive a freeze."""
        if not line.strip():
            return []
        if is_limit(line):
            return limit_event(now, line)
        return [Event(seq=0, at=now, kind="message", text=cap(line))]


def no_skills(cli_name: str) -> FakeAdapter:
    return FakeAdapter()
