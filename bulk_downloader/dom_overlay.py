"""Read-only capture HUD (F2.7).

A capture-time, on-page heads-up display that shows the operator what the
recon capture has gathered *so far* — request volume, media readiness, DOM
recording health, and any anti-bot/challenge presence the site already
returned. It is a **read-only mirror of capture data**: every panel here is a
pure function of an already-built capture dict (plus the dom-recorder's own
status), so the entire HUD is exercisable on fixtures with no browser.

Posture (mirrors :mod:`bulk_downloader.capture_redactor`): the HUD changes
what a capture **shows the operator**, never what the tool **does**. It emits
nothing runnable, performs no replay/reconstruction, and never echoes a value
— only kinds and counts (F2 posture: report kinds+counts, never values).
URLs (which may carry signing material even post-redaction structure) are
deliberately excluded from the payload; only the host and per-kind tallies
cross the seam.

Two halves:

* **Pure half (sandbox-testable):** :func:`hud_panels` / :func:`hud_payload`
  build a JSON-serialisable snapshot from a capture dict. :func:`overlay_js`
  renders that snapshot into a self-contained Shadow-DOM widget script.
* **Live half (stash-only):** :func:`inject_overlay` mounts the widget via the
  page's ``evaluate`` (isolated world, CSP-immune). It does not piggy-back on
  the dom-recorder's injection, so the three capture guards
  (``session_capture``, ``dom_capture``, ``dom_recorder``) stay byte-identical.
  The widget builds a CLOSED Shadow root, so only an empty host marker reaches
  the page DOM and the rrweb capture is not polluted by the HUD.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .netlog_classify import (
    classify_network_log,
    KIND_HLS_MANIFEST,
    KIND_DASH_MANIFEST,
    KIND_HLS_SEGMENT,
    KIND_DIRECT,
)

# Panel identifiers — stable keys so the in-page renderer and the tests agree.
PANEL_SESSION = "session"
PANEL_MEDIA = "media"
PANEL_DOM = "dom"
PANEL_RISK = "risk"
PANEL_READINESS = "readiness"

PANEL_ORDER = (
    PANEL_SESSION,
    PANEL_MEDIA,
    PANEL_DOM,
    PANEL_RISK,
    PANEL_READINESS,
)

# Readiness tiers (verdict, not a score).
TIER_READY = "ready"
TIER_PARTIAL = "partial"
TIER_BLOCKED = "blocked"
TIER_THIN = "thin"


def _as_capture_dict(capture: Any) -> Dict[str, Any]:
    """Accept either a full capture dict or an object exposing
    ``to_capture_dict()``; return a plain dict. Never raises on odd input."""
    if isinstance(capture, dict):
        return capture
    to_dict = getattr(capture, "to_capture_dict", None)
    if callable(to_dict):
        try:
            d = to_dict()
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    return {}


def _host_of(cap: Dict[str, Any]) -> Optional[str]:
    host = cap.get("host")
    if host:
        return str(host)
    # Fall back to the page_context url's host, structure-only (no query).
    url = cap.get("url") or cap.get("page_url") or ""
    if isinstance(url, str) and "://" in url:
        rest = url.split("://", 1)[1]
        return rest.split("/", 1)[0].split("?", 1)[0] or None
    return None


def session_panel(cap: Dict[str, Any]) -> Dict[str, Any]:
    """Top-line session facts: host, request/WS volume, redaction state,
    elapsed span. Pure; values never include URLs or secrets."""
    nl = cap.get("network_log") or []
    nlc = cap.get("network_log_count")
    if not isinstance(nlc, int):
        nlc = len(nl) if isinstance(nl, (list, tuple)) else 0
    wsc = cap.get("websocket_log_count")
    if not isinstance(wsc, int):
        ws = cap.get("websocket_log") or []
        wsc = len(ws) if isinstance(ws, (list, tuple)) else 0

    # Elapsed span from the first/last network timestamps, if present.
    span_ms: Optional[int] = None
    ts = [e.get("timestamp") for e in nl
          if isinstance(e, dict) and isinstance(e.get("timestamp"), int)] \
        if isinstance(nl, (list, tuple)) else []
    if len(ts) >= 2:
        span_ms = max(ts) - min(ts)

    redacted = not bool(cap.get("_UNREDACTED"))
    return {
        "host": _host_of(cap),
        "requests": int(nlc),
        "websockets": int(wsc),
        "redacted": redacted,
        "span_ms": span_ms,
    }


def media_panel(cap: Dict[str, Any]) -> Dict[str, Any]:
    """Media-readiness tallies from the production classifier. Counts only —
    no URLs cross the seam. ``classify_network_log`` is the shipped, in-package
    classifier (the fMP4/DASH widening lives in the diagnostic tool, not the
    release, so it is intentionally out of scope here)."""
    report = classify_network_log(cap, host=_host_of(cap))
    items = report.items
    direct = [i for i in items if i.kind == KIND_DIRECT]
    return {
        "hls_manifests": len(report.hls_manifests),
        "dash_manifests": len(report.dash_manifests),
        "segments": len(report.segments),
        "direct_media": len(direct),
        "signed": len(report.signed_items),
        "drm": len([i for i in items if i.drm]),
        "media_total": len(items),
    }


def dom_panel(cap: Dict[str, Any],
              recorder_status: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """DOM-recording health. ``recorder_status`` is the dict from
    :func:`bulk_downloader.dom_recorder.get_status`; passed in so this stays a
    pure function (no module-global read). Falls back to all-unknown when not
    supplied."""
    st = recorder_status if isinstance(recorder_status, dict) else {}
    dom_log = cap.get("dom_log")
    dom_events = len(dom_log) if isinstance(dom_log, (list, tuple)) else 0
    dropped = st.get("dom_events_dropped", 0)
    streak = st.get("arm_fail_streak", 0)
    assets_ok = bool(st.get("rrweb_present")) and bool(st.get("snapdom_present"))

    # Mirror the cockpit badge logic: error if assets missing, degraded on
    # drops or a sustained arm-fail streak, else ok.
    if recorder_status is None:
        badge = "unknown"
    elif not assets_ok:
        badge = "error"
    elif (isinstance(dropped, int) and dropped > 0) or \
         (isinstance(streak, int) and streak >= 5):
        badge = "degraded"
    else:
        badge = "ok"
    return {
        "badge": badge,
        "dom_events": dom_events,
        "dom_events_dropped": int(dropped) if isinstance(dropped, int) else 0,
        "arm_fail_streak": int(streak) if isinstance(streak, int) else 0,
        "assets_ok": assets_ok,
    }


def risk_panel(cap: Dict[str, Any]) -> Dict[str, Any]:
    """Anti-bot / challenge presence — read from the capture's own
    ``fingerprint_detection`` finding (presence-only, detect-and-surface). No
    evasion guidance and no values; counts and vendor *names* only."""
    fp = cap.get("fingerprint_detection")
    if not isinstance(fp, dict):
        fp = {}
    vendors = fp.get("vendors") or []
    challenges = fp.get("challenges") or []
    fp_echo = fp.get("fp_echo_headers") or []
    vendor_names = [str(v.get("vendor")) for v in vendors
                    if isinstance(v, dict) and v.get("vendor")]
    return {
        "fingerprinting_detected": bool(fp.get("fingerprinting_detected")),
        "vendors": vendor_names,
        "vendor_count": len(vendor_names),
        "challenge_count": len(challenges) if isinstance(challenges, (list, tuple)) else 0,
        "fp_echo_count": len(fp_echo) if isinstance(fp_echo, (list, tuple)) else 0,
    }


def readiness_panel(cap: Dict[str, Any],
                    recorder_status: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Synthesised one-line verdict from the other panels. A *verdict*, not a
    score: a thin capture (no media, no DOM) reads ``thin``; a DRM-only or
    challenge-walled capture reads ``blocked``; a manifest/segment capture with
    healthy DOM reads ``ready``; anything in between reads ``partial``."""
    media = media_panel(cap)
    dom = dom_panel(cap, recorder_status)
    risk = risk_panel(cap)
    sess = session_panel(cap)

    has_manifest = (media["hls_manifests"] + media["dash_manifests"]) > 0
    has_segments = media["segments"] > 0
    has_direct = media["direct_media"] > 0
    has_media = media["media_total"] > 0
    drm_only = media["drm"] > 0 and (media["media_total"] - media["drm"]) <= 0
    challenged = risk["challenge_count"] > 0

    if challenged or drm_only:
        tier = TIER_BLOCKED
        reasons = []
        if drm_only:
            reasons.append("media is DRM-protected")
        if challenged:
            reasons.append("challenge/interstitial responses present")
        note = "blocked: " + "; ".join(reasons)
    elif (has_manifest or has_segments or has_direct) and dom["badge"] in ("ok", "unknown"):
        # A streaming ladder (HLS/DASH manifest or segments) OR a direct-media
        # download (a signed progressive MP4 as JWPlayer/direct-download sites
        # serve) both count as a complete capture — mirrors verify_summary's
        # ``has_direct`` so the top badge and the finish-bar agree. (Previously
        # ladder-only, which read PARTIAL on a fully-captured direct download.)
        tier = TIER_READY
        bits = []
        if has_manifest:
            bits.append("manifest")
        if has_segments:
            bits.append("segments")
        if has_direct:
            bits.append("direct media")
        note = "ready: " + "+".join(bits) + " captured"
    elif has_media or sess["requests"] > 0:
        tier = TIER_PARTIAL
        note = "partial: requests captured, no manifest/segment/direct-media yet"
    else:
        tier = TIER_THIN
        note = "thin: no media or requests captured"
    return {"tier": tier, "note": note}


def hud_panels(capture: Any,
               recorder_status: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """All five panels as a dict keyed by panel id. Pure function of the
    capture dict (and the optional recorder status)."""
    cap = _as_capture_dict(capture)
    return {
        PANEL_SESSION: session_panel(cap),
        PANEL_MEDIA: media_panel(cap),
        PANEL_DOM: dom_panel(cap, recorder_status),
        PANEL_RISK: risk_panel(cap),
        PANEL_READINESS: readiness_panel(cap, recorder_status),
    }


def hud_payload(capture: Any,
                recorder_status: Optional[Dict[str, Any]] = None,
                actions: Optional[List[Dict[str, Any]]] = None,
                verify: Optional[Dict[str, Any]] = None,
                rec: bool = False) -> Dict[str, Any]:
    """JSON-serialisable HUD snapshot: ``{order, panels[, actions, verify, rec]}``.
    This is exactly what crosses into page scope; assert in tests that it
    round-trips through ``json.dumps`` and carries no ``url``/``http`` value.

    ``actions`` is the resolved action->effect timeline (selectors = structure,
    effects = kinds/counts), ``verify`` the finish-time readout, ``rec`` whether
    the auto-recorder is active — all from :mod:`bulk_downloader.inspect_pick`,
    supplied by the capture driver. They are omitted when not provided so
    existing read-only callers are unaffected."""
    out: Dict[str, Any] = {
        "order": list(PANEL_ORDER),
        "panels": hud_panels(capture, recorder_status),
    }
    if actions:
        out["actions"] = list(actions)
    if verify:
        out["verify"] = verify
    if rec:
        out["rec"] = True
    return out


# The in-page widget. A single IIFE with EXPLICIT statement terminators — the
# ``;`` separators are load-bearing exactly as in dom_recorder.recorder_script
# (the vendored bundles are not newline-terminated; ASI must never be relied
# on). The widget builds a CLOSED Shadow root so it cannot be read or restyled
# by the page and never appears in the page's own DOM tree (and therefore not
# in the rrweb DOM capture). It is render-only: no listeners that touch the
# page, no network, no globals beyond the one mount guard.
#
# v3.66.230.x fix: the widget is now SELF-HEALING. The earlier single-mount
# guard (``if (window.__bd_hud_mounted) return``) keyed on a window global, so
# once it was set the HUD could NEVER re-mount or refresh while that window
# persisted. Two operator-visible failures fell out of that:
#   * DISAPPEAR — a real login flow does a full nav to a sub-origin (window
#     reset -> HUD mounts), then the SPA re-renders ``document.body`` IN THE
#     SAME document (window persists, our host node is wiped). The old guard
#     was still ``true``, so ``_pump_dom``'s next tick early-returned and the
#     host was never re-added — gone for good, and silently (evaluate still
#     "succeeded", so no ``[hud] overlay not shown`` warning fired).
#   * STALE/EMPTY DATA — the HUD mounted once on the login page when the
#     capture was empty (0 req / 0 media / host ``-``); the guard then blocked
#     every data refresh, so it showed that frozen empty snapshot forever.
# Both are fixed by keying on DOM PRESENCE, not a window flag: re-mount whenever
# the host is missing, and refresh the panel values in place (via a retained
# ``window.__bd_hud_box`` reference) when it is still there. The Shadow root
# stays CLOSED, so only the empty ``<div data-bd-hud>`` host marker ever reaches
# the page DOM / the rrweb capture (WACZ-safe), and the orphan-removal step
# guarantees there is never more than one host. The ``;`` separators are
# load-bearing exactly as before (no ASI reliance).
_WIDGET_TEMPLATE = (
    "(function(){"
    "  var DATA = %s;"
    "  function el(tag, css, text){ var n=document.createElement(tag);"
    "    if(css){ n.style.cssText=css; } if(text!=null){ n.textContent=text; } return n; }"
    "  var TIER = {ready:'#48c774',partial:'#ffdd57',blocked:'#f14668',thin:'#7a818c'};"
    "  function render(box, D){"
    "    box.textContent = '';"
    "    var p=(D&&D.panels)||{}, acts=(D&&D.actions)||[], vf=(D&&D.verify)||null;"
    "    var t = el('div','font-weight:700;margin-bottom:6px;opacity:0.85;letter-spacing:0.3px;"
    "display:flex;justify-content:space-between;align-items:center');"
    "    var bd=el('span',null,'BD capture');"
    "    bd.appendChild(el('span','opacity:0.55;font-size:10px;margin-left:6px',window.__bd_hud_collapsed?'\\u25b8':'\\u25be'));"
    "    t.appendChild(bd);"
    "    var grip=el('span','cursor:move;opacity:0.5;font-size:11px;margin-left:8px;padding:0 4px','\\\\u22ee\\\\u22ee');"
    "    grip.title='drag';"
    "    grip.onclick=function(ev){ ev.stopPropagation(); };"
    "    grip.onmousedown=function(ev){ ev.preventDefault(); ev.stopPropagation();"
    "      var h=box.getRootNode().host; var r=h.getBoundingClientRect();"
    "      var sx=ev.clientX, sy=ev.clientY, ox=r.left, oy=r.top;"
    "      h.style.right='auto'; h.style.left=ox+'px'; h.style.top=oy+'px';"
    "      function mv(e){ var nl=ox+(e.clientX-sx), nt=oy+(e.clientY-sy);"
    "        nl=Math.max(0,Math.min(nl,(window.innerWidth||1024)-40));"
    "        nt=Math.max(0,Math.min(nt,(window.innerHeight||768)-20));"
    "        h.style.left=nl+'px'; h.style.top=nt+'px'; window.__bd_hud_pos={left:nl,top:nt}; }"
    "      function up(){ document.removeEventListener('mousemove',mv,true); document.removeEventListener('mouseup',up,true); }"
    "      document.addEventListener('mousemove',mv,true); document.addEventListener('mouseup',up,true); };"
    "    t.appendChild(grip);"
    "    if(D&&D.rec){ t.appendChild(el('span','font-size:10px;font-weight:700;color:#fff;"
    "background:#c33;border-radius:4px;padding:1px 6px','REC')); }"
    "    t.style.cursor='pointer'; t.title='collapse/expand';"
    "    t.onclick=function(){ window.__bd_hud_collapsed=!window.__bd_hud_collapsed; render(box, D); };"
    "    box.appendChild(t);"
    "    if (window.__bd_hud_collapsed){ var _cs=(D&&D.panels&&D.panels.session)||{}, _crd=(D&&D.panels&&D.panels.readiness)||{};"
    "      box.appendChild(el('div','opacity:0.75;font-size:11px;margin-top:2px',(_cs.host||'-')+' \\u00b7 '+((_crd.tier||'?').toUpperCase()))); return; }"
    "    function row(label,value){ var r=el('div','display:flex;justify-content:space-between;gap:10px');"
    "      r.appendChild(el('span','opacity:0.7',label));"
    "      r.appendChild(el('span','font-weight:600',String(value))); box.appendChild(r); }"
    "    try {"
    "      var s=p.session||{}, m=p.media||{}, d=p.dom||{}, rk=p.risk||{}, rd=p.readiness||{};"
    "      row('host', s.host || '-');"
    "      row('requests', s.requests || 0);"
    "      row('media', (m.media_total||0) + ' (' + (m.segments||0) + ' seg)');"
    "      row('dom', (d.badge||'?') + ' /' + (d.dom_events||0));"
    "      row('risk', (rk.vendor_count||0) + 'v ' + (rk.challenge_count||0) + 'c');"
    "      box.appendChild(el('div','margin-top:6px;padding-top:6px;border-top:1px solid #3a3f4b;"
    "font-weight:700;color:' + (TIER[rd.tier]||'#e6e6e6'), (rd.tier||'?').toUpperCase()));"
    "    } catch (e) { box.appendChild(el('div',null,'HUD render error')); }"
    "    if (acts && acts.length) {"
    "      var ah = el('div','margin-top:8px;padding-top:6px;border-top:1px solid #3a3f4b;"
    "color:#cfe1ff;font-weight:700;display:flex;justify-content:space-between');"
    "      ah.appendChild(el('span',null,'Actions'));"
    "      ah.appendChild(el('span','opacity:0.6;font-weight:600',String(acts.length)+' rec'));"
    "      box.appendChild(ah);"
    "      for (var i=0;i<acts.length;i++){"
    "        var a=acts[i]||{}, eff=a.effect||{};"
    "        var step=el('div','padding:3px 0;border-bottom:1px solid #20242d');"
    "        var line=el('div','display:flex;gap:6px;align-items:baseline');"
    "        line.appendChild(el('span','color:#5d6470;min-width:12px',String(i+1)));"
    "        line.appendChild(el('span','color:#7fb2ff;word-break:break-all',a.selector||'?'));"
    "        step.appendChild(line);"
    "        var effstr, effcol='#9aa3af';"
    "        if ((eff.req_count||0)===0){ effstr='\\u26a0 0 req \\u2014 no effect'; effcol='#f1a85a'; }"
    "        else { var ps=[]; if(eff.manifest){ps.push('manifest');} if(eff.segments){ps.push(eff.segments+' seg');}"
    "          if(eff.direct_media){ps.push('direct-media');} var b=eff.req_count+' req';"
    "          if(ps.length){ b+=' \\u00b7 '+ps.join('+'); effcol='#48c774'; }"
    "          if(eff.signed){ b+=' \\u00b7 signed'; } if(eff.nav){ b+=' \\u00b7 \\u2192 nav'; } effstr=b; }"
    "        step.appendChild(el('div','color:'+effcol+';font-size:11px;margin-left:18px',effstr));"
    "        box.appendChild(step);"
    "      }"
    "    }"
    "    if (vf) {"
    "      var vh=el('div','margin-top:8px;padding-top:6px;border-top:1px solid #3a3f4b;"
    "color:#cfe1ff;font-weight:700;display:flex;justify-content:space-between');"
    "      vh.appendChild(el('span',null,'Ready to finish?'));"
    "      vh.appendChild(el('span','color:'+(TIER[vf.tier]||'#e6e6e6'),(vf.tier||'?').toUpperCase()));"
    "      box.appendChild(vh);"
    "      var trg=el('div','display:flex;gap:6px;margin:3px 0;font-weight:600');"
    "      if (vf.trigger_resolved){ trg.appendChild(el('span','color:#48c774','\\u25b6'));"
    "        trg.appendChild(el('span','color:#9fe0b4',(vf.trigger_selector||'media trigger')+' \\u2192 media captured')); }"
    "      else { trg.appendChild(el('span','color:#f1a85a','\\u25b6'));"
    "        trg.appendChild(el('span','color:#e2c08a','no media trigger resolved yet \\u2014 click the player')); }"
    "      box.appendChild(trg);"
    "      (vf.checks||[]).forEach(function(c){ var l=el('div','display:flex;gap:6px;margin:2px 0');"
    "        l.appendChild(el('span','color:#48c774','\\u2713')); l.appendChild(el('span','color:#c2c8d2',c)); box.appendChild(l); });"
    "      (vf.warnings||[]).forEach(function(w){ var l=el('div','display:flex;gap:6px;margin:2px 0');"
    "        l.appendChild(el('span','color:#f1a85a','\\u26a0')); l.appendChild(el('span','color:#c2c8d2',w)); box.appendChild(l); });"
    "      if ((vf.gap_count||0)>0){ box.appendChild(el('div','margin-top:6px;padding:6px 8px;"
    "background:rgba(241,168,90,0.1);border:1px solid #6b5326;border-radius:6px;color:#f1c98a;font-size:11px',"
    "        vf.gap_count+' advisory \\u2014 finish via your usual ENTER/FINISH, or keep capturing.')); }"
    "    }"
    "  }"
    "  var existing = document.querySelector('[data-bd-hud]');"
    "  if (existing && window.__bd_hud_box) {"
    "    try { render(window.__bd_hud_box, DATA); } catch (e) {}"
    "    return;"
    "  }"
    "  if (existing && existing.remove) { existing.remove(); }"
    "  var host = document.createElement('div');"
    "  host.setAttribute('data-bd-hud','1');"
    # all:initial MUST come first — placed last it resets position/top/right/z-index
    # back to their initial values (position:static), dropping the HUD into normal
    # document flow so it scrolls away / is covered (and lands top-left, not -right).
    "  host.style.cssText = 'all:initial;position:fixed;top:8px;right:8px;z-index:2147483647';"
    "  var root = host.attachShadow ? host.attachShadow({mode:'closed'}) : host;"
    "  var box = document.createElement('div');"
    "  box.style.cssText = 'font:12px/1.45 ui-monospace,Menlo,monospace;color:#e6e6e6;"
    "background:rgba(20,22,28,0.95);border:1px solid #3a3f4b;border-radius:8px;"
    # max-height + overflow-y:auto so a tall HUD (many actions + verify + finish
    # box) scrolls INSIDE the box instead of overflowing off the viewport with no
    # way to reach the bottom. calc(100vh - 16px) leaves the 8px top + 8px bottom
    # gutters; the box (not the host) scrolls so the border/rounding stay intact.
    "padding:8px 10px;box-sizing:border-box;min-width:210px;max-width:96vw;width:264px;box-shadow:0 2px 10px rgba(0,0,0,0.45);"
    "max-height:calc(100vh - 16px);min-height:60px;overflow:auto;resize:both';"
    "  root.appendChild(box);"
    "  (document.body || document.documentElement).appendChild(host);"
    "  if (window.__bd_hud_pos){ host.style.right='auto'; host.style.left=window.__bd_hud_pos.left+'px'; host.style.top=window.__bd_hud_pos.top+'px'; }"
    "  if (window.__bd_hud_size){ box.style.width=window.__bd_hud_size.w+'px'; box.style.height=window.__bd_hud_size.h+'px'; }"
    "  box.addEventListener('mouseup', function(){ window.__bd_hud_size={w:box.offsetWidth,h:box.offsetHeight}; }, true);"
    "  window.__bd_hud_box = box;"
    "  render(box, DATA);"
    "})();"
)


def overlay_js(payload: Dict[str, Any]) -> str:
    """Render the HUD payload into a self-contained, Shadow-DOM widget script.

    The payload is embedded as a JSON literal (``json.dumps`` — safe, no
    template injection, and proves there are no Python values smuggled in). The
    result is what :func:`inject_overlay` hands to ``page.evaluate``."""
    data_literal = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return _WIDGET_TEMPLATE % data_literal


def inject_overlay(page, capture: Any,
                   recorder_status: Optional[Dict[str, Any]] = None,
                   actions: Optional[List[Dict[str, Any]]] = None,
                   verify: Optional[Dict[str, Any]] = None,
                   rec: bool = False) -> bool:
    """Mount the HUD on a live page via the page's ``evaluate`` (the page is a
    real Playwright/CloakBrowser page — cloak wraps Playwright and returns the
    underlying page object, so ``evaluate`` is the genuine CDP-backed call).

    Injection is over ``page.evaluate`` — NOT ``page.add_script_tag`` — on
    purpose: ``evaluate`` runs in the page's main world but executes via CDP
    ``Runtime.evaluate``, which is **not governed by the page's ``script-src``
    CSP**, so the HUD mounts on sites that would refuse an injected ``<script>``.
    The widget builds a CLOSED Shadow root (only an empty host marker reaches the
    page DOM / rrweb capture) and is self-healing (re-mounts whenever the host is
    missing; refreshes panel values in place when present). It never drives the
    page — the recorder keeps ``add_script_tag`` because it must hook the page's
    own runtime; the HUD does not.

    ``actions`` / ``verify`` / ``rec`` (from :mod:`bulk_downloader.inspect_pick`,
    via the capture driver) add the resolved action->effect timeline + the
    finish-time verify readout when present; omitting them yields the read-only
    HUD unchanged.

    Best-effort: returns True iff the widget ran, False on any failure (a closed
    page, no body). Never raises into the capture path. The dom-recorder guards
    are untouched."""
    try:
        payload = hud_payload(capture, recorder_status, actions=actions,
                              verify=verify, rec=rec)
        page.evaluate(overlay_js(payload))
        return True
    except Exception:
        return False


# ── Live element-inspector / action-recorder injection (Wave A) ──────────────
# This is the in-page half of the observational inspector. It is injected by
# ``tools/capture_session`` over ``page.evaluate`` (re-installed each pump tick,
# like the HUD, so it survives navigation) ALONGSIDE a Playwright
# ``expose_binding("__bd_inspect_pick", ...)``. On every click the operator
# makes, it reads the clicked element into a structured DESCRIPTOR and hands it
# back through that binding — Python resolves it (selector/XPath/role) and
# correlates it with the network log (:mod:`inspect_pick`).
#
# It is strictly OBSERVATIONAL: the listener is ``capture:true, passive:true``
# and NEVER calls ``preventDefault``/``stopPropagation``, so the operator's own
# click still reaches the site normally. It reads the DOM; it does not drive it.
# No values cross here unredacted — the raw ``outerHTML`` is capped and scrubbed
# sink-side by ``inspect_pick.redact_excerpt``.
_PICKER_TEMPLATE = (
    "(function(){"
    "  if (window.__bd_picker_installed) { return; }"
    "  window.__bd_picker_installed = true;"
    "  if (!window.__bd_picks) { window.__bd_picks = []; }"
    "  function classesOf(n){ return (n && n.classList) ? Array.prototype.slice.call(n.classList) : []; }"
    "  function nthOf(n){ var i=1, s=n; while((s=s.previousElementSibling)){ i++; } return i; }"
    "  function ofTypeNth(n){ var i=1, s=n, t=n.tagName; while((s=s.previousElementSibling)){ if(s.tagName===t){ i++; } } return i; }"
    "  function descOf(el){"
    "    var anc=[], p=el.parentElement, lvl=0;"
    "    while(p && lvl<3){ anc.push({tag:(p.tagName||'').toLowerCase(), id:p.id||'',"
    "      classes:classesOf(p), nth:nthOf(p)}); p=p.parentElement; lvl++; }"
    "    var data={}; if(el.dataset){ for(var k in el.dataset){ data[k]=el.dataset[k]; } }"
    "    var attrs={}, want=['href','src','type','role','name','aria-label','alt','title','download'];"
    "    for(var j=0;j<want.length;j++){ if(el.hasAttribute && el.hasAttribute(want[j])){ attrs[want[j]]=el.getAttribute(want[j]); } }"
    "    return { tag:(el.tagName||'').toLowerCase(), id:el.id||'', classes:classesOf(el),"
    "      data_attrs:data, attrs:attrs, text:(el.textContent||'').trim().slice(0,80),"
    "      nth:nthOf(el), of_type_nth:ofTypeNth(el), outer_html:(el.outerHTML||'').slice(0,600) };"
    "  }"
    "  document.addEventListener('click', function(ev){"
    "    try {"
    "      var el = ev.target; if(!el || el.nodeType!==1){ return; }"
    "      var rec = { d: descOf(el), ts: Date.now() };"
    "      if (typeof window.__bd_inspect_pick === 'function') {"
    "        try { window.__bd_inspect_pick(rec); return; } catch (e2) { /* fall through to buffer */ }"
    "      }"
    "      (window.__bd_picks = window.__bd_picks || []).push(rec);"
    "      if (window.__bd_picks.length > 500) { window.__bd_picks.splice(0, window.__bd_picks.length - 500); }"
    "    } catch (e) { /* observational; never disturb the page */ }"
    "  }, { capture:true, passive:true });"
    "})();"
)


def picker_script() -> str:
    """The in-page element-inspector/recorder bootstrap (see ``_PICKER_TEMPLATE``).
    Pure string; the caller injects it via ``page.evaluate`` next to a
    ``expose_binding('__bd_inspect_pick', ...)``."""
    return _PICKER_TEMPLATE
