# claude-longhorizon (`lhx`)

A **long-horizon extension module for Claude Code** + a controlled **A/B
evaluation harness** that proves whether it helps.

Long-horizon agent runs fail because the run spans more tokens, sessions, and
failure opportunities than one context window holds. `lhx` adds the missing
policy layer — external memory, checkpoint/resume, doom-loop guards, reflection,
a completion gate, and a fresh-context evaluator — wired into Claude Code through
**hooks**. The eval harness then runs a paired A/B that toggles *only* the module
and reports capability, reliability, and long-horizon-specific metrics with
honest uncertainty.

> Take-home: direction = **Long-horizon**, target = **Claude Code**. Full
> rationale, prior-art mapping, and methodology are in **[DESIGN.md](DESIGN.md)**.

## Headline result

Simulated backend, 11 tasks × k=10 × 2 arms = 220 trials (`lhx-eval run -k 10`):

| metric | module ON | module OFF | Δ |
|---|---|---|---|
| pass@1 (macro) | **91.8%** | 50.0% | +41.8pp |
| pass^3 (reliability) | **78.4%** | 45.5% | +32.9pp |
| compaction-survival | **80.0%** | 2.5% | +77.5pp |
| resume-after-interruption | **93.3%** | 13.3% | +80.0pp |
| goal-drift rate | **0.0%** | 50.0% | −50.0pp |
| doom-loops / trial | **0.10** | 0.44 | −0.34 |

Paired success delta **+0.418 [+0.327, +0.509]** (95% bootstrap CI); McNemar
exact **p ≈ 2.8e-14** (helped 46, hurt 0). ⚠️ This is a **harness-validation**
run, not a real-model capability claim: the numbers come from a *simulated* agent
with known ground-truth effects, used to prove the harness **detects an effect it
knows exists** (see DESIGN §5.8). For **real** metrics, the same harness runs
against real Claude with **executable verification** (DESIGN §5.9, already
working): `python scripts/smoke_sdk.py v01-slugify-verified`.

## Quickstart

```bash
pip install -e .

python scripts/seed_tasks.py     # generate + validate the synthetic task suite
lhx-eval validate                # reference-solution sanity check (graders not vacuous)
lhx-eval run -k 10               # the paired A/B → runs/latest/{results.json,dashboard.html}
pytest -q                        # 43 unit + integration tests

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

# REAL grading: run a task that is verified by executable checks (not self-report)
python scripts/smoke_sdk.py v01-slugify-verified        # runs the produced code
python scripts/smoke_sdk.py v02-health-endpoint-verified # starts the server & probes it

# integration check on a self-report task (no executable verify):
python scripts/smoke_sdk.py                 # default task r01-hello-endpoint

# a REAL, executable-graded A/B over the verified tasks (ON vs OFF, ~$0.20):
LHX_SDK_MAX_TURNS=25 lhx-eval run --backend sdk --verified-only -k 1
# → all trials graded by executable checks; ON fires hooks, OFF inert; Δ≈0 on
#   these short tasks (a correct negative control — the module helps on long tasks).

# the full paired A/B against real Claude over the whole suite:
lhx-eval run -k 3 --backend sdk
```

`.env` knobs (see [.env.template](.env.template)): `LHX_SDK_TRANSPORT`
(`auto`|`sdk`|`cli`), `LHX_SDK_MODEL`, `LHX_SDK_MAX_TURNS`, `LHX_SDK_TIMEOUT`.
Transport `auto` uses the `claude` CLI if it's on PATH, else the Python
`claude_agent_sdk`. The backend installs the module's hooks into an isolated
sandbox and toggles arms via `LHX_ENABLED` — so ON vs OFF holds everything else
fixed, exactly like the offline run.

> Note: a live run is **billed and non-deterministic**, so it's a manual check,
> not part of `pytest`. The integration *seam* is covered offline by a mocked
> smoke test ([tests/test_backend_sdk.py](tests/test_backend_sdk.py)).

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
  sandbox.py             #   isolated, seeded, torn-down per-trial workspace
  graders.py             #   deterministic outcome graders + partial credit
  metrics.py             #   pass@k / pass^k curves, long-horizon metrics
  stats.py               #   paired bootstrap CI, McNemar exact, Beta posterior
  runner.py              #   the paired A/B driver
  report.py              #   results.json + static HTML dashboard
  cli.py                 #   `lhx-eval run|validate`
  tasks/                 #   JSON task schema + synthetic suite
scripts/                 # seed_tasks.py, install.sh, run_eval.sh
tests/                   # 43 tests: math, guards, state, hooks, end-to-end smoke
```

## What's real vs needs credentials

Everything is implemented and tested offline (simulated backend). The **real**
backend (`ClaudeAgentSDKBackend`) is fully working and **validated live**: it preps
an isolated sandbox, installs the module's hooks (`--setting-sources project`),
wires the ON/OFF arm via `LHX_ENABLED`, runs real Claude (`claude -p` or the Python
Agent SDK), captures cost/tokens (`--output-format json`), and grades the produced
workspace with **executable verification** (`v01`/`v02` above). It just needs the
`claude` CLI + `ANTHROPIC_API_KEY`. What remains future work: a *large* verified
task suite and container isolation (see DESIGN §7) for a full real-model benchmark.

See **[DESIGN.md](DESIGN.md)** for the full methodology, the eval-modes section,
and "how I validated the eval itself" (incl. two real bugs the self-check caught).
