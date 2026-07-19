"""EME (Encrypted Media Extensions) page-hook recorder -- DETECTION ONLY.

Installed at document-start on every BrowserContext (runner_browser._install_stealth).
The init script hooks ``navigator.requestMediaKeySystemAccess`` and records the
requested key-system on ``window.__bd_eme`` -- then calls straight through to the
real implementation. It is pure OBSERVATION: it never requests a license, drives a
CDM, extracts keys, or alters playback. It exists so a page that uses EME/CDM-DRM
without a parseable manifest is still labeled (via drm_detect.classify_protection).

This is the charter's existing "detect, never defeat" floor. See
DRM_EME_DETECTION_DECISION.md.
"""
from __future__ import annotations

from typing import Any, Dict, List

# Document-start hook. Idempotent (guard flag), defensive (never throws into the
# page), and transparent (records then calls the original through unchanged).
EME_INIT_JS = r"""
(() => {
  try {
    if (window.__bd_eme_installed) return;
    window.__bd_eme_installed = true;
    window.__bd_eme = [];
    var nav = navigator;
    if (nav && typeof nav.requestMediaKeySystemAccess === 'function') {
      var orig = nav.requestMediaKeySystemAccess.bind(nav);
      nav.requestMediaKeySystemAccess = function(keySystem, configs) {
        try {
          window.__bd_eme.push({
            keySystem: String(keySystem),
            configs: (configs && configs.length) || 0,
            ts: Date.now()
          });
        } catch (e) {}
        return orig(keySystem, configs);
      };
    }
  } catch (e) {}
})();
"""


def read_eme_records(page) -> List[Dict[str, Any]]:
    """Read the recorded EME calls from a live Playwright page. Thin + fail-safe."""
    try:
        recs = page.evaluate("window.__bd_eme || []")
        return recs if isinstance(recs, list) else []
    except Exception:
        return []


def classify_eme_records(records: List[Any]) -> List[Dict[str, Any]]:
    """Map each recorded EME key-system to its protection classification (via
    drm_detect). Returns one entry per recorded call. Detection only."""
    from .drm_detect import classify_protection
    out: List[Dict[str, Any]] = []
    for r in records or []:
        ks = r.get("keySystem") if isinstance(r, dict) else str(r)
        if not ks:
            continue
        prot = classify_protection(key_system=ks)
        out.append({"key_system": ks, "system": prot["system"],
                    "category": prot["category"]})
    return out


_CAT_RANK = {"cdm-drm": 3, "clearkey": 2, "downloadable-aes": 1, "none": 0}


def summarize_eme(records: List[Any]) -> Dict[str, Any]:
    """Collapse recorded EME calls into one signal: whether EME was used, the
    strongest protection category seen, and the distinct systems. Detection only."""
    classified = classify_eme_records(records)
    if not classified:
        return {"eme_used": False, "category": "none", "systems": []}
    top = max((c["category"] for c in classified), key=lambda c: _CAT_RANK.get(c, 0))
    systems = sorted({c["system"] for c in classified if c["system"]})
    return {"eme_used": True, "category": top, "systems": systems}
