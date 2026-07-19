#!/usr/bin/env python3
"""Comprehensive SPA capture for the functional navigator.

Captures, in BOTH themes, into /home/claude/capture/:
  - every nav route (29) + every site drill-in (5)
  - in-page filter subtabs (Sites/Activity/History)
  - the cockpit console (/cockpit)
  - popups: Add-Site wizard, command palette, mobile "More" drawer,
            cockpit gear popover, cockpit nav dropdown
Emits manifest.json describing every shot (category/route/label/theme/file/
heading/height/errcount). Backend must serve the built dist on :5599.
"""
import os, json, asyncio
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:5599"
OUT  = "/home/claude/capture"
VP   = {"width": 1440, "height": 900}

# (route, label) — nav tabs
NAV = [
    ("/", "Home"), ("/queue", "Queue"), ("/history", "History"),
    ("/activity", "Activity"), ("/needs-review", "Needs review"),
    ("/library", "Library"), ("/sites", "Sites"), ("/capture", "Capture"),
    ("/templates", "Template Manager"), ("/dom-analyzer", "DOM analyzer"),
    ("/ai-teach", "AI selector repair"), ("/pools-macros", "Pools & macros"),
    ("/batch-ops", "Batch ops"), ("/imports", "Imports"),
    ("/import-views", "Import & saved views"), ("/dedup", "Dedup"),
    ("/rebalance", "Storage Rebalance"), ("/maintenance", "Maintenance"),
    ("/backup", "Backup"), ("/integrations", "Integrations"),
    ("/notifications", "Notifications"), ("/vpn", "VPN"),
    ("/cluster", "Cluster"), ("/secrets", "Secrets"),
    ("/settings", "Settings"), ("/settings/advanced", "Settings · Advanced"),
    ("/dashboard", "System Overview"), ("/more-actions", "More actions"),
    ("/logs/diff", "Logs diff"),
]
# (route, label) — site drill-ins (probe site)
DRILL = [
    ("/sites/__probe__", "Site detail"),
    ("/sites/__probe__/actions", "Site · Actions"),
    ("/sites/__probe__/inspect", "Site · Inspect (dry-run)"),
    ("/sites/__probe__/payload-actions", "Site · Payload actions"),
    ("/sites/__probe__/settings", "Site · Settings"),
]
# in-page filter subtabs
SUBTABS = [
    ("/sites",    ["All", "Active", "Paused", "Issues"]),
    ("/activity", ["24h", "7d", "30d", "All"]),
    ("/history",  ["History", "Events", "Logs", "Saved"]),
]

manifest = []

def slug(s):
    return s.strip("/").replace("/", "_").replace(" ", "-").replace("·", "").replace("(", "").replace(")", "").lower() or "home"

async def meta(page):
    return await page.evaluate("""() => {
        const h = document.querySelector('h1,h2');
        return {head: h ? h.textContent.trim().slice(0,60) : '(none)',
                H: document.body.scrollHeight,
                ox: document.body.scrollWidth > window.innerWidth + 2};
    }""")

async def shot(ctx, route, label, cat, theme, fname):
    page = await ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    try:
        await page.goto(BASE + route, wait_until="domcontentloaded", timeout=20000)
        try: await page.wait_for_load_state("networkidle", timeout=6000)
        except Exception: pass
        await page.wait_for_timeout(800)
        path = f"{OUT}/{theme}/{fname}"
        await page.screenshot(path=path, full_page=True)
        m = await meta(page)
        real = [e for e in errs if 'net::ERR' not in e and '403' not in e and 'Failed to load resource' not in e]
        manifest.append({"cat": cat, "route": route, "label": label, "theme": theme,
                         "file": f"{theme}/{fname}", "head": m["head"], "h": m["H"],
                         "err": len(real), "ox": m["ox"]})
        st = "ok" if not m["ox"] and not real else ("OVERFLOW" if m["ox"] else f"{len(real)}err")
        print(f"  [{theme}] {route:<34} {st}")
    except Exception as e:
        manifest.append({"cat": cat, "route": route, "label": label, "theme": theme,
                         "file": None, "head": f"ERROR {e}"[:50], "h": 0, "err": 1, "ox": False})
        print(f"  [{theme}] {route:<34} ERROR {str(e)[:40]}")
    await page.close()

async def shot_subtabs(ctx, route, tabs, theme):
    for i, t in enumerate(tabs, 1):
        page = await ctx.new_page()
        try:
            await page.goto(BASE + route, wait_until="domcontentloaded", timeout=20000)
            try: await page.wait_for_load_state("networkidle", timeout=6000)
            except Exception: pass
            await page.wait_for_timeout(600)
            clicked = False
            for sel in [f'button:has-text("{t}")', f'[role=tab]:has-text("{t}")', f'a:has-text("{t}")']:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=1500); clicked = True; break
            await page.wait_for_timeout(450)
            fname = f"subtab_{slug(route)}_{i}_{t.lower().replace(' ','')}.png"
            await page.screenshot(path=f"{OUT}/{theme}/{fname}", full_page=True)
            m = await meta(page)
            manifest.append({"cat": "subtab", "route": f"{route} › {t}", "label": f"{route} › {t}",
                             "theme": theme, "file": f"{theme}/{fname}", "head": m["head"],
                             "h": m["H"], "err": 0, "ox": m["ox"], "clicked": clicked})
            print(f"  [{theme}] {route} › {t:<10} {'clicked' if clicked else 'no-click'}")
        except Exception as e:
            print(f"  [{theme}] {route} › {t} ERROR {str(e)[:30]}")
        await page.close()

async def popup_addsite(ctx, theme):
    page = await ctx.new_page()
    try:
        await page.goto(BASE + "/sites", wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(800)
        for txt in ["Add site", "Add your first site", "Add Site", "New site", "+ Add"]:
            loc = page.locator(f'button:has-text("{txt}"), a:has-text("{txt}")').first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=2000); break
        await page.wait_for_timeout(700)
        has = await page.locator('[role=dialog], .modal, [aria-modal=true]').count()
        fname = "popup_addsite_wizard.png"
        await page.screenshot(path=f"{OUT}/{theme}/{fname}", full_page=True)
        manifest.append({"cat": "popup", "route": "Add-site wizard", "label": "Add-site wizard (modal)",
                         "theme": theme, "file": f"{theme}/{fname}", "head": "Add site",
                         "h": 0, "err": 0, "ox": False, "present": bool(has)})
        print(f"  [{theme}] popup add-site wizard  dialog={bool(has)}")
    except Exception as e:
        print(f"  [{theme}] popup add-site ERROR {str(e)[:30]}")
    await page.close()

async def popup_cmdk(ctx, theme):
    page = await ctx.new_page()
    try:
        await page.goto(BASE + "/", wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(700)
        await page.keyboard.press("Meta+KeyK")
        await page.wait_for_timeout(250)
        await page.keyboard.press("Control+KeyK")
        await page.wait_for_timeout(450)
        present = await page.locator('[role=dialog], [cmdk-root], input[placeholder*="ommand" i], input[placeholder*="earch" i]').count()
        fname = "popup_command_palette.png"
        await page.screenshot(path=f"{OUT}/{theme}/{fname}")
        manifest.append({"cat": "popup", "route": "Command palette", "label": "Command palette (⌘K)",
                         "theme": theme, "file": f"{theme}/{fname}", "head": "⌘K",
                         "h": 0, "err": 0, "ox": False, "present": bool(present)})
        print(f"  [{theme}] popup command palette  present={bool(present)}")
    except Exception as e:
        print(f"  [{theme}] popup cmdk ERROR {str(e)[:30]}")
    await page.close()

async def popup_mobile(ctx_factory, theme):
    # mobile viewport, open the More/hamburger drawer
    page = await ctx_factory(VP)  # placeholder, replaced below
    return

async def popup_mobile_drawer(browser, theme):
    ctx = await browser.new_context(viewport={"width": 414, "height": 896})
    await ctx.add_init_script("try{localStorage.setItem('bd-theme','%s')}catch(e){}" % theme)
    page = await ctx.new_page()
    try:
        await page.goto(BASE + "/", wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(800)
        opened = False
        for sel in ['button[aria-label*="enu" i]', 'button:has-text("More")',
                    'button[aria-label*="avigation" i]', '.hamburger', 'button:has-text("☰")']:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=1500); opened = True; break
        await page.wait_for_timeout(500)
        fname = "popup_mobile_drawer.png"
        await page.screenshot(path=f"{OUT}/{theme}/{fname}", full_page=True)
        manifest.append({"cat": "popup", "route": "Mobile nav drawer", "label": "Mobile nav / More drawer (414px)",
                         "theme": theme, "file": f"{theme}/{fname}", "head": "Mobile nav",
                         "h": 0, "err": 0, "ox": False, "opened": opened})
        print(f"  [{theme}] popup mobile drawer  opened={opened}")
    except Exception as e:
        print(f"  [{theme}] popup mobile ERROR {str(e)[:30]}")
    await page.close(); await ctx.close()

async def cockpit_shots(browser, theme):
    ctx = await browser.new_context(viewport=VP)
    await ctx.add_init_script("try{localStorage.setItem('bd-theme','%s')}catch(e){}" % theme)
    # base cockpit
    page = await ctx.new_page()
    try:
        await page.goto(BASE + "/cockpit", wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(900)
        await page.screenshot(path=f"{OUT}/{theme}/cockpit_home.png", full_page=True)
        m = await meta(page)
        manifest.append({"cat": "cockpit", "route": "/cockpit", "label": "Cockpit / Review Center",
                         "theme": theme, "file": f"{theme}/cockpit_home.png", "head": m["head"],
                         "h": m["H"], "err": 0, "ox": m["ox"]})
        print(f"  [{theme}] /cockpit  ok")
        # gear popover
        for sel in ['button:has-text("⚙")', '[aria-label*="ettings" i]', '.gear', 'button[title*="heme" i]']:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=1500); break
        await page.wait_for_timeout(500)
        await page.screenshot(path=f"{OUT}/{theme}/cockpit_gear_popover.png")
        manifest.append({"cat": "popup", "route": "Cockpit gear popover", "label": "Cockpit gear / theme popover",
                         "theme": theme, "file": f"{theme}/cockpit_gear_popover.png", "head": "Cockpit settings",
                         "h": 0, "err": 0, "ox": False})
        print(f"  [{theme}] cockpit gear popover  ok")
    except Exception as e:
        print(f"  [{theme}] cockpit ERROR {str(e)[:40]}")
    await page.close(); await ctx.close()

async def main():
    for t in ("light", "dark"):
        os.makedirs(f"{OUT}/{t}", exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for theme in ("light", "dark"):
            print(f"\n######## CAPTURE — {theme.upper()} ########")
            ctx = await browser.new_context(viewport=VP)
            await ctx.add_init_script("try{localStorage.setItem('bd-theme','%s')}catch(e){}" % theme)
            print("-- nav tabs --")
            for route, label in NAV:
                await shot(ctx, route, label, "nav", theme, f"nav_{slug(route)}.png")
            print("-- site drill-ins --")
            for route, label in DRILL:
                await shot(ctx, route, label, "drillin", theme, f"drill_{slug(route)}.png")
            print("-- in-page subtabs --")
            for route, tabs in SUBTABS:
                await shot_subtabs(ctx, route, tabs, theme)
            print("-- popups --")
            await popup_addsite(ctx, theme)
            await popup_cmdk(ctx, theme)
            await ctx.close()
            await popup_mobile_drawer(browser, theme)
            await cockpit_shots(browser, theme)
        await browser.close()
    with open(f"{OUT}/manifest.json", "w") as f:
        json.dump(manifest, f, indent=1)
    # summary
    cats = {}
    for m in manifest:
        cats[m["cat"]] = cats.get(m["cat"], 0) + 1
    errs = [m for m in manifest if m.get("err")]
    oflo = [m for m in manifest if m.get("ox")]
    print(f"\n==== MANIFEST: {len(manifest)} shots ====")
    print("  by category:", cats)
    print(f"  console-errors: {len(errs)}   overflowX: {len(oflo)}")
    if errs: print("  ERR routes:", [(m['route'], m['theme']) for m in errs][:20])
    if oflo: print("  OFLOW routes:", [(m['route'], m['theme']) for m in oflo][:20])

if __name__ == "__main__":
    asyncio.run(main())
