#!/usr/bin/env python3
"""
render_check.py -- cockpit shell layout render-validation harness.

WHY THIS EXISTS (durable lesson, learned @347/348):
  Structural / marker tests pass while the *rendered* cockpit layout is broken.
  v3.66.346/347 shipped a collapsed-sidebar that squished the main panel to a
  ~50px sliver with a mid-screen scrollbar -- and all 22 structural tests were
  green. The only thing that catches this class of bug is rendering the shell in
  a real headless browser and measuring the COMPUTED layout -- specifically the
  CONTENT width (the h1 / paragraph getBoundingClientRect), NOT just the #main
  box. A wide box can hold squished content; an early version of this harness
  false-passed on exactly that.

WHAT IT DOES:
  1. Imports the cockpit blueprint from BD_RENDER_ROOT (default: /home/claude/work,
     set BD_RENDER_ROOT=<extracted-zip-dir> to validate a built zip instead of the
     work tree -- this is the pre-cut gate for cockpit work).
  2. Serves it from a background Flask thread on a free localhost port.
  3. Drives headless Chromium (sandbox ms-playwright) and measures computed layout
     across: default(sidebar) -> collapsed(f) -> re-expand(f).
  4. Cycles all 6 selectable layouts and asserts none squish the main content.
  5. Forces a persisted layout=focus, reloads, and asserts the device AUTO-RECOVERS
     to Sidebar (the f5.x focus-layout-trap regression: switcher reachable, content
     not squished, NO 'Focus' option in the dropdown).

EXIT: 0 = all PASS, 1 = any FAIL (gate a cut on $?).
"""
from __future__ import annotations
import os
import sys
import json
import socket
import threading
import time
import urllib.request

# ----------------------------------------------------------------------------- config
ROOT = os.environ.get("BD_RENDER_ROOT", "/home/claude/work")
ROOT = os.path.abspath(ROOT)
VIEWPORT = (int(os.environ.get("BD_RENDER_W", "1280")),
            int(os.environ.get("BD_RENDER_H", "800")))
# A non-squished main content area must be at least this wide (px). The bug
# squished content to ~50px; healthy content at 1280 viewport is ~900-1150px.
MIN_CONTENT = int(os.environ.get("BD_RENDER_MIN_CONTENT", "500"))
# Horizontal overflow tolerance (scrollbar gutter etc.)
OVERFLOW_TOL = 4

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    _results.append((ok, label))
    tag = PASS if ok else FAIL
    line = f"  {tag}  {label}"
    if detail:
        line += f"   \033[2m{detail}\033[0m"
    print(line)
    return ok


# ----------------------------------------------------------------------------- flask app
def build_app():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    from flask import Flask
    from tools import cockpit_console as cock  # noqa: E402
    app = Flask(__name__)
    app.register_blueprint(cock.bp)
    return app


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def serve(app, port: int):
    app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False, debug=False)


def wait_up(port: int, path: str = "/cockpit/", timeout: float = 20.0):
    url = f"http://127.0.0.1:{port}{path}"
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return url
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.25)
    raise RuntimeError(f"server never came up at {url}: {last}")


# ----------------------------------------------------------------------------- measurement (runs in-page)
MEASURE_JS = r"""
() => {
  const vw = window.innerWidth;
  const app = document.querySelector('.app');
  const side = document.querySelector('.side');
  const main = document.querySelector('.main');
  const reexpand = document.querySelector('#reexpand');
  const sel = document.querySelector('#layout_sel') || document.querySelector('#s_layout_sel');
  const brand = document.querySelector('.brand');

  const rect = el => { if(!el) return null; const r = el.getBoundingClientRect();
                       return {w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.x)}; };
  const cs = el => el ? getComputedStyle(el) : null;

  // CONTENT width = the widest of the main panel's own h1 + first paragraph.
  // This is the load-bearing measure: a wide #main box can still hold squished
  // content, which is exactly the bug we are guarding against.
  let contentW = 0;
  if (main) {
    const h1 = main.querySelector('h1');
    const p  = main.querySelector('p, .sub');
    [h1, p].forEach(e => { if(e){ const w = Math.round(e.getBoundingClientRect().width); if(w>contentW) contentW=w; } });
  }

  const doc = document.documentElement;
  const overflowX = Math.max(doc.scrollWidth, document.body ? document.body.scrollWidth : 0) - vw;

  // dropdown options (to assert 'Focus' is gone, and read the active value)
  let opts = [], selVal = null;
  if (sel) { selVal = sel.value; opts = Array.from(sel.options).map(o => o.value); }

  return {
    vw,
    appClass: app ? app.className : null,
    appCollapsed: app ? (app.dataset.collapsed || null) : null,
    gridCols: app ? cs(app).gridTemplateColumns : null,
    sideDisplay: side ? cs(side).display : 'absent',
    side: rect(side),
    main: main ? {...rect(main), scrollW: Math.round(main.scrollWidth), clientW: Math.round(main.clientWidth)} : null,
    contentW,
    brandH: brand ? Math.round(brand.getBoundingClientRect().height) : null,
    reexpandDisplay: reexpand ? cs(reexpand).display : 'absent',
    overflowX,
    selVal,
    selOptions: opts,
  };
}
"""


def measure(page):
    return page.evaluate(MEASURE_JS)


def fmt(m: dict) -> str:
    return (f"vw={m['vw']} content={m['contentW']} main={m['main']['w'] if m['main'] else '-'} "
            f"side={m['sideDisplay']}/{m['side']['w'] if m['side'] else '-'} "
            f"ovfX={m['overflowX']} cls='{m['appClass']}' collapsed={m['appCollapsed']}")


# ----------------------------------------------------------------------------- the run
def run():
    print("=" * 70)
    print(f"  render_check -- root={ROOT}  viewport={VIEWPORT[0]}x{VIEWPORT[1]}  min_content={MIN_CONTENT}")
    print("=" * 70)

    app = build_app()
    port = free_port()
    t = threading.Thread(target=serve, args=(app, port), daemon=True)
    t.start()
    url = wait_up(port)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]})
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_selector(".app", timeout=8000)
        page.wait_for_timeout(450)  # let boot/applyLayout + initial fetches settle

        # ---- STATE 1: default (sidebar) -----------------------------------------
        print("\n[1] default (Sidebar)")
        m = measure(page)
        print("    " + fmt(m))
        check(m["sideDisplay"] not in ("none", "absent"), "sidebar is visible")
        check(m["contentW"] >= MIN_CONTENT, "main content not squished",
              f"contentW={m['contentW']} >= {MIN_CONTENT}")
        check(m["overflowX"] <= OVERFLOW_TOL, "no horizontal overflow (no mid-screen scrollbar)",
              f"overflowX={m['overflowX']}")
        # the false-pass trap: wide box but squished content
        if m["main"]:
            wide_box_squished = m["main"]["w"] > 800 and m["contentW"] < 400
            check(not wide_box_squished, "main box width tracks content width",
                  f"box={m['main']['w']} content={m['contentW']}")

        # ---- STATE 2: collapsed (press f) ---------------------------------------
        print("\n[2] collapsed (f-key hides sidebar)")
        page.keyboard.press("f")
        page.wait_for_timeout(250)
        m = measure(page)
        print("    " + fmt(m))
        check(m["appCollapsed"] == "1", "data-collapsed flips to 1")
        check(m["sideDisplay"] == "none", "sidebar hidden")
        check(m["reexpandDisplay"] not in ("none", "absent"), "re-expand affordance is shown (exitable)",
              f"#reexpand display={m['reexpandDisplay']}")
        check(m["contentW"] >= MIN_CONTENT, "main content NOT squished when collapsed",
              f"contentW={m['contentW']} >= {MIN_CONTENT}  (this is the 346/347 bug)")
        check(m["overflowX"] <= OVERFLOW_TOL, "no mid-screen scrollbar when collapsed",
              f"overflowX={m['overflowX']}")

        # ---- STATE 3: re-expand (press f again) ---------------------------------
        print("\n[3] re-expand (f-key restores sidebar)")
        page.keyboard.press("f")
        page.wait_for_timeout(250)
        m = measure(page)
        print("    " + fmt(m))
        check(m["appCollapsed"] is None, "data-collapsed cleared")
        check(m["sideDisplay"] not in ("none", "absent"), "sidebar restored")
        check(m["contentW"] >= MIN_CONTENT, "content restored")

        # ---- STATE 4: cycle all 6 layouts, none squish content ------------------
        print("\n[4] cycle 6 layouts (none squish main content)")
        layouts = page.evaluate("() => (typeof LAYOUTS!=='undefined') ? LAYOUTS.map(x=>x[0]) : []")
        check(len(layouts) == 6, "LAYOUTS has exactly 6 entries", f"{layouts}")
        check("focus" not in layouts, "'focus' is NOT a selectable layout")
        for lay in layouts:
            page.evaluate("(l)=>{ if(typeof applyLayout==='function') applyLayout(l); }", lay)
            page.wait_for_timeout(180)
            m = measure(page)
            ok = m["contentW"] >= MIN_CONTENT and m["overflowX"] <= OVERFLOW_TOL
            check(ok, f"layout '{lay}' content not squished",
                  f"contentW={m['contentW']} ovfX={m['overflowX']}")
        # restore to side for the recovery test
        page.evaluate("()=>{ if(typeof applyLayout==='function') applyLayout('side'); }")

        # ---- STATE 5: focus-layout-trap recovery --------------------------------
        print("\n[5] focus-layout-trap recovery (persisted layout=focus -> Sidebar)")
        page.evaluate("()=>{ localStorage.setItem('bd_cockpit_layout','focus'); }")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".app", timeout=8000)
        page.wait_for_timeout(450)
        m = measure(page)
        print("    " + fmt(m))
        stored = page.evaluate("()=>localStorage.getItem('bd_cockpit_layout')")
        check("focus" not in (m["appClass"] or ""), "app is NOT in a focus layout after reload",
              f"appClass='{m['appClass']}'")
        check(m["selVal"] == "side" if m["selVal"] is not None else True,
              "layout switcher reads 'side'", f"selVal={m['selVal']}")
        check("focus" not in m["selOptions"], "'Focus' is absent from the dropdown",
              f"options={m['selOptions']}")
        check(stored == "side", "persisted layout re-normalized to 'side'", f"localStorage={stored}")
        check(m["sideDisplay"] not in ("none", "absent"), "switcher reachable (sidebar visible)")
        check(m["contentW"] >= MIN_CONTENT, "content NOT squished after focus recovery",
              f"contentW={m['contentW']}")

        # ---- STATE 6: bar-layout click reachability (352 regression) -------------
        # A horizontal bar (.app.topnav/.app.bottombar .side) with overflow-x:auto
        # becomes a clip box that swallows the gear popover + nav hover-dropdowns.
        # Box measurement (states 1-5) stays green while these are unclickable, so
        # this state probes the ACTUAL menu/dropdown link with elementFromPoint.
        print("\n[6] bar-layout click reachability (gear popover + nav dropdown)")
        GEAR_JS = r"""() => {
          const gb=document.querySelector('#gearbtn'); if(!gb) return {err:'no gear'};
          gb.click();
          const am=document.querySelector('#appmenu'); const r=am.getBoundingClientRect();
          const onscreen = r.y>=-1 && r.x>=-1 && r.y+r.height<=innerHeight+1 && r.x+r.width<=innerWidth+1;
          const opt=document.querySelector('#laypick .opt')||document.querySelector('#appmenu [data-l]');
          let reach=false;
          if(opt){const o=opt.getBoundingClientRect();
            const el=document.elementFromPoint(o.x+Math.min(o.width/2,80), o.y+Math.min(o.height/2,10));
            reach=!!(el&&(el===opt||opt.contains(el)||(el.closest&&el.closest('#appmenu'))));}
          am.classList.remove('open');
          return {onscreen, reach};
        }"""
        DROP_JS = r"""() => {
          const secs=[...document.querySelectorAll('.nav .navsec')];
          const i=secs.findIndex(s=>{const h=s.querySelector('.navhead');return h&&/Captures/.test(h.textContent);});
          if(i<0) return {err:'no captures sec'};
          const it=secs[i].querySelector('.navitems'); const cs=getComputedStyle(it);
          if(cs.display==='none') return {hidden:true};
          const link=it.querySelector('a[data-p]'); const lr=link.getBoundingClientRect();
          const el=document.elementFromPoint(lr.x+Math.min(lr.width/2,70), lr.y+Math.min(lr.height/2,8));
          return {reach: !!(el&&el.getAttribute&&el.getAttribute('data-p'))};
        }"""
        for lay in ("top", "bottombar"):
            page.evaluate("(l)=>localStorage.setItem('bd_cockpit_layout',l)", lay)
            page.reload(); page.wait_for_timeout(300)
            g = page.evaluate(GEAR_JS)
            check(bool(g.get("onscreen")) and bool(g.get("reach")),
                  f"'{lay}': gear popover on-screen + option clickable", f"{g}")
            try:
                cap = page.query_selector("xpath=//*[contains(@class,'navhead')][contains(.,'Captures')]")
                if cap:
                    cap.hover(); page.wait_for_timeout(180)
            except Exception:
                pass
            dd = page.evaluate(DROP_JS)
            check(bool(dd.get("reach")),
                  f"'{lay}': nav dropdown link clickable (not clipped)", f"{dd}")

        browser.close()

    # ---- summary -------------------------------------------------------------
    n_fail = sum(1 for ok, _ in _results if not ok)
    n_pass = len(_results) - n_fail
    print("\n" + "=" * 70)
    if n_fail == 0:
        print(f"  \033[32mRESULT: PASS\033[0m -- {n_pass}/{len(_results)} checks green  (root={ROOT})")
    else:
        print(f"  \033[31mRESULT: FAIL\033[0m -- {n_fail} failed, {n_pass} passed")
        for ok, lab in _results:
            if not ok:
                print(f"     - {lab}")
    print("=" * 70)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
