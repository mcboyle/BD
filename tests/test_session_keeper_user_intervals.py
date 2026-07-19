"""v3.64.2: user-assignable session_keeper intervals.

Three new keys in app_config.json control how often the keep-alive
subsystem refreshes a login session:

    session_keep_alive_lead_time_min
    session_keep_alive_fetch_interval_min
    session_keep_alive_navigate_interval_min

All values are minutes, validated to [1, 360]. Module-level constants
remain as fallbacks (REFRESH_LEAD_TIME_SEC etc.) — these are read only
when global_config doesn't have a valid override.

Read path: helpers call global_config.get_config() on EACH invocation,
which is cheap because global_config caches by mtime. This is what
lets the operator edit the value in Settings and have the keeper pick
it up on the next scheduler iteration without a service restart.
"""
from __future__ import annotations


def test_fetch_interval_defaults_to_module_constant(monkeypatch):
    """Empty config → falls through to FETCH_HEARTBEAT_INTERVAL_SEC."""
    from bulk_downloader import session_keeper as sk
    from bulk_downloader import global_config as gc

    monkeypatch.setattr(gc, "get_config", lambda: {})
    assert sk._fetch_interval_sec() == sk.FETCH_HEARTBEAT_INTERVAL_SEC


def test_fetch_interval_respects_user_override(monkeypatch):
    """Config sets 15 min → helper returns 15 * 60 seconds."""
    from bulk_downloader import session_keeper as sk
    from bulk_downloader import global_config as gc

    monkeypatch.setattr(
        gc, "get_config",
        lambda: {"session_keep_alive_fetch_interval_min": 15})
    assert sk._fetch_interval_sec() == 15 * 60


def test_navigate_interval_respects_user_override(monkeypatch):
    from bulk_downloader import session_keeper as sk
    from bulk_downloader import global_config as gc

    monkeypatch.setattr(
        gc, "get_config",
        lambda: {"session_keep_alive_navigate_interval_min": 90})
    assert sk._navigate_interval_sec() == 90 * 60


def test_lead_time_respects_user_override(monkeypatch):
    from bulk_downloader import session_keeper as sk
    from bulk_downloader import global_config as gc

    monkeypatch.setattr(
        gc, "get_config",
        lambda: {"session_keep_alive_lead_time_min": 45})
    assert sk._lead_time_sec() == 45 * 60


def test_out_of_range_falls_back_to_constant(monkeypatch):
    """Values outside [1, 360] are silently dropped — same band as the
    backend POST handler enforces. This is the safety net for a stale
    config from before the validation was added."""
    from bulk_downloader import session_keeper as sk
    from bulk_downloader import global_config as gc

    # Above 360
    monkeypatch.setattr(
        gc, "get_config",
        lambda: {"session_keep_alive_fetch_interval_min": 9999})
    assert sk._fetch_interval_sec() == sk.FETCH_HEARTBEAT_INTERVAL_SEC

    # Zero (would busy-loop the keeper)
    monkeypatch.setattr(
        gc, "get_config",
        lambda: {"session_keep_alive_fetch_interval_min": 0})
    assert sk._fetch_interval_sec() == sk.FETCH_HEARTBEAT_INTERVAL_SEC

    # Negative
    monkeypatch.setattr(
        gc, "get_config",
        lambda: {"session_keep_alive_fetch_interval_min": -5})
    assert sk._fetch_interval_sec() == sk.FETCH_HEARTBEAT_INTERVAL_SEC


def test_non_numeric_falls_back_to_constant(monkeypatch):
    """A string value (e.g. someone hand-edited the JSON wrong) doesn't
    crash the keeper — falls back silently. The subsystem must never
    crash because of a config edit."""
    from bulk_downloader import session_keeper as sk
    from bulk_downloader import global_config as gc

    monkeypatch.setattr(
        gc, "get_config",
        lambda: {"session_keep_alive_fetch_interval_min": "thirty"})
    assert sk._fetch_interval_sec() == sk.FETCH_HEARTBEAT_INTERVAL_SEC


def test_global_config_import_failure_falls_back(monkeypatch):
    """If global_config itself raises on import (extreme paranoia case),
    the helper still returns the fallback. Without this guard a corrupt
    config file would take the whole keep-alive subsystem down."""
    from bulk_downloader import session_keeper as sk
    from bulk_downloader import global_config as gc

    def _raise():
        raise RuntimeError("simulated read failure")

    monkeypatch.setattr(gc, "get_config", _raise)
    assert sk._fetch_interval_sec() == sk.FETCH_HEARTBEAT_INTERVAL_SEC
    assert sk._navigate_interval_sec() == sk.NAVIGATE_HEARTBEAT_INTERVAL_SEC
    assert sk._lead_time_sec() == sk.REFRESH_LEAD_TIME_SEC


def test_module_constants_are_30_minutes():
    """The defaults shipped in v3.64.2 are 30/30/30 minutes. Raised
    from 10/5/30 once the persistent Chromium context made the
    aggressive 5-minute fetch unnecessary. This test pins the defaults
    so a future refactor that 'simplifies' them doesn't quietly
    revert."""
    from bulk_downloader import session_keeper as sk

    assert sk.REFRESH_LEAD_TIME_SEC == 30 * 60
    assert sk.FETCH_HEARTBEAT_INTERVAL_SEC == 30 * 60
    assert sk.NAVIGATE_HEARTBEAT_INTERVAL_SEC == 30 * 60
