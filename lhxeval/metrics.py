"""Long-horizon metrics — pure Python (auditable, no numpy). See DESIGN §8.4.

* **pass@k** = P(≥1 of k succeeds) = ``1 − C(n−c,k)/C(n,k)`` (Chen et al.); rises with k.
* **pass^k** = P(all k succeed) = ``C(c,k)/C(n,k)``; falls with k — the reliability metric.

Plus trajectory aggregates (steps/tokens/cost). Estimators clamp when k > n.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import comb
from statistics import mean
from typing import Sequence


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k estimator (Chen et al. 2021)."""
    if k <= 0 or n <= 0:
        return 0.0
    if c < 0:
        c = 0
    if c >= n:
        return 1.0
    if k > n:
        k = n
    # 1 - C(n-c, k)/C(n, k); guard the case n-c < k → no all-fail subset exists.
    if n - c < k:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)


def pass_caret_k(n: int, c: int, k: int) -> float:
    """Unbiased pass^k estimator: P(a random k-subset of trials are all successes)."""
    if k <= 0 or n <= 0:
        return 0.0
    if k > n:
        k = n
    if c < k:
        return 0.0
    return comb(c, k) / comb(n, k)


# k used for the headline reliability number (pass^k). Moderate, not all-of-n:
# pass^n collapses to all-or-nothing per task and stops discriminating.
HEADLINE_RELIABILITY_K = 3


@dataclass
class ArmMetrics:
    arm: str
    n_trials: int
    n_success: int
    k: int                        # trials per task
    pass_at_1: float
    pass_caret_k: float           # headline reliability at k=HEADLINE_RELIABILITY_K
    reliability_k: int            # the k used for the headline pass^k
    pass_at_curve: dict           # k -> macro pass@k
    pass_caret_curve: dict        # k -> macro pass^k
    mean_steps: float
    mean_tokens: float
    mean_cost_usd: float
    compaction_survival: float | None   # success rate among forced-compaction trials
    resume_success: float | None        # success rate among interruption trials
    drift_rate: float                    # fraction of trials whose outcome drifted off-spec
    doom_loop_rate: float                # mean doom-loop episodes per trial

    def as_row(self) -> dict:
        return self.__dict__.copy()


def _safe_rate(num: int, den: int) -> float | None:
    return (num / den) if den else None


def _group_by_task(trials: Sequence["TrialResult"]) -> dict[str, list]:  # noqa: F821
    groups: dict[str, list] = {}
    for t in trials:
        groups.setdefault(t.task_id, []).append(t)
    return groups


def summarize_arm(arm: str, trials: Sequence["TrialResult"]) -> ArmMetrics:  # noqa: F821
    """Aggregate a list of TrialResult into ArmMetrics.

    pass@1 and pass^k are computed **per task** (so a task's difficulty is not
    conflated with another's) and then **macro-averaged** across tasks — the
    standard reliability framing (cf. tau-bench pass^k). k is the number of
    trials per task. Trajectory aggregates are means over all trials.
    """
    n = len(trials)
    c = sum(1 for t in trials if t.success)

    by_task = _group_by_task(trials)
    k = max((len(v) for v in by_task.values()), default=0)
    # per-task (n_trials, n_success) tuples
    counts = [(len(v), sum(t.success for t in v)) for v in by_task.values()]

    def macro_pass_at(kk: int) -> float:
        vals = [pass_at_k(nn, cc, kk) for nn, cc in counts]
        return mean(vals) if vals else 0.0

    def macro_pass_caret(kk: int) -> float:
        vals = [pass_caret_k(nn, cc, kk) for nn, cc in counts]
        return mean(vals) if vals else 0.0

    # Curves over k=1..k (macro-averaged across tasks).
    pass_at_curve = {kk: macro_pass_at(kk) for kk in range(1, k + 1)}
    pass_caret_curve = {kk: macro_pass_caret(kk) for kk in range(1, k + 1)}
    rel_k = min(HEADLINE_RELIABILITY_K, k) if k else 0

    comp = [t for t in trials if t.forced_compaction]
    comp_succ = sum(1 for t in comp if t.success)
    intr = [t for t in trials if t.interrupted]
    intr_succ = sum(1 for t in intr if t.success)

    return ArmMetrics(
        arm=arm,
        n_trials=n,
        n_success=c,
        k=k,
        pass_at_1=macro_pass_at(1),
        pass_caret_k=macro_pass_caret(rel_k),
        reliability_k=rel_k,
        pass_at_curve=pass_at_curve,
        pass_caret_curve=pass_caret_curve,
        mean_steps=mean([t.steps for t in trials]) if trials else 0.0,
        mean_tokens=mean([t.tokens for t in trials]) if trials else 0.0,
        mean_cost_usd=mean([t.cost_usd for t in trials]) if trials else 0.0,
        compaction_survival=_safe_rate(comp_succ, len(comp)),
        resume_success=_safe_rate(intr_succ, len(intr)),
        drift_rate=mean([1.0 if t.drifted else 0.0 for t in trials]) if trials else 0.0,
        doom_loop_rate=mean([t.doom_loops for t in trials]) if trials else 0.0,
    )
