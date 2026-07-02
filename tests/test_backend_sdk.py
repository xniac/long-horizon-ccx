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
from lhxeval.backends import BackendError, ClaudeAgentSDKBackend, Directives
from lhxeval.tasks.schema import FeatureSpec, RunConfig, Task


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


def test_cli_backend_raises_on_nonzero_exit(monkeypatch):
    """A bad API key (CLI exits non-zero) must abort loudly, NOT silently grade
    the empty workspace as a legitimate 0% pass."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude")

    def fake_run(cmd, input=None, capture_output=False, text=False, timeout=None,
                 cwd=None, env=None, check=False):
        if cmd[0] != "claude":
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return types.SimpleNamespace(
            returncode=1, stdout="", stderr="Invalid API key · Please run /login")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(BackendError, match="Invalid API key"):
        ClaudeAgentSDKBackend().run(_task(), Config(enabled=True), 0, Directives())


def test_cli_backend_raises_on_is_error_envelope(monkeypatch):
    """Even on exit 0, an `is_error` result envelope means the agent didn't run."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude")

    def fake_run(cmd, input=None, capture_output=False, text=False, timeout=None,
                 cwd=None, env=None, check=False):
        if cmd[0] != "claude":
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return types.SimpleNamespace(
            returncode=0,
            stdout='{"type":"result","subtype":"error_during_execution","is_error":true}',
            stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(BackendError, match="is_error"):
        ClaudeAgentSDKBackend().run(_task(), Config(enabled=True), 0, Directives())


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


def _count_claude_calls(monkeypatch):
    """Mock the CLI; return a dict tracking #invocations and the last --max-turns."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/claude")
    calls = {"n": 0, "max_turns": None}

    def fake_run(cmd, input=None, capture_output=False, text=False, timeout=None,
                 cwd=None, env=None, check=False):
        if cmd[0] != "claude":
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        calls["n"] += 1
        calls["max_turns"] = cmd[cmd.index("--max-turns") + 1]
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_task_run_config_drives_turns_and_sessions(monkeypatch):
    """With no env/CLI override, a task's RunConfig sets max_turns & max_sessions —
    so `--task-id v05` reproduces the multi-session win without extra knobs."""
    calls = _count_claude_calls(monkeypatch)
    task = _task()
    task.run = RunConfig(max_turns=7, max_sessions=3)
    ClaudeAgentSDKBackend().run(task, Config(enabled=True), 0, Directives())
    assert calls["n"] == 3           # sessions from task config
    assert calls["max_turns"] == "7"  # turns from task config


def test_explicit_setting_overrides_task_run_config(monkeypatch):
    """An explicit env/CLI value wins over the per-task RunConfig."""
    calls = _count_claude_calls(monkeypatch)
    task = _task()
    task.run = RunConfig(max_turns=7, max_sessions=3)
    ClaudeAgentSDKBackend(max_turns=5, max_sessions=1).run(
        task, Config(enabled=True), 0, Directives())
    assert calls["n"] == 1
    assert calls["max_turns"] == "5"


def test_verified_tasks_carry_reproduction_settings():
    """The documented positive-delta tasks ship their reproduce settings, so the
    result can't silently depend on remembering CLI knobs (DESIGN §7)."""
    from lhxeval.cli import DEFAULT_TASKS
    from lhxeval.tasks.schema import load_suite

    by_id = {t.id: t for t in load_suite(DEFAULT_TASKS)}
    assert (by_id["v05-incremental-app"].run.max_turns,
            by_id["v05-incremental-app"].run.max_sessions) == (80, 3)
    assert by_id["v06-debug-session-scoped"].run.max_sessions == 4
    assert by_id["v07-debug-amnesiac-pytest"].run.max_sessions == 12
