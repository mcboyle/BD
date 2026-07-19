"""Cut 3 — route contract for the two read-only import-preview endpoints.

  POST /api/user_templates/import/preview   (body: export payload; ?merge=0|1)
  POST /api/marketplace/import/preview      (body: {bundle, target_site_id?, verify_with?})

Both are READ-ONLY: they classify what an import would do and return it; they
persist nothing. Tested with a sessionless test client (no session cookie ->
_check_csrf skips), so no pairing/CSRF dance is required.

RED on pristine 372: both routes 404.
"""
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def _with_temp_user_templates():
    from bulk_downloader import user_templates as ut
    with tempfile.TemporaryDirectory() as td:
        orig = ut.USER_TEMPLATES_FILE
        ut.USER_TEMPLATES_FILE = Path(td) / "user_templates.json"
        try:
            yield ut
        finally:
            ut.USER_TEMPLATES_FILE = orig


def _good_learned():
    return {"download": {"row_selectors": ["a.btn[href]"], "url_attribute": "href"}}


def _store_bytes(ut):
    p = ut.USER_TEMPLATES_FILE
    return p.read_bytes() if p.exists() else b""


def test_user_templates_import_preview_route_is_readonly():
    from bulk_downloader import app as A
    with _with_temp_user_templates() as ut:
        ut.save_user_template("Existing", "d", [], _good_learned())
        before = _store_bytes(ut)
        c = A.app.test_client()
        payload = {"version": 1, "templates": [{
            "id": "user_new_route", "name": "New via route",
            "description": "d", "patterns": [r"x\.com"],
            "learned": _good_learned(),
        }]}
        r = c.post("/api/user_templates/import/preview", json=payload)
        assert r.status_code == 200, r.get_json()
        d = r.get_json()
        assert d["ok"] is True
        assert d["mode"] == "merge"
        assert d["counts"]["new"] == 1
        assert any(i["status"] == "new" for i in d["items"])
        # READ-ONLY: store byte-unchanged, still exactly one template
        assert _store_bytes(ut) == before
        assert len(ut.list_user_templates()) == 1


def test_user_templates_import_preview_replace_mode_param():
    from bulk_downloader import app as A
    with _with_temp_user_templates() as ut:
        ut.save_user_template("WouldBeWiped", "d", [], _good_learned())
        before = _store_bytes(ut)
        c = A.app.test_client()
        payload = {"templates": [{
            "id": "user_only", "name": "Only", "description": "d",
            "patterns": [], "learned": _good_learned(),
        }]}
        r = c.post("/api/user_templates/import/preview?merge=0", json=payload)
        assert r.status_code == 200, r.get_json()
        d = r.get_json()
        assert d["mode"] == "replace"
        assert d["counts"]["destructive"] == 1  # the existing one would be removed
        assert _store_bytes(ut) == before


def test_marketplace_import_preview_route():
    from bulk_downloader import app as A
    from bulk_downloader import marketplace as mp
    c = A.app.test_client()
    bundle = {
        "schema": mp.SCHEMA_VERSION,
        "exported_at": "2026-01-01T00:00:00Z",
        "site_id": "newsite",
        "template": {"name": "N", "password": "secret",
                     "login_url": "https://n.test"},
        "metadata": {"name": "N"},
    }
    r = c.post("/api/marketplace/import/preview", json={"bundle": bundle})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d["ok"] is True
    assert d["site_id"] == "newsite"
    assert d["status"] in ("new", "changed")
    assert "password" not in d["config_preview"]
    assert "password" in d["secrets_omitted"]


def test_marketplace_import_preview_requires_bundle():
    from bulk_downloader import app as A
    c = A.app.test_client()
    r = c.post("/api/marketplace/import/preview", json={})
    assert r.status_code == 400, r.get_json()
