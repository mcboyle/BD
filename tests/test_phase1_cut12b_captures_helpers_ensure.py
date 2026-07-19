"""Phase 1 Cut 1.2 corrective (v3.66.615): the db capture-index helpers must
self-ensure their table.

Regression caught by the on-stash gate at 614: test_analyzer_endpoints.py::
test_captures_list_ok failed (500, not 200). Root cause: Cut 1.2 repointed
GET /api/analyzer/captures onto db_captures_all(), but the capture helpers query
`captures` WITHOUT ensuring it exists. A bare app.test_client() with no db_init in
the current cwd/DB -> `no such table: captures` -> the route's try/except -> 500.
The OLD route was a pure FS walk (no DB), so the bare-client test passed.

Fix: db_captures_all / db_captures_upsert / db_captures_prune_missing ensure the
table first (CREATE TABLE IF NOT EXISTS -- the retention._ensure_tables pattern),
so any caller is robust whether or not db_init ran in this cwd.

RED on 614: db_captures_all against a fresh cwd with no db_init raises; the
analyzer route 500s.
"""
import json
import os
import tempfile


def test_captures_all_works_without_db_init():
    """db_captures_all must not raise when db_init was never called in this cwd --
    it self-ensures the table and returns []."""
    d = tempfile.mkdtemp()
    os.chdir(d)
    from bulk_downloader import db
    # NOTE: deliberately NO db.db_init() here.
    rows = db.db_captures_all()  # must not raise "no such table: captures"
    assert rows == [], f"expected [] on a fresh DB, got {rows!r}"


def test_captures_upsert_works_without_db_init():
    """Upsert must also self-ensure the table (a capture write may be the first
    db touch in a cwd)."""
    d = tempfile.mkdtemp()
    os.chdir(d)
    from bulk_downloader import db
    n = db.db_captures_upsert([{
        "rel_path": "captures/h/c.wacz", "name": "c.wacz", "dir": "captures",
        "host": "h", "captured_at": 1.0, "size": 1, "kind": "wacz", "redacted": False,
    }])
    assert n == 1
    assert len(db.db_captures_all()) == 1


def test_prune_missing_works_without_db_init():
    d = tempfile.mkdtemp()
    os.chdir(d)
    from bulk_downloader import db
    removed = db.db_captures_prune_missing(set())  # must not raise
    assert removed == 0


def test_analyzer_captures_route_200_without_db_init():
    """The regression itself: GET /api/analyzer/captures must return 200 from a
    bare test client (no db_init), mirroring test_analyzer_endpoints.test_captures_list_ok."""
    d = tempfile.mkdtemp()
    os.chdir(d)
    from bulk_downloader import app as app_mod
    r = app_mod.app.test_client().get("/api/analyzer/captures")
    assert r.status_code == 200, r.get_data(as_text=True)
    b = json.loads(r.data)
    assert b["ok"] is True
    assert isinstance(b["captures"], list)
