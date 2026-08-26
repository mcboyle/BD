"""v3.57 (Phase 9) — regression tests for the library backlog.

Covers the two testable Phase 9 items shipped in v3.57.0:
#15 filename templating live preview, #69 integrity / rename detection.
"""
import os
import sqlite3
import sys

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

# ── #15 filename templating live preview ────────────────────────────────

def test_preview_renders_basic_template():
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.post("/api/sites/preview_filename",
               json={"template": "{site}/{title}{ext}"})
    body = r.get_json()
    assert body["ok"] is True
    assert body["preview"] == "examplesite/Sample Scene Title.mp4"


def test_preview_uses_sample_context():
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.post("/api/sites/preview_filename",
               json={"template": "{resolution}"})
    assert r.get_json()["preview"] == "1080p"


def test_preview_caller_context_overrides_sample():
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.post("/api/sites/preview_filename",
               json={"template": "{title}{ext}",
                     "context": {"title": "My Custom Title"}})
    assert r.get_json()["preview"] == "My Custom Title.mp4"


def test_preview_flags_unknown_placeholders():
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.post("/api/sites/preview_filename",
               json={"template": "{title}_{nonsense}{ext}"})
    body = r.get_json()
    assert "nonsense" in body["unknown_placeholders"]
    # Unknown placeholder is stripped from the rendered output
    assert "nonsense" not in body["preview"]


def test_preview_reports_used_placeholders():
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.post("/api/sites/preview_filename",
               json={"template": "{site}-{year}{ext}"})
    used = set(r.get_json()["used_placeholders"])
    assert used == {"site", "year", "ext"}


def test_preview_rejects_non_string_template():
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.post("/api/sites/preview_filename",
               json={"template": ["not", "a", "string"]})
    assert r.status_code == 400


def test_preview_empty_template():
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.post("/api/sites/preview_filename", json={"template": ""})
    body = r.get_json()
    assert body["ok"] is True
    assert body["preview"] == ""


# ── #69 integrity / rename detection ────────────────────────────────────

def test_integrity_endpoint_shape():
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.get("/api/library/integrity")
    body = r.get_json()
    assert body["ok"] is True
    assert "issues" in body
    assert "stats" in body
    assert "count" in body


def test_integrity_endpoint_empty_clean_install():
    """A fresh install has no integrity issues."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.get("/api/library/integrity")
    body = r.get_json()
    assert body["ok"] is True
    assert body["count"] == 0
    stats = body["stats"]
    assert stats["ok"] is True
    assert stats["inventory_status"] == "measured", stats
    assert stats["available"] is True
    assert stats["open_issues"] == 0


def test_integrity_endpoint_unreadable_inventory_stays_unknown(monkeypatch):
    """An unreadable inventory must not look like a clean fresh install."""
    from flask import Flask

    from bulk_downloader import app_library
    from bulk_downloader import bitrot
    from bulk_downloader import db

    class UnreadableInventory:
        def __enter__(self):
            raise sqlite3.OperationalError("database is locked")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(bitrot, "_ensure_integrity_table", lambda: None)
    monkeypatch.setattr(db, "db_conn", UnreadableInventory)

    app = Flask("library-integrity-measurement-probe")
    app.register_blueprint(app_library.library_bp)
    response = app.test_client().get("/api/library/integrity")
    assert response.status_code == 200
    body = response.get_json()
    stats = body["stats"]
    assert body["ok"] is True
    assert body["count"] == 0
    assert stats["ok"] is False
    assert stats["inventory_status"] == "unknown"
    assert stats["available"] is False
    assert "locked" in stats["error"]
    assert stats["open_issues"] is None
    assert stats["open_issues"] != 0


def test_integrity_endpoint_kind_filter():
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.get("/api/library/integrity?kind=missing")
    assert r.get_json()["ok"] is True


def test_integrity_endpoint_limit_clamped():
    from bulk_downloader import app as a
    c = a.app.test_client()
    # An absurd limit should be clamped, not error
    r = c.get("/api/library/integrity?limit=999999")
    assert r.get_json()["ok"] is True
