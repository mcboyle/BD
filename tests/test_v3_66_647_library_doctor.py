"""v3.66.647 -- S2.3 (part): library-doctor gets duplicate + size-drift detection.

audit() already reports orphans + missing_from_disk. This extends it (same
/api/library/audit route -- no new route) with two cheap, advisory signals:

  * list_duplicate_candidates(download_dir): video files sharing an exact byte
    size -- likely duplicate copies (stat-only; a size collision is a candidate,
    not a confirmed dupe). Reports reclaimable space.
  * list_size_drift(download_dir): history rows (status=done) whose recorded
    file_size differs from the file's actual on-disk size -- a truncated/altered
    download. (history.file_size vs os.path.getsize.)

Sandbox-safe: temp download_dir, isolated temp DB via db.DB_PATH, zero-arg tests.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import bulk_downloader.db as db
from bulk_downloader import library_final as lf


def _vid(dirpath, name, size):
    p = Path(dirpath) / name
    p.write_bytes(b"\0" * size)
    return str(p)


def test_duplicate_candidates_groups_same_size():
    d = tempfile.mkdtemp(prefix="dupe_")
    _vid(d, "a.mp4", 1000)
    _vid(d, "b.mp4", 1000)          # same size as a -> duplicate candidate
    _vid(d, "c.mp4", 2048)          # unique size -> not flagged
    groups = lf.list_duplicate_candidates(d)
    sizes = {g["size_bytes"]: g for g in groups}
    assert 1000 in sizes, f"the same-size pair must be a candidate group; got {groups}"
    assert sizes[1000]["count"] == 2
    assert 2048 not in sizes, "a unique-size file must not be flagged"


def test_duplicate_candidates_ignores_nonvideo_and_empty():
    d = tempfile.mkdtemp(prefix="dupe2_")
    (Path(d) / "x.txt").write_bytes(b"\0" * 1000)
    (Path(d) / "y.txt").write_bytes(b"\0" * 1000)   # non-video -> ignored
    _vid(d, "z.mp4", 0)                               # empty -> ignored
    assert lf.list_duplicate_candidates(d) == []


def test_size_drift_flags_truncated_file():
    d = tempfile.mkdtemp(prefix="drift_")
    saved_path = db.DB_PATH
    dbf = os.path.join(tempfile.mkdtemp(prefix="drift_db_"), "queue.db")
    db.DB_PATH = dbf
    db.db_init()
    try:
        vid = _vid(d, "movie.mp4", 500)          # actual on-disk = 500 bytes
        # record it in history as 1000 bytes -> a 500-byte truncation drift
        db.db_log(site_id="s", site_name="S", url="http://x/v",
                  status="done", filename=vid, file_size=1000)
        drift = lf.list_size_drift(d)
        hit = [x for x in drift if x["filename"] == vid]
        assert hit, f"a size mismatch must be flagged; got {drift}"
        assert hit[0]["recorded_bytes"] == 1000
        assert hit[0]["disk_bytes"] == 500
        assert hit[0]["delta_bytes"] == -500
    finally:
        db.DB_PATH = saved_path


def test_size_drift_ignores_matching_file():
    d = tempfile.mkdtemp(prefix="drift_ok_")
    saved_path = db.DB_PATH
    dbf = os.path.join(tempfile.mkdtemp(prefix="drift_ok_db_"), "queue.db")
    db.DB_PATH = dbf
    db.db_init()
    try:
        vid = _vid(d, "ok.mp4", 800)
        db.db_log(site_id="s", site_name="S", url="http://x/ok",
                  status="done", filename=vid, file_size=800)   # matches disk
        drift = lf.list_size_drift(d)
        assert [x for x in drift if x["filename"] == vid] == [], drift
    finally:
        db.DB_PATH = saved_path


def test_audit_surfaces_dupes_and_drift_keys():
    d = tempfile.mkdtemp(prefix="audit_")
    _vid(d, "a.mp4", 1234)
    _vid(d, "b.mp4", 1234)
    saved_path = db.DB_PATH
    dbf = os.path.join(tempfile.mkdtemp(prefix="audit_db_"), "queue.db")
    db.DB_PATH = dbf
    db.db_init()
    try:
        rep = lf.audit(download_dir=d)
        for k in ("duplicate_groups", "duplicate_reclaimable_gb",
                  "size_drift", "sample_duplicates", "sample_size_drift"):
            assert k in rep, f"audit() must report {k}; got {sorted(rep)}"
        assert rep["duplicate_groups"] >= 1
    finally:
        db.DB_PATH = saved_path
