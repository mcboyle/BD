"""Item 3 -- the captures SCAN + browser routes.

POST /api/captures/scan rebuilds the recursive inventory + caches it (the
expensive, operator-triggered call). GET /api/captures serves the cached
inventory, paginated + filterable, opening no zips. Value-free payloads (relative
subpath tokens + cheap metadata, never absolute paths or secrets -- F2 posture).
"""
from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

import bulk_downloader.app as bd_app
import bulk_downloader.app_captures as ac
import bulk_downloader.dom_analyzer as da


def _seed_capture_tree(monkeypatch) -> Path:
    root = Path(tempfile.mkdtemp())
    d = root / "captures" / "template_onboarding" / "app.reptyle.com_0b60f1ec_20250621_aa"
    d.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(d / "x.wacz", "w") as z:
        z.writestr("archive/capture.json", "{}")
    flat = root / "captures"
    with zipfile.ZipFile(flat / "auth.site.tv_dead_20250101_bb.wacz", "w") as z:
        z.writestr("archive/capture.json", "{}")
    # point dom_analyzer's project root at the seeded tree
    monkeypatch.setattr(da, "_project_root", lambda: root)
    # clear any cached inventory between tests
    ac._SCAN_CACHE["rows"] = []
    ac._SCAN_CACHE["summary"] = None
    ac._SCAN_CACHE["built_at"] = None
    return root


def test_scan_route_builds_inventory(fresh_app, monkeypatch):
    _seed_capture_tree(monkeypatch)
    r = fresh_app.post("/api/captures/scan", json={})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["ok"] is True
    assert body["total"] >= 2
    assert body["new_since_last"] == body["total"]  # first scan: all new
    assert body["by_host"].get("app.reptyle.com", 0) >= 1
    assert isinstance(body["took_ms"], (int, float))


def test_list_route_serves_cache(fresh_app, monkeypatch):
    _seed_capture_tree(monkeypatch)
    # before any scan, the list is empty + flagged not-yet-scanned
    r0 = fresh_app.get("/api/captures")
    assert r0.status_code == 200
    assert r0.get_json()["scanned"] is False
    assert r0.get_json()["total"] == 0
    # after a scan, the list serves the cached rows
    fresh_app.post("/api/captures/scan", json={})
    r = fresh_app.get("/api/captures")
    body = r.get_json()
    assert body["ok"] is True
    assert body["scanned"] is True
    assert body["total"] >= 2
    rels = {c["rel_path"] for c in body["captures"]}
    assert any("template_onboarding" in x for x in rels)


def test_list_route_filters_by_host(fresh_app, monkeypatch):
    _seed_capture_tree(monkeypatch)
    fresh_app.post("/api/captures/scan", json={})
    r = fresh_app.get("/api/captures?host=app.reptyle.com")
    caps = r.get_json()["captures"]
    assert caps and all(c["host"] == "app.reptyle.com" for c in caps)


def test_list_route_paginates(fresh_app, monkeypatch):
    _seed_capture_tree(monkeypatch)
    fresh_app.post("/api/captures/scan", json={})
    r = fresh_app.get("/api/captures?page=1&per_page=1")
    body = r.get_json()
    assert body["per_page"] == 1
    assert len(body["captures"]) == 1
    assert body["total"] >= 2


def test_list_route_no_absolute_paths(fresh_app, monkeypatch):
    root = _seed_capture_tree(monkeypatch)
    fresh_app.post("/api/captures/scan", json={})
    raw = fresh_app.get("/api/captures").get_data(as_text=True)
    # F2: the value-free payload carries relative subpath tokens only
    assert str(root) not in raw
