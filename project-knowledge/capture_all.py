#!/usr/bin/env python3
"""Comprehensive SPA capture for the functional navigator.

Captures the independent 52-view contract in BOTH themes into BD_CAPTURE_DIR
(or reports/capture).  Every planned view emits exactly one manifest row with
an explicit ``ok`` or ``error`` status; the manifest is diagnostic evidence,
not its own completeness denominator.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from playwright.async_api import async_playwright

import capture_manifest_contract
from capture_manifest_contract import (
    DRILL,
    EXPECTED_VIEWS,
    NAV,
    SUBTABS,
    THEMES,
    ManifestContractError,
    expected_manifest_keys,
    expected_view,
    slug,
    validate_manifest,
)


BASE = "http://127.0.0.1:5599"
ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
OUT = os.environ.get("BD_CAPTURE_DIR", os.path.join(ROOT, "reports", "capture"))
VP = {"width": 1440, "height": 900}

manifest = []


async def meta(page):
    return await page.evaluate("""() => {
        const h = document.querySelector('h1,h2');
        return {head: h ? h.textContent.trim().slice(0,60) : '(none)',
                H: document.body.scrollHeight,
                ox: document.body.scrollWidth > window.innerWidth + 2};
    }""")


def _failure_row(view, theme, exc):
    row = {
        "cat": view.cat,
        "route": view.route,
        "label": view.label,
        "theme": theme,
        "status": "error",
        "file": None,
        "head": f"ERROR {exc}"[:80],
        "h": 0,
        "err": 1,
        "ox": False,
        "error": str(exc)[:240],
    }
    manifest.append(row)
    return row


def _success_row(view, theme, *, head, height, overflow, errors=(), **extra):
    errors = tuple(errors)
    row = {
        "cat": view.cat,
        "route": view.route,
        "label": view.label,
        "theme": theme,
        "status": "ok" if not errors else "error",
        "file": f"{theme}/{view.filename}",
        "head": head,
        "h": height,
        "err": len(errors),
        "ox": bool(overflow),
        **extra,
    }
    if errors:
        row["error"] = "; ".join(str(error) for error in errors)[:240]
    manifest.append(row)
    return row


async def _close_page(page):
    if page is not None:
        try:
            await page.close()
        except Exception as exc:
            print(f"  page close ERROR {str(exc)[:40]}")


async def _close_context(ctx):
    if ctx is not None:
        try:
            await ctx.close()
        except Exception as exc:
            print(f"  context close ERROR {str(exc)[:40]}")


async def shot(ctx, route, label, cat, theme, fname):
    view = expected_view(cat, route)
    if (label, fname) != (view.label, view.filename):
        raise ManifestContractError(f"capture plan drifted for {cat}:{route}")
    page = None
    errs = []
    try:
        page = await ctx.new_page()
        page.on("console", lambda msg: errs.append(msg.text)
                if msg.type == "error" else None)
        page.on("pageerror", lambda error: errs.append(str(error)))
        await page.goto(BASE + route, wait_until="domcontentloaded", timeout=20000)
        try:
            await page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:
            pass
        await page.wait_for_timeout(800)
        await page.screenshot(path=f"{OUT}/{theme}/{view.filename}", full_page=True)
        measured = await meta(page)
        real = [
            error for error in errs
            if "net::ERR" not in error and "403" not in error
            and "Failed to load resource" not in error
        ]
        _success_row(
            view, theme, head=measured["head"], height=measured["H"],
            overflow=measured["ox"], errors=real,
        )
        state = ("ok" if not measured["ox"] and not real else
                 "OVERFLOW" if measured["ox"] else f"{len(real)}err")
        print(f"  [{theme}] {route:<34} {state}")
    except Exception as exc:
        _failure_row(view, theme, exc)
        print(f"  [{theme}] {route:<34} ERROR {str(exc)[:40]}")
    finally:
        await _close_page(page)


async def shot_subtabs(ctx, route, tabs, theme):
    for index, tab in enumerate(tabs, 1):
        view = expected_view("subtab", f"{route} › {tab}")
        page = None
        try:
            page = await ctx.new_page()
            await page.goto(BASE + route, wait_until="domcontentloaded", timeout=20000)
            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            await page.wait_for_timeout(600)
            clicked = False
            selectors = (
                f'button:has-text("{tab}")',
                f'[role=tab]:has-text("{tab}")',
                f'a:has-text("{tab}")',
            )
            for selector in selectors:
                loc = page.locator(selector).first
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=1500)
                    clicked = True
                    break
            if not clicked:
                raise RuntimeError(f"subtab trigger unavailable: {tab}")
            await page.wait_for_timeout(450)
            await page.screenshot(
                path=f"{OUT}/{theme}/{view.filename}", full_page=True)
            measured = await meta(page)
            _success_row(
                view, theme, head=measured["head"], height=measured["H"],
                overflow=measured["ox"], clicked=True,
            )
            print(f"  [{theme}] {route} › {tab:<10} clicked")
        except Exception as exc:
            _failure_row(view, theme, exc)
            print(f"  [{theme}] {route} › {tab} ERROR {str(exc)[:30]}")
        finally:
            await _close_page(page)


async def popup_addsite(ctx, theme):
    view = expected_view("popup", "Add-site wizard")
    page = None
    try:
        page = await ctx.new_page()
        await page.goto(BASE + "/sites", wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(800)
        clicked = False
        for text in ("Add site", "Add your first site", "Add Site", "New site", "+ Add"):
            loc = page.locator(
                f'button:has-text("{text}"), a:has-text("{text}")').first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=2000)
                clicked = True
                break
        await page.wait_for_timeout(700)
        present = await page.locator(
            '[role=dialog], .modal, [aria-modal=true]').count()
        if not clicked or not present:
            raise RuntimeError("add-site dialog unavailable after trigger")
        await page.screenshot(
            path=f"{OUT}/{theme}/{view.filename}", full_page=True)
        _success_row(
            view, theme, head="Add site", height=0, overflow=False,
            present=True,
        )
        print(f"  [{theme}] popup add-site wizard  dialog=True")
    except Exception as exc:
        _failure_row(view, theme, exc)
        print(f"  [{theme}] popup add-site ERROR {str(exc)[:30]}")
    finally:
        await _close_page(page)


async def popup_cmdk(ctx, theme):
    view = expected_view("popup", "Command palette")
    page = None
    try:
        page = await ctx.new_page()
        await page.goto(BASE + "/", wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(700)
        await page.keyboard.press("Meta+KeyK")
        await page.wait_for_timeout(250)
        await page.keyboard.press("Control+KeyK")
        await page.wait_for_timeout(450)
        present = await page.locator(
            '[role=dialog], [cmdk-root], input[placeholder*="ommand" i], '
            'input[placeholder*="earch" i]').count()
        if not present:
            raise RuntimeError("command palette unavailable after shortcut")
        await page.screenshot(path=f"{OUT}/{theme}/{view.filename}")
        _success_row(
            view, theme, head="⌘K", height=0, overflow=False, present=True)
        print(f"  [{theme}] popup command palette  present=True")
    except Exception as exc:
        _failure_row(view, theme, exc)
        print(f"  [{theme}] popup cmdk ERROR {str(exc)[:30]}")
    finally:
        await _close_page(page)


async def popup_mobile_drawer(browser, theme):
    view = expected_view("popup", "Mobile nav drawer")
    ctx = None
    page = None
    try:
        ctx = await browser.new_context(viewport={"width": 414, "height": 896})
        await ctx.add_init_script(
            "try{localStorage.setItem('bd-theme','%s')}catch(e){}" % theme)
        page = await ctx.new_page()
        await page.goto(BASE + "/", wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(800)
        opened = False
        for selector in (
            'button[aria-label*="enu" i]', 'button:has-text("More")',
            'button[aria-label*="avigation" i]', ".hamburger",
            'button:has-text("☰")',
        ):
            loc = page.locator(selector).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=1500)
                opened = True
                break
        if not opened:
            raise RuntimeError("mobile navigation trigger unavailable")
        await page.wait_for_timeout(500)
        await page.screenshot(
            path=f"{OUT}/{theme}/{view.filename}", full_page=True)
        _success_row(
            view, theme, head="Mobile nav", height=0, overflow=False,
            opened=True,
        )
        print(f"  [{theme}] popup mobile drawer  opened=True")
    except Exception as exc:
        _failure_row(view, theme, exc)
        print(f"  [{theme}] popup mobile ERROR {str(exc)[:30]}")
    finally:
        await _close_page(page)
        await _close_context(ctx)


async def _cockpit_popup(
        ctx, theme, route, selectors, *, layout=None, visible_selector=None):
    view = expected_view("popup", route)
    page = None
    try:
        page = await ctx.new_page()
        await page.goto(BASE + "/cockpit", wait_until="domcontentloaded", timeout=20000)
        if layout is not None:
            await page.evaluate(
                "layout => localStorage.setItem('bd_cockpit_layout', layout)",
                layout,
            )
            await page.reload(wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(900)
        opened = False
        for selector in selectors:
            loc = page.locator(selector).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=1500)
                opened = True
                break
        if not opened:
            raise RuntimeError(f"{route} trigger unavailable")
        await page.wait_for_timeout(500)
        if visible_selector is not None:
            visible = page.locator(visible_selector).first
            if not await visible.count() or not await visible.is_visible():
                raise RuntimeError(f"{route} stayed closed after trigger")
        await page.screenshot(path=f"{OUT}/{theme}/{view.filename}")
        _success_row(
            view, theme, head=route, height=0, overflow=False, opened=True)
        print(f"  [{theme}] {route.lower()}  opened=True")
    except Exception as exc:
        _failure_row(view, theme, exc)
        print(f"  [{theme}] {route.lower()} ERROR {str(exc)[:40]}")
    finally:
        await _close_page(page)


async def cockpit_shots(browser, theme):
    views = (
        expected_view("cockpit", "/cockpit"),
        expected_view("popup", "Cockpit gear popover"),
        expected_view("popup", "Cockpit nav dropdown"),
    )
    ctx = None
    try:
        ctx = await browser.new_context(viewport=VP)
        await ctx.add_init_script(
            "try{localStorage.setItem('bd-theme','%s')}catch(e){}" % theme)
    except Exception as exc:
        for view in views:
            _failure_row(view, theme, exc)
        await _close_context(ctx)
        return

    try:
        await shot(
            ctx, "/cockpit", "Cockpit / Review Center", "cockpit", theme,
            "cockpit_home.png",
        )
        await _cockpit_popup(
            ctx, theme, "Cockpit gear popover",
            ("#gearbtn", 'button:has-text("⚙")',
             '[aria-label*="ettings" i]', ".gear"),
            visible_selector="#appmenu.open",
        )
        await _cockpit_popup(
            ctx, theme, "Cockpit nav dropdown",
            ('.nav .navsec .navhead:has-text("Captures")',), layout="top",
            visible_selector=".nav .navsec.baropen .navitems",
        )
    finally:
        await _close_context(ctx)


def _desktop_views():
    keys = [*(('nav', route) for route, _label in NAV),
            *(('drillin', route) for route, _label in DRILL)]
    keys.extend(("subtab", f"{route} › {tab}")
                for route, tabs in SUBTABS for tab in tabs)
    keys.extend((("popup", "Add-site wizard"),
                 ("popup", "Command palette")))
    return tuple(expected_view(cat, route) for cat, route in keys)


async def capture_theme(browser, theme):
    if theme not in THEMES:
        raise ManifestContractError(f"unexpected capture theme: {theme}")
    ctx = None
    try:
        ctx = await browser.new_context(viewport=VP)
        await ctx.add_init_script(
            "try{localStorage.setItem('bd-theme','%s')}catch(e){}" % theme)
    except Exception as exc:
        for view in _desktop_views():
            _failure_row(view, theme, exc)
        await _close_context(ctx)
    else:
        try:
            print("-- nav tabs --")
            for route, label in NAV:
                await shot(
                    ctx, route, label, "nav", theme,
                    expected_view("nav", route).filename,
                )
            print("-- site drill-ins --")
            for route, label in DRILL:
                await shot(
                    ctx, route, label, "drillin", theme,
                    expected_view("drillin", route).filename,
                )
            print("-- in-page subtabs --")
            for route, tabs in SUBTABS:
                await shot_subtabs(ctx, route, tabs, theme)
            print("-- popups --")
            await popup_addsite(ctx, theme)
            await popup_cmdk(ctx, theme)
        finally:
            await _close_context(ctx)

    await popup_mobile_drawer(browser, theme)
    await cockpit_shots(browser, theme)


async def main():
    manifest.clear()
    for theme in THEMES:
        os.makedirs(f"{OUT}/{theme}", exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        for theme in THEMES:
            print(f"\n######## CAPTURE — {theme.upper()} ########")
            await capture_theme(browser, theme)
        await browser.close()

    with open(f"{OUT}/manifest.json", "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=1)

    cats = {}
    for row in manifest:
        cats[row["cat"]] = cats.get(row["cat"], 0) + 1
    errors = [row for row in manifest if row.get("status") != "ok"]
    overflow = [row for row in manifest if row.get("ox")]
    print(f"\n==== MANIFEST: {len(manifest)} rows ====")
    print("  by category:", cats)
    print(f"  failed: {len(errors)}   overflowX: {len(overflow)}")
    if errors:
        print("  ERROR views:",
              [(row["route"], row["theme"]) for row in errors][:20])
    if overflow:
        print("  OFLOW views:",
              [(row["route"], row["theme"]) for row in overflow][:20])
    try:
        validate_manifest(manifest)
    except ManifestContractError as exc:
        print(f"CAPTURE MANIFEST UNKNOWN: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
