import asyncio, os
from playwright.async_api import async_playwright

THEME = os.environ.get("BD_THEME", "light")
ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

# (route, [tab button texts], slug-prefix)
GROUPS = [
    ("/sites",    ["All", "Active", "Paused", "Issues"], "sites"),
    ("/activity", ["24h", "7d", "30d", "All"],           "activity"),
    ("/history",  ["History", "Events", "Logs", "Saved"], "history"),
]

async def main():
    OUTDIR = os.environ.get("BD_OUT", os.path.join(ROOT, "reports", "subtabs"))
    os.makedirs(OUTDIR, exist_ok=True)
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        print("%-12s %-16s %-7s %s" % ("PAGE", "TAB", "clicked", "note"))
        print("-" * 70)
        for route, tabs, pref in GROUPS:
            ctx = await b.new_context(viewport={"width": 1280, "height": 900})
            await ctx.add_init_script("try{localStorage.setItem('bd-theme','%s')}catch(e){}" % THEME)
            p = await ctx.new_page()
            await p.goto("http://127.0.0.1:5599" + route, wait_until="domcontentloaded")
            try:
                await p.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            await p.wait_for_timeout(700)
            for i, t in enumerate(tabs, 1):
                clicked = False
                note = ""
                # try an exact-ish clickable with this text inside the header region
                for sel in [f"button:has-text(\"{t}\")", f"[role=tab]:has-text(\"{t}\")", f"a:has-text(\"{t}\")"]:
                    try:
                        loc = p.locator(sel).first
                        if await loc.count() and await loc.is_visible():
                            await loc.click(timeout=1500)
                            clicked = True
                            break
                    except Exception as e:
                        note = str(e)[:30]
                await p.wait_for_timeout(500)
                slug = f"{pref}_{i}_{t.lower().replace(' ', '')}"
                await p.screenshot(path=f"{OUTDIR}/tab_{slug}.png")
                print("%-12s %-16s %-7s %s" % (route, t, "yes" if clicked else "NO", note))
            await ctx.close()
        await b.close()

asyncio.run(main())
