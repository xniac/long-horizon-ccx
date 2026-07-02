# claude-longhorizon (`lhx`)

A **long-horizon extension module for Claude Code** + a controlled **A/B
evaluation harness** that proves whether it helps.

Long-horizon agent runs fail because the run spans more tokens, sessions, and
failure opportunities than one context window holds. `lhx` adds the missing
policy layer — external memory, checkpoint/resume, doom-loop guards, reflection,
a completion gate, and a fresh-context evaluator — wired into Claude Code through
**hooks**. The eval harness then runs a paired A/B that toggles *only* the module
and reports capability, reliability, efficiency, and long-horizon-specific metrics
with honest uncertainty.

> Take-home: direction = **Long-horizon**, target = **Claude Code**. Full
> rationale, the four-layer decoupling (Agent / Harness / Task / Eval), prior-art
> mapping, and methodology are in **[DESIGN.md](DESIGN.md)**.

## Two layers of result (don't conflate them)

**1. Harness-validation — instrument calibration, *not* a capability claim and *not*
an industry-standard eval** (nobody simulates the agent to measure it). On a
simulated backend with a *known* effect (16 tasks × k=10 = 320 trials), the harness
correctly detects it, with no false positive on the regression control:

| metric | ON | OFF | Δ |
|---|---|---|---|
| pass@1 (macro) | 91.2% | 50.6% | +40.6pp |
| pass^3 (reliability) | 76.2% | 40.4% | +35.8pp |
| compaction-survival | 84.3% | 17.1% | +67.2pp |
| goal-drift rate | 0.0% | 53.1% | −53.1pp |

Paired Δ +0.406 [+0.331, +0.481]; McNemar helped=66, hurt=1, p≈9e-19. The effect
is *by construction* — it proves the ruler + pipeline are correct (stats, sandbox,
aggregation), not that the module helps real Claude.

**2. Real evaluation (the honest one).** On real Claude (Haiku 4.5) graded by
**executable checks**, the effect is scenario-dependent and the eval reports each
case honestly:

- **single-session tasks** (v01–v03): ON ≈ OFF success; ON just pays ~30–50% more
  tokens in protocol overhead.
- **agent shortcut** (v04, 178k-token audit): bypassed — Haiku batches via
  `sed`/`python3 -c`, so context never fills.
- **cross-session coordination** (v05 build, v06 session-scoped debug):
  **ON 3/3 vs OFF 0/3**, Δ +1.00 — the completion gate forces the work through where
  OFF self-reports "done" and stalls at ~half.
- **cross-session efficiency** (v07): both pass, but ON uses **−45% tokens** (the
  on-disk ledger saves re-orientation on each cold restart).

The contribution is the eval that tells these apart — where the module helps, where
it's overhead, where the agent routes around it — not a single flashy number.

## Quickstart

```bash
pip install -e .

python scripts/seed_tasks.py     # generate + validate the synthetic task suite
lhx-eval validate                # reference-solution sanity check (graders not vacuous)
lhx-eval run -k 10               # the paired A/B → runs/latest/{results.json,dashboard.html}
pytest -q                        # 53 unit + integration tests

# one-shot:
scripts/run_eval.sh 10           # validate + run + point you at the dashboard
```

No API key, numpy, scipy, or jinja2 required — the stats and dashboard are
pure-Python so the whole A/B runs offline and deterministically.

## Run against real Claude (Agent SDK / CLI)

The same harness runs against real Claude — only the backend changes. Provide a
key via `.env`:

```bash
cp .env.template .env          # then put your ANTHROPIC_API_KEY in .env
pip install -e ".[sdk]"        # only if you want the Python Agent SDK transport

# REAL grading: executable checks decide success, not the agent's self-report.
python scripts/smoke_sdk.py v01-slugify-verified         # runs the produced code
python scripts/smoke_sdk.py v02-health-endpoint-verified # starts the server & probes it

# Reproduce the documented deltas. Each task carries its own multi-session settings
# (schema.RunConfig), so `--task-id` alone is enough — no env knobs to remember:
lhx-eval run --backend sdk --task-id v05-incremental-app -k 3        # ON 3/3 vs OFF 0/3
lhx-eval run --backend sdk --task-id v06-debug-session-scoped -k 3   # ON 3/3 vs OFF 0/3
lhx-eval run --backend sdk --task-id v07-debug-amnesiac-pytest -k 2  # both pass, ON −45% tokens
lhx-eval run --backend sdk --verified-only -k 1                      # all verified tasks, each at its own config

# Single-session tasks are the correct negative control (Δ≈0):
lhx-eval run --backend sdk -k 1 \
  --task-id v01-slugify-verified --task-id v02-health-endpoint-verified --task-id v03-tasklib-verified
```

A bad key / model-access error **aborts loudly and writes no results** — a backend
failure is never silently scored as a 0% pass. Override a task's built-in settings
with `LHX_SDK_MAX_TURNS` / `LHX_SDK_MAX_SESSIONS` when needed.

`.env` knobs (see [.env.template](.env.template)): `LHX_SDK_TRANSPORT`
(`auto`|`sdk`|`cli`), `LHX_SDK_MODEL`, `LHX_SDK_MAX_TURNS`, `LHX_SDK_MAX_SESSIONS`,
`LHX_SDK_TIMEOUT`. Transport `auto` uses the `claude` CLI if it's on PATH, else the
Python `claude_agent_sdk`. The backend installs the module's hooks into an isolated
sandbox and toggles arms via `LHX_ENABLED` — so ON vs OFF holds everything else
fixed, exactly like the offline run.

> Note: a live run is **billed and non-deterministic**, so it's a manual check,
> not part of `pytest`. The integration *seam* (incl. per-task RunConfig and the
> fail-loud error handling) is covered offline by a mocked smoke test
> ([tests/test_backend_sdk.py](tests/test_backend_sdk.py)).

## Install the module into a real project

```bash
scripts/install.sh /path/to/your-project     # copies .claude/{settings.json,CLAUDE.md,agents/}
# in the env `claude` runs in, ensure `pip install -e <this repo>` is active
export LHX_ENABLED=false                       # ← this is the A/B "OFF" arm, same config file
```

The drop-in `.claude/settings.json` wires six hooks (`SessionStart`,
`PreToolUse`, `PostToolUse`, `PreCompact`, `Stop`, `SubagentStop`) to
`python -m lhx.hooks.*`. Every primitive is toggleable via `LHX_*` env vars (see
[lhx/config.py](lhx/config.py)).

## Repo map

```
lhx/                     # the extension module (the product)
  config.py              #   one Config; per-primitive toggles + LHX_* env overrides
  state.py               #   progress ledger + default-FAIL feature_list.json (atomic IO)
  memory.py              #   immutable BRIEF.md + capped MEMORY.md
  checkpoint.py          #   git checkpoint + resume-context injection
  loop_guard.py          #   doom-loop detector + step-budget circuit breaker
  reflection.py          #   periodic forced-reflection nudge
  drift.py               #   goal-drift signal
  hooks/                 #   six thin Claude Code hook entry points
agents/evaluator.md      # fresh-context evaluator sub-agent (PASS / NEEDS_WORK)
claude_config/           # drop-in .claude/ (settings.json wiring + CLAUDE.md conventions)

lhxeval/                 # the evaluation harness (the centerpiece)
  backends.py            #   SimulatedBackend (offline, ground-truth) + ClaudeAgentSDKBackend
                         #   (records the backend; fails loud on backend/auth errors)
  sandbox.py             #   isolated, seeded, torn-down per-trial workspace
  graders.py             #   deterministic outcome graders + partial credit
  metrics.py             #   pass@k / pass^k curves, long-horizon + efficiency metrics
  stats.py               #   paired bootstrap CI, McNemar exact, Beta posterior
  runner.py              #   the paired A/B driver (records the backend)
  report.py              #   results.json + self-contained HTML dashboard (labels the backend)
  cli.py                 #   `lhx-eval run|validate`
  tasks/                 #   task schema (incl. per-task RunConfig) + suite (t/r01–03, v01–07)
scripts/                 # seed_tasks.py, install.sh, run_eval.sh, smoke_sdk.py
tests/                   # 53 tests: math, guards, state, hooks, backend seam, report, end-to-end
```

## What's real vs needs credentials

Everything is implemented and tested offline (simulated backend). The **real**
backend (`ClaudeAgentSDKBackend`) is fully working and **validated live**: it preps
an isolated sandbox, installs the module's hooks (`--setting-sources project`),
wires the ON/OFF arm via `LHX_ENABLED`, runs real Claude (`claude -p` or the Python
Agent SDK), captures cost/tokens (`--output-format json`), and grades the produced
workspace with **executable verification**. It just needs the `claude` CLI +
`ANTHROPIC_API_KEY`. What remains future work: a *large* verified task suite and
container isolation (see DESIGN §6) for a full real-model benchmark.

See **[DESIGN.md](DESIGN.md)** for the full methodology, the eval-mode axes (§4),
and how the eval itself was validated (§2.3–§2.4, incl. two real bugs the self-check
caught).
