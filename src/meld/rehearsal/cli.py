"""The stand-in AI CLIs of the rehearsal: a `claude` and a `codex` on PATH that the real adapters launch (plan 48).

Each answers what the adapter and the doctor ask of the real binary (`--version`, `--help` naming every flag meld
passes, the login probe), and a launch runs the scripted actor and writes the transcript in that CLI's own format:
Claude Code's `stream-json` lines, Codex's `--json` lines plus the `-o` last-message file. The adapters parse them
unchanged, so the viewer, the limits and the logs see what a real run produces.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import time
from pathlib import Path

from meld.rehearsal import actor

SCRIPT = "#!{python}\nfrom meld.rehearsal.cli import main\nmain({kind!r})\n"
CLAUDE_FLAGS = ("-p", "--model", "--output-format", "--verbose", "--permission-mode", "--strict-mcp-config", "--mcp-config", "--settings",
                "--setting-sources", "--max-turns")
CODEX_FLAGS = ("-m", "-c", "-s", "-C", "--json", "-o", "--ignore-user-config", "--ephemeral", "--add-dir")


def install(bin_dir: Path, python: str | None = None) -> dict[str, Path]:
    """Write `claude` and `codex` into `bin_dir`; the test prepends it to PATH."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for kind in ("claude", "codex"):
        path = bin_dir / kind
        path.write_text(SCRIPT.format(python=python or sys.executable, kind=kind))
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        out[kind] = path
    return out


def _launch_facts(prompt: str) -> tuple[int, str]:
    n = re.search(r"Launch number: (\d+)", prompt)
    r = re.search(r"Reason: `([a-z]+)`", prompt)
    return (int(n.group(1)) if n else 1), (r.group(1) if r else "start")


class ClaudeTranscript:
    def __init__(self, model: str):
        self.n = 0
        print(json.dumps({"type": "system", "subtype": "init", "model": model, "session_id": f"rehearsal-{os.getpid()}", "cwd": os.getcwd()}), flush=True)

    def say(self, text: str) -> None:
        self.n += 1
        print(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}), flush=True)

    def tool(self, argv: list[str], output: str, code: int) -> None:
        self.n += 1
        tid = f"toolu_{self.n}"
        print(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": " ".join(argv)}}]}}), flush=True)
        print(json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": output[:4000], "is_error": code != 0}]}}), flush=True)

    def capacity(self, text: str) -> None:
        print(json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": f"API Error: 529 {text}", "num_turns": self.n}), flush=True)

    def finish(self, text: str, ok: bool = True) -> None:
        print(json.dumps({"type": "result", "subtype": "success" if ok else "error_during_execution", "is_error": not ok, "result": text, "num_turns": self.n,
                          "total_cost_usd": 0.0, "usage": {"input_tokens": 100 * self.n, "output_tokens": 20 * self.n, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}), flush=True)


class CodexTranscript:
    def __init__(self, last_message: Path | None):
        self.n = 0
        self.last_message = last_message
        self.last = ""
        print(json.dumps({"type": "thread.started", "thread_id": f"rehearsal-{os.getpid()}"}), flush=True)
        print(json.dumps({"type": "turn.started"}), flush=True)

    def say(self, text: str) -> None:
        self.n += 1
        self.last = text
        print(json.dumps({"type": "item.completed", "item": {"id": f"item_{self.n}", "type": "agent_message", "text": text}}), flush=True)

    def tool(self, argv: list[str], output: str, code: int) -> None:
        self.n += 1
        item = {"id": f"item_{self.n}", "type": "command_execution", "command": " ".join(argv), "aggregated_output": output[:4000], "exit_code": code, "status": "completed"}
        print(json.dumps({"type": "item.started", "item": {**item, "exit_code": None, "status": "in_progress", "aggregated_output": ""}}), flush=True)
        print(json.dumps({"type": "item.completed", "item": item}), flush=True)

    def capacity(self, text: str) -> None:
        print(json.dumps({"type": "error", "message": text}), flush=True)
        print(json.dumps({"type": "turn.failed", "error": {"message": text}}), flush=True)

    def finish(self, text: str, ok: bool = True) -> None:
        self.say(text)
        print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100 * self.n, "cached_input_tokens": 0, "output_tokens": 20 * self.n}}), flush=True)
        if self.last_message is not None:
            self.last_message.write_text(self.last)


def _play(transcript, cwd: Path, prompt: str) -> None:
    say, tool = transcript.say, transcript.tool
    try:
        if os.environ.get("MELD_WORKER") == "1":
            mark = os.environ.get("MELD_REHEARSAL_CAPACITY_MARK")
            if mark and not Path(mark).exists():
                # The first worker launch dies the way Codex did on the first real run (plan 50): the mark file says it
                # happened once, so the retry the framework owes it goes through.
                Path(mark).write_text("the first worker launch died of capacity\n")
                transcript.capacity("Selected model is at capacity. Please try a different model.")
                sys.exit(1)
            outcome = actor.worker(cwd, say, tool)
        else:
            launch_no, reason = _launch_facts(prompt)
            meld = actor.Meld(cwd, say, tool)
            role = os.environ.get("MELD_ROLE", "orchestrator")
            outcome = actor.orchestrator(meld, launch_no=launch_no, reason=reason) if role == "orchestrator" else actor.pm(meld, launch_no=launch_no, reason=reason)
    except Exception as e:   # noqa: BLE001 -- the stand-in reports what broke the way a CLI reports an error result
        transcript.finish(f"{type(e).__name__}: {e}", ok=False)
        sys.exit(1)
    transcript.finish(f"session over: {outcome}")


def main(kind: str) -> None:
    argv = sys.argv[1:]
    if argv[:1] == ["--version"]:
        print(f"2.1.0 ({'Claude Code' if kind == 'claude' else 'codex-cli'} rehearsal stand-in)")
        sys.exit(0)
    if kind == "claude":
        if argv[:1] == ["--help"]:
            print("Usage: claude [options] [command]\n\nOptions:\n" + "\n".join(f"  {f} <value>" for f in CLAUDE_FLAGS))
            sys.exit(0)
        if argv[:2] == ["auth", "status"]:
            print(json.dumps({"loggedIn": True, "authMethod": "rehearsal", "email": "rehearsal@example.com"}))
            sys.exit(0)
        if "-p" in argv:
            prompt = argv[argv.index("-p") + 1]
            model = argv[argv.index("--model") + 1] if "--model" in argv else "claude"
            _play(ClaudeTranscript(model), Path.cwd(), prompt)
            sys.exit(0)
    else:
        if argv[:2] == ["exec", "--help"]:
            print("Run Codex non-interactively\n\nUsage: codex exec [OPTIONS] [PROMPT]\n\nOptions:\n" + "\n".join(f"  {f} <value>" for f in CODEX_FLAGS))
            sys.exit(0)
        if argv[:2] == ["login", "status"]:
            print("Logged in using ChatGPT (rehearsal stand-in)")
            sys.exit(0)
        if argv[:1] == ["exec"]:
            prompt = argv[-1]
            cwd = Path(argv[argv.index("-C") + 1]) if "-C" in argv else Path.cwd()
            last = Path(argv[argv.index("-o") + 1]) if "-o" in argv else None
            os.chdir(cwd)
            _play(CodexTranscript(last), cwd, prompt)
            sys.exit(0)
    sys.stderr.write(f"{kind} (rehearsal): unsupported invocation {' '.join(argv[:4])}\n")
    sys.exit(2)
