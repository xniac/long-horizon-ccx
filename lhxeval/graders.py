"""Graders — score the outcome, not the path (DESIGN §8.3). Deterministic and
outcome-based: each feature passes iff the produced artifact contains all its
``requires`` tokens (F2P/P2P-style); success = all features pass, with weighted
partial credit. Deterministic-first, because pass^k is only trustworthy once
graders are.
"""

from __future__ import annotations

from dataclasses import dataclass

from lhx.drift import keyword_drift

from .backends import RunOutcome
from .tasks.schema import Task


@dataclass
class GradeResult:
    success: bool
    partial_credit: float
    satisfied: list[str]
    unsatisfied: list[str]
    detail: dict


def grade_outcome(task: Task, outcome: RunOutcome) -> GradeResult:
    """Deterministic outcome grader with weighted partial credit."""
    satisfied: list[str] = []
    unsatisfied: list[str] = []
    weight_total = 0.0
    weight_ok = 0.0

    for feat in task.features:
        weight_total += feat.weight
        produced = outcome.artifact.get(feat.id, "")
        # Outcome check: every required token present in this feature's artifact.
        ok = all(req.lower() in produced.lower() for req in feat.requires) if feat.requires else (
            feat.id in outcome.features_completed
        )
        if ok:
            satisfied.append(feat.id)
            weight_ok += feat.weight
        else:
            unsatisfied.append(feat.id)

    partial = (weight_ok / weight_total) if weight_total else 0.0
    success = len(unsatisfied) == 0 and task.n_features > 0
    return GradeResult(
        success=success,
        partial_credit=partial,
        satisfied=satisfied,
        unsatisfied=unsatisfied,
        detail={
            "n_features": task.n_features,
            "n_satisfied": len(satisfied),
            "weight_ok": weight_ok,
            "weight_total": weight_total,
        },
    )


def check_drift(task: Task, outcome: RunOutcome) -> bool:
    """Independent goal-drift signal: did the produced artifact drift off the
    immutable brief? Uses the module's keyword-drift heuristic on the brief."""
    report = keyword_drift(task.goal, outcome.artifact_text())
    return report.drifted


def reference_solution_outcome(task: Task) -> RunOutcome:
    """Construct the outcome a correct reference solution would produce.

    Used by the sanity check (seed_tasks) to prove every task is solvable and the
    grader is not vacuous: the reference must score success=True, and an empty
    outcome must score success=False.
    """
    artifact = {f.id: " ".join(f.requires) if f.requires else f.id for f in task.features}
    return RunOutcome(
        artifact=artifact,
        features_completed=[f.id for f in task.features],
    )
