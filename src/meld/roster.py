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
    root = resources.files("meld").joinpath("prompts", "workers")
    out: list[WorkerType] = []
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".md"):
            with resources.as_file(entry) as p:
                w = parse_worker_file(Path(p), "builtin")
                out.append(replace(w, path=None))
    return out


def product_workers(repo_root: Path) -> list[WorkerType]:
    d = repo_root / ".meld" / "workers"
    if not d.is_dir():
        return []
    return [parse_worker_file(p, "product") for p in sorted(d.glob("*.md")) if p.is_file()]


def product_skills(repo_root: Path) -> list[WorkerType]:
    """Skills the product ships under .meld/skills/<name>/SKILL.md (frontmatter name/description), shared by every group."""
    from meld.adapters.base import scan_skills   # local import: adapters.base imports roster
    d = repo_root / ".meld" / "skills"
    return scan_skills([d], source="product-skill") if d.is_dir() else []


def roster(repo_root: Path | None, extra: Iterable[WorkerType] = ()) -> list[WorkerType]:
    by_name: dict[str, WorkerType] = {w.name: w for w in builtin_workers()}
    if repo_root is not None:
        for w in product_workers(repo_root):
            by_name[w.name] = w
        # Merged before adapter `extra` skills (whose setdefault below would otherwise lose to a same-named
        # host skill), so a product skill still overrides a same-named skill. But a product skill may only
        # take a name that is unclaimed or already held by a skill -- it can never displace a builtin/product
        # WORKER: a SKILL.md whose frontmatter `name:` collides with a worker's name is dropped entirely (not
        # kept under its directory name either), so the worker's role prompt is never silently replaced by
        # skill text.
        for w in product_skills(repo_root):
            existing = by_name.get(w.name)
            if existing is None or existing.source in ("skill", "product-skill"):
                by_name[w.name] = w
    for w in extra:
        by_name.setdefault(w.name, w)
    return sorted(by_name.values(), key=lambda w: w.name)
