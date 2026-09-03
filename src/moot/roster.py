"""Worker-type inventory: built-in prompts, product overrides, and adapter-provided skills."""
from __future__ import annotations

from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class WorkerType:
    name: str
    description: str
    source: str
    path: Path | None
    text: str = ""


def parse_worker_file(path: Path, source: str) -> WorkerType:
    text = path.read_text()
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# "):
        raise ValueError(f"{path}: worker file must start with a '# <name>' heading")
    description = ""
    for line in lines[1:]:
        if line.strip():
            description = line.strip()
            break
    return WorkerType(name=path.stem, description=description, source=source, path=path, text=text)


def builtin_workers() -> list[WorkerType]:
    root = resources.files("moot").joinpath("prompts", "workers")
    out: list[WorkerType] = []
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".md"):
            with resources.as_file(entry) as p:
                w = parse_worker_file(Path(p), "builtin")
                out.append(replace(w, path=None))
    return out


def product_workers(repo_root: Path) -> list[WorkerType]:
    d = repo_root / ".moot" / "workers"
    if not d.is_dir():
        return []
    return [parse_worker_file(p, "product") for p in sorted(d.glob("*.md")) if p.is_file()]


def roster(repo_root: Path | None, extra: Iterable[WorkerType] = ()) -> list[WorkerType]:
    by_name: dict[str, WorkerType] = {w.name: w for w in builtin_workers()}
    if repo_root is not None:
        for w in product_workers(repo_root):
            by_name[w.name] = w
    for w in extra:
        by_name.setdefault(w.name, w)
    return sorted(by_name.values(), key=lambda w: w.name)


def roster_names(repo_root: Path | None, extra: Iterable[WorkerType] = ()) -> frozenset[str]:
    return frozenset(w.name for w in roster(repo_root, extra))
