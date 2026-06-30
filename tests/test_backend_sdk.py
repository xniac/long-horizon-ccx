"""Smoke test for the headless `claude -p` backend, with subprocess mocked.

We never call a real model: we stub `shutil.which` and `subprocess.run` so the
test verifies the *seam* — sandbox prep, env wiring, and reconstruction of a
RunOutcome from the on-disk artifacts the module writes."""

import shutil
import subprocess
import types
from pathlib import Path

import pytest

from lhx.config import Config
from lhx.state import FeatureList
from lhxeval.backends import ClaudeAgentSDKBackend, Directives
from lhxeval.tasks.schema import FeatureSpec, Task


def _task():
    return Task(
        id="sdk/smoke..test",  # deliberately unsafe id → exercises path sanitisation
        title="smoke",
        goal="do the thing",
        prompt="do it",
        features=[
            FeatureSpec(id="f0", description="a", requires=["x"]),
            FeatureSpec(id="f1", description="b", requires=["y"]),
        ],
    )


def test_sdk_backend_requires_cli(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="claude"):
        ClaudeAgentSDKBackend().run(_task(), Config(enabled=True), 0, Directives())


def test_sdk_backend_reconstructs_outcome_from_disk(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude")

    seen = {}

    def fake_run(cmd, input=None, capture_output=False, text=False, timeout=None,
                 cwd=None, env=None, check=False):
        # The sandbox shells out to git (env=None); only intercept the claude call.
        if cmd[0] != "claude":
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        ws = Path(cwd)
        seen["cwd"] = ws
        seen["enabled"] = env.get("LHX_ENABLED")
        seen["module_installed"] = (ws / ".claude" / "settings.json").exists()
        # Simulate the module recording events + verifying one feature with evidence.
        (ws / ".lh").mkdir(parents=True, exist_ok=True)
        (ws / ".lh" / "events.jsonl").write_text(
            '{"type":"tool_use","tool":"Write","sig":"a"}\n'
            '{"type":"compaction"}\n'
            '{"type":"guard_block","kind":"doom_loop"}\n',
            encoding="utf-8",
        )
        fl = FeatureList.load(ws / "feature_list.json")
        fl.mark_pass("f0", evidence="proof.txt")
        fl.save(ws / "feature_list.json")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    outcome = ClaudeAgentSDKBackend().run(_task(), Config(enabled=True), 0, Directives())

    assert seen["enabled"] == "true"            # arm wired via env
    assert seen["module_installed"] is True     # hooks installed into sandbox
    assert "lhx-sdk-smoke--test-" in seen["cwd"].name  # id sanitised, no '/' or '..'
    assert outcome.features_completed == ["f0"]
    assert outcome.steps == 1                   # one tool_use event
    assert outcome.forced_compaction is True
    assert outcome.doom_loops == 1


def test_sdk_backend_passes_tool_restriction_flags(monkeypatch):
    """`tools`/`disallowed_tools` must reach the `claude` CLI cmdline — without
    them the long-horizon experiment can't force per-file Read+Edit."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude")

    captured: dict = {}

    def fake_run(cmd, input=None, capture_output=False, text=False, timeout=None,
                 cwd=None, env=None, check=False):
        if cmd[0] == "claude":
            captured["cmd"] = list(cmd)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    backend = ClaudeAgentSDKBackend(
        tools=["Read", "Edit", "Write", "Glob"],
        disallowed_tools=["Bash(sed *)"],
    )
    backend.run(_task(), Config(enabled=True), 0, Directives())

    cmd = captured["cmd"]
    assert "--tools" in cmd and {"Read", "Edit", "Write", "Glob"}.issubset(set(cmd))
    assert "--disallowedTools" in cmd and "Bash(sed *)" in cmd
