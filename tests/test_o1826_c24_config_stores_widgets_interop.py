"""O1826 BRIEF-24: config stores -- widgets_config + interop_registry.

M185  widgets_config._sanitize_list sliced the raw list to 24 BEFORE validating,
      and 24 < len(VALID_WIDGET_IDS) == 36, so a PUT of 30 valid ids silently
      dropped 6 and invalid/duplicate entries ate the budget of valid ones.
M186  widgets_config.load() caught only OSError/JSONDecodeError: a widgets.json
      that parsed to a non-object or carried a non-numeric schema_version raised
      on EVERY load (``_loaded`` never latched).
M075  interop_registry.acknowledge/set_enabled/register discarded _save()'s False,
      so an unpersisted change still reported success.
M074  interop_registry load-modify-save had no lock and a fixed temp name, so
      concurrent writers lost each other's updates.
"""
from __future__ import annotations

import json
import os
import threading

import pytest

BD_GATE_SCOPE = "module"


# --------------------------------------------------------------------- widgets

@pytest.fixture
def wc(tmp_path, monkeypatch):
    from bulk_downloader import widgets_config
    cfg = tmp_path / "widgets.json"
    monkeypatch.setenv("BD_WIDGETS_CONFIG_PATH", str(cfg))
    widgets_config._reset_for_tests()
    yield widgets_config, cfg
    widgets_config._reset_for_tests()


def test_cap_admits_every_valid_widget(wc):
    widgets_config, _ = wc
    assert widgets_config.MAX_WIDGETS_PER_SCOPE == len(widgets_config.VALID_WIDGET_IDS) == 36


def test_put_30_valid_ids_keeps_all_30(wc):
    widgets_config, cfg = wc
    widgets_config.load()
    ids = sorted(widgets_config.VALID_WIDGET_IDS)[:30]
    out = widgets_config.set_global([{"id": w, "size": "md"} for w in ids])
    assert [w["id"] for w in out] == ids
    on_disk = json.loads(cfg.read_text(encoding="utf-8"))
    assert [w["id"] for w in on_disk["global"]] == ids


def test_invalid_and_duplicate_entries_do_not_consume_the_cap(wc):
    widgets_config, _ = wc
    widgets_config.load()
    junk = [{"id": "no_such_widget"}] * 30 + [{"id": "done_today"}] * 30 + ["x"] * 30
    out = widgets_config.set_global(junk + [{"id": "throughput", "size": "lg"}])
    assert out == [{"id": "done_today", "size": "sm"}, {"id": "throughput", "size": "lg"}]


def test_cap_still_bounds_the_output(wc, monkeypatch):
    widgets_config, _ = wc
    monkeypatch.setattr(widgets_config, "MAX_WIDGETS_PER_SCOPE", 3)
    ids = sorted(widgets_config.VALID_WIDGET_IDS)
    out = widgets_config._sanitize_list([{"id": "bogus"}] + [{"id": w} for w in ids])
    assert [w["id"] for w in out] == ids[:3]


@pytest.mark.parametrize("body", [
    "[]",
    '"a string"',
    "42",
    "null",
])
def test_load_tolerates_non_object_json(wc, body, capsys):
    widgets_config, cfg = wc
    cfg.write_text(body, encoding="utf-8")
    first = widgets_config.load()
    assert first["global"] == widgets_config.DEFAULT_WIDGETS
    assert first["schema_version"] == widgets_config.SCHEMA_VERSION
    assert "not a JSON object" in capsys.readouterr().err
    assert widgets_config.load() == first   # latched: no raise on the next load


@pytest.mark.parametrize("version", ['"one"', "null", "[1]", '{"v": 1}'])
def test_load_tolerates_bad_schema_version(wc, version, capsys):
    widgets_config, cfg = wc
    cfg.write_text(
        '{"schema_version": %s, "global": [{"id": "gpu", "size": "lg"}]}' % version,
        encoding="utf-8")
    snap = widgets_config.load()
    assert snap["schema_version"] == widgets_config.SCHEMA_VERSION
    assert snap["global"] == [{"id": "gpu", "size": "lg"}]
    assert "bad schema_version" in capsys.readouterr().err
    assert widgets_config.load() == snap


def test_load_good_file_unchanged(wc, capsys):
    """Negative control: a well-formed file loads as before, nothing logged."""
    widgets_config, cfg = wc
    cfg.write_text(json.dumps({
        "schema_version": 1,
        "global": [{"id": "gpu", "size": "lg"}],
        "per_site": {"s1": [{"id": "stuck", "size": "md"}]},
    }), encoding="utf-8")
    snap = widgets_config.load()
    assert snap["global"] == [{"id": "gpu", "size": "lg"}]
    assert snap["per_site"]["s1"] == [{"id": "stuck", "size": "md"}]
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------- interop

@pytest.fixture
def ir(tmp_path, monkeypatch):
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    from bulk_downloader import interop_registry
    return interop_registry


def _fail_replace(monkeypatch, ir):
    calls = []

    def boom(src, dst):
        calls.append((str(src), str(dst)))
        raise PermissionError(13, "Read-only file system", str(dst))
    monkeypatch.setattr(ir.os, "replace", boom)
    return calls


def test_set_enabled_reports_unpersisted_change(ir, monkeypatch):
    assert ir.register("chromium_extension", "e", sha256="h1")["enabled"] is False
    assert ir.set_enabled("chromium_extension", "e", True) is True
    with monkeypatch.context() as mp:
        calls = _fail_replace(mp, ir)
        assert ir.set_enabled("chromium_extension", "e", False) is False
    assert len(calls) == 1
    assert ir.get("chromium_extension", "e")["enabled"] is True   # disk kept the old value


def test_acknowledge_reports_unpersisted_change(ir, monkeypatch):
    ir.register("jd_plugin", "p", source="op")
    with monkeypatch.context() as mp:
        calls = _fail_replace(mp, ir)
        assert ir.acknowledge("jd_plugin", "p") is False
    assert len(calls) == 1
    assert ir.get("jd_plugin", "p")["risk_acknowledged"] is False


def test_register_raises_when_unpersisted(ir, monkeypatch, tmp_path):
    with monkeypatch.context() as mp:
        calls = _fail_replace(mp, ir)
        with pytest.raises(OSError, match="could not persist"):
            ir.register("ytdlp_plugin", "p1", source="repo")
    assert len(calls) == 1
    assert ir.get("ytdlp_plugin", "p1") is None
    assert sorted(os.listdir(tmp_path)) == ["interop_registry.json.lock"]   # no temp left behind


def test_unregistered_item_still_false_without_write(ir, monkeypatch):
    calls = _fail_replace(monkeypatch, ir)
    assert ir.acknowledge("jd_plugin", "ghost") is False
    assert ir.set_enabled("jd_plugin", "ghost", True) is False
    assert calls == []


def test_normal_saves_persist(ir):
    """Negative control: the success path still returns True and lands on disk."""
    ir.register("chromium_extension", "e", sha256="h1")
    assert ir.acknowledge("chromium_extension", "e") is True
    assert ir.set_enabled("chromium_extension", "e", True) is True
    assert ir.is_permitted("chromium_extension", "e", live_sha256="h1") is True
    raw = json.loads(open(ir._registry_path(), encoding="utf-8").read())
    assert raw["chromium_extension"]["e"]["enabled"] is True


def test_stale_fixed_temp_name_does_not_block_save(ir):
    """A leftover ``interop_registry.json.tmp`` (here a directory, so it cannot be
    overwritten) must not wedge every save: temp names are unique per write."""
    os.mkdir(str(ir._registry_path()) + ".tmp")
    ir.register("jd_plugin", "p", source="op")
    assert ir.acknowledge("jd_plugin", "p") is True
    assert ir.get("jd_plugin", "p")["risk_acknowledged"] is True


def test_concurrent_registers_do_not_lose_updates(ir, monkeypatch):
    """Every writer loads before any writer saves (forced by a barrier inside
    _load). Unlocked, each saves {its own item} over the others. Locked, the
    first holder times the barrier out and the rest load serially."""
    n = 8
    barrier = threading.Barrier(n)
    real_load = ir._load

    def load_then_wait():
        reg = real_load()
        try:
            barrier.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            pass
        return reg
    errors = []

    def worker(i):
        try:
            ir.register("jd_plugin", f"host{i}", source="op")
        except Exception as e:   # pragma: no cover - surfaced by the assert below
            errors.append(repr(e))
    with monkeypatch.context() as mp:
        mp.setattr(ir, "_load", load_then_wait)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not any(t.is_alive() for t in threads)
    assert errors == []
    assert sorted(i for _, i, _ in ir.list_all()) == sorted(f"host{i}" for i in range(n))


def test_unopenable_lock_file_still_writes(ir):
    """The sibling lock file cannot be opened (here: a directory sits on its
    name). The write proceeds under the thread lock and still persists."""
    os.mkdir(str(ir._registry_path()) + ".lock")
    ir.register("jd_plugin", "p", source="op")
    assert ir.set_enabled("jd_plugin", "p", True) is True
    assert ir.get("jd_plugin", "p")["enabled"] is True
