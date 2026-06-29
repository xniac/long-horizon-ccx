"""PreToolUse hook — operator controls + doom-loop guard + step-budget breaker.

Order of precedence (highest first):
1. **Kill switch** — if ``AGENT_STOP`` exists, block every tool call.
2. **Steering** — if ``STEER.md`` exists, surface its contents once as operator
   guidance and clear it.
3. **Doom-loop / step-budget guard** — block identical-retry loops and runaway
   sessions.

This is the single most load-bearing hook for autonomous safety, so it stays
small and the decision logic lives in ``lhx.loop_guard``.
"""

from __future__ import annotations

import sys

from ..loop_guard import check, tool_signature
from ._io import block, build_runtime, inject_context, read_event


def main() -> int:
    event = read_event()
    rt = build_runtime(event)
    if not rt.config.enabled:
        # Module off: never interfere (this is the A/B "OFF" arm).
        from ._io import allow

        allow()
        return 0

    # 1. Kill switch.
    if rt.kill_switch_path.exists():
        block(
            "OPERATOR KILL-SWITCH active (AGENT_STOP present). Halting. "
            "Stop work immediately.",
            "PreToolUse",
        )
        return 0

    # 2. Steering (surface once, then consume).
    if rt.steer_path.exists():
        try:
            steer = rt.steer_path.read_text(encoding="utf-8").strip()
        except OSError:
            steer = ""
        if steer:
            rt.steer_path.unlink(missing_ok=True)
            inject_context(f"OPERATOR STEERING:\n{steer}", "PreToolUse")
            return 0

    # 3. Loop guard / circuit breaker.
    if rt.config.loop_guard:
        tool_name = event.get("tool_name", "")
        tool_input = event.get("tool_input") or event.get("tool_input_json") or {}
        sig = tool_signature(tool_name, tool_input)
        decision = check(
            rt.signatures(),
            sig,
            window=rt.config.doom_loop_window,
            step_budget=rt.config.step_budget,
        )
        if decision.block:
            rt.ledger.record_event({"type": "guard_block", "kind": decision.kind})
            block(decision.reason or "blocked", "PreToolUse")
            return 0

    from ._io import allow

    allow()
    return 0


if __name__ == "__main__":
    sys.exit(main())
