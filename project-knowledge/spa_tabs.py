#!/usr/bin/env python3
"""Capture every nav tab of the SPA at desktop width, uniform viewport tiles.
Boots nothing itself — expects backend on 127.0.0.1:5599 (the wrapper boots it).
Writes /home/claude/tabs/<slug>.png and prints a per-tab report (overflow + errors).
"""
import asyncio, os
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:5599"
THEME = os.environ.get("BD_THEME", "light")   # 'light' | 'dark'
OUT = os.environ.get("BD_OUT", "/home/claude/tabs")
W, H = 1280, 900

TABS = [
    ("/", "01_home", "Home"),
    ("/sites", "02_sites", "Sites"),
    ("/queue", "03_queue", "Queue"),
    ("/activity", "04_activity", "Activity"),
    ("/settings", "05_settings", "Settings"),
    ("/capture", "06_capture", "Capture"),
    ("/needs-review", "07_needsreview", "Needs review"),
    ("/library", "08_library", "Library"),
    ("/maintenance", "09_maintenance", "Maintenance"),
    ("/batch-ops", "10_batchops", "Batch ops"),
    ("/history", "11_history", "History"),
    ("/imports", "12_imports", "Imports"),
    ("/dedup", "13_dedup", "Dedup"),
    ("/rebalance", "14_rebalance", "Rebalance"),
    ("/vpn", "15_vpn", "VPN"),
    ("/integrations", "16_integrations", "Integrations"),
    ("/secrets", "17_secrets", "Secrets"),
    ("/notifications", "18_notifications", "Notifications"),
    ("/templates", "19_templates", "Templates"),
    ("/pools-macros", "20_poolsmacros", "Pools & macros"),
    ("/ai-teach", "21_aiteach", "AI repair"),
]

async def main():
    os.makedirs(OUT, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        rows = []
        for route, slug, label in TABS:
            ctx = await browser.new_context(viewport={"width": W, "height": H})
            await ctx.add_init_script(f"try{{localStorage.setItem('bd-theme','{THEME}')}}catch(e){{}}")
            page = await ctx.new_page()
            errs = []
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errs.append(f"PAGEERR {e}"))
            try:
                await page.goto(BASE + route, wait_until="domcontentloaded", timeout=15000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=6000)
                except Exception:
                    pass
                await page.wait_for_timeout(900)
                await page.screenshot(path=f"{OUT}/{slug}.png")  # viewport clip
                m = await page.evaluate("""() => {
                    const b=document.body;
                    const h=[...document.querySelectorAll('h1,h2')].map(e=>e.textContent.trim()).filter(Boolean)[0]||'';
                    return {ofx: b.scrollWidth>window.innerWidth+2, bw:b.scrollWidth, sh:b.scrollHeight, h:h.slice(0,40)};
                }""")
                # filter out the known network-off external 403/font noise
                real = [e for e in errs if 'status of 403' not in e and 'fonts.' not in e and 'ERR_' not in e]
                rows.append((label, route, m['ofx'], m['bw'], m['sh'], m['h'], len(real), real[:2]))
            except Exception as e:
                rows.append((label, route, None, None, None, "LOAD FAIL", 1, [str(e)[:80]]))
            await ctx.close()
        await browser.close()

        print(f"{'TAB':16} {'ROUTE':16} {'OVFLOW':6} {'bodyW':6} {'pageH':6} {'ERR':3} heading / notes")
        print("-"*100)
        for label, route, ofx, bw, sh, h, ne, ex in rows:
            flag = "OVER!" if ofx else ("?" if ofx is None else "ok")
            note = h + ((" | ERR: "+ " ; ".join(ex)) if ne else "")
            print(f"{label:16} {route:16} {flag:6} {str(bw):6} {str(sh):6} {ne:<3} {note}")

asyncio.run(main())
