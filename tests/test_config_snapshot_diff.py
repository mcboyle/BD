"""#3 / D-90 -- config snapshot DIFF (read-only). Diff the live app config
against a named snapshot: added / removed / changed keys, with secret values
redacted (never echoed). Read-only: writes nothing, mutates nothing.

clean_workdir isolates config_snapshots/ per test; we save/restore the module
globals we touch so serial-mode downstream tests are not poisoned.
"""
import json

from bulk_downloader import dev_suite as ds
from bulk_downloader import app as bd_app


def test_diff_detects_added_removed_changed(clean_workdir):
    saved_log = bd_app._app_cfg.get("log_level")
    try:
        bd_app._app_cfg["log_level"] = "INFO"
        bd_app._app_cfg.pop("diff_probe_added", None)
        ds.config_snapshot(name="base")
        # mutate live: change an existing key, add a new one
        bd_app._app_cfg["log_level"] = "DEBUG"
        bd_app._app_cfg["diff_probe_added"] = "new"
        r = ds.config_snapshot_diff(name="base")
        assert r["ok"] is True
        assert "log_level" in {c["key"] for c in r["changed"]}
        assert "diff_probe_added" in {a["key"] for a in r["added"]}
        assert r["identical"] is False
        assert r["summary"]["added"] >= 1 and r["summary"]["changed"] >= 1
    finally:
        bd_app._app_cfg.pop("diff_probe_added", None)
        if saved_log is not None:
            bd_app._app_cfg["log_level"] = saved_log


def test_diff_reports_removed_keys(clean_workdir):
    try:
        bd_app._app_cfg["diff_probe_removed"] = "present_at_snapshot"
        ds.config_snapshot(name="hadkey")
        del bd_app._app_cfg["diff_probe_removed"]  # gone from live
        r = ds.config_snapshot_diff(name="hadkey")
        assert r["ok"] is True
        assert "diff_probe_removed" in {x["key"] for x in r["removed"]}
    finally:
        bd_app._app_cfg.pop("diff_probe_removed", None)


def test_diff_never_echoes_secret_values(clean_workdir):
    saved = bd_app._app_cfg.get("api_token")
    try:
        bd_app._app_cfg["api_token"] = "OLD_SECRET_VALUE_AAAA"
        ds.config_snapshot(name="secbase")
        bd_app._app_cfg["api_token"] = "NEW_SECRET_VALUE_BBBB"
        r = ds.config_snapshot_diff(name="secbase")
        blob = json.dumps(r)
        assert "OLD_SECRET_VALUE_AAAA" not in blob, "snapshot secret value leaked"
        assert "NEW_SECRET_VALUE_BBBB" not in blob, "live secret value leaked"
        # the secret key still appears as a CHANGED entry, value redacted
        sec = [c for c in r["changed"] if c["key"] == "api_token"]
        assert sec and sec[0].get("secret") is True
        assert "redacted" in (str(sec[0].get("from")) + str(sec[0].get("to"))).lower()
    finally:
        if saved is None:
            bd_app._app_cfg.pop("api_token", None)
        else:
            bd_app._app_cfg["api_token"] = saved


def test_diff_identical_when_unchanged(clean_workdir):
    ds.config_snapshot(name="same")
    r = ds.config_snapshot_diff(name="same")
    assert r["ok"] is True
    assert r["identical"] is True
    assert r["summary"] == {"added": 0, "removed": 0, "changed": 0}


def test_diff_rejects_bad_name(clean_workdir):
    for bad in ("../etc/passwd", "has spaces", "a/b"):
        r = ds.config_snapshot_diff(name=bad)
        assert r["ok"] is False, f"bad name accepted: {bad!r}"


def test_diff_missing_snapshot(clean_workdir):
    r = ds.config_snapshot_diff(name="does_not_exist_xyz")
    assert r["ok"] is False
    assert "no snapshot" in r["error"].lower()


def test_diff_requires_a_name(clean_workdir):
    r = ds.config_snapshot_diff(name=None)
    assert r["ok"] is False


def test_diff_route_is_registered():
    # the read-only GET route exists and points at the diff handler
    rules = {r.rule: r for r in bd_app.app.url_map.iter_rules()}
    assert "/api/dev/config_snapshot_diff" in rules
    methods = rules["/api/dev/config_snapshot_diff"].methods
    assert "GET" in methods
