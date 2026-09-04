"""Atomic file writes: never leave a half-written file where a reader can see it."""
from __future__ import annotations

import os
from pathlib import Path


def write_atomic(path: Path, text: str) -> None:
    """Write text to a temporary file beside the target, then atomically replace it."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
