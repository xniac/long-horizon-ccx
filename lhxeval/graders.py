"""Graders — score the outcome, not the path. ``grade()`` dispatches between two
modes: **executable checks** (run the task's ``verify`` commands against the
produced workspace; real backend) and the **token grader** (the produced artifact
must contain each feature's ``requires`` tokens; simulated backend). Both give
weighted partial credit; success = everything passes. Deterministic-first, because
pass^k is only trustworthy once graders are.
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


def grade(task: Task, outcome: RunOutcome) -> GradeResult:
    """Pick the grader.

    If the task has executable ``verify`` checks and they were run, those checks
    are the **sole** success criterion — deliberately *not* blended with the token
    grader, since mixing executable truth with token-matching would dilute the
    real signal with noise. The token grader is only the fallback when no checks
    ran (e.g. a task without ``verify``)."""
    if task.verify and outcome.checks:
        return grade_checks(task, outcome)
    return grade_outcome(task, outcome)


def grade_checks(task: Task, outcome: RunOutcome) -> GradeResult:
    """Grade by executable verification results (F2P/P2P): success = all checks
    pass; weighted partial credit. Grades what was *built*, not the agent's claim."""
    satisfied, unsatisfied = [], []
    weight_total = weight_ok = 0.0
    for c in task.verify:
        weight_total += c.weight
        if outcome.checks.get(c.id):
            satisfied.append(c.id)
            weight_ok += c.weight
        else:
            unsatisfied.append(c.id)
    return GradeResult(
        success=(not unsatisfied and len(task.verify) > 0),
        partial_credit=(weight_ok / weight_total) if weight_total else 0.0,
        satisfied=satisfied,
        unsatisfied=unsatisfied,
        detail={"mode": "executable-checks", "n_checks": len(task.verify)},
    )


def grade_outcome(task: Task, outcome: RunOutcome) -> GradeResult:
    """Deterministic outcome grader with weighted partial credit (token-based;
    used by the simulated backend, where the artifact *is* the produced tokens)."""
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
