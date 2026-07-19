"""Phase 1 Cut 1.4 (v3.66.614): capture-retention engine over the db `captures`
index (Cut 1.1) — a NEW axis, distinct from the existing download/history
retention already in retention.py.

Charter (RETENTION_AND_TAKEDOWN_POLICY.md): captures are KEEP-FOREVER by default;
capture retention is strictly opt-in. Rules: TTL (age), size-cap, keep-N-per-host.
Reuses the module's safety machinery: dry_run=True default; the takedown floor
(minors / illegal content, §3) is the one non-negotiable deletion path,
independent of retention config.

Public API added:
  * find_capture_candidates(policy, *, now=None) -> list of candidate capture rows
  * apply_capture_retention(policy, *, dry_run=True, confirm_paths=None) -> result

RED: these functions don't exist on 613; the keep-forever default + keep-N logic
are not implemented.
"""
import os
import tempfile
import time


def _isolated_db_with_captures(rows):
    d = tempfile.mkdtemp()
    os.chdir(d)
    from bulk_downloader import db
    db.db_init()
    db.db_captures_upsert(rows)
    return db


def _cap(rel, host, captured_at, kind="wacz"):
    return {"rel_path": rel, "name": rel.rsplit("/", 1)[-1], "dir": "captures",
            "host": host, "captured_at": captured_at, "size": 1000,
            "kind": kind, "redacted": False}


def test_default_policy_keeps_everything_forever():
    """The keep-forever charter default: with NO retention policy configured,
    find_capture_candidates returns nothing — captures are never candidates."""
    from bulk_downloader import retention
    _isolated_db_with_captures([
        _cap("captures/a/c1.wacz", "a", 1.0),
        _cap("captures/a/c2.wacz", "a", 2.0),
    ])
    assert hasattr(retention, "find_capture_candidates"), \
        "retention.find_capture_candidates missing"
    # empty / all-zero policy = keep forever
    assert retention.find_capture_candidates({}) == []
    assert retention.find_capture_candidates(
        {"capture_ttl_days": 0, "capture_max_gb": 0, "capture_keep_n_per_host": 0}) == []


def test_keep_n_per_host_marks_only_the_overflow():
    """keep_n_per_host=K keeps the K newest captures per host; only the older
    overflow becomes a candidate. Hosts under the cap are untouched."""
    from bulk_downloader import retention
    _isolated_db_with_captures([
        _cap("captures/a/c1.wacz", "a", 1.0),
        _cap("captures/a/c2.wacz", "a", 2.0),
        _cap("captures/a/c3.wacz", "a", 3.0),
        _cap("captures/b/c1.wacz", "b", 5.0),  # host b under the cap -> safe
    ])
    cands = retention.find_capture_candidates({"capture_keep_n_per_host": 2})
    rels = {c["rel_path"] for c in cands}
    # host a: keep the 2 newest (c3=3.0, c2=2.0); c1 (oldest) is the only candidate
    assert rels == {"captures/a/c1.wacz"}, f"keep-N wrong: {rels}"


def test_ttl_marks_only_older_than_cutoff():
    """capture_ttl_days marks captures older than the cutoff; newer ones stay."""
    from bulk_downloader import retention
    now = time.time()
    _isolated_db_with_captures([
        _cap("captures/h/old.wacz", "h", now - 40 * 86400),   # 40 days old
        _cap("captures/h/new.wacz", "h", now - 1 * 86400),    # 1 day old
    ])
    cands = retention.find_capture_candidates({"capture_ttl_days": 30}, now=now)
    rels = {c["rel_path"] for c in cands}
    assert rels == {"captures/h/old.wacz"}, f"TTL wrong: {rels}"


def test_apply_is_dry_run_by_default_and_deletes_nothing():
    """apply_capture_retention defaults to dry_run=True: it reports candidates but
    removes nothing from the index or disk."""
    from bulk_downloader import retention, db
    root = tempfile.mkdtemp()
    os.chdir(root)
    db.db_init()
    # a real file on disk so a non-dry run WOULD have something to delete
    capdir = os.path.join(root, "captures", "h")
    os.makedirs(capdir, exist_ok=True)
    fp = os.path.join(capdir, "old.wacz")
    open(fp, "w").write("x")
    db.db_captures_upsert([_cap("captures/h/old.wacz", "h", 1.0)])
    res = retention.apply_capture_retention({"capture_keep_n_per_host": 0,
                                             "capture_ttl_days": 1},
                                            dry_run=True)
    assert res.get("dry_run") is True
    assert os.path.exists(fp), "dry_run must not delete the file"
    assert len(db.db_captures_all()) == 1, "dry_run must not drop the index row"


def test_apply_confirm_paths_binds_deletion_to_preview(monkeypatch):
    """Preview-verbatim safety (mirrors apply_retention): with confirm_paths, only
    the INTERSECTION of current candidates and confirm_paths is deleted — never a
    capture the operator did not see in the preview."""
    from bulk_downloader import retention, db
    import bulk_downloader.dom_analyzer as da
    root = tempfile.mkdtemp()
    os.chdir(root)
    # the FS-authoritative resolver deletes relative to _project_root(); point it
    # at the isolated root so it resolves the seeded capture files.
    from pathlib import Path as _P
    monkeypatch.setattr(da, "_project_root", lambda: _P(root))
    db.db_init()
    capdir = os.path.join(root, "captures", "h")
    os.makedirs(capdir, exist_ok=True)
    for n, ts in (("c1.wacz", 1.0), ("c2.wacz", 2.0), ("c3.wacz", 3.0)):
        open(os.path.join(capdir, n), "w").write("x")
    db.db_captures_upsert([
        _cap("captures/h/c1.wacz", "h", 1.0),
        _cap("captures/h/c2.wacz", "h", 2.0),
        _cap("captures/h/c3.wacz", "h", 3.0),
    ])
    # policy would make c1+c2 candidates (keep 1 newest); confirm only c1
    res = retention.apply_capture_retention(
        {"capture_keep_n_per_host": 1}, dry_run=False,
        confirm_paths={"captures/h/c1.wacz"})
    remaining = {r["rel_path"] for r in db.db_captures_all()}
    assert "captures/h/c1.wacz" not in remaining, "confirmed candidate not deleted"
    assert "captures/h/c2.wacz" in remaining, \
        "a NON-confirmed candidate was deleted (preview-binding violated)"
    assert "captures/h/c3.wacz" in remaining, "a kept capture was deleted"
    assert not os.path.exists(os.path.join(capdir, "c1.wacz")), \
        "confirmed capture file not removed from disk"


def test_apply_deletes_index_row_and_file_for_confirmed(monkeypatch):
    """A real apply (dry_run=False) removes both the file and its index row for
    confirmed candidates, so the picker reflects the deletion."""
    from bulk_downloader import retention, db
    import bulk_downloader.dom_analyzer as da
    root = tempfile.mkdtemp()
    os.chdir(root)
    from pathlib import Path as _P
    monkeypatch.setattr(da, "_project_root", lambda: _P(root))
    db.db_init()
    capdir = os.path.join(root, "captures", "h")
    os.makedirs(capdir, exist_ok=True)
    old = os.path.join(capdir, "old.wacz")
    new = os.path.join(capdir, "new.wacz")
    open(old, "w").write("x")
    open(new, "w").write("x")
    now = time.time()
    db.db_captures_upsert([
        _cap("captures/h/old.wacz", "h", now - 40 * 86400),
        _cap("captures/h/new.wacz", "h", now - 1 * 86400),
    ])
    res = retention.apply_capture_retention({"capture_ttl_days": 30}, dry_run=False,
                                            confirm_paths={"captures/h/old.wacz"},
                                            now=now)
    assert not os.path.exists(old), "old capture file not deleted"
    assert os.path.exists(new), "new capture wrongly deleted"
    remaining = {r["rel_path"] for r in db.db_captures_all()}
    assert remaining == {"captures/h/new.wacz"}, f"index not updated: {remaining}"
    assert res.get("deleted", 0) == 1
