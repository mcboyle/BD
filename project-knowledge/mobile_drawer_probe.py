#!/usr/bin/env python3
"""Runtime probe (the non-negotiable gate): drive the cockpit at mobile width
(420px) in headless chromium and verify the off-canvas drawer affordance works
end-to-end. Structural tests cannot catch JS runtime bugs (e.g. a boot-order TDZ
that silently kills the page); this does.

Checks at 420px:
  1. #mobilebar visible, #navtoggle visible & hit-testable.
  2. Drawer (.side) starts OFF-canvas (right edge <= 1px).
  3. Tap #navtoggle -> .app.navopen, drawer slides in (x ~ 0, on-screen),
     #navscrim visible, navtoggle aria-expanded=true.
  4. Tap #navscrim -> closed (drawer off-canvas, aria-expanded=false).
  5. Re-open then tap a nav link -> drawer closes (go() path).
  6. Escape closes an open drawer.
  7. Resize to 1280 clears navopen (no stuck fixed overlay on desktop).
"""
import os, sys, time, socket, threading, urllib.request

ROOT = os.environ.get("BD_RENDER_ROOT", "/home/claude/work")


def build_app():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    from flask import Flask
    from tools import cockpit_console as cock
    app = Flask(__name__)
    app.register_blueprint(cock.bp)
    return app


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def wait_up(port, timeout=20.0):
    url = f"http://127.0.0.1:{port}/cockpit/"
    dl = time.time() + timeout; last = None
    while time.time() < dl:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return url
        except Exception as e:
            last = e; time.sleep(0.25)
    raise RuntimeError(f"server never came up: {last}")


RESULTS = []
def check(ok, label, detail=""):
    RESULTS.append(ok)
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   {detail}" if detail else ""))


def main():
    app = build_app()
    port = free_port()
    threading.Thread(target=lambda: app.run(host="127.0.0.1", port=port,
                     threaded=True, use_reloader=False, debug=False), daemon=True).start()
    url = wait_up(port)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = b.new_context(viewport={"width": 420, "height": 760})
        pg = ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded")
        pg.wait_for_timeout(400)

        def vis(sel):
            return pg.eval_on_selector(sel, "e=>{const r=e.getBoundingClientRect();const s=getComputedStyle(e);return {disp:s.display, x:Math.round(r.x), right:Math.round(r.right), w:Math.round(r.width), vis:s.visibility};}") if pg.query_selector(sel) else None

        mb = vis("#mobilebar")
        check(bool(mb) and mb["disp"] != "none", "#mobilebar visible at 420px", str(mb))
        nt = vis("#navtoggle")
        check(bool(nt) and nt["w"] > 0, "#navtoggle visible", str(nt))

        side0 = vis(".side")
        check(bool(side0) and side0["right"] <= 1, "drawer starts off-canvas", str(side0))

        # 3. open
        pg.click("#navtoggle")
        pg.wait_for_timeout(350)
        side1 = vis(".side")
        navopen1 = pg.eval_on_selector(".app", "e=>e.classList.contains('navopen')")
        scrim1 = vis("#navscrim")
        exp1 = pg.eval_on_selector("#navtoggle", "e=>e.getAttribute('aria-expanded')")
        check(navopen1 and side1["x"] >= -1 and side1["right"] > 100, "tap opens drawer (slides in)", str(side1))
        check(bool(scrim1) and scrim1["disp"] == "block", "scrim visible when open", str(scrim1))
        check(exp1 == "true", "navtoggle aria-expanded=true when open", repr(exp1))

        # 4. scrim closes
        pg.click("#navscrim", position={"x": 380, "y": 600})
        pg.wait_for_timeout(350)
        side2 = vis(".side")
        navopen2 = pg.eval_on_selector(".app", "e=>e.classList.contains('navopen')")
        exp2 = pg.eval_on_selector("#navtoggle", "e=>e.getAttribute('aria-expanded')")
        check(not navopen2 and side2["right"] <= 1, "tap scrim closes drawer", str(side2))
        check(exp2 == "false", "aria-expanded=false after close", repr(exp2))

        # 5. nav-tap closes (go path)
        pg.click("#navtoggle"); pg.wait_for_timeout(300)
        # click a real nav link inside the drawer
        link = pg.query_selector(".side #nav a")
        if link:
            link.click(); pg.wait_for_timeout(400)
            navopen3 = pg.eval_on_selector(".app", "e=>e.classList.contains('navopen')")
            check(not navopen3, "nav-tap closes drawer (go path)")
        else:
            check(False, "could not find a nav link in the drawer")

        # 6. Escape closes
        pg.click("#navtoggle"); pg.wait_for_timeout(300)
        pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
        navopen4 = pg.eval_on_selector(".app", "e=>e.classList.contains('navopen')")
        check(not navopen4, "Escape closes drawer")

        # 7. resize to desktop clears navopen
        pg.click("#navtoggle"); pg.wait_for_timeout(250)
        pg.set_viewport_size({"width": 1280, "height": 800}); pg.wait_for_timeout(350)
        navopen5 = pg.eval_on_selector(".app", "e=>e.classList.contains('navopen')")
        mb_desktop = vis("#mobilebar")
        check(not navopen5, "resize to 1280 clears navopen")
        check(mb_desktop["disp"] == "none", "#mobilebar hidden at desktop width", str(mb_desktop))

        b.close()

    n = len(RESULTS); ok = sum(RESULTS)
    print("=" * 60)
    print(f"  RESULT: {'PASS' if ok==n else 'FAIL'} -- {ok}/{n} checks green  (root={ROOT})")
    print("=" * 60)
    sys.exit(0 if ok == n else 1)


if __name__ == "__main__":
    main()
