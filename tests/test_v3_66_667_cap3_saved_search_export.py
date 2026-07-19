"""Cut 667 — CAP-3: saved-search -> scheduled-export chaining.

A scheduled export can be LINKED to a saved search via a new
``source_saved_search`` column. When set, the export resolves its rows through
the SAME FTS path the saved search uses (``db_search_fts(query, site_id,
status)``), so the export tracks the LIVE search criteria (edit the search ->
the export follows) rather than a frozen ``filter_json`` snapshot.

Design:
  * additive nullable ``source_saved_search`` column (lazy-migrated; null =
    today's behavior, byte-identical).
  * ``saved_searches.criteria_for(id)`` returns {query, site_id, status} or None
    (missing/disabled).
  * ``scheduled_exports._resolve_source_rows(id)`` -> row list via db_search_fts,
    or None (missing search -> caller falls back to filter_json: FAIL-OPEN).
  * the exporters gain an optional ``rows=`` override so a pre-resolved row list
    exports directly (backward-compatible: rows=None -> existing filter path).

Backend-only; no route/guard/import-edge (function-local imports). These are
unit tests with a temp BD_HOME db + a monkeypatched db_search_fts seam.
"""
import os
import tempfile

from bulk_downloader import exports as _exp
from bulk_downloader import scheduled_exports as _se
from bulk_downloader import saved_searches as _ss


# ── exporter rows= override (pure, no db) ────────────────────────────

def test_exporters_accept_rows_override():
    rows = [{"id": 1, "site_id": "demo", "url": "u", "filename": "",
             "status": "done", "message": "m"}]
    csv = _exp.to_csv({"limit": 0}, rows=rows).decode()
    assert "demo" in csv and "done" in csv, csv
    js = _exp.to_json({"limit": 0}, rows=rows).decode()
    assert '"site_id": "demo"' in js, js


def test_exporter_rows_none_is_unchanged_behavior():
    # rows=None (default) must still resolve via the filter path -> a bytes blob
    # with the header row present (no crash, backward-compatible).
    out = _exp.to_csv({"limit": 1})
    assert out.startswith(b"id,site_id"), out[:40]


# ── column round-trip (temp db) ──────────────────────────────────────

def _fresh_db():
    d = tempfile.mkdtemp()
    os.environ["BD_HOME"] = d
    return d


def test_add_schedule_persists_source_saved_search():
    _fresh_db()
    rid = _se.add_schedule(label="linked", format="json", destination="/tmp",
                           cadence_hours=24, source_saved_search=7)
    assert rid is not None
    rows = _se.list_schedules()
    got = [r for r in rows if r["id"] == rid]
    assert got and got[0].get("source_saved_search") == 7, got


def test_add_schedule_source_defaults_null():
    _fresh_db()
    rid = _se.add_schedule(label="plain", format="csv", destination="/tmp",
                           cadence_hours=24)
    rows = _se.list_schedules()
    got = [r for r in rows if r["id"] == rid][0]
    assert not got.get("source_saved_search"), got


# ── criteria_for + resolver (monkeypatched FTS seam) ─────────────────

def test_criteria_for_returns_search_fields(monkeypatch):
    _fresh_db()
    sid = _ss.add(name="cap3-crit", query="beach", site_id="demo", status="done")
    crit = _ss.criteria_for(sid)
    assert crit == {"query": "beach", "site_id": "demo", "status": "done"}, crit


def test_resolve_source_rows_uses_search_criteria_via_fts(monkeypatch):
    _fresh_db()
    sid = _ss.add(name="cap3-fts", query="sunset", site_id="s1", status="done")
    seen = {}

    def fake_fts(query, *, site_id=None, status=None, limit=500):
        seen.update(query=query, site_id=site_id, status=status)
        return [{"id": 99, "site_id": site_id, "url": "x", "status": status}]

    import bulk_downloader.db as _db_mod
    monkeypatch.setattr(_db_mod, "db_search_fts", fake_fts)
    rows = _se._resolve_source_rows(sid)
    assert rows and rows[0]["id"] == 99
    assert seen == {"query": "sunset", "site_id": "s1", "status": "done"}, seen


def test_resolve_source_rows_none_when_search_missing():
    _fresh_db()
    # a source id that does not exist -> None (caller falls back to filter_json)
    assert _se._resolve_source_rows(123456) is None
    assert _se._resolve_source_rows(None) is None
    assert _se._resolve_source_rows(0) is None
