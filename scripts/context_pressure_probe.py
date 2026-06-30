#!/usr/bin/env python3
"""Trigger a REAL Haiku compaction on demand, to confirm the long-horizon regime
is reachable (and to seed compaction-crossing tasks).

The trick (learned the hard way): content seeded *on disk* lets a capable agent
grep/sed around it — it never enters context, so no compaction (see DESIGN §5.9,
v04). Putting the content **in the prompt** forces it into context from turn 1;
at ~168k tokens the conversation crosses Haiku's auto-compact threshold and the
PreCompact hook logs a `compaction` event.

    cp .env.template .env            # ANTHROPIC_API_KEY
    python scripts/context_pressure_probe.py            # ~168k-token prompt
    python scripts/context_pressure_probe.py --approx-tokens 120000

Reports whether a real compaction fired (observed via .lh/events.jsonl).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lhx.config import Config  # noqa: E402
from lhxeval.backends import ClaudeAgentSDKBackend, Directives  # noqa: E402
from lhxeval.env import load_dotenv  # noqa: E402
from lhxeval.tasks.schema import FeatureSpec, Task, VerifyCheck  # noqa: E402


def build_prompt(approx_tokens: int) -> str:
    # ~4 chars/token for this repetitive filler. Concatenate N "modules" inline.
    line = "    # lorem ipsum dolor sit amet consectetur adipiscing elit\n"
    lines_per_mod = max(1, (approx_tokens * 4) // (50 * len(line)))
    filler = line * lines_per_mod
    mods = [f"=== module mod_{i:02d}.py ===\n\"\"\"Module {i}.\"\"\"\n{filler}\n"
            f"def handler():\n    return {i}\n" for i in range(50)]
    return ("Below are 50 Python modules concatenated inline.\n\n" + "\n".join(mods) +
            "\n\nTASK: using your tools, write a file `answer.txt` containing exactly "
            "the integer number of modules shown above (just the number).")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--approx-tokens", type=int, default=168000)
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")

    prompt = build_prompt(args.approx_tokens)
    print(f"prompt size: {len(prompt) // 1024} KB (~{len(prompt) // 4000}k tokens)")
    task = Task(
        id="context-pressure-probe", title="probe", goal="count modules", prompt=prompt,
        features=[FeatureSpec(id="f1", description="answer", requires=["50"])],
        verify=[VerifyCheck(id="answer-50",
                cmd="python3 -c \"assert open('answer.txt').read().strip()=='50'\"")],
    )
    backend = ClaudeAgentSDKBackend(max_turns=30, timeout_seconds=560, keep_sandbox=True)
    out = backend.run(task, Config(enabled=True), seed=0, directives=Directives())
    kinds = Counter(e.get("type") for e in out.events)

    print(f"events={len(out.events)} {dict(kinds)}")
    print(f"COMPACTION fired? {'YES' if kinds.get('compaction') else 'NO'}")
    print(f"verify={out.checks}  cost=${out.cost_usd:.3f}  sandbox={backend.last_workspace}")
    return 0 if kinds.get("compaction") else 1


if __name__ == "__main__":
    sys.exit(main())
