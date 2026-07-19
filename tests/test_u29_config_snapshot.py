"""U29 — config snapshot/restore (D-90), an (A) tool. Snapshots the
live app config and restores a named snapshot.

clean_workdir gives each test an isolated cwd, so the config_snapshots/
directory is private per test.
"""
from bulk_downloader import dev_suite as ds
from bulk_downloader import app as bd_app


# ── snapshot ───────────────────────────────────────────────────────

def test_snapshot_writes_a_named_file(clean_workdir):
    r = ds.config_snapshot(name="my_snap")
    assert r["ok"] is True
    assert r["snapshot"] == "my_snap.json"
    assert r["key_count"] >= 1


def test_snapshot_auto_names_when_none_given(clean_workdir):
    r = ds.config_snapshot()
    assert r["ok"] is True
    assert r["snapshot"].startswith("snap_")
    assert r["snapshot"].endswith(".json")


def test_snapshot_rejects_a_bad_name(clean_workdir):
    for bad in ("../etc/passwd", "has spaces", "a/b", ""):
        r = ds.config_snapshot(name=bad)
        # empty falls through to auto-name; the rest must be rejected
        if bad == "":
            assert r["ok"] is True
        else:
            assert r["ok"] is False


# ── list ───────────────────────────────────────────────────────────

def test_list_reports_snapshots(clean_workdir):
    ds.config_snapshot(name="alpha")
    ds.config_snapshot(name="beta")
    r = ds.config_snapshot_list()
    assert r["ok"] is True
    names = {s["snapshot"] for s in r["snapshots"]}
    assert "alpha.json" in names and "beta.json" in names


# ── restore (round trip) ───────────────────────────────────────────

def test_snapshot_restore_round_trip(clean_workdir):
    # v3.66.6: save/restore the two module-global _app_cfg keys this
    # test mutates. Without this, downstream tests running in the same
    # interpreter (serial mode, nproc=1, or --workers=1) inherit the
    # post-test values — log_level="INFO", global_max_concurrent=3 —
    # instead of the boot defaults. Same defect class as the u28 cache
    # poisoning fixed earlier in this version.
    saved_log = bd_app._app_cfg.get("log_level")
    saved_cap = bd_app._app_cfg.get("global_max_concurrent")
    try:
        # snapshot a known state
        bd_app._app_cfg["log_level"] = "INFO"
        bd_app._app_cfg["global_max_concurrent"] = 3
        ds.config_snapshot(name="known_good")
        # change the live config
        bd_app._app_cfg["log_level"] = "DEBUG"
        bd_app._app_cfg["global_max_concurrent"] = 99
        # restore brings it back exactly
        r = ds.config_restore(name="known_good")
        assert r["ok"] is True
        assert r["restored_from"] == "known_good.json"
        assert bd_app._app_cfg["log_level"] == "INFO"
        assert bd_app._app_cfg["global_max_concurrent"] == 3
        # the merge caveat is surfaced honestly
        assert r["merge_restore"] is True
        assert "restart" in r["note"]
    finally:
        if saved_log is None:
            bd_app._app_cfg.pop("log_level", None)
        else:
            bd_app._app_cfg["log_level"] = saved_log
        if saved_cap is None:
            bd_app._app_cfg.pop("global_max_concurrent", None)
        else:
            bd_app._app_cfg["global_max_concurrent"] = saved_cap


def test_restore_missing_snapshot(clean_workdir):
    r = ds.config_restore(name="does_not_exist")
    assert r["ok"] is False
    assert "no snapshot named" in r["error"]


def test_restore_requires_a_name(clean_workdir):
    r = ds.config_restore()
    assert r["ok"] is False
    assert "required" in r["error"]


def test_restore_rejects_path_traversal(clean_workdir):
    # even a name that resolves outside config_snapshots/ is refused
    r = ds.config_restore(name="../../etc/passwd")
    assert r["ok"] is False
    assert "invalid snapshot name" in r["error"]


# ── routes ─────────────────────────────────────────────────────────

def test_snapshot_route(fresh_app):
    resp = fresh_app.post("/api/dev/config_snapshot",
                          json={"name": "route_snap"})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_snapshots_list_route(fresh_app):
    fresh_app.post("/api/dev/config_snapshot", json={"name": "ls_snap"})
    body = fresh_app.get("/api/dev/config_snapshots").get_json()
    assert body["ok"] is True
    assert body["snapshot_count"] >= 1


def test_restore_route(fresh_app):
    fresh_app.post("/api/dev/config_snapshot",
                   json={"name": "restore_route_snap"})
    resp = fresh_app.post("/api/dev/config_restore",
                          json={"name": "restore_route_snap"})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
