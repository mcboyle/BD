"""Fixture tests for the read-only capture HUD (F2.7, bulk_downloader/dom_overlay).

Zero-arg test functions per run_tests.py conventions; repo root via __file__.
The HUD's five panels are pure functions of a capture dict, so the whole pure
half is exercised here with no browser. The live half (inject_overlay) is
proven against a stub page object; the real on-page mount is stash-only.
"""
import importlib.util
import json
import os
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

os.environ.setdefault("BD_HOME", tempfile.mkdtemp(prefix="bd_hud_"))
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")


def _load():
    import sys
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from bulk_downloader import dom_overlay as mod
    return mod


# ── fixtures ──────────────────────────────────────────────────────

def _net(url, status=200, method="GET", ct=None, ts=None):
    headers = [["content-type", ct]] if ct else []
    e = {"url": url, "response_status": status, "method": method,
         "response_headers": headers}
    if ts is not None:
        e["timestamp"] = ts
    return e


def _cap_thin():
    return {"host": "thin.example", "network_log": [], "network_log_count": 0}


def _cap_partial():
    nl = [_net("https://api.example/v1/info", ts=1000),
          _net("https://api.example/v1/list", ts=1500)]
    return {"host": "api.example", "network_log": nl, "network_log_count": len(nl)}


def _cap_ready():
    nl = [
        _net("https://cdn.example/master.m3u8", ct="application/x-mpegurl", ts=100),
        _net("https://cdn.example/seg0.ts", ts=200),
        _net("https://cdn.example/seg1.ts", ts=300),
        _net("https://cdn.example/seg2.ts", ts=400),
    ]
    return {"host": "cdn.example", "network_log": nl, "network_log_count": len(nl)}


def _cap_direct():
    # A direct progressive MP4 download (JWPlayer / direct-download site like
    # WowGirls) — no HLS/DASH ladder, just the signed media file. Reads as
    # "media N (0 seg)" in the wild.
    nl = [_net("https://cdn.example/video/clip-60fps.mp4?sig=abc", ts=100)]
    return {"host": "cdn.example", "network_log": nl, "network_log_count": 1}


def _cap_drm_only():
    nl = [_net("https://cdn.example/video.mp4?widevine=1&licenseUrl=x", ts=10)]
    return {"host": "cdn.example", "network_log": nl, "network_log_count": 1}


def _cap_challenged():
    return {
        "host": "walled.example",
        "network_log": [_net("https://walled.example/challenge", status=403)],
        "network_log_count": 1,
        "fingerprint_detection": {
            "fingerprinting_detected": True,
            "vendors": [{"vendor": "cloudflare", "tells": ["header:cf-ray"],
                         "first_seen_url": "https://walled.example/"}],
            "fp_echo_headers": [],
            "challenges": [{"url": "https://walled.example/challenge",
                            "status": 403, "reason": "interstitial"}],
            "summary": "challenge present",
            "requests_scanned": 1,
        },
    }


_GOOD_STATUS = {"rrweb_present": True, "rrweb_bytes": 1, "snapdom_present": True,
                "snapdom_bytes": 1, "dom_events_dropped": 0, "arm_fail_streak": 0}


# ── pure-panel tests ──────────────────────────────────────────────

def test_panels_shape_and_keys():
    m = _load()
    panels = m.hud_panels(_cap_ready(), _GOOD_STATUS)
    assert set(panels.keys()) == set(m.PANEL_ORDER)
    assert len(m.PANEL_ORDER) == 5


def test_session_panel_counts_and_span():
    m = _load()
    s = m.session_panel(_cap_ready())
    assert s["host"] == "cdn.example"
    assert s["requests"] == 4
    assert s["websockets"] == 0
    assert s["redacted"] is True
    assert s["span_ms"] == 300  # 400 - 100


def test_media_panel_manifest_and_segments():
    m = _load()
    md = m.media_panel(_cap_ready())
    assert md["hls_manifests"] == 1
    assert md["segments"] == 3
    assert md["drm"] == 0


def test_media_panel_drm_counted():
    m = _load()
    md = m.media_panel(_cap_drm_only())
    assert md["drm"] == 1
    assert md["media_total"] == 1


def test_dom_panel_badges():
    m = _load()
    cap = _cap_ready()
    assert m.dom_panel(cap, _GOOD_STATUS)["badge"] == "ok"
    assert m.dom_panel(cap, None)["badge"] == "unknown"
    degraded = dict(_GOOD_STATUS, dom_events_dropped=5)
    assert m.dom_panel(cap, degraded)["badge"] == "degraded"
    missing = dict(_GOOD_STATUS, snapdom_present=False)
    assert m.dom_panel(cap, missing)["badge"] == "error"


def test_risk_panel_presence_only():
    m = _load()
    r = m.risk_panel(_cap_challenged())
    assert r["fingerprinting_detected"] is True
    assert r["vendor_count"] == 1
    assert r["vendors"] == ["cloudflare"]
    assert r["challenge_count"] == 1
    # clean capture → no risk
    assert m.risk_panel(_cap_ready())["challenge_count"] == 0


def test_readiness_tiers():
    m = _load()
    assert m.readiness_panel(_cap_thin())["tier"] == m.TIER_THIN
    assert m.readiness_panel(_cap_partial())["tier"] == m.TIER_PARTIAL
    assert m.readiness_panel(_cap_ready(), _GOOD_STATUS)["tier"] == m.TIER_READY
    assert m.readiness_panel(_cap_drm_only())["tier"] == m.TIER_BLOCKED
    assert m.readiness_panel(_cap_challenged())["tier"] == m.TIER_BLOCKED


def test_readiness_direct_media_only_is_ready():
    """v3.66.239: a direct-media download (a signed progressive MP4, no HLS/DASH
    ladder — JWPlayer/direct-download sites) reads READY, not PARTIAL. Mirrors
    verify_summary's has_direct so the top badge and the finish-bar agree;
    previously ladder-only, so a fully-captured direct download mislabelled
    PARTIAL ('no manifest/segment ladder yet')."""
    m = _load()
    r = m.readiness_panel(_cap_direct(), _GOOD_STATUS)
    assert r["tier"] == m.TIER_READY, r
    assert "direct media" in r["note"], r
    # the drm-only direct file must still read BLOCKED, not ready (positive control)
    assert m.readiness_panel(_cap_drm_only())["tier"] == m.TIER_BLOCKED


def test_panels_pure_no_mutation():
    m = _load()
    cap = _cap_ready()
    before = json.dumps(cap, sort_keys=True)
    m.hud_panels(cap, _GOOD_STATUS)
    assert json.dumps(cap, sort_keys=True) == before


# ── payload / serialisation / leak tests ──────────────────────────

def test_payload_round_trips_json():
    m = _load()
    for cap in (_cap_thin(), _cap_partial(), _cap_ready(),
                _cap_drm_only(), _cap_challenged()):
        payload = m.hud_payload(cap, _GOOD_STATUS)
        s = json.dumps(payload)            # must not raise
        assert json.loads(s) == payload
        assert payload["order"] == list(m.PANEL_ORDER)


def test_payload_leaks_no_urls_or_secrets():
    """F2 posture: the payload carries kinds+counts+host, never a media/secret
    URL. The fixtures use distinctive URL tokens that must NOT appear."""
    m = _load()
    needles = ("http://", "https://", ".m3u8", ".ts", "widevine",
               "licenseUrl", "/challenge", "seg0", "master")
    for cap in (_cap_ready(), _cap_drm_only(), _cap_challenged()):
        blob = json.dumps(m.hud_payload(cap, _GOOD_STATUS))
        for n in needles:
            assert n not in blob, "leaked %r in HUD payload" % n


# ── overlay_js / injector tests ───────────────────────────────────

def test_overlay_js_is_self_contained_shadow_widget():
    m = _load()
    js = m.overlay_js(m.hud_payload(_cap_ready(), _GOOD_STATUS))
    assert "attachShadow" in js
    # SELF-HEALING guard (v3.66.230.x): the widget keys on DOM PRESENCE, not a
    # window flag, so it re-mounts whenever the host is wiped (SPA re-render /
    # nav) and refreshes the panel values in place when the host is still there.
    # The old single-mount ``window.__bd_hud_mounted`` guard could never recover
    # or refresh once set — assert it is gone and the new shape is present.
    assert "__bd_hud_mounted" not in js      # the broken once-only guard is gone
    assert "querySelector('[data-bd-hud]')" in js  # presence check -> re-mount when missing
    assert "__bd_hud_box" in js              # retained ref -> in-place data refresh
    assert "mode:'closed'" in js             # closed Shadow root (WACZ-safe marker only)
    assert js.strip().startswith("(function()")
    assert js.rstrip().endswith("})();")
    # JSON literal embedded; explicit ; terminators (no reliance on ASI).
    assert '"panels":' in js
    assert "fetch(" not in js                # render-only, no network


def test_overlay_host_is_viewport_fixed_not_clobbered_by_all_initial():
    """Regression (v3.66.234.x): the HUD host must stay pinned to the viewport.

    The host needs ``all:initial`` to isolate it from page CSS AND
    ``position:fixed`` to stay put on scroll/nav. Source order matters in a
    single cssText: if ``all:initial`` comes LAST it resets position/top/
    right/z-index back to initial (position:static), dropping the HUD into
    normal document flow -> it scrolls away, is covered, and lands top-left
    instead of top-right. Assert the corrected order and reject the broken one.
    """
    m = _load()
    js = m.overlay_js(m.hud_payload(_cap_ready(), _GOOD_STATUS))
    assert "all:initial;position:fixed" in js          # reset first, then position wins
    assert "z-index:2147483647;all:initial" not in js  # positive control: broken order absent
    # all:initial must precede position/z-index in the host declaration
    i_all = js.find("all:initial")
    i_pos = js.find("position:fixed")
    assert 0 <= i_all < i_pos, (i_all, i_pos)


def test_overlay_box_scrolls_internally_when_tall():
    """Regression (v3.66.238): a tall HUD (many actions + verify + finish box)
    must scroll INSIDE the box, not overflow off the viewport with the bottom
    (finish guidance) unreachable. The box caps its height to the viewport minus
    the 8px top/bottom gutters and scrolls internally. v3.66.242 widened this to
    ``overflow:auto`` (both axes) to support user resize; the internal-scroll
    behaviour is preserved. Assert the box still carries
    max-height:calc(100vh - 16px) + overflow:auto."""
    m = _load()
    js = m.overlay_js(m.hud_payload(_cap_ready(), _GOOD_STATUS))
    assert "max-height:calc(100vh - 16px)" in js
    assert "overflow:auto" in js


def test_overlay_hud_is_draggable_and_resizable():
    """v3.66.242: the HUD can be dragged (a cursor:move grip in the title moves
    the closed-shadow host and persists window.__bd_hud_pos) and resized (box
    carries resize:both + box-sizing:border-box so the persisted offset size
    round-trips without growth; window.__bd_hud_size is captured on mouseup).
    The grip reaches the positioned element via box.getRootNode().host and the
    drag clamps to the viewport. Collapse stays a separate title click."""
    m = _load()
    js = m.overlay_js(m.hud_payload(_cap_ready(), _GOOD_STATUS))
    # resizable
    assert "resize:both" in js
    assert "box-sizing:border-box" in js
    # draggable: a move-cursor grip + closed-shadow host reach + persisted pos
    assert "cursor:move" in js
    assert "box.getRootNode().host" in js
    assert "window.__bd_hud_pos" in js
    assert "window.__bd_hud_size" in js
    # viewport clamp on drag
    assert "window.innerWidth" in js and "window.innerHeight" in js


def test_overlay_renders_trigger_line_resolved_and_unresolved():
    """#2a: the verify section must give an explicit play/download-trigger signal
    so the operator KNOWS whether their play click captured a media trigger."""
    m = _load()
    # resolved -> positive 'media captured' line
    vr = {"tier": "ready", "checks": ["play captured"], "warnings": [],
          "gap_count": 0, "trigger_selector": "button.play", "trigger_resolved": True,
          "action_count": 1}
    js_r = m.overlay_js({"panels": {}, "actions": [], "verify": vr})
    assert "media captured" in js_r
    assert "trigger_resolved" in js_r
    # unresolved -> explicit 'no media trigger resolved' line
    vu = dict(vr); vu["trigger_resolved"] = False; vu["trigger_selector"] = None
    js_u = m.overlay_js({"panels": {}, "actions": [], "verify": vu})
    assert "no media trigger resolved yet" in js_u


def test_overlay_has_collapse_toggle():
    """#1: the HUD must be collapsible so it doesn't permanently cover the page."""
    m = _load()
    js = m.overlay_js({"panels": {"session": {"host": "x"},
                                  "readiness": {"tier": "partial"}},
                       "actions": [], "verify": None})
    assert "__bd_hud_collapsed" in js          # collapse state flag
    assert "collapse/expand" in js             # the clickable affordance title
    assert "t.onclick" in js                   # title toggles collapse


def test_inject_overlay_uses_page_evaluate():
    # The HUD mounts via page.evaluate (isolated world, CSP-immune), NOT
    # add_script_tag — so it shows even on sites whose script-src would refuse
    # an injected <script>. (cloak returns a real Playwright page, so .evaluate
    # is the genuine CDP-backed call.)
    m = _load()

    class _Page:
        def __init__(self):
            self.evald = []
        def evaluate(self, expression):
            self.evald.append(expression)
        def add_script_tag(self, *, content):  # must NOT be used
            raise AssertionError("inject_overlay must use evaluate, not add_script_tag")

    pg = _Page()
    assert m.inject_overlay(pg, _cap_ready(), _GOOD_STATUS) is True
    assert len(pg.evald) == 1
    assert "attachShadow" in pg.evald[0]


def test_inject_overlay_never_raises_on_bad_page():
    m = _load()

    class _BadPage:
        def evaluate(self, expression):
            raise RuntimeError("page closed")

    assert m.inject_overlay(_BadPage(), _cap_ready()) is False


# ── Wave A: action timeline / verify payload + observational picker ──────────
def test_payload_includes_actions_verify_rec_when_supplied():
    m = _load()
    acts = [{"selector": "a.dl", "role": "download link",
             "effect": {"req_count": 1, "direct_media": 1, "signed": True}}]
    vf = {"tier": "ready", "checks": ["play captured"], "warnings": [], "gap_count": 0}
    p = m.hud_payload(_cap_ready(), _GOOD_STATUS, actions=acts, verify=vf, rec=True)
    assert p["actions"] == acts
    assert p["verify"] == vf
    assert p["rec"] is True


def test_payload_omits_action_keys_for_readonly_callers():
    m = _load()
    p = m.hud_payload(_cap_ready(), _GOOD_STATUS)   # the read-only HUD path
    assert "actions" not in p and "verify" not in p and "rec" not in p


def test_overlay_js_renders_actions_and_verify_sections():
    m = _load()
    js = m.overlay_js(m.hud_payload(
        _cap_ready(), _GOOD_STATUS,
        actions=[{"selector": "a.dl", "effect": {"req_count": 0}}],
        verify={"tier": "partial", "checks": [], "warnings": [], "gap_count": 1}, rec=True))
    assert "Actions" in js and "Ready to finish?" in js
    assert "REC" in js
    # the embedded payload carries the section data, no URL/secret
    assert '"actions":' in js and '"verify":' in js
    assert "http" not in js.lower()


def test_picker_script_is_passive_and_observational():
    m = _load()
    js = m.picker_script()
    # passive capture-phase listener that NEVER disturbs the page
    assert "passive:true" in js.replace(" ", "")
    assert "capture:true" in js.replace(" ", "")
    assert "preventDefault" not in js          # positive control: never blocks the click
    assert "stopPropagation" not in js
    # it hands a descriptor to the binding; it does NOT drive the site
    assert "__bd_inspect_pick" in js
    assert ".click(" not in js and ".submit(" not in js and "page.goto" not in js
    assert js.strip().startswith("(function()") and js.rstrip().endswith("})();")


def test_picker_buffers_when_binding_absent_else_uses_binding():
    """#2b: the picker must survive a cross-origin document swap. It calls the
    binding when present (live, nav-safe) and otherwise buffers the pick in-page
    (window.__bd_picks) for the pump to drain — the path that resolves the venus
    clicks that the binding-only transport dropped."""
    m = _load()
    js = m.picker_script()
    # binding-first: the click handler tries window.__bd_inspect_pick then returns
    i_bind = js.find("__bd_inspect_pick(rec)")
    i_buf = js.find("__bd_picks")
    assert i_bind != -1 and i_buf != -1
    assert "typeof window.__bd_inspect_pick === 'function'" in js
    assert i_bind < js.rfind("__bd_picks")          # binding attempt precedes buffer push
    assert "Date.now()" in js                       # in-page epoch-ms stamp
    # still strictly passive — no behaviour change to the operator's click
    assert "passive:true" in js and "preventDefault" not in js
