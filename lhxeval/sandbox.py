"""Isolated per-trial workspace.

Each trial must start from a clean environment with no leftover files, cached
data or git history from a prior trial — otherwise trials are not independent and
the results are corrupt (Anthropic observed agents gaining unfair advantage by
reading prior-trial git history). This module creates a fresh temp dir per trial,
seeds the task's immutable BRIEF + default-FAIL feature_list, optionally inits a
git repo, and guarantees teardown.
"""

from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from lhx.state import FeatureList, Feature
from lhx.memory import Memory

from .tasks.schema import Task


@contextmanager
def trial_sandbox(task: Task, *, init_git: bool = False, keep: bool = False):
    """Yield a clean Path for one trial; remove it afterwards unless ``keep``."""
    root = Path(tempfile.mkdtemp(prefix=f"lhx-{task.id}-"))
    try:
        # Seed the immutable brief.
        mem = Memory(root / "BRIEF.md", root / "MEMORY.md")
        mem.init_brief(task.goal)

        # Seed the default-FAIL feature contract.
        fl = FeatureList(
            goal=task.goal,
            features=[Feature(id=f.id, description=f.description) for f in task.features],
        )
        fl.save(root / "feature_list.json")

        if init_git:
            import subprocess

            subprocess.run(["git", "init", "-q"], cwd=root, check=False)
            subprocess.run(["git", "add", "-A"], cwd=root, check=False)
            subprocess.run(
                ["git", "-c", "user.email=eval@lhx", "-c", "user.name=lhx-eval",
                 "commit", "-q", "-m", "seed"],
                cwd=root, check=False,
            )

        yield root
    finally:
        if not keep:
            shutil.rmtree(root, ignore_errors=True)
