"""CAP-FINISH (#2) — finish & SAVE an in-flight onboarding capture from the SPA,
plus self-heal of a stale in-flight marker.

Companion to test_cap_cancel.py. The onboarding launch starts capture_session.py
detached with ``--finish-file <wacz>.FINISH``; it blocks in its held-open loop
until that sentinel appears, then writes the WACZ and the shell script builds the
draft. Before this there was no GUI finish -- only a second SSH shell or the
25-minute auto-save -- so the whole pipeline stalled behind an un-GUI-able step.

CAP-FINISH adds:
  * POST /api/sites/<sid>/template_capture_finish -- write <wacz>.FINISH from the
    persisted cfg["template_capture"]["wacz"] and clear the marker (no raw paths
    in the response, F2 posture).
  * /template_status self-heals a stale marker: when the pipeline's draft exists
    (auto-save / SIGTERM / out-of-band finish), capture_in_flight resolves to
    False instead of sticking forever.
  * A "Finish & Save" control in SiteTemplateCard, POSTing the FULL /api/…
    literal so the parity scanner counts it spa_wired.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import bulk_downloader.app as bd_app


SPA_CARD = (
    Path(__file__).resolve().parent.parent
    / "frontend" / "src" / "components" / "SiteTemplateCard.tsx"
)
FINISH_ROUTE_LITERAL = (
    "/api/sites/${encodeURIComponent(siteId)}/template_capture_finish"
)


def _seed_with_capture(sid="f1", *, make_draft=False):
    d = Path(tempfile.mkdtemp())
    wacz = d / "host_f1_ts.wacz"
    draft = d / "host.template-draft.json"
    if make_draft:
        draft.write_text("{}", encoding="utf-8")
    bd_app.s_cfg[sid] = {
        "name": sid,
        "login_url": "https://example.test/",
        "template_onboarding": "capture_required",
        "template_capture": {
            "profile_dir": str(d / "profiles" / "f1-cloak"),
            "wacz": str(wacz),
            "draft": str(draft),
            "display": ":99",
        },
    }
    return sid, wacz, draft


# --- POST /template_capture_finish -----------------------------------------

def test_finish_writes_finish_sentinel_and_clears_marker(fresh_app):
    sid, wacz, _ = _seed_with_capture()
    r = fresh_app.post(f"/api/sites/{sid}/template_capture_finish", json={})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["ok"] is True
    assert body["finished"] is True
    # the .FINISH sibling capture_session.py is blocked waiting on must exist
    assert wacz.with_suffix(".FINISH").exists()
    # and the SAVE path must NOT have written a discard sentinel
    assert not wacz.with_suffix(".CANCEL").exists()
    # the in-flight marker is cleared so the control resolves
    assert "template_capture" not in bd_app.s_cfg[sid]


def test_finish_unknown_site_404(fresh_app):
    r = fresh_app.post("/api/sites/ghost/template_capture_finish", json={})
    assert r.status_code == 404
    assert r.get_json()["ok"] is False


def test_finish_no_capture_in_flight_is_graceful(fresh_app):
    bd_app.s_cfg["nf1"] = {"name": "nf1", "login_url": "https://example.test/"}
    r = fresh_app.post("/api/sites/nf1/template_capture_finish", json={})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["finished"] is False


def test_finish_response_carries_no_path_values(fresh_app):
    sid, wacz, _ = _seed_with_capture("f2")
    profile = bd_app.s_cfg[sid]["template_capture"]["profile_dir"]
    r = fresh_app.post(f"/api/sites/{sid}/template_capture_finish", json={})
    raw = r.get_data(as_text=True)
    assert str(wacz) not in raw
    assert profile not in raw


# --- GET /template_status: self-heal of a stale marker ---------------------

def test_status_selfheals_stale_marker_when_draft_exists(fresh_app):
    # Pipeline finished out-of-band (auto-save / SIGTERM): draft written but the
    # marker was never cleared. Status must reconcile to not-in-flight.
    sid, _, _draft = _seed_with_capture("f3", make_draft=True)
    r = fresh_app.get(f"/api/sites/{sid}/template_status")
    assert r.status_code == 200
    assert r.get_json()["capture_in_flight"] is False
    # and the stale marker is actually cleared, not just hidden
    assert "template_capture" not in bd_app.s_cfg[sid]


def test_status_still_in_flight_while_draft_absent(fresh_app):
    # Capture genuinely running: no draft yet -> marker stays, control persists.
    sid, _, _ = _seed_with_capture("f4", make_draft=False)
    r = fresh_app.get(f"/api/sites/{sid}/template_status")
    assert r.status_code == 200
    assert r.get_json()["capture_in_flight"] is True
    assert "template_capture" in bd_app.s_cfg[sid]


# --- SPA wiring (static scan, like test_cap_cancel) ------------------------

def test_spa_finish_control_wired():
    src = SPA_CARD.read_text(encoding="utf-8")
    # FULL /api/ literal so tools/gui_parity_inventory counts it spa_wired
    assert FINISH_ROUTE_LITERAL in src, "finish route literal missing from card"
    assert "capture_in_flight" in src, "card does not read capture_in_flight"
    assert "Finish & Save" in src, "no 'Finish & Save' control in the card"
