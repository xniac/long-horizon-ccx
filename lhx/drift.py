"""Goal-drift check.

On a long run an agent can slowly optimise for something *adjacent* to the
original goal (it "drifts"). We provide two complementary checks:

* **Contract check (deterministic)** — does the final state satisfy the
  machine-checkable feature contract? This is the authoritative signal and is
  what the eval harness scores.
* **Keyword/spec heuristic** — a cheap, model-free signal comparing the
  immutable BRIEF against the produced artifacts for the presence of required
  tokens. Useful as a fast in-loop nudge between full evaluator runs.

An optional LLM-judge drift score is exposed as a hook point but kept out of the
default path so the module has no hard model dependency.
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
        "should", "must", "will", "must", "have", "when", "then", "must",
        "implement", "build", "create", "make", "using", "code",
    }
    seen, out = set(), []
    for w in words:
        if len(w) >= min_len and w not in stop and w not in seen:
            seen.add(w)
            out.append(w)
    return out


def keyword_drift(brief: str, produced_text: str) -> DriftReport:
    """Fraction of brief keywords absent from the produced artifacts."""
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
