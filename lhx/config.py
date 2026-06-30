"""Configuration for the long-horizon module.

A single ``Config`` object controls every primitive. Each guardrail can be
toggled independently, which is what lets the eval harness flip exactly one
variable (the whole module, or one primitive) while holding everything else
fixed. Values are overridable by environment variable so the same hook scripts
can be reconfigured per trial without editing code.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class Config(BaseModel):
    """Resolved configuration for one Claude Code session / eval trial."""

    # --- master switch (the A/B independent variable) ---------------------
    enabled: bool = True

    # --- per-primitive toggles -------------------------------------------
    progress_ledger: bool = True
    checkpointing: bool = True
    loop_guard: bool = True
    reflection: bool = True
    drift_check: bool = True
    completion_gate: bool = True

    # --- on-disk locations (relative to the working dir) ------------------
    state_dir: str = ".lh"
    progress_file: str = "PROGRESS.md"
    feature_file: str = "feature_list.json"
    brief_file: str = "BRIEF.md"
    memory_file: str = "MEMORY.md"
    kill_switch_file: str = "AGENT_STOP"
    steer_file: str = "STEER.md"

    # --- thresholds -------------------------------------------------------
    # Doom-loop: how many *identical* recent (tool, args) tuples trigger a block.
    doom_loop_window: int = 3
    # Circuit breaker: hard cap on tool calls in a single session.
    step_budget: int = 400
    # Reflection: inject a "step back and reflect" nudge every N tool calls.
    reflection_interval: int = 8
    # Memory: cap the rolling MEMORY.md scratchpad (chars) so it never bloats.
    memory_char_cap: int = 2000

    @classmethod
    def from_env(cls) -> "Config":
        """Build a Config from LHX_* environment variables (used by hooks).

        ``LHX_CONFIG`` may be a path to a JSON file *or* an inline JSON string.
        This must never raise — a hook that crashes on a bad env value silently
        disables the whole module (a long, costly bug to find), so every step
        here is guarded.
        """
        base: dict = {}
        raw = os.environ.get("LHX_CONFIG")
        if raw:
            text = None
            try:
                p = Path(raw)
                if len(raw) < 4096 and p.is_file():
                    text = p.read_text(encoding="utf-8")
            except OSError:
                text = None  # e.g. "File name too long" when raw is inline JSON
            if text is None and raw.lstrip().startswith("{"):
                text = raw  # inline JSON
            if text:
                try:
                    base = json.loads(text)
                except (ValueError, TypeError):
                    base = {}

        cfg = cls(**base)
        cfg.enabled = _env_bool("LHX_ENABLED", cfg.enabled)
        cfg.progress_ledger = _env_bool("LHX_PROGRESS_LEDGER", cfg.progress_ledger)
        cfg.checkpointing = _env_bool("LHX_CHECKPOINTING", cfg.checkpointing)
        cfg.loop_guard = _env_bool("LHX_LOOP_GUARD", cfg.loop_guard)
        cfg.reflection = _env_bool("LHX_REFLECTION", cfg.reflection)
        cfg.drift_check = _env_bool("LHX_DRIFT_CHECK", cfg.drift_check)
        cfg.completion_gate = _env_bool("LHX_COMPLETION_GATE", cfg.completion_gate)
        cfg.doom_loop_window = _env_int("LHX_DOOM_LOOP_WINDOW", cfg.doom_loop_window)
        cfg.step_budget = _env_int("LHX_STEP_BUDGET", cfg.step_budget)
        cfg.reflection_interval = _env_int(
            "LHX_REFLECTION_INTERVAL", cfg.reflection_interval
        )
        cfg.memory_char_cap = _env_int("LHX_MEMORY_CHAR_CAP", cfg.memory_char_cap)
        return cfg

    def workdir_path(self, base: str | os.PathLike[str], name: str) -> Path:
        return Path(base) / name

    def state_path(self, base: str | os.PathLike[str], name: str) -> Path:
        d = Path(base) / self.state_dir
        d.mkdir(parents=True, exist_ok=True)
        return d / name


# Default singleton convenience for hooks that don't want to thread config.
def load_config() -> Config:
    return Config.from_env()
