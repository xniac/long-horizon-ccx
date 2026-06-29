"""Shared stdin/stdout helpers for hooks + the Claude Code hook JSON contract.

The hook payload schema has drifted across Claude Code versions; we read fields
defensively and fall back to the current working directory when ``cwd`` is
absent. Output helpers cover the two control mechanisms used here: a decision
object (``allow``/``block``) and additional-context injection.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ..config import Config
from ..runtime import Runtime


def read_event() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def event_cwd(event: dict) -> Path:
    return Path(event.get("cwd") or os.getcwd())


def build_runtime(event: dict) -> Runtime:
    return Runtime(event_cwd(event), Config.from_env())


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def allow() -> None:
    """Explicit no-op: let Claude Code proceed normally."""
    emit({})


def block(reason: str, hook_event: str = "PreToolUse") -> None:
    """Block a tool call and feed ``reason`` back to the model.

    Uses the JSON ``{"decision": "block"}`` form (rather than exit code 2) so the
    reason is delivered cleanly regardless of stderr handling.
    """
    emit(
        {
            "decision": "block",
            "reason": reason,
            "hookSpecificOutput": {
                "hookEventName": hook_event,
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            },
        }
    )


def inject_context(text: str, hook_event: str) -> None:
    """Inject additional context into the model's view."""
    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": hook_event,
                "additionalContext": text,
            }
        }
    )
