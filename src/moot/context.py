"""Resolve config, party, GitHub client, and state store for one CLI invocation."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from moot.config import Config, ConfigError, Party, load_config, party_from_env
from moot.github import GitHub
from moot.models import ModelRegistry
from moot.roster import roster_names
from moot.rules import Actor
from moot.state import StateStore, moot_home


@dataclass
class Context:
    cfg: Config
    party: Party
    gh: GitHub
    store: StateStore
    models: ModelRegistry
    repo_root: Path
    actor_login: str

    def actor(self) -> Actor:
        names = roster_names(self.repo_root) if self.party.group else frozenset()
        return Actor(self.party, self.actor_login, names)


def find_config(start: Path) -> Path:
    for d in [start, *start.parents]:
        candidate = d / ".moot.toml"
        if candidate.exists():
            return candidate
    raise ConfigError(f"no .moot.toml found from {start} upward; pass --config")


def load_context(config_path: Path | None = None, env: Mapping[str, str] | None = None, run=subprocess.run,
                 cwd: Path | None = None, models: ModelRegistry | None = None) -> Context:
    env = os.environ if env is None else env
    path = config_path or find_config(cwd or Path.cwd())
    cfg = load_config(path)
    party = party_from_env(cfg, env)
    store = StateStore(moot_home(env), party.group)
    token: str | None = None
    if party.group:
        g = cfg.groups[party.group]
        token = env.get(g.token_env)
        if not token:
            raise ConfigError(f"{g.token_env} is not set in the environment")
    gh = GitHub(cfg.repo_name, token=token, run=run, cache_dir=store.cache_dir)
    login = cfg.groups[party.group].bot if party.group else gh.viewer_login()
    return Context(cfg=cfg, party=party, gh=gh, store=store, models=models or ModelRegistry.load(),
                   repo_root=path.parent, actor_login=login)
