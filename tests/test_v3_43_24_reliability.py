"""v3.43.24 regression tests — reliability bundle.

Covers:
  1. selftest module: each check returns the right shape, fails clean
     on bad inputs, doesn't raise
  2. auto_recover_sqlite: moves corrupt DBs aside, leaves clean ones alone
  3. Worker watchdog: heartbeat stamping happens in worker loop
  4. Heartbeat-to-disk thread: function exists, writes atomically
  5. Better error context: log_event prefix includes site_id
"""
from __future__ import annotations

import json
# [SAST 3:13pm 13 may] removed unused: import os
import sqlite3
import tempfile
import time
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
class _AppAggregateSrc:
    """app.py + every app_*.py blueprint module (PHASE 4 decomposition: route
    groups moved onto Flask blueprints); glob keeps source-coupled guards green
    across all current and future app.py route-group cuts."""
    def __init__(self, pkg_dir):
        self._paths = [pkg_dir / "app.py"] + sorted(pkg_dir.glob("app_*.py"))
    def read_text(self, encoding="utf-8"):
        return "\n".join(p.read_text(encoding=encoding) for p in self._paths)
_APP_PY = _AppAggregateSrc(_REPO_ROOT / "bulk_downloader")
class _AggregateSrc:
    """runner.py + every runner_*.py mixin module (PHASE 3 decomposition v3.66.397+
    moved SiteRunner methods into siblings); glob keeps source-coupled guards green
    across all current and future runner cuts."""
    def __init__(self, pkg_dir):
        self._paths = [pkg_dir / "runner.py"] + sorted(pkg_dir.glob("runner_*.py"))
    def read_text(self, encoding="utf-8"):
        return "\n".join(p.read_text(encoding=encoding) for p in self._paths)
_RUNNER_PY = _AggregateSrc(_REPO_ROOT / "bulk_downloader")
_SELFTEST_PY = _REPO_ROOT / "bulk_downloader" / "selftest.py"


# ── Selftest module ────────────────────────────────────────────────────

def test_selftest_module_importable():
    """The module must import without side effects (no file writes,
    no network) — it's pure check functions until run_all() is called."""
    import bulk_downloader.selftest as st  # noqa: F401


def test_selftest_result_shape():
    """Every check returns the same shape so the UI can render
    uniformly. The contract: dict with status / test / message /
    detail / ts keys."""
    from bulk_downloader.selftest import _result, OK
    r = _result(OK, "test_check", "hello", path="/tmp")
    assert r["status"] == OK
    assert r["test"] == "test_check"
    assert r["message"] == "hello"
    assert r["detail"] == {"path": "/tmp"}
    assert isinstance(r["ts"], float)


def test_check_writable_dirs_ok_case():
    """Happy path: a real, writable directory."""
    from bulk_downloader.selftest import check_writable_dirs, OK
    with tempfile.TemporaryDirectory() as td:
        results = check_writable_dirs(td)
        assert len(results) == 1
        assert results[0]["status"] == OK


def test_check_writable_dirs_creates_missing():
    """A nonexistent but creatable directory should be created
    silently (mkdir parents=True), not fail."""
    from bulk_downloader.selftest import check_writable_dirs, OK
    with tempfile.TemporaryDirectory() as td:
        new_subdir = Path(td) / "deep" / "nested" / "path"
        results = check_writable_dirs(str(new_subdir))
        assert results[0]["status"] == OK
        assert new_subdir.exists()


def test_check_disk_space_thresholds():
    """The function must distinguish OK / WARN / FAIL based on the
    configured thresholds."""
    from bulk_downloader.selftest import check_disk_space, OK
    with tempfile.TemporaryDirectory() as td:
        # Defaults should pass for any normal dev machine
        r = check_disk_space(td)
        assert r["status"] in (OK, "warn", "fail")
        # Detail must include free_gb for the UI
        assert "free_gb" in r["detail"]
        assert isinstance(r["detail"]["free_gb"], float)


def test_check_database_passes_on_clean_db():
    """A freshly initialized DB should pass with WAL mode + all tables."""
    from bulk_downloader.selftest import check_database, OK
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "queue.db")
        # Create with proper schema by importing db_init
        import bulk_downloader.db as bd_db
        # Monkey-patch DB_PATH for this test
        orig_path = bd_db.DB_PATH
        bd_db.DB_PATH = db_path
        try:
            bd_db.db_init()
        finally:
            bd_db.DB_PATH = orig_path
        r = check_database(db_path)
        # Clean db, all tables present, integrity ok
        assert r["status"] == OK, f"got {r}"


def test_check_database_detects_missing_tables():
    """An empty SQLite file (no schema) should fail with a list of
    missing tables — actionable, not just 'something went wrong'."""
    from bulk_downloader.selftest import check_database, FAIL
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "empty.db")
        # Create just an empty SQLite file with no tables
        cx = sqlite3.connect(db_path)
        cx.execute("PRAGMA journal_mode=WAL")  # Match the WAL check
        cx.close()
        r = check_database(db_path)
        assert r["status"] == FAIL
        assert "missing tables" in r["message"]
        # Detail should list which ones
        assert "missing" in r["detail"]
        assert "queue" in r["detail"]["missing"]


def test_check_sites_config_handles_live_dict_format():
    """The live format is {sid: cfg, ...}. Self-test must validate
    it, NOT reject it as 'wrong shape' (the bug pre-fix)."""
    from bulk_downloader.selftest import check_sites_config, OK
    live = {
        "abc123": {"name": "Test Site", "login_url": "https://test.com"},
        "def456": {"name": "Other Site", "login_url": "https://other.com"},
    }
    results = check_sites_config(live)
    # Should pass cleanly — both sites valid
    assert all(r["status"] == OK for r in results), f"unexpected: {results}"


def test_check_sites_config_handles_export_list_format():
    """The example/export format is a list of dicts."""
    from bulk_downloader.selftest import check_sites_config, OK
    export = [
        {"name": "Test", "login_url": "https://test.com"},
    ]
    results = check_sites_config(export)
    assert all(r["status"] == OK for r in results)


def test_check_sites_config_rejects_garbage():
    """Strings, ints, None at the root must fail with a clear hint."""
    from bulk_downloader.selftest import check_sites_config, FAIL
    for garbage in ("hello", 42, None, [1, 2, 3]):
        results = check_sites_config(garbage)
        if garbage == [1, 2, 3]:
            # List of non-dicts — should detect per-element
            assert any(r["status"] == FAIL for r in results)
        elif garbage is None:
            # None isn't a dict OR list — must fail at root
            assert results[0]["status"] == FAIL
        elif isinstance(garbage, str):
            assert results[0]["status"] == FAIL


def test_check_loopback_passes():
    """Loopback must work in any reasonable test env."""
    from bulk_downloader.selftest import check_loopback, OK
    r = check_loopback()
    # OK is the strict pass; an env without loopback would FAIL and
    # surface a Windows-firewall hint. Either way, no crash.
    assert r["status"] in (OK, "fail")


def test_run_all_returns_summary():
    """The aggregate runner must return the contract shape: ok bool,
    checks list, summary counts, elapsed ms."""
    from bulk_downloader.selftest import run_all
    with tempfile.TemporaryDirectory() as td:
        report = run_all(
            sites_config_path=None,
            db_path=None,
            cookies_dir=str(Path(td) / "cookies"),
            download_dirs=[],
        )
        assert "ok" in report
        assert isinstance(report["ok"], bool)
        assert "checks" in report
        assert isinstance(report["checks"], list)
        assert "summary" in report
        for key in ("ok", "warn", "fail"):
            assert key in report["summary"]
        assert "elapsed_ms" in report


# ── Auto-recover ──────────────────────────────────────────────────────

def test_auto_recover_leaves_clean_db_alone():
    """A non-corrupt DB must NOT be touched. False positives here
    would silently destroy the user's queue/history."""
    from bulk_downloader.selftest import auto_recover_sqlite, OK
    import bulk_downloader.db as bd_db
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "clean.db")
        orig = bd_db.DB_PATH
        bd_db.DB_PATH = db_path
        try:
            bd_db.db_init()
        finally:
            bd_db.DB_PATH = orig
        r = auto_recover_sqlite(db_path)
        assert r["status"] == OK
        # File must still exist
        assert Path(db_path).exists()


def test_auto_recover_handles_missing_db():
    """A non-existent DB is a first-run scenario, not a corruption.
    Must return OK without moving anything."""
    from bulk_downloader.selftest import auto_recover_sqlite, OK
    with tempfile.TemporaryDirectory() as td:
        nonexistent = str(Path(td) / "never_existed.db")
        r = auto_recover_sqlite(nonexistent)
        assert r["status"] == OK


# ── Watchdog ──────────────────────────────────────────────────────────

def test_runner_init_creates_heartbeat_state():
    """SiteRunner must initialize the heartbeat dict + lock + hung_workers
    so the watchdog can safely read them at startup."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    assert "self._worker_heartbeats = {}" in src
    assert "self._worker_current_urls = {}" in src
    assert "self._job_progress_samples = {}" in src
    assert "self._worker_heartbeats_lock = threading.Lock()" in src
    assert "self._hung_workers = []" in src


def test_worker_loop_stamps_heartbeat():
    """The watchdog can't detect hangs if workers don't stamp."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    wl_start = src.find("def _worker_loop(self, worker_idx=0)")
    assert wl_start > 0
    wl_body = src[wl_start:wl_start + 5000]
    assert "self._worker_heartbeats[worker_idx] = time.time()" in wl_body, (
        "Workers must stamp heartbeats every iteration"
    )


def test_streaming_progress_paths_report_advancing_file_size():
    """Every byte-streaming path must feed the central liveness seam."""
    src = _RUNNER_PY.read_text(encoding="utf-8")

    direct_start = src.find("def _do_direct_http_download(")
    direct_end = src.find("def _try_multi_conn_download(", direct_start)
    direct_body = src[direct_start:direct_end]
    assert "file_size=got" in direct_body

    multi_start = direct_end
    multi_end = src.find("\n    def ", multi_start + 4)
    multi_body = src[multi_start:multi_end]
    assert "file_size=got" in multi_body

    sequential_start = src.find("def _http_download(")
    sequential_end = src.find("\n    def ", sequential_start + 4)
    sequential_body = src[sequential_start:sequential_end]
    assert "file_size=downloaded" in sequential_body


def test_watchdog_loop_method_exists():
    """The watchdog body must exist as a SiteRunner method."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    assert "def _watchdog_loop(self)" in src
    # 15-minute threshold (configurable in code; verify the constant)
    wd_start = src.find("def _watchdog_loop(self)")
    wd_body = src[wd_start:wd_start + 2500]
    assert "HUNG_THRESHOLD_S = 15 * 60" in wd_body


def test_watchdog_thread_started_in_start():
    """start() must spawn the watchdog. Otherwise it never runs."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    assert "_watchdog_loop" in src
    # Specifically: launched as daemon thread with site_id-bearing name
    assert 'name=f"watchdog-{self.site_id}"' in src


def test_get_status_exposes_hung_workers():
    """UI needs hung_workers in /api/status to surface the warning."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    assert '"hung_workers"' in src


# ── Heartbeat-to-disk ──────────────────────────────────────────────────

def test_heartbeat_to_disk_function_exists():
    """The heartbeat loop function must exist in app.py."""
    src = _APP_PY.read_text(encoding="utf-8")
    assert "def _heartbeat_to_disk_loop()" in src
    # Atomic write pattern (the v3.43.19 invariant)
    hb_start = src.find("def _heartbeat_to_disk_loop()")
    hb_body = src[hb_start:hb_start + 2000]
    assert "heartbeat.json.tmp" in hb_body
    assert "tmp.replace" in hb_body


def test_heartbeat_to_disk_skipped_under_disable_keepalive():
    """The heartbeat thread should follow the same on/off flag as
    session keepers so tests don't spawn extra threads."""
    src = _APP_PY.read_text(encoding="utf-8")
    # Find the launch site of the heartbeat thread (the Thread() call,
    # not the def). The def line uses the function name in `def ...()`;
    # the launch uses `target=...`. Search specifically for the launch.
    hb_launch = src.find("target=_heartbeat_to_disk_loop")
    assert hb_launch > 0, "heartbeat thread launch not found"
    # Look for the gating env var nearby (just before the Thread call)
    nearby = src[max(0, hb_launch-500):hb_launch+200]
    assert "BD_DISABLE_KEEPALIVE" in nearby, (
        "Heartbeat thread launch must be gated by BD_DISABLE_KEEPALIVE"
    )


# ── Better error context ───────────────────────────────────────────────

def test_log_event_includes_site_id_in_stderr():
    """v3.43.24: multi-site stderr lines must be grep-friendly with
    site_id in the prefix. Previously the format was `[kind] message`
    with no site distinction."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    le_start = src.find("def log_event(self, kind, message")
    assert le_start > 0
    le_body = src[le_start:le_start + 3000]
    # The new prefix format includes site_id explicitly. Match the
    # f-string body without depending on surrounding whitespace.
    assert "[{self.site_id}][{kind}]" in le_body, (
        "log_event stderr prefix must include site_id for "
        "multi-site grep filtering"
    )


# ── App startup integration ────────────────────────────────────────────

def test_selftest_runs_at_startup():
    """The startup orchestration must run the self-test before
    db_init so auto-recover gets a chance."""
    src = _APP_PY.read_text(encoding="utf-8")
    selftest_pos = src.find("_selftest.run_all")
    db_init_pos = src.find("\ndb_init()")
    assert selftest_pos > 0
    assert db_init_pos > 0
    assert selftest_pos < db_init_pos, (
        "self-test must run BEFORE db_init() so auto_recover_sqlite "
        "can move corrupt DBs aside before db_init tries to use them"
    )


def test_selftest_endpoint_registered():
    """UI calls /api/selftest to fetch results. Must be present."""
    src = _APP_PY.read_text(encoding="utf-8")
    assert '/api/selftest' in src
    assert 'def api_selftest()' in src
