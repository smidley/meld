"""Load .moot.toml into a typed Config, and identify the calling Party."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

# A group name becomes a party label, so it must not collide with a reserved party and
# must parse as one in a coordination header.
RESERVED_PARTY_NAMES = ("human", "joint")
GROUP_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ConfigError(ValueError):
    """Raised for a missing or invalid configuration value."""


@dataclass(frozen=True)
class RoleConfig:
    model: str
    effort: str


@dataclass(frozen=True)
class GroupConfig:
    name: str
    cli: str
    bot: str
    token_env: str
    concurrency: int
    review_concurrency: int
    orchestrator: RoleConfig
    pm: RoleConfig


def _require(data: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in data:
        raise ConfigError(f"missing required setting {path}.{key}")
    return data[key]


def _role(data: Mapping[str, Any], path: str) -> RoleConfig:
    return RoleConfig(model=str(_require(data, "model", path)), effort=str(_require(data, "effort", path)))


@dataclass(frozen=True)
class Config:
    repo_name: str
    default_branch: str
    private: bool
    humans: tuple[str, ...]
    groups: dict[str, GroupConfig]
    review_quorum: int
    pingpong_cap: int
    security_paths: tuple[str, ...]
    amend_cap: int
    poll_interval_seconds: int
    wait_timeout_seconds: int
    restart_hours: int
    roster_refresh_minutes: int
    silence_grace_minutes: int
    summary_cadence_minutes: int
    timeouts: dict[str, str]
    budget: dict[str, int]
    budget_wallclock_hours: int
    notify_webhook_env: str | None
    notify_repeat_after_minutes: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Config":
        repo = data.get("repo", {})
        humans = data.get("humans", {})
        groups_raw = data.get("groups", {})
        if not groups_raw:
            raise ConfigError("missing required table groups (at least one group)")
        groups: dict[str, GroupConfig] = {}
        for name, g in groups_raw.items():
            path = f"groups.{name}"
            if name in RESERVED_PARTY_NAMES:
                raise ConfigError(f"{path}: {name!r} is a reserved party name and cannot name a group")
            if not GROUP_NAME_RE.match(name):
                raise ConfigError(f"{path}: group names must match {GROUP_NAME_RE.pattern}")
            groups[name] = GroupConfig(
                name=name,
                cli=str(_require(g, "cli", path)),
                bot=str(_require(g, "bot", path)),
                token_env=str(_require(g, "token_env", path)),
                concurrency=int(g.get("concurrency", 8)),
                review_concurrency=int(g.get("review_concurrency", 4)),
                orchestrator=_role(_require(g, "orchestrator", path), f"{path}.orchestrator"),
                pm=_role(_require(g, "pm", path), f"{path}.pm"),
            )
        review = data.get("review", {})
        agreement = data.get("agreement", {})
        polling = data.get("polling", {})
        runners = data.get("runners", {})
        timeouts = data.get("timeouts", {})
        budget = data.get("budget", {})
        notify = data.get("notify", {})
        return cls(
            repo_name=str(_require(repo, "name", "repo")),
            default_branch=str(repo.get("default_branch", "main")),
            private=bool(repo.get("private", True)),
            humans=tuple(str(h) for h in humans.get("accounts", [])),
            groups=groups,
            review_quorum=int(review.get("quorum", 2)),
            pingpong_cap=int(review.get("pingpong_cap", 3)),
            security_paths=tuple(str(p) for p in review.get("security_paths", [])),
            amend_cap=int(agreement.get("amend_cap", 3)),
            poll_interval_seconds=int(polling.get("interval_seconds", 30)),
            wait_timeout_seconds=int(polling.get("wait_timeout_seconds", 300)),
            restart_hours=int(runners.get("restart_hours", 4)),
            roster_refresh_minutes=int(runners.get("roster_refresh_minutes", 60)),
            silence_grace_minutes=int(runners.get("silence_grace_minutes", 30)),
            summary_cadence_minutes=int(runners.get("summary_cadence_minutes", 60)),
            timeouts={"S": str(timeouts.get("S", "20m")), "M": str(timeouts.get("M", "45m")), "L": str(timeouts.get("L", "90m"))},
            budget={
                "small": int(budget.get("small", 200)),
                "medium": int(budget.get("medium", 80)),
                "large": int(budget.get("large", 20)),
            },
            budget_wallclock_hours=int(budget.get("wallclock_hours", 72)),
            notify_webhook_env=notify.get("slack_dm_webhook_env"),
            notify_repeat_after_minutes=int(notify.get("repeat_after_minutes", 120)),
        )

    @property
    def group_names(self) -> tuple[str, ...]:
        return tuple(self.groups.keys())

    @property
    def effective_quorum(self) -> int:
        return max(1, min(self.review_quorum, len(self.groups) - 1))

    def group_for_bot(self, login: str) -> GroupConfig | None:
        for g in self.groups.values():
            if g.bot == login:
                return g
        return None

    def is_human(self, login: str) -> bool:
        return login in self.humans


def load_config(path: Path) -> Config:
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError as e:
        raise ConfigError(f"config file not found: {path}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}") from e
    return Config.from_dict(data)


PartyKind = Literal["group", "pm", "human", "joint"]


@dataclass(frozen=True)
class Party:
    kind: PartyKind
    group: str | None = None

    @property
    def label(self) -> str:
        if self.kind == "group":
            return f"[{self.group}]"
        if self.kind == "pm":
            return f"[{self.group}:pm]"
        return f"[{self.kind}]"

    @classmethod
    def parse(cls, text: str, cfg: Config) -> "Party | None":
        if text == "human":
            return cls("human")
        if text == "joint":
            return cls("joint")
        name, _, role = text.partition(":")
        if name not in cfg.groups:
            return None
        if role == "":
            return cls("group", name)
        if role == "pm":
            return cls("pm", name)
        return None


def party_from_env(cfg: Config, env: Mapping[str, str]) -> Party:
    group = env.get("MOOT_GROUP")
    if not group:
        return Party("human")
    if group not in cfg.groups:
        raise ConfigError(f"MOOT_GROUP={group!r} is not a configured group")
    role = env.get("MOOT_ROLE", "orchestrator")
    if role == "orchestrator":
        return Party("group", group)
    if role == "pm":
        return Party("pm", group)
    raise ConfigError(f"MOOT_ROLE={role!r} must be 'orchestrator' or 'pm'")
