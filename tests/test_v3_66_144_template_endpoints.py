"""v3.66.144 — template visibility/onboarding endpoint tests.

Exercises GET /api/sites/<sid>/template_status and POST
/api/sites/<sid>/template_onboard against the real app via the fresh_app
test client. No capture subprocess is launched: the capture-required case
uses run=False, and approved sites never launch.
"""
from __future__ import annotations

import bulk_downloader.app as bd_app


REPTYLE = "https://app.reptyle.com/"
NOHOST = "https://no-such-host.example/"


def _seed(sid, **cfg):
    cfg.setdefault("name", sid)
    bd_app.s_cfg[sid] = cfg
    return cfg


# --- GET /template_status --------------------------------------------------

def test_status_reports_enabled_reviewed_template(fresh_app):
    _seed("r1", login_url=REPTYLE)
    r = fresh_app.get("/api/sites/r1/template_status")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["template"]["enabled"] is True
    assert body["template"]["host"] == "app.reptyle.com"
    assert "enabled" in body["label"]
    assert 2160 in body["template"]["resolutions"]


def test_status_unknown_site_404(fresh_app):
    r = fresh_app.get("/api/sites/ghost/template_status")
    assert r.status_code == 404
    assert r.get_json()["ok"] is False


def test_status_no_template_for_unknown_host(fresh_app):
    _seed("x1", login_url=NOHOST)
    r = fresh_app.get("/api/sites/x1/template_status")
    assert r.status_code == 200
    body = r.get_json()
    assert body["template"]["enabled"] is False
    assert body["label"] == "No reviewed template"


# --- POST /template_onboard ------------------------------------------------

def test_onboard_capture_required_does_not_launch(fresh_app):
    _seed("x2", login_url=NOHOST)
    r = fresh_app.post("/api/sites/x2/template_onboard", json={"run": False})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["template_onboarding"] == "capture_required"
    assert body["auto_teach_first_run"] is False
    assert body["launched"] is False
    # persisted onto the in-memory config
    assert bd_app.s_cfg["x2"]["template_onboarding"] == "capture_required"
    assert bd_app.s_cfg["x2"]["auto_teach_first_run"] is False


def test_onboard_approved_template_never_launches(fresh_app):
    _seed("r2", login_url=REPTYLE)
    # run defaults to true, but an approved site must not launch a capture.
    r = fresh_app.post("/api/sites/r2/template_onboard", json={})
    assert r.status_code == 200
    body = r.get_json()
    assert body["template_onboarding"] == "approved_template_found"
    assert body["template_auto_detect_mode"] == "reviewed"
    assert body["auto_teach_first_run"] is False
    assert body["launched"] is False


def test_onboard_unknown_site_404(fresh_app):
    r = fresh_app.post("/api/sites/ghost/template_onboard", json={"run": False})
    assert r.status_code == 404
