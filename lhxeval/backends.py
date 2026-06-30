"""Agent backends behind one interface; a backend returns a ``RunOutcome`` that
graders score by outcome (not path). See DESIGN §5.3-7 / §8.8.

* ``SimulatedBackend`` (default, offline) — deterministic model of a long-horizon
  run whose mitigations are gated on the *real* ``lhx.Config`` toggles, so it
  validates the eval harness against a known ground truth before trusting it.
* ``ClaudeAgentSDKBackend`` — real Claude via the Python Agent SDK or `claude -p`.
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
    """Runs real Claude in an isolated sandbox, via either the Python Agent SDK
    (``claude_agent_sdk.query()``) or the headless ``claude -p`` CLI.

    The module is wired into the sandbox by installing the drop-in ``.claude/``
    config; the only A/B difference is ``LHX_ENABLED`` (same config, inert when
    off), holding the agent harness identical. The trajectory is reconstructed
    from the on-disk artifacts the module writes (``.lh/events.jsonl`` +
    ``feature_list.json``) — so the *outcome* grading is transport-agnostic; only
    cost/token capture differs (the SDK path reads it from the ResultMessage).

    Transport is chosen by ``LHX_SDK_TRANSPORT`` (``auto``|``sdk``|``cli``); auto
    prefers the CLI if present, else the SDK. Both need ANTHROPIC_API_KEY. The CLI
    path is covered by a mocked smoke test; the SDK path is written to the
    documented API and validated with a live key (see scripts/smoke_sdk.py).
    """

    name = "claude-sdk"

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        max_turns: int = 80,
        timeout_seconds: int = 900,
        max_budget_usd: float = 1.0,
        transport: str = "auto",
        permission_mode: str = "bypassPermissions",
        keep_sandbox: bool = False,
    ):
        self.model = model
        self.max_turns = max_turns
        self.timeout_seconds = timeout_seconds
        self.max_budget_usd = max_budget_usd
        self.transport = transport
        # Headless tool use needs a non-interactive permission mode, or the CLI
        # blocks on a permission/trust prompt and looks "stuck". Sandboxes are
        # disposable, so bypassPermissions is appropriate here.
        self.permission_mode = permission_mode
        self.keep_sandbox = keep_sandbox
        # Diagnostics from the most recent run (for smoke/debug).
        self.last_cli: dict | None = None
        self.last_workspace: str | None = None

    def _resolve_transport(self) -> str:
        import importlib.util
        import os
        import shutil as _shutil

        t = os.environ.get("LHX_SDK_TRANSPORT", self.transport or "auto").lower()
        if t in ("sdk", "cli"):
            return t
        if _shutil.which("claude") is not None:
            return "cli"
        if importlib.util.find_spec("claude_agent_sdk") is not None:
            return "sdk"
        raise RuntimeError(
            "No transport available: install the `claude` CLI or "
            "`pip install claude-agent-sdk`, and set ANTHROPIC_API_KEY. "
            "Use --backend simulated for offline runs."
        )

    def run(self, task: Task, config: Config, seed: int, directives: Directives) -> RunOutcome:
        import os

        from lhx.state import FeatureList

        from .sandbox import trial_sandbox

        transport = self._resolve_transport()
        with trial_sandbox(
            task, init_git=True, install_module=True, keep=self.keep_sandbox
        ) as ws:
            self.last_workspace = str(ws)
            env = os.environ.copy()
            env["LHX_ENABLED"] = "true" if config.enabled else "false"
            # LHX_CONFIG is a *file path* (carries per-primitive ablation), not a
            # JSON blob — a blob overflows NAME_MAX and crashes the hooks.
            cfg_file = ws / ".lh" / "config.json"
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(config.model_dump_json(), encoding="utf-8")
            env["LHX_CONFIG"] = str(cfg_file)

            cost = (
                self._run_cli(ws, env, task.prompt)
                if transport == "cli"
                else self._run_sdk(ws, env, task.prompt)
            )
            outcome = self._reconstruct_outcome(ws, FeatureList)
            outcome.tokens = cost.get("tokens", 0)
            outcome.cost_usd = cost.get("usd", 0.0)
            return outcome

    def _run_cli(self, ws, env, prompt: str) -> dict:
        import shutil as _shutil
        import subprocess

        if _shutil.which("claude") is None:
            raise RuntimeError("transport=cli but `claude` CLI is not on PATH.")
        import json

        cmd = ["claude", "-p", "--model", self.model, "--max-turns", str(self.max_turns)]
        # CRITICAL: as of Claude Code 2.x, project settings (our hooks) are NOT
        # loaded unless opted in — without this the module is inert and the A/B
        # measures nothing. (See SDK path: setting_sources=["project"].)
        cmd += ["--setting-sources", "user,project,local"]
        cmd += ["--output-format", "json"]  # machine-readable result incl. cost/usage
        if self.permission_mode and self.permission_mode != "default":
            cmd += ["--permission-mode", self.permission_mode]  # don't hang on prompts
        try:
            proc = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True,
                timeout=self.timeout_seconds, cwd=str(ws), env=env, check=False,
            )
            self.last_cli = {
                "cmd": " ".join(cmd),
                "returncode": proc.returncode,
                "stdout": proc.stdout or "",
                "stderr": proc.stderr or "",
            }
        except subprocess.TimeoutExpired:
            self.last_cli = {"cmd": " ".join(cmd), "returncode": "TIMEOUT",
                             "stdout": "", "stderr": "timed out"}
            return {}

        # Parse cost/usage from the JSON result envelope.
        cost: dict = {}
        try:
            data = json.loads(self.last_cli["stdout"])
            if isinstance(data, dict):
                cost["usd"] = data.get("total_cost_usd") or 0.0
                usage = data.get("usage") or {}
                cost["tokens"] = (usage.get("input_tokens", 0) or 0) + (
                    usage.get("output_tokens", 0) or 0
                )
                self.last_cli["result_text"] = data.get("result", "")
        except (ValueError, TypeError):
            pass  # non-JSON (e.g. error/timeout); cost stays empty
        return cost

    def _run_sdk(self, ws, env, prompt: str) -> dict:
        """Drive the in-process Agent SDK. Captures cost/usage from ResultMessage."""
        import os

        import anyio
        from claude_agent_sdk import ClaudeAgentOptions, query  # type: ignore

        for k, v in env.items():  # ensure the subprocessless SDK sees LHX_*/key
            os.environ[k] = v

        cost = {"usd": 0.0, "tokens": 0}

        async def _go():
            # setting_sources=["project"] loads the sandbox .claude/ (hooks +
            # CLAUDE.md); if your SDK version lacks a kwarg, drop to the minimal form.
            try:
                options = ClaudeAgentOptions(
                    cwd=str(ws), permission_mode="bypassPermissions",
                    max_turns=self.max_turns, setting_sources=["project"],
                )
            except TypeError:
                options = ClaudeAgentOptions(cwd=str(ws), permission_mode="bypassPermissions")
            async for msg in query(prompt=prompt, options=options):
                tc = getattr(msg, "total_cost_usd", None)
                if tc is not None:
                    cost["usd"] = tc
                usage = getattr(msg, "usage", None)
                if isinstance(usage, dict):
                    cost["tokens"] = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)

        anyio.run(_go)
        return cost

    @staticmethod
    def _reconstruct_outcome(ws, feature_list_cls) -> RunOutcome:
        """Rebuild a RunOutcome from the artifacts the module left on disk —
        identical for both transports, since state lives on disk."""
        import json
        from pathlib import Path

        events: list[dict] = []
        events_path = Path(ws) / ".lh" / "events.jsonl"
        if events_path.exists():
            for ln in events_path.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if ln:
                    try:
                        events.append(json.loads(ln))
                    except json.JSONDecodeError:
                        continue

        fl = feature_list_cls.load(Path(ws) / "feature_list.json")
        done = [f for f in fl.features if f.passes]
        tool_events = [e for e in events if e.get("type") == "tool_use"]
        guard_blocks = [e for e in events if e.get("type") == "guard_block"]

        return RunOutcome(
            artifact={f.id: (f.evidence or f.id) for f in done},
            features_completed=[f.id for f in done],
            steps=len(tool_events),
            doom_loops=sum(1 for e in guard_blocks if e.get("kind") == "doom_loop"),
            forced_compaction=any(e.get("type") == "compaction" for e in events),
            events=events,
        )


def get_backend(name: str, **kwargs) -> AgentBackend:
    if name in ("sim", "simulated"):
        return SimulatedBackend()
    if name in ("sdk", "claude", "claude-sdk", "cli"):
        return ClaudeAgentSDKBackend(**kwargs)
    raise ValueError(f"unknown backend: {name}")
