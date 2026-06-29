"""Aggregate an ABResult into results.json + a static HTML dashboard.

No templating dependency — the dashboard is a single self-contained HTML string
so it can be opened directly from disk. It shows the per-arm metric table, the
paired deltas with confidence intervals, the McNemar verdict, and the
long-horizon-specific metrics that are the point of the module.
"""

from __future__ import annotations

import json
from pathlib import Path

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


def render_html(result: ABResult) -> str:
    on, off = result.on, result.off
    ps = result.to_dict()["paired_stats"]
    stat_rows = "\n".join(
        f"<tr><td>{k.replace('_', ' ')}</td><td>{v or '—'}</td></tr>"
        for k, v in ps.items()
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Long-horizon module — A/B eval</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:880px;margin:2rem auto;padding:0 1rem;color:#1a1a1a}}
 h1{{font-size:1.5rem}} h2{{font-size:1.1rem;margin-top:2rem;border-bottom:1px solid #eee;padding-bottom:.3rem}}
 table{{border-collapse:collapse;width:100%;margin:.5rem 0;font-size:.92rem}}
 th,td{{border:1px solid #e3e3e3;padding:.45rem .6rem;text-align:left}}
 th{{background:#fafafa}}
 .good{{color:#0a7d2c;font-weight:600}} .bad{{color:#c0392b;font-weight:600}}
 .meta{{color:#666;font-size:.85rem}}
 code{{background:#f4f4f4;padding:.1rem .3rem;border-radius:3px}}
</style></head><body>
<h1>Long-horizon module — controlled A/B</h1>
<p class="meta">{len(result.tasks)} tasks &times; k={result.k} seeds &times; 2 arms
 = {len(result.trials)} trials. Backend: simulated (deterministic) unless noted.
 Only the long-horizon module is toggled; model, tasks, harness and seeds are fixed.</p>

<h2>Per-arm metrics</h2>
<table>
 <tr><th>metric</th><th>module ON</th><th>module OFF</th><th>&Delta; (ON&minus;OFF)</th></tr>
 {_metric_rows(on, off)}
</table>
<p class="meta">Green = ON is better; red = ON is worse. pp = percentage points.</p>

<h2>Paired statistics</h2>
<table>
 <tr><th>statistic</th><th>value</th></tr>
 {stat_rows}
</table>
<p class="meta">CIs are paired bootstrap (resampling (task,seed) pairs). McNemar
 is the exact paired test on per-trial pass/fail; "helped" = ON passed where OFF
 failed, "hurt" = the reverse.</p>

<h2>pass@k vs pass^k curves (macro-averaged over tasks)</h2>
<table>
 <tr><th>k</th><th>pass@k ON</th><th>pass@k OFF</th><th>pass^k ON</th><th>pass^k OFF</th></tr>
 {_curve_rows(on, off)}
</table>
<p class="meta">pass@k (at least one of k succeeds) rises with k and is
 "exponentially forgiving"; pass^k (all k succeed) falls with k and is the honest
 reliability number. The ON/OFF gap on pass^k is the long-horizon payoff.</p>

<h2>How to read this</h2>
<p>The module is designed to win on the <b>long-horizon-specific</b> metrics —
 compaction-survival, resume-after-interruption, goal-drift and doom-loops — and
 those gains should show up as higher reliability (pass^k) more than raw pass@1.
 A near-zero delta on pass@1 with large deltas on the long-horizon metrics is the
 expected signature, not a null result.</p>
</body></html>
"""


def write_dashboard(result: ABResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(result), encoding="utf-8")
