"""Adapter protocol: the only layer that knows how a particular CLI is launched."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterable, Mapping, Protocol

from meld.roster import WorkerType

SCRUBBED_EXACT = ("GH_TOKEN", "GITHUB_TOKEN", "MELD_GROUP", "MELD_ROLE", "MELD_PUSH_TOKEN")
SCRUBBED_PREFIXES = ("MELD_TOKEN_",)


@dataclass(frozen=True)
class LaunchSpec:
    role: str
    model: str
    effort: str
    cwd: Path
    prompt: str
    log_path: Path
    timeout_seconds: int
    env: dict[str, str]
    max_turns: int


class Adapter(Protocol):
    name: str

    def launch(self, spec: LaunchSpec) -> subprocess.Popen: ...

    def installed_skills(self) -> list[WorkerType]: ...

    def command_timeout_ceiling_seconds(self) -> int: ...

    def supports_effort(self) -> bool: ...


def scrub_env(env: Mapping[str, str]) -> dict[str, str]:
    """Environment for a worker: no GitHub credentials, no party identity, a marker that it is a worker."""
    out = {k: v for k, v in env.items() if k not in SCRUBBED_EXACT and not k.startswith(SCRUBBED_PREFIXES)}
    out["MELD_WORKER"] = "1"
    return out


def parse_frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def scan_skills(dirs: Iterable[Path], source: str = "skill") -> list[WorkerType]:
    found: dict[str, WorkerType] = {}
    for d in dirs:
        if not d.is_dir():
            continue
        for skill_dir in sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith(".")):
            f = skill_dir / "SKILL.md"
            if not f.is_file():
                continue
            text = f.read_text()
            meta = parse_frontmatter(text)
            name = meta.get("name") or skill_dir.name
            found.setdefault(name, WorkerType(name=name, description=meta.get("description", ""), source=source, path=f, text=text))
    return sorted(found.values(), key=lambda w: w.name)


def open_log(path: Path) -> IO[bytes]:
    path.parent.mkdir(parents=True, exist_ok=True)
    return open(path, "ab")


def popen_detached(argv: list[str], spec: LaunchSpec, log: IO[bytes]) -> subprocess.Popen:
    """Start the CLI in its own session so a timeout can kill the whole process group."""
    return subprocess.Popen(argv, cwd=str(spec.cwd), env=spec.env, stdin=subprocess.DEVNULL, stdout=log,
                            stderr=subprocess.STDOUT, start_new_session=True)
