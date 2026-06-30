"""Doom-loop detector + step-budget circuit breaker (M5).

Pure functions over the event trail (hence trivially unit-tested): block when the
last ``window`` tool signatures are identical, or hard-stop past ``step_budget``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


def tool_signature(tool_name: str, tool_input: dict | None) -> str:
    """Stable hash of a tool call for loop detection."""
    payload = json.dumps(tool_input or {}, sort_keys=True, default=str)
    h = hashlib.sha256(f"{tool_name}\x00{payload}".encode()).hexdigest()
    return h[:16]


@dataclass
class GuardDecision:
    block: bool
    reason: str | None = None
    kind: str | None = None  # "doom_loop" | "step_budget" | None


def check(
    prior_signatures: list[str],
    next_signature: str,
    *,
    window: int = 3,
    step_budget: int = 400,
) -> GuardDecision:
    """Decide whether the *next* tool call should be blocked.

    ``prior_signatures`` is the chronological list of tool signatures already
    executed this session. ``next_signature`` is the one about to run.
    """
    # Circuit breaker first.
    if len(prior_signatures) >= step_budget:
        return GuardDecision(
            block=True,
            kind="step_budget",
            reason=(
                f"Step budget of {step_budget} tool calls exhausted. Stop and "
                f"checkpoint: update PROGRESS.md with current state and what "
                f"remains, then end the session so a fresh one can resume."
            ),
        )

    # Doom loop: the proposed call plus the last (window-1) calls are identical.
    recent = prior_signatures[-(window - 1) :] if window > 1 else []
    if window >= 2 and len(recent) == window - 1 and all(s == next_signature for s in recent):
        return GuardDecision(
            block=True,
            kind="doom_loop",
            reason=(
                f"Doom-loop guard: this is identical to the previous "
                f"{window - 1} tool call(s). Do NOT retry with identical "
                f"arguments. Drop a gear — switch to read-only investigation, "
                f"re-read PROGRESS.md, and decompose the step into smaller "
                f"sub-steps before acting again."
            ),
        )

    return GuardDecision(block=False)
