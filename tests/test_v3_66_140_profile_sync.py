"""v3.66.140 — manual-login -> runtime profile session handoff (profile_sync).

Pure filesystem behavior (no browser): after a manual login the login-continuity
storage in profiles/<sid>/manual must be copied into the runtime profiles
(main / w<N> / keepalive_<N>), and only those items — not caches/History/etc.
"""
import os

from bulk_downloader import profile_sync as ps


def _write(p, data="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data)


def _seed_manual(root, sid, *, default=True):
    """Create a manual profile with all 6 continuity items + some non-continuity
    state that must NOT be copied."""
    base = root / sid / "manual"
    d = base / "Default" if default else base
    _write(d / "Cookies", "COOKIEDATA")
    _write(d / "Cookies-journal", "JOURNAL")
    _write(d / "Local Storage" / "leveldb" / "000.log", "LS")
    _write(d / "Session Storage" / "000.log", "SS")
    _write(d / "IndexedDB" / "https_site_0.indexeddb.leveldb" / "CURRENT", "IDB")
    _write(d / "WebStorage" / "QuotaManager", "WS")
    # non-continuity — must be ignored
    _write(d / "History", "HIST")
    _write(d / "Cache" / "data_0", "CACHE")
    return base


def _items_present(profile_default):
    return {p.name for p in profile_default.iterdir()}


def test_sync_seeds_main_and_keepalive_and_existing_workers(tmp_path):
    root = tmp_path / "profiles"
    sid = "site1"
    _seed_manual(root, sid)
    # an existing main with a STALE session, and an existing worker w1
    _write(root / sid / "main" / "Default" / "Cookies", "OLD")
    _write(root / sid / "main" / "Default" / "History", "oldhist")
    (root / sid / "w1" / "Default").mkdir(parents=True)

    summ = ps.sync_manual_to_runtime(
        sid, profiles_root=str(root), ensure=("main", "keepalive_0"))

    assert summ["skipped_reason"] is None
    assert set(summ["synced"]) == {"main", "w1", "keepalive_0"}
    assert not summ["errors"]

    # main: stale cookie overwritten with the manual session
    assert (root / sid / "main" / "Default" / "Cookies").read_text() == "COOKIEDATA"
    assert (root / sid / "main" / "Default" / "Cookies-journal").read_text() == "JOURNAL"
    assert (root / sid / "main" / "Default" / "Local Storage" / "leveldb" / "000.log").read_text() == "LS"
    assert (root / sid / "main" / "Default" / "Session Storage" / "000.log").exists()
    assert (root / sid / "main" / "Default" / "IndexedDB").is_dir()
    assert (root / sid / "main" / "Default" / "WebStorage" / "QuotaManager").exists()

    # non-continuity state is NOT propagated, and main's own History is untouched
    present = _items_present(root / sid / "main" / "Default")
    assert "Cache" not in present
    assert (root / sid / "main" / "Default" / "History").read_text() == "oldhist"

    # existing worker + freshly-seeded keepalive both got the session
    assert (root / sid / "w1" / "Default" / "Cookies").read_text() == "COOKIEDATA"
    assert (root / sid / "keepalive_0" / "Default" / "Cookies").read_text() == "COOKIEDATA"

    # the copied-item list excludes non-continuity items
    assert "Cookies" in summ["synced"]["main"]
    assert "History" not in summ["synced"]["main"]
    assert "Cache" not in summ["synced"]["main"]


def test_runtime_profile_dirs_excludes_manual(tmp_path):
    root = tmp_path / "profiles"
    sid = "site2"
    for name in ("manual", "main", "w1", "w2", "keepalive_0", "junk_dir"):
        (root / sid / name).mkdir(parents=True)
    names = {p.name for p in ps.runtime_profile_dirs(sid, profiles_root=str(root))}
    assert names == {"main", "w1", "w2", "keepalive_0"}  # manual + junk excluded


def test_no_manual_profile_is_skipped(tmp_path):
    root = tmp_path / "profiles"
    sid = "site3"
    (root / sid / "main").mkdir(parents=True)  # runtime exists, but no manual
    summ = ps.sync_manual_to_runtime(sid, profiles_root=str(root))
    assert summ["skipped_reason"] == "no manual profile to sync from"
    assert summ["synced"] == {}


def test_flat_layout_without_default_subdir(tmp_path):
    root = tmp_path / "profiles"
    sid = "site4"
    _seed_manual(root, sid, default=False)  # items at profile root, no Default/
    summ = ps.sync_manual_to_runtime(sid, profiles_root=str(root), ensure=("main",))
    # mirrored flat into the destination root
    assert (root / sid / "main" / "Cookies").read_text() == "COOKIEDATA"
    assert (root / sid / "main" / "Local Storage" / "leveldb" / "000.log").exists()
    assert "main" in summ["synced"]
