"""v3.66.512 — 3e/C1: reuse the authenticated onboarding session for downloads.

Onboarding writes the session (login + cf_clearance) into
profiles/<slug>-<host>-cloak, but the worker opens profiles/<sid>/main and
never reads it. profile_sync.sync_onboarding_to_runtime + the new route
POST /api/sites/<sid>/session/reuse_onboarding transplant the login-continuity
state into the runtime profiles. Session reuse, not challenge-solving. The
route is value-free (NAMES/counts/host only — no paths/values).
"""
from __future__ import annotations

import bulk_downloader.app as bd_app
from bulk_downloader import profile_sync as ps


def _write(p, data="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data)


def _seed_cloak(root, sid, host, *, default=True):
    """Create an onboarding cloak profile profiles/<sid>-<host>-cloak with the
    continuity items + non-continuity state that must NOT be copied."""
    base = root / f"{sid}-{host}-cloak"
    d = base / "Default" if default else base
    _write(d / "Cookies", "COOKIEDATA")
    _write(d / "Cookies-journal", "JOURNAL")
    _write(d / "Local Storage" / "leveldb" / "000.log", "LS")
    _write(d / "Session Storage" / "000.log", "SS")
    _write(d / "IndexedDB" / "https_site_0.indexeddb.leveldb" / "CURRENT", "IDB")
    _write(d / "WebStorage" / "QuotaManager", "WS")
    _write(d / "History", "HIST")           # non-continuity — must be ignored
    _write(d / "Cache" / "data_0", "CACHE")  # non-continuity — must be ignored
    return base


def _seed(sid, **cfg):
    cfg.setdefault("name", sid)
    bd_app.s_cfg[sid] = cfg
    return cfg


# ── profile_sync.sync_onboarding_to_runtime ──────────────────────────────

def test_onboarding_reuse_copies_session_into_main(tmp_path):
    root = tmp_path / "profiles"
    sid = "site1"
    _seed_cloak(root, sid, "app.reptyle.com")
    # an existing main with a STALE session + its own History
    _write(root / sid / "main" / "Default" / "Cookies", "OLD")
    _write(root / sid / "main" / "Default" / "History", "oldhist")

    summ = ps.sync_onboarding_to_runtime(sid, profiles_root=str(root))

    assert summ["skipped_reason"] is None
    assert summ["host"] == "app.reptyle.com"
    assert "main" in summ["synced"]
    # stale cookie overwritten with the onboarding session
    assert (root / sid / "main" / "Default" / "Cookies").read_text() == "COOKIEDATA"
    assert (root / sid / "main" / "Default" / "Local Storage" / "leveldb" / "000.log").read_text() == "LS"
    # non-continuity state NOT propagated; main's own History untouched
    present = {p.name for p in (root / sid / "main" / "Default").iterdir()}
    assert "Cache" not in present
    assert (root / sid / "main" / "Default" / "History").read_text() == "oldhist"


def test_onboarding_reuse_picks_newest_cloak_and_seeds_workers(tmp_path):
    import os
    import time
    root = tmp_path / "profiles"
    sid = "s2"
    older = _seed_cloak(root, sid, "auth.reptyle.com")
    newer = _seed_cloak(root, sid, "app.reptyle.com")
    # make app.reptyle.com strictly newer
    t = time.time()
    os.utime(older / "Default" / "Cookies", (t - 1000, t - 1000))
    os.utime(newer / "Default" / "Cookies", (t, t))
    os.utime(older, (t - 1000, t - 1000))
    os.utime(newer, (t, t))
    # an existing worker w1
    (root / sid / "w1" / "Default").mkdir(parents=True)

    summ = ps.sync_onboarding_to_runtime(
        sid, profiles_root=str(root), ensure=("main", "keepalive_0"))

    assert summ["host"] == "app.reptyle.com"
    assert {"main", "w1", "keepalive_0"} <= set(summ["synced"])
    assert (root / sid / "w1" / "Default" / "Cookies").read_text() == "COOKIEDATA"
    assert (root / sid / "keepalive_0" / "Default" / "Cookies").read_text() == "COOKIEDATA"


def test_onboarding_reuse_no_profile_is_a_clean_skip(tmp_path):
    root = tmp_path / "profiles"
    summ = ps.sync_onboarding_to_runtime("nope", profiles_root=str(root))
    assert summ["synced"] == {}
    assert "no onboarding session" in (summ["skipped_reason"] or "")


def test_onboarding_reuse_explicit_cloak_dir(tmp_path):
    root = tmp_path / "profiles"
    sid = "s3"
    cloak = _seed_cloak(root, sid, "example.com")
    summ = ps.sync_onboarding_to_runtime(
        sid, cloak_dir=str(cloak), profiles_root=str(root))
    assert "main" in summ["synced"]
    assert (root / sid / "main" / "Default" / "Cookies").read_text() == "COOKIEDATA"


# ── route POST /api/sites/<sid>/session/reuse_onboarding ──────────────────

def test_reuse_route_unknown_site_404(fresh_app):
    r = fresh_app.post("/api/sites/ghost/session/reuse_onboarding", json={})
    assert r.status_code == 404
    assert r.get_json()["ok"] is False


def test_reuse_route_no_session_clean_summary(fresh_app):
    # a site with no onboarding cloak profile -> reused False + reason, and the
    # value-free contract: seeded entries expose only metadata keys.
    _seed("reuse_nosession_x", login_url="https://no-host.example/")
    r = fresh_app.post("/api/sites/reuse_nosession_x/session/reuse_onboarding", json={})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["reused"] is False
    assert "no onboarding session" in (body["skipped_reason"] or "")
    allowed = {"profile", "items", "count"}
    assert all(set(s.keys()) <= allowed for s in body["seeded"])
    # F2: no filesystem paths anywhere in the response
    import json as _j
    assert "/profiles/" not in _j.dumps(body)
    assert "-cloak" not in _j.dumps(body)
