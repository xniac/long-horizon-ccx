#!/usr/bin/env python3
"""One-shot live smoke test of the real backend (Agent SDK or `claude -p` CLI).

Loads .env, runs ONE small task through the real Claude integration with the
module ON, and prints the reconstructed outcome + grade. This is the manual
"does my key work end-to-end" check — it is intentionally NOT part of pytest
(a live, billed, non-deterministic call has no place in CI).

    cp .env.template .env   # add ANTHROPIC_API_KEY
    python scripts/smoke_sdk.py                  # smallest regression task
    python scripts/smoke_sdk.py t01-multi-file-api
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lhxeval.backends import ClaudeAgentSDKBackend, Directives  # noqa: E402
from lhxeval.env import load_dotenv  # noqa: E402
from lhxeval.graders import grade_outcome  # noqa: E402
from lhxeval.tasks.schema import load_suite  # noqa: E402
from lhx.config import Config  # noqa: E402

SUITE = ROOT / "lhxeval" / "tasks" / "synthetic"


def main() -> int:
    load_dotenv(ROOT / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set. `cp .env.template .env` and fill it in.",
              file=sys.stderr)
        return 2

    tasks = {t.id: t for t in load_suite(SUITE)}
    task_id = sys.argv[1] if len(sys.argv) > 1 else "r01-hello-endpoint"
    if task_id not in tasks:
        print(f"unknown task '{task_id}'. Available: {', '.join(sorted(tasks))}", file=sys.stderr)
        return 2
    task = tasks[task_id]

    backend = ClaudeAgentSDKBackend(
        model=os.environ.get("LHX_SDK_MODEL", "claude-haiku-4-5-20251001"),
        max_turns=int(os.environ.get("LHX_SDK_MAX_TURNS", "40")),
        timeout_seconds=int(os.environ.get("LHX_SDK_TIMEOUT", "600")),
        keep_sandbox=True,  # keep the workspace so we can inspect what happened
    )
    print(f"Running task '{task.id}' (module ON) via transport="
          f"{backend._resolve_transport()} — this can take a few minutes ...\n")

    outcome = backend.run(task, Config(enabled=True), seed=0, directives=Directives())
    grade = grade_outcome(task, outcome)

    # This script is an INTEGRATION check, not a benchmark: it verifies the module
    # actually ran against real Claude. The token "success" below is graded off the
    # agent's self-reported feature_list.json evidence — i.e. it trusts the
    # contract, NOT an independent test. A trustworthy real A/B needs per-task
    # executable verification (see DESIGN §10); the simulated backend is the
    # validated scoring path.
    import json as _json
    from collections import Counter

    ws = Path(backend.last_workspace) if backend.last_workspace else None
    events = []
    ev_path = (ws / ".lh" / "events.jsonl") if ws else None
    if ev_path and ev_path.exists():
        events = [_json.loads(l) for l in ev_path.read_text().splitlines() if l.strip()]
    kinds = Counter(e.get("type") for e in events)

    print("=== module integration (the real check) ===")
    print(f"  hooks fired?         : {'YES' if events else 'NO — module was inert!'}")
    print(f"  hook events recorded : {len(events)}  {dict(kinds)}")
    print(f"  tool-call steps      : {outcome.steps}")
    print(f"  doom-loop blocks     : {outcome.doom_loops}")
    print(f"  crossed compaction   : {outcome.forced_compaction}")
    print(f"  tokens / cost(USD)   : {outcome.tokens} / {outcome.cost_usd:.4f}")

    print("\n=== contract self-report (NOT independent verification) ===")
    print(f"  features marked pass : {outcome.features_completed}")
    print(f"  token grade          : success={grade.success} partial={grade.partial_credit:.2f}")

    cli = backend.last_cli or {}
    print("\n=== claude CLI ===")
    print(f"  cmd        : {cli.get('cmd')}")
    print(f"  returncode : {cli.get('returncode')}")
    if cli.get("stderr"):
        print(f"  stderr     : {cli['stderr'][:800]}")
    print(f"  result     : {(cli.get('result_text') or cli.get('stdout',''))[-800:]!r}")
    if ws and ws.exists():
        created = sorted(p.name for p in ws.iterdir() if not p.name.startswith("."))
        print(f"\n  sandbox    : {ws}  (files: {created})")
        print(f"  inspect with: ls -la {ws}")

    # Exit 0 if the integration worked (hooks fired), regardless of token grade.
    return 0 if events else 1


if __name__ == "__main__":
    sys.exit(main())
