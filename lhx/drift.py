"""Goal-drift check (M6). The authoritative signal is the deterministic feature
contract; the keyword heuristic below is only a cheap in-loop nudge (see the
limitation note on ``keyword_drift``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class DriftReport:
    drifted: bool
    score: float  # 0.0 = perfectly on-spec, 1.0 = fully drifted
    missing: list[str]
    detail: str


def _keywords(brief: str, min_len: int = 4) -> list[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]+", brief.lower())
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "into", "your",
        "should", "must", "will", "have", "when", "then",
        "implement", "build", "create", "make", "using", "code",
    }
    seen, out = set(), []
    for w in words:
        if len(w) >= min_len and w not in stop and w not in seen:
            seen.add(w)
            out.append(w)
    return out


def keyword_drift(brief: str, produced_text: str) -> DriftReport:
    """Fraction of brief keywords absent from the produced artifacts.

    Heuristic only; brittle on domain-specific terms and synonyms (a correct
    artifact can use different vocabulary than the brief). It is a cheap in-loop
    nudge, not an authoritative grader — use the deterministic feature contract
    as the source of truth and an LLM-rubric judge as the production drift
    detector / degraded path.
    """
    kws = _keywords(brief)
    if not kws:
        return DriftReport(False, 0.0, [], "no keywords extracted from brief")
    hay = produced_text.lower()
    missing = [k for k in kws if k not in hay]
    score = len(missing) / len(kws)
    return DriftReport(
        drifted=score > 0.5,
        score=score,
        missing=missing,
        detail=f"{len(kws) - len(missing)}/{len(kws)} brief keywords present",
    )
