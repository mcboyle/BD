"""q-import-export (O1789 bughunt queue-templates-io, finder bd-worker-S34-D): M2 M3 L1 L3 L8 L6.

M2  export then import(mode=replace) brought a cancelled ("stopped") row back as pending -> it downloads again.
M3  POST /api/sites/import never ran _validate_config_paths: download_dir=/etc/cron.d was stored while
    POST /api/sites refuses the same value ("not under any configured allowlist root").
L1  POST /api/sites/<sid>/queue_url {"urls": 123} -> 500 TypeError.
L3  queue import treated force_download "false" as true.
L8  sites/import with "_had_secrets": true -> 500 (join on a bool); GET /api/export/json?limit=abc -> 500 whose
    body carried the ValueError text.
L6  (ADD, part 1) GET /api/sites/<unknown>/queue/search -> 200; export gives 404.

Real Flask test client on the isolated BD_HOME the conftest autouse fixture provides; no network.
"""

import sys

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

A = "https://example.com/video/qie-a"
B = "https://example.com/video/qie-b"


@pytest.fixture(scope="module", autouse=True)
def _restore_bd_modules_after_file():
    """Put the bulk_downloader module table back when this file finishes (1034 leaker census)."""
    saved_modules = {
        m: mod
        for m, mod in sys.modules.items()
        if m == "bulk_downloader" or m.startswith("bulk_downloader.")
    }
    try:
        yield
    finally:
        for m in [
            m
            for m in sys.modules
            if m == "bulk_downloader" or m.startswith("bulk_downloader.")
        ]:
            del sys.modules[m]
        sys.modules.update(saved_modules)


def _setup():
    for mod in list(sys.modules):
        if mod.startswith("bulk_downloader"):
            del sys.modules[mod]
    from bulk_downloader import app as a

    c = a.app.test_client()
    sid = c.post("/api/sites", json={"name": "QieSite"}).get_json()["id"]
    return a, c, sid


def _db_rows(c, sid):
    """The persisted queue, read back through the export route (db.queue_load)."""
    return {
        r["url"]: r for r in c.get(f"/api/sites/{sid}/queue/export").get_json()["rows"]
    }


def _db_status(c, sid):
    return {u: r["status"] for u, r in _db_rows(c, sid).items()}


def test_m2_cancelled_row_survives_export_then_replace_import():
    a, c, sid = _setup()
    runner = a.runners[sid]
    added = c.post(f"/api/sites/{sid}/queue_url", json={"urls": [A, B]}).get_json()
    assert added["added"] == 2, added
    cancel = c.post("/api/queue/v2/cancel", json={"site_id": sid, "url": A})
    assert cancel.status_code == 200, cancel.get_json()
    export = c.get(f"/api/sites/{sid}/queue/export").get_json()
    by_url = {r["url"]: r for r in export["rows"]}
    assert by_url[A]["status"] == "stopped", export  # the finding's precondition
    assert by_url[B]["status"] == "pending", export

    resp = c.post(
        f"/api/sites/{sid}/queue/import",
        json={"rows": export["rows"], "mode": "replace"},
    )
    assert resp.status_code == 200, resp.get_json()
    assert runner.jobs[A]["status"] == "stopped", (
        f"M2: cancelled row came back {runner.jobs[A]['status']!r} after import"
    )
    assert runner.jobs[A]["message"] == by_url[A]["message"]
    assert runner.jobs[B]["status"] == "pending"  # control: a pending row stays pending
    assert _db_status(c, sid) == {A: "stopped", B: "pending"}


def test_m2_settled_statuses_restore_and_active_ones_import_pending():
    a, c, sid = _setup()
    runner = a.runners[sid]
    rows = [
        {
            "url": f"https://example.com/video/qie-{s}",
            "status": s,
            "message": f"was {s}",
        }
        for s in ("done", "failed", "needs_review", "running", "pending", "bogus")
    ]
    rows.append({"url": "https://example.com/video/qie-nomsg", "status": "stopped"})
    resp = c.post(
        f"/api/sites/{sid}/queue/import", json={"rows": rows, "mode": "append"}
    )
    assert resp.status_code == 200 and resp.get_json()["added"] == 7, resp.get_json()
    got = {u.rsplit("-", 1)[1]: j["status"] for u, j in runner.jobs.items()}
    assert got == {
        "done": "done",
        "failed": "failed",
        "needs_review": "needs_review",
        "running": "pending",
        "pending": "pending",
        "bogus": "pending",
        "nomsg": "stopped",
    }, got
    assert runner.jobs["https://example.com/video/qie-done"]["message"] == "was done"
    assert (
        runner.jobs["https://example.com/video/qie-nomsg"]["message"]
        == "Restored by queue import"
    )
    assert _db_status(c, sid)["https://example.com/video/qie-failed"] == "failed"
    db_msg = {u: r["message"] for u, r in _db_rows(c, sid).items()}
    # a restored "done" keeps its own message, not the folder-scan one
    assert db_msg["https://example.com/video/qie-done"] == "was done", db_msg


def test_l3_force_download_string_false_is_off():
    a, c, sid = _setup()
    runner = a.runners[sid]
    flags = {
        "sf": "false",
        "s0": "0",
        "se": "",
        "n0": 0,
        "st": "true",
        "s1": "1",
        "bt": True,
    }
    rows = [
        {"url": f"https://example.com/video/qie-{k}", "force_download": v}
        for k, v in flags.items()
    ]
    resp = c.post(f"/api/sites/{sid}/queue/import", json={"rows": rows})
    assert resp.status_code == 200, resp.get_json()
    forced = {
        u.rsplit("-", 1)[1] for u, j in runner.jobs.items() if j.get("force_download")
    }
    assert forced == {"st", "s1", "bt"}, f"L3: forced rows {sorted(forced)}"


def test_l1_queue_url_non_list_urls_is_a_400():
    _a, c, sid = _setup()
    resp = c.post(f"/api/sites/{sid}/queue_url", json={"urls": 123})
    assert resp.status_code == 400, (
        resp.status_code,
        resp.get_data(as_text=True)[:200],
    )
    assert resp.get_json()["error"] == "urls must be a list"
    ok = c.post(f"/api/sites/{sid}/queue_url", json={"urls": [A]})  # control
    assert ok.status_code == 200 and ok.get_json()["added"] == 1


def test_m3_sites_import_applies_the_download_dir_allowlist(monkeypatch, tmp_path):
    a, c, _ = _setup()
    monkeypatch.setitem(a._app_cfg, "path_allowlist", [str(tmp_path)])
    # control: the create route already refuses it
    add = c.post("/api/sites", json={"name": "QieAdd", "download_dir": "/etc/cron.d"})
    assert add.status_code == 400 and "allowlist" in add.get_data(as_text=True)
    before = set(a.runners)
    resp = c.post(
        "/api/sites/import",
        json={"config": {"name": "QieImp", "download_dir": "/etc/cron.d"}},
    )
    assert resp.status_code == 400, f"M3: import stored download_dir: {resp.get_json()}"
    assert any("allowlist" in e for e in resp.get_json()["errors"]), resp.get_json()
    assert set(a.runners) == before, "a refused import must not create a site"
    inside = c.post(
        "/api/sites/import",
        json={"config": {"name": "QieOk", "download_dir": str(tmp_path / "dl")}},
    )
    assert inside.status_code == 200, inside.get_json()
    assert a.s_cfg[inside.get_json()["id"]]["download_dir"] == str(tmp_path / "dl")


def test_l8_had_secrets_of_any_shape_is_not_a_500():
    _a, c, _ = _setup()
    bad = c.post(
        "/api/sites/import", json={"config": {"name": "QieS1"}, "_had_secrets": True}
    )
    assert bad.status_code == 200, (bad.status_code, bad.get_data(as_text=True)[:200])
    assert any("unspecified fields" in w for w in bad.get_json()["warnings"])
    listed = c.post(
        "/api/sites/import",
        json={"config": {"name": "QieS2"}, "_had_secrets": ["password"]},
    )
    assert listed.status_code == 200
    assert any("removed: password." in w for w in listed.get_json()["warnings"])


@pytest.mark.parametrize("route", ["json", "csv", "m3u"])
def test_l8_export_bad_limit_is_a_400_without_the_exception_text(route):
    _a, c, _ = _setup()
    resp = c.get(f"/api/export/{route}?limit=abc")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 400, (resp.status_code, body[:200])
    assert resp.get_json() == {"error": "limit must be an integer"}
    assert "invalid literal" not in body
    assert c.get(f"/api/export/{route}?limit=5").status_code == 200  # control


def test_l6_queue_search_unknown_site_is_a_404():
    _a, c, sid = _setup()
    resp = c.get("/api/sites/qie-no-such-site/queue/search?q=x")
    assert resp.status_code == 404, (
        resp.status_code,
        resp.get_data(as_text=True)[:200],
    )
    assert resp.get_json() == {"error": "Not found"}
    assert (
        c.get("/api/sites/qie-no-such-site/queue/export").status_code == 404
    )  # parity
    ok = c.get(f"/api/sites/{sid}/queue/search?q=x")  # control: a real site
    assert ok.status_code == 200 and ok.get_json()["ok"] is True
