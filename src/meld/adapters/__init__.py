"""Adapter registry. Import adapters lazily so a missing CLI never breaks the rest of meld."""
from __future__ import annotations

from importlib import import_module

from meld.adapters.base import Adapter, LaunchSpec, scrub_env

ADAPTER_NAMES = ("claude", "codex", "gemini", "grok", "antigravity")


class UnknownAdapter(KeyError):
    pass


def get_adapter(name: str) -> Adapter:
    if name not in ADAPTER_NAMES:
        raise UnknownAdapter(f"unknown adapter {name!r}; known: {', '.join(ADAPTER_NAMES)}")
    module = import_module(f"meld.adapters.{name}")
    return module.ADAPTER


__all__ = ["ADAPTER_NAMES", "Adapter", "LaunchSpec", "UnknownAdapter", "get_adapter", "scrub_env"]
