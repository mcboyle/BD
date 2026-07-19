"""Track F1-A admission seam tests (F1.1 / F1.2 / F1.3).

Custom runner: zero-arg functions, no pytest builtins, restore module
globals in try/finally. All signals are exercised with synthetic stubs
(no real disk / cookie file / clock dependence).
"""

import time
from datetime import datetime

from bulk_downloader import admission


# ── F1.2 disk-aware admission ────────────────────────────────────────

def test_disk_hold_below_threshold():
    cfg = {"download_dir": "/data", "disk_threshold_gb": 2.0}
    r = admission.admission_hold(cfg, disk_free_fn=lambda p: 0.5)
    assert r == "low_disk", r


def test_disk_admit_above_threshold():
    cfg = {"download_dir": "/data", "disk_threshold_gb": 2.0}
    r = admission.admission_hold(cfg, disk_free_fn=lambda p: 50.0)
    assert r is None, r


def test_disk_no_download_dir_admits():
    cfg = {"disk_threshold_gb": 2.0}
    assert admission.admission_hold(cfg, disk_free_fn=lambda p: 0.0) is None


def test_disk_default_threshold():
    # No disk_threshold_gb -> default 2.0
    cfg = {"download_dir": "/data"}
    assert admission.admission_hold(cfg, disk_free_fn=lambda p: 1.0) == "low_disk"
    assert admission.admission_hold(cfg, disk_free_fn=lambda p: 3.0) is None


def test_disk_bad_threshold_falls_back_to_default():
    cfg = {"download_dir": "/data", "disk_threshold_gb": "garbage"}
    # garbage -> default 2.0; free 1.0 < 2.0 -> hold
    assert admission.admission_hold(cfg, disk_free_fn=lambda p: 1.0) == "low_disk"


def test_disk_free_none_admits():
    # disk_free_gb can return None (unreadable path) -> admit, never hold
    cfg = {"download_dir": "/data"}
    assert admission.admission_hold(cfg, disk_free_fn=lambda p: None) is None


# ── F1.3 cookie-expiry admission ─────────────────────────────────────

def _expired(n):   # n cookies, all expired
    return [{"name": f"c{i}", "expires": time.time() - 3600} for i in range(n)]


def _live(n):
    return [{"name": f"c{i}", "expires": time.time() + 86400} for i in range(n)]


def _session(n):
    return [{"name": f"c{i}"} for i in range(n)]  # no expiry field


def test_cookie_hold_disabled_by_default():
    # cookie_admission_enabled absent -> never evaluated
    cfg = {"cookie_file": "/x.json"}
    r = admission.admission_hold(cfg, cookie_loader=lambda p: _expired(3))
    assert r is None, r


def test_cookie_hold_all_expired():
    cfg = {"cookie_admission_enabled": True, "cookie_file": "/x.json"}
    r = admission.admission_hold(cfg, cookie_loader=lambda p: _expired(3))
    assert r == "cookies_expired", r


def test_cookie_admit_one_live():
    cfg = {"cookie_admission_enabled": True, "cookie_file": "/x.json"}
    jar = _expired(3) + _live(1)
    assert admission.admission_hold(cfg, cookie_loader=lambda p: jar) is None


def test_cookie_session_only_does_not_hold():
    # session cookies (no expiry) can't be proven expired -> admit
    cfg = {"cookie_admission_enabled": True, "cookie_file": "/x.json"}
    assert admission.admission_hold(cfg, cookie_loader=lambda p: _session(4)) is None


def test_cookie_empty_jar_admits():
    cfg = {"cookie_admission_enabled": True, "cookie_file": "/x.json"}
    assert admission.admission_hold(cfg, cookie_loader=lambda p: []) is None


def test_cookie_no_file_admits():
    cfg = {"cookie_admission_enabled": True}  # no cookie_file
    assert admission.admission_hold(cfg, cookie_loader=lambda p: _expired(3)) is None


def test_cookie_expirationDate_alias():
    # Playwright export uses expirationDate; ensure alias is honored
    cfg = {"cookie_admission_enabled": True, "cookie_file": "/x.json"}
    jar = [{"name": "c", "expirationDate": time.time() - 10}]
    assert admission.admission_hold(cfg, cookie_loader=lambda p: jar) == "cookies_expired"


# ── precedence + fail-open ───────────────────────────────────────────

def test_disk_precedence_over_cookies():
    cfg = {"download_dir": "/d", "cookie_admission_enabled": True, "cookie_file": "/x"}
    r = admission.admission_hold(
        cfg, disk_free_fn=lambda p: 0.1, cookie_loader=lambda p: _expired(2))
    assert r == "low_disk", r


def test_fail_open_on_loader_error():
    cfg = {"cookie_admission_enabled": True, "cookie_file": "/x.json"}

    def boom(_):
        raise OSError("unreadable")

    assert admission.admission_hold(cfg, cookie_loader=boom) is None


def test_fail_open_on_disk_error():
    cfg = {"download_dir": "/d"}

    def boom(_):
        raise RuntimeError("statvfs blew up")

    assert admission.admission_hold(cfg, disk_free_fn=boom) is None


def test_non_dict_config_admits():
    assert admission.admission_hold(None) is None
    assert admission.admission_hold("nope") is None


# ── F1.1 bad-hours retry ─────────────────────────────────────────────

def test_retry_unchanged_when_window_disabled():
    ts = time.time() + 600
    assert admission.next_eligible_retry(ts, {}) == ts
    assert admission.next_eligible_retry(ts, {"window_enabled": False}) == ts


def test_retry_unchanged_when_in_window():
    # 00:00-23:59 -> effectively always open -> unchanged.
    # v3.66.772: use a FIXED noon timestamp. time.time()+600 flaked when it
    # landed in the 23:59:xx tail (the window end minute) or crossed midnight;
    # next_eligible_retry keys off the ts's own time-of-day, so a mid-day ts is
    # unambiguously in-window and deterministic.
    cfg = {"window_enabled": True, "window_active_hours": "00:00-23:59"}
    ts = datetime(2026, 6, 13, 12, 0, 0).timestamp()
    assert admission.next_eligible_retry(ts, cfg) == ts


def test_retry_snaps_forward_when_closed():
    # Window 09:00-17:00; pick a retry time at 03:00 (closed) -> must move
    # forward to >= the 09:00 open boundary.
    cfg = {"window_enabled": True, "window_active_hours": "09:00-17:00"}
    base = datetime(2026, 6, 13, 3, 0, 0)  # 03:00 local, closed
    ts = base.timestamp()
    out = admission.next_eligible_retry(ts, cfg)
    assert out > ts, (out, ts)
    snapped = datetime.fromtimestamp(out)
    # Snapped time should be inside the open window
    from bulk_downloader import download_window as dw
    assert dw.site_in_window(cfg, now=snapped), snapped.isoformat()


def test_retry_fail_open_on_bad_ts():
    # A garbage timestamp must not raise; returns input unchanged.
    cfg = {"window_enabled": True, "window_active_hours": "09:00-17:00"}
    bad = float("nan")
    out = admission.next_eligible_retry(bad, cfg)
    # nan != nan, so just assert it didn't raise and returned a float
    assert isinstance(out, float)


def test_retry_non_dict_config_unchanged():
    ts = time.time() + 100
    assert admission.next_eligible_retry(ts, None) == ts


# ── integration: start() actually consults the cookie gate ───────────

def test_start_holds_on_expired_cookies():
    """Prove the runner.start() wiring fires: an opt-in site with a fully
    expired cookie jar lands in 'cookies_expired' and spawns no workers."""
    import json
    import os
    import tempfile
    from bulk_downloader.db import db_init
    db_init()
    from bulk_downloader.runner import SiteRunner

    d = tempfile.mkdtemp()
    cookie_path = os.path.join(d, "cookies.json")
    with open(cookie_path, "w", encoding="utf-8") as f:
        json.dump([{"name": "sid", "value": "x",
                    "expirationDate": int(time.time()) - 3600}], f)

    r = SiteRunner("t_f1a_cookie", {
        "name": "t",
        "cookie_admission_enabled": True,
        "cookie_file": cookie_path,
        "auto_teach_first_run": False,
    })
    try:
        r.load_urls(["https://example.com/v1"])
        r.start()
        assert r._state == "cookies_expired", r._state
        assert len(r._worker_threads) == 0
        # The URL stays pending — held, not failed.
        assert r.jobs["https://example.com/v1"]["status"] == "pending"
    finally:
        r.stop()
        try:
            r._stop_auto_retry()
        except Exception:
            pass


def test_start_admits_with_live_cookies():
    """Control: a live cookie jar does NOT trip the cookie gate. Uses the
    auto-teach pre-flight so start() halts at needs_review (no worker/browser
    spawn in-sandbox) AFTER clearing the admission gate."""
    import json
    import os
    import tempfile
    from bulk_downloader.db import db_init
    db_init()
    from bulk_downloader.runner import SiteRunner

    d = tempfile.mkdtemp()
    cookie_path = os.path.join(d, "cookies.json")
    with open(cookie_path, "w", encoding="utf-8") as f:
        json.dump([{"name": "sid", "value": "x",
                    "expirationDate": int(time.time()) + 86400}], f)

    r = SiteRunner("t_f1a_live", {
        "name": "t",
        "cookie_admission_enabled": True,
        "cookie_file": cookie_path,
        "auto_teach_first_run": True,   # halt at needs_review, spawn no workers
    })
    try:
        r.load_urls(["https://example.com/v1"])
        r.start()
        # Gate was cleared (not held) and start() progressed to the
        # auto-teach pre-flight, not the cookie hold.
        assert r._state != "cookies_expired", r._state
        assert r.jobs["https://example.com/v1"]["status"] == "needs_review"
        assert len(r._worker_threads) == 0
    finally:
        r.stop()
        try:
            r._stop_auto_retry()
        except Exception:
            pass
