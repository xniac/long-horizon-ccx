"""Task schema (JSON, zero parser dep), validated via pydantic.

Key fields and why: ``reference_solution`` proves solvability (the 0%-pass sanity
check); ``difficulty`` splits a capability vs regression suite; ``simulation``
parameterises the simulated backend so one task definition drives both backends.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class FeatureSpec(BaseModel):
    id: str
    description: str
    # Substrings that must all appear in the produced artifact for this feature
    # to be considered done by the deterministic grader (outcome check).
    requires: list[str] = Field(default_factory=list)
    weight: float = 1.0


class SimulationParams(BaseModel):
    """Ground-truth difficulty knobs for the deterministic simulated backend.

    These define a *known* effect so we can validate that the eval harness
    detects what it should. They are ignored by the real SDK backend.
    """

    # Expected number of steps to complete one feature.
    steps_per_feature: int = 6
    # Per-step probability the agent slips into a doom loop (OFF arm only,
    # since the loop guard is what suppresses it).
    base_doom_loop_prob: float = 0.05
    # Probability that crossing a compaction boundary loses the goal when there
    # is NO external progress ledger (OFF). With the ledger (ON) ~0.
    compaction_amnesia_prob: float = 0.6
    # Per-feature probability of slow goal drift without reflection/brief (OFF).
    drift_prob_per_feature: float = 0.08
    # Probability an interrupted+cold-restarted session fails to recover (OFF).
    cold_resume_fail_prob: float = 0.7
    # Tokens consumed per step (used for token/cost aggregates).
    tokens_per_step: int = 1500
    usd_per_1k_tokens: float = 0.003
    # Irreducible per-feature failure probability that the module CANNOT fix
    # (genuine task difficulty). Applies to BOTH arms, so it shows up as
    # pass@1 < 100% and pass^k < pass@1 even with the module on — which is the
    # realistic regime and the reason pass^k matters.
    residual_fail_prob: float = 0.0


class VerifyCheck(BaseModel):
    """An executable, outcome-based grader (F2P/P2P style). The ``cmd`` is run in
    the produced workspace after a real run; exit code 0 == passed. This grades
    what was *built*, not what the agent *claims* — the only honest signal for a
    real-model A/B."""

    id: str
    cmd: str
    weight: float = 1.0


class Task(BaseModel):
    id: str
    title: str
    goal: str
    prompt: str
    difficulty: str = "capability"      # "capability" | "regression"
    features: list[FeatureSpec]
    # How many compaction boundaries this task is expected to cross.
    compaction_boundaries: int = 0
    # If true, the harness will interrupt mid-task and require resume.
    interruption: bool = False
    reference_solution: str = ""         # proves solvability; used by sanity check
    # Shell command run in the fresh workspace BEFORE the agent starts, to seed
    # initial state (mirrors OSWorld's `config` setup / Harbor task fixtures).
    # Used to seed a large repo that forces real context pressure. Real backend only.
    setup: str = ""
    simulation: SimulationParams = Field(default_factory=SimulationParams)
    # Executable verification for REAL runs (the SDK/CLI backend). When present
    # and run against a real workspace, these — not the agent's self-report —
    # decide success. The simulated backend ignores them (it uses ``features``).
    verify: list[VerifyCheck] = Field(default_factory=list)

    @property
    def n_features(self) -> int:
        return len(self.features)

    @classmethod
    def load(cls, path: Path) -> "Task":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


def load_suite(directory: Path) -> list[Task]:
    tasks = [Task.load(p) for p in sorted(Path(directory).glob("*.json"))]
    ids = [t.id for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate task ids in {directory}")
    return tasks
