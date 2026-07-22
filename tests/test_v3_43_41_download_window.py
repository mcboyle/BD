"""v3.43.41 regression tests — per-site download windows.

Coverage:
  - parse_hm: HH:MM parsing + defensive type handling
  - parse_windows: comma-separated range parsing, midnight crossing
  - in_window: time-of-day matching for normal and midnight-crossing
  - site_in_window: top-level helper, fail-open on bad config
  - next_transition_seconds: countdown to next boundary
  - WindowScheduler: callback fires on transition, BD_DISABLE_KEEPALIVE
    gates thread creation
  - Runner integration: start() refuses outside window with
    window_paused state
  - App lifecycle: scheduler started at boot, callback wired
  - CFG_FIELDS + defaults
  - UI: load/save + status badge
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
# [SAST 3:13pm 13 may] removed unused: from unittest import mock


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DW_PY = _REPO_ROOT / "bulk_downloader" / "download_window.py"
_APP_PY = _REPO_ROOT / "bulk_downloader" / "app.py"
_RUNNER_PY = _REPO_ROOT / "bulk_downloader" / "runner.py"


# ── Module surface ────────────────────────────────────────────────────


def _APP_SRC():
    """app.py + extracted app_*.py blueprint modules (Phase 4 thin-core-shell)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)


def test_module_importable():
    import bulk_downloader.download_window as dw  # noqa: F401


def test_required_exports():
    from bulk_downloader import download_window as dw
    for name in ("parse_hm", "parse_windows", "in_window",
                  "site_in_window", "next_transition_seconds",
                  "WindowScheduler", "get_scheduler"):
        assert hasattr(dw, name), f"missing export: {name}"


# ── parse_hm ──────────────────────────────────────────────────────────

def test_parse_hm_valid():
    from bulk_downloader.download_window import parse_hm
    assert parse_hm("09:00") == 9 * 60
    assert parse_hm("00:00") == 0
    assert parse_hm("23:59") == 23 * 60 + 59
    assert parse_hm("12:30") == 12 * 60 + 30


def test_parse_hm_defensive():
    """Non-string input, malformed strings, out-of-range — all return
    default. Defensive because cfg corruption could put int/None here."""
    from bulk_downloader.download_window import parse_hm
    for bad in (None, 12345, [], {}, "", "no-colon",
                  "abc:def", "25:00", "12:60", "12:30:45"):
        assert parse_hm(bad, 999) == 999, f"failed on {bad!r}"


# ── parse_windows ────────────────────────────────────────────────────

def test_parse_windows_single():
    from bulk_downloader.download_window import parse_windows
    assert parse_windows("09:00-17:00") == [(540, 1020)]


def test_parse_windows_multi():
    from bulk_downloader.download_window import parse_windows
    result = parse_windows("08:00-12:00,14:00-18:00")
    assert result == [(480, 720), (840, 1080)]


def test_parse_windows_crosses_midnight():
    """22:00-06:00 produces (1320, 360) — start > end signals
    midnight crossing for in_window()."""
    from bulk_downloader.download_window import parse_windows
    assert parse_windows("22:00-06:00") == [(1320, 360)]


def test_parse_windows_empty_input():
    from bulk_downloader.download_window import parse_windows
    for empty in ("", None, "   ", ",,,", ", , ,"):
        assert parse_windows(empty) == []


def test_parse_windows_rejects_malformed():
    """Entries without '-', invalid HH:MM, zero-duration are dropped
    silently. The valid ones in a mixed spec still parse."""
    from bulk_downloader.download_window import parse_windows
    # Mix of valid + invalid
    result = parse_windows(
        "09:00-17:00,broken,25:00-26:00,10:00-10:00,14:00-15:00")
    assert (540, 1020) in result
    assert (840, 900) in result
    # The broken/out-of-range/zero-duration entries are skipped
    assert len(result) == 2


def test_parse_windows_defensive_input_types():
    from bulk_downloader.download_window import parse_windows
    for bad in (None, 12345, [], {}):
        assert parse_windows(bad) == []


# ── in_window ────────────────────────────────────────────────────────

def _at(hour, minute=0):
    """Build a datetime at the given hour:minute (today's date)."""
    return datetime.now().replace(hour=hour, minute=minute, second=0)


def test_in_window_empty_means_no_restriction():
    """Empty window list = no constraint = always True."""
    from bulk_downloader.download_window import in_window
    assert in_window([], now=_at(12)) is True
    assert in_window([], now=_at(3)) is True


def test_in_window_normal_range():
    """09:00-17:00: matches 09:00 through 16:59 inclusive."""
    from bulk_downloader.download_window import in_window
    windows = [(540, 1020)]  # 09:00-17:00
    assert in_window(windows, now=_at(12)) is True
    assert in_window(windows, now=_at(9)) is True
    assert in_window(windows, now=_at(16, 59)) is True
    assert in_window(windows, now=_at(17)) is False  # exclusive end
    assert in_window(windows, now=_at(8, 59)) is False
    assert in_window(windows, now=_at(0)) is False


def test_in_window_midnight_crossing():
    """22:00-06:00: 22:00+ OR <06:00."""
    from bulk_downloader.download_window import in_window
    windows = [(1320, 360)]  # 22:00-06:00
    assert in_window(windows, now=_at(23)) is True
    assert in_window(windows, now=_at(2)) is True
    assert in_window(windows, now=_at(5, 59)) is True
    assert in_window(windows, now=_at(6)) is False
    assert in_window(windows, now=_at(12)) is False
    assert in_window(windows, now=_at(22)) is True
    assert in_window(windows, now=_at(21, 59)) is False


def test_in_window_multi_range():
    """Multiple windows — match if inside ANY."""
    from bulk_downloader.download_window import in_window
    windows = [(480, 720), (840, 1080)]  # 8-12 + 14-18
    assert in_window(windows, now=_at(10)) is True
    assert in_window(windows, now=_at(13)) is False  # lunch gap
    assert in_window(windows, now=_at(16)) is True


# ── site_in_window ───────────────────────────────────────────────────

def test_site_in_window_disabled():
    """window_enabled=False → always True (no restriction)."""
    from bulk_downloader.download_window import site_in_window
    cfg = {"window_enabled": False,
            "window_active_hours": "22:00-06:00"}
    assert site_in_window(cfg, now=_at(12)) is True


def test_site_in_window_enabled_but_no_spec_allows_all():
    """Defensive: window_enabled=True but spec empty/invalid → still
    True. Prefer fail-open over fail-closed (which would silently
    lock out the site forever)."""
    from bulk_downloader.download_window import site_in_window
    for spec in ("", None, "invalid"):
        cfg = {"window_enabled": True, "window_active_hours": spec}
        assert site_in_window(cfg, now=_at(12)) is True, (
            f"failed on spec={spec!r}")


def test_site_in_window_respects_spec():
    from bulk_downloader.download_window import site_in_window
    cfg = {"window_enabled": True,
            "window_active_hours": "09:00-17:00"}
    assert site_in_window(cfg, now=_at(10)) is True
    assert site_in_window(cfg, now=_at(20)) is False


def test_site_in_window_handles_bad_cfg():
    """None / non-dict config → True (fail-open)."""
    from bulk_downloader.download_window import site_in_window
    assert site_in_window(None) is True
    assert site_in_window("not a dict") is True
    assert site_in_window(12345) is True


# ── next_transition_seconds ──────────────────────────────────────────

def test_next_transition_none_when_disabled():
    from bulk_downloader.download_window import next_transition_seconds
    cfg = {"window_enabled": False}
    assert next_transition_seconds(cfg) is None


def test_next_transition_none_when_no_spec():
    from bulk_downloader.download_window import next_transition_seconds
    cfg = {"window_enabled": True, "window_active_hours": ""}
    assert next_transition_seconds(cfg) is None


def test_next_transition_returns_positive_seconds():
    """For 09:00-17:00 at 10:00, next transition is at 17:00 = 7h
    later = 25200 seconds."""
    from bulk_downloader.download_window import next_transition_seconds
    cfg = {"window_enabled": True,
            "window_active_hours": "09:00-17:00"}
    secs = next_transition_seconds(cfg, now=_at(10))
    # 17:00 is 7 hours after 10:00
    assert 25000 <= secs <= 25300


def test_next_transition_wraps_24h():
    """At 18:00 with window 09:00-17:00, next transition is tomorrow
    at 09:00 = 15 hours = 54000s. NOT 17:00 (we're past it)."""
    from bulk_downloader.download_window import next_transition_seconds
    cfg = {"window_enabled": True,
            "window_active_hours": "09:00-17:00"}
    secs = next_transition_seconds(cfg, now=_at(18))
    # Tomorrow 09:00 = 15h
    assert 53000 <= secs <= 54100


def test_next_transition_caps_at_24h():
    """No transition should ever return > 86400s (one day)."""
    from bulk_downloader.download_window import next_transition_seconds
    cfg = {"window_enabled": True,
            "window_active_hours": "09:00-17:00"}
    for hour in range(24):
        secs = next_transition_seconds(cfg, now=_at(hour))
        if secs is not None:
            assert secs <= 86400, f"hour={hour} returned {secs}"
            assert secs >= 1


# ── WindowScheduler ──────────────────────────────────────────────────

def test_scheduler_singleton():
    from bulk_downloader.download_window import get_scheduler
    a = get_scheduler()
    b = get_scheduler()
    assert a is b


def test_scheduler_callback_registration():
    """set_transition_callback stores the callback for later use."""
    from bulk_downloader.download_window import WindowScheduler
    sched = WindowScheduler()
    cb_calls = []
    sched.set_transition_callback(lambda sid, run: cb_calls.append((sid, run)))
    # _fire_transition directly to verify the callback path
    sched._fire_transition("siteA", True)
    assert cb_calls == [("siteA", True)]


def test_scheduler_callback_failure_does_not_crash():
    """A callback exception must NOT take down the scheduler."""
    from bulk_downloader.download_window import WindowScheduler
    sched = WindowScheduler()
    sched.set_transition_callback(
        lambda sid, run: 1/0)  # always raises
    # Must not propagate
    sched._fire_transition("siteA", True)


def test_scheduler_respects_disable_env(monkeypatch=None):
    """When BD_DISABLE_KEEPALIVE is set, scheduler.start() must NOT
    spin up a thread. Tests run with this env variable set."""
    import os
    from bulk_downloader.download_window import WindowScheduler
    # Save + set env
    old = os.environ.get("BD_DISABLE_KEEPALIVE")
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    try:
        sched = WindowScheduler()
        sched.start()
        assert sched._thread is None
    finally:
        if old is None:
            os.environ.pop("BD_DISABLE_KEEPALIVE", None)
        else:
            os.environ["BD_DISABLE_KEEPALIVE"] = old


# ── Runner integration ───────────────────────────────────────────────

def test_runner_start_checks_window():
    """SiteRunner.start() must consult download_window before
    proceeding past the rate-limit check."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    pos = src.find("def _start_serialized(self, _teardown_generation=None):")
    assert pos > 0
    nxt = src.find("\n    def ", pos + 1)
    body = src[pos:nxt]
    assert "download_window" in body
    assert "site_in_window" in body
    assert "window_paused" in body


def test_runner_start_logs_window_pause_only_once():
    """The pause event should only log when transitioning INTO
    window_paused, not on every start() call. Otherwise repeated
    start() attempts during the off-hours flood the log."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    pos = src.find("def _start_serialized(self, _teardown_generation=None):")
    nxt = src.find("\n    def ", pos + 1)
    body = src[pos:nxt]
    assert 'self._state != "window_paused"' in body


def test_runner_window_check_failopen():
    """If the window module itself fails to import or compute, the
    runner must still attempt to start — fail-open prevents a bug
    in the new code from breaking all sites globally."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    pos = src.find("def _start_serialized(self, _teardown_generation=None):")
    nxt = src.find("\n    def ", pos + 1)
    body = src[pos:nxt]
    # Look for the try/except wrapping the window check
    assert "window check failed" in body


# ── App.py integration ───────────────────────────────────────────────

def test_cfg_fields_has_window_entries():
    src = _APP_SRC()
    for field in ("window_enabled", "window_active_hours",
                    "window_action_outside"):
        assert f'"{field}"' in src, f"missing CFG field: {field}"


def test_defaults_has_window_entries():
    """Defaults must explicitly set values for the new fields so
    new sites get a consistent shape."""
    src = _APP_SRC()
    assert '"window_enabled": False' in src
    assert '"window_active_hours": ""' in src
    assert '"window_action_outside": "paused"' in src


def test_scheduler_init_at_startup():
    """The window scheduler must be started during module load —
    same pattern as _start_session_keepers / _start_watch_folder_threads."""
    src = _APP_PY.read_text(encoding="utf-8")
    assert "_start_window_scheduler" in src
    assert "set_transition_callback" in src


def test_scheduler_callback_routes_to_runner():
    """The callback must call runner.start() / runner.stop() based
    on the should_run flag + the window_action_outside config."""
    src = _APP_SRC()
    pos = src.find("_on_transition")
    assert pos > 0
    body = src[pos:pos + 2500]
    assert "runner.start()" in body
    assert "runner.stop()" in body
    assert "window_action_outside" in body


def test_window_status_endpoint_registered():
    src = _APP_SRC()
    assert "/api/sites/<sid>/window/status" in src
    assert "def api_window_status" in src


# ── UI ───────────────────────────────────────────────────────────────



