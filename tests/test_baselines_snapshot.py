"""Fixture test for tools/baselines_snapshot.py (Track F1 baseline gate).

Builds a synthetic sqlite DB with the real history / session_history schema,
inserts known rows, and asserts the computed snapshot. Custom-runner format:
zero-arg functions, stdlib only.
"""

import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime

# tools/ is not a package; load the module by path.
import importlib.util

_TOOL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tools", "baselines_snapshot.py")
_spec = importlib.util.spec_from_file_location("baselines_snapshot", _TOOL)
bs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bs)


def _mkdb():
    """A DB with just the two tables the tool reads."""
    d = tempfile.mkdtemp()
    path = os.path.join(d, "history.db")
    cx = sqlite3.connect(path)
    cx.execute("""CREATE TABLE history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_id TEXT, site_name TEXT, url TEXT, status TEXT,
        filename TEXT, file_size INTEGER, message TEXT, screenshot TEXT,
        honeypot_score REAL DEFAULT NULL, ts TEXT)""")
    cx.execute("""CREATE TABLE session_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL, site_id TEXT NOT NULL, account_idx INTEGER,
        event_type TEXT NOT NULL, detail TEXT DEFAULT '')""")
    cx.commit()
    return cx


def _iso(now, days_ago, hour):
    t = datetime.fromtimestamp(now - days_ago * 86400)
    return t.replace(hour=hour, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S")


def test_hourly_stats_per_site():
    now = time.time()
    cx = _mkdb()
    # siteA: 2 ok @09:00 (1d ago), 1 failed @09:00 (2d ago)
    cx.execute("INSERT INTO history(site_id,url,status,ts) VALUES(?,?,?,?)",
               ("siteA", "u1", "ok", _iso(now, 1, 9)))
    cx.execute("INSERT INTO history(site_id,url,status,ts) VALUES(?,?,?,?)",
               ("siteA", "u2", "ok", _iso(now, 1, 9)))
    cx.execute("INSERT INTO history(site_id,url,status,ts) VALUES(?,?,?,?)",
               ("siteA", "u3", "failed", _iso(now, 2, 9)))
    # siteB: 1 ok @14:00, and one OLD row (30d ago) that must be excluded
    cx.execute("INSERT INTO history(site_id,url,status,ts) VALUES(?,?,?,?)",
               ("siteB", "u4", "ok", _iso(now, 1, 14)))
    cx.execute("INSERT INTO history(site_id,url,status,ts) VALUES(?,?,?,?)",
               ("siteB", "u5", "ok", _iso(now, 30, 14)))
    cx.commit()
    snap = bs.compute_baselines(cx, now_epoch=now, window_days=7)
    hs = snap["metrics"]["hourly_stats_per_site"]
    assert hs["siteA"]["total"] == 3, hs["siteA"]
    assert hs["siteA"]["by_hour"]["09"]["ok"] == 2, hs["siteA"]
    assert hs["siteA"]["by_hour"]["09"]["failed"] == 1, hs["siteA"]
    assert hs["siteB"]["total"] == 1, hs["siteB"]  # 30d-old row excluded
    cx.close()


def test_heartbeat_fail_7d():
    now = time.time()
    cx = _mkdb()
    def ev(site, etype, days_ago):
        cx.execute("INSERT INTO session_history(ts,site_id,event_type) VALUES(?,?,?)",
                   (now - days_ago * 86400, site, etype))
    ev("siteA", "heartbeat_fail", 1)
    ev("siteA", "auto_relogin_fail", 2)
    ev("siteA", "heartbeat_ok", 1)          # NOT a fail -> excluded
    ev("siteB", "needs_takeover", 3)
    ev("siteB", "heartbeat_fail", 30)       # outside window -> excluded
    cx.commit()
    snap = bs.compute_baselines(cx, now_epoch=now, window_days=7)
    hb = snap["metrics"]["heartbeat_fail_7d"]
    assert hb["total"] == 3, hb
    assert hb["per_site"]["siteA"]["heartbeat_fail"] == 1, hb
    assert hb["per_site"]["siteA"]["auto_relogin_fail"] == 1, hb
    assert hb["per_site"]["siteB"]["needs_takeover"] == 1, hb
    assert "heartbeat_ok" not in hb["per_site"].get("siteA", {}), hb
    cx.close()


def test_dup_url_fetch_7d():
    now = time.time()
    cx = _mkdb()
    # u1 fetched 3x (2 redundant), u2 fetched 2x (1 redundant), u3 once (0)
    for _ in range(3):
        cx.execute("INSERT INTO history(site_id,url,status,ts) VALUES(?,?,?,?)",
                   ("s", "u1", "ok", _iso(now, 1, 10)))
    for _ in range(2):
        cx.execute("INSERT INTO history(site_id,url,status,ts) VALUES(?,?,?,?)",
                   ("s", "u2", "ok", _iso(now, 1, 11)))
    cx.execute("INSERT INTO history(site_id,url,status,ts) VALUES(?,?,?,?)",
               ("s", "u3", "ok", _iso(now, 1, 12)))
    cx.commit()
    snap = bs.compute_baselines(cx, now_epoch=now, window_days=7)
    du = snap["metrics"]["dup_url_fetch_7d"]
    assert du["dup_urls"] == 2, du           # u1, u2
    assert du["redundant_fetches"] == 3, du  # (3-1)+(2-1)
    cx.close()


def test_idle_tab_stub_is_explicit():
    now = time.time()
    cx = _mkdb()
    snap = bs.compute_baselines(cx, now_epoch=now)
    it = snap["metrics"]["idle_tab_request_rate"]
    assert it["available"] is False, it
    assert "instrumentation" in it["reason"], it
    cx.close()


def test_missing_table_is_fail_soft():
    # A DB with NO tables -> each metric reports an error, snapshot still builds.
    d = tempfile.mkdtemp()
    cx = sqlite3.connect(os.path.join(d, "empty.db"))
    snap = bs.compute_baselines(cx, now_epoch=time.time())
    assert "error" in snap["metrics"]["hourly_stats_per_site"]
    assert "error" in snap["metrics"]["heartbeat_fail_7d"]
    # the stub never errors
    assert snap["metrics"]["idle_tab_request_rate"]["available"] is False
    cx.close()


def test_snapshot_shape_stable():
    now = time.time()
    cx = _mkdb()
    snap = bs.compute_baselines(cx, now_epoch=now)
    assert set(snap["metrics"].keys()) == {
        "hourly_stats_per_site", "heartbeat_fail_7d",
        "dup_url_fetch_7d", "idle_tab_request_rate"}
    assert snap["window_days"] == 7
    assert snap["generated_at"].endswith("Z")
    cx.close()
