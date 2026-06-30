"""PreCompact hook — back up the transcript and flush critical state to disk.

Compaction summarises a near-full context window into a fresh one; details can
be lost. Before that happens we (a) back up the raw transcript so nothing is
irrecoverable, and (b) append a marker to PROGRESS.md so the post-compaction
session knows a boundary was crossed. The durable state (feature_list.json,
PROGRESS.md, MEMORY.md) is already on disk, so the goal survives the boundary.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from ._io import allow, build_runtime, read_event


def _discover_transcript() -> str | None:
    """Best-effort fallback when the event lacks transcript_path."""
    candidates = [
        Path.home() / ".claude" / "current-session.jsonl",
        Path.home() / ".claude" / "projects" / "session.jsonl",
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    return None


def main() -> int:
    event = read_event()
    rt = build_runtime(event)
    if not rt.config.enabled:
        allow()
        return 0

    # The hook event carries the authoritative transcript path; prefer it. Fall
    # back to known on-disk locations only if the event omits it (the path has
    # moved across Claude Code versions, so we probe rather than hardcode one).
    transcript = event.get("transcript_path") or _discover_transcript()
    if transcript and Path(transcript).is_file():
        backups = rt.cwd / rt.config.state_dir / "transcripts"
        backups.mkdir(parents=True, exist_ok=True)
        dest = backups / (Path(transcript).stem + ".precompact.jsonl")
        try:
            shutil.copy2(transcript, dest)
        except OSError:
            pass

    rt.ledger.append(
        f"COMPACTION boundary crossed (trigger={event.get('trigger', 'auto')}). "
        f"State preserved on disk; re-read PROGRESS.md and feature_list.json."
    )
    rt.ledger.record_event({"type": "compaction"})
    allow()
    return 0


if __name__ == "__main__":
    sys.exit(main())
