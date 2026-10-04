"""O1826 C05 -- list_orphans called the whole library an orphan when it could not read history.

M097. ``list_orphans`` swallowed a failed history query and went on with
``known`` empty, so every video on disk was reported as having no history row.
The sibling scans (``missing_from_disk_scan``, ``size_drift_scan``) already set
``query_failed`` and return nothing; the orphan scan now does the same, through
``list_orphans_scan``, and ``list_orphans`` is its projection.

M098. One ``audit()`` call walked the library four times: an os.walk in
``list_orphans``, another in ``list_duplicate_candidates``, and a
``_basename_index`` rglob in each of the two DB scans. It now walks once and
hands the file list / basename index to all four.

The equivalence test composes audit()'s result from the public functions
called WITHOUT the shared walk -- base's audit() body -- so the single walk
cannot change what audit() reports.
"""
from __future__ import annotations

import importlib
import os
import tempfile
from pathlib import Path

from bulk_downloader import library_final as lf

BD_GATE_SCOPE = "module"


def _db():
    return importlib.import_module("bulk_downloader.db")


def _vid(dirpath, name, size):
    p = Path(dirpath) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\0" * size)
    return str(p)


def _fresh_db():
    db = _db()
    saved = db.DB_PATH
    db.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="o1826c05_db_"), "queue.db")
    db.db_init()
    return saved


def _boom(*_a, **_k):
    raise RuntimeError("o1826c05: history unreadable")


def test_failed_history_query_reports_no_orphans(monkeypatch):
    d = tempfile.mkdtemp(prefix="o1826c05_lib_")
    _vid(d, "a.mp4", 10)
    _vid(d, "sub/b.mkv", 20)
    with monkeypatch.context() as m:
        m.setattr(_db(), "db_conn", _boom)
        got = lf.list_orphans(d)
    assert got == [], (
        "O1826C05-M097: history query raised, yet list_orphans reported "
        "%d of 2 library videos as orphans: %r" % (len(got), got))


def test_failed_history_query_sets_query_failed(monkeypatch):
    d = tempfile.mkdtemp(prefix="o1826c05_lib_")
    _vid(d, "a.mp4", 10)
    with monkeypatch.context() as m:
        m.setattr(_db(), "db_conn", _boom)
        scan = lf.list_orphans_scan(d)
        report = lf.audit(download_dir=d)
    assert scan == {"rows": [], "query_failed": True}
    assert report["orphans"] == 0 and report["sample_orphans"] == []


def test_readable_history_still_reports_a_real_orphan():
    d = tempfile.mkdtemp(prefix="o1826c05_lib_")
    known = _vid(d, "known.mp4", 10)
    stray = _vid(d, "sub/stray.mp4", 20)
    db = _db()
    saved = _fresh_db()
    try:
        db.db_log(site_id="s", site_name="S", url="http://x/k",
                  status="done", filename=known, file_size=10)
        assert lf.list_orphans(d) == [{"path": stray, "size_bytes": 20}]
    finally:
        db.DB_PATH = saved


def test_readable_history_scan_is_not_failed():
    d = tempfile.mkdtemp(prefix="o1826c05_lib_")
    stray = _vid(d, "stray.mp4", 20)
    db = _db()
    saved = _fresh_db()
    try:
        scan = lf.list_orphans_scan(d)
    finally:
        db.DB_PATH = saved
    assert scan == {"rows": [{"path": stray, "size_bytes": 20}],
                    "query_failed": False}


def _library(d):
    """Nested tree covering every audit() count: orphan, missing, dupes,
    drift, an ambiguous basename and a non-video file."""
    db = _db()
    ok = _vid(d, "ok.mp4", 30)
    drift = _vid(d, "a/drift.mkv", 40)
    _vid(d, "a/b/orphan.mp4", 70)
    _vid(d, "c/orphan2.webm", 70)
    _vid(d, "a/same.mp4", 5)
    _vid(d, "c/same.mp4", 6)
    _vid(d, "a/b/notes.txt", 70)
    db.db_log(site_id="s", site_name="S", url="http://x/1",
              status="done", filename=ok, file_size=30)
    db.db_log(site_id="s", site_name="S", url="http://x/2",
              status="done", filename="drift.mkv", file_size=99)
    db.db_log(site_id="s", site_name="S", url="http://x/3",
              status="done", filename="gone.mp4", file_size=11)
    db.db_log(site_id="s", site_name="S", url="http://x/4",
              status="done", filename="same.mp4", file_size=5)
    return drift


def _composed_audit(d, limit=lf._AUDIT_ROW_LIMIT):
    o = lf.list_orphans(d)
    mscan = lf.missing_from_disk_scan(download_dir=d, limit=limit)
    dupes = lf.list_duplicate_candidates(d)
    dscan = lf.size_drift_scan(d, limit=limit)
    total_orphan = sum(x["size_bytes"] for x in o)
    reclaimable = sum(g["size_bytes"] * (g["count"] - 1) for g in dupes)
    return {
        "orphans": len(o),
        "missing": len(mscan["rows"]),
        "duplicate_groups": len(dupes),
        "duplicate_reclaimable_gb": round(reclaimable / (1024**3), 2),
        "size_drift": len(dscan["rows"]),
        "orphan_size_gb": round(total_orphan / (1024**3), 2),
        "missing_saturated": bool(mscan["limit_hit"]),
        "size_drift_saturated": bool(dscan["limit_hit"]),
        "audit_row_limit": int(limit),
        "sample_orphans": o[:10],
        "sample_missing": mscan["rows"][:10],
        "sample_duplicates": dupes[:10],
        "sample_size_drift": dscan["rows"][:10],
    }


def test_audit_equals_the_unshared_scans():
    d = tempfile.mkdtemp(prefix="o1826c05_lib_")
    db = _db()
    saved = _fresh_db()
    try:
        _library(d)
        got = lf.audit(download_dir=d)
        want = _composed_audit(d)
    finally:
        db.DB_PATH = saved
    assert got == want
    # the fixture exercises every count, so equality is not 0 == 0
    assert (got["orphans"], got["missing"], got["duplicate_groups"],
            got["size_drift"]) == (2, 1, 1, 1), got


def _symlink_library(d):
    """_library plus the links the shared walk must treat as base did: a
    symlinked file (an orphan, and a 30-byte dupe of ok.mp4), a broken
    symlink named by a history row (missing), and a symlinked dir pointing
    outside the library (never descended)."""
    _library(d)
    outside = tempfile.mkdtemp(prefix="o1826c05_out_")
    _vid(outside, "far.webm", 70)
    os.symlink(os.path.join(d, "ok.mp4"), os.path.join(d, "a", "link.mp4"))
    os.symlink(os.path.join(d, "nowhere.mp4"), os.path.join(d, "broken.mp4"))
    os.symlink(outside, os.path.join(d, "c", "outlink"))
    _db().db_log(site_id="s", site_name="S", url="http://x/5",
                 status="done", filename="broken.mp4", file_size=7)


def test_index_files_equals_basename_index_on_symlinks():
    d = tempfile.mkdtemp(prefix="o1826c05_lib_")
    db = _db()
    saved = _fresh_db()
    try:
        _symlink_library(d)
    finally:
        db.DB_PATH = saved
    got = {k: sorted(map(str, v))
           for k, v in lf._index_files(d, lf._walk_files(d)).items()}
    want = {k: sorted(map(str, v)) for k, v in lf._basename_index(d).items()}
    assert got == want, (
        "O1826C05-SYMLINK-INDEX: the shared walk's index differs from "
        "_basename_index: only-walk=%r only-rglob=%r"
        % (sorted(set(got) - set(want)), sorted(set(want) - set(got))))
    assert "link.mp4" in got and "broken.mp4" not in got
    assert "far.webm" not in got


def test_audit_counts_on_symlinks():
    d = tempfile.mkdtemp(prefix="o1826c05_lib_")
    db = _db()
    saved = _fresh_db()
    try:
        _symlink_library(d)
        got = lf.audit(download_dir=d)
    finally:
        db.DB_PATH = saved
    counts = (got["orphans"], got["missing"], got["duplicate_groups"],
              got["size_drift"])
    dupes = sorted((g["size_bytes"], g["count"])
                   for g in got["sample_duplicates"])
    # orphans: orphan.mp4, orphan2.webm, a/link.mp4; missing: gone, broken;
    # dupes: {70: orphan, orphan2}, {30: ok, link}; drift: drift.mkv
    assert (counts, dupes) == ((3, 2, 2, 1), [(30, 2), (70, 2)]), (
        "O1826C05-SYMLINK-COUNTS: audit() on the symlink library gave "
        "(orphans, missing, duplicate_groups, size_drift)=%r dupes=%r"
        % (counts, dupes))


def test_audit_walks_the_library_once(monkeypatch):
    d = tempfile.mkdtemp(prefix="o1826c05_lib_")
    db = _db()
    saved = _fresh_db()
    calls = []
    real_walk, real_rglob = os.walk, Path.rglob

    def walk(*a, **k):
        calls.append("os.walk")
        return real_walk(*a, **k)

    def rglob(self, *a, **k):
        calls.append("rglob")
        return real_rglob(self, *a, **k)

    try:
        _library(d)
        with monkeypatch.context() as m:
            m.setattr(os, "walk", walk)
            m.setattr(Path, "rglob", rglob)
            lf.audit(download_dir=d)
    finally:
        db.DB_PATH = saved
    assert len(calls) == 1, (
        "O1826C05-M098: one audit() walked the library %d times: %r"
        % (len(calls), calls))
