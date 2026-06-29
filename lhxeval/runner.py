"""A/B driver — the controlled experiment.

Independent variable: the long-horizon module (ON vs OFF). Everything else is
held fixed — same model/backend, same task suite, same seeds, same agent
harness. We use a **paired** design: for each (task, seed) we run both arms with
that same seed, so the per-pair difference cancels task/seed variance. We run
``k`` seeds per task.

The runner is backend-agnostic: it works identically with the simulated backend
(offline, deterministic) and the real Claude Agent SDK backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lhx.config import Config

from .backends import AgentBackend, Directives, SimulatedBackend, get_backend
from .graders import check_drift, grade_outcome
from .metrics import ArmMetrics, summarize_arm
from .results import TrialResult
from .stats import CI, McNemarResult, beta_rate, mcnemar_exact, paired_bootstrap_diff
from .tasks.schema import Task


def arm_config(arm: str) -> Config:
    """The two arms differ only in the module master switch."""
    if arm == "on":
        return Config(enabled=True)
    if arm == "off":
        return Config(enabled=False)
    raise ValueError(arm)


def _directives_for(task: Task) -> Directives:
    return Directives(
        force_compaction_boundaries=task.compaction_boundaries,
        interrupt_at_fraction=0.5 if task.interruption else None,
    )


def run_trial(
    backend: AgentBackend, task: Task, arm: str, seed: int, trial_index: int
) -> TrialResult:
    cfg = arm_config(arm)
    outcome = backend.run(task, cfg, seed, _directives_for(task))
    grade = grade_outcome(task, outcome)
    # Ground-truth drift comes from the backend. The keyword-drift heuristic is
    # only meaningful on *real prose* artifacts, so we apply it as an extra
    # signal only for non-simulated backends (it would false-positive on the
    # simulated backend's synthetic token-bag artifacts).
    drifted = outcome.drifted
    if not isinstance(backend, SimulatedBackend):
        drifted = drifted or check_drift(task, outcome)
    return TrialResult(
        task_id=task.id,
        arm=arm,
        seed=seed,
        trial_index=trial_index,
        success=grade.success,
        partial_credit=grade.partial_credit,
        steps=outcome.steps,
        tokens=outcome.tokens,
        cost_usd=outcome.cost_usd,
        drifted=drifted,
        doom_loops=outcome.doom_loops,
        forced_compaction=outcome.forced_compaction,
        interrupted=outcome.interrupted,
        resumed_ok=outcome.resumed_ok,
        grader_detail=grade.detail,
        transcript=outcome.events,
    )


@dataclass
class ABResult:
    tasks: list[str]
    k: int
    seeds: list[int]
    on: ArmMetrics
    off: ArmMetrics
    trials: list[TrialResult] = field(default_factory=list)

    # paired statistics on the headline metrics
    success_ci: CI | None = None
    partial_ci: CI | None = None
    steps_ci: CI | None = None
    tokens_ci: CI | None = None
    mcnemar: McNemarResult | None = None

    def to_dict(self) -> dict:
        return {
            "tasks": self.tasks,
            "k": self.k,
            "seeds": self.seeds,
            "arms": {"on": self.on.as_row(), "off": self.off.as_row()},
            "paired_stats": {
                "success_diff_ci": str(self.success_ci) if self.success_ci else None,
                "partial_credit_diff_ci": str(self.partial_ci) if self.partial_ci else None,
                "steps_diff_ci": str(self.steps_ci) if self.steps_ci else None,
                "tokens_diff_ci": str(self.tokens_ci) if self.tokens_ci else None,
                "mcnemar": str(self.mcnemar) if self.mcnemar else None,
            },
            "trials": [t.to_dict() for t in self.trials],
        }


def run_ab(
    tasks: list[Task],
    *,
    backend: AgentBackend | None = None,
    k: int = 5,
    base_seed: int = 1000,
    bootstrap_iters: int = 10000,
) -> ABResult:
    backend = backend or SimulatedBackend()
    seeds = [base_seed + i for i in range(k)]

    trials: list[TrialResult] = []
    # paired vectors keyed implicitly by (task, seed) order
    on_success, off_success = [], []
    on_partial, off_partial = [], []
    on_steps, off_steps = [], []
    on_tokens, off_tokens = [], []

    for task in tasks:
        for i, seed in enumerate(seeds):
            r_on = run_trial(backend, task, "on", seed, i)
            r_off = run_trial(backend, task, "off", seed, i)
            trials.extend([r_on, r_off])

            on_success.append(1.0 if r_on.success else 0.0)
            off_success.append(1.0 if r_off.success else 0.0)
            on_partial.append(r_on.partial_credit)
            off_partial.append(r_off.partial_credit)
            on_steps.append(float(r_on.steps))
            off_steps.append(float(r_off.steps))
            on_tokens.append(float(r_on.tokens))
            off_tokens.append(float(r_off.tokens))

    on_trials = [t for t in trials if t.arm == "on"]
    off_trials = [t for t in trials if t.arm == "off"]

    return ABResult(
        tasks=[t.id for t in tasks],
        k=k,
        seeds=seeds,
        on=summarize_arm("on", on_trials),
        off=summarize_arm("off", off_trials),
        trials=trials,
        success_ci=paired_bootstrap_diff(on_success, off_success, iters=bootstrap_iters),
        partial_ci=paired_bootstrap_diff(on_partial, off_partial, iters=bootstrap_iters),
        steps_ci=paired_bootstrap_diff(on_steps, off_steps, iters=bootstrap_iters),
        tokens_ci=paired_bootstrap_diff(on_tokens, off_tokens, iters=bootstrap_iters),
        mcnemar=mcnemar_exact(
            [s > 0.5 for s in on_success], [s > 0.5 for s in off_success]
        ),
    )
