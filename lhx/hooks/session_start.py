"""SessionStart hook (M4) — inject resume context so a fresh session gets its
bearings. Chosen because it fires on startup *and* on resume (``source="resume"``)
and its output is injected into context — the right place for time-sensitive state.
"""

from __future__ import annotations

import sys

from ..checkpoint import resume_context
from ._io import allow, build_runtime, inject_context, read_event


def main() -> int:
    event = read_event()
    rt = build_runtime(event)
    if not (rt.config.enabled and rt.config.progress_ledger):
        allow()
        return 0

    ctx = resume_context(
        progress_path=rt.progress_path,
        feature_path=rt.feature_path,
        checkpoint_path=rt.checkpoint_path,
        cwd=rt.cwd,
    )
    inject_context(ctx, "SessionStart")
    return 0


if __name__ == "__main__":
    sys.exit(main())
