"""Dashboard rendering: honest backend label + re-render from saved json."""

from __future__ import annotations

import json
from pathlib import Path

from lhxeval.cli import DEFAULT_TASKS
from lhxeval.report import (
    _backend_note,
    render_from_results_json,
    render_html,
    write_results_json,
)
from lhxeval.runner import run_ab
from lhxeval.tasks.schema import load_suite


def _tiny_result():
    tasks = [t for t in load_suite(DEFAULT_TASKS) if t.id == "r02-add-function"]
    assert tasks, "expected seed task r02-add-function"
    return run_ab(tasks, k=1)  # simulated backend, 2 trials


def test_backend_note_distinguishes_sim_from_real():
    assert _backend_note("simulated") == "simulated (deterministic)"
    assert _backend_note("claude-sdk").startswith("real Claude")
    assert "claude-sdk" in _backend_note("claude-sdk")
    assert _backend_note(None) == "simulated (deterministic)"  # safe default


def test_result_records_backend_and_dashboard_labels_it():
    result = _tiny_result()
    assert result.backend == "simulated"
    assert result.to_dict()["backend"] == "simulated"
    html = render_html(result)
    # the meta line must not mislabel a sim run, and must not hardcode anything
    assert "simulated (deterministic)" in html


def test_regen_from_json_roundtrips_and_override(tmp_path: Path):
    result = _tiny_result()
    jpath = tmp_path / "results.json"
    write_results_json(result, jpath)

    # backend is persisted, so a re-render from json matches the live render
    assert json.loads(jpath.read_text())["backend"] == "simulated"
    assert render_from_results_json(jpath) == render_html(result)

    # explicit override relabels a run whose json predates the backend field
    html = render_from_results_json(jpath, backend_note="real Claude (Haiku 4.5)")
    assert "real Claude (Haiku 4.5)" in html
    assert "simulated (deterministic)" not in html.split("How to read")[0]
