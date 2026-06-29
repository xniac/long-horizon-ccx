"""Exercise the hook entry points end-to-end via their stdin/stdout contract."""

import io
import json

import pytest

from lhx.hooks import (
    post_tool_use,
    pre_tool_use,
    session_start,
    stop,
)
from lhx.state import Feature, FeatureList
from lhx.memory import Memory


def run_hook(hook_main, event, monkeypatch, env=None):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    for k in list(__import__("os").environ):
        if k.startswith("LHX_"):
            monkeypatch.delenv(k, raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    hook_main()
    raw = out.getvalue()
    return json.loads(raw) if raw.strip() else {}


def seed_workspace(tmp_path, n_features=2, passing=0):
    feats = [Feature(id=f"f{i}", description="d", passes=(i < passing)) for i in range(n_features)]
    FeatureList(goal="build the thing", features=feats).save(tmp_path / "feature_list.json")
    Memory(tmp_path / "BRIEF.md", tmp_path / "MEMORY.md").init_brief("build the thing")
    return {"cwd": str(tmp_path)}


def test_session_start_injects_resume_context(tmp_path, monkeypatch):
    event = seed_workspace(tmp_path)
    out = run_hook(session_start.main, event, monkeypatch)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "LONG-HORIZON RESUME CONTEXT" in ctx
    assert "build the thing" in ctx


def test_pre_tool_use_kill_switch_blocks(tmp_path, monkeypatch):
    event = seed_workspace(tmp_path)
    (tmp_path / "AGENT_STOP").write_text("stop")
    event["tool_name"] = "Bash"
    event["tool_input"] = {"command": "ls"}
    out = run_hook(pre_tool_use.main, event, monkeypatch)
    assert out["decision"] == "block"
    assert "KILL-SWITCH" in out["reason"]


def test_pre_tool_use_blocks_doom_loop(tmp_path, monkeypatch):
    event = seed_workspace(tmp_path)
    event["tool_name"] = "Read"
    event["tool_input"] = {"file": "x.py"}
    # Record two identical prior tool calls via PostToolUse, then PreToolUse a 3rd.
    run_hook(post_tool_use.main, dict(event), monkeypatch)
    run_hook(post_tool_use.main, dict(event), monkeypatch)
    out = run_hook(pre_tool_use.main, dict(event), monkeypatch, env={"LHX_DOOM_LOOP_WINDOW": "3"})
    assert out.get("decision") == "block"
    assert "Doom-loop" in out["reason"]


def test_pre_tool_use_disabled_arm_never_blocks(tmp_path, monkeypatch):
    event = seed_workspace(tmp_path)
    (tmp_path / "AGENT_STOP").write_text("stop")  # even with kill switch present
    event["tool_name"] = "Bash"
    event["tool_input"] = {"command": "ls"}
    out = run_hook(pre_tool_use.main, event, monkeypatch, env={"LHX_ENABLED": "false"})
    assert out == {}  # module off → no interference (the A/B OFF arm)


def test_post_tool_use_reflection_nudge(tmp_path, monkeypatch):
    event = seed_workspace(tmp_path)
    event["tool_name"] = "Read"
    out = {}
    for i in range(4):
        event["tool_input"] = {"file": f"{i}.py"}  # distinct → no loop block
        out = run_hook(post_tool_use.main, dict(event), monkeypatch, env={"LHX_REFLECTION_INTERVAL": "4"})
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "REFLECTION CHECKPOINT" in ctx


def test_stop_completion_gate_blocks_until_all_pass(tmp_path, monkeypatch):
    event = seed_workspace(tmp_path, n_features=2, passing=1)
    out = run_hook(stop.main, event, monkeypatch)
    assert out["decision"] == "block"
    assert "Completion gate" in out["reason"]


def test_stop_allows_when_all_features_pass(tmp_path, monkeypatch):
    event = seed_workspace(tmp_path, n_features=2, passing=2)
    out = run_hook(stop.main, event, monkeypatch)
    assert out == {}  # gate satisfied → stop allowed
