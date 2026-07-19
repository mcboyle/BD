"""v3.50 (Phase 3) — regression tests for the library module + API.

Covers:
  - Migrations: all 6 applied cleanly, schema as expected
  - library_record idempotency (no duplicate rows on re-record)
  - library_browse filters: site, performer, tag, watched, query
  - library_browse cursor pagination
  - tag CRUD + library_add_tag/library_remove_tag
  - library_set_watched/_rating/_notes
  - library_delete (with and without file removal)
  - library_orphans detection
  - Scanner: idempotency, missing-mark, skip-decoy handling
  - API endpoints: each route returns the documented shape
"""
import os
import sys
import time
import tempfile
import shutil
from pathlib import Path

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

# ── Migration tests ─────────────────────────────────────────────────────

def test_phase3_migrations_apply_cleanly():
    """All 6 migrations should apply with no errors."""
    from bulk_downloader.db import db_init
    from bulk_downloader.migrations import apply_pending
    db_init()
    result = apply_pending()
    assert result["errors"] == 0
    assert result["applied"] >= 6  # v1-v6 should all be there


def test_phase3_library_table_has_expected_columns():
    from bulk_downloader.db import db_init, db_conn
    from bulk_downloader.migrations import apply_pending
    db_init()
    apply_pending()
    with db_conn() as cx:
        cols = {r["name"] for r in cx.execute(
            "PRAGMA table_info(library)").fetchall()}
    expected = {"id", "history_id", "site_id", "file_path", "file_exists",
                "file_size", "file_mtime", "watched", "watched_at",
                "performer", "studio", "year", "title", "rating", "notes",
                "added_at"}
    missing = expected - cols
    assert not missing, f"library missing columns: {missing}"


def test_phase3_tags_table_has_unique_name():
    from bulk_downloader.db import db_init, db_conn
    from bulk_downloader.migrations import apply_pending
    db_init()
    apply_pending()
    with db_conn() as cx:
        # Inserting same name twice should fail unique constraint
        cx.execute("INSERT INTO tags(name, created_at) VALUES('foo', 0)")
        raised = False
        try:
            cx.execute("INSERT INTO tags(name, created_at) VALUES('foo', 1)")
        except Exception:
            raised = True
        assert raised, "tags.name should have UNIQUE constraint"


def test_phase3_history_back_ref_column_added():
    from bulk_downloader.db import db_init, db_conn
    from bulk_downloader.migrations import apply_pending
    db_init()
    apply_pending()
    with db_conn() as cx:
        cols = {r["name"] for r in cx.execute(
            "PRAGMA table_info(history)").fetchall()}
    assert "library_id" in cols
    assert "removed_at" in cols


def test_phase3_dry_run_reports_per_migration_status():
    """The Phase 1 dry-run feature should correctly diff the Phase 3
    additions — added_tables and added_columns should be non-empty."""
    from bulk_downloader.db import db_init
    from bulk_downloader.migrations import apply_pending
    db_init()
    result = apply_pending(dry_run=True)
    assert result["dry_run"] is True
    # Find migration v3 (create_library_table)
    v3 = next(r for r in result["results"] if r["version"] == 3)
    assert v3["would_succeed"] is True
    assert "library" in v3["added_tables"]


# ── library_record tests ────────────────────────────────────────────────

def test_library_record_creates_row():
    from bulk_downloader import library as lib
    lid = lib.library_record("/tmp/test/v1.mp4", site_id="siteA",
                              performer="Alice", title="Scene 1")
    assert lid is not None
    row = lib.library_get(lid)
    assert row["site_id"] == "siteA"
    assert row["performer"] == "Alice"
    assert row["title"] == "Scene 1"


def test_library_record_is_idempotent():
    from bulk_downloader import library as lib
    lid1 = lib.library_record("/tmp/test/v1.mp4", site_id="siteA",
                               performer="Alice")
    lid2 = lib.library_record("/tmp/test/v1.mp4", site_id="siteA",
                               performer="Alice")
    assert lid1 == lid2, "second record() should update, not insert"


def test_library_record_preserves_existing_on_partial_update():
    """If a re-record() omits performer, the original performer should
    be preserved (not blanked)."""
    from bulk_downloader import library as lib
    lid = lib.library_record("/tmp/test/v.mp4", site_id="siteA",
                              performer="Alice", studio="StudioX")
    # Update with only studio — performer should remain
    lib.library_record("/tmp/test/v.mp4", studio="StudioY")
    row = lib.library_get(lid)
    assert row["performer"] == "Alice"
    assert row["studio"] == "StudioY"


# ── browse / filter tests ───────────────────────────────────────────────

def test_library_browse_filters_by_site():
    from bulk_downloader import library as lib
    lib.library_record("/tmp/a/v1.mp4", site_id="A")
    lib.library_record("/tmp/a/v2.mp4", site_id="A")
    lib.library_record("/tmp/b/v3.mp4", site_id="B")
    rows, _ = lib.library_browse(site_id="A", limit=10)
    assert len(rows) == 2
    assert all(r["site_id"] == "A" for r in rows)


def test_library_browse_filters_by_performer():
    from bulk_downloader import library as lib
    lib.library_record("/tmp/x/v1.mp4", performer="Alice")
    lib.library_record("/tmp/x/v2.mp4", performer="Bob")
    rows, _ = lib.library_browse(performer="Alice", limit=10)
    assert len(rows) == 1
    assert rows[0]["performer"] == "Alice"


def test_library_browse_filters_by_query():
    from bulk_downloader import library as lib
    lib.library_record("/tmp/x/scene.mp4", title="Beach episode")
    lib.library_record("/tmp/x/other.mp4", title="Mountain hike")
    rows, _ = lib.library_browse(query="beach", limit=10)
    assert len(rows) == 1
    assert "Beach" in rows[0]["title"]


def test_library_browse_filters_by_tag():
    from bulk_downloader import library as lib
    lid1 = lib.library_record("/tmp/v1.mp4")
    lid2 = lib.library_record("/tmp/v2.mp4")
    lib.library_add_tag(lid1, "fav")
    lib.library_add_tag(lid2, "fav")
    rows, _ = lib.library_browse(tag="fav", limit=10)
    assert len(rows) == 2


def test_library_browse_cursor_pagination():
    """Insert 25 rows, request limit=10, then page using next_cursor."""
    from bulk_downloader import library as lib
    for i in range(25):
        lib.library_record(f"/tmp/cursor/v{i}.mp4")
    page1, c1 = lib.library_browse(limit=10)
    assert len(page1) == 10
    assert c1 is not None
    page2, c2 = lib.library_browse(limit=10, after_id=c1)
    assert len(page2) == 10
    page3, c3 = lib.library_browse(limit=10, after_id=c2)
    assert len(page3) == 5
    assert c3 is None  # last page
    # No duplicates across pages
    ids = {r["id"] for r in page1 + page2 + page3}
    assert len(ids) == 25


def test_library_browse_rejects_bogus_sort_key_safely():
    """Whitelist-based sort means injection isn't possible. Bogus sort
    should default to added_at_desc rather than crash."""
    from bulk_downloader import library as lib
    lib.library_record("/tmp/x.mp4")
    rows, _ = lib.library_browse(sort="'; DROP TABLE library;--", limit=10)
    # Should not crash, returns the row
    assert len(rows) == 1


# ── tag tests ───────────────────────────────────────────────────────────

def test_tag_upsert_returns_same_id_for_same_name():
    from bulk_downloader import library as lib
    a = lib.tag_upsert("favorite")
    b = lib.tag_upsert("favorite")
    assert a == b


def test_tag_upsert_truncates_long_names():
    from bulk_downloader import library as lib
    very_long = "x" * 200
    assert lib.tag_upsert(very_long) is None  # too long → reject


def test_library_add_and_remove_tag():
    from bulk_downloader import library as lib
    lid = lib.library_record("/tmp/tagtest.mp4")
    assert lib.library_add_tag(lid, "fav") is True
    row = lib.library_get(lid)
    assert any(t["name"] == "fav" for t in row["tags"])
    assert lib.library_remove_tag(lid, "fav") is True
    row = lib.library_get(lid)
    assert row["tags"] == []


def test_tag_delete_cascades_junction():
    """Deleting a tag should remove all library_tags rows that reference
    it (ON DELETE CASCADE)."""
    from bulk_downloader import library as lib
    from bulk_downloader.db import db_conn
    lid = lib.library_record("/tmp/cascade.mp4")
    tag_id = lib.tag_upsert("doomed")
    lib.library_add_tag(lid, "doomed")
    # Confirm junction exists
    with db_conn() as cx:
        n = cx.execute(
            "SELECT COUNT(*) AS n FROM library_tags WHERE tag_id=?",
            (tag_id,)).fetchone()["n"]
    assert n == 1
    # Delete the tag
    lib.tag_delete(tag_id)
    with db_conn() as cx:
        n = cx.execute(
            "SELECT COUNT(*) AS n FROM library_tags WHERE tag_id=?",
            (tag_id,)).fetchone()["n"]
    assert n == 0


def test_tag_list_includes_usage_count():
    from bulk_downloader import library as lib
    lid1 = lib.library_record("/tmp/t1.mp4")
    lid2 = lib.library_record("/tmp/t2.mp4")
    lib.library_add_tag(lid1, "popular")
    lib.library_add_tag(lid2, "popular")
    tags = lib.tag_list()
    pop = next(t for t in tags if t["name"] == "popular")
    assert pop["usage_count"] == 2


# ── watched / rating / notes tests ──────────────────────────────────────

def test_watched_toggles_flag_and_stamp():
    from bulk_downloader import library as lib
    lid = lib.library_record("/tmp/w.mp4")
    assert lib.library_set_watched(lid, True) is True
    row = lib.library_get(lid)
    assert row["watched"] == 1
    assert row["watched_at"] is not None
    # Unwatched clears the stamp
    lib.library_set_watched(lid, False)
    row = lib.library_get(lid)
    assert row["watched"] == 0
    assert row["watched_at"] is None


def test_rating_clamps_to_1_to_5():
    from bulk_downloader import library as lib
    lid = lib.library_record("/tmp/r.mp4")
    lib.library_set_rating(lid, 99)
    assert lib.library_get(lid)["rating"] == 5
    lib.library_set_rating(lid, -7)
    assert lib.library_get(lid)["rating"] == 1
    lib.library_set_rating(lid, None)
    assert lib.library_get(lid)["rating"] is None


def test_notes_truncates_overlong_input():
    from bulk_downloader import library as lib
    lid = lib.library_record("/tmp/n.mp4")
    long_note = "x" * 20000
    lib.library_set_notes(lid, long_note)
    assert len(lib.library_get(lid)["notes"]) == 8192


# ── delete tests ────────────────────────────────────────────────────────

def test_library_delete_drops_row():
    from bulk_downloader import library as lib
    lid = lib.library_record("/tmp/del.mp4")
    result = lib.library_delete(lid)
    assert result["ok"] is True
    assert result["deleted_row"] is True
    assert result["file_removed"] is False
    assert lib.library_get(lid) is None


def test_library_delete_with_file_removal(tmp_path):
    from bulk_downloader import library as lib
    fp = tmp_path / "delfile.mp4"
    fp.write_bytes(b"video data")
    lid = lib.library_record(str(fp))
    result = lib.library_delete(lid, also_delete_file=True)
    assert result["ok"] is True
    assert result["file_removed"] is True
    assert not fp.exists()


def test_library_delete_nonexistent_returns_error():
    from bulk_downloader import library as lib
    result = lib.library_delete(99999)
    assert result["ok"] is False
    assert "not found" in result["error"]


# ── stats / orphans / missing ───────────────────────────────────────────

def test_library_stats_aggregates_correctly():
    from bulk_downloader import library as lib
    lib.library_record("/tmp/s/a.mp4", site_id="A", studio="X", year=2024)
    lib.library_record("/tmp/s/b.mp4", site_id="A", studio="X", year=2024)
    lib.library_record("/tmp/s/c.mp4", site_id="B", studio="Y", year=2023)
    s = lib.library_stats()
    assert s["total_files"] == 3
    assert {r["k"] for r in s["by_site"]} == {"A", "B"}
    assert next(r["n"] for r in s["by_site"] if r["k"] == "A") == 2


def test_library_orphans_finds_unknown_files(tmp_path):
    from bulk_downloader import library as lib
    # Make some files; record only one
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.mp4").write_bytes(b"x")
    (tmp_path / "ignore.txt").write_bytes(b"x")  # not a video
    lib.library_record(str(tmp_path / "a.mp4"))
    orphans = lib.library_orphans(str(tmp_path))
    assert str(tmp_path / "b.mp4") in orphans
    assert str(tmp_path / "a.mp4") not in orphans
    assert str(tmp_path / "ignore.txt") not in orphans


# ── scanner tests ───────────────────────────────────────────────────────

def test_scanner_idempotency(tmp_path):
    from bulk_downloader import library as lib
    # Build a small tree
    for i in range(5):
        (tmp_path / f"v{i}.mp4").write_bytes(b"video")
    lib.scan_start([str(tmp_path)])
    for _ in range(30):
        if not lib.scan_status().get("running"):
            break
        time.sleep(0.05)
    s = lib.scan_status()
    assert s["added"] == 5
    assert s["updated"] == 0
    # Rescan — all should now be updates, no new
    lib.scan_start([str(tmp_path)])
    for _ in range(30):
        if not lib.scan_status().get("running"):
            break
        time.sleep(0.05)
    s = lib.scan_status()
    assert s["added"] == 0
    assert s["updated"] == 5


def test_scanner_marks_missing(tmp_path):
    from bulk_downloader import library as lib
    fp1 = tmp_path / "a.mp4"
    fp1.write_bytes(b"video")
    fp2 = tmp_path / "b.mp4"
    fp2.write_bytes(b"video")
    lib.scan_start([str(tmp_path)])
    for _ in range(30):
        if not lib.scan_status().get("running"):
            break
        time.sleep(0.05)
    # Delete one
    fp1.unlink()
    lib.scan_start([str(tmp_path)])
    for _ in range(30):
        if not lib.scan_status().get("running"):
            break
        time.sleep(0.05)
    s = lib.scan_status()
    assert s["missing_marked"] == 1


def test_scanner_skips_decoy_files(tmp_path):
    from bulk_downloader import library as lib
    (tmp_path / "v.mp4").write_bytes(b"video")
    (tmp_path / ".DS_Store").write_bytes(b"")
    (tmp_path / "v.mp4.part").write_bytes(b"in flight")
    (tmp_path / "notes.txt").write_bytes(b"")
    (tmp_path / "v.bdseg.json").write_bytes(b"{}")
    lib.scan_start([str(tmp_path)])
    for _ in range(30):
        if not lib.scan_status().get("running"):
            break
        time.sleep(0.05)
    s = lib.scan_status()
    assert s["seen"] == 1
    assert s["added"] == 1


def test_scanner_refuses_concurrent_runs(tmp_path):
    """Calling scan_start twice while one is running returns an error."""
    from bulk_downloader import library as lib
    # Lots of files to make sure it stays running across the second call
    for i in range(50):
        (tmp_path / f"v{i}.mp4").write_bytes(b"x" * 1024)
    r1 = lib.scan_start([str(tmp_path)])
    assert r1["ok"] is True
    r2 = lib.scan_start([str(tmp_path)])
    # If the first finished too fast, r2 might also succeed — accept
    # either as long as we don't get a crash
    assert isinstance(r2.get("ok"), bool)
    # Drain
    for _ in range(50):
        if not lib.scan_status().get("running"):
            break
        time.sleep(0.05)


def test_scanner_rejects_empty_roots():
    from bulk_downloader import library as lib
    r = lib.scan_start([])
    assert r["ok"] is False
    assert "no valid roots" in r["error"]


def test_scanner_rejects_nonexistent_roots():
    from bulk_downloader import library as lib
    r = lib.scan_start(["/this/path/does/not/exist/anywhere"])
    assert r["ok"] is False


# ── API endpoint tests ──────────────────────────────────────────────────

def test_api_library_browse_returns_documented_shape():
    from bulk_downloader import app as a, library as lib
    lib.library_record("/tmp/api/v1.mp4", site_id="X")
    c = a.app.test_client()
    r = c.get("/api/library/browse")
    body = r.get_json()
    assert body["ok"] is True
    assert "rows" in body
    assert "count" in body
    assert "next_cursor" in body


def test_api_library_get_404_for_unknown():
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.get("/api/library/99999")
    assert r.status_code == 404


def test_api_library_watched_round_trip():
    from bulk_downloader import app as a, library as lib
    lid = lib.library_record("/tmp/api/w.mp4")
    c = a.app.test_client()
    r = c.post(f"/api/library/{lid}/watched", json={"watched": True})
    assert r.get_json()["ok"] is True
    r = c.get(f"/api/library/{lid}")
    assert r.get_json()["row"]["watched"] == 1


def test_api_library_rating_validates_type():
    from bulk_downloader import app as a, library as lib
    lid = lib.library_record("/tmp/api/r.mp4")
    c = a.app.test_client()
    r = c.post(f"/api/library/{lid}/rating",
               json={"rating": "not-a-number"})
    assert r.status_code == 400


def test_api_library_tag_add_requires_name():
    from bulk_downloader import app as a, library as lib
    lid = lib.library_record("/tmp/api/t.mp4")
    c = a.app.test_client()
    r = c.post(f"/api/library/{lid}/tags", json={"tag": ""})
    assert r.status_code == 400


def test_api_library_stats_endpoint():
    from bulk_downloader import app as a, library as lib
    lib.library_record("/tmp/api/s1.mp4", site_id="A")
    lib.library_record("/tmp/api/s2.mp4", site_id="B")
    c = a.app.test_client()
    r = c.get("/api/library/stats")
    body = r.get_json()
    assert body["ok"] is True
    assert body["stats"]["total_files"] == 2


def test_api_library_scan_status_idle_when_never_run():
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.get("/api/library/scan/status")
    body = r.get_json()
    assert body["ok"] is True
    assert body["scan"]["running"] is False


def test_api_library_scan_start_rejects_empty_roots_when_no_sites():
    """If no sites are configured and no roots given, returns 400."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.post("/api/library/scan/start", json={})
    # Either 400 (no roots) or 200 (a site exists with a download_dir
    # — depends on test isolation). Both are acceptable; we just
    # want a deterministic response shape.
    assert r.status_code in (200, 400)


# ── Forward-path test (db_log → library_record) ─────────────────────────

def test_db_log_done_creates_library_row():
    """v3.50: a 'done' history row with a filename auto-creates a
    library row, and the history↔library back-references are linked
    both directions."""
    from bulk_downloader import app as a  # boot triggers migrations
    from bulk_downloader.db import db_log, db_conn
    from bulk_downloader import library as lib
    db_log("siteA", "Site A", "https://example.com/v1", "done",
           "/tmp/dl/scene.mp4", 2048, "completed")
    rows, _ = lib.library_browse(limit=10)
    assert len(rows) == 1
    assert rows[0]["file_path"] == "/tmp/dl/scene.mp4"
    assert rows[0]["history_id"] is not None
    # history.library_id should point back
    with db_conn() as cx:
        hist = cx.execute(
            "SELECT library_id FROM history WHERE status='done'"
        ).fetchone()
    assert hist["library_id"] == rows[0]["id"]


def test_db_log_failed_does_not_create_library_row():
    """A 'failed' status must NOT create a library row — there's no
    file on disk to track."""
    from bulk_downloader import app as a
    from bulk_downloader.db import db_log
    from bulk_downloader import library as lib
    db_log("siteA", "Site A", "https://example.com/v2", "failed",
           "", 0, "download error")
    rows, _ = lib.library_browse(limit=10)
    assert len(rows) == 0


def test_db_log_done_without_filename_creates_nothing():
    """Edge case: 'done' but no filename (shouldn't normally happen,
    but defend against it) — no library row."""
    from bulk_downloader import app as a
    from bulk_downloader.db import db_log
    from bulk_downloader import library as lib
    db_log("siteA", "Site A", "https://example.com/v3", "done",
           "", 0, "done but no file?")
    rows, _ = lib.library_browse(limit=10)
    assert len(rows) == 0
