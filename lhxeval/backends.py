"""Agent backends — what actually "runs" a task under an arm's configuration.

Two backends behind one interface:

* ``SimulatedBackend`` (default) — a deterministic, seedable model of a
  long-horizon agent run with **known ground-truth effects**. Crucially, the
  mitigations it applies are gated on the *real* ``lhx.Config`` toggles, so
  flipping ``config.enabled`` (or one primitive) changes behaviour exactly as the
  deployed module would. This lets the entire A/B run offline with no API key,
  and — more importantly for an Eval Engineer — lets us *validate the eval
  harness itself* against a ground truth before trusting it on real models.

* ``ClaudeAgentSDKBackend`` — runs real Claude through the Claude Agent SDK in an
  isolated sandbox with the module's hooks installed (or not, for the OFF arm),
  and reconstructs the trajectory from the message stream / event trail. Imported
  lazily so the package works without the SDK installed.

A backend returns a ``RunOutcome`` describing the produced artifact and the
trajectory; **graders** then decide success from the artifact (outcome, not
path).
"""

from __future__ import annotations

import hashlib
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from lhx.config import Config

from .tasks.schema import Task


@dataclass
class RunOutcome:
    # produced artifact: feature_id -> text the agent "wrote" for it
    artifact: dict[str, str] = field(default_factory=dict)
    features_completed: list[str] = field(default_factory=list)
    steps: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    doom_loops: int = 0
    forced_compaction: bool = False
    interrupted: bool = False
    resumed_ok: bool = False
    drifted: bool = False
    events: list[dict] = field(default_factory=list)

    def artifact_text(self) -> str:
        return "\n".join(self.artifact.values())


@dataclass
class Directives:
    """Per-trial perturbations the harness injects to probe long-horizon ability."""

    force_compaction_boundaries: int = 0
    interrupt_at_fraction: float | None = None  # e.g. 0.5 → kill halfway


class AgentBackend(ABC):
    name: str = "abstract"

    @abstractmethod
    def run(self, task: Task, config: Config, seed: int, directives: Directives) -> RunOutcome:
        ...


class SimulatedBackend(AgentBackend):
    """Deterministic simulation of a long-horizon run with known effects."""

    name = "simulated"

    @staticmethod
    def _stable_seed(*parts) -> int:
        # str.__hash__ is salted per-process (PYTHONHASHSEED); use a stable digest
        # so results reproduce across separate invocations.
        key = "|".join(str(p) for p in parts).encode()
        return int.from_bytes(hashlib.sha256(key).digest()[:8], "big")

    def run(self, task: Task, config: Config, seed: int, directives: Directives) -> RunOutcome:
        rng = random.Random(self._stable_seed(task.id, seed, config.enabled))
        sim = task.simulation
        out = RunOutcome()

        # Effective module switches (master switch gates everything).
        on = config.enabled
        ledger = on and config.progress_ledger
        guard = on and config.loop_guard
        reflect = on and config.reflection
        drift_guard = on and config.drift_check
        checkpoint = on and config.checkpointing

        n_features = task.n_features
        boundaries = max(task.compaction_boundaries, directives.force_compaction_boundaries)
        out.forced_compaction = boundaries > 0

        # Decide an interruption point (fraction of features).
        interrupt_after = None
        if task.interruption or directives.interrupt_at_fraction is not None:
            frac = directives.interrupt_at_fraction or 0.5
            interrupt_after = max(1, int(round(frac * n_features)))
            out.interrupted = True

        completed: list[str] = []
        artifact: dict[str, str] = {}
        steps = 0
        doom_loops = 0
        drifted_any = False
        goal_lost = False  # set by compaction amnesia → premature victory

        def emit(ev: dict) -> None:
            out.events.append(ev)

        for idx, feat in enumerate(task.features):
            # --- compaction boundary handling -----------------------------
            # Spread boundaries across the feature list.
            crossed_boundary = boundaries > 0 and idx > 0 and (
                idx % max(1, n_features // (boundaries + 1)) == 0
            )
            if crossed_boundary:
                emit({"type": "compaction"})
                if not ledger and rng.random() < sim.compaction_amnesia_prob:
                    # No external memory → the post-compaction session loses the
                    # goal and "declares victory" early. Remaining features never
                    # get done.
                    goal_lost = True
                    emit({"type": "goal_lost_after_compaction"})
                    break

            # --- interruption + resume ------------------------------------
            if interrupt_after is not None and idx == interrupt_after:
                emit({"type": "interrupt"})
                if checkpoint:
                    out.resumed_ok = True
                    emit({"type": "resume", "ok": True})
                else:
                    # Cold restart with no checkpoint.
                    if rng.random() < sim.cold_resume_fail_prob:
                        emit({"type": "resume", "ok": False})
                        goal_lost = True
                        break
                    out.resumed_ok = True
                    emit({"type": "resume", "ok": True})

            # --- doom loops on this feature -------------------------------
            feature_steps = sim.steps_per_feature
            if not guard:
                # Without the guard, retries can pile up; occasionally fatal.
                loops_here = 0
                for _ in range(feature_steps):
                    if rng.random() < sim.base_doom_loop_prob:
                        loops_here += 1
                doom_loops += loops_here
                feature_steps += loops_here * 3  # wasted steps
                if loops_here >= 3:
                    # Stuck in a loop the agent can't break → feature fails.
                    emit({"type": "stuck", "feature": feat.id})
                    steps += feature_steps
                    continue
            else:
                # Guard catches repeats fast: at most one short loop, recovered.
                if rng.random() < sim.base_doom_loop_prob:
                    doom_loops += 1
                    emit({"type": "guard_block", "kind": "doom_loop"})
                    feature_steps += 1

            # --- goal drift ------------------------------------------------
            feature_text = " ".join(feat.requires) if feat.requires else feat.id
            if not (drift_guard or reflect):
                if rng.random() < sim.drift_prob_per_feature:
                    # Drifted: produce something adjacent that misses requirements.
                    drifted_any = True
                    feature_text = f"partial-{feat.id}"  # omits required tokens
                    emit({"type": "drift", "feature": feat.id})

            # --- irreducible difficulty (affects both arms) ---------------
            steps += feature_steps
            if sim.residual_fail_prob > 0 and rng.random() < sim.residual_fail_prob:
                emit({"type": "feature_failed", "feature": feat.id})
                continue  # feature left incomplete; artifact missing requires

            artifact[feat.id] = feature_text
            completed.append(feat.id)
            emit({"type": "feature_done", "feature": feat.id})

        out.artifact = artifact
        out.features_completed = completed
        out.drifted = drifted_any or goal_lost
        out.doom_loops = doom_loops
        out.steps = steps
        out.tokens = steps * sim.tokens_per_step
        out.cost_usd = out.tokens / 1000.0 * sim.usd_per_1k_tokens
        return out


class ClaudeAgentSDKBackend(AgentBackend):
    """Runs real Claude via the Claude Agent SDK in an isolated sandbox.

    The module is wired in by copying ``claude_config/`` into the sandbox's
    ``.claude/`` for the ON arm; the OFF arm runs with ``LHX_ENABLED=false`` (same
    config file, module inert), holding the agent harness identical.
    """

    name = "claude-sdk"

    def __init__(self, model: str = "claude-haiku-4-5-20251001", max_budget_usd: float = 1.0):
        self.model = model
        self.max_budget_usd = max_budget_usd

    def run(self, task: Task, config: Config, seed: int, directives: Directives) -> RunOutcome:
        try:
            import anyio
            from claude_agent_sdk import query, ClaudeAgentOptions  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on optional dep
            raise RuntimeError(
                "ClaudeAgentSDKBackend requires `pip install claude-agent-sdk` and "
                "ANTHROPIC_API_KEY. Use the simulated backend for offline runs."
            ) from exc

        # NOTE: sandbox setup (clean workspace, BRIEF/feature_list seeding, hook
        # install, interruption injection) is handled by the runner via
        # lhxeval.sandbox; this backend assumes config.* paths point inside the
        # already-prepared sandbox. Full implementation is out of scope for the
        # offline 2-day deliverable but the seam is real.
        raise NotImplementedError(
            "Wire ClaudeAgentSDKBackend.run to query() with permissionMode, "
            "allowedTools, hooks and max_budget_usd; reconstruct RunOutcome from "
            "the message stream + .lh/events.jsonl. See DESIGN.md §7."
        )


def get_backend(name: str, **kwargs) -> AgentBackend:
    if name in ("sim", "simulated"):
        return SimulatedBackend()
    if name in ("sdk", "claude", "claude-sdk"):
        return ClaudeAgentSDKBackend(**kwargs)
    raise ValueError(f"unknown backend: {name}")
