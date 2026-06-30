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

Simulated backend, 9 tasks × k=10 × 2 arms = 180 trials (`lhx-eval run -k 10`):

| metric | module ON | module OFF | Δ |
|---|---|---|---|
| pass@1 (macro) | **90.0%** | 38.9% | +51.1pp |
| pass^3 (reliability) | **73.6%** | 33.3% | +40.3pp |
| compaction-survival | **80.0%** | 2.5% | +77.5pp |
| resume-after-interruption | **93.3%** | 13.3% | +80.0pp |
| goal-drift rate | **0.0%** | 61.1% | −61.1pp |
| doom-loops / trial | **0.12** | 0.53 | −0.41 |

Paired success delta **+0.511 [+0.400, +0.611]** (95% bootstrap CI); McNemar
exact **p ≈ 2.8e-14** (helped 46, hurt 0). ⚠️ This is a **harness-validation**
run, not a real-model capability claim: the numbers come from a *simulated* agent
with known ground-truth effects, used to prove the harness **detects an effect it
knows exists** (see DESIGN §8.8). The same harness runs unchanged against real
Claude via the Agent-SDK / CLI backend.

## Quickstart

```bash
pip install -e .

python scripts/seed_tasks.py     # generate + validate the synthetic task suite
lhx-eval validate                # reference-solution sanity check (graders not vacuous)
lhx-eval run -k 10               # the paired A/B → runs/latest/{results.json,dashboard.html}
pytest -q                        # 37 unit + integration tests

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

# one tiny task, end-to-end, module ON — the simplest "does my key work?" check:
python scripts/smoke_sdk.py                 # default task r01-hello-endpoint
python scripts/smoke_sdk.py t01-multi-file-api

# the full paired A/B against real Claude instead of the simulated backend:
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
tests/                   # 37 tests: math, guards, state, hooks, end-to-end smoke
```

## What's real vs needs credentials

Everything is implemented and tested offline. The `lhx` module, the eval harness,
metrics, stats, graders, sandbox and dashboard are fully exercised by the
deterministic **simulated** backend. The **real** backend
(`ClaudeAgentSDKBackend`) is a complete headless-`claude -p` skeleton — it preps
an isolated sandbox, installs the module's hooks, wires the ON/OFF arm via
`LHX_ENABLED`, and reconstructs the trajectory from the on-disk artifacts the
module writes — and is covered by a **mocked smoke test**
([tests/test_backend_sdk.py](tests/test_backend_sdk.py)). The only thing it needs
to run live is the `claude` CLI on PATH + `ANTHROPIC_API_KEY`; token/cost parsing
from the session transcript is the one marked `TODO`.

See **[DESIGN.md](DESIGN.md)** for the full design, methodology, and the
"how I validated the eval itself" section.
