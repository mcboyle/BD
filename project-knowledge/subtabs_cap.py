import asyncio, os
from playwright.async_api import async_playwright

THEME = os.environ.get("BD_THEME", "light")
ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
OUT = os.environ.get("BD_OUT", os.path.join(ROOT, "reports", "subtabs"))
ROUTES = [
    ("/dashboard", "s01_dashboard", "Dashboard"),
    ("/settings/advanced", "s02_advanced", "Settings - Advanced"),
    ("/more-actions", "s03_moreactions", "More actions"),
    ("/logs/diff", "s04_logdiff", "Logs - Diff"),
    ("/import-views", "s05_importviews", "Import views"),
    ("/cluster", "s06_cluster", "Cluster"),
    ("/sites/__probe__", "s07_sitedetail", "Site detail (probe)"),
]

async def main():
    os.makedirs(OUT, exist_ok=True)
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        print("%-22s %-22s %-6s %-26s %s" % ("PAGE", "ROUTE", "pageH", "final-url", "heading"))
        print("-" * 100)
        for route, slug, label in ROUTES:
            ctx = await b.new_context(viewport={"width": 1280, "height": 900})
            await ctx.add_init_script("try{localStorage.setItem('bd-theme','%s')}catch(e){}" % THEME)
            p = await ctx.new_page()
            try:
                await p.goto("http://127.0.0.1:5599" + route, wait_until="domcontentloaded", timeout=15000)
                try:
                    await p.wait_for_load_state("networkidle", timeout=6000)
                except Exception:
                    pass
                await p.wait_for_timeout(900)
                await p.screenshot(path=os.path.join(OUT, "%s.png" % slug), full_page=True)
                m = await p.evaluate("() => ({h:(([...document.querySelectorAll('h1,h2')].map(e=>e.textContent.trim()).filter(Boolean)[0])||'').slice(0,40), sh:document.body.scrollHeight, url:location.pathname})")
                print("%-22s %-22s %-6s %-26s %s" % (label, route, m["sh"], m["url"], m["h"]))
            except Exception as e:
                print("%-22s %-22s FAIL %s" % (label, route, str(e)[:50]))
            await ctx.close()
        await b.close()

asyncio.run(main())
