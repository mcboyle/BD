"""fx-dorcelclub-frame-gate (O1567, bd1 10.0.70.51, 2026-09-29 21:36Z).

dorcelclub.com draws its 18+/cookie wall inside an ``about:blank`` iframe
that is ``position:fixed`` over the whole viewport (z-index 10000). The
wall reads "PROHIBITED TO PERSONS UNDER 18 YEARS OLD" with the controls
"ENTER and accept cookies" (a <button>), "Enter without accepting cookies",
"Set cookies" and "Exit". The download path's ``clear_gates`` only looked
in the MAIN frame, so the wall stayed up, the members "a.btn-dl" click timed
out ("visible but the click failed"), and the scene fell to needs_review
("Clicked but no download started ... DOWNLOAD THE VIDEO").

The fix: when the page itself had no gate, the consent and age tiers are
offered to a viewport-covering child frame with no origin of its own
(about:blank) or the page's own origin. A foreign-origin frame is never
clicked, whatever it shows; a control that FORBIDDEN refuses stays refused.
"""
from __future__ import annotations

import pytest

from bulk_downloader import interstitial

BD_GATE_SCOPE = "module"

SCENE = "https://www.dorcelclub.test/en/scene/849534/under-the-husbands-watch"
FOREIGN = "https://ads.example.test/wall.html"

WALL_BODY = """
<p>PROHIBITED TO PERSONS UNDER 18 YEARS OLD</p>
<button class="button agree" onclick="document.body.setAttribute('data-clicked','1');parent.document.getElementById('wall').style.display='none'">
  <span class="big">ENTER</span> <span>and accept cookies</span></button>
<p><a href="#" onclick="parent.document.getElementById('wall').style.display='none';return false">Enter without accepting cookies</a>
<a href="#" onclick="return false">Set cookies</a> <a href="#" onclick="return false">Exit</a></p>
<p>This website is intended for an adult and informed public, people over 18 years of age.</p>
"""

WALL_EXIT_ONLY = """
<p>PROHIBITED TO PERSONS UNDER 18 YEARS OLD</p>
<button onclick="document.body.setAttribute('data-clicked','1')">Exit and accept cookies</button>
"""


SCENE_BASE = """<html><body>
<h1>Under the husband's watch</h1>
<a class="btn-dl" href="#" onclick="document.title='download-opened';return false">DOWNLOAD THE VIDEO</a>
<iframe id="wall" %s style="position:fixed;top:0;left:0;width:100%%;height:100%%;border:0;z-index:10000;background:#000"></iframe>
%s
</body></html>"""

# the same wall in a small inline frame (an embed, not an overlay)
INLINE_BASE = SCENE_BASE.replace(
    "position:fixed;top:0;left:0;width:100%%;height:100%%",
    "position:static;width:300px;height:150px")

# dorcel's own shape: an iframe with no src whose document is written by script.
_WRITE_WALL = """<script>
const d = document.getElementById('wall').contentDocument;
d.open(); d.write(%r); d.close();
</script>"""


def _chromium():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright unavailable -- real-browser frame-gate probe UNKNOWN")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
    except Exception as exc:
        pw.stop()
        pytest.skip(f"chromium unavailable -- real-browser frame-gate probe UNKNOWN: {exc}")
    return pw, browser


def _serve(page, routes):
    def handler(route):
        body = routes.get(route.request.url)
        if body is None:
            route.fulfill(status=404, body="")
        else:
            route.fulfill(status=200, content_type="text/html", body=body)
    page.route("**/*", handler)


def _download_clickable(page) -> bool:
    try:
        page.locator("a.btn-dl").first.click(trial=True, timeout=1500)
        return True
    except Exception:
        return False


def _run(scene_html, extra_routes=None):
    pw, browser = _chromium()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        routes = {SCENE: scene_html}
        routes.update(extra_routes or {})
        _serve(page, routes)
        page.goto(SCENE, wait_until="load")
        page.wait_for_timeout(300)
        before = _download_clickable(page)
        actions = interstitial.clear_gates(
            page, site_gates="", url=SCENE, sleep=lambda _s: page.wait_for_timeout(200))
        after = _download_clickable(page)
        wall_visible = page.locator("#wall").is_visible()
        clicked = [f.url for f in page.frames if f is not page.main_frame
                   and f.evaluate("() => document.body && document.body.getAttribute('data-clicked')") == "1"]
        return before, actions, after, wall_visible, clicked
    finally:
        browser.close()
        pw.stop()


def test_frame_hosted_age_wall_is_cleared_and_the_download_control_is_reachable():
    before, actions, after, wall_visible, clicked = _run(
        SCENE_BASE % ("", _WRITE_WALL % WALL_BODY))
    assert before is False, "fixture: the wall must block the download control"
    assert after and not wall_visible and clicked == ["about:blank"], (
        "FX_DORCEL_FRAME_GATE_NOT_CLEARED: the about:blank viewport-covering "
        f"age wall stayed up; download clickable={after}; notes={actions}")
    assert any("cleared via" in a and "child frame" in a for a in actions), actions


def test_forbidden_control_in_a_covering_frame_is_not_clicked():
    before, actions, after, wall_visible, clicked = _run(
        SCENE_BASE % ("", _WRITE_WALL % WALL_EXIT_ONLY))
    assert before is False
    assert not clicked and wall_visible and not after, actions


def test_non_covering_inline_frame_is_not_walked():
    before, actions, after, wall_visible, clicked = _run(
        INLINE_BASE % ("", _WRITE_WALL % WALL_BODY))
    assert not clicked, f"FX_DORCEL_FRAME_GATE_INLINE_CLICKED: {actions}"
    assert wall_visible, actions


def test_foreign_origin_frame_wall_is_never_clicked():
    foreign_wall = "<html><body>%s</body></html>" % WALL_BODY
    before, actions, after, wall_visible, clicked = _run(
        SCENE_BASE % ('src="%s"' % FOREIGN, ""), {FOREIGN: foreign_wall})
    assert before is False
    assert not clicked, f"FX_DORCEL_FRAME_GATE_FOREIGN_CLICKED: {clicked} {actions}"
    assert wall_visible and not after, actions
    assert not any("cleared via" in a for a in actions), actions


def test_page_without_child_frames_is_unchanged():
    html = "<html><body><a class='btn-dl' href='#'>DOWNLOAD THE VIDEO</a></body></html>"
    pw, browser = _chromium()
    try:
        page = browser.new_page()
        _serve(page, {SCENE: html})
        page.goto(SCENE, wait_until="load")
        assert interstitial.clear_gates(
            page, site_gates="", url=SCENE, sleep=lambda _s: None) == []
    finally:
        browser.close()
        pw.stop()
