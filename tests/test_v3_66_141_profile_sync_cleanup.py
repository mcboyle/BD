"""v3.66.141 — follow-up cleanups on the v3.66.140 profile_sync handoff.

Pure filesystem behaviour (no browser):
  1. timestamped backups before replacing existing auth/session items
  2. LOCK files in storage dirs are never copied
  3. a profile is reported "synced" only if >=1 item was actually copied
  4. keepalive profiles are guarded by the takeover lock during the copy
  5. copied item names + target profile names are logged
"""
import contextlib
import io
from pathlib import Path

from bulk_downloader import profile_sync as ps


def _write(p, data="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data)


def _seed_manual(root, sid, *, default=True):
    base = root / sid / "manual"
    d = base / "Default" if default else base
    _write(d / "Cookies", "COOKIEDATA")
    _write(d / "Cookies-journal", "JOURNAL")
    _write(d / "Local Storage" / "leveldb" / "000.log", "LS")
    _write(d / "Session Storage" / "000.log", "SS")
    _write(d / "IndexedDB" / "https_site_0.indexeddb.leveldb" / "CURRENT", "IDB")
    _write(d / "WebStorage" / "QuotaManager", "WS")
    return base


# --- Fix 1: timestamped backups -------------------------------------------

def test_existing_items_backed_up_before_replace(tmp_path):
    root = tmp_path / "profiles"; sid = "bk1"
    _seed_manual(root, sid)
    _write(root / sid / "main" / "Default" / "Cookies", "OLDCOOKIE")
    _write(root / sid / "main" / "Default" / "Local Storage" / "leveldb" / "000.log", "OLDLS")

    ps.sync_manual_to_runtime(sid, profiles_root=str(root), ensure=("main",))

    # new session is in place
    assert (root / sid / "main" / "Default" / "Cookies").read_text() == "COOKIEDATA"
    # the old auth/session items were backed up (timestamped, at the profile root)
    backups = list((root / sid / "main" / ".sync_backups").glob("*"))
    assert len(backups) == 1
    bdir = backups[0]
    assert (bdir / "Cookies").read_text() == "OLDCOOKIE"
    assert (bdir / "Local Storage" / "leveldb" / "000.log").read_text() == "OLDLS"
    # backups live OUTSIDE Default/ so Chromium ignores them
    assert ".sync_backups" not in {p.name for p in (root / sid / "main" / "Default").iterdir()}


def test_no_backup_dir_when_destination_is_fresh(tmp_path):
    root = tmp_path / "profiles"; sid = "bk2"
    _seed_manual(root, sid)
    ps.sync_manual_to_runtime(sid, profiles_root=str(root), ensure=("main",))
    # fresh main had nothing to overwrite -> no backup dir created
    assert not (root / sid / "main" / ".sync_backups").exists()


# --- Fix 2: LOCK files never copied ---------------------------------------

def test_lock_files_not_copied(tmp_path):
    root = tmp_path / "profiles"; sid = "lk1"
    base = _seed_manual(root, sid)
    d = base / "Default"
    _write(d / "Local Storage" / "leveldb" / "LOCK", "L1")
    _write(d / "Session Storage" / "LOCK", "L2")
    _write(d / "IndexedDB" / "https_site_0.indexeddb.leveldb" / "LOCK", "L3")

    ps.sync_manual_to_runtime(sid, profiles_root=str(root), ensure=("main",))

    dd = root / sid / "main" / "Default"
    # real data still copied
    assert (dd / "Local Storage" / "leveldb" / "000.log").read_text() == "LS"
    # but no LOCK anywhere under the copied dirs
    assert list(dd.rglob("LOCK")) == []


# --- Fix 3: synced only when something was copied -------------------------

def test_profile_with_nothing_to_copy_is_not_reported_synced(tmp_path):
    root = tmp_path / "profiles"; sid = "nc1"
    # manual exists but holds NO continuity items (only non-continuity state)
    _write(root / sid / "manual" / "Default" / "History", "HIST")
    _write(root / sid / "manual" / "Default" / "Cache" / "data_0", "CACHE")

    summ = ps.sync_manual_to_runtime(sid, profiles_root=str(root), ensure=("main",))

    assert summ["skipped_reason"] is None
    assert summ["synced"] == {}                       # nothing copied -> not synced
    assert summ["skipped"].get("main") == "no continuity items copied"
    # and main got no continuity item written
    assert not (root / sid / "main" / "Default" / "Cookies").exists()


# --- Fix 4: keepalive guarded by the takeover lock ------------------------

class _RecLock:
    def __init__(self):
        self.held = False
        self.events = []

    def acquire(self, blocking=True):
        self.held = True
        self.events.append("acquire")
        return True

    def release(self):
        self.held = False
        self.events.append("release")


def test_keepalive_lock_held_during_copy_and_released(tmp_path, monkeypatch):
    root = tmp_path / "profiles"; sid = "kg1"
    _seed_manual(root, sid)
    rec = _RecLock()
    monkeypatch.setattr(ps, "_get_takeover_lock", lambda s, i: rec)

    held_during = {}
    real_sync = ps.sync_profile

    def spy(src, dst, **kw):
        held_during[Path(dst).name] = rec.held
        return real_sync(src, dst, **kw)

    monkeypatch.setattr(ps, "sync_profile", spy)

    summ = ps.sync_manual_to_runtime(
        sid, profiles_root=str(root), ensure=("main", "keepalive_0"))

    # lock acquired exactly once (for keepalive_0) and released
    assert rec.events == ["acquire", "release"]
    assert rec.held is False
    # held while copying keepalive_0, NOT held for the non-keepalive main
    assert held_during["keepalive_0"] is True
    assert held_during["main"] is False
    assert "keepalive_0" in summ["synced"]


def test_keepalive_skipped_when_keeper_holds_lock(tmp_path, monkeypatch):
    root = tmp_path / "profiles"; sid = "kg2"
    _seed_manual(root, sid)

    class _Busy:
        def acquire(self, blocking=True):
            return False

        def release(self):
            pass

    monkeypatch.setattr(ps, "_get_takeover_lock", lambda s, i: _Busy())

    summ = ps.sync_manual_to_runtime(
        sid, profiles_root=str(root), ensure=("main", "keepalive_0"))

    # keepalive_0 not touched (live profile not clobbered)
    assert "keepalive_0" not in summ["synced"]
    assert summ["skipped"].get("keepalive_0") == "profile in use by keepalive"
    assert not (root / sid / "keepalive_0" / "Default" / "Cookies").exists()
    # main (not a keepalive) is unaffected and still synced
    assert "main" in summ["synced"]


# --- Fix 5: logging --------------------------------------------------------

def test_logs_copied_items_and_target_names(tmp_path):
    root = tmp_path / "profiles"; sid = "lg1"
    _seed_manual(root, sid)
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        ps.sync_manual_to_runtime(
            sid, profiles_root=str(root), ensure=("main", "keepalive_0"))
    out = buf.getvalue()
    assert f"profile_sync[{sid}]: main <- manual" in out
    assert "Cookies" in out
    assert "keepalive_0 <- manual" in out
