"""Isolated per-trial workspace.

Each trial must start from a clean environment with no leftover files, cached
data or git history from a prior trial — otherwise trials are not independent and
the results are corrupt (Anthropic observed agents gaining unfair advantage by
reading prior-trial git history). This module creates a fresh temp dir per trial,
seeds the task's immutable BRIEF + default-FAIL feature_list, optionally inits a
git repo, and guarantees teardown.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from lhx.state import FeatureList, Feature
from lhx.memory import Memory

from .tasks.schema import Task

# Repo root, used to locate the drop-in module config for install_module=True.
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _install_module_config(root: Path) -> None:
    """Copy the drop-in .claude/ (hooks wiring + conventions + evaluator) into
    the sandbox so a real `claude` run loads the long-horizon module."""
    claude_dir = root / ".claude"
    (claude_dir / "agents").mkdir(parents=True, exist_ok=True)
    shutil.copy2(_REPO_ROOT / "claude_config" / "settings.json", claude_dir / "settings.json")
    shutil.copy2(_REPO_ROOT / "claude_config" / "CLAUDE.md", claude_dir / "CLAUDE.md")
    shutil.copy2(_REPO_ROOT / "agents" / "evaluator.md", claude_dir / "agents" / "evaluator.md")


@contextmanager
def trial_sandbox(
    task: Task, *, init_git: bool = False, keep: bool = False, install_module: bool = False
):
    """Yield a clean Path for one trial; remove it afterwards unless ``keep``.

    ``install_module`` copies the drop-in .claude/ config so a real `claude` run
    (the SDK/CLI backend) loads the module's hooks. The simulated backend doesn't
    need it.
    """
    # Sanitise task.id: it flows into a filesystem path, and the schema does not
    # constrain it, so a user-authored task with '/' or '..' must not escape tmp.
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "-", task.id)[:64]
    root = Path(tempfile.mkdtemp(prefix=f"lhx-{safe_id}-"))
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

        if install_module:
            _install_module_config(root)

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
