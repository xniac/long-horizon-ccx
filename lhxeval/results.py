"""Result records shared across the harness (kept dependency-free to avoid
circular imports between backends, graders, metrics and the runner)."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class TrialResult:
    task_id: str
    arm: str                       # "on" | "off"
    seed: int
    trial_index: int

    success: bool = False
    partial_credit: float = 0.0    # 0..1 from graders

    # trajectory aggregates
    steps: int = 0
    tokens: int = 0
    cost_usd: float = 0.0

    # long-horizon-specific signals
    drifted: bool = False
    doom_loops: int = 0
    forced_compaction: bool = False
    interrupted: bool = False
    resumed_ok: bool = False

    grader_detail: dict[str, Any] = field(default_factory=dict)
    transcript: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Transcripts are bulky; record a count, not the full trail, in results.json.
        d["n_events"] = len(self.transcript)
        d.pop("transcript", None)
        return d
