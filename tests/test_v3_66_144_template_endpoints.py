"""v3.66.144 — template visibility/onboarding endpoint tests.

Exercises GET /api/sites/<sid>/template_status and POST
/api/sites/<sid>/template_onboard against the real app via the fresh_app
test client. No capture subprocess is launched: the capture-required case
uses run=False, and approved sites never launch.
"""
from __future__ import annotations

import importlib

import pytest

import bulk_downloader.app as bd_app


REPTYLE = "https://app.reptyle.com/"
NOHOST = "https://no-such-host.example/"
LOGIN = "https://auth.example.invalid/login"
CRAWLER = "https://members.example.invalid/library"
LISTING = "https://members.example.invalid/scenes"
START = "https://members.example.invalid/start"
FINGERPRINT_SCENE = "https://cdn.example.invalid/videos/scene/"


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

def test_onboard_capture_prefers_content_url_over_login(
    fresh_app, tmp_path, monkeypatch
):
    onboard_site_template = importlib.import_module(
        "tools.onboard_site_template"
    )

    sid = "content-first"
    cfg = _seed(sid, login_url=LOGIN, crawler_listing_url=CRAWLER)
    assert cfg["login_url"] == LOGIN
    assert cfg["crawler_listing_url"] == CRAWLER
    assert LOGIN != CRAWLER
    build_calls = []
    launches = []
    info = {
        "profile_dir": str(tmp_path / "profile"),
        "wacz": str(tmp_path / "capture.wacz"),
        "draft": str(tmp_path / "capture.template-draft.json"),
        "display": ":97",
    }
    monkeypatch.setenv("DISPLAY", ":97")
    monkeypatch.setattr(
        onboard_site_template,
        "plan_site",
        lambda _cfg: {
            "template_onboarding": "capture_required",
            "template_auto_detect_mode": "capture_then_review",
            "auto_teach_first_run": False,
        },
    )

    def build_capture(site_id, url, display):
        build_calls.append((site_id, url, display))
        return dict(info)

    monkeypatch.setattr(
        onboard_site_template, "build_capture_command", build_capture
    )
    monkeypatch.setattr(
        onboard_site_template,
        "run_capture_flow",
        lambda received, *, run: launches.append((received, run)),
    )

    response = fresh_app.post(
        f"/api/sites/{sid}/template_onboard", json={"run": True}
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert build_calls == [(sid, CRAWLER, ":97")], (
        f"capture builder received {build_calls!r}"
    )
    assert launches == [(info, True)]
    assert response.get_json()["launched"] is True


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ({"crawler_listing_url": CRAWLER, "listing_url": LISTING}, CRAWLER),
        ({"listing_url": LISTING}, LISTING),
        (
            {
                "url_fingerprint": {
                    "known_hosts": ["cdn.example.invalid"],
                    "known_path_prefixes": ["/videos/scene/"],
                }
            },
            FINGERPRINT_SCENE,
        ),
        ({"start_url": START}, START),
    ],
)
def test_site_primary_url_uses_each_content_source_before_login(
    content, expected
):
    cfg = {"login_url": LOGIN, **content}
    assert expected != LOGIN
    assert len(content) > 0
    assert bd_app._site_primary_url(cfg) == expected


def test_site_primary_url_falls_back_to_login_without_content():
    cfg = {"login_url": LOGIN}
    assert len(cfg) == 1
    assert bd_app._site_primary_url(cfg) == LOGIN


def test_site_primary_url_skips_unusable_content_before_login():
    cfg = {
        "login_url": LOGIN,
        "crawler_listing_url": "javascript:alert(1)",
        "listing_url": "not-a-url",
        "url_fingerprint": {
            "known_hosts": ["cdn.example.invalid"],
            "known_path_prefixes": ["relative/scene/"],
        },
    }
    assert len(cfg["url_fingerprint"]["known_hosts"]) == 1
    assert len(cfg["url_fingerprint"]["known_path_prefixes"]) == 1
    assert bd_app._site_primary_url(cfg) == LOGIN


@pytest.mark.parametrize(
    "fingerprint",
    [
        {"known_hosts": 7, "known_path_prefixes": ["/videos/"]},
        {"known_hosts": ["cdn.example.invalid"], "known_path_prefixes": 7},
        {"known_hosts": ["."], "known_path_prefixes": ["/videos/"]},
    ],
)
def test_site_primary_url_malformed_fingerprint_falls_back_to_login(
    fingerprint,
):
    cfg = {"login_url": LOGIN, "url_fingerprint": fingerprint}
    assert len(fingerprint) == 2
    assert bd_app._site_primary_url(cfg) == LOGIN

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
