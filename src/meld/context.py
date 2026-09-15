"""Resolve config, party, GitHub client, and state store for one CLI invocation."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from meld.adapters import Adapter, get_adapter
from meld.appauth import AppAuth, AppAuthError, load_pem
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
    app_auth: AppAuth | None = None
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

    def refresh_roster(self) -> None:
        """Forget the memoized adapter and roster, so the next lookup re-scans installed skills and product worker files."""
        self._adapter_cache = _UNSET
        self._roster_cache = None

    def actor(self) -> Actor:
        names = frozenset(w.name for w in self.roster_entries()) if self.party.group else frozenset()
        return Actor(self.party, self.actor_login, names)


def refuse_worker(env: Mapping[str, str]) -> None:
    """Refuse when the caller is a worker (spec section 18). One message, one behaviour, for every verb.

    Workers hold no token and no party; a worker that reaches for `meld` is trying to act as its group.
    `load_context` calls this, which covers every verb that needs a board -- and `meld tail`, which
    deliberately needs no board and so never calls `load_context`, calls it directly. Without that a
    worker could read the *other* group's orchestrator transcript through a framework-blessed path.
    """
    if env.get("MELD_WORKER") == "1":
        raise ConfigError("meld may not be run by a worker")


def find_config(start: Path) -> Path:
    for d in [start, *start.parents]:
        candidate = d / ".meld.toml"
        if candidate.exists():
            return candidate
    raise ConfigError(f"no .meld.toml found from {start} upward; pass --config")


def load_context(config_path: Path | None = None, env: Mapping[str, str] | None = None, run=subprocess.run,
                 cwd: Path | None = None, models: ModelRegistry | None = None, adapter_for: Callable[[str], Adapter] = get_adapter,
                 app_post=None) -> Context:
    env = os.environ if env is None else env
    refuse_worker(env)
    path = config_path or find_config(cwd or Path.cwd())
    cfg = load_config(path)
    party = party_from_env(cfg, env)
    role = {"group": "orchestrator", "pm": "pm"}.get(party.kind)
    store = StateStore(meld_home(env), party.group, role)
    token: str | None = None
    provider = None
    app_auth: AppAuth | None = None
    if party.group:
        g = cfg.groups[party.group]
        if g.is_app:
            try:
                pem = load_pem(env, g.private_key_env, g.private_key_file)
            except AppAuthError as e:
                raise ConfigError(f"[groups.{g.name}]: {e}") from e
            # Least privilege, and the evidence doctor's coverage check reads: the token is scoped to the product
            # repository. The API wants bare repository names, not owner/name.
            app_auth = AppAuth(g.app_id, g.installation_id, pem, store.cache_dir / "app-token.json", post=app_post,  # type: ignore[arg-type]
                               repositories=[cfg.repo_name.rpartition("/")[2] or cfg.repo_name])
            provider = app_auth.token
        else:
            token = env.get(g.token_env or "")
            if not token:
                raise ConfigError(f"{g.token_env} is not set in the environment")
    gh = GitHub(cfg.repo_name, token=token, run=run, cache_dir=store.cache_dir, token_provider=provider)
    login = cfg.groups[party.group].bot if party.group else gh.viewer_login()
    return Context(cfg=cfg, party=party, gh=gh, store=store, models=models or ModelRegistry.load(),
                   repo_root=path.parent, actor_login=login, adapter_for=adapter_for, app_auth=app_auth)
