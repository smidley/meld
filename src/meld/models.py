"""Model registry: which models and efforts each adapter accepts, and size fit."""
from __future__ import annotations

import json
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


def write_product_model(path: Path, adapter: str, model_id: str, *, tier: str, context_class: str, efforts: list[str]) -> None:
    """Add or replace one entry in the product's catalogue file, keeping every other entry it holds. Written by hand
    (the standard library reads TOML and does not write it): one `[adapter."id"]` table per model, keys quoted so a
    model id with a dot or a colon (`qwen3-coder:30b`) stays one key."""
    from meld.atomic import write_atomic
    existing = tomllib.loads(path.read_text()) if path.is_file() else {}
    existing.setdefault(adapter, {})[model_id] = {"context_class": context_class, "efforts": list(efforts), "tier": tier}
    lines = ["# The product's own model catalogue, laid over Meld's packaged models.toml (`meld add-model`; edit freely).", ""]
    for a in sorted(existing):
        for mid in sorted(existing[a]):
            m = existing[a][mid]
            quoted = json.dumps(mid)
            lines += [f"[{a}.{quoted}]", f"context_class = {json.dumps(str(m['context_class']))}",
                      "efforts = [" + ", ".join(json.dumps(str(e)) for e in m["efforts"]) + "]", f"tier = {json.dumps(str(m['tier']))}", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, "\n".join(lines))


def size_fits(size: str, context_class: str) -> bool:
    """True if an issue of `size` can run on a model of `context_class`."""
    return _CLASS_RANK[context_class] >= _CLASS_RANK[_SIZE_MIN_CLASS[size]]


class ModelRegistry:
    def __init__(self, specs: dict[str, dict[str, ModelSpec]]):
        self._specs = specs

    @classmethod
    def load(cls, path: Path | None = None, extra: Path | None = None) -> "ModelRegistry":
        """The packaged catalogue (or `path`), with the product's own entries from `extra` -- `.meld/models.toml` in the
        product repository, written by `meld add-model` or by hand -- laid over it: a local model behind an endpoint is the
        product's to name, and its tier and context class are the product's call."""
        text = resources.files("meld.data").joinpath("models.toml").read_text() if path is None else path.read_text()
        specs = cls._parse(text)
        if extra is not None and extra.is_file():
            for adapter, models in cls._parse(extra.read_text()).items():
                specs.setdefault(adapter, {}).update(models)
        return cls(specs)

    @staticmethod
    def _parse(text: str) -> dict[str, dict[str, ModelSpec]]:
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
        return specs

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
