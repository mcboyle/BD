"""tpl95-whoreshub-1 r2 -- the Login control is a <div data-href> fancybox trigger, not an <a>/<button>.

Measured 2026-10-14T20:01Z/20:06Z headless on 10.0.70.95 (the app's host; CDP oracle down,
AMENDMENT 7): login_url https://www.whoreshub.com/ holds no password input; its header
Login control is ``<div data-href="https://www.whoreshub.com/login/" data-fancybox="ajax"
id="login">Login</div>`` (mobile twin ``<span id="login">`` in ul.mob-member, display:none).
``_AUTO_TRIGGER_JS`` censused "button, a, [role='button']" -> no hit, so
``_fire_login_trigger_if_needed`` returned (False, False, "") and do_login reported
"Couldn't find username field" with no trigger prefix (RESULT-tpl95-whoreshub-1, LIVE-FAIL).
Clicking the div is intercepted by the age-gate ``#overlay`` (z-index 2000000); following
its data-href renders the real form (#login_username, name=username, 1 password input).

Fixtures are verbatim trimmed captures (tests/fixtures/tpl95_whoreshub_1_r2_home_header.html,
tests/fixtures/tpl95_whoreshub_login_fragment.html); every request is answered by page.route
(the /login/ fragment, or 204), nothing reaches the network.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.capture_serial

FIX = Path(__file__).parent / "fixtures"
HOME_URL = "https://www.whoreshub.com/"
LOGIN_URL = "https://www.whoreshub.com/login/"
HOME = (FIX / "tpl95_whoreshub_1_r2_home_header.html").read_text(encoding="utf-8")
LOGIN = (FIX / "tpl95_whoreshub_login_fragment.html").read_text(encoding="utf-8")


def _html(r, body):
    r.fulfill(status=200, content_type="text/html", body=body)


@contextmanager
def _page(home_html=HOME, login_html=LOGIN):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    pages = {HOME_URL: home_html, LOGIN_URL: login_html}
    with sync_playwright() as p:
        br = p.chromium.launch(headless=True)
        try:
            pg = br.new_page(viewport={"width": 1366, "height": 900})
            pg.route("**/*", lambda r: _html(r, pages[r.request.url])
                     if r.request.url in pages else r.fulfill(status=204, body=""))
            pg.goto(HOME_URL, wait_until="load")
            yield pg
        finally:
            br.close()


def _fire(pg):
    from bulk_downloader.login_impl import _common as c
    from bulk_downloader.login_impl.submit import USER_FIELD_FALLBACKS

    needed, fired, detail = c._fire_login_trigger_if_needed(
        pg, "", USER_FIELD_FALLBACKS, config={"login_url": HOME_URL})
    pg.wait_for_timeout(300)
    ok, why = c._try_fill(pg, USER_FIELD_FALLBACKS, "someuser", "username")
    return (needed, fired, detail), (ok, why)


def test_fixture_is_the_measured_header():
    with _page() as pg:
        assert pg.locator("input[type='password']").count() == 0
        assert pg.locator("#login").count() == 2, "desktop div#login + mobile span#login twins"
        assert pg.locator("button#login, a#login, [role='button']#login").count() == 0
        assert pg.locator("div#login[data-href][data-fancybox='ajax']").is_visible()
        assert not pg.locator("span#login").is_visible(), "ul.mob-member is display:none at 1366px"


def test_data_href_login_control_under_the_age_gate_reveals_the_username_field():
    with _page() as pg:
        (needed, fired, detail), (ok, why) = _fire(pg)
        url = pg.url
        passwords = pg.locator("input[type='password']").count()
        typed = pg.locator("#login_username").input_value() if passwords else ""
    assert (needed, fired) == (True, True), (
        f"WHORESHUB_DATA_HREF_LOGIN_CONTROL_NOT_SEEN: helper said {detail!r}")
    assert "data-href" in detail and "not actionable" in detail, detail
    assert url == LOGIN_URL and passwords == 1, (url, passwords)
    assert ok is True and typed == "someuser", (ok, why, typed)


def test_two_visible_data_href_login_controls_are_not_guessed_at():
    twin = HOME.replace("<ul class=\"mob-member\">", "<ul class=\"mob-member\" style=\"display:flex\">")
    with _page(home_html=twin) as pg:
        assert pg.locator("span#login").is_visible()
        (needed, fired, detail), (ok, _why) = _fire(pg)
        url = pg.url
    assert (needed, fired, detail) == (False, False, ""), detail
    assert url == HOME_URL and ok is False


def test_plain_div_labelled_login_without_data_href_is_not_a_candidate():
    bare = HOME.replace(
        '<div data-href="https://www.whoreshub.com/login/" data-fancybox="ajax" id="login">Login</div>',
        '<div id="login">Login</div>')
    assert bare != HOME
    with _page(home_html=bare) as pg:
        assert pg.locator("div#login").is_visible()
        (needed, fired, detail), (ok, _why) = _fire(pg)
        url = pg.url
    assert (needed, fired, detail) == (False, False, ""), detail
    assert url == HOME_URL and ok is False


def test_signup_sibling_is_not_mistaken_for_the_login_control():
    no_login = HOME.replace(
        '<li><div data-href="https://www.whoreshub.com/login/" data-fancybox="ajax" id="login">Login</div></li>', "")
    assert no_login != HOME
    with _page(home_html=no_login) as pg:
        assert pg.locator("ul.member-links div[data-href]").count() == 1  # Sign up only
        (needed, fired, detail), (ok, _why) = _fire(pg)
        url = pg.url
    assert (needed, fired, detail) == (False, False, ""), detail
    assert url == HOME_URL and ok is False
