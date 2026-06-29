from lhx.memory import Memory
from lhx.reflection import should_reflect, reflection_text


def test_brief_is_immutable(tmp_path):
    m = Memory(tmp_path / "BRIEF.md", tmp_path / "MEMORY.md")
    m.init_brief("the original goal")
    m.init_brief("a different goal")  # must not overwrite
    assert "original goal" in m.read_brief()
    assert "different goal" not in m.read_brief()


def test_memory_is_capped_and_keeps_recent(tmp_path):
    m = Memory(tmp_path / "BRIEF.md", tmp_path / "MEMORY.md", char_cap=120)
    for i in range(50):
        m.note(f"note number {i} with some padding text")
    mem = m.read_memory()
    assert len(mem) <= 160  # cap + truncation marker headroom
    assert "note number 49" in mem        # most recent kept
    assert "note number 0 " not in mem    # oldest dropped


def test_should_reflect_boundaries():
    assert should_reflect(8, 8)
    assert should_reflect(16, 8)
    assert not should_reflect(7, 8)
    assert not should_reflect(0, 8)
    assert not should_reflect(8, 0)  # disabled
    assert "REFLECTION CHECKPOINT" in reflection_text(8)
