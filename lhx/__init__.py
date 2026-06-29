"""lhx — a long-horizon extension module for Claude Code.

The module wires three of Anthropic's long-horizon primitives — structured
note-taking (a progress ledger + default-FAIL feature list), checkpoint/resume,
and a fresh-context evaluator — into Claude Code via lifecycle hooks, and adds
two guardrails for autonomous multi-session runs: a doom-loop detector and a
periodic forced-reflection nudge.

Everything is toggleable from a single config so the evaluation harness can run
a controlled A/B (module ON vs OFF) holding the model, tasks, and agent harness
fixed.
"""

from .config import Config, load_config

__all__ = ["Config", "load_config"]
__version__ = "0.1.0"
