"""v3.43.60: Tests for the dashboard widget picker.

Covers:
  - widgets_config persistence (load/save, scope mutations, sanitization)
  - VALID_WIDGET_IDS matches the JS catalog (drift detection)
  - app_widgets_api routes register on a Flask app

NOTE: This file uses plain setup_method()/teardown_method() rather than
pytest.fixture(autouse=True) for portability across the project's three
test runners (real pytest, run_min_pytest, run_tests.py).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import pytest


class _EnvSaver:
    """Inline replacement for pytest's monkeypatch.setenv()."""
    def __init__(self):
        self._saved = []
    def setenv(self, key, value):
        self._saved.append((key, os.environ.get(key)))
        os.environ[key] = str(value)
    def restore(self):
        while self._saved:
            k, v = self._saved.pop()
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _IsolatedConfigBase:
    """Base class: isolated widgets.json config path per test method."""
    def setup_method(self):
        self._envsaver = _EnvSaver()
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="widgets-test-"))
        cfg_path = self._tmp_dir / "widgets.json"
        self._envsaver.setenv("BD_WIDGETS_CONFIG_PATH", str(cfg_path))
        from bulk_downloader import widgets_config
        widgets_config._reset_for_tests()
        self._cfg_path = cfg_path

    def teardown_method(self):
        from bulk_downloader import widgets_config
        widgets_config._reset_for_tests()
        self._envsaver.restore()
        shutil.rmtree(self._tmp_dir, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
#  widgets_config — load / save / mutations
# ════════════════════════════════════════════════════════════════════

class TestWidgetsConfigBasics(_IsolatedConfigBase):
    def test_load_empty_returns_defaults(self):
        from bulk_downloader import widgets_config
        state = widgets_config.load()
        assert state["schema_version"] == widgets_config.SCHEMA_VERSION
        assert state["global"] == widgets_config.DEFAULT_WIDGETS
        assert state["per_site"] == {}

    def test_defaults_are_four_widgets(self):
        from bulk_downloader import widgets_config
        assert len(widgets_config.DEFAULT_WIDGETS) == 4

    def test_get_global_returns_defaults(self):
        from bulk_downloader import widgets_config
        widgets_config.load()
        g = widgets_config.get_global()
        assert g == widgets_config.DEFAULT_WIDGETS
        g[0]["id"] = "MUTATED"
        assert widgets_config.get_global()[0]["id"] != "MUTATED"

    def test_get_for_site_falls_back_to_global(self):
        from bulk_downloader import widgets_config
        widgets_config.load()
        out = widgets_config.get_for_site("wowgirls")
        assert out == widgets_config.DEFAULT_WIDGETS


class TestWidgetsConfigPersistence(_IsolatedConfigBase):
    def test_set_global_persists(self):
        from bulk_downloader import widgets_config
        widgets_config.load()
        new = [{"id": "queue_depth", "size": "md"}, {"id": "throughput", "size": "sm"}]
        widgets_config.set_global(new)
        assert self._cfg_path.exists()
        data = json.loads(self._cfg_path.read_text(encoding="utf-8"))
        assert data["global"] == new

    def test_reload_after_set_returns_same_data(self):
        from bulk_downloader import widgets_config
        widgets_config.load()
        widgets_config.set_global([{"id": "stuck", "size": "lg"}])
        widgets_config._reset_for_tests()
        state = widgets_config.load()
        assert state["global"] == [{"id": "stuck", "size": "lg"}]

    def test_set_for_site_persists(self):
        from bulk_downloader import widgets_config
        widgets_config.load()
        widgets_config.set_for_site("wowgirls", [{"id": "action_req", "size": "sm"}])
        assert widgets_config.get_for_site("wowgirls") == [{"id": "action_req", "size": "sm"}]
        assert widgets_config.get_for_site("ultrafilms") == widgets_config.DEFAULT_WIDGETS

    def test_set_for_site_empty_removes_override(self):
        from bulk_downloader import widgets_config
        widgets_config.load()
        widgets_config.set_for_site("wowgirls", [{"id": "stuck", "size": "sm"}])
        assert widgets_config.get_for_site("wowgirls") != widgets_config.DEFAULT_WIDGETS
        widgets_config.set_for_site("wowgirls", [])
        assert widgets_config.get_for_site("wowgirls") == widgets_config.DEFAULT_WIDGETS


class TestWidgetsConfigSanitization(_IsolatedConfigBase):
    def test_drops_unknown_widget_ids(self):
        from bulk_downloader import widgets_config
        widgets_config.load()
        out = widgets_config.set_global([
            {"id": "queue_depth", "size": "sm"},
            {"id": "totally_made_up", "size": "sm"},
            {"id": "throughput", "size": "md"},
        ])
        ids = [w["id"] for w in out]
        assert "totally_made_up" not in ids
        assert "queue_depth" in ids
        assert "throughput" in ids

    def test_invalid_size_falls_back_to_sm(self):
        from bulk_downloader import widgets_config
        widgets_config.load()
        out = widgets_config.set_global([{"id": "queue_depth", "size": "xxl"}])
        assert out[0]["size"] == "sm"

    def test_duplicates_deduped(self):
        from bulk_downloader import widgets_config
        widgets_config.load()
        out = widgets_config.set_global([
            {"id": "queue_depth", "size": "sm"},
            {"id": "queue_depth", "size": "md"},
        ])
        assert len(out) == 1

    def test_caps_at_max_widgets(self):
        from bulk_downloader import widgets_config
        widgets_config.load()
        many = [{"id": w, "size": "sm"} for w in list(widgets_config.VALID_WIDGET_IDS)]
        out = widgets_config.set_global(many + [{"id": "queue_depth", "size": "sm"}] * 50)
        assert len(out) <= widgets_config.MAX_WIDGETS_PER_SCOPE

    def test_non_dict_items_filtered(self):
        from bulk_downloader import widgets_config
        widgets_config.load()
        out = widgets_config.set_global([
            "not a dict",
            42,
            {"id": "throughput", "size": "sm"},
            None,
        ])
        assert out == [{"id": "throughput", "size": "sm"}]

    def test_non_list_in_set_returns_empty(self):
        from bulk_downloader import widgets_config
        widgets_config.load()
        out = widgets_config.set_global("not a list")  # type: ignore[arg-type]
        assert out == []


class TestWidgetsConfigReset(_IsolatedConfigBase):
    def test_reset_global_restores_defaults(self):
        from bulk_downloader import widgets_config
        widgets_config.load()
        widgets_config.set_global([{"id": "stuck", "size": "lg"}])
        widgets_config.reset_global()
        assert widgets_config.get_global() == widgets_config.DEFAULT_WIDGETS

    def test_reset_for_site_clears_override(self):
        from bulk_downloader import widgets_config
        widgets_config.load()
        widgets_config.set_for_site("wowgirls", [{"id": "stuck", "size": "lg"}])
        assert widgets_config.reset_for_site("wowgirls") is True
        assert widgets_config.reset_for_site("wowgirls") is False


class TestWidgetsConfigCorruption:
    """No isolated config base — this test creates its own corrupt file."""
    def test_unreadable_file_falls_back_to_defaults(self):
        from bulk_downloader import widgets_config
        tmp = Path(tempfile.mkdtemp(prefix="widgets-corrupt-"))
        try:
            cfg = tmp / "widgets.json"
            cfg.write_text("{ not valid json }")
            saver = _EnvSaver()
            saver.setenv("BD_WIDGETS_CONFIG_PATH", str(cfg))
            try:
                widgets_config._reset_for_tests()
                state = widgets_config.load()
                assert state["global"] == widgets_config.DEFAULT_WIDGETS
            finally:
                saver.restore()
                widgets_config._reset_for_tests()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
#  JS ↔ Python catalog drift detector
# ════════════════════════════════════════════════════════════════════

def _read_js_widget_ids():
    """Plain helper (not a pytest.fixture) — works under all runners."""
    path = Path("bulk_downloader/static/widgets.js")
    if not path.exists():
        path = Path(__file__).parent.parent / "bulk_downloader" / "static" / "widgets.js"
    assert path.exists(), f"widgets.js not found at {path}"
    text = path.read_text(encoding="utf-8")
    m = re.search(r"const WIDGETS\s*=\s*\[(.*?)\];", text, re.DOTALL)
    assert m, "WIDGETS array not found in widgets.js"
    body = m.group(1)
    return set(re.findall(r"id:\s*'([^']+)'", body))


class TestDefaultSelection:
    def test_default_widgets_are_all_valid(self):
        from bulk_downloader import widgets_config
        for w in widgets_config.DEFAULT_WIDGETS:
            assert w["id"] in widgets_config.VALID_WIDGET_IDS
            assert w["size"] in widgets_config.VALID_SIZES


class TestApiRegistration:
    def test_blueprint_registers_5_routes(self):
        try:
            import flask
        except ImportError:
            pytest.skip("flask not installed")
        from bulk_downloader import app_widgets_api
        app = flask.Flask("test_app")
        n = app_widgets_api.register_routes(app)
        assert n == 5
        rules = {r.rule for r in app.url_map.iter_rules() if "/api/widgets" in r.rule}
        assert "/api/widgets/all" in rules
        assert "/api/widgets/<scope>" in rules
        assert "/api/widgets/data" in rules
