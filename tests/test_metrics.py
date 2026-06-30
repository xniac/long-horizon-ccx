from math import comb

from lhxeval.backends import RunOutcome
from lhxeval.graders import grade
from lhxeval.metrics import pass_at_k, pass_caret_k
from lhxeval.tasks.schema import Task, FeatureSpec, VerifyCheck


def _verify_task():
    return Task(
        id="v", title="v", goal="g", prompt="p",
        features=[FeatureSpec(id="f1", description="d", requires=["x"])],
        verify=[VerifyCheck(id="c1", cmd="true"), VerifyCheck(id="c2", cmd="false")],
    )


def test_grade_uses_executable_checks_when_present():
    t = _verify_task()
    # both checks pass → success; outcome.artifact irrelevant (we grade the build)
    g = grade(t, RunOutcome(checks={"c1": True, "c2": True}))
    assert g.success and g.partial_credit == 1.0 and g.detail["mode"] == "executable-checks"
    # one check fails → not success, half credit
    g2 = grade(t, RunOutcome(checks={"c1": True, "c2": False}))
    assert not g2.success and abs(g2.partial_credit - 0.5) < 1e-9


def test_grade_falls_back_to_token_grader_without_checks():
    # No checks run (e.g. simulated backend) → token grader on the artifact.
    t = _verify_task()
    g = grade(t, RunOutcome(artifact={"f1": "x"}, features_completed=["f1"]))
    assert g.success and g.detail.get("mode") != "executable-checks"


def test_pass_at_k_edges():
    assert pass_at_k(10, 0, 5) == 0.0          # never succeeds
    assert pass_at_k(10, 10, 5) == 1.0         # always succeeds
    assert pass_at_k(10, 5, 1) == 0.5          # pass@1 == empirical rate
    # k > n clamps to n
    assert pass_at_k(3, 1, 99) == pass_at_k(3, 1, 3)


def test_pass_at_k_matches_chen_formula():
    n, c, k = 10, 3, 4
    expected = 1 - comb(n - c, k) / comb(n, k)
    assert abs(pass_at_k(n, c, k) - expected) < 1e-12


def test_pass_caret_k():
    # P(all k of k succeed) with c == n is 1; with c < k is 0.
    assert pass_caret_k(5, 5, 5) == 1.0
    assert pass_caret_k(5, 2, 3) == 0.0
    # combinatorial value
    assert abs(pass_caret_k(10, 6, 3) - comb(6, 3) / comb(10, 3)) < 1e-12
    # pass^1 == empirical rate
    assert pass_caret_k(8, 5, 1) == 5 / 8


def test_pass_at_k_geq_pass_caret_k():
    # For any n,c,k: pass@k >= pass^k (at-least-one dominates all).
    for n in range(2, 8):
        for c in range(0, n + 1):
            for k in range(1, n + 1):
                assert pass_at_k(n, c, k) + 1e-9 >= pass_caret_k(n, c, k)
