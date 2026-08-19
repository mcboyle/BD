#!/usr/bin/env python3
"""SPA render harness — screenshots a route of the live SPA in headless chromium.

Usage: python3 spa_shot.py [route] [outprefix]
  route     SPA path (default "/")
  outprefix output filename prefix (default "home")

Captures desktop (1280x900) + mobile (390x844). Waits for react-query to settle.
Assumes the Flask backend is serving the built dist on 127.0.0.1:5599.
"""
import sys, asyncio, os
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:5599"
ROUTE = sys.argv[1] if len(sys.argv) > 1 else "/"
PREFIX = sys.argv[2] if len(sys.argv) > 2 else "home"
ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
OUT = os.environ.get("BD_OUT", os.path.join(ROOT, "reports", "render"))

VIEWPORTS = [("desktop", 1280, 900), ("mobile", 390, 844)]

async def shot(pw, name, w, h):
    browser = await pw.chromium.launch()
    ctx = await browser.new_context(viewport={"width": w, "height": h},
                                    device_scale_factor=1)
    page = await ctx.new_page()
    errors = []
    page.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    await page.goto(BASE + ROUTE, wait_until="domcontentloaded")
    # let react mount + react-query resolve
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    await page.wait_for_timeout(1200)
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, f"{PREFIX}_{name}.png")
    await page.screenshot(path=out, full_page=True)
    # quick measurements
    dims = await page.evaluate("""() => {
        const b = document.body;
        const main = document.querySelector('main') || document.querySelector('#root');
        const h1 = document.querySelector('h1');
        return {
            bodyW: b.scrollWidth, bodyH: b.scrollHeight,
            mainW: main ? Math.round(main.getBoundingClientRect().width) : null,
            h1: h1 ? h1.textContent.trim().slice(0,60) : null,
            overflowX: b.scrollWidth > window.innerWidth + 2
        };
    }""")
    await browser.close()
    return out, dims, errors

async def main():
    async with async_playwright() as pw:
        for name, w, h in VIEWPORTS:
            out, dims, errors = await shot(pw, name, w, h)
            print(f"[{name} {w}x{h}] -> {out}")
            print(f"   dims: {dims}")
            if errors:
                print(f"   console errors ({len(errors)}): " + " | ".join(errors[:5]))
            else:
                print("   console errors: none")

asyncio.run(main())
