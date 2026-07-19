"""v3.66.789 -- A-DISCO cut 4b: the operator manual-trigger route + FE control.

The daily ``disco.scheduled_run`` task is the autonomous path (gated by the
``auto_disco`` DAILY toggle). This cut adds an operator-facing run-now:

  * ``POST /api/discovery/disco/run`` -- CSRF-guarded manual trigger. It composes
    the SAME tested entry point the scheduler uses
    (``disco_runner.scheduled_disco``) over the live ``s_cfg`` / ``runners``, but
    FORCES a pass past the ``auto_disco`` daily toggle -- an attended, explicit
    operator action, matching the app's other run-now controls (drift_repair).
    It does NOT bypass the MASTER off-switch: that still dominates (default
    off_switch_fn = automation_controller's kill path -> inert when engaged),
    per-site ``disco.enabled`` still gates, and the bounded budget + AR4 cap
    still apply.
  * ``GET /api/discovery/disco/runs`` -- the persisted run history
    (``disco_runner.recent_runs``), so the operator can see what a trigger did.

RED on the pristine 788 tree: neither route is registered (404).
"""
import bulk_downloader.app as a
from bulk_downloader import disco_runner as dr
from bulk_downloader import disco_triage as dtr
from bulk_downloader import automation_controller as ac


# -- helpers ------------------------------------------------------------------

def _client():
    return a.app.test_client()


def _stub_csrf():
    orig = a._check_csrf
    a._check_csrf = lambda *x, **k: None
    return orig


def _restore_csrf(orig):
    a._check_csrf = orig


class _FakeRunner:
    def __init__(self):
        self.loaded = []

    def load_urls(self, urls):
        self.loaded.extend(urls)


# -- POST /api/discovery/disco/run : route exists -----------------------------

def test_manual_run_route_is_registered():
    orig = _stub_csrf()
    try:
        r = _client().post("/api/discovery/disco/run", json={})
        assert r.status_code != 404, "manual-trigger route not registered (404)"
        assert r.status_code == 200, r.get_data(as_text=True)[:200]
    finally:
        _restore_csrf(orig)


# -- force-semantics: run-now runs even with the DAILY toggle OFF --------------

def test_manual_run_forces_past_daily_toggle():
    # auto_disco is OFF by default. The DAILY scheduled path would no-op; the
    # manual run-now FORCES a pass anyway (ran=True), because it is an explicit
    # operator action. (Empty s_cfg -> sites=0, but it RAN -- not "disabled".)
    orig = _stub_csrf()
    try:
        r = _client().post("/api/discovery/disco/run", json={})
        assert r.status_code == 200, r.get_data(as_text=True)[:200]
        body = r.get_json()
        assert body.get("ran") is True, body
        assert body.get("reason") != "disabled", body
    finally:
        _restore_csrf(orig)


def test_manual_run_forces_enabled_fn_true(monkeypatch):
    # Prove the route hands scheduled_disco an enabled_fn that returns True
    # (the force), and passes the live seams.
    orig = _stub_csrf()
    seen = {}

    def _spy(*, s_cfg=None, runners=None, enabled_fn=None, **kw):
        seen["s_cfg"] = s_cfg
        seen["runners"] = runners
        seen["forced"] = bool(enabled_fn and enabled_fn())
        return {"ran": True, "reason": "ok", "sites": 1,
                "runs": [{"site_id": "s1", "enqueued": 2}]}

    monkeypatch.setattr(dr, "scheduled_disco", _spy)
    try:
        r = _client().post("/api/discovery/disco/run", json={})
        assert r.status_code == 200, r.get_data(as_text=True)[:200]
        body = r.get_json()
        assert body.get("ran") is True and body.get("sites") == 1, body
        assert "s_cfg" in seen and "runners" in seen, seen
        assert seen.get("forced") is True, "route did not force enabled_fn"
    finally:
        _restore_csrf(orig)


# -- SAFETY: the MASTER off-switch still dominates a forced manual run ---------

def test_manual_run_master_off_switch_still_dominates(monkeypatch):
    # Force is not a bypass of the kill-switch. With the master off-switch
    # engaged, an enabled site's pass is inert -> nothing is enqueued, even on a
    # manual run-now.
    orig = _stub_csrf()
    monkeypatch.setattr(ac, "off_switch_engaged", lambda: True)
    runner = _FakeRunner()
    # inject the live seams the route reads via _app_s_cfg() / _app_runners()
    import bulk_downloader.app_state as st
    monkeypatch.setattr(st, "s_cfg",
                        {"s_off": {"disco": {"enabled": True,
                                             "root_url": "https://off.example/lib"}}},
                        raising=False)
    monkeypatch.setattr(st, "runners", {"s_off": runner}, raising=False)
    try:
        r = _client().post("/api/discovery/disco/run", json={})
        assert r.status_code == 200, r.get_data(as_text=True)[:200]
        body = r.get_json()
        # ran True (forced), but the per-site pass was inert -> zero enqueued.
        assert runner.loaded == [], "master off-switch did not stop a forced run"
        for rec in body.get("runs", []):
            assert rec.get("enqueued", 0) == 0, rec
    finally:
        _restore_csrf(orig)


# -- CSRF is enforced on the POST (mutating) route ----------------------------

def test_manual_run_is_csrf_guarded():
    called = {"n": 0}
    orig = a._check_csrf

    def _boom(*x, **k):
        called["n"] += 1
        from flask import abort
        abort(403)

    a._check_csrf = _boom
    try:
        r = _client().post("/api/discovery/disco/run", json={})
        assert called["n"] >= 1, "route did not call _check_csrf"
        assert r.status_code == 403, r.status_code
    finally:
        a._check_csrf = orig


# -- GET /api/discovery/disco/runs : history route ----------------------------

def test_disco_runs_history_route_is_registered():
    r = _client().get("/api/discovery/disco/runs")
    assert r.status_code != 404, "history route not registered (404)"
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    assert isinstance(r.get_json().get("runs"), list)


def test_disco_runs_history_returns_recent(monkeypatch):
    rows = [{"id": 2, "site_id": "s1", "enqueued": 3},
            {"id": 1, "site_id": "s1", "enqueued": 0}]
    monkeypatch.setattr(dr, "recent_runs", lambda limit=50: rows)
    r = _client().get("/api/discovery/disco/runs?limit=10")
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    assert r.get_json()["runs"] == rows
