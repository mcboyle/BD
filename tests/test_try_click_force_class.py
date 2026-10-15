"""try-click-force-class r1 (RULING-pm-A4-A-lanes-and-tryclick-0225Z item 2; class found by S125-B in dl95-scrolller-3 r2,
NOTE-S125-B-scrolller-3-r2-try-click-force-fallback-class-0117Z.md).

login_impl/_common.py _try_click falls back to click(force=True) when the normal click is refused by Playwright's
hit-target check. force=True skips that check and clicks the control's centre: whatever stands there (a promo /
consent / upsell cover) takes the click, and _try_click still returned ok=True -- the journal then reported a click
that never reached the control (scrolller r1: `click 'Log in!' after the password field`, no credential POST).
r2 gated the two submit.py submit call sites only; every other caller (submit.py _staged_password_retry's continue
click) kept the misreport.

r1: the forced pass hit-tests the control's centre first and clicks only when the centre is the control (or a
descendant, or a <label> whose control it is). A covered control is not clicked; ok=False and the reason says
"forced click refused" and names the cover. Local headless chromium, page.set_content; no live site, no credentials.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

# #covered sits under a full-viewport cover (the scrolller promo shape); #moving never becomes "stable" for
# Playwright (an endless transform animation), so its normal click times out and only the forced pass can click it
# -- with nothing over it. Every element counts its own clicks.
PAGE_HTML = """<!doctype html><html><head><style>
@keyframes wob { from { transform: translateX(0) } to { transform: translateX(40px) } }
#moving { animation: wob 0.3s linear infinite alternate; }
</style></head><body>
<button type="button" id="covered" style="position:absolute;left:40px;top:40px;width:160px;height:40px">Log in!</button>
<button type="button" id="moving" style="position:absolute;left:40px;top:400px;width:160px;height:40px">Continue</button>
<div id="cover" class="promo overlay"
     style="position:fixed;left:0;top:0;width:100%;height:300px;z-index:100;background:rgba(0,0,0,.6)">
  <p>Get Premium 73% Off</p></div>
<script>
const bump = (k) => () => { window[k] = (window[k] || 0) + 1; };
document.getElementById('covered').addEventListener('click', bump('__covered'));
document.getElementById('moving').addEventListener('click', bump('__moving'));
document.getElementById('cover').addEventListener('click', bump('__cover'));
</script>
</body></html>"""

# Hit-test states the forced pass sees: a checkbox under its own <label> (the label's click reaches it), a button in
# a shadow root (the document hit is the host), a display:none button (no box).
STATES_HTML = """<!doctype html><html><body>
<div style="position:relative;width:200px;height:30px;margin:20px">
  <input type="checkbox" id="remember" style="position:absolute;left:0;top:0;margin:0">
  <label for="remember" id="remember-label"
         style="position:absolute;left:0;top:0;width:200px;height:30px;display:block">Remember me</label>
</div>
<div id="host"></div>
<button type="button" id="hidden" style="display:none">Hidden</button>
<script>
const root = document.getElementById('host').attachShadow({mode: 'open'});
root.innerHTML = '<button type="button" id="shadow-btn" style="width:120px;height:30px">Shadow</button>';
</script>
</body></html>"""


@contextmanager
def _page(html=PAGE_HTML):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, timeout=20000,
                                        args=["--no-sandbox", "--disable-dev-shm-usage"])
        except Exception as e:
            pytest.fail(f"chromium not launchable here -- a launch failure is a FAILURE (T5): {e}")
        try:
            page = browser.new_page(viewport={"width": 1000, "height": 760})
            page.set_content(html, wait_until="load")
            yield page
        finally:
            browser.close()


def _counts(page):
    return page.evaluate("() => ({covered: window.__covered||0, moving: window.__moving||0, cover: window.__cover||0})")


def test_premise_a_forced_click_on_the_covered_control_lands_on_the_cover():
    """Positive control for every zero below: the normal click on #covered is refused, a forced click at its
    centre reaches the cover and never the control; #moving's normal click is refused and a forced click reaches it."""
    with _page() as page:
        with pytest.raises(Exception):
            page.click("#covered", timeout=1500)
        page.click("#covered", force=True, timeout=2000)
        assert _counts(page) == {"covered": 0, "moving": 0, "cover": 1}
        with pytest.raises(Exception):
            page.click("#moving", timeout=1500)
        page.click("#moving", force=True, timeout=2000)
        assert _counts(page)["moving"] == 1


def test_the_row_a_covered_control_is_not_reported_clicked():
    """THE ROW: before r1 the forced pass clicked the cover and returned (True, "#covered (forced)")."""
    from bulk_downloader.login_impl._common import _try_click
    with _page() as page:
        ok, info = _try_click(page, ["#covered"], "submit button")
        counts = _counts(page)
    assert (ok, counts["cover"]) == (False, 0), (
        f"TRY-CLICK-FORCE-CLASS: a covered control was force-clicked and reported ok={ok} ({info}); "
        f"cover clicks={counts['cover']}, control clicks={counts['covered']}")
    assert counts["covered"] == 0
    assert "forced click refused" in info and "'#covered' is covered at its centre by div#cover.promo.overlay" in info, info


def test_an_uncovered_control_is_still_force_clicked_after_a_covered_one_is_refused():
    """The force fallback still works where it reaches the control: the walk refuses #covered and clicks #moving."""
    from bulk_downloader.login_impl._common import _try_click
    with _page() as page:
        ok, info = _try_click(page, ["#covered", "#moving"], "continue")
        counts = _counts(page)
    assert (ok, info) == (True, "#moving (forced)"), info
    assert counts == {"covered": 0, "moving": 1, "cover": 0}, counts


def test_the_staged_login_continue_caller_refuses_a_covered_continue():
    """Sibling call site (submit.py _staged_password_retry, not gated by r2): a covered continue is not clicked and
    the staged-login reason carries the refusal, instead of `clicked continue [... (forced)]`."""
    from bulk_downloader.login_impl import submit
    with _page() as page:
        ok, info = submit._staged_password_retry(page, ["#covered"], ["input[type=password]"], "unused-pw")
        counts = _counts(page)
    assert ok is False and counts == {"covered": 0, "moving": 0, "cover": 0}, (ok, info, counts)
    assert info.startswith("staged-login: no continue affordance (") and "forced click refused" in info, info


def test_forced_click_hit_test_states():
    """clear for a label-covered checkbox (the label's click reaches it) and a shadow-root button; covered for the
    promo; no-box for display:none. Only clear lets the forced click run."""
    from bulk_downloader.login_impl._common import _forced_click_hit_test
    with _page(STATES_HTML) as page:
        assert _forced_click_hit_test(page.locator("#remember"))["state"] == "clear"
        assert _forced_click_hit_test(page.locator("#shadow-btn"))["state"] == "clear"
        assert _forced_click_hit_test(page.locator("#hidden"))["state"] == "no-box"
    with _page() as page:
        probe = _forced_click_hit_test(page.locator("#covered"))
        assert probe == {"state": "covered", "hit": "div#cover.promo.overlay", "cover": "div#cover.promo.overlay"}, probe
        assert _forced_click_hit_test(page.locator("#moving"))["state"] == "clear"


def test_a_probe_that_cannot_look_refuses_the_forced_click():
    """Unknown is never permission: a hit-test error refuses the click with a distinct reason (no cover named)."""
    from bulk_downloader.login_impl._common import _forced_click_hit_test, _forced_click_refusal

    class _Gone:
        def evaluate(self, *_a, **_k):
            raise RuntimeError("Element is not attached to the DOM")

    probe = _forced_click_hit_test(_Gone())
    assert probe == {"state": "error", "error": "Element is not attached to the DOM"}
    assert _forced_click_refusal("#x", probe) == "'#x' centre not hit-testable (error: Element is not attached to the DOM)"
