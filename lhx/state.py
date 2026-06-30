"""External, on-disk state: the progress ledger and the default-FAIL feature list.

This is the "structured note-taking / agentic memory" primitive. State lives on
disk (not in the context window) so it survives compaction, session restarts and
process kills. Two artifacts:

* ``PROGRESS.md`` — a human-readable, append-mostly narrative log ("engineers
  working in shifts" handoff notes).
* ``feature_list.json`` — a machine-checkable task contract. Every feature
  starts ``"passes": false`` (the *default-FAIL* contract from Anthropic's
  ``cwc-long-running-agents``): a feature only flips to ``true`` when the agent
  presents verified evidence, never by assertion. JSON is used (not Markdown)
  because the model is far less likely to inappropriately rewrite it.

Writes are atomic (write-temp-then-rename) so a process kill mid-write can never
corrupt the ledger — important for the resume-after-interruption guarantee.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write(path: Path, data: str) -> None:
    """Write ``data`` to ``path`` atomically (temp file + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic on POSIX
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class Feature(BaseModel):
    id: str
    description: str
    passes: bool = False  # default-FAIL
    evidence: str | None = None  # path/notes proving the pass
    verified_at: str | None = None


class FeatureList(BaseModel):
    """The machine-checkable contract for a task."""

    goal: str = ""
    features: list[Feature] = Field(default_factory=list)

    # ---- queries --------------------------------------------------------
    @property
    def total(self) -> int:
        return len(self.features)

    @property
    def passing(self) -> int:
        return sum(1 for f in self.features if f.passes)

    @property
    def all_pass(self) -> bool:
        return self.total > 0 and self.passing == self.total

    def fraction_passing(self) -> float:
        return self.passing / self.total if self.total else 0.0

    # ---- mutations ------------------------------------------------------
    def mark_pass(self, feature_id: str, evidence: str) -> bool:
        """Flip a feature to passing *with evidence*. Returns True if found."""
        for f in self.features:
            if f.id == feature_id:
                f.passes = True
                f.evidence = evidence
                f.verified_at = _now()
                return True
        return False

    # ---- persistence ----------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> "FeatureList":
        if not path.exists():
            return cls()
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, path: Path) -> None:
        atomic_write(path, self.model_dump_json(indent=2))


class ProgressLedger:
    """Append-mostly narrative log + a typed event trail used for observability.

    The Markdown ``PROGRESS.md`` is what a human (or the next session) reads to
    "get their bearings". ``.lh/events.jsonl`` is the structured trail the
    loop-guard, reflection and metrics code consume.
    """

    def __init__(self, progress_path: Path, events_path: Path):
        self.progress_path = progress_path
        self.events_path = events_path

    def init(self, goal: str) -> None:
        if self.progress_path.exists():
            return
        header = (
            f"# Progress Log\n\n"
            f"**Goal:** {goal}\n\n"
            f"_Conventions: always read this file first; work one feature at a "
            f"time; record proof before marking a feature passing; leave a clean "
            f"state._\n\n"
            f"## Session log\n\n"
            f"- {_now()} — session initialised.\n"
        )
        atomic_write(self.progress_path, header)

    def append(self, line: str) -> None:
        # Append-only: O(1) per call. Atomicity is not critical here (worst case
        # a torn trailing line), and rewriting the whole file every call would be
        # O(n^2) over a long session — the Claude-plays-Pokemon pattern is
        # thousands of steps. init() is the only writer that uses atomic_write.
        self.progress_path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not self.progress_path.exists()
        with open(self.progress_path, "a", encoding="utf-8") as f:
            if new_file:
                f.write("# Progress Log\n\n## Session log\n\n")
            f.write(f"- {_now()} — {line}\n")
            f.flush()
            os.fsync(f.fileno())

    def record_event(self, event: dict) -> None:
        event = {"ts": _now(), **event}
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    def read_events(self) -> list[dict]:
        if not self.events_path.exists():
            return []
        out = []
        for ln in self.events_path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))
        return out

    def tool_events(self) -> list[dict]:
        return [e for e in self.read_events() if e.get("type") == "tool_use"]
