"""Model registry: which models and efforts each adapter accepts, and size fit."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

SIZES = ("S", "M", "L")
_SIZE_MIN_CLASS = {"S": "small", "M": "medium", "L": "large"}
_CLASS_RANK = {"small": 0, "medium": 1, "large": 2}


@dataclass(frozen=True)
class ModelSpec:
    adapter: str
    id: str
    context_class: str
    efforts: tuple[str, ...]
    tier: str


def size_fits(size: str, context_class: str) -> bool:
    """True if an issue of `size` can run on a model of `context_class`."""
    return _CLASS_RANK[context_class] >= _CLASS_RANK[_SIZE_MIN_CLASS[size]]


class ModelRegistry:
    def __init__(self, specs: dict[str, dict[str, ModelSpec]]):
        self._specs = specs

    @classmethod
    def load(cls, path: Path | None = None) -> "ModelRegistry":
        if path is None:
            text = resources.files("moot.data").joinpath("models.toml").read_text()
        else:
            text = path.read_text()
        raw = tomllib.loads(text)
        specs: dict[str, dict[str, ModelSpec]] = {}
        for adapter, models in raw.items():
            specs[adapter] = {}
            for model_id, m in models.items():
                specs[adapter][model_id] = ModelSpec(
                    adapter=adapter,
                    id=model_id,
                    context_class=str(m["context_class"]),
                    efforts=tuple(str(e) for e in m["efforts"]),
                    tier=str(m["tier"]),
                )
        return cls(specs)

    @property
    def adapters(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def get(self, adapter: str, model_id: str) -> ModelSpec | None:
        return self._specs.get(adapter, {}).get(model_id)

    def for_adapter(self, adapter: str) -> list[ModelSpec]:
        return list(self._specs.get(adapter, {}).values())

    def tier_of(self, adapter: str, model_id: str) -> str | None:
        spec = self.get(adapter, model_id)
        return spec.tier if spec else None

    def validate(self, adapter: str, model_id: str, effort: str) -> list[str]:
        spec = self.get(adapter, model_id)
        if spec is None:
            return [f"model '{model_id}' unknown for adapter '{adapter}'"]
        if effort not in spec.efforts:
            return [f"effort '{effort}' not valid for {adapter}/{model_id} (valid: {', '.join(spec.efforts)})"]
        return []
