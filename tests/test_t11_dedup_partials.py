"""T11 (Tier 3) — D-37 dedup-hash explorer + D-38 partial-download
finder. Two read-only pipeline-inspection tools in Group D. D-37
queries the existing perceptual-hash registry; D-38 walks configured
download_dirs for .part tempfiles.
"""
import contextlib
import os
import sqlite3
import time

from bulk_downloader import dev_suite as ds


# ── helpers ────────────────────────────────────────────────────────

@contextlib.contextmanager
def _permissive_paths():
    """Empty path_allowlist == permissive. partial_download_finder
    routes each site download_dir through app._validate_path, so
    tests writing into pytest tmp_path need permissive mode to be
    reachable. (Custom runner doesn't auto-inject file-local
    fixtures, so this is a context manager not a fixture.)"""
    from bulk_downloader import app as bd_app
    saved = bd_app._app_cfg.get("path_allowlist")
    bd_app._app_cfg["path_allowlist"] = []
    try:
        yield
    finally:
        bd_app._app_cfg["path_allowlist"] = saved


@contextlib.contextmanager
def _dedup_db_at(path):
    """Point app._app_cfg['dedup_db_path'] at `path` for the duration
    of the test."""
    from bulk_downloader import app as bd_app
    saved = bd_app._app_cfg.get("dedup_db_path")
    bd_app._app_cfg["dedup_db_path"] = str(path)
    try:
        yield
    finally:
        bd_app._app_cfg["dedup_db_path"] = saved


def _seed_dedup_db(path, rows):
    """Create a video_hashes table at `path` and insert rows. Each
    row: (path, hash_hex, size, duration, codec)."""
    c = sqlite3.connect(str(path))
    try:
        c.execute("""CREATE TABLE IF NOT EXISTS video_hashes(
            path TEXT PRIMARY KEY, hash_hex TEXT NOT NULL,
            duration_sec REAL, file_size_bytes INTEGER,
            computed_at REAL NOT NULL, ffprobe_codec TEXT,
            notes TEXT)""")
        for p, h, sz, dur, codec in rows:
            c.execute(
                "INSERT OR REPLACE INTO video_hashes "
                "(path, hash_hex, duration_sec, file_size_bytes, "
                "computed_at, ffprobe_codec, notes) "
                "VALUES (?,?,?,?,?,?,?)",
                (p, h, dur, sz, time.time(), codec, ""))
        c.commit()
    finally:
        c.close()


# ── D-37 dedup_hash_explore ────────────────────────────────────────

def test_dedup_explore_no_registry(clean_workdir, tmp_path):
    # registry file doesn't exist yet -> clean "not in use" verdict
    missing = tmp_path / "no_such.db"
    with _dedup_db_at(missing):
        r = ds.dedup_hash_explore()
    assert r["ok"] is True
    assert r["tool"] == "dedup_hash_explore"
    assert r["registry_present"] is False
    assert r["total_hashes"] == 0
    assert "not in use yet" in r["verdict"]


def test_dedup_explore_counts_total(clean_workdir, tmp_path):
    dbp = tmp_path / "v.db"
    _seed_dedup_db(dbp, [
        ("/dl/a.mp4", "aaaa1111bbbb2222", 1000, 60.0, "h264"),
        ("/dl/b.mp4", "cccc3333dddd4444", 2000, 30.0, "hevc"),
    ])
    with _dedup_db_at(dbp):
        r = ds.dedup_hash_explore()
    assert r["registry_present"] is True
    assert r["total_hashes"] == 2
    assert r["distinct_hashes"] == 2
    assert r["duplicate_clusters"] == 0
    assert r["clusters"] == []


def test_dedup_explore_finds_clusters(clean_workdir, tmp_path):
    dbp = tmp_path / "v.db"
    _seed_dedup_db(dbp, [
        # two paths share hash AAAA
        ("/dl/a1.mp4", "aaaa", 5000, 60.0, "h264"),
        ("/dl/a2.mp4", "aaaa", 7000, 60.0, "hevc"),
        # three paths share hash BBBB
        ("/dl/b1.mp4", "bbbb", 1000, 30.0, "h264"),
        ("/dl/b2.mp4", "bbbb", 2000, 30.0, "h264"),
        ("/dl/b3.mp4", "bbbb", 3000, 30.0, "hevc"),
        # unique
        ("/dl/c.mp4", "cccc", 4000, 45.0, "h264"),
    ])
    with _dedup_db_at(dbp):
        r = ds.dedup_hash_explore()
    assert r["total_hashes"] == 6
    assert r["duplicate_clusters"] == 2
    counts = sorted(c["count"] for c in r["clusters"])
    assert counts == [2, 3]
    # largest cluster first
    assert r["clusters"][0]["count"] == 3
    assert r["clusters"][0]["hash_hex"] == "bbbb"
    # within a cluster, paths are ordered by size desc
    big = r["clusters"][0]["paths"]
    assert big[0]["size_bytes"] >= big[-1]["size_bytes"]


def test_dedup_explore_caps_top(clean_workdir, tmp_path):
    dbp = tmp_path / "v.db"
    rows = []
    # 5 distinct hashes, each shared by 2 files
    for i in range(5):
        h = f"hash{i:04d}"
        rows.append((f"/dl/{i}a.mp4", h, 100 * i, 30.0, "h264"))
        rows.append((f"/dl/{i}b.mp4", h, 100 * i + 1, 30.0, "h264"))
    _seed_dedup_db(dbp, rows)
    with _dedup_db_at(dbp):
        r = ds.dedup_hash_explore(top=2)
    assert r["duplicate_clusters"] == 5
    assert r["dupes_shown"] == 2
    # absurd top clamps, never explodes
    with _dedup_db_at(dbp):
        r2 = ds.dedup_hash_explore(top=99999)
    assert r2["dupes_shown"] <= 200


# ── D-38 partial_download_finder ───────────────────────────────────

def test_partial_finder_no_sites(clean_workdir):
    r = ds.partial_download_finder(site_configs={})
    assert r["ok"] is True
    assert r["tool"] == "partial_download_finder"
    assert r["partials"] == []
    assert "no configured download_dirs" in r["verdict"]


def test_partial_finder_finds_part_files(clean_workdir, tmp_path):
    dl = tmp_path / "dl"
    dl.mkdir()
    (dl / "video1.mp4.part").write_bytes(b"x" * 1024)
    (dl / "video2.webm.part").write_bytes(b"x" * 2048)
    (dl / "finished.mp4").write_bytes(b"x" * 100)  # NOT a .part
    with _permissive_paths():
        r = ds.partial_download_finder(
            site_configs={"siteA": {"download_dir": str(dl)}})
    assert len(r["partials"]) == 2
    assert all(p["site_id"] == "siteA" for p in r["partials"])
    assert all(p["path"].endswith(".part") for p in r["partials"])
    assert r["total_bytes"] == 1024 + 2048


def test_partial_finder_distinguishes_resumable_vs_orphaned(
        clean_workdir, tmp_path):
    dl = tmp_path / "dl"
    dl.mkdir()
    # resumable: .part + .part.meta sidecar
    (dl / "good.mp4.part").write_bytes(b"x" * 500)
    (dl / "good.mp4.part.meta").write_text("{}")
    # orphaned: .part with no sidecar
    (dl / "lost.mp4.part").write_bytes(b"x" * 500)
    with _permissive_paths():
        r = ds.partial_download_finder(
            site_configs={"siteA": {"download_dir": str(dl)}})
    assert r["resumable_count"] == 1
    assert r["orphaned_count"] == 1
    assert "1 resumable, 1 orphaned" in r["verdict"]


def test_partial_finder_recurses_into_subdirs(clean_workdir, tmp_path):
    dl = tmp_path / "dl"
    (dl / "site_subdir").mkdir(parents=True)
    (dl / "site_subdir" / "nested.mp4.part").write_bytes(b"x" * 256)
    with _permissive_paths():
        r = ds.partial_download_finder(
            site_configs={"siteA": {"download_dir": str(dl)}})
    assert len(r["partials"]) == 1
    assert "nested.mp4.part" in r["partials"][0]["path"]


def test_partial_finder_skips_missing_dir(clean_workdir, tmp_path):
    with _permissive_paths():
        r = ds.partial_download_finder(
            site_configs={"siteA": {"download_dir": str(
                tmp_path / "does_not_exist")}})
    assert r["partials"] == []
    assert len(r["dirs_skipped"]) == 1
    assert "does not exist" in r["dirs_skipped"][0]["reason"]


def test_partial_finder_dedupes_shared_dirs(clean_workdir, tmp_path):
    dl = tmp_path / "shared"
    dl.mkdir()
    (dl / "vid.mp4.part").write_bytes(b"x" * 100)
    # two sites point at the same download_dir — must not double-count
    with _permissive_paths():
        r = ds.partial_download_finder(site_configs={
            "siteA": {"download_dir": str(dl)},
            "siteB": {"download_dir": str(dl)},
        })
    assert len(r["dirs_scanned"]) == 1
    assert len(r["partials"]) == 1


def test_partial_finder_respects_allowlist(clean_workdir, tmp_path):
    from bulk_downloader import app as bd_app
    dl = tmp_path / "dl"
    dl.mkdir()
    (dl / "x.part").write_bytes(b"x")
    saved = bd_app._app_cfg.get("path_allowlist")
    try:
        # restrict to a different root — the test's dir is NOT under
        # it, so the directory must be skipped, no file disclosed
        bd_app._app_cfg["path_allowlist"] = ["/no/such/root"]
        r = ds.partial_download_finder(
            site_configs={"siteA": {"download_dir": str(dl)}})
        assert r["partials"] == []
        assert len(r["dirs_skipped"]) == 1
        assert "allowlist" in r["dirs_skipped"][0]["reason"]
    finally:
        bd_app._app_cfg["path_allowlist"] = saved


def test_partial_finder_caps_max_files(clean_workdir, tmp_path):
    dl = tmp_path / "dl"
    dl.mkdir()
    for i in range(20):
        (dl / f"v{i}.mp4.part").write_bytes(b"x")
    with _permissive_paths():
        r = ds.partial_download_finder(
            site_configs={"siteA": {"download_dir": str(dl)}},
            max_files=5)
    assert len(r["partials"]) == 5
    assert r["truncated"] is True


# ── dev routes ─────────────────────────────────────────────────────

def test_dedup_hashes_route_no_registry(fresh_app, tmp_path):
    # default db_path doesn't exist -> clean response, not a 500
    j = fresh_app.get("/api/dev/dedup_hashes").get_json()
    assert j["ok"] is True
    assert j["tool"] == "dedup_hash_explore"
    assert j["registry_present"] is False


def test_partials_route(fresh_app):
    j = fresh_app.get("/api/dev/partials").get_json()
    assert j["ok"] is True
    assert j["tool"] == "partial_download_finder"


def test_t11_routes_are_dev_gated():
    os.environ.pop("BD_DEV_MODE", None)
    os.environ["BD_DEV_MODE_DISABLE"] = "1"
    try:
        from bulk_downloader.app import app
        c = app.test_client()
        for ep in ("/api/dev/dedup_hashes", "/api/dev/partials"):
            assert c.get(ep).status_code == 404
    finally:
        os.environ.pop("BD_DEV_MODE_DISABLE", None)
