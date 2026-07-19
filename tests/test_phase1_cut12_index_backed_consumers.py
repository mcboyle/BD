"""Phase 1 Cut 1.2 (v3.66.613): repoint the capture read-consumers onto the
db `captures` index (Cut 1.1), off the in-memory _SCAN_CACHE / FS-walk.

Consumers repointed:
  * POST /api/captures/scan  -> reconcile: walk PRODUCES rows, then upsert into
    the table + prune-missing (durable). The summary still returns.
  * GET  /api/captures       -> serve from db_captures_all (survives a restart:
    a fresh _SCAN_CACHE still returns rows after a prior scan).
  * GET  /api/analyzer/captures -> serve from the index; reconcile-if-empty so it
    never regresses to "No captures found" when captures exist on disk.

SAFETY INVARIANT (must stay green): the token->file resolve gate
(dom_analyzer.resolve_capture_token) stays FS-authoritative — it is NOT repointed
to the index. A stale index row must never authorize resolving a swapped/symlinked
file. The "zero FS walk" goal is for LIST/SUMMARY, not for the resolve security
re-check (adjacent to open finding F-APP03-02).

RED: the persistence-across-fresh-cache assertions fail on 612 (list reads the
ephemeral cache); the analyzer-index assertion fails (it always walks).
"""
from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path

import bulk_downloader.app_captures as ac
import bulk_downloader.app_analyzer as an  # noqa: F401  (import proves the module loads)
import bulk_downloader.dom_analyzer as da
from bulk_downloader import db


def _seed_capture_tree(monkeypatch) -> Path:
    root = Path(tempfile.mkdtemp())
    d = root / "captures" / "template_onboarding" / "app.reptyle.com_0b60f1ec_20250621_aa"
    d.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(d / "x.wacz", "w") as z:
        z.writestr("archive/capture.json", "{}")
    flat = root / "captures"
    with zipfile.ZipFile(flat / "auth.site.tv_dead_20250101_bb.wacz", "w") as z:
        z.writestr("archive/capture.json", "{}")
    monkeypatch.setattr(da, "_project_root", lambda: root)
    ac._SCAN_CACHE["rows"] = []
    ac._SCAN_CACHE["summary"] = None
    ac._SCAN_CACHE["built_at"] = None
    return root


def test_scan_persists_rows_into_the_index(fresh_app, monkeypatch):
    """POST /api/captures/scan must UPSERT the walked rows into the db table (not
    only the in-memory cache)."""
    _seed_capture_tree(monkeypatch)
    r = fresh_app.post("/api/captures/scan", json={})
    assert r.status_code == 200, r.get_data(as_text=True)
    rows = db.db_captures_all()
    assert len(rows) >= 2, f"scan did not persist rows into the index: {rows}"
    hosts = {row["host"] for row in rows}
    assert "app.reptyle.com" in hosts


def test_list_survives_a_fresh_cache(fresh_app, monkeypatch):
    """GET /api/captures must serve from the index, so a restart (simulated by
    wiping _SCAN_CACHE) still returns the rows without a re-scan. This is the
    whole point of Cut 1.1/1.2: no more restart-ephemeral inventory."""
    _seed_capture_tree(monkeypatch)
    fresh_app.post("/api/captures/scan", json={})
    # simulate a process restart: the in-memory cache is gone, the DB persists
    ac._SCAN_CACHE["rows"] = []
    ac._SCAN_CACHE["summary"] = None
    ac._SCAN_CACHE["built_at"] = None
    body = fresh_app.get("/api/captures").get_json()
    assert body["ok"] is True
    assert body["total"] >= 2, \
        f"list did not survive a fresh cache (still cache-backed?): {body['total']}"
    rels = {c["rel_path"] for c in body["captures"]}
    assert any("template_onboarding" in x for x in rels)


def test_list_filters_and_paginates_from_index(fresh_app, monkeypatch):
    """Host/kind filters + paging still work when served from the index."""
    _seed_capture_tree(monkeypatch)
    fresh_app.post("/api/captures/scan", json={})
    ac._SCAN_CACHE["rows"] = []  # force index-backed read
    r = fresh_app.get("/api/captures?host=app.reptyle.com")
    caps = r.get_json()["captures"]
    assert caps and all(c["host"] == "app.reptyle.com" for c in caps)
    r2 = fresh_app.get("/api/captures?page=1&per_page=1")
    b2 = r2.get_json()
    assert b2["per_page"] == 1 and len(b2["captures"]) == 1 and b2["total"] >= 2


def test_scan_prunes_deleted_captures_from_index(fresh_app, monkeypatch):
    """A capture deleted on disk must drop out of the index on the next scan
    (db_captures_prune_missing wired into the reconcile)."""
    root = _seed_capture_tree(monkeypatch)
    fresh_app.post("/api/captures/scan", json={})
    before = {r["rel_path"] for r in db.db_captures_all()}
    # delete one capture on disk
    gone = root / "captures" / "auth.site.tv_dead_20250101_bb.wacz"
    gone.unlink()
    fresh_app.post("/api/captures/scan", json={})
    after = {r["rel_path"] for r in db.db_captures_all()}
    assert len(after) < len(before), "scan did not prune the deleted capture"
    assert not any("auth.site.tv" in x for x in after)


def test_analyzer_captures_is_index_backed(fresh_app, monkeypatch):
    """GET /api/analyzer/captures serves from the index (reconcile-if-empty), so
    it reflects the same durable inventory — and populates it on first use rather
    than regressing to empty."""
    _seed_capture_tree(monkeypatch)
    # no explicit scan first: the analyzer picker must still surface the captures
    body = fresh_app.get("/api/analyzer/captures").get_json()
    assert body["ok"] is True, body
    assert len(body["captures"]) >= 2, \
        f"analyzer picker empty; index-backed reconcile-if-empty missing: {body}"
    # and it seeded the durable index as a side effect
    assert len(db.db_captures_all()) >= 2


def test_resolve_gate_stays_fs_authoritative(monkeypatch):
    """SAFETY REGRESSION GUARD: resolve_capture_token must refuse a token whose
    on-disk target is a symlink, EVEN IF the token sits in the index. The gate is
    the on-disk symlink/is_file check, never the index row. If a future edit
    repoints the resolver to trust the index, this fails."""
    root = Path(tempfile.mkdtemp())
    capdir = root / "captures"
    capdir.mkdir(parents=True, exist_ok=True)
    real = capdir / "real.wacz"
    with zipfile.ZipFile(real, "w") as z:
        z.writestr("archive/capture.json", "{}")
    monkeypatch.setattr(da, "_project_root", lambda: root)
    # seed the INDEX with a rel_path pointing at a name we will make a symlink
    outside = root / "outside_secret.txt"
    outside.write_text("secret")
    link = capdir / "sneaky.wacz"
    os.symlink(outside, link)
    db.db_init()
    db.db_captures_upsert([{
        "rel_path": "captures/sneaky.wacz", "name": "sneaky.wacz", "dir": "captures",
        "host": "x", "captured_at": 1.0, "size": 1, "kind": "wacz", "redacted": False,
    }])
    # even though the index lists it, the resolver must refuse the symlink
    resolved = da.resolve_capture_token("captures/sneaky.wacz", root=root)
    assert resolved is None, \
        "resolve gate trusted the index over the on-disk symlink check (F-APP03 regression)"
    # the real file still resolves (sanity: the gate isn't just refusing everything)
    ok = da.resolve_capture_token("captures/real.wacz", root=root)
    assert ok is not None, "resolver refused a legitimate real capture"
