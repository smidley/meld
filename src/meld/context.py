"""Resolve config, party, GitHub client, and state store for one CLI invocation."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from meld.adapters import Adapter, get_adapter
from meld.config import Config, ConfigError, Party, load_config, party_from_env
from meld.github import GitHub
from meld.models import ModelRegistry
from meld.roster import WorkerType, roster
from meld.rules import Actor
from meld.state import StateStore, meld_home


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

    def adapter(self) -> Adapter | None:
        if not self.party.group:
            return None
        return self.adapter_for(self.cfg.groups[self.party.group].cli)

    def roster_entries(self) -> list[WorkerType]:
        adapter = self.adapter()
        return roster(self.repo_root, adapter.installed_skills() if adapter else ())

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
