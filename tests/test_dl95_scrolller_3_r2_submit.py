"""dl95-scrolller-3 r2 (scrolller, test2 .95 build 1e334559, 2026-10-14T23:16Z, tester S127-B LIVE-FAIL with the r1 fix
present; evidence landing/dl95/tester-S127-B/scrolller/). The r1 path ran:

  login submit: click submit selector -> skip (could not click submit button; tried 59 selectors)
  login submit: password submit -> click 'Log in!' after the password field; waiting...
  login submit: password submit sent no credential POST

and the pre-submit screenshot shows a "Get Premium 73% Off" promo modal (an X close control top-right, one
"Claim 73% Off" button) standing over the whole page, the login modal underneath it. Mechanism: _try_click's normal
pass cannot hit 'Log in!' (the cover intercepts pointer events) and its force=True fallback clicks at the control's
centre -- the cover receives that click, the login never does, and m1b still reported the click as made.

r2: before 'Log in!' is clicked, the control is hit-tested at its centre; a cover is closed through a close control
INSIDE the cover only (never the login modal's own X, never a page-wide first match), the origin is re-checked, and
a cover that cannot be closed refuses the click with a diagnostic naming it. No forced click into a cover.

The promo's DOM is not in any capture (the NSFW_GIF page HTML has no close control); the close control shape here is
inferred from the screenshot's X and the download path's run-1049 line "cleared via button[aria-label*='close' i]".
Local headless chromium, fixture served by page.route; no live site, no credentials sent anywhere.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-scrolller3r2.test"
HOME_URL = ORIGIN + "/"

# The r1 fixture (SPA login, no <form>) plus the login modal's OWN close X (#login-x) that must never be clicked.
SPA_HTML = """<!doctype html><html><body>
<header>
  <form class="inputWrapper" action="/search/collections"><input placeholder="Search" type="text"></form>
  <button type="button" class="accountMenu" id="nav">Login</button>
</header>
<div id="modal" style="position:fixed;inset:0;z-index:10;background:rgba(0,0,0,.7)">
  <button type="button" aria-label="close" id="login-x" style="position:absolute;right:8px;top:8px">&times;</button>
  <div class="tabContainer"><button class="choice">REGISTER</button>
    <button class="choice choiceActive" id="tab">LOG IN</button></div>
  <div class="inputGroup">
    <label for="identifier-input">Username / Email</label>
    <input id="identifier-input" placeholder="Username / Email" type="text" value="fixture-user">
    <label for="password-input">Password</label>
    <div><input id="password-input" placeholder="password" type="password" value="fixture-pass"><div>Show</div></div>
    <button class="action" type="button" id="forgot">Forgot Password</button>
  </div>
  <button class="acceptButton" id="accept">Log in!</button>
</div>
<script>
const bump = (k) => () => { window[k] = (window[k] || 0) + 1; };
document.getElementById('nav').addEventListener('click', bump('__navLogin'));
document.getElementById('tab').addEventListener('click', bump('__tab'));
document.getElementById('forgot').addEventListener('click', bump('__forgot'));
document.getElementById('login-x').addEventListener('click', bump('__loginClosed'));
document.getElementById('accept').addEventListener('click', () => {
  bump('__login')();
  document.getElementById('modal').remove();
  document.querySelector('header').insertAdjacentHTML('beforeend', '<a href="/logout">Logout</a>');
});
</script>
</body></html>"""

SEARCH_HTML = "<!doctype html><html><body><h1>search results</h1></body></html>"

# The promo cover as seen on the pre-submit screenshot: full-viewport overlay above the login modal, a panel with an
# X close control and a "Claim 73% Off" button. `close` chooses the close control shape; "" = no close control.
_PROMO_JS = """(close) => {
  const promo = document.createElement('div');
  promo.id = 'promo';
  promo.className = 'index-module-scss-module__SGgCpa__overlay';
  promo.style.cssText = 'position:fixed;inset:0;z-index:100;background:rgba(0,0,0,.6)';
  promo.innerHTML = '<div class="panel" style="position:absolute;inset:24px;background:#222;color:#eee">'
    + close
    + '<h2>Get Premium 73% Off</h2><p>Browse without ads, download in HD.</p>'
    + '<button type="button" id="claim">Claim 73% Off</button></div>';
  document.body.appendChild(promo);
  const bump = (k) => () => { window[k] = (window[k] || 0) + 1; };
  promo.addEventListener('click', bump('__cover'));
  document.getElementById('claim').addEventListener('click', bump('__claim'));
  const x = document.getElementById('promo-x');
  if (x) x.addEventListener('click', (ev) => { ev.stopPropagation(); bump('__promoClosed')(); promo.remove(); });
}"""
CLOSE_X = ("<button type=\"button\" aria-label=\"close\" id=\"promo-x\""
           " style=\"position:absolute;right:8px;top:8px\">&times;</button>")
# A close control that sends the page to another origin (a partner offer): an escape, not a clearance.
PARTNER = "https://fixture-premium-partner.test"
CLOSE_X_LEAVES = ("<button type=\"button\" aria-label=\"close\" id=\"promo-x\" "
                  "onclick=\"location.href='" + PARTNER + "/offer'\">&times;</button>")


def _serve(route, request):
    url = request.url.split("?", 1)[0]
    if url.startswith(ORIGIN + "/search") or not url.startswith(ORIGIN + "/"):
        route.fulfill(status=200, content_type="text/html", body=SEARCH_HTML)
    else:
        route.fulfill(status=200, content_type="text/html", body=SPA_HTML)


@contextmanager
def _spa_page(promo_close=None):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, timeout=20000,
                                        args=["--no-sandbox", "--disable-dev-shm-usage"])
        except Exception as e:
            pytest.fail(f"chromium not launchable here -- a launch failure is a FAILURE (T5): {e}")
        try:
            page = browser.new_page(viewport={"width": 1000, "height": 760})
            page.route("**/*", _serve)
            page.goto(HOME_URL, wait_until="load")
            if promo_close is not None:
                page.evaluate(_PROMO_JS, promo_close)
            yield page
        finally:
            browser.close()


def _counts(page):
    return page.evaluate("() => ({login: window.__login||0, loginClosed: window.__loginClosed||0,"
                         " cover: window.__cover||0, claim: window.__claim||0,"
                         " promoClosed: window.__promoClosed||0, tab: window.__tab||0,"
                         " nav: window.__navLogin||0, forgot: window.__forgot||0,"
                         " promo: document.querySelectorAll('#promo').length})")


def _sweep(page):
    from bulk_downloader.login_impl import submit
    return submit._submit_login(page, list(submit.SUBMIT_FALLBACKS), list(submit.PASS_FIELD_FALLBACKS))


def test_premise_a_forced_click_on_the_covered_control_lands_on_the_cover():
    """The fixture's premise (positive control for every zero below): with the promo standing, a normal click on
    'Log in!' is refused by the hit-target check and a forced click at its centre reaches the cover, not the login."""
    with _spa_page(promo_close=CLOSE_X) as page:
        with pytest.raises(Exception):
            page.click("#accept", timeout=1500)
        page.click("#accept", force=True, timeout=2000)
        counts = _counts(page)
        assert counts["login"] == 0 and counts["cover"] == 1, counts


def test_the_row_the_promo_covering_the_login_is_closed_and_log_in_is_clicked(capsys):
    """THE ROW: the live sweep with the promo over the login modal. r1 reported `click 'Log in!' after the password
    field` while the cover had taken the click. Now: the cover is closed via its own X, 'Log in!' is clicked once."""
    with _spa_page(promo_close=CLOSE_X) as page:
        ok, info = _sweep(page)
        counts = _counts(page)
        err = capsys.readouterr().err
    assert counts["login"] == 1, (
        f"DL95-SCROLLLER-3-R2: 'Log in!' was not clicked under the promo cover (counts={counts!r}, "
        f"sweep={ok!r} {info!r})\n{err[-1500:]}")
    assert counts["promo"] == 0 and counts["promoClosed"] == 1, counts
    assert counts["cover"] == 0 and counts["claim"] == 0, f"the cover took a click: {counts!r}"
    assert counts["loginClosed"] == 0 and counts["tab"] == 0 and counts["nav"] == 0 and counts["forgot"] == 0, counts
    assert "closed div#promo.index-module-scss-module__SGgCpa__overlay covering 'Log in!' via " in err, err[-1500:]
    assert "click 'Log in!' after the password field" in err, err[-1500:]
    assert ok is False and "SPA login" in info, (ok, info)


def test_negative_control_a_cover_without_a_close_control_refuses_the_click_and_names_the_cover(capsys):
    """No forced click into a cover: 'Log in!' stays unclicked, the cover and its "Claim 73% Off" take nothing,
    the login modal's own X is never used as the cover's close, and the journal names the cover."""
    with _spa_page(promo_close="") as page:
        ok, info = _sweep(page)
        counts = _counts(page)
        err = capsys.readouterr().err
    assert counts["login"] == 0 and counts["cover"] == 0 and counts["claim"] == 0, counts
    assert counts["loginClosed"] == 0 and counts["promo"] == 1, counts
    assert "'Log in!' is covered by div#promo.index-module-scss-module__SGgCpa__overlay" in err, err[-1500:]
    assert "no close control inside the cover" in err, err[-1500:]
    assert "click 'Log in!' after the password field" not in err, err[-1500:]
    assert ok is False, (ok, info)


def test_negative_control_without_a_cover_the_login_modals_own_x_is_never_touched():
    """r1 behaviour intact: no cover -> no close control is clicked (the login modal's X stays), 'Log in!' once."""
    with _spa_page() as page:
        ok, info = _sweep(page)
        counts = _counts(page)
    assert counts["login"] == 1 and counts["loginClosed"] == 0, (counts, ok, info)
    assert counts["tab"] == 0 and counts["nav"] == 0 and counts["forgot"] == 0, counts


def test_negative_control_a_close_control_that_leaves_the_origin_is_not_a_clearance(capsys):
    """Closing the cover navigated away: that is an escape, not a clearance. The submit is not clicked on
    whatever page that is, and the journal says the origin was left."""
    with _spa_page(promo_close=CLOSE_X_LEAVES) as page:
        ok, info = _sweep(page)
        err = capsys.readouterr().err
        url = page.url
    assert "left the origin" in err, err[-1500:]
    assert "click 'Log in!' after the password field" not in err, err[-1500:]
    assert ok is False and url.startswith(PARTNER), (ok, info, url)


def test_the_cover_hit_test_js_reports_clear_covered_and_gone():
    from bulk_downloader.login_impl import submit
    with _spa_page(promo_close=CLOSE_X) as page:
        assert page.evaluate(submit._PASSWORD_SCOPED_SUBMIT_JS, ["input[type=password]"]) == "Log in!"
        got = submit._hit_test_control(page, page.locator("[data-bd-pw-submit='1']"))
        assert got["state"] == "covered", got
        assert got["cover"] == "div#promo.index-module-scss-module__SGgCpa__overlay", got
        assert page.locator("[data-bd-pw-cover='1']").count() == 1
        page.evaluate("() => document.getElementById('promo').remove()")
        got = submit._hit_test_control(page, page.locator("[data-bd-pw-submit='1']"))
        assert got["state"] == "clear", got
        assert page.locator("[data-bd-pw-cover='1']").count() == 0
        page.evaluate("() => document.getElementById('accept').remove()")
        got = submit._hit_test_control(page, page.locator("[data-bd-pw-submit='1']"))
        assert got["state"] == "gone", got
