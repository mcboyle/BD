"""RED-first tests for Cut 622 / C5: SQLite continuous-replication (Litestream) plumbing.

Sandbox-runner conventions (no pytest builtins): every test is zero-arg, uses
tempfile.mkdtemp (not tmp_path), and restores any global it touches in try/finally.
The litestream binary is un-sandbox-verifiable (like yt-dlp/gallery-dl) — these
tests exercise config-generation, store enumeration, status, the fail-closed
lifecycle guards, and the restore-plumbing WITHOUT the binary present. Live WAL
shipping is validated on-stash.

Charter: replication is default-OFF; every entry point must no-op / fail closed
until the operator enables it AND the binary exists.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path


def _mkbase_with_config(cfg: dict | None):
    """Create a temp base_dir; optionally plant an app_config.json carrying a
    'replication' block. Returns the base_dir path (str)."""
    base = tempfile.mkdtemp(prefix="bdrepl_")
    if cfg is not None:
        with open(os.path.join(base, "app_config.json"), "w") as fh:
            json.dump({"replication": cfg}, fh)
    return base


def _plant_sqlite(base: str, name: str):
    """Create a tiny valid WAL-mode sqlite db called <name> under base."""
    p = os.path.join(base, name)
    cx = sqlite3.connect(p)
    try:
        cx.execute("PRAGMA journal_mode=WAL")
        cx.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)")
        cx.execute("INSERT INTO t (v) VALUES ('hello')")
        cx.commit()
    finally:
        cx.close()
    return p


# ── config generation (pure) ───────────────────────────────────────────

def test_render_config_lists_each_store_with_a_file_replica():
    from bulk_downloader import db_replication as R
    stores = [Path("/srv/bd/queue.db"), Path("/srv/bd/video_hashes.db")]
    replica_root = "/srv/bd/replicas"
    text = R.render_litestream_config(stores, replica_root)
    # every source db path appears
    assert "/srv/bd/queue.db" in text
    assert "/srv/bd/video_hashes.db" in text
    # each has a file-type replica rooted under replica_root
    assert "type: file" in text
    assert "/srv/bd/replicas/queue.db" in text
    assert "/srv/bd/replicas/video_hashes.db" in text
    # it's a dbs: document (litestream top-level key)
    assert "dbs:" in text


def test_render_config_is_deterministic():
    from bulk_downloader import db_replication as R
    stores = [Path("/a/queue.db")]
    a = R.render_litestream_config(stores, "/a/rep")
    b = R.render_litestream_config(stores, "/a/rep")
    assert a == b


# ── store enumeration (existing-on-disk) ────────────────────────────────

def test_replication_stores_finds_only_existing_dbs():
    from bulk_downloader import db_replication as R
    base = _mkbase_with_config(None)
    _plant_sqlite(base, "queue.db")
    # video_hashes.db intentionally NOT planted
    found = {Path(p).name for p in R.replication_stores(base_dir=base)}
    assert "queue.db" in found
    assert "video_hashes.db" not in found


# ── binary availability + status (never raises) ─────────────────────────

def test_litestream_available_returns_bool():
    from bulk_downloader import db_replication as R
    assert isinstance(R.litestream_available(), bool)


def test_status_never_raises_and_reports_disabled_by_default():
    from bulk_downloader import db_replication as R
    base = _mkbase_with_config(None)  # no replication block -> defaults
    st = R.replication_status(base_dir=base)
    assert isinstance(st, dict)
    assert st["enabled"] is False                       # charter default-OFF
    assert st["binary_present"] == R.litestream_available()
    assert st["running"] is False
    assert "replica_root" in st


# ── fail-closed lifecycle (no binary in sandbox) ────────────────────────

def test_start_is_fail_closed_when_disabled():
    from bulk_downloader import db_replication as R
    base = _mkbase_with_config(None)  # disabled
    res = R.start_replication(base_dir=base)
    assert res["ok"] is False
    assert "disabl" in res.get("reason", "").lower()


def test_start_is_fail_closed_when_binary_absent_even_if_enabled():
    from bulk_downloader import db_replication as R
    if R.litestream_available():
        return  # only meaningful when the binary is genuinely absent (sandbox)
    base = _mkbase_with_config({"enabled": True})
    _plant_sqlite(base, "queue.db")
    res = R.start_replication(base_dir=base)
    assert res["ok"] is False
    reason = res.get("reason", "").lower()
    assert "binary" in reason or "not found" in reason or "litestream" in reason


def test_restore_is_fail_closed_when_binary_absent():
    from bulk_downloader import db_replication as R
    if R.litestream_available():
        return
    base = _mkbase_with_config({"enabled": True})
    dest = os.path.join(tempfile.mkdtemp(prefix="bdreplrest_"), "restored.db")
    res = R.restore_store("queue.db", dest, base_dir=base)
    assert res["ok"] is False
    reason = res.get("reason", "").lower()
    assert "binary" in reason or "not found" in reason or "litestream" in reason


# ── default config resolution ───────────────────────────────────────────

def test_default_cfg_is_off_and_has_replica_root():
    from bulk_downloader import db_replication as R
    base = _mkbase_with_config(None)
    cfg = R._load_repl_cfg(base_dir=base)
    assert cfg["enabled"] is False
    assert cfg.get("replica_root")  # non-empty default path


if __name__ == "__main__":
    # allow direct execution for quick RED/GREEN checks
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
            print(f"  [PASS] {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"  [FAIL] {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed} passed / {failed} failed")
    raise SystemExit(1 if failed else 0)
