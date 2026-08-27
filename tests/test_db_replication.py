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


def test_repeated_start_spawns_exactly_one_sidecar():
    """The second start reaches the owned-instance refusal, not Popen."""
    from bulk_downloader import db_replication as R
    base = _mkbase_with_config({"enabled": True})
    _plant_sqlite(base, "queue.db")
    saved_available = R.litestream_available
    saved_popen = R.subprocess.Popen
    missing = object()
    saved_alive = getattr(R, "_pid_alive", missing)
    saved_start = getattr(R, "_proc_start", missing)
    spawns = []

    class FakeProc:
        def __init__(self, pid):
            self.pid = pid

        def terminate(self):
            raise AssertionError("a successfully recorded fake must not be terminated")

    def fake_popen(*args, **kwargs):
        spawns.append((args, kwargs))
        return FakeProc(41111 + len(spawns))

    try:
        R.litestream_available = lambda: True
        R.subprocess.Popen = fake_popen
        R._pid_alive = lambda pid: True
        R._proc_start = lambda pid: "start-%d" % pid
        first = R.start_replication(base_dir=base)
        second = R.start_replication(base_dir=base)
    finally:
        R.litestream_available = saved_available
        R.subprocess.Popen = saved_popen
        if saved_alive is missing:
            delattr(R, "_pid_alive")
        else:
            R._pid_alive = saved_alive
        if saved_start is missing:
            delattr(R, "_proc_start")
        else:
            R._proc_start = saved_start

    assert first["ok"] is True, first
    assert second["ok"] is False, second
    assert "already" in second.get("reason", "").lower(), second
    assert len(spawns) == 1, "repeated start spawned %d sidecars" % len(spawns)


def test_stop_refuses_an_unidentified_legacy_pid_without_signalling():
    """A numeric-only pidfile is not authority to signal that PID."""
    from bulk_downloader import db_replication as R
    base = _mkbase_with_config({"enabled": True})
    pidfile = os.path.join(base, R._PIDFILE_BASENAME)
    with open(pidfile, "w") as fh:
        fh.write("42222")
    saved_kill = R.os.kill
    signals = []
    try:
        R.os.kill = lambda pid, sig: signals.append((pid, sig))
        out = R.stop_replication(base_dir=base)
    finally:
        R.os.kill = saved_kill
    assert out["ok"] is False, out
    assert "identity" in out.get("reason", "").lower(), out
    assert signals == [], "numeric-only identity triggered signals: %r" % signals
    assert os.path.exists(pidfile), "unverifiable ownership evidence was discarded"


def test_owned_identity_reaches_exactly_one_pidfd_signal():
    """Negative control: a matching identity still has a reachable stop path."""
    from bulk_downloader import db_replication as R
    base = _mkbase_with_config({"enabled": True})
    pid = 43333
    pidfile = os.path.join(base, R._PIDFILE_BASENAME)
    with open(pidfile, "w") as fh:
        json.dump({"schema": "bd-litestream-pid/1", "pid": pid,
                   "start": "start-43333"}, fh)

    missing = object()
    names = ("_open_pidfd", "_send_pidfd_term", "_close_pidfd", "_proc_start")
    saved = {name: getattr(R, name, missing) for name in names}
    opened = []
    sent = []
    closed = []
    try:
        R._open_pidfd = lambda value: opened.append(value) or 91
        R._send_pidfd_term = lambda fd: sent.append(fd)
        R._close_pidfd = lambda fd: closed.append(fd)
        R._proc_start = lambda value: "start-43333"
        out = R.stop_replication(base_dir=base)
    finally:
        for name, value in saved.items():
            if value is missing:
                delattr(R, name)
            else:
                setattr(R, name, value)

    assert out == {"ok": True, "stopped": True}, out
    assert opened == [pid]
    assert sent == [91]
    assert closed == [91]
    assert not os.path.exists(pidfile)


def test_reused_pid_identity_is_closed_without_signalling():
    """A pidfd alone is insufficient when the recorded start tick differs."""
    from bulk_downloader import db_replication as R
    base = _mkbase_with_config({"enabled": True})
    pid = 44444
    pidfile = os.path.join(base, R._PIDFILE_BASENAME)
    with open(pidfile, "w") as fh:
        json.dump({"schema": "bd-litestream-pid/1", "pid": pid,
                   "start": "owned-start"}, fh)

    opened = []
    sent = []
    closed = []
    saved = {
        "_open_pidfd": R._open_pidfd,
        "_send_pidfd_term": R._send_pidfd_term,
        "_close_pidfd": R._close_pidfd,
        "_proc_start": R._proc_start,
    }
    try:
        R._open_pidfd = lambda value: opened.append(value) or 92
        R._send_pidfd_term = lambda fd: sent.append(fd)
        R._close_pidfd = lambda fd: closed.append(fd)
        R._proc_start = lambda value: "reused-start"
        out = R.stop_replication(base_dir=base)
    finally:
        for name, value in saved.items():
            setattr(R, name, value)

    assert out == {"ok": True, "stopped": False}, out
    assert opened == [pid]
    assert sent == [], "reused PID received a signal: %r" % sent
    assert closed == [92]
    assert not os.path.exists(pidfile)


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
