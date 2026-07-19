"""v3.66.649 -- S2.1 (part): search/saved-search result facets.

app_search gains a read-only /api/search/facets that groups matching history rows
by site and by status (the same filter fields a saved search uses), so the UI can
show 'N matches across M sites' and drill down. Pure db GROUP BY; read-only.

Sandbox-safe: isolated temp DB via db.DB_PATH + db_init, seeded via db_log.
"""
from __future__ import annotations

import os
import tempfile

import bulk_downloader.db as db
import bulk_downloader.app_search as asr


def _iso_db():
    d = tempfile.mkdtemp(prefix="facets_")
    dbf = os.path.join(d, "queue.db")
    db.DB_PATH = dbf
    db.db_init()


def test_facet_counts_groups_by_site_and_status():
    saved = db.DB_PATH
    _iso_db()
    try:
        db.db_log(site_id="a", site_name="A", url="http://a/1", status="done", filename="f1")
        db.db_log(site_id="a", site_name="A", url="http://a/2", status="done", filename="f2")
        db.db_log(site_id="a", site_name="A", url="http://a/3", status="error", filename="f3")
        db.db_log(site_id="b", site_name="B", url="http://b/1", status="done", filename="f4")
        fac = asr._facet_counts()
        assert fac["by_site"] == {"a": 3, "b": 1}, fac
        assert fac["by_status"] == {"done": 3, "error": 1}, fac
        assert fac["total"] == 4, fac
    finally:
        db.DB_PATH = saved


def test_facet_counts_respects_site_filter():
    saved = db.DB_PATH
    _iso_db()
    try:
        db.db_log(site_id="a", site_name="A", url="http://a/1", status="done", filename="f1")
        db.db_log(site_id="b", site_name="B", url="http://b/1", status="done", filename="f2")
        fac = asr._facet_counts(site_id="a")
        assert fac["by_site"] == {"a": 1}, fac
        assert fac["total"] == 1, fac
    finally:
        db.DB_PATH = saved


def test_facets_route_registered_and_returns_shape():
    from flask import Flask
    saved = db.DB_PATH
    _iso_db()
    try:
        db.db_log(site_id="a", site_name="A", url="http://a/1", status="done", filename="movie")
        app = Flask(__name__)
        asr.register_routes(app)
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/search/facets" in rules, sorted(rules)
        r = app.test_client().get("/api/search/facets?query=movie")
        assert r.status_code == 200, r.status_code
        data = r.get_json()
        assert data["ok"] is True
        assert data["facets"]["total"] == 1, data
        assert data["facets"]["by_site"] == {"a": 1}, data
    finally:
        db.DB_PATH = saved
