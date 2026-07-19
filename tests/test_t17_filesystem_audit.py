"""T17 (Tier 3) — D-112 disk-usage breakdown + D-113 download-folder
scanner. Both share one disk walk over configured download_dirs and
both honour the path_allowlist. D-112 reports usage stats; D-113
reports anomalies the operator should clean up.
"""
import contextlib
import os
import time

from bulk_downloader import dev_suite as ds


@contextlib.contextmanager
def _permissive_paths():
    """Empty path_allowlist == permissive. Same pattern as T10/T11."""
    from bulk_downloader import app as bd_app
    saved = bd_app._app_cfg.get("path_allowlist")
    bd_app._app_cfg["path_allowlist"] = []
    try:
        yield
    finally:
        bd_app._app_cfg["path_allowlist"] = saved


# ── D-112 disk_usage_breakdown ─────────────────────────────────────

def test_disk_usage_no_configured_dirs(clean_workdir):
    r = ds.disk_usage_breakdown(site_configs={})
    assert r["ok"] is True
    assert r["tool"] == "disk_usage_breakdown"
    assert r["total_files"] == 0
    assert r["total_bytes"] == 0
    assert "no configured" in r["verdict"]


def test_disk_usage_counts_bytes_per_extension(clean_workdir, tmp_path):
    dd = tmp_path / "dl"
    dd.mkdir()
    (dd / "a.mp4").write_bytes(b"x" * 1000)
    (dd / "b.mp4").write_bytes(b"x" * 2000)
    (dd / "c.webm").write_bytes(b"x" * 500)
    (dd / "d.txt").write_bytes(b"x" * 50)
    site_configs = {"siteA": {"download_dir": str(dd)}}
    with _permissive_paths():
        r = ds.disk_usage_breakdown(site_configs=site_configs)
    assert r["total_files"] == 4
    by_ext = {e["ext"]: e for e in r["per_extension"]}
    assert by_ext[".mp4"]["count"] == 2
    assert by_ext[".mp4"]["bytes"] == 3000
    assert by_ext[".webm"]["bytes"] == 500
    # sorted descending by bytes -> .mp4 is first
    assert r["per_extension"][0]["ext"] == ".mp4"


def test_disk_usage_buckets_by_age(clean_workdir, tmp_path):
    dd = tmp_path / "dl"
    dd.mkdir()
    fresh = dd / "fresh.mp4"
    fresh.write_bytes(b"x" * 100)
    old = dd / "old.mp4"
    old.write_bytes(b"x" * 200)
    # backdate old to 100 days ago
    age = time.time() - (100 * 86400)
    os.utime(old, (age, age))
    site_configs = {"siteA": {"download_dir": str(dd)}}
    with _permissive_paths():
        r = ds.disk_usage_breakdown(site_configs=site_configs)
    by_b = {b["bucket"]: b for b in r["per_age_bucket"]}
    assert by_b["0_1d"]["count"] >= 1
    assert by_b["90d_plus"]["count"] >= 1
    assert by_b["90d_plus"]["bytes"] >= 200


def test_disk_usage_per_site(clean_workdir, tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    (a / "v.mp4").write_bytes(b"x" * 1000)
    b = tmp_path / "b"
    b.mkdir()
    (b / "v.mp4").write_bytes(b"x" * 500)
    site_configs = {
        "siteA": {"download_dir": str(a)},
        "siteB": {"download_dir": str(b)},
    }
    with _permissive_paths():
        r = ds.disk_usage_breakdown(site_configs=site_configs)
    by_site = {s["site_id"]: s for s in r["per_site"]}
    assert by_site["siteA"]["bytes"] == 1000
    assert by_site["siteB"]["bytes"] == 500


def test_disk_usage_respects_allowlist(clean_workdir, tmp_path):
    from bulk_downloader import app as bd_app
    dd = tmp_path / "dl"
    dd.mkdir()
    (dd / "v.mp4").write_bytes(b"x" * 1000)
    site_configs = {"siteA": {"download_dir": str(dd)}}
    saved = bd_app._app_cfg.get("path_allowlist")
    try:
        bd_app._app_cfg["path_allowlist"] = ["/no/such/root"]
        r = ds.disk_usage_breakdown(site_configs=site_configs)
        assert r["total_files"] == 0
        assert any("not under any configured allowlist root"
                   in s["reason"] for s in r["dirs_skipped"])
    finally:
        bd_app._app_cfg["path_allowlist"] = saved


def test_disk_usage_clamps_max_files(clean_workdir, tmp_path):
    dd = tmp_path / "many"
    dd.mkdir()
    for i in range(8):
        (dd / f"v{i}.mp4").write_bytes(b"x")
    site_configs = {"siteA": {"download_dir": str(dd)}}
    with _permissive_paths():
        r = ds.disk_usage_breakdown(site_configs=site_configs,
                                      max_files=3)
    assert r["total_files"] == 3
    assert r["truncated"] is True


# ── D-113 download_folder_scan ─────────────────────────────────────

def test_scan_no_configured_dirs(clean_workdir):
    r = ds.download_folder_scan(site_configs={})
    assert r["ok"] is True
    assert r["tool"] == "download_folder_scan"
    assert r["zero_byte_files"] == []
    assert r["orphaned_partials"] == []


def test_scan_finds_zero_byte_files(clean_workdir, tmp_path):
    dd = tmp_path / "dl"
    dd.mkdir()
    (dd / "good.mp4").write_bytes(b"x" * 1_000_000)
    (dd / "empty.mp4").write_bytes(b"")
    site_configs = {"siteA": {"download_dir": str(dd)}}
    with _permissive_paths():
        r = ds.download_folder_scan(site_configs=site_configs)
    assert len(r["zero_byte_files"]) == 1
    assert "empty.mp4" in r["zero_byte_files"][0]["path"]


def test_scan_finds_very_small_files(clean_workdir, tmp_path):
    dd = tmp_path / "dl"
    dd.mkdir()
    (dd / "tiny.mp4").write_bytes(b"x" * 100)  # 100 bytes
    (dd / "normal.mp4").write_bytes(b"x" * 50_000)
    site_configs = {"siteA": {"download_dir": str(dd)}}
    with _permissive_paths():
        r = ds.download_folder_scan(site_configs=site_configs)
    assert len(r["very_small_files"]) == 1
    assert "tiny.mp4" in r["very_small_files"][0]["path"]


def test_scan_finds_orphaned_partials(clean_workdir, tmp_path):
    dd = tmp_path / "dl"
    dd.mkdir()
    # resumable (has .meta sidecar) — NOT an orphan
    (dd / "ok.mp4.part").write_bytes(b"x" * 100)
    (dd / "ok.mp4.part.meta").write_text("{}", encoding="utf-8")
    # orphan — no sidecar
    (dd / "orph.mp4.part").write_bytes(b"x" * 100)
    site_configs = {"siteA": {"download_dir": str(dd)}}
    with _permissive_paths():
        r = ds.download_folder_scan(site_configs=site_configs)
    assert len(r["orphaned_partials"]) == 1
    assert "orph.mp4.part" in r["orphaned_partials"][0]["path"]


def test_scan_finds_duplicate_filenames_across_sites(clean_workdir,
                                                     tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    (a / "shared.mp4").write_bytes(b"x" * 1000)
    b = tmp_path / "b"
    b.mkdir()
    (b / "shared.mp4").write_bytes(b"x" * 2000)
    (b / "unique.mp4").write_bytes(b"x" * 500)
    site_configs = {
        "siteA": {"download_dir": str(a)},
        "siteB": {"download_dir": str(b)},
    }
    with _permissive_paths():
        r = ds.download_folder_scan(site_configs=site_configs)
    dup_names = [d["filename"] for d in r["duplicate_filenames"]]
    assert "shared.mp4" in dup_names
    assert "unique.mp4" not in dup_names


def test_scan_anomaly_count_aggregates(clean_workdir, tmp_path):
    dd = tmp_path / "dl"
    dd.mkdir()
    (dd / "z.mp4").write_bytes(b"")          # 1 zero-byte
    (dd / "t.mp4").write_bytes(b"x" * 100)   # 1 very-small
    (dd / "o.mp4.part").write_bytes(b"x")    # 1 orphan
    site_configs = {"siteA": {"download_dir": str(dd)}}
    with _permissive_paths():
        r = ds.download_folder_scan(site_configs=site_configs)
    assert r["anomaly_count"] >= 3


# ── dev routes ─────────────────────────────────────────────────────

def test_disk_usage_route(fresh_app):
    j = fresh_app.get("/api/dev/disk_usage").get_json()
    assert j["ok"] is True
    assert j["tool"] == "disk_usage_breakdown"


def test_download_scan_route(fresh_app):
    j = fresh_app.get("/api/dev/download_scan").get_json()
    assert j["ok"] is True
    assert j["tool"] == "download_folder_scan"


def test_t17_routes_are_dev_gated():
    os.environ.pop("BD_DEV_MODE", None)
    os.environ["BD_DEV_MODE_DISABLE"] = "1"
    try:
        from bulk_downloader.app import app
        c = app.test_client()
        for ep in ("/api/dev/disk_usage", "/api/dev/download_scan"):
            assert c.get(ep).status_code == 404
    finally:
        os.environ.pop("BD_DEV_MODE_DISABLE", None)
