"""SubagentStop hook — capture the fresh-context evaluator's verdict.

The evaluator subagent (agents/evaluator.md) reviews the diff/artifacts from a
context that never saw the build and must begin its reply with ``PASS`` or
``NEEDS_WORK``. This hook parses that verdict from the subagent transcript and
records it in the ledger so the next builder session can seed its work from the
findings on ``NEEDS_WORK``.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from ._io import emit, build_runtime, read_event


def _last_text_from_transcript(path: str | None) -> str:
    if not path or not Path(path).is_file():
        return ""
    last = ""
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue
        content = obj.get("message", {}).get("content") or obj.get("content")
        if isinstance(content, str):
            last = content
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    last = block.get("text", last)
    return last


def parse_verdict(text: str) -> str | None:
    """Find the PASS / NEEDS_WORK verdict.

    The contract asks for it on the first line, but LLM output isn't 100%
    controllable (e.g. a "Based on my review:" preamble), so we scan the first
    few non-empty lines and match on a word boundary (NEEDS_WORK is checked
    first so it isn't shadowed, and 'PASSED' won't false-match 'PASS').
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for line in lines[:5]:
        if re.match(r"^NEEDS_WORK\b", line, re.IGNORECASE):
            return "NEEDS_WORK"
        if re.match(r"^PASS\b", line, re.IGNORECASE):
            return "PASS"
    return None


def main() -> int:
    event = read_event()
    rt = build_runtime(event)
    if not rt.config.enabled:
        emit({})
        return 0

    text = _last_text_from_transcript(event.get("transcript_path"))
    verdict = parse_verdict(text)
    if verdict:
        rt.ledger.record_event({"type": "evaluator_verdict", "verdict": verdict})
        rt.ledger.append(f"Fresh-context evaluator verdict: {verdict}.")
    emit({})
    return 0


if __name__ == "__main__":
    sys.exit(main())
