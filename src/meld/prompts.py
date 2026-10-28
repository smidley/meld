"""Render the long-lived role prompts from the packaged markdown plus the group's configuration."""
from __future__ import annotations

from importlib import resources
from pathlib import Path

from meld.brief import product_standards, product_context
from meld.config import Config, GroupConfig
from meld.roster import WorkerType, roster

ROLES = ("orchestrator", "pm")
LAUNCH_REASONS = ("start", "exit", "crash", "recycle", "limit", "capacity")
_REASON_TEXT = {
    "start": "the supervisor has just started; this is the first session of this role",
    "exit": "the previous session of this role finished its turn and exited normally; this is routine",
    "crash": "the previous session ended abnormally (a non-zero exit, or it exited within a minute of starting)",
    "recycle": "the previous session was recycled on the restart schedule (runners.restart_hours)",
    "limit": "the previous session ended because this account's quota ran out; the supervisor froze the "
             "role and is relaunching now that the window has passed",
    "capacity": "the previous session ended because the model was at capacity (busy, not out of quota); the supervisor "
                "paused a minute and is relaunching; nothing of yours failed",
}
COMMANDS = ("meld status", "meld wait --filter <kinds> [--timeout S]", "meld post VERB [REF] -m TEXT [--joint]", "meld claim N",
            "meld release N", "meld dispatch N [--kind review|audit] [--worker security]", "meld tick", "meld collect N",
            "meld review N", "meld merge N", "meld audit N", "meld reopen N", "meld reap N",
            "meld challenge <open|evidence|retract|uphold|dismiss> REF -m TEXT",
            "meld tag N [--worker W] [--model M] [--effort E] [--size S]", "meld roster", "meld models",
            "meld decide REF", "meld remember REF -m TEXT", "meld recall QUERY",
            "meld plan issues REF [--dry-run]", "meld plan approve", "meld plan ack REF",
            "meld inbox", "meld chat [--once TEXT]", "meld notify [--dry-run]",
            "meld setup REPO --goal TEXT [--milestone TITLE] [--pr]", "meld doctor [--probe-status] [--sweep --dry-run]")


def _read(name: str) -> str:
    return resources.files("meld").joinpath("prompts", name).read_text()


def launch_block(launch_no: int, reason: str) -> list[str]:
    """Tell a role which launch this is and why, so it can decide what to announce (see orchestrator.md and pm.md)."""
    if reason not in LAUNCH_REASONS:
        raise ValueError(f"reason must be one of {', '.join(LAUNCH_REASONS)}, got {reason!r}")
    if launch_no < 1:
        raise ValueError(f"launch_no must be at least 1, got {launch_no}")
    return ["## This launch", "",
            f"- Launch number: {launch_no}" + (" (the first launch under this supervisor)" if launch_no == 1 else ""),
            f"- Reason: `{reason}` ({_REASON_TEXT[reason]})"]


def continue_prompt(launch_no: int, reason: str) -> str:
    """What a resumed session is told (plan 55): the launch block and nothing else, since everything else is still in its
    context. The role prompt costs ten thousand tokens a launch; this costs fifty."""
    return "\n".join([*launch_block(launch_no, reason), "",
                      "This is your own previous session, continued: your context, the board and your loop are as you left them. "
                      "Run `meld status` and carry on; post `RESUMED` only when the reason above says so."])


def role_prompt(role: str, cfg: Config, group: GroupConfig, repo_root: Path, *, roster_entries: list[WorkerType] | None = None,
                launch_no: int = 1, reason: str = "start") -> str:
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}, got {role!r}")
    own = group.orchestrator if role == "orchestrator" else group.pm
    others = [g for g in cfg.groups.values() if g.name != group.name]
    lines = [
        "## Your configuration", "",
        f"- Repository: `{cfg.repo_name}` (default branch `{cfg.default_branch}`)",
        f"- Your group: **{group.name}**, bot `{group.bot}`; you speak as `[{group.name}]` (orchestrator) or `[{group.name}:pm]` (PM)",
        "- Other groups: " + (", ".join(f"{g.name} (bot `{g.bot}`)" for g in others) or "none"),
        "- Humans: " + (", ".join(f"`{h}`" for h in cfg.humans) or "none listed"),
        f"- You run as `{own.model}` at effort {own.effort}",
        f"- Caps: concurrency {group.concurrency}, review concurrency {group.review_concurrency}; review quorum {cfg.effective_quorum}",
        f"- Cadences: poll every {cfg.poll_interval_seconds}s, wait timeout {cfg.wait_timeout_seconds}s, restart every {cfg.restart_hours}h, "
        f"roster refresh every {cfg.roster_refresh_minutes}m, silence grace {cfg.silence_grace_minutes}m, status summary every {cfg.summary_cadence_minutes}m",
        f"- Worker timeouts: S {cfg.timeouts['S']}, M {cfg.timeouts['M']}, L {cfg.timeouts['L']}; amend cap {cfg.amend_cap}; ping-pong cap {cfg.pingpong_cap}",
        "- Budget (worker runs per tier this milestone): " + ", ".join(f"{k} {v}" for k, v in cfg.budget.items()),
        "", "## Product standards", "", product_standards(repo_root),
        *(["", "## Product context", "", context, "", "Plan on what exists: read the files this names before proposing or claiming work."] if (context := product_context(repo_root)) else []),
        "", "## Your roster", "",
    ]
    entries = roster_entries if roster_entries is not None else roster(repo_root)
    lines += [f"- `{w.name}` ({w.source}): {w.description}" for w in entries]
    lines += ["", "## Commands available", ""] + [f"- `{c}`" for c in COMMANDS]
    lines += ["", *launch_block(launch_no, reason)]
    return "\n".join([_read("_preamble.md").strip(), "", _read(f"{role}.md").strip(), "", *lines, ""])
