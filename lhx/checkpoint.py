"""Checkpoint / resume (M7).

Two layers of durability: a git commit of *tracked* changes at session end
(deliberately not ``git add -A``, to keep ephemeral artifacts out of history) + a
typed ``.lh/checkpoint.json``. ``resume_context()`` builds the SessionStart
injection that re-orients a fresh session.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .state import FeatureList, atomic_write


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def is_git_repo(cwd: Path) -> bool:
    r = _git(["rev-parse", "--is-inside-work-tree"], cwd)
    return r.returncode == 0 and r.stdout.strip() == "true"


def git_checkpoint(cwd: Path, message: str) -> str | None:
    """Commit tracked changes only. Returns the commit hash, or None if nothing
    to commit / not a repo."""
    if not is_git_repo(cwd):
        return None
    # -a stages tracked modifications/deletions but not new untracked files.
    r = _git(["commit", "-a", "-m", message], cwd)
    if r.returncode != 0:
        return None
    rev = _git(["rev-parse", "HEAD"], cwd)
    return rev.stdout.strip() or None


def git_log_oneline(cwd: Path, n: int = 10) -> str:
    if not is_git_repo(cwd):
        return ""
    r = _git(["log", "--oneline", f"-{n}"], cwd)
    return r.stdout.strip()


def save_checkpoint(path: Path, data: dict) -> None:
    atomic_write(path, json.dumps(data, indent=2))


def load_checkpoint(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def resume_context(
    *,
    progress_path: Path,
    feature_path: Path,
    checkpoint_path: Path,
    cwd: Path,
) -> str:
    """Build the SessionStart context block that re-orients a fresh session.

    Designed to be re-run on resume (Claude Code re-runs SessionStart with
    ``source="resume"``), so it always reflects the latest on-disk truth.
    """
    parts: list[str] = ["=== LONG-HORIZON RESUME CONTEXT ==="]

    fl = FeatureList.load(feature_path)
    if fl.total:
        parts.append(
            f"Feature contract: {fl.passing}/{fl.total} verified passing. "
            f"Goal: {fl.goal}"
        )
        remaining = [f.id for f in fl.features if not f.passes]
        if remaining:
            parts.append("Remaining (work ONE at a time): " + ", ".join(remaining[:20]))
        else:
            parts.append(
                "All features report passing — VERIFY independently before "
                "declaring victory; do not take the contract's word for it."
            )

    ckpt = load_checkpoint(checkpoint_path)
    if ckpt:
        parts.append(
            f"Last checkpoint: session={ckpt.get('session_id', '?')} "
            f"tool_calls={ckpt.get('tool_calls', '?')}"
        )

    log = git_log_oneline(cwd, 5)
    if log:
        parts.append("Recent commits:\n" + log)

    if progress_path.exists():
        tail = progress_path.read_text(encoding="utf-8").strip().splitlines()[-15:]
        parts.append("PROGRESS.md (tail):\n" + "\n".join(tail))

    parts.append(
        "FIRST ACTIONS: read PROGRESS.md and BRIEF.md fully, run init.sh / "
        "smoke test to confirm current state, THEN continue the next unfinished "
        "feature. Do not assume prior progress is correct — verify it."
    )
    return "\n".join(parts)
