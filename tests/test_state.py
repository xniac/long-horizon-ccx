import json

from lhx.state import Feature, FeatureList, ProgressLedger, atomic_write


def test_feature_list_default_fail(tmp_path):
    fl = FeatureList(
        goal="g",
        features=[Feature(id="a", description="x"), Feature(id="b", description="y")],
    )
    assert not fl.all_pass
    assert fl.passing == 0
    assert fl.fraction_passing() == 0.0


def test_mark_pass_requires_evidence_and_persists(tmp_path):
    path = tmp_path / "feature_list.json"
    fl = FeatureList(goal="g", features=[Feature(id="a", description="x")])
    assert fl.mark_pass("a", evidence="tests/out.txt")
    assert not fl.mark_pass("missing", evidence="e")
    f = fl.features[0]
    assert f.passes and f.evidence == "tests/out.txt" and f.verified_at
    fl.save(path)
    reloaded = FeatureList.load(path)
    assert reloaded.all_pass
    assert reloaded.features[0].evidence == "tests/out.txt"


def test_atomic_write_replaces_cleanly(tmp_path):
    p = tmp_path / "x.txt"
    atomic_write(p, "one")
    atomic_write(p, "two")
    assert p.read_text() == "two"
    # no leftover temp files
    assert list(tmp_path.glob(".tmp-*")) == []


def test_progress_ledger_events_and_tail(tmp_path):
    ledger = ProgressLedger(tmp_path / "PROGRESS.md", tmp_path / "events.jsonl")
    ledger.append("did step one")  # append-only; self-creates the header
    ledger.record_event({"type": "tool_use", "tool": "Read", "sig": "abc"})
    ledger.record_event({"type": "compaction"})
    progress = (tmp_path / "PROGRESS.md").read_text()
    assert "did step one" in progress and "Session log" in progress
    assert len(ledger.read_events()) == 2
    assert len(ledger.tool_events()) == 1
