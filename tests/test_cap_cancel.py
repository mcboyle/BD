"""CAP-CANCEL — cancel an in-flight onboarding capture from the SPA.

The onboarding flow (POST /api/sites/<sid>/template_onboard, capture_required)
launches capture_session.py as a DETACHED subprocess with
``--finish-file <wacz>.FINISH``; capture_session derives the matching
``<wacz>.CANCEL`` sibling and discards (no WACZ written) when it appears.
That launch is fire-and-forget — no cockpit task_id — so /cockpit/api/captures/finish
(task_id-keyed) cannot reach it.

CAP-CANCEL adds:
  * POST /api/sites/<sid>/template_capture_cancel — write <wacz>.CANCEL from the
    persisted cfg["template_capture"]["wacz"], clear the marker. No raw paths in
    the response (F2 posture). The .CANCEL watch in capture_session.py (a release
    guard) is UNCHANGED — it already handles the discard.
  * capture_in_flight: bool on /template_status, so the SPA can show the control.
  * A "Cancel capture" control in SiteTemplateCard, gated on capture_in_flight,
    POSTing the FULL /api/… literal (so the parity scanner counts it spa_wired).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import bulk_downloader.app as bd_app


SPA_CARD = (
    Path(__file__).resolve().parent.parent
    / "frontend" / "src" / "components" / "SiteTemplateCard.tsx"
)
CANCEL_ROUTE_LITERAL = (
    "/api/sites/${encodeURIComponent(siteId)}/template_capture_cancel"
)


def _seed_with_capture(sid="c1"):
    d = Path(tempfile.mkdtemp())
    wacz = d / "host_c1_ts.wacz"
    bd_app.s_cfg[sid] = {
        "name": sid,
        "login_url": "https://example.test/",
        "template_onboarding": "capture_required",
        "template_capture": {
            "profile_dir": str(d / "profiles" / "c1-cloak"),
            "wacz": str(wacz),
            "draft": str(d / "host.template-draft.json"),
            "display": ":99",
        },
    }
    return sid, wacz


# --- POST /template_capture_cancel -----------------------------------------

def test_cancel_writes_sentinel_and_clears_marker(fresh_app):
    sid, wacz = _seed_with_capture()
    r = fresh_app.post(f"/api/sites/{sid}/template_capture_cancel", json={})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["ok"] is True
    assert body["cancelled"] is True
    # the .CANCEL sibling capture_session.py polls for must exist
    assert wacz.with_suffix(".CANCEL").exists()
    # the in-flight marker is cleared so the control disappears
    assert "template_capture" not in bd_app.s_cfg[sid]


def test_cancel_unknown_site_404(fresh_app):
    r = fresh_app.post("/api/sites/ghost/template_capture_cancel", json={})
    assert r.status_code == 404
    assert r.get_json()["ok"] is False


def test_cancel_no_capture_in_flight_is_graceful(fresh_app):
    bd_app.s_cfg["n1"] = {"name": "n1", "login_url": "https://example.test/"}
    r = fresh_app.post("/api/sites/n1/template_capture_cancel", json={})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["cancelled"] is False


def test_cancel_response_carries_no_path_values(fresh_app):
    sid, wacz = _seed_with_capture("c2")
    profile = bd_app.s_cfg[sid]["template_capture"]["profile_dir"]
    r = fresh_app.post(f"/api/sites/{sid}/template_capture_cancel", json={})
    raw = r.get_data(as_text=True)
    # F2: never echo capture filesystem paths back to the client
    assert str(wacz) not in raw
    assert profile not in raw


# --- GET /template_status: capture_in_flight -------------------------------

def test_status_reports_capture_in_flight(fresh_app):
    sid, _ = _seed_with_capture("c3")
    r = fresh_app.get(f"/api/sites/{sid}/template_status")
    assert r.status_code == 200
    assert r.get_json()["capture_in_flight"] is True


def test_status_no_capture_in_flight_when_idle(fresh_app):
    bd_app.s_cfg["i1"] = {"name": "i1", "login_url": "https://example.test/"}
    r = fresh_app.get("/api/sites/i1/template_status")
    assert r.status_code == 200
    assert r.get_json()["capture_in_flight"] is False


# --- SPA wiring (static scan, like test_v3_66_292) -------------------------

def test_spa_cancel_control_wired():
    src = SPA_CARD.read_text(encoding="utf-8")
    # FULL /api/ literal so tools/gui_parity_inventory counts it spa_wired
    assert CANCEL_ROUTE_LITERAL in src, "cancel route literal missing from card"
    # the control is gated on the in-flight signal
    assert "capture_in_flight" in src, "card does not read capture_in_flight"
    # a user-facing cancel affordance exists
    assert "Cancel capture" in src, "no 'Cancel capture' control in the card"
