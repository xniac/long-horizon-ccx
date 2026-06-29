from lhxeval.stats import beta_rate, mcnemar_exact, paired_bootstrap_diff


def test_paired_bootstrap_positive_effect():
    on = [1.0] * 20
    off = [0.0] * 20
    ci = paired_bootstrap_diff(on, off, iters=2000, seed=1)
    assert ci.point == 1.0
    assert ci.lo > 0.9  # clearly positive, CI excludes 0


def test_paired_bootstrap_no_effect_contains_zero():
    on = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0]
    off = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0]
    ci = paired_bootstrap_diff(on, off, iters=2000, seed=1)
    assert ci.point == 0.0
    assert ci.lo <= 0.0 <= ci.hi


def test_mcnemar_counts_and_significance():
    on = [True] * 10 + [False] * 2
    off = [False] * 10 + [True] * 2
    r = mcnemar_exact(on, off)
    assert r.b == 10 and r.c == 2
    assert r.n_discordant == 12
    assert 0.0 <= r.p_value <= 1.0


def test_mcnemar_no_discordant_is_p1():
    on = [True, False, True]
    off = [True, False, True]
    r = mcnemar_exact(on, off)
    assert r.p_value == 1.0


def test_beta_rate_interval_brackets_point():
    est = beta_rate(7, 10)
    assert est.lo <= est.rate <= est.hi
    assert 0.0 <= est.lo < est.hi <= 1.0
    # more data → tighter interval
    wide = beta_rate(7, 10)
    tight = beta_rate(70, 100)
    assert (tight.hi - tight.lo) < (wide.hi - wide.lo)
