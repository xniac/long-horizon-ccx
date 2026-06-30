"""Tiny zero-dependency .env loader (avoids a python-dotenv dependency)."""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | os.PathLike = ".env", *, override: bool = False) -> bool:
    """Load KEY=VALUE lines from ``path`` into os.environ. Returns True if found.

    Existing env vars win unless ``override``. Ignores blanks/comments; strips one
    layer of surrounding quotes.
    """
    p = Path(path)
    if not p.is_file():
        return False
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and (override or key not in os.environ):
            os.environ[key] = val
    return True
