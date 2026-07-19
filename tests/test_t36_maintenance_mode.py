"""T36 (Tier 4) — D-122 maintenance-mode switch.

Read-side: bulk_downloader.dev_suite.maintenance_mode_status()
Mutating: bulk_downloader.maintenance.add_window_now() and
          bulk_downloader.maintenance.end_active_overrides()

The mutating side rides on the existing maintenance-windows
subsystem — an immediate override is a one-shot window starting
now, labelled with _OVERRIDE_LABEL_PREFIX so the toggle can find it.
"""
import json

from bulk_downloader import dev_suite as ds
from bulk_downloader import maintenance as mw


# Each test runs in its own clean_workdir → fresh INSTALL_DIR → fresh
# SQLite DB. The maintenance_windows table is created on demand.

def test_maintenance_status_when_no_windows(clean_workdir):
    r = ds.maintenance_mode_status()
    assert r["ok"] is True
    assert r["tool"] == "maintenance_mode_status"
    assert r["active_windows"] == []
    assert r["paused_actions"] == []


def test_maintenance_status_shape(clean_workdir):
    r = ds.maintenance_mode_status()
    # All four expected keys are present with correct types
    assert isinstance(r["active_windows"], list)
    assert isinstance(r["all_windows"], list)
    assert isinstance(r["paused_actions"], list)
    assert isinstance(r["verdict"], str)


def test_add_window_now_creates_active_override(clean_workdir):
    wid = mw.add_window_now(actions_paused=["workers"], note="test")
    assert wid is not None
    assert isinstance(wid, int)
    active = mw.list_active_overrides()
    assert len(active) == 1
    assert active[0]["id"] == wid


def test_add_window_now_default_pauses_workers(clean_workdir):
    wid = mw.add_window_now()
    assert wid is not None
    active = mw.list_active_overrides()
    assert "workers" in active[0]["actions_paused"]


def test_add_window_now_custom_actions(clean_workdir):
    wid = mw.add_window_now(actions_paused=["workers", "discovery"])
    assert wid is not None
    active = mw.list_active_overrides()
    paused = active[0]["actions_paused"]
    assert "workers" in paused
    assert "discovery" in paused


def test_maintenance_status_reports_active_override(clean_workdir):
    mw.add_window_now(actions_paused=["workers"], note="ops")
    r = ds.maintenance_mode_status()
    assert len(r["active_windows"]) >= 1
    assert "workers" in r["paused_actions"]


def test_end_active_overrides_removes_them(clean_workdir):
    wid1 = mw.add_window_now(note="first")
    wid2 = mw.add_window_now(note="second")
    assert wid1 is not None and wid2 is not None
    removed = mw.end_active_overrides()
    assert removed == 2
    assert mw.list_active_overrides() == []


def test_end_active_overrides_returns_zero_when_none(clean_workdir):
    removed = mw.end_active_overrides()
    assert removed == 0


def test_end_active_overrides_leaves_real_windows_alone(clean_workdir):
    # A real scheduled window (not an override) — created via
    # add_window directly. Must survive end_active_overrides.
    import datetime
    now = datetime.datetime.now()
    later = now + datetime.timedelta(hours=1)
    wid_real = mw.add_window(
        label="weekly-backup",
        start_iso=now.isoformat(timespec="seconds"),
        end_iso=later.isoformat(timespec="seconds"),
    )
    wid_override = mw.add_window_now(note="manual")
    assert wid_real is not None
    assert wid_override is not None
    removed = mw.end_active_overrides()
    assert removed == 1  # only the override
    remaining = mw.list_windows(include_past=False)
    labels = [w["label"] for w in remaining]
    assert "weekly-backup" in labels


def test_override_label_carries_note(clean_workdir):
    mw.add_window_now(note="db-maintenance")
    active = mw.list_active_overrides()
    assert "db-maintenance" in active[0]["label"]


def test_status_is_json_serializable(clean_workdir):
    mw.add_window_now(note="x")
    r = ds.maintenance_mode_status()
    json.dumps(r, default=str)
