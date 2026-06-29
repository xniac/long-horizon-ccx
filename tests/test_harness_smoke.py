from pathlib import Path

from lhxeval.backends import SimulatedBackend
from lhxeval.runner import run_ab, run_trial
from lhxeval.sandbox import trial_sandbox
from lhxeval.tasks.schema import Task, load_suite

SUITE = Path(__file__).resolve().parents[1] / "lhxeval" / "tasks" / "synthetic"


def _suite():
    return load_suite(SUITE)


def test_suite_loads():
    tasks = _suite()
    assert len(tasks) >= 6
    assert any(t.compaction_boundaries > 0 for t in tasks)
    assert any(t.interruption for t in tasks)
    assert any(t.difficulty == "regression" for t in tasks)


def test_ab_runs_and_module_helps_on_long_horizon_metrics():
    tasks = _suite()
    res = run_ab(tasks, backend=SimulatedBackend(), k=8, bootstrap_iters=1000)
    # The whole point: ON should beat OFF on the long-horizon-specific metrics.
    assert res.on.compaction_survival > res.off.compaction_survival
    assert res.on.resume_success > res.off.resume_success
    assert res.on.drift_rate <= res.off.drift_rate
    assert res.on.doom_loop_rate <= res.off.doom_loop_rate
    # paired success effect is positive and significant in this simulation
    assert res.success_ci.point > 0
    assert res.mcnemar.b > res.mcnemar.c


def test_simulation_is_deterministic():
    tasks = _suite()
    a = run_ab(tasks, k=5, bootstrap_iters=500)
    b = run_ab(tasks, k=5, bootstrap_iters=500)
    assert a.on.n_success == b.on.n_success
    assert a.off.n_success == b.off.n_success


def test_simulation_deterministic_across_processes():
    """Catches per-process hash-salt leaking into the RNG seed (PYTHONHASHSEED)."""
    import subprocess
    import sys

    code = (
        "from lhxeval.runner import run_ab; from lhxeval.tasks.schema import load_suite;"
        f"r=run_ab(load_suite(r'{SUITE}'), k=5, bootstrap_iters=10);"
        "print(r.on.n_success, r.off.n_success)"
    )
    outs = set()
    for salt in ("0", "1", "random"):
        env = {"PYTHONHASHSEED": salt}
        import os
        env = {**os.environ, "PYTHONHASHSEED": salt}
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
        outs.add(r.stdout.strip())
    assert len(outs) == 1, f"non-deterministic across hash seeds: {outs}"


def test_regression_tasks_near_100_for_both_arms():
    tasks = [t for t in _suite() if t.difficulty == "regression"]
    res = run_ab(tasks, k=10, bootstrap_iters=200)
    # regression suite should not differ much between arms (catches backsliding)
    assert res.off.pass_at_1 > 0.9
    assert res.on.pass_at_1 > 0.9


def test_sandbox_is_isolated_and_seeded(tmp_path):
    task = _suite()[0]
    seen = []
    with trial_sandbox(task) as root:
        assert (root / "BRIEF.md").exists()
        assert (root / "feature_list.json").exists()
        seen.append(root)
        # feature_list starts default-FAIL
        import json
        fl = json.loads((root / "feature_list.json").read_text())
        assert all(f["passes"] is False for f in fl["features"])
    # cleaned up after the context exits
    assert not seen[0].exists()
