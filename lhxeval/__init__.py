"""lhxeval — the evaluation harness (the centerpiece).

A controlled, paired A/B evaluator for the long-horizon module. It holds the
model, the agent harness, the task suite and the random seeds fixed, and toggles
exactly one variable: the long-horizon module ON vs OFF. It runs k trials per
(task, seed, arm) in isolated sandboxes, grades outcomes (not paths) with
deterministic graders, and reports capability/regression metrics plus
long-horizon-specific metrics (compaction-survival, resume-after-interruption,
goal-drift, doom-loop incidence) with honest uncertainty (paired bootstrap CIs,
McNemar, Beta posteriors).
"""

__all__ = []
