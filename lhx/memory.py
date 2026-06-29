"""Constant-size external memory: an immutable BRIEF + a capped MEMORY scratchpad.

Mirrors the Codex "constant-size memory" pattern referenced in the research:

* ``BRIEF.md`` is written once and treated as immutable — it is the original,
  un-drifted statement of the goal. The drift checker compares outcomes against
  this, not against the (mutable) conversation.
* ``MEMORY.md`` is a rolling scratchpad capped at ``memory_char_cap`` characters
  so it can never grow without bound and re-bloat the context window. When the
  cap is exceeded we keep the most recent content (the head is the oldest).

Keeping memory bounded is the whole point: it lets a session re-hydrate the
*essential* state after a compaction or restart without paying for an
ever-growing note file.
"""

from __future__ import annotations

from pathlib import Path

from .state import atomic_write


class Memory:
    def __init__(self, brief_path: Path, memory_path: Path, char_cap: int = 2000):
        self.brief_path = brief_path
        self.memory_path = memory_path
        self.char_cap = char_cap

    def init_brief(self, goal: str) -> None:
        """Write the immutable brief exactly once."""
        if self.brief_path.exists():
            return
        atomic_write(
            self.brief_path,
            f"# BRIEF (immutable)\n\n{goal}\n",
        )

    def read_brief(self) -> str:
        return self.brief_path.read_text(encoding="utf-8") if self.brief_path.exists() else ""

    def read_memory(self) -> str:
        return self.memory_path.read_text(encoding="utf-8") if self.memory_path.exists() else ""

    def note(self, text: str) -> str:
        """Append a note, then truncate to the cap keeping the most recent text."""
        current = self.read_memory()
        combined = (current.rstrip() + "\n" + text.strip() + "\n") if current else text.strip() + "\n"
        if len(combined) > self.char_cap:
            # Keep the tail (most recent), cut on a line boundary if possible.
            tail = combined[-self.char_cap :]
            nl = tail.find("\n")
            if 0 <= nl < len(tail) - 1:
                tail = tail[nl + 1 :]
            combined = "<...older notes truncated...>\n" + tail
        atomic_write(self.memory_path, combined)
        return combined
