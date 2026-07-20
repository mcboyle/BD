#!/usr/bin/env python3
"""MOD-1 C-8 (KASM-T10): does the live-X (Arch B / KasmVNC) takeover browser
present a materially different -- worse -- fingerprint than the headless (Arch A)
one? This is the counter-tell: it can invert the rationale for Arch B on some
targets, so MEASURE it, do not assume.

The tool samples the surface a bot-check actually reads from a browser launched
headful on an X display (the takeover browser) and from the same browser launched
headless, then diffs them.

The honest part (CLAUDE.md 0/1): the WebGL renderer -- the property most likely to
flag live-X as datacenter hardware -- is **GPU-dependent**. On a GPU-less host both
modes report a software renderer (SwiftShader/llvmpipe), so the delta collapses and a
sandbox run UNDERSTATES the real-hardware magnitude. The tool detects that and says
so, rather than reporting a small delta as if it were the answer. Run it on the box
(real GPU) for the number that matters.

Pure functions (``diff_fingerprints`` / ``verdict`` / ``is_gpu_less``) carry the
logic and are unit-tested; the browser launch + CLI live below them.

    python3 tools/kasm_fingerprint_probe.py --display :5           # headful on :5 vs headless
    python3 tools/kasm_fingerprint_probe.py --display :5 --json out.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

# The properties a bot-check samples. Kept as data so the diff/verdict logic and
# the JS probe stay in step.
FINGERPRINT_KEYS = (
    "webgl_vendor", "webgl_renderer", "screen", "avail_screen", "color_depth",
    "device_pixel_ratio", "hardware_concurrency", "device_memory", "webdriver",
    "user_agent", "platform", "vendor", "languages", "timezone", "max_touch_points",
    "plugins_count", "webgl_extensions_count", "canvas_hash",
)

# WebGL vendor/renderer only carry signal on real GPU hardware; a GPU-less host
# reports a software renderer in BOTH modes, so a delta here is unmeasurable in
# sandbox -- flag it rather than pretend it is zero.
_GPU_DEPENDENT = frozenset({"webgl_vendor", "webgl_renderer"})

# Properties bot-checks weight heavily as automation tells.
_KNOWN_TELLS = frozenset({"webdriver", "user_agent"})

# Software-renderer signatures: if the WebGL renderer matches one, there is no GPU
# to expose, so the live-X vs headless renderer delta cannot be observed here.
_SOFTWARE_RENDERER_MARKERS = ("swiftshader", "llvmpipe", "software", "mesa")

# The in-page probe. Returns a flat dict keyed by FINGERPRINT_KEYS. canvas_hash is
# left to Python (a stable digest of the toDataURL) so the JS stays simple.
FINGERPRINT_JS = r"""() => {
  function gl() {
    try {
      const c = document.createElement('canvas');
      const g = c.getContext('webgl') || c.getContext('experimental-webgl');
      if (!g) return {vendor: null, renderer: null, ext: 0};
      const dbg = g.getExtension('WEBGL_debug_renderer_info');
      const exts = g.getSupportedExtensions() || [];
      return {
        vendor: dbg ? g.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null,
        renderer: dbg ? g.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null,
        ext: exts.length,
      };
    } catch (e) { return {vendor: null, renderer: null, ext: 0}; }
  }
  function canvas() {
    try {
      const c = document.createElement('canvas');
      c.width = 240; c.height = 60;
      const ctx = c.getContext('2d');
      ctx.textBaseline = 'top';
      ctx.font = "14px 'Arial'";
      ctx.fillStyle = '#f60'; ctx.fillRect(2, 2, 100, 20);
      ctx.fillStyle = '#069'; ctx.fillText('BD-KASM-T10 ☃', 4, 4);
      return c.toDataURL();
    } catch (e) { return ''; }
  }
  const w = gl();
  return {
    webgl_vendor: w.vendor,
    webgl_renderer: w.renderer,
    webgl_extensions_count: w.ext,
    screen: [screen.width, screen.height],
    avail_screen: [screen.availWidth, screen.availHeight],
    color_depth: screen.colorDepth,
    device_pixel_ratio: window.devicePixelRatio,
    hardware_concurrency: navigator.hardwareConcurrency,
    device_memory: navigator.deviceMemory || null,
    webdriver: navigator.webdriver,
    user_agent: navigator.userAgent,
    platform: navigator.platform,
    vendor: navigator.vendor,
    languages: (navigator.languages || []).join(','),
    timezone: (Intl.DateTimeFormat().resolvedOptions().timeZone) || null,
    max_touch_points: navigator.maxTouchPoints,
    plugins_count: (navigator.plugins || []).length,
    _canvas_raw: canvas(),
  };
}"""


# ── pure logic (unit-tested) ─────────────────────────────────────────────────

def is_gpu_less(sample: dict) -> bool:
    """True when the sample shows no real GPU -- either a software rasterizer or
    no renderer string at all (unknown, so we cannot claim a GPU is present).
    Without a GPU the live-X vs headless renderer delta (the strongest tell)
    cannot be observed. A sandbox run is gpu-less; the box is not."""
    r = str((sample or {}).get("webgl_renderer") or "").lower()
    if not r:
        return True  # unknown renderer -> conservatively assume no observable GPU
    return any(m in r for m in _SOFTWARE_RENDERER_MARKERS)


def diff_fingerprints(headful: dict, headless: dict) -> list:
    """Per-property delta between the headful-on-X sample and the headless sample.
    Each row: key, both values, whether they differ, and whether the property is
    GPU-dependent (delta unobservable without a GPU) or a known automation tell."""
    headful = headful or {}
    headless = headless or {}
    keys = [k for k in FINGERPRINT_KEYS if k in headful or k in headless]
    rows = []
    for k in keys:
        hv, lv = headful.get(k), headless.get(k)
        rows.append({
            "key": k,
            "headful": hv,
            "headless": lv,
            "differs": hv != lv,
            "gpu_dependent": k in _GPU_DEPENDENT,
            "known_tell": k in _KNOWN_TELLS,
        })
    return rows


def verdict(diff: list, headful: dict, headless: dict) -> dict:
    """Summarize the delta honestly. Does NOT auto-declare 'material' -- it reports
    the differing properties, flags the GPU-dependent ones a sandbox cannot see, and
    calls out whether headful actually clears the two headline tells (webdriver, UA).
    The per-target 'is this worse' judgement is the operator's, informed by this."""
    differ = [d for d in diff if d["differs"]]
    # The HEADFUL sample reveals whether the host has a GPU -- headless ALWAYS
    # masks to a software renderer, so it is not the indicator. If headful shows a
    # real GPU, the renderer delta is observable and this run is not gpu-less.
    gpu_less = is_gpu_less(headful)
    wd = next((d for d in diff if d["key"] == "webdriver"), None)
    ua = next((d for d in diff if d["key"] == "user_agent"), None)
    return {
        "properties_sampled": len(diff),
        "properties_differ": len(differ),
        "differing_keys": [d["key"] for d in differ],
        # a GPU-less run cannot observe the renderer delta; the number is a floor.
        "gpu_less_run": gpu_less,
        "gpu_dependent_delta_observable": (not gpu_less) and any(
            d["differs"] for d in diff if d["gpu_dependent"]),
        # headful is supposed to look more human; check the two headline tells.
        "webdriver_true_in_both": bool(wd) and wd["headful"] is True and wd["headless"] is True,
        "user_agent_headless_leaked": bool(ua) and "Headless" in str(ua["headless"] or ""),
        "user_agent_fixed_by_headful": bool(ua) and "Headless" in str(ua["headless"] or "")
                                       and "Headless" not in str(ua["headful"] or ""),
        "note": (
            "GPU-less run: the WebGL renderer reads as a software rasterizer in BOTH "
            "modes, so the delta that would flag live-X as datacenter hardware is NOT "
            "observable here. This understates the real-hardware magnitude -- rerun on "
            "the box (real GPU) for the number that can invert the case for Arch B."
        ) if gpu_less else (
            "Real-GPU run: the WebGL renderer delta below is the material signal."
        ),
    }


def _canvas_hash(raw: str) -> str:
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()[:16]


def normalize_sample(raw_sample: dict) -> dict:
    """Fold the JS probe's ``_canvas_raw`` into a stable ``canvas_hash`` and drop
    the bulky raw value. Pure."""
    out = dict(raw_sample or {})
    out["canvas_hash"] = _canvas_hash(out.pop("_canvas_raw", ""))
    return out


# ── browser launch + CLI (verified live, not unit-tested) ────────────────────

_ANTI_AUTOMATION_ARGS = [
    "--no-sandbox", "--disable-notifications", "--disable-popup-blocking",
    "--disable-infobars", "--no-default-browser-check", "--no-first-run",
    "--disable-features=PushMessaging,Translate,AutomationControlled",
    "--disable-blink-features=AutomationControlled", "--window-size=1366,800",
]


def _sample(headless: bool, display: str | None, executable_path: str | None) -> dict:
    """Launch a browser (headful on ``display`` when headless is False) and run the
    probe. Mirrors the takeover browser's anti-automation args so the measured
    fingerprint reflects the real Arch B / Arch A browser, not a vanilla one."""
    from playwright.sync_api import sync_playwright
    env = dict(os.environ)
    if not headless and display:
        env["DISPLAY"] = display
    kw = {"headless": headless, "args": _ANTI_AUTOMATION_ARGS, "env": env}
    if executable_path:
        kw["executable_path"] = executable_path
    with sync_playwright() as pw:
        browser = pw.chromium.launch(**kw)
        try:
            page = browser.new_page()
            page.goto("about:blank")
            return normalize_sample(page.evaluate(FINGERPRINT_JS))
        finally:
            browser.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MOD-1 C-8 KASM-T10 fingerprint delta")
    ap.add_argument("--display", default=":5",
                    help="X display for the headful (Arch B) sample, e.g. :5")
    ap.add_argument("--executable-path", default=os.environ.get("BD_VNC_CHROME") or None,
                    help="pin the chromium binary (else Playwright's default)")
    ap.add_argument("--json", default=None, help="write the full report to this path")
    args = ap.parse_args(argv)

    headful = _sample(headless=False, display=args.display,
                      executable_path=args.executable_path)
    headless = _sample(headless=True, display=None,
                       executable_path=args.executable_path)
    diff = diff_fingerprints(headful, headless)
    v = verdict(diff, headful, headless)

    report = {"headful": headful, "headless": headless, "diff": diff, "verdict": v}
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2, default=str)

    print("== KASM-T10 fingerprint delta (headful-on-X vs headless) ==")
    print(v["note"])
    print(f"\nproperties differing: {v['properties_differ']}/{v['properties_sampled']} "
          f"-> {', '.join(v['differing_keys']) or '(none)'}")
    print(f"gpu-less run: {v['gpu_less_run']}   "
          f"webdriver true in BOTH: {v['webdriver_true_in_both']}   "
          f"headless UA leaked: {v['user_agent_headless_leaked']}")
    print("\nkey                     headful                              headless")
    for d in diff:
        if d["differs"]:
            tag = " [GPU]" if d["gpu_dependent"] else (" [TELL]" if d["known_tell"] else "")
            print(f"  {d['key']:<22}{str(d['headful'])[:34]:<36}{str(d['headless'])[:30]}{tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
