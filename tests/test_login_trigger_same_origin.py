from __future__ import annotations
from contextlib import contextmanager
import pytest
BD_GATE_SCOPE = 'module'
pytestmark = pytest.mark.capture_serial
HOME_URL = 'https://www.whoreshub.com/'
SAME_LOGIN_URL = 'https://www.whoreshub.com/login/'
EVIL_LOGIN_URL = 'https://evil.example/login/'
_OVERLAY = '<div style="position:fixed;left:0;top:0;width:100%;height:100%;background:transparent;z-index:2000000"></div>'
_LOGIN_HTML = "<!doctype html><html><body><form><input id='login_username' type='text' name='username'><input type='password' name='password'></form></body></html>"

def _home_with(control_html: str) -> str:
    return f'<!doctype html><html><body><nav>{control_html}</nav>{_OVERLAY}</body></html>'

@contextmanager
def _routed_page(home_html: str):
    sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright
    pages = {HOME_URL: home_html, SAME_LOGIN_URL: _LOGIN_HTML, EVIL_LOGIN_URL: _LOGIN_HTML}
    visited = []
    with sync_playwright() as p:
        br = p.chromium.launch(headless=True)
        try:
            pg = br.new_page(viewport={'width': 1366, 'height': 900})

            def _handle(route):
                visited.append(route.request.url)
                body = pages.get(route.request.url)
                if body is not None:
                    route.fulfill(status=200, content_type='text/html', body=body)
                else:
                    route.fulfill(status=204, body='')
            pg.route('**/*', _handle)
            pg.goto(HOME_URL, wait_until='load')
            yield (pg, visited)
        finally:
            br.close()

def test_off_origin_anchor_href_is_refused_by_auto_login_trigger():
    html = _home_with('<a href="https://evil.example/login/">Log In</a>')
    with _routed_page(html) as (pg, visited):
        from bulk_downloader.login_impl._common import _fire_auto_login_trigger
        needed, fired, detail = _fire_auto_login_trigger(pg)
        final_url = pg.url
        passwords = pg.locator("input[type='password']").count()
    assert (needed, fired) == (True, False), f'OFF_ORIGIN_ANCHOR_FOLLOWED: detail={detail!r} final_url={final_url!r}'
    assert 'could not click' in detail
    assert final_url == HOME_URL and EVIL_LOGIN_URL not in visited
    assert passwords == 0

def test_off_origin_data_href_is_refused_by_auto_login_trigger():
    html = _home_with('<div data-href="https://evil.example/login/">Login</div>')
    with _routed_page(html) as (pg, visited):
        from bulk_downloader.login_impl._common import _fire_auto_login_trigger
        needed, fired, detail = _fire_auto_login_trigger(pg)
        final_url = pg.url
        passwords = pg.locator("input[type='password']").count()
    assert (needed, fired) == (True, False), f'OFF_ORIGIN_DATA_HREF_FOLLOWED: detail={detail!r} final_url={final_url!r}'
    assert 'could not click' in detail
    assert final_url == HOME_URL and EVIL_LOGIN_URL not in visited
    assert passwords == 0

def test_same_origin_relative_data_href_is_resolved_and_followed():
    html = _home_with('<div data-href="/login/">Login</div>')
    with _routed_page(html) as (pg, visited):
        from bulk_downloader.login_impl._common import _fire_auto_login_trigger
        needed, fired, detail = _fire_auto_login_trigger(pg)
        final_url = pg.url
        passwords = pg.locator("input[type='password']").count()
    assert (needed, fired) == (True, True), detail
    assert 'data-href' in detail
    assert final_url == SAME_LOGIN_URL and SAME_LOGIN_URL in visited
    assert passwords == 1

def test_scheme_downgrade_anchor_href_is_refused_by_auto_login_trigger():
    html = _home_with('<a href="http://www.whoreshub.com/login/">Log In</a>')
    with _routed_page(html) as (pg, visited):
        from bulk_downloader.login_impl._common import _fire_auto_login_trigger
        needed, fired, detail = _fire_auto_login_trigger(pg)
        final_url = pg.url
    assert (needed, fired) == (True, False), f'SCHEME_DOWNGRADE_FOLLOWED: detail={detail!r} final_url={final_url!r}'
    assert 'could not click' in detail
    assert final_url == HOME_URL and 'http://www.whoreshub.com/login/' not in visited
