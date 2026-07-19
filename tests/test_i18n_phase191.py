"""Tests for Phase 191 i18n runtime (translation lookup + DOM wrap)."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path


def _isolated_bd():
    tmp = tempfile.mkdtemp(prefix="bd-i18n-test-")
    # v3.66.9: snapshot env + modules so teardown_module can
    # restore them. Without this, BD_INSTALL_DIR leaks to
    # subsequent test files that use chdir-only isolation.
    if not _ISOLATED_BD_SAVED:
        _ISOLATED_BD_SAVED['env'] = {
            k: os.environ.get(k)
            for k in ('BD_INSTALL_DIR', 'BD_DISABLE_KEEPALIVE')
        }
        _ISOLATED_BD_SAVED['modules'] = {
            k: v for k, v in sys.modules.items()
            if k.startswith('bulk_downloader')
        }
    os.environ["BD_INSTALL_DIR"] = tmp
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    for m in list(sys.modules):
        if m.startswith("bulk_downloader"):
            del sys.modules[m]
    return tmp


_ISOLATED_BD_SAVED = {}


def teardown_module(module):
    """v3.66.9: pytest hook — restore env + sys.modules after
    this file's tests run, so the leaked BD_INSTALL_DIR doesn't
    taint downstream tests."""
    if not _ISOLATED_BD_SAVED:
        return
    for k, v in _ISOLATED_BD_SAVED['env'].items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    for m in [k for k in list(sys.modules)
              if k.startswith('bulk_downloader')]:
        del sys.modules[m]
    sys.modules.update(_ISOLATED_BD_SAVED['modules'])
    _ISOLATED_BD_SAVED.clear()


class TestLocaleLoading(unittest.TestCase):

    def setup_method(self, method=None):
        self.tmp = _isolated_bd()
        from bulk_downloader import i18n
        # Clear any stale cache from earlier tests
        i18n._LOCALES_CACHE.clear()
        self.i18n = i18n

    setUp = setup_method

    def test_baseline_en_locale_ships(self):
        """The package should ship a baseline en.json."""
        bundled = self.i18n._bundled_locales_dir()
        en_path = bundled / "en.json"
        self.assertTrue(en_path.is_file(),
            f"expected baseline locale at {en_path}")
        data = json.loads(en_path.read_text(encoding="utf-8"))
        # Smoke: tabs + primary buttons exist
        for required_key in ("tab.queue", "tab.history", "btn.save",
                             "btn.cancel", "btn.start"):
            self.assertIn(required_key, data,
                f"baseline en.json missing {required_key!r}")

    def test_demo_es_locale_ships(self):
        """Spanish demo locale ships as proof-of-concept."""
        bundled = self.i18n._bundled_locales_dir()
        es_path = bundled / "es.json"
        self.assertTrue(es_path.is_file())
        data = json.loads(es_path.read_text(encoding="utf-8"))
        self.assertEqual(data.get("tab.queue"), "Cola")
        self.assertEqual(data.get("btn.save"), "Guardar")

    def test_load_locale_strips_meta_block(self):
        """The _meta header in the JSON file is metadata for translators;
        the runtime should not see it."""
        d = self.i18n.load_locale("en")
        self.assertNotIn("_meta", d)
        # Sanity: real strings DO come through
        self.assertEqual(d.get("tab.queue"), "Queue")

    def test_missing_locale_returns_empty(self):
        d = self.i18n.load_locale("xx")
        self.assertEqual(d, {})

    def test_install_dir_overrides_bundled(self):
        """Operator can drop a custom locale in INSTALL_DIR/locales/
        to override the bundled baseline. Used for partial translations
        or per-deployment terminology."""
        override_dir = Path(self.tmp) / "locales"
        override_dir.mkdir(parents=True, exist_ok=True)
        (override_dir / "en.json").write_text(json.dumps({
            "_meta": {"lang": "en", "name": "Custom override"},
            "tab.queue": "MY-CUSTOM-QUEUE-LABEL",
        }), encoding="utf-8")
        # Re-clear cache so the override is picked up
        self.i18n._LOCALES_CACHE.clear()
        d = self.i18n.load_locale("en")
        self.assertEqual(d.get("tab.queue"), "MY-CUSTOM-QUEUE-LABEL")

    def test_load_locale_caches(self):
        """Repeated calls return the same dict reference (cache hit).
        Prevents thrashing the disk on every page load."""
        a = self.i18n.load_locale("en")
        b = self.i18n.load_locale("en")
        self.assertIs(a, b)


class TestI18nAPI(unittest.TestCase):

    def setup_method(self, method=None):
        _isolated_bd()
        from bulk_downloader.db import db_init
        db_init()
        from bulk_downloader import i18n
        i18n._LOCALES_CACHE.clear()
        from bulk_downloader import app as bd_app
        self.client = bd_app.app.test_client()

    setUp = setup_method

    def test_load_endpoint_serves_baseline_en(self):
        r = self.client.get("/api/i18n/load/en")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data.get("lang"), "en")
        strings = data.get("strings") or {}
        self.assertIn("tab.queue", strings)
        self.assertEqual(strings["tab.queue"], "Queue")
        # Meta should be stripped from API response
        self.assertNotIn("_meta", strings)

    def test_load_endpoint_serves_spanish(self):
        r = self.client.get("/api/i18n/load/es")
        self.assertEqual(r.status_code, 200)
        strings = r.get_json()["strings"]
        self.assertEqual(strings.get("tab.queue"), "Cola")
        self.assertEqual(strings.get("btn.save"), "Guardar")

    def test_load_endpoint_returns_empty_for_missing(self):
        r = self.client.get("/api/i18n/load/xx")
        # 200 with empty strings — front-end falls back to literals
        self.assertEqual(r.status_code, 200)
        strings = r.get_json()["strings"]
        self.assertEqual(strings, {})

    def test_locales_endpoint_lists_available(self):
        r = self.client.get("/api/i18n/locales")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        # Endpoint returns {"available": [...lang_codes...]}
        codes = data.get("available", [])
        self.assertIn("en", codes,
            f"expected 'en' in locales list, got {codes}")
        self.assertIn("es", codes,
            f"expected 'es' in locales list, got {codes}")


if __name__ == "__main__":
    unittest.main()
