"""Stop hook — completion gate + end-of-session checkpoint.

Counters the "premature victory" failure mode: a session that thinks it is done
is blocked from stopping while (a) the feature contract is not fully verified and
(b) the step budget is not exhausted and (c) the operator has not requested a
stop. The gate keys on a *real* machine-checkable condition (the default-FAIL
contract), which is what prevents it from becoming an infinite loop.

On a legitimate stop it writes an end-of-session git checkpoint and updates the
checkpoint file so the next session can resume.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from ..checkpoint import git_checkpoint
from ..state import FeatureList
from ._io import build_runtime, emit, read_event


def main() -> int:
    event = read_event()
    rt = build_runtime(event)
    if not rt.config.enabled:
        emit({})
        return 0

    # Avoid loops: if a previous Stop hook already blocked, let it through.
    if event.get("stop_hook_active"):
        emit({})
        return 0

    fl = FeatureList.load(rt.feature_path)
    budget_hit = rt.tool_call_count() >= rt.config.step_budget
    operator_stop = rt.kill_switch_path.exists()

    if (
        rt.config.completion_gate
        and fl.total > 0
        and not fl.all_pass
        and not budget_hit
        and not operator_stop
    ):
        remaining = [f.id for f in fl.features if not f.passes]
        rt.ledger.record_event({"type": "completion_gate_block"})
        emit(
            {
                "decision": "block",
                "reason": (
                    f"Completion gate: {fl.passing}/{fl.total} features verified "
                    f"passing. Do not stop yet. Work the next unfinished feature "
                    f"({remaining[0]}), verify it with evidence, then mark it. "
                    f"Remaining: {', '.join(remaining[:10])}."
                ),
            }
        )
        return 0

    # Legitimate stop → checkpoint.
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    commit = git_checkpoint(rt.cwd, f"session checkpoint: {stamp}")
    rt.save_checkpoint(
        {
            "session_id": event.get("session_id", "?"),
            "tool_calls": rt.tool_call_count(),
            "features_passing": fl.passing,
            "features_total": fl.total,
            "last_commit": commit,
            "stopped_at": stamp,
        }
    )
    rt.ledger.append(
        f"Session stopped. {fl.passing}/{fl.total} features passing"
        + (f"; commit {commit[:8]}." if commit else ".")
    )
    emit({})
    return 0


if __name__ == "__main__":
    sys.exit(main())
