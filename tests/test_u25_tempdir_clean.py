"""U25 — temp-dir cleaner (D-114, an (A) action) + lock-file scanner
(D-118, read-only). Structural pair over one orphan-resource walk.

Every test redirects tempfile.gettempdir() to an isolated tmp_path so
the suite never reads or writes the real system temp dir.
"""
import os
import tempfile
import time

from bulk_downloader import dev_suite as ds


def _make_artifacts(root):
    """Create BD-style temp artifacts under `root`: an old sweepable
    backup dir, a fresh (too-young) restore dir, an old .lock file,
    and a non-BD dir that must always be left alone. Returns their
    paths."""
    old_dir = os.path.join(root, "bdback_oldjob")
    fresh_dir = os.path.join(root, "bdrestore_freshjob")
    other_dir = os.path.join(root, "someoneelses_dir")
    lock = os.path.join(root, "aborted_move.lock")
    for d in (old_dir, fresh_dir, other_dir):
        os.makedirs(d)
    open(lock, "w").close()
    old = time.time() - 7200  # 2h — well past the 1h age gate
    os.utime(old_dir, (old, old))
    os.utime(lock, (old, old))
    return old_dir, fresh_dir, other_dir, lock


# ── lockfile_scan (D-118) ──────────────────────────────────────────

def test_scan_finds_bd_artifacts_and_ignores_others(tmp_path,
                                                     monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    old_dir, fresh_dir, other_dir, lock = _make_artifacts(str(tmp_path))
    r = ds.lockfile_scan()
    assert r["ok"] is True
    names = {d["name"] for d in r["bd_temp_dirs"]}
    assert "bdback_oldjob" in names
    assert "bdrestore_freshjob" in names
    # a non-BD directory is never matched
    assert "someoneelses_dir" not in names
    assert r["lock_file_count"] == 1


def test_scan_marks_only_old_dirs_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    _make_artifacts(str(tmp_path))
    r = ds.lockfile_scan()
    by_name = {d["name"]: d for d in r["bd_temp_dirs"]}
    assert by_name["bdback_oldjob"]["sweepable"] is True
    assert by_name["bdrestore_freshjob"]["sweepable"] is False


# ── tempdir_clean (D-114) — dry run ────────────────────────────────

def test_dry_run_is_the_default_and_deletes_nothing(tmp_path,
                                                    monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    old_dir, fresh_dir, other_dir, lock = _make_artifacts(str(tmp_path))
    r = ds.tempdir_clean()
    assert r["dry_run"] is True
    assert r["candidate_count"] >= 2  # old dir + lock
    # nothing was actually removed
    assert os.path.isdir(old_dir)
    assert os.path.exists(lock)


def test_dry_run_excludes_young_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    old_dir, fresh_dir, other_dir, lock = _make_artifacts(str(tmp_path))
    would = {os.path.basename(x["path"])
             for x in ds.tempdir_clean()["would_remove"]}
    assert "bdback_oldjob" in would
    assert "aborted_move.lock" in would
    # the fresh restore dir is younger than the age gate
    assert "bdrestore_freshjob" not in would


# ── tempdir_clean (D-114) — real sweep ─────────────────────────────

def test_real_sweep_removes_only_stale_bd_artifacts(tmp_path,
                                                    monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    old_dir, fresh_dir, other_dir, lock = _make_artifacts(str(tmp_path))
    r = ds.tempdir_clean(dry_run=False)
    assert r["dry_run"] is False
    assert len(r["removed"]) >= 2
    assert r["failed"] == []
    # stale BD artifacts gone
    assert not os.path.exists(old_dir)
    assert not os.path.exists(lock)
    # a young BD dir and a non-BD dir both survive
    assert os.path.isdir(fresh_dir)
    assert os.path.isdir(other_dir)


def test_sweep_string_dry_run_flag_is_coerced(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    old_dir, _, _, _ = _make_artifacts(str(tmp_path))
    # a JSON body could deliver "false" as a string — must still sweep
    r = ds.tempdir_clean(dry_run="false")
    assert r["dry_run"] is False
    assert not os.path.exists(old_dir)


def test_min_age_zero_makes_everything_a_candidate(tmp_path,
                                                   monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    _make_artifacts(str(tmp_path))
    # with min_age 0 even the fresh dir is sweepable
    would = {os.path.basename(x["path"])
             for x in ds.tempdir_clean(min_age_seconds=0)["would_remove"]}
    assert "bdrestore_freshjob" in would


# ── routes ─────────────────────────────────────────────────────────

def test_lockfile_scan_route(fresh_app):
    body = fresh_app.get("/api/dev/lockfile_scan").get_json()
    assert body["ok"] is True
    assert "bd_temp_dirs" in body and "lock_files" in body


def test_tempdir_clean_route_defaults_to_dry_run(fresh_app):
    resp = fresh_app.post("/api/dev/tempdir_clean", json={})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    # the route must default to a safe dry run
    assert body["dry_run"] is True
