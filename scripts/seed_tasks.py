#!/usr/bin/env python3
"""Generate (and validate) the synthetic long-horizon task suite.

Produces a *balanced* set following Anthropic's eval guidance:
* a **capability** sub-suite (longer, multi-feature, crossing compaction
  boundaries and/or interruptions — where the module should help), and
* a **regression** sub-suite (short, clean, no boundaries — should run near 100%
  for both arms and catch backsliding).

Each task ships unique ``requires`` tokens per feature so the deterministic
grader is non-vacuous, plus a one-line reference-solution descriptor. Re-running
is idempotent. After writing, it runs the reference-solution sanity check.

Usage:  python scripts/seed_tasks.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "lhxeval" / "tasks" / "synthetic"


def feat(i: int, slug: str, *requires: str, weight: float = 1.0) -> dict:
    return {
        "id": f"F{i:02d}-{slug}",
        "description": f"Implement {slug.replace('-', ' ')}",
        "requires": list(requires),
        "weight": weight,
    }


def task(
    id, title, goal, prompt, features, *,
    difficulty="capability", compaction_boundaries=0, interruption=False,
    reference="", sim=None, verify=None,
) -> dict:
    t = {
        "id": id,
        "title": title,
        "goal": goal,
        "prompt": prompt,
        "difficulty": difficulty,
        "features": features,
        "compaction_boundaries": compaction_boundaries,
        "interruption": interruption,
        "reference_solution": reference,
    }
    if verify:
        t["verify"] = verify
    if sim is None and difficulty == "capability":
        sim = CAP_SIM
    if sim is None and difficulty == "regression":
        sim = REG_SIM
    if sim:
        t["simulation"] = sim
    return t


# Server-probe verification: actually starts the produced app and hits it over
# HTTP (the harder, more realistic real-grader mode). Pure stdlib (urllib), fixed
# port, polls for startup, always kills the server. Exit 0 == endpoint works.
SERVER_PROBE = (
    "python3 - <<'PY'\n"
    "import subprocess, sys, time, urllib.request\n"
    "p = subprocess.Popen([sys.executable, 'app.py'])\n"
    "try:\n"
    "    for _ in range(20):\n"
    "        time.sleep(0.5)\n"
    "        try:\n"
    "            r = urllib.request.urlopen('http://localhost:8765/health', timeout=1)\n"
    "            if r.status == 200 and r.read().strip() == b'ok':\n"
    "                sys.exit(0)\n"
    "        except Exception:\n"
    "            pass\n"
    "    sys.exit(1)\n"
    "finally:\n"
    "    p.terminate()\n"
    "PY\n"
)


# Genuine, irreducible per-feature difficulty for the longer capability tasks
# (the module cannot fix it). Keeps the ON arm realistically below 100% so the
# pass@1 vs pass^k divergence is visible. Regression tasks stay clean (~100%).
CAP_SIM = {"residual_fail_prob": 0.03}

# Regression tasks are short and clean: no failure modes, so BOTH arms run near
# 100%. Their job is to catch backsliding, not to show an effect.
REG_SIM = {
    "base_doom_loop_prob": 0.0,
    "drift_prob_per_feature": 0.0,
    "compaction_amnesia_prob": 0.0,
    "cold_resume_fail_prob": 0.0,
    "residual_fail_prob": 0.0,
}


def build() -> list[dict]:
    tasks: list[dict] = []

    # --- Capability: long multi-feature builds crossing compaction --------
    tasks.append(task(
        "t01-multi-file-api",
        "Build a multi-file REST API",
        "Build a Flask todo API with models, routes, validation, tests and a README.",
        "Implement the full todo API described in BRIEF.md, one feature at a time.",
        [
            feat(1, "models", "class Todo", "id", "title", "done"),
            feat(2, "create-route", "POST", "/todos", "201"),
            feat(3, "list-route", "GET", "/todos", "200"),
            feat(4, "update-route", "PUT", "/todos/<id>", "204"),
            feat(5, "validation", "400", "missing title"),
            feat(6, "tests", "def test_", "assert", "pytest"),
            feat(7, "readme", "## Usage", "curl"),
        ],
        compaction_boundaries=2,
        reference="Standard Flask app.py + models.py + tests/test_api.py + README.md.",
    ))

    tasks.append(task(
        "t02-refactor-chain",
        "Sequential refactor chain",
        "Refactor a monolith into modules without breaking the passing test suite.",
        "Perform the refactor steps in BRIEF.md in order; keep all tests green.",
        [
            feat(1, "extract-db", "module db", "connection"),
            feat(2, "extract-auth", "module auth", "hash", "verify"),
            feat(3, "extract-handlers", "module handlers", "route"),
            feat(4, "wire-imports", "from db", "from auth", "from handlers"),
            feat(5, "keep-green", "pytest", "passed", "no regressions"),
        ],
        compaction_boundaries=1,
        reference="Move code into db.py/auth.py/handlers.py, update imports, rerun tests.",
    ))

    tasks.append(task(
        "t03-migration-chain",
        "Schema migration chain (interrupted)",
        "Apply a 6-step DB migration chain; the session is interrupted midway.",
        "Apply migrations 1..6 from BRIEF.md; resume cleanly if interrupted.",
        [
            feat(1, "m1-add-users", "migration 0001", "users"),
            feat(2, "m2-add-index", "migration 0002", "index"),
            feat(3, "m3-add-orders", "migration 0003", "orders"),
            feat(4, "m4-fk", "migration 0004", "foreign key"),
            feat(5, "m5-backfill", "migration 0005", "backfill"),
            feat(6, "m6-verify", "migration 0006", "verify", "all applied"),
        ],
        interruption=True,
        reference="Sequenced migration files 0001..0006 applied in order with verification.",
    ))

    tasks.append(task(
        "t04-cli-tool",
        "Multi-subcommand CLI",
        "Build a CLI with init/add/list/done/report subcommands and tests.",
        "Implement each subcommand in BRIEF.md, verifying each end to end.",
        [
            feat(1, "init", "cmd init", "creates store"),
            feat(2, "add", "cmd add", "appends item"),
            feat(3, "list", "cmd list", "prints items"),
            feat(4, "done", "cmd done", "marks complete"),
            feat(5, "report", "cmd report", "summary"),
            feat(6, "tests", "def test_", "argparse", "assert"),
        ],
        compaction_boundaries=1,
        interruption=True,
        reference="argparse CLI with five subcommands + tests/test_cli.py.",
    ))

    tasks.append(task(
        "t05-data-pipeline",
        "ETL pipeline with stages",
        "Build extract/transform/validate/load stages with a smoke test.",
        "Implement each pipeline stage in BRIEF.md and a smoke test.",
        [
            feat(1, "extract", "def extract", "reads source"),
            feat(2, "transform", "def transform", "normalizes"),
            feat(3, "validate", "def validate", "rejects bad rows"),
            feat(4, "load", "def load", "writes sink"),
            feat(5, "smoke", "def test_pipeline", "end to end"),
        ],
        compaction_boundaries=2,
        reference="pipeline.py with four stage functions + tests/test_pipeline.py.",
    ))

    tasks.append(task(
        "t06-bugfix-sweep",
        "Fix a list of known bugs",
        "Fix 6 enumerated bugs; do not regress neighbouring behaviour.",
        "Fix each bug in BRIEF.md with a regression test; keep the suite green.",
        [
            feat(1, "bug-off-by-one", "fix off-by-one", "boundary"),
            feat(2, "bug-null-deref", "guard null", "none check"),
            feat(3, "bug-race", "lock", "thread-safe"),
            feat(4, "bug-encoding", "utf-8", "decode"),
            feat(5, "bug-timezone", "utc", "aware datetime"),
            feat(6, "regression-tests", "def test_", "all pass"),
        ],
        interruption=True,
        reference="Six targeted fixes each with a regression test.",
    ))

    # --- Regression sub-suite: short, clean, should be ~100% both arms ----
    tasks.append(task(
        "r01-hello-endpoint",
        "Single endpoint",
        "Add a /health endpoint returning 200 ok.",
        "Add the /health endpoint described in BRIEF.md.",
        [feat(1, "health", "GET", "/health", "200", "ok")],
        difficulty="regression",
        reference="One route returning ('ok', 200).",
    ))

    tasks.append(task(
        "r02-add-function",
        "Pure function + test",
        "Add a slugify() function and one unit test.",
        "Implement slugify() and a test per BRIEF.md.",
        [
            feat(1, "slugify", "def slugify", "lowercase", "hyphen"),
            feat(2, "test", "def test_slugify", "assert"),
        ],
        difficulty="regression",
        reference="slugify() lowercasing and hyphenating + one assert.",
    ))

    tasks.append(task(
        "r03-config-loader",
        "Config loader",
        "Add a config loader reading env with defaults.",
        "Implement load_config() per BRIEF.md.",
        [
            feat(1, "loader", "def load_config", "os.environ", "default"),
            feat(2, "test", "def test_config", "assert"),
        ],
        difficulty="regression",
        reference="load_config() reading env with fallback defaults + test.",
    ))

    # --- Executable-verified task (REAL grading via `verify`) --------------
    # Tightly specified so verification is deterministic and language-fixed —
    # the real backend runs these commands against the produced workspace and
    # grades by exit code (F2P), NOT by the agent's self-report. The simulated
    # backend ignores `verify` and uses `features` as usual.
    tasks.append(task(
        "v01-slugify-verified",
        "Slugify (executable-verified)",
        "Implement a Python slugify() verified by running it.",
        "Create a Python module `slugify.py` at the project root exposing a "
        "function `slugify(s: str) -> str` that lowercases the input and replaces "
        "every run of non-alphanumeric characters with a single hyphen, stripping "
        "leading/trailing hyphens. Examples: slugify('Hello, World!') == "
        "'hello-world'; slugify('  A__B  ') == 'a-b'.",
        [feat(1, "slugify", "def slugify", "lowercase", "hyphen")],
        difficulty="regression",
        reference="slugify.py implementing the spec.",
        verify=[
            {"id": "hello-world",
             "cmd": "python3 -c \"from slugify import slugify; assert slugify('Hello, World!')=='hello-world'\"",
             "weight": 1.0},
            {"id": "collapse-and-trim",
             "cmd": "python3 -c \"from slugify import slugify; assert slugify('  A__B  ')=='a-b'\"",
             "weight": 1.0},
        ],
    ))

    # Harder real-grader mode: a running HTTP server, verified by probing it.
    tasks.append(task(
        "v02-health-endpoint-verified",
        "Health endpoint (server-probe verified)",
        "Implement a stdlib HTTP server with /health, verified by hitting it.",
        "Create `app.py` using ONLY the Python standard library (no third-party "
        "packages such as Flask). Running `python3 app.py` must start an HTTP "
        "server on port 8765 that responds to GET /health with HTTP status 200 and "
        "body exactly `ok`. The server should keep running until terminated.",
        [feat(1, "health", "GET", "/health", "200", "ok")],
        difficulty="regression",
        reference="http.server-based app.py serving /health -> 200 'ok' on :8765.",
        verify=[{"id": "server-responds", "cmd": SERVER_PROBE, "weight": 1.0}],
    ))

    return tasks


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tasks = build()
    for t in tasks:
        (OUT / f"{t['id']}.json").write_text(json.dumps(t, indent=2), encoding="utf-8")
    print(f"Wrote {len(tasks)} tasks to {OUT}")

    # Validate using the same code path as `lhx-eval validate`.
    from lhxeval.cli import cmd_validate
    import argparse

    print("\nReference-solution sanity check:")
    return cmd_validate(argparse.Namespace(tasks=str(OUT)))


if __name__ == "__main__":
    sys.exit(main())
