"""Regression tests for Config.from_env — a crash here silently disables the
whole module in live runs (see the LHX_CONFIG 'File name too long' bug)."""

from lhx.config import Config


def _clear_lhx(monkeypatch):
    import os
    for k in list(os.environ):
        if k.startswith("LHX_"):
            monkeypatch.delenv(k, raising=False)


def test_from_env_handles_inline_json_blob(monkeypatch):
    _clear_lhx(monkeypatch)
    # The full serialized config is >255 bytes — Path(...).is_file() used to raise
    # OSError: File name too long. from_env must treat it as inline JSON instead.
    monkeypatch.setenv("LHX_CONFIG", Config(enabled=True, step_budget=123).model_dump_json())
    cfg = Config.from_env()
    assert cfg.enabled is True
    assert cfg.step_budget == 123


def test_from_env_reads_a_real_file(tmp_path, monkeypatch):
    _clear_lhx(monkeypatch)
    p = tmp_path / "cfg.json"
    p.write_text(Config(reflection_interval=5).model_dump_json())
    monkeypatch.setenv("LHX_CONFIG", str(p))
    assert Config.from_env().reflection_interval == 5


def test_from_env_never_raises_on_garbage(monkeypatch):
    _clear_lhx(monkeypatch)
    for bad in ["not json", "{broken", "/no/such/path.json", "x" * 5000]:
        monkeypatch.setenv("LHX_CONFIG", bad)
        Config.from_env()  # must not raise


def test_from_env_never_raises_on_wrong_typed_json(monkeypatch):
    _clear_lhx(monkeypatch)
    # Well-formed JSON that pydantic rejects (wrong type / not an object) must
    # fall back to defaults, not crash the hook.
    for bad in ['{"step_budget": "lots"}', '{"enabled": {"nested": 1}}', '[1, 2]', '"str"']:
        monkeypatch.setenv("LHX_CONFIG", bad)
        cfg = Config.from_env()
        assert cfg.step_budget == Config().step_budget


def test_env_vars_override_base_config(monkeypatch):
    _clear_lhx(monkeypatch)
    monkeypatch.setenv("LHX_CONFIG", Config(enabled=True).model_dump_json())
    monkeypatch.setenv("LHX_ENABLED", "false")  # env wins over the base blob
    assert Config.from_env().enabled is False
