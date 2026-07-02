"""Aggregate an ABResult into results.json + a self-contained static HTML
dashboard (per-arm table, paired deltas + CIs, McNemar, pass@k/pass^k curves).
HTML lives in a sibling ``dashboard.html`` ``string.Template``.
"""

from __future__ import annotations

import json
from pathlib import Path
from string import Template

from .metrics import ArmMetrics
from .runner import ABResult


def write_results_json(result: ABResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")


def _fmt(x, pct=False):
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x*100:.1f}%" if pct else f"{x:,.2f}"
    return str(x)


def _metric_rows(on: ArmMetrics, off: ArmMetrics) -> str:
    def row(label, on_v, off_v, pct=False, lower_better=False):
        delta = ""
        if isinstance(on_v, (int, float)) and isinstance(off_v, (int, float)):
            d = on_v - off_v
            good = (d < 0) if lower_better else (d > 0)
            cls = "good" if good and abs(d) > 1e-9 else ("bad" if abs(d) > 1e-9 else "")
            dv = f"{d*100:+.1f}pp" if pct else f"{d:+,.2f}"
            delta = f'<span class="{cls}">{dv}</span>'
        return (
            f"<tr><td>{label}</td><td>{_fmt(on_v, pct)}</td>"
            f"<td>{_fmt(off_v, pct)}</td><td>{delta}</td></tr>"
        )

    return "\n".join([
        row("pass@1 (macro)", on.pass_at_1, off.pass_at_1, pct=True),
        row(f"pass^{on.reliability_k} (reliability)", on.pass_caret_k, off.pass_caret_k, pct=True),
        row("compaction-survival", on.compaction_survival, off.compaction_survival, pct=True),
        row("resume-after-interruption", on.resume_success, off.resume_success, pct=True),
        row("goal-drift rate", on.drift_rate, off.drift_rate, pct=True, lower_better=True),
        row("doom-loops / trial", on.doom_loop_rate, off.doom_loop_rate, lower_better=True),
        row("mean steps", on.mean_steps, off.mean_steps, lower_better=True),
        row("mean tokens", on.mean_tokens, off.mean_tokens, lower_better=True),
        row("mean cost (USD)", on.mean_cost_usd, off.mean_cost_usd, lower_better=True),
    ])


def _curve_rows(on: ArmMetrics, off: ArmMetrics) -> str:
    ks = sorted(on.pass_caret_curve)
    rows = []
    for kk in ks:
        rows.append(
            f"<tr><td>{kk}</td>"
            f"<td>{on.pass_at_curve.get(kk, 0)*100:.0f}%</td>"
            f"<td>{off.pass_at_curve.get(kk, 0)*100:.0f}%</td>"
            f"<td>{on.pass_caret_curve.get(kk, 0)*100:.0f}%</td>"
            f"<td>{off.pass_caret_curve.get(kk, 0)*100:.0f}%</td></tr>"
        )
    return "\n".join(rows)


# HTML/CSS lives in a sibling template (string.Template, $-placeholders — so CSS
# braces need no escaping and styling is editable without touching Python).
_TEMPLATE_PATH = Path(__file__).parent / "dashboard.html"


def _backend_note(backend: str) -> str:
    """Honest one-line backend label for the dashboard meta line."""
    b = (backend or "simulated").lower()
    if "sim" in b:
        return "simulated (deterministic)"
    return f"real Claude ({backend})"


def _arm_from_row(row: dict) -> ArmMetrics:
    """Rebuild ArmMetrics from a results.json arm row (JSON stringifies the curve
    keys, so restore them to int)."""
    r = dict(row)
    for key in ("pass_at_curve", "pass_caret_curve"):
        r[key] = {int(kk): vv for kk, vv in r[key].items()}
    return ArmMetrics(**r)


def _render(on: ArmMetrics, off: ArmMetrics, paired_stats: dict,
            n_tasks: int, k: int, n_trials: int, backend_note: str) -> str:
    stat_rows = "\n".join(
        f"<tr><td>{sk.replace('_', ' ')}</td><td>{sv or '—'}</td></tr>"
        for sk, sv in paired_stats.items()
    )
    template = Template(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.substitute(
        n_tasks=n_tasks,
        k=k,
        n_trials=n_trials,
        backend_note=backend_note,
        metric_rows=_metric_rows(on, off),
        stat_rows=stat_rows,
        curve_rows=_curve_rows(on, off),
    )


def render_html(result: ABResult) -> str:
    return _render(
        result.on, result.off, result.to_dict()["paired_stats"],
        len(result.tasks), result.k, len(result.trials),
        _backend_note(getattr(result, "backend", "simulated")),
    )


def render_from_results_json(path: Path, backend_note: str | None = None) -> str:
    """Re-render a dashboard from a saved results.json — no re-run needed.

    ``backend_note`` overrides the meta label (used for runs whose json predates
    the ``backend`` field, e.g. an old real-Claude run mislabeled as simulated).
    """
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    on, off = _arm_from_row(d["arms"]["on"]), _arm_from_row(d["arms"]["off"])
    note = backend_note or _backend_note(d.get("backend", "simulated"))
    return _render(on, off, d["paired_stats"],
                   len(d["tasks"]), d["k"], len(d["trials"]), note)


def write_dashboard(result: ABResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(result), encoding="utf-8")
