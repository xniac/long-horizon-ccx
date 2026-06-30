"""PostToolUse hook — record the tool call and (periodically) nudge reflection.

Every tool call is appended to the structured event trail (``.lh/events.jsonl``)
with its loop-detection signature. This trail powers the loop guard, the
reflection cadence and the eval metrics (steps, doom-loop incidence). On
reflection boundaries we inject the forced-reflection prompt.
"""

from __future__ import annotations

import sys

from ..loop_guard import tool_signature
from ..reflection import reflection_text, should_reflect
from ._io import allow, build_runtime, inject_context, read_event


def main() -> int:
    event = read_event()
    rt = build_runtime(event)
    if not rt.config.enabled:
        allow()
        return 0

    tool_name = event.get("tool_name", "")
    tool_input = event.get("tool_input") or event.get("tool_input_json") or {}
    sig = tool_signature(tool_name, tool_input)
    rt.ledger.record_event({"type": "tool_use", "tool": tool_name, "sig": sig})

    # Rolling, constant-size memory (Codex pattern): record a one-line summary of
    # mutating actions to the capped MEMORY.md, so the essential "what changed"
    # survives a compaction even when PROGRESS.md has grown long.
    if tool_name in ("Write", "Edit", "Bash"):
        target = (
            tool_input.get("file_path")
            or tool_input.get("command")
            or tool_input.get("path")
            or ""
        )
        rt.memory.note(f"[{tool_name}] {str(target)[:80]}")

    if rt.config.reflection:
        count = rt.tool_call_count()
        if should_reflect(count, rt.config.reflection_interval):
            inject_context(reflection_text(rt.config.reflection_interval), "PostToolUse")
            return 0

    allow()
    return 0


if __name__ == "__main__":
    sys.exit(main())
