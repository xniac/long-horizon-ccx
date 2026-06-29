"""Periodic forced-reflection nudge.

Every ``interval`` tool calls, inject a short prompt asking the agent to step
back: what has been accomplished, what is blocking, is the current approach
working. This counters slow goal drift and "tunnel vision" on long runs without
the cost of running a separate sub-agent every step.

Pure function of the tool-call count so it is deterministic and testable.
"""

from __future__ import annotations

REFLECTION_PROMPT = (
    "REFLECTION CHECKPOINT (injected every {interval} tool calls):\n"
    "1. Re-read the goal in BRIEF.md — is your current work still serving it?\n"
    "2. What have you actually accomplished and verified since the last "
    "checkpoint? Update PROGRESS.md.\n"
    "3. What is blocking you? Is your current approach working, or should you "
    "drop a gear and decompose?\n"
    "Answer briefly to yourself, then continue."
)


def should_reflect(tool_call_count: int, interval: int) -> bool:
    """True when this tool-call count lands on a reflection boundary."""
    if interval <= 0:
        return False
    return tool_call_count > 0 and tool_call_count % interval == 0


def reflection_text(interval: int) -> str:
    return REFLECTION_PROMPT.format(interval=interval)
