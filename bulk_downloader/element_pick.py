"""Live element-pick selector deriver for the noVNC capture workflow.

NON-guard. This module carries the in-page JavaScript that turns an operator's
click on the held-open live page into a robust, *built-like* CSS selector.

Why in-page (and not a noVNC canvas coordinate map): the operator's click
inside the noVNC canvas is forwarded as a genuine mouse event into the remote
Chromium, so the live page already knows -- to the pixel, with no scaling/DPR
math -- which element was hit. The pick bridge (phase 2) injects this function
over the existing CDP session (``session_capture.capture_via_cdp`` already
attaches one) and either resolves ``document.elementFromPoint`` or hooks a
one-shot capture-phase click listener; either way the SELECTOR is derived here,
in the page's own DOM. No coordinate mapping anywhere.

Selector philosophy mirrors ``tools/build_template_from_wacz.py::_html_selectors``
so a *picked* selector looks like a *built* one:

  1. a STABLE ``tag#id`` (``input#username``, ``input#user-password``) -- but a
     hashed/volatile id (``btn_a1b2c3d4e5f6a7b8``) is rejected;
  2. else ``tag.class[data-attr]...`` -- meaningful classes plus the *minimum*
     set of stable ``data-*`` attributes needed to be unique
     (``a.ct_dl_button[data-framerate="60"]``);
  3. else a minimal scoped ``:nth-of-type`` path.

Every result also reports ``visible`` (FIX-1: decoys are display:none) and
``unique``/``count`` so the surface can warn before a thin/decoy selector lands
in a draft.
"""

# A single function declaration (no IIFE) so callers can embed it directly:
#   page.evaluate("(x,y)=>{ %s; const el=document.elementFromPoint(x,y);
#                           return bdPickSelector(el); }" % PICK_SELECTOR_JS, x, y)
# or inject once via add_init_script wrapped to assign window.bdPickSelector.
PICK_SELECTOR_JS = r"""
function bdPickSelector(el) {
  if (!el || el.nodeType !== 1) return null;
  var doc = el.ownerDocument;
  var tag = el.tagName.toLowerCase();

  function esc(s) {
    return (window.CSS && CSS.escape) ? CSS.escape(s) : String(s);
  }
  function escAttr(v) {
    return String(v).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  }
  // A stable id is alnum/_/- , starts with a letter, not too long, and does NOT
  // look hashed (no 12+ hex run, no leading digit). Mirrors the builder's use of
  // literal ids (#username, #password) and rejects framework-generated ids.
  function stableId(id) {
    if (!id || id.length > 32) return false;
    if (!/^[A-Za-z][\w-]*$/.test(id)) return false;
    if (/^\d/.test(id)) return false;
    if (/[0-9a-fA-F]{12,}/.test(id)) return false;   // hashed / uuid-ish
    return true;
  }
  function stableClasses(node) {
    var out = [];
    var list = node.classList ? Array.prototype.slice.call(node.classList) : [];
    for (var i = 0; i < list.length; i++) {
      var c = list[i];
      if (c.length < 2) continue;
      if (!/^[A-Za-z][\w-]*$/.test(c)) continue;      // skip odd/empty tokens
      if (/^[a-f0-9]{8,}$/i.test(c)) continue;        // skip pure-hash classes
      out.push(c);
    }
    return out;
  }
  // Stable data-* attrs: short, non-variable values. Skips instrumentation hooks
  // (data-test / data-pick). "framerate"/"res" pass; a long content id does not.
  function stableData(node) {
    var out = [];
    var attrs = node.attributes || [];
    for (var i = 0; i < attrs.length; i++) {
      var a = attrs[i];
      if (!/^data-/.test(a.name)) continue;
      if (/^data-(test|pick)/.test(a.name)) continue;
      var v = a.value == null ? "" : String(a.value);
      if (v.length > 24) continue;
      if (/[0-9a-fA-F]{16,}/.test(v)) continue;       // variable-segment-ish
      if (/^\d{6,}$/.test(v)) continue;
      out.push({ name: a.name, value: v });
    }
    return out;
  }
  function isExactly(sel, node) {
    try {
      var m = doc.querySelectorAll(sel);
      return m.length === 1 && m[0] === node;
    } catch (e) { return false; }
  }
  function countOf(sel) {
    try { return doc.querySelectorAll(sel).length; } catch (e) { return 0; }
  }
  function visible(node) {
    var r = node.getBoundingClientRect();
    var s = node.ownerDocument.defaultView.getComputedStyle(node);
    return s.display !== "none" && s.visibility !== "hidden" &&
           s.opacity !== "0" && r.width > 0 && r.height > 0;
  }

  function countVisible(sel) {
    try {
      var m = doc.querySelectorAll(sel), v = 0;
      for (var i = 0; i < m.length; i++) { if (visible(m[i])) v++; }
      return v;
    } catch (e) { return 0; }
  }

  // The GROUP (repeating) selector: tag + stable classes only — no id, no
  // data-attr, no nth. This is what a ROW selector wants: match every sibling
  // row, not the one clicked. (The unique selector below pins to one element
  // via data-id/nth, which is wrong for a grid.) Null when the element has no
  // stable classes or the class signature does not actually repeat. group_count
  // is the raw match count; group_visible exposes responsive-duplicate
  // inflation (e.g. an lg+md+mob grid renders each tile 3x — 75 matched, 25
  // visible — so the operator can scope before promoting a 3x-inflated row set).
  function groupOf(node) {
    var gtag = node.tagName.toLowerCase();
    var gcls = stableClasses(node);
    if (!gcls.length) return null;
    var gsel = gtag;
    for (var i = 0; i < gcls.length; i++) gsel += "." + esc(gcls[i]);
    var gc = countOf(gsel);
    if (gc < 2) return null;  // does not repeat -> not a useful row selector
    return { selector: gsel, count: gc, visible: countVisible(gsel) };
  }

  // A stable, repeatable signature for a SCOPING ancestor: a stable tag#id, else
  // tag + stable classes. Null when the ancestor has no stable signature (don't
  // root a scope on an nth-path — that is itself fragile).
  function ancestorSig(node) {
    var t = node.tagName.toLowerCase();
    if (node.id && stableId(node.id)) return t + "#" + esc(node.id);
    var cls = stableClasses(node);
    if (!cls.length) return null;
    var s = t;
    for (var i = 0; i < cls.length; i++) s += "." + esc(cls[i]);
    return s;
  }

  // Auto-scope a repeating selector to the VISIBLE responsive container when the
  // raw count is inflated by lg+md+mob duplicate wrappers (group_count >
  // group_visible). Walk up to the nearest ancestor whose scoped selector
  // matches ONLY the visible rows (sc === svis) and ALL of them (svis === gvis)
  // -- that ancestor is the active wrapper. Rejects an ancestor that still spans
  // multiple wrappers (sc > gvis) or clips the set (sc < gvis). Null = no clean
  // scope found; the caller keeps the bare selector and flags it for review.
  function scopedGroupOf(node, gsel, gc, gvis) {
    if (gc <= gvis) return null;       // not inflated -> nothing to scope
    var anc = node.parentElement;
    while (anc && anc !== doc.documentElement) {
      var asig = ancestorSig(anc);
      if (asig) {
        var scoped = asig + " " + gsel;
        var sc = countOf(scoped);
        var svis = countVisible(scoped);
        if (sc > 0 && sc === svis && svis === gvis) {
          return { selector: scoped, count: sc, visible: svis };
        }
      }
      anc = anc.parentElement;
    }
    return null;
  }

  function finalize(sel) {
    var grp = groupOf(el);
    var raw = grp ? grp.selector : null;
    var scoped = grp ? scopedGroupOf(el, grp.selector, grp.count, grp.visible) : null;
    var inflated = grp ? (grp.count > grp.visible) : false;
    return {
      selector: sel,
      unique: countOf(sel) === 1,
      count: countOf(sel),
      visible: visible(el),
      tag: tag,
      text: (el.textContent || "").trim().slice(0, 40),
      // The chosen row selector: auto-scoped to the visible container when
      // inflation was detected and a clean scope exists, else the bare repeating
      // selector. group_raw_selector always carries the unscoped signature so
      // the surface can still show "matched N / visible M".
      group_selector: scoped ? scoped.selector : raw,
      group_count: grp ? grp.count : 0,
      group_visible: grp ? grp.visible : 0,
      group_raw_selector: raw,
      // true  = auto-scoped to the visible container;
      // false = inflation present but no stable ancestor scoped cleanly (the bare
      //         selector is kept -- review before promote);
      // null  = no inflation (the bare repeating selector is already correct).
      group_scoped: scoped ? true : (inflated ? false : null)
    };
  }

  // (1) stable tag#id
  if (el.id && stableId(el.id)) {
    var idSel = tag + "#" + esc(el.id);
    if (isExactly(idSel, el)) return finalize(idSel);
  }

  // (2) tag + meaningful classes, then add the MINIMUM stable data-attrs needed
  var base = tag;
  var classes = stableClasses(el);
  for (var i = 0; i < classes.length; i++) base += "." + esc(classes[i]);
  var candidates = [base];
  var acc = base;
  var datas = stableData(el);
  for (var j = 0; j < datas.length; j++) {
    acc += "[" + datas[j].name + '="' + escAttr(datas[j].value) + '"]';
    candidates.push(acc);
  }
  for (var k = 0; k < candidates.length; k++) {
    if (isExactly(candidates[k], el)) return finalize(candidates[k]);
  }

  // (3) minimal scoped :nth-of-type path -- prepend ancestors until unique,
  // rooting at the nearest ancestor that itself has a stable id.
  function seg(node) {
    var t = node.tagName.toLowerCase();
    var n = 1, sib = node;
    while ((sib = sib.previousElementSibling)) {
      if (sib.tagName.toLowerCase() === t) n++;
    }
    return t + ":nth-of-type(" + n + ")";
  }
  var parts = [seg(el)];
  var node = el;
  while (true) {
    var joined = parts.join(" > ");
    if (isExactly(joined, el)) return finalize(joined);
    var p = node.parentElement;
    if (!p || p === doc.documentElement) break;
    if (p.id && stableId(p.id)) {
      parts.unshift(p.tagName.toLowerCase() + "#" + esc(p.id));
      var rooted = parts.join(" > ");
      if (isExactly(rooted, el)) return finalize(rooted);
      break;
    }
    parts.unshift(seg(p));
    node = p;
  }
  return finalize(parts.join(" > "));
}
""".strip()


# ──────────────────────────────────────────────────────────────────────────
# Auto-detect repeating ROW GROUPS (no operator click).
#
# Ranks the dominant repeating, visible, download-shaped tile signature on a
# live page and returns ranked candidates. Self-contained (its own helper
# copies) so it does not couple to bdPickSelector's internals. Heuristic +
# operator-gated: this RECOMMENDS row_selectors to pre-fill the wizard; nothing
# is promoted or enabled from it. The same ranking is mirrored offline in
# tools/build_template_from_wacz._generic_row_selectors_from_html for WACZ-built
# drafts (offline has no computed layout, so it cannot score visibility -- the
# live path here is the visibility-aware one).
# ──────────────────────────────────────────────────────────────────────────
AUTO_ROW_GROUPS_JS = r"""
function bdAutoRowGroups(rootDoc, opts) {
  var doc = rootDoc || document;
  var max = (opts && opts.max) || 5;
  var DL_RE = /download|\bdl\b|\.mp4|\.mkv|\.mov|\.webm|\.m4v|\.ts/i;
  var RES_RE = /\d{3,4}\s*p\b|\d{3,4}\s*[x\u00d7]\s*\d{3,4}|\b(?:4k|2k|8k|hd|fhd|uhd|qhd)\b/i;

  function esc(s) { return (window.CSS && CSS.escape) ? CSS.escape(s) : String(s); }
  function stableClasses(node) {
    var out = [];
    var list = node.classList ? Array.prototype.slice.call(node.classList) : [];
    for (var i = 0; i < list.length; i++) {
      var c = list[i];
      if (c.length < 2) continue;
      if (!/^[A-Za-z][\w-]*$/.test(c)) continue;
      if (/^[a-f0-9]{8,}$/i.test(c)) continue;
      out.push(c);
    }
    return out.sort();
  }
  function visible(node) {
    try {
      var r = node.getBoundingClientRect();
      var s = node.ownerDocument.defaultView.getComputedStyle(node);
      return s.display !== "none" && s.visibility !== "hidden" &&
             s.opacity !== "0" && r.width > 0 && r.height > 0;
    } catch (e) { return false; }
  }
  function countOf(sel) { try { return doc.querySelectorAll(sel).length; } catch (e) { return 0; } }
  function countVisible(sel) {
    try {
      var m = doc.querySelectorAll(sel), v = 0;
      for (var i = 0; i < m.length; i++) { if (visible(m[i])) v++; }
      return v;
    } catch (e) { return 0; }
  }
  // A member is download-shaped if it (or a descendant) carries a media-ish
  // href/data-href, a [download] attr, or visible resolution text.
  function hasDlShape(node) {
    var els = [node].concat(Array.prototype.slice.call(node.querySelectorAll("*")));
    for (var i = 0; i < els.length; i++) {
      var e = els[i];
      if (e.hasAttribute && e.hasAttribute("download")) return true;
      var href = "";
      try { href = (e.getAttribute("href") || e.getAttribute("data-href") ||
                    e.getAttribute("data-url") || e.getAttribute("data-src") || ""); }
      catch (x) { href = ""; }
      if (href && DL_RE.test(href)) return true;
      var txt = (e.textContent || "");
      if (RES_RE.test(txt)) return true;
    }
    return false;
  }
  function sigOf(node) {
    var cls = stableClasses(node);
    if (!cls.length) return null;
    var s = node.tagName.toLowerCase();
    for (var i = 0; i < cls.length; i++) s += "." + esc(cls[i]);
    return s;
  }
  function ancestorSig(node) {
    var t = node.tagName.toLowerCase();
    if (node.id && /^[A-Za-z][\w-]*$/.test(node.id) && node.id.length <= 32 &&
        !/[0-9a-fA-F]{12,}/.test(node.id)) return t + "#" + esc(node.id);
    var cls = stableClasses(node);
    if (!cls.length) return null;
    var s = t;
    for (var i = 0; i < cls.length; i++) s += "." + esc(cls[i]);
    return s;
  }
  function scopeIfInflated(rep, sel, gc, gvis) {
    if (gc <= gvis) return sel;
    var anc = rep.parentElement;
    while (anc && anc !== doc.documentElement) {
      var asig = ancestorSig(anc);
      if (asig) {
        var scoped = asig + " " + sel;
        var sc = countOf(scoped), svis = countVisible(scoped);
        if (sc > 0 && sc === svis && svis === gvis) return scoped;
      }
      anc = anc.parentElement;
    }
    return sel;
  }

  // Bucket every classed element by its stable signature.
  var buckets = {};
  var all = doc.querySelectorAll("*");
  for (var i = 0; i < all.length; i++) {
    var node = all[i];
    var sig = sigOf(node);
    if (!sig) continue;
    (buckets[sig] = buckets[sig] || []).push(node);
  }

  var out = [];
  for (var sig in buckets) {
    if (!buckets.hasOwnProperty(sig)) continue;
    var members = buckets[sig];
    if (members.length < 2) continue;            // must repeat
    var vis = 0, rep = null, dl = false, sample = "";
    for (var j = 0; j < members.length; j++) {
      if (visible(members[j])) {
        vis++;
        if (!rep) { rep = members[j]; sample = (members[j].textContent || "").trim().slice(0, 60); }
      }
      if (!dl && hasDlShape(members[j])) dl = true;
    }
    if (vis < 2) continue;                        // need a visible repeating set
    var selector = scopeIfInflated(rep || members[0], sig, members.length, vis);
    // Download-shaped grids dominate; among equals, more visible rows win.
    var score = vis + (dl ? 1000 : 0);
    out.push({ selector: selector, count: members.length, visible: vis,
               has_dl_shape: dl, score: score, sample_text: sample });
  }
  out.sort(function (a, b) { return b.score - a.score; });
  return out.slice(0, max);
}
""".strip()


# ──────────────────────────────────────────────────────────────────────────
# Active one-shot pick bridge
#
# The held-open capture runs in a SEPARATE process (tools/capture_session.py)
# that owns the live Playwright pages; Flask cannot reach those pages directly.
# So the bridge is filesystem-sentinel based, scoped to the capture out_dir
# exactly like FINISH/CANCEL:
#
#   Flask           writes  <out_dir>/PICK_ARM
#   capture on_tick reads    PICK_ARM -> injects ACTIVE_PICK_JS into each live
#                            page (one-shot, preventDefault) -> on the next tick
#                            drains window.__bd_active_pick -> writes
#                            <out_dir>/PICK_RESULT.json and removes PICK_ARM
#   Flask           reads    PICK_RESULT.json (poll/SSE) and consumes it
#
# This is DISTINCT from the observational picker (dom_overlay.picker_script):
# that one is {passive:true} and records descriptors of every click for the
# action timeline; this one is active, single-shot, cancels the click's default
# action, and returns ONE finished selector for the armed draft field.
# ──────────────────────────────────────────────────────────────────────────

import json as _json
import re as _re

_ARM_NAME = "PICK_ARM"
_RESULT_NAME = "PICK_RESULT.json"

# F2.7c: cross-process live-DOM-excerpt sentinels (same out_dir protocol as the
# pick arm/result). Flask drops DOM_REQUEST; the capture's _pump_dom reads the
# live outerHTML, scrubs credential values, writes DOM_RESULT.json, clears the
# request. The excerpt is the operator's own authenticated session DOM going to
# the operator's own AI assist for selector suggestion -- structure is kept,
# credential VALUES (JWT / signed-query / bearer) are scrubbed so they never
# leave the box. Cap mirrors aiassist.suggest_selectors' 16k dom_excerpt clamp.
_DOM_REQ_NAME = "DOM_REQUEST"
_DOM_RESULT_NAME = "DOM_RESULT.json"
_DOM_MAX = 16000

# v3.66.276: auto-detect-row-groups sentinels (same out_dir protocol as the pick
# arm/result). Flask drops AUTO_ROW_REQUEST; the capture on_tick runs
# bdAutoRowGroups against the live page, writes the ranked candidates to
# AUTO_ROW_RESULT.json, and clears the request. Structure-only (selectors +
# counts + sample text) -- no URLs/values leave the box; the result feeds the
# wizard's row_selectors field as a RECOMMENDATION (operator-gated, never
# auto-promoted).
_AUTOROW_REQ_NAME = "AUTO_ROW_REQUEST"
_AUTOROW_RESULT_NAME = "AUTO_ROW_RESULT.json"
_AUTOROW_MAX = 8
# C3 (v3.66.290): LIVE-MIRROR of the HUD's action timeline + verify readout.
# Written (overwrite) by the capture's per-tick pump; read WITHOUT deleting by
# the SPA (it polls a continuously-refreshed mirror, not a one-shot result).
_INSPECT_STATE_NAME = "INSPECT_STATE.json"

# GCW-2: persistent pick OVERLAY (ports learn.py TEACH_OVERLAY_JS into the live
# capture picker). Installed ONCE per document at document-start via
# add_init_script (idempotent: the __bd_pick_overlay_installed guard makes
# re-evaluation each on_tick a no-op). Replaces the old self-removing one-shot
# listener, which (a) was injected only on the next on_tick AFTER arming (the
# operator could click before it existed -> the click fired the real download),
# and (b) removed itself after one click ("doesn't stop real clicks"). The
# persistent listener is always present and survives cross-origin navigation; it
# is GATED on window.__bd_pick_armed (mirrored from the PICK_ARM sentinel by the
# capture on_tick) so it is completely inert outside an active pick.
#
# Behaviour while armed:
#   * hover-highlight: a follow-mouse outline box + candidate-selector label
#     (skips the picker's own nodes; hidden on mouseout and whenever disarmed);
#   * default click  -> preventDefault + stopPropagation (picking a download row
#     does NOT fire the download), record the selector, PINK flash;
#   * shift-click    -> record the selector AND let the click THROUGH (so a click
#     that opens a modal still opens it: two-step download/login flows), CYAN
#     flash, NO preventDefault;
#   * one pick per arm: after any recorded pick the overlay disarms IN-PAGE
#     (window.__bd_pick_armed = false) so an immediate second click is inert
#     until the next arm -- the arm/poll/consume sentinel protocol is unchanged.
# (Pink = recorded-only, cyan = shift-through. The legacy green "live mode" flash
# has no analogue here: every armed pick is a teach-style capture, not a live
# click, so there is no green path.)
ACTIVE_PICK_JS = (
    "(function(){\n"
    + PICK_SELECTOR_JS + "\n"
    + "  if (window.__bd_pick_overlay_installed) { return; }\n"
      "  window.__bd_pick_overlay_installed = true;\n"
      "  if (typeof window.__bd_pick_armed === 'undefined') {\n"
      "    window.__bd_pick_armed = false;\n"
      "  }\n"
      "  var root = document.documentElement || document.body;\n"
      "  if (!root) { return; }\n"
      "  // ── hover overlay (visible only while armed) ──\n"
      "  var hover = document.createElement('div');\n"
      "  hover.id = '__bd_pick_hover';\n"
      "  hover.style.cssText = 'position:fixed;pointer-events:none;"
      "z-index:2147483646;border:2px dashed #ff4d8f;border-radius:3px;"
      "background:rgba(255,77,143,0.06);display:none;';\n"
      "  root.appendChild(hover);\n"
      "  var hoverLabel = document.createElement('div');\n"
      "  hoverLabel.id = '__bd_pick_hover_label';\n"
      "  hoverLabel.style.cssText = 'position:fixed;pointer-events:none;"
      "z-index:2147483647;background:#ff4d8f;color:#fff;"
      "font:11px/1.4 system-ui,sans-serif;padding:3px 6px;border-radius:3px;"
      "max-width:380px;white-space:nowrap;overflow:hidden;"
      "text-overflow:ellipsis;display:none;';\n"
      "  root.appendChild(hoverLabel);\n"
      "  function __bdHideHover(){\n"
      "    hover.style.display = 'none'; hoverLabel.style.display = 'none';\n"
      "  }\n"
      "  document.addEventListener('mousemove', function(e){\n"
      "    if (!window.__bd_pick_armed) { __bdHideHover(); return; }\n"
      "    var t = e.target;\n"
      "    if (!t || t === hover || t === hoverLabel) { return; }\n"
      "    var r;\n"
      "    try { r = t.getBoundingClientRect(); } catch (err) { return; }\n"
      "    if (!r || r.width < 1 || r.height < 1) { return; }\n"
      "    hover.style.display = 'block';\n"
      "    hover.style.left = r.left + 'px'; hover.style.top = r.top + 'px';\n"
      "    hover.style.width = r.width + 'px';\n"
      "    hover.style.height = r.height + 'px';\n"
      "    var sel = '<' + ((t.tagName || '?').toLowerCase()) + '>';\n"
      "    try { var d = bdPickSelector(t);\n"
      "      if (d && d.group_selector) { sel = d.group_selector + ' (x' + d.group_visible + ')'; }\n"
      "      else if (d && d.selector) { sel = d.selector; } }\n"
      "    catch (err2) { /* label falls back to the tag */ }\n"
      "    hoverLabel.textContent = sel;\n"
      "    hoverLabel.style.display = 'block';\n"
      "    hoverLabel.style.left = r.left + 'px';\n"
      "    hoverLabel.style.top = Math.max(0, r.top - 22) + 'px';\n"
      "  }, true);\n"
      "  document.addEventListener('mouseout', function(){ __bdHideHover(); }, true);\n"
      "  // ── colored flash feedback ──\n"
      "  function __bdFlash(el, color){\n"
      "    try {\n"
      "      var old = el.style.outline;\n"
      "      el.style.outline = '3px solid ' + color;\n"
      "      el.style.outlineOffset = '2px';\n"
      "      setTimeout(function(){ el.style.outline = old; }, 350);\n"
      "    } catch (e) { /* never disturb the live page */ }\n"
      "  }\n"
      "  // ── persistent capture-phase click interceptor (gated on armed) ──\n"
      "  function __bdPickClick(ev){\n"
      "    if (!window.__bd_pick_armed) { return; }  // disarmed: click passes through\n"
      "    var r = null;\n"
      "    try { r = bdPickSelector(ev.target); } catch (e) { /* keep going */ }\n"
      "    var shift = !!ev.shiftKey;\n"
      "    if (shift) {\n"
      "      // shift-through: record AND let the click through (open the modal)\n"
      "      __bdFlash(ev.target, '#06b6d4');  // cyan: shift-through\n"
      "    } else {\n"
      "      // default pick: cancel the click so the download does NOT fire\n"
      "      ev.preventDefault(); ev.stopPropagation();\n"
      "      if (ev.stopImmediatePropagation) { ev.stopImmediatePropagation(); }\n"
      "      __bdFlash(ev.target, '#ff4d8f');  // pink: recorded only\n"
      "    }\n"
      "    if (r) { r.ts = Date.now(); r.shift = shift; window.__bd_active_pick = r; }\n"
      "    window.__bd_pick_armed = false;  // one pick per arm\n"
      "    __bdHideHover();\n"
      "  }\n"
      "  document.addEventListener('click', __bdPickClick, { capture:true });\n"
      "  // BUG-8: a <video>/<audio> element (and its native controls) acts on\n"
      "  // POINTER/MOUSE-DOWN before 'click' ever fires -- so clicking a player to\n"
      "  // pick it toggles play/pause and the pick lands on nothing. While armed,\n"
      "  // swallow the earlier pointer/mouse events over a media element (or a node\n"
      "  // inside one) so the real 'click' selector-derivation still wins. Capture\n"
      "  // phase + stopImmediatePropagation blocks the element's own control\n"
      "  // handlers; preventDefault blocks the UA default (play/pause/scrub).\n"
      "  // Non-media targets are untouched (return fast), so normal picks are\n"
      "  // unchanged. shift-through is honored (record-and-pass) here too.\n"
      "  function __bdInMedia(t){\n"
      "    for (var n = t; n && n !== document; n = n.parentNode) {\n"
      "      var tag = (n.tagName || '').toLowerCase();\n"
      "      if (tag === 'video' || tag === 'audio') { return n; }\n"
      "      // Shadow-DOM media controls (e.g. custom players) expose a host with\n"
      "      // a media-ish role/part; treat an explicit media part as media too.\n"
      "      var part = (n.getAttribute && (n.getAttribute('part') || '')) || '';\n"
      "      if (/media|scrubber|play|pause|timeline/i.test(part)) { return n; }\n"
      "    }\n"
      "    return null;\n"
      "  }\n"
      "  function __bdMediaGuard(ev){\n"
      "    if (!window.__bd_pick_armed) { return; }\n"
      "    if (ev.shiftKey) { return; }  // shift-through: let it pass to 'click'\n"
      "    if (!__bdInMedia(ev.target)) { return; }  // non-media: untouched\n"
      "    // Stop the media element/controls from consuming this gesture, but do\n"
      "    // NOT clear the armed flag -- the trailing 'click' still derives the\n"
      "    // selector and disarms once (one pick per arm).\n"
      "    ev.preventDefault();\n"
      "    ev.stopPropagation();\n"
      "    if (ev.stopImmediatePropagation) { ev.stopImmediatePropagation(); }\n"
      "  }\n"
      "  ['pointerdown','mousedown','pointerup','mouseup'].forEach(function(t){\n"
      "    document.addEventListener(t, __bdMediaGuard, { capture:true });\n"
      "  });\n"
      "})();"
)

# Flip the in-page armed flag (mirrors the PICK_ARM sentinel). Kept separate from
# install so the overlay can sit installed-but-inert when no pick is armed.
_SET_ARMED_JS = "(a)=>{ window.__bd_pick_armed = !!a; }"

_READ_AND_CLEAR_JS = (
    "(function(){var r=window.__bd_active_pick;"
    "window.__bd_active_pick=null;return r;})()"
)


def arm_path(out_dir):
    """The PICK_ARM sentinel path for a capture out_dir."""
    from pathlib import Path
    return Path(out_dir) / _ARM_NAME


def result_path(out_dir):
    """The PICK_RESULT.json path for a capture out_dir."""
    from pathlib import Path
    return Path(out_dir) / _RESULT_NAME


# ── Flask side (request handlers) ───────────────────────────────────────────

def arm(out_dir):
    """Request a one-shot active pick on the live capture (Flask -> capture)."""
    import time
    try:
        arm_path(out_dir).write_text(str(int(time.time())), encoding="utf-8")
        return True
    except OSError:
        return False


def is_armed(out_dir):
    try:
        return arm_path(out_dir).exists()
    except OSError:
        return False


def disarm(out_dir):
    """Cancel a pending arm (operator left pick mode before clicking)."""
    try:
        arm_path(out_dir).unlink()
    except OSError:
        pass


def consume_result(out_dir):
    """Read-and-delete PICK_RESULT.json. Returns the pick dict or None."""
    rp = result_path(out_dir)
    try:
        if not rp.exists():
            return None
        data = _json.loads(rp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    try:
        rp.unlink()
    except OSError:
        pass
    return data


# ── v3.66.276: auto-detect-row-groups sentinel (Flask side) ─────────────────

def autorow_req_path(out_dir):
    """The AUTO_ROW_REQUEST sentinel path for a capture out_dir."""
    from pathlib import Path
    return Path(out_dir) / _AUTOROW_REQ_NAME


def autorow_result_path(out_dir):
    """The AUTO_ROW_RESULT.json path for a capture out_dir."""
    from pathlib import Path
    return Path(out_dir) / _AUTOROW_RESULT_NAME


def request_autorows(out_dir):
    """Ask the live capture to auto-detect row groups (Flask -> capture)."""
    import time
    try:
        autorow_req_path(out_dir).write_text(str(int(time.time())), encoding="utf-8")
        return True
    except OSError:
        return False


def consume_autorows(out_dir):
    """Read-and-delete AUTO_ROW_RESULT.json. Returns the ranked list or None."""
    rp = autorow_result_path(out_dir)
    try:
        if not rp.exists():
            return None
        data = _json.loads(rp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    try:
        rp.unlink()
    except OSError:
        pass
    return data


# ── capture-process side (called from the on_tick / _pump_dom hook) ─────────

# Track pages whose document-start init script is already registered, so the
# overlay's add_init_script runs ONCE per page even though the on_tick re-enters
# every second (Playwright has no de-dupe / removal for init scripts).
import weakref as _weakref
_OVERLAY_INSTALLED = _weakref.WeakSet()


def install_pick_overlay(page):
    """Install the persistent pick overlay (hover + capture-phase interceptor) in
    a live page. Idempotent and best-effort:

      * registers ACTIVE_PICK_JS as a document-start init script ONCE per page
        (so the listener survives a cross-origin document swap, present from page
        load -- no arm-lag), and
      * evaluates it on the CURRENT document each call; the in-page
        __bd_pick_overlay_installed guard makes re-evaluation a no-op.

    Does NOT arm -- arming is a separate flag flip (set_pick_armed), so the
    overlay can sit installed-but-inert when no pick is armed.
    """
    try:
        if page not in _OVERLAY_INSTALLED:
            try:
                page.add_init_script(ACTIVE_PICK_JS)
            except Exception:
                pass
            _OVERLAY_INSTALLED.add(page)
        page.evaluate(ACTIVE_PICK_JS)
        return True
    except Exception:
        return False


def set_pick_armed(page, armed):
    """Flip the in-page window.__bd_pick_armed flag. Best-effort. The capture
    on_tick mirrors the PICK_ARM filesystem sentinel into this flag so the
    persistent listener acts only during an active pick."""
    try:
        page.evaluate(_SET_ARMED_JS, bool(armed))
        return True
    except Exception:
        return False


def inject_active_pick(page):
    """Install the overlay AND arm THIS document in one call. Best-effort.

    Used by the direct-call path (and the existing Playwright bridge test); the
    cross-process on_tick uses install_pick_overlay + set_pick_armed instead,
    mirroring the PICK_ARM sentinel."""
    ok = install_pick_overlay(page)
    set_pick_armed(page, True)
    return ok


def read_active_pick(page):
    """Read-and-clear the in-page pick result for a page. Best-effort."""
    try:
        return page.evaluate(_READ_AND_CLEAR_JS)
    except Exception:
        return None


def maybe_arm_and_collect(pages, out_dir):
    """One on_tick step. Installs the persistent pick overlay on every live page
    (idempotent, from page load), mirrors the PICK_ARM sentinel into the in-page
    window.__bd_pick_armed flag, and -- while armed -- drains any in-page result.
    On the first result, writes PICK_RESULT.json and removes PICK_ARM. Returns the
    result dict or None.

    The overlay is installed UNCONDITIONALLY (even when not armed) so the
    capture-phase listener pre-exists before the operator arms+clicks; it stays
    completely inert (no preventDefault, no hover) until __bd_pick_armed is set.
    The ~1s lag between the SPA arming (PICK_ARM written) and this tick flipping
    the in-page flag is inherent to the sentinel/on_tick transport, and is far
    shorter than the operator's own arm->move-mouse->click latency.

    Best-effort throughout: a failure here must never disturb or take down the
    capture (mirrors the observational picker / DOM-drain contract).
    """
    ap = arm_path(out_dir)
    try:
        armed = ap.exists()
    except OSError:
        return None

    # v3.66.276: service a pending auto-detect-rows request on the same tick.
    # Best-effort + isolated so it can never disturb the pick path or the
    # capture (mirrors the DOM-drain contract). The guard call site in
    # tools/capture_session.py is unchanged -- it already calls this once per
    # tick; the new behavior rides inside this NON-guard function.
    try:
        maybe_suggest_rows(pages, out_dir)
    except Exception:
        pass

    result = None
    for pg in list(pages or []):
        install_pick_overlay(pg)       # persistent overlay, present from load
        set_pick_armed(pg, armed)      # mirror the PICK_ARM sentinel in-page
        if armed:
            r = read_active_pick(pg)
            if r and result is None:
                result = r

    if result is not None:
        try:
            result_path(out_dir).write_text(
                _json.dumps(result), encoding="utf-8")
        except OSError:
            pass
        try:
            ap.unlink()
        except OSError:
            pass
    return result


def maybe_suggest_rows(pages, out_dir):
    """One on_tick step for the AUTO_ROW_REQUEST sentinel. When present, run the
    in-page bdAutoRowGroups detector against the first live page, write the
    ranked candidates to AUTO_ROW_RESULT.json, and clear the request. Returns the
    ranked list or None.

    Structure-only output (selectors + counts + sample text); no URLs/values.
    Best-effort: any failure leaves the request in place for a later tick and
    never disturbs the capture.
    """
    req = autorow_req_path(out_dir)
    try:
        if not req.exists():
            return None
    except OSError:
        return None
    groups = None
    for pg in list(pages or []):
        try:
            groups = pg.evaluate(
                "() => { %s ; return bdAutoRowGroups(document, {max: %d}); }"
                % (AUTO_ROW_GROUPS_JS, _AUTOROW_MAX)
            )
        except Exception:
            groups = None
        if groups:
            break
    if groups is None:
        groups = []
    try:
        autorow_result_path(out_dir).write_text(
            _json.dumps(groups), encoding="utf-8")
    except OSError:
        return None
    try:
        req.unlink()
    except OSError:
        pass
    return groups


# ── F2.7c: live DOM-excerpt for the capture-console AI assist ───────────────

def dom_req_path(out_dir):
    """The DOM_REQUEST sentinel path for a capture out_dir."""
    from pathlib import Path
    return Path(out_dir) / _DOM_REQ_NAME


def dom_result_path(out_dir):
    """The DOM_RESULT.json path for a capture out_dir."""
    from pathlib import Path
    return Path(out_dir) / _DOM_RESULT_NAME


# Live-DOM excerpt scrub. The excerpt LEAVES the capture process (written to
# DOM_RESULT.json and surfaced in the pick panel), so it must not carry the
# CAP-01 I0008 hard-credential floor. We reuse the canonical floor redactor
# (capture_artifact_redact) so this sink cannot drift from CAP-01, plus a few
# DOM-specific passes for the credential forms the string-floor structurally
# cannot key off: Authorization: headers (colon form, not key=value) and
# <input>/<meta> values keyed by the field NAME rather than a key=value pair.
# VALUES only -- the tags / classes / data-* KEYS selectors key off are kept.
# (F-CBD03-02)
try:
    from .capture_artifact_redact import (redact_value as _canon_redact,
                                           _kv_key_is_secret as _floor_key_is_secret,
                                           PLACEHOLDER as _SCRUB_PH)
except Exception:  # pragma: no cover - app not importable (tooling context)
    _canon_redact = None
    _SCRUB_PH = "<scrubbed>"

    def _floor_key_is_secret(_k):
        return False

# Explicit JWT / token= / Signature= belt. Also covered by the canonical floor
# above; kept so the excerpt stays defended on the fallback path where
# capture_artifact_redact is unavailable.
_CRED_SUBS = [
    (_re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{6,}(?:\.[A-Za-z0-9_-]{2,})?"),
     _SCRUB_PH),
    (_re.compile(
        r"((?:access_token|token|signature|sig|bearer)=)[A-Za-z0-9._%-]{6,}", _re.I),
     r"\g<1>" + _SCRUB_PH),
    (_re.compile(r"(Signature=)[A-Za-z0-9%+/=._-]{6,}"), r"\g<1>" + _SCRUB_PH),
]
# Authorization / Proxy-Authorization header credential (colon form).
_AUTH_HDR_RE = _re.compile(
    r"((?:Proxy-)?Authorization\s*:\s*(?:[A-Za-z]+\s+)?)([A-Za-z0-9._~+/=-]{6,})", _re.I)
# <input>/<meta> value keyed by a floor-secret field NAME (either attr order).
_INPUT_NAME_VALUE_RE = _re.compile(
    r'(<(?:input|meta)\b[^>]*?\bname\s*=\s*["\']?)([\w:.-]+)'
    r'(["\']?[^>]*?\b(?:value|content)\s*=\s*["\'])([^"\']*)(["\'])', _re.I)
_INPUT_VALUE_NAME_RE = _re.compile(
    r'(<(?:input|meta)\b[^>]*?\b(?:value|content)\s*=\s*["\'])([^"\']*)'
    r'(["\'][^>]*?\bname\s*=\s*["\']?)([\w:.-]+)(["\']?)', _re.I)


def _scrub_dom_excerpt(html):
    """Strip credential VALUES from a live-DOM excerpt, preserving structure.
    Reuses the canonical CAP-01 I0008 floor plus DOM-specific passes so the
    excerpt cannot carry a hard credential off the capture process. Best-effort
    string pass -- never raises; on error returns ``""`` rather than risk
    leaking an unscrubbed blob (F-CBD03-02)."""
    if not html:
        return ""
    try:
        # 1. canonical floor: signed-query / key=secret / JWT / opaque / email /
        #    userinfo VALUES (code / state / api_key / nonce / otp / csrf / token
        #    / signature / bearer in key=value or query form, anchored by the SoT).
        if _canon_redact is not None:
            html = _canon_redact(html)
        # 2. explicit JWT / token= / Signature= belt (fallback defence).
        for rx, repl in _CRED_SUBS:
            html = rx.sub(repl, html)
        # 3. Authorization: <scheme> <token> header form.
        html = _AUTH_HDR_RE.sub(lambda m: m.group(1) + _SCRUB_PH, html)
        # 4. <input>/<meta> value keyed by a floor-secret NAME (both orders).
        html = _INPUT_NAME_VALUE_RE.sub(
            lambda m: (m.group(1) + m.group(2) + m.group(3) + _SCRUB_PH + m.group(5)
                       if _floor_key_is_secret(m.group(2)) else m.group(0)), html)
        html = _INPUT_VALUE_NAME_RE.sub(
            lambda m: (m.group(1) + _SCRUB_PH + m.group(3) + m.group(4) + m.group(5)
                       if _floor_key_is_secret(m.group(4)) else m.group(0)), html)
        return html
    except Exception:
        return ""


def request_dom(out_dir):
    """Flask -> capture: request a one-shot live-DOM excerpt. Returns True iff
    the DOM_REQUEST sentinel was written."""
    import time
    try:
        dom_req_path(out_dir).write_text(str(int(time.time())), encoding="utf-8")
        return True
    except OSError:
        return False


def dom_requested(out_dir):
    try:
        return dom_req_path(out_dir).exists()
    except OSError:
        return False


def clear_dom_request(out_dir):
    """Cancel a pending DOM request (operator navigated away / closed)."""
    try:
        dom_req_path(out_dir).unlink()
    except OSError:
        pass


def consume_dom_result(out_dir):
    """Flask side: read-and-delete DOM_RESULT.json. Returns {html,url} or None."""
    rp = dom_result_path(out_dir)
    try:
        if not rp.exists():
            return None
        data = _json.loads(rp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    try:
        rp.unlink()
    except OSError:
        pass
    return data


def read_dom_excerpt(page):
    """Capture side: read the live page's outerHTML + url. Best-effort; returns
    a scrubbed/clamped {html,url} dict or None."""
    try:
        data = page.evaluate(
            "(function(){try{return {"
            "html:(document.documentElement?document.documentElement.outerHTML:'')||'',"
            "url:(location&&location.href)||''};}catch(e){return null;}})()")
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    html = _scrub_dom_excerpt(data.get("html") or "")[:_DOM_MAX]
    return {"html": html, "url": data.get("url") or ""}


# ── C3: live-mirror of the HUD action timeline + verify readout ───────────
# Unlike PICK_RESULT / DOM_RESULT (one-shot, read-and-delete), this is a
# continuously-refreshed MIRROR: the capture's per-tick pump overwrites it and
# the SPA polls it without consuming. Content is structure-only (correlate_
# timeline / verify_summary already emit no values), so it is safe to surface.

def inspect_state_path(out_dir):
    """The INSPECT_STATE.json path for a capture out_dir."""
    from pathlib import Path
    return Path(out_dir) / _INSPECT_STATE_NAME


def write_inspect_state(out_dir, state):
    """Capture side: overwrite the live inspect-state mirror. Best-effort;
    returns True on success, False on any OSError/serialisation failure (a
    write failure must never disturb the capture)."""
    try:
        inspect_state_path(out_dir).write_text(
            _json.dumps(state), encoding="utf-8")
        return True
    except (OSError, TypeError, ValueError):
        return False


def read_inspect_state(out_dir):
    """Flask side: read the live inspect-state mirror WITHOUT deleting it
    (it is polled repeatedly). Returns the dict, or None if absent/unreadable."""
    sp = inspect_state_path(out_dir)
    try:
        if not sp.exists():
            return None
        return _json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def maybe_collect_dom(pages, out_dir):
    """One on_tick step. If DOM_REQUEST is present: read the excerpt from the
    first usable page, write DOM_RESULT.json, clear DOM_REQUEST. Returns the
    result dict or None. Best-effort throughout -- a failure here must never
    disturb or take down the capture (mirrors maybe_arm_and_collect)."""
    rq = dom_req_path(out_dir)
    try:
        if not rq.exists():
            return None
    except OSError:
        return None

    result = None
    for pg in list(pages or []):
        r = read_dom_excerpt(pg)
        if r and r.get("html") and result is None:
            result = r

    if result is not None:
        try:
            dom_result_path(out_dir).write_text(
                _json.dumps(result), encoding="utf-8")
        except OSError:
            pass
        try:
            rq.unlink()
        except OSError:
            pass
    return result
