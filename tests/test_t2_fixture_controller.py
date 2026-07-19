"""T2 (Tier 3) — D-72 fixture-site controller.

SCOPE NOTE — D-76 (selftest-on-demand) was NOT built: it already
exists. selftest.run_all() is exposed at /api/selftest, and the
in-GUI on-demand test runner is /api/dev/run (dev_tools.start_run).
Building another selftest endpoint would duplicate that surface
(lesson E10), so T2 delivers only D-72.

D-72 starts/stops the tools/fixture_site*.py mock sites in-process —
an (A) state-changing tool (spawns a server thread): POST + CSRF +
dev-gated. fixture_site_status is read-only (GET).
"""
import os
import time
import urllib.request

from bulk_downloader import dev_suite as ds


# ── import-cleanliness ─────────────────────────────────────────────

def test_fixture_registry_is_empty_at_import():
    # DANGER_MAP: dev modules do no work at import. The registry is
    # just an empty dict until a start call spawns a thread.
    # (Other tests may have populated it; assert the *type/shape*.)
    assert isinstance(ds._FIXTURE_SERVERS, dict)
    assert set(ds._FIXTURE_DEFS) == {"site1", "site2"}


# ── start / serve / stop lifecycle ─────────────────────────────────

def test_fixture_site_start_serve_stop():
    # use an off-default port so the test never clashes with a real
    # hand-launched fixture
    r = ds.fixture_site_start("site1", port=18991)
    try:
        assert r["ok"] is True
        assert r["already_running"] is False
        assert r["port"] == 18991
        # the server actually answers
        time.sleep(0.4)
        body = urllib.request.urlopen(
            "http://127.0.0.1:18991/", timeout=5).read()
        assert len(body) > 0
    finally:
        s = ds.fixture_site_stop("site1")
        assert s["ok"] is True
        assert s["was_running"] is True


def test_fixture_site_start_is_idempotent():
    ds.fixture_site_start("site1", port=18992)
    try:
        again = ds.fixture_site_start("site1", port=18992)
        assert again["ok"] is True
        assert again["already_running"] is True
    finally:
        ds.fixture_site_stop("site1")


def test_fixture_site_stop_when_not_running_is_noop():
    # stopping one that was never started is a success no-op
    s = ds.fixture_site_stop("site2")
    assert s["ok"] is True
    assert s["was_running"] is False


def test_fixture_site_unknown_name_is_error():
    r = ds.fixture_site_start("site99")
    assert r["ok"] is False
    assert "unknown fixture" in r["error"]
    s = ds.fixture_site_stop("site99")
    assert s["ok"] is False


# ── status ─────────────────────────────────────────────────────────

def test_fixture_site_status_shape():
    st = ds.fixture_site_status()
    assert st["ok"] is True
    assert len(st["fixtures"]) == 2
    names = {f["name"] for f in st["fixtures"]}
    assert names == {"site1", "site2"}
    for f in st["fixtures"]:
        assert "running" in f and "default_port" in f


def test_fixture_site_status_reflects_a_running_site():
    ds.fixture_site_start("site1", port=18993)
    try:
        st = ds.fixture_site_status()
        site1 = next(f for f in st["fixtures"]
                     if f["name"] == "site1")
        assert site1["running"] is True
        assert site1["port"] == 18993
        assert st["running_count"] >= 1
    finally:
        ds.fixture_site_stop("site1")


# ── dev routes ─────────────────────────────────────────────────────

def _dev_app():
    os.environ["BD_DEV_MODE"] = "1"
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    from bulk_downloader.app import app
    return app


def test_fixture_status_route():
    r = _dev_app().test_client().get("/api/dev/fixture_site/status")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_fixture_start_stop_are_post_only():
    c = _dev_app().test_client()
    # GET on a POST route -> 405
    assert c.get("/api/dev/fixture_site/start").status_code == 405
    assert c.get("/api/dev/fixture_site/stop").status_code == 405


def test_fixture_routes_are_dev_gated():
    os.environ.pop("BD_DEV_MODE", None)
    os.environ["BD_DEV_MODE_DISABLE"] = "1"
    try:
        from bulk_downloader.app import app
        c = app.test_client()
        assert c.get(
            "/api/dev/fixture_site/status").status_code == 404
        assert c.post(
            "/api/dev/fixture_site/start").status_code == 404
    finally:
        os.environ.pop("BD_DEV_MODE_DISABLE", None)


# ── D-76 was already served — verify the existing surface exists ───

def test_d76_selftest_surface_already_exists():
    # D-76 (selftest-on-demand) was not rebuilt — confirm the
    # existing surface is present so the scope decision stays honest
    from bulk_downloader import selftest
    assert hasattr(selftest, "run_all")
    app = _dev_app()
    rules = {r.rule for r in app.url_map.iter_rules()}
    # the standing selftest endpoint and the on-demand test runner
    assert "/api/selftest" in rules
    assert "/api/dev/run" in rules
