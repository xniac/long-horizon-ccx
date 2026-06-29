from lhx.loop_guard import check, tool_signature


def test_signature_stable_and_order_independent():
    a = tool_signature("Bash", {"command": "ls", "cwd": "/tmp"})
    b = tool_signature("Bash", {"cwd": "/tmp", "command": "ls"})
    assert a == b  # arg order doesn't matter
    c = tool_signature("Bash", {"command": "rm"})
    assert a != c


def test_doom_loop_blocks_on_identical_window():
    sig = tool_signature("Read", {"file": "x.py"})
    # window=3 → block when prior two are identical and next is identical
    prior = [sig, sig]
    d = check(prior, sig, window=3, step_budget=1000)
    assert d.block and d.kind == "doom_loop"


def test_doom_loop_allows_when_not_all_identical():
    sig = tool_signature("Read", {"file": "x.py"})
    other = tool_signature("Read", {"file": "y.py"})
    prior = [sig, other]
    d = check(prior, sig, window=3, step_budget=1000)
    assert not d.block


def test_step_budget_circuit_breaker():
    sig = tool_signature("Read", {"file": "x.py"})
    prior = [tool_signature("Read", {"file": str(i)}) for i in range(50)]
    d = check(prior, sig, window=3, step_budget=50)
    assert d.block and d.kind == "step_budget"


def test_window_one_never_loop_blocks():
    sig = tool_signature("Read", {"file": "x.py"})
    d = check([sig, sig, sig], sig, window=1, step_budget=1000)
    assert not d.block
