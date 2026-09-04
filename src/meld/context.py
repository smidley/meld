"""Resolve config, party, GitHub client, and state store for one CLI invocation."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from meld.adapters import Adapter, get_adapter
from meld.config import Config, ConfigError, Party, load_config, party_from_env
from meld.github import GitHub
from meld.models import ModelRegistry
from meld.roster import WorkerType, roster
from meld.rules import Actor
from meld.state import StateStore, meld_home

_UNSET = object()


@dataclass
class Context:
    cfg: Config
    party: Party
    gh: GitHub
    store: StateStore
    models: ModelRegistry
    repo_root: Path
    actor_login: str
    adapter_for: Callable[[str], Adapter] = get_adapter
    _adapter_cache: Any = field(default=_UNSET, init=False, repr=False, compare=False)
    _roster_cache: list[WorkerType] | None = field(default=None, init=False, repr=False, compare=False)

    def adapter(self) -> Adapter | None:
        if self._adapter_cache is not _UNSET:
            return self._adapter_cache
        if not self.party.group:
            result = None
        else:
            result = self.adapter_for(self.cfg.groups[self.party.group].cli)
        self._adapter_cache = result
        return result

    def roster_entries(self) -> list[WorkerType]:
        if self._roster_cache is not None:
            return self._roster_cache
        adapter = self.adapter()
        result = roster(self.repo_root, adapter.installed_skills() if adapter else ())
        self._roster_cache = result
        return result

    def actor(self) -> Actor:
        names = frozenset(w.name for w in self.roster_entries()) if self.party.group else frozenset()
        return Actor(self.party, self.actor_login, names)


def find_config(start: Path) -> Path:
    for d in [start, *start.parents]:
        candidate = d / ".meld.toml"
        if candidate.exists():
            return candidate
    raise ConfigError(f"no .meld.toml found from {start} upward; pass --config")


def load_context(config_path: Path | None = None, env: Mapping[str, str] | None = None, run=subprocess.run,
                 cwd: Path | None = None, models: ModelRegistry | None = None, adapter_for: Callable[[str], Adapter] = get_adapter) -> Context:
    env = os.environ if env is None else env
    if env.get("MELD_WORKER") == "1":
        # Workers hold no token and no party; a worker that reaches for meld is trying to act as its group.
        raise ConfigError("meld may not be run by a worker")
    path = config_path or find_config(cwd or Path.cwd())
    cfg = load_config(path)
    party = party_from_env(cfg, env)
    role = {"group": "orchestrator", "pm": "pm"}.get(party.kind)
    store = StateStore(meld_home(env), party.group, role)
    token: str | None = None
    if party.group:
        g = cfg.groups[party.group]
        token = env.get(g.token_env)
        if not token:
            raise ConfigError(f"{g.token_env} is not set in the environment")
    gh = GitHub(cfg.repo_name, token=token, run=run, cache_dir=store.cache_dir)
    login = cfg.groups[party.group].bot if party.group else gh.viewer_login()
    return Context(cfg=cfg, party=party, gh=gh, store=store, models=models or ModelRegistry.load(),
                   repo_root=path.parent, actor_login=login, adapter_for=adapter_for)
