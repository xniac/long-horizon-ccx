"""Glue that builds the module's stateful objects from a working directory.

Hooks are thin: they parse the Claude Code hook JSON, build a ``Runtime`` for
the session's working directory, and call into the primitives. Keeping the wiring
here (not in each hook) means the same logic is reachable from the eval harness's
in-process backend without shelling out.
"""

from __future__ import annotations

from pathlib import Path

from .checkpoint import load_checkpoint, save_checkpoint
from .config import Config
from .memory import Memory
from .state import FeatureList, ProgressLedger


class Runtime:
    def __init__(self, cwd: Path, config: Config):
        self.cwd = Path(cwd)
        self.config = config

        self.progress_path = self.cwd / config.progress_file
        self.feature_path = self.cwd / config.feature_file
        self.brief_path = self.cwd / config.brief_file
        self.memory_path = self.cwd / config.memory_file
        self.kill_switch_path = self.cwd / config.kill_switch_file
        self.steer_path = self.cwd / config.steer_file

        state_dir = self.cwd / config.state_dir
        self.events_path = state_dir / "events.jsonl"
        self.checkpoint_path = state_dir / "checkpoint.json"
        self.signatures_path = state_dir / "signatures.json"

        self.ledger = ProgressLedger(self.progress_path, self.events_path)
        self.features = FeatureList.load(self.feature_path)
        self.memory = Memory(self.brief_path, self.memory_path, config.memory_char_cap)

    # --- tool-call counting / signatures (used by loop guard + reflection) --
    def tool_call_count(self) -> int:
        return len(self.ledger.tool_events())

    def signatures(self) -> list[str]:
        return [e["sig"] for e in self.ledger.tool_events() if "sig" in e]

    # --- checkpoint helpers -------------------------------------------------
    def load_checkpoint(self) -> dict:
        return load_checkpoint(self.checkpoint_path)

    def save_checkpoint(self, data: dict) -> None:
        save_checkpoint(self.checkpoint_path, data)
