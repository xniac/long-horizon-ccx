"""Aggregate an ABResult into results.json + a self-contained static HTML
dashboard (per-arm table, paired deltas + CIs, McNemar, pass@k/pass^k curves).
HTML lives in a sibling ``dashboard.html`` ``string.Template``. See DESIGN §9.
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


def render_html(result: ABResult) -> str:
    on, off = result.on, result.off
    ps = result.to_dict()["paired_stats"]
    stat_rows = "\n".join(
        f"<tr><td>{k.replace('_', ' ')}</td><td>{v or '—'}</td></tr>"
        for k, v in ps.items()
    )
    template = Template(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.substitute(
        n_tasks=len(result.tasks),
        k=result.k,
        n_trials=len(result.trials),
        metric_rows=_metric_rows(on, off),
        stat_rows=stat_rows,
        curve_rows=_curve_rows(on, off),
    )


def write_dashboard(result: ABResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(result), encoding="utf-8")
