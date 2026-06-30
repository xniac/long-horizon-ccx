"""Uncertainty + significance — pure Python (no scipy); small and exact, since
eval suites are small (n=20–50, k=3–5) and normal approximations mislead. DESIGN §8.5.

* **Paired bootstrap CI** — resamples (task,seed) pairs to respect the pairing.
* **McNemar exact** — two-sided binomial on the discordant pairs (paired binary test).
* **Beta posterior** (Jeffreys) — honest credible interval for a single rate at small n.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from math import comb, lgamma, exp
from typing import Sequence


# --------------------------------------------------------------------------- #
# Paired bootstrap
# --------------------------------------------------------------------------- #
@dataclass
class CI:
    point: float
    lo: float
    hi: float
    level: float = 0.95

    def __str__(self) -> str:
        return f"{self.point:+.3f} [{self.lo:+.3f}, {self.hi:+.3f}] ({self.level:.0%} CI)"


def paired_bootstrap_diff(
    on: Sequence[float],
    off: Sequence[float],
    *,
    iters: int = 10000,
    level: float = 0.95,
    seed: int = 0,
) -> CI:
    """Bootstrap CI for mean(on - off) over paired observations.

    ``on[i]`` and ``off[i]`` are the metric for the same (task, seed) pair under
    each arm.
    """
    if len(on) != len(off):
        raise ValueError("on/off must be paired (equal length)")
    diffs = [a - b for a, b in zip(on, off)]
    n = len(diffs)
    if n == 0:
        return CI(0.0, 0.0, 0.0, level)
    point = sum(diffs) / n
    rng = random.Random(seed)
    means = []
    for _ in range(iters):
        s = sum(diffs[rng.randrange(n)] for _ in range(n))
        means.append(s / n)
    means.sort()
    lo_idx = int((1 - level) / 2 * iters)
    hi_idx = int((1 + level) / 2 * iters) - 1
    hi_idx = min(max(hi_idx, 0), iters - 1)
    return CI(point, means[lo_idx], means[hi_idx], level)


# --------------------------------------------------------------------------- #
# McNemar exact (paired binary)
# --------------------------------------------------------------------------- #
@dataclass
class McNemarResult:
    b: int  # on-success, off-failure  (module helped)
    c: int  # on-failure, off-success  (module hurt)
    p_value: float
    n_discordant: int

    def __str__(self) -> str:
        # Use scientific notation for tiny p so it doesn't round to a misleading
        # "0.0000" — a 46-0 split gives p ~ 2.8e-14, not zero.
        p = f"{self.p_value:.2e}" if self.p_value < 1e-4 else f"{self.p_value:.4f}"
        return (
            f"McNemar exact: helped={self.b}, hurt={self.c}, "
            f"discordant={self.n_discordant}, p={p}"
        )


def mcnemar_exact(on_pass: Sequence[bool], off_pass: Sequence[bool]) -> McNemarResult:
    """Two-sided exact McNemar on paired pass/fail vectors."""
    if len(on_pass) != len(off_pass):
        raise ValueError("vectors must be paired (equal length)")
    b = sum(1 for o, f in zip(on_pass, off_pass) if o and not f)
    c = sum(1 for o, f in zip(on_pass, off_pass) if (not o) and f)
    n = b + c
    if n == 0:
        return McNemarResult(b, c, 1.0, 0)
    # Exact two-sided binomial test, p=0.5, statistic = min(b, c).
    x = min(b, c)
    tail = sum(comb(n, i) for i in range(0, x + 1)) / (2 ** n)
    p = min(1.0, 2 * tail)
    return McNemarResult(b, c, p, n)


# --------------------------------------------------------------------------- #
# Beta posterior for a single rate
# --------------------------------------------------------------------------- #
def _beta_ppf(p: float, a: float, b: float, *, steps: int = 4000) -> float:
    """Inverse Beta CDF (quantile) for credible intervals.

    Numeric CDF inversion via the trapezoidal rule over [0,1] with linear
    interpolation in the crossing bin; accurate to ~1e-3 for typical a,b < 50,
    which is the regime of small eval suites. Avoids a scipy dependency. See the
    regularised incomplete Beta function I_x(a,b) for the closed form.
    """
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    log_norm = lgamma(a + b) - lgamma(a) - lgamma(b)
    # cumulative trapezoid over [0,1]
    dx = 1.0 / steps
    cum = 0.0
    prev = 0.0  # pdf at x=0 is 0 for a>1; for a<1 it diverges — clamp via tiny x
    target = p
    x_prev = 0.0
    for i in range(1, steps + 1):
        x = i * dx
        # pdf(x)
        if 0 < x < 1:
            logpdf = log_norm + (a - 1) * _safe_log(x) + (b - 1) * _safe_log(1 - x)
            pdf = exp(logpdf)
        else:
            pdf = 0.0
        cum += (prev + pdf) / 2 * dx
        if cum >= target:
            return x_prev + dx * (target - (cum - (prev + pdf) / 2 * dx)) / (
                (prev + pdf) / 2 * dx + 1e-12
            )
        prev = pdf
        x_prev = x
    return 1.0


def _safe_log(x: float) -> float:
    from math import log

    return log(max(x, 1e-12))


@dataclass
class RateEstimate:
    rate: float
    lo: float
    hi: float
    n: int
    successes: int
    level: float = 0.95

    def __str__(self) -> str:
        return f"{self.rate:.3f} [{self.lo:.3f}, {self.hi:.3f}] (n={self.n})"


def beta_rate(successes: int, n: int, *, level: float = 0.95) -> RateEstimate:
    """Posterior mean + equal-tailed credible interval with Jeffreys prior."""
    a = successes + 0.5
    b = (n - successes) + 0.5
    mean = a / (a + b)
    lo = _beta_ppf((1 - level) / 2, a, b)
    hi = _beta_ppf((1 + level) / 2, a, b)
    return RateEstimate(mean, lo, hi, n, successes, level)
