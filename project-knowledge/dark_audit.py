#!/usr/bin/env python3
"""Dark-mode render audit -- control BACKGROUNDS and muted TEXT.

v3.66.805: absorbs the former `dark_text_audit.py` behind `--target`. The two
checks answer different questions about the same render and were split only by
history:

  bg    a control (input/textarea/select) computing a LIGHT background on a dark
        page -- a token leak. Luminance composited over the dark surface; > 0.6
        is a leak. Fully-transparent controls INHERIT and are not leaks.
  text  a `.text-muted-foreground` element computing NEAR-WHITE (all RGB > 200)
        in dark mode. The original bug: the utility was inert (unknown), so it
        inherited --ink and rendered near-white, collapsing the muted hierarchy.

DEFAULT IS `bg`, so the bare `python3 dark_audit.py` that `spa_render.sh:84`
invokes is behaviour-identical to the pre-805 script. `--target both` runs the
pair in one browser launch.

Exit: 0 clean, 1 findings, 2 could-not-evaluate. A check that cannot see its
subject must SAY SO rather than report clean -- both arms return UNKNOWN
(exit 2) on a zero denominator, because "0 findings of 0 examined" is a green
that proves nothing.

Assumes the backend is serving the built dist on 127.0.0.1:5599.
"""
import argparse
import asyncio
import re
import sys

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:5599"

BG_PAGES = ["/", "/sites", "/queue", "/activity", "/settings", "/secrets", "/vpn",
            "/integrations", "/imports", "/batch-ops", "/ai-teach", "/rebalance",
            "/maintenance", "/import-views", "/notifications", "/pools-macros",
            "/templates", "/library", "/history", "/dashboard"]

TEXT_ROUTES = ["/settings", "/history", "/library", "/queue", "/sites", "/vpn",
               "/backup"]


def lum(rgb):
    """Relative luminance of a computed colour, composited over the dark surface.
    Returns None for transparent (inherits -- not a leak) or unparseable input."""
    m = re.findall(r"[\d.]+", rgb)
    if len(m) < 3:
        return None
    r, g, b = float(m[0]), float(m[1]), float(m[2])
    a = float(m[3]) if len(m) > 3 else 1.0
    if a < 0.05:
        return None  # transparent: inherits, not a leak

    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    L = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    return a * L + (1 - a) * 0.02  # composite over dark (#14161c ~ 0.08)


async def _dark_ctx(browser):
    ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
    await ctx.add_init_script(
        "try{localStorage.setItem('bd-theme','dark')}catch(e){}")
    return ctx


async def audit_bg(browser):
    """Control backgrounds. Returns (leaks, examined)."""
    leaks = []
    examined = 0
    for route in BG_PAGES:
        ctx = await _dark_ctx(browser)
        p = await ctx.new_page()
        try:
            await p.goto(BASE + route, wait_until="domcontentloaded", timeout=15000)
            try:
                await p.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            await p.wait_for_timeout(700)
            ctrls = await p.evaluate("""() => {
                const out=[];
                for (const e of document.querySelectorAll('input,textarea,select')) {
                    const s=getComputedStyle(e);
                    if (e.type==='checkbox'||e.type==='radio'||e.type==='range'||e.type==='hidden') continue;
                    out.push({tag:e.tagName.toLowerCase(), type:e.type||'', bg:s.backgroundColor, cls:(e.className||'').slice(0,40)});
                }
                return out;
            }""")
            examined += len(ctrls)
            for c in ctrls:
                L = lum(c["bg"])
                if L is not None and L > 0.6:
                    leaks.append((route, c["tag"], c["type"], c["bg"], c["cls"]))
        except Exception as e:  # noqa: BLE001
            leaks.append((route, "LOAD", "FAIL", str(e)[:40], ""))
        await ctx.close()

    print("=== DARK-MODE CONTROL LEAKS (light bg on dark page) ===")
    if not examined:
        print("  UNKNOWN -- 0 controls examined; the page did not render or the "
              "selector matched nothing. NOT a pass.")
    elif not leaks:
        print(f"  NONE -- {examined} controls examined, all dark/transparent.")
    else:
        for route, tag, typ, bg, cls in leaks:
            print(f"  {route:16} {tag}[{typ}] bg={bg:28} .{cls}")
    return leaks, examined


async def audit_text(browser):
    """Muted text colour. Returns (nearwhite, examined)."""
    total = 0
    nearwhite = 0
    ctx = await _dark_ctx(browser)
    p = await ctx.new_page()
    print("=== DARK muted-text audit (text-muted-foreground) ===")
    for r in TEXT_ROUTES:
        try:
            await p.goto(BASE + r, wait_until="domcontentloaded", timeout=15000)
            await p.wait_for_timeout(800)
            res = await p.evaluate("""() => {
                const els = [...document.querySelectorAll('.text-muted-foreground')];
                let nw = 0;
                for (const e of els) {
                    const c = getComputedStyle(e).color;
                    const m = c.match(/\\d+/g);
                    if (m && +m[0] > 200 && +m[1] > 200 && +m[2] > 200) nw++;
                }
                return { n: els.length, nw };
            }""")
        except Exception as e:  # noqa: BLE001
            print(f"  {r:12s} LOAD FAIL {str(e)[:40]}")
            continue
        total += res["n"]
        nearwhite += res["nw"]
        flag = "" if res["nw"] == 0 else f"  <-- {res['nw']} near-white"
        print(f"  {r:12s} muted-text els={res['n']:3d}  near-white={res['nw']:3d}{flag}")
    await ctx.close()
    print(f"  TOTAL muted-text els={total}  near-white={nearwhite}")
    if total == 0:
        print("  RESULT: UNKNOWN -- 0 muted elements examined across every route. "
              "'0 near-white of 0' is not a pass; the selector or the render is wrong.")
    else:
        print("  RESULT:", "PASS -- no near-white muted text" if nearwhite == 0
              else f"FAIL -- {nearwhite} near-white")
    return nearwhite, total


async def main(target):
    rc = 0
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        if target in ("bg", "both"):
            leaks, examined = await audit_bg(b)
            if not examined:
                rc = max(rc, 2)
            elif leaks:
                rc = max(rc, 1)
        if target in ("text", "both"):
            nearwhite, total = await audit_text(b)
            if not total:
                rc = max(rc, 2)
            elif nearwhite:
                rc = max(rc, 1)
        await b.close()
    return rc


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--target", choices=("bg", "text", "both"), default="bg",
                    help="bg (default, pre-805 behaviour) | text | both")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.target)))
