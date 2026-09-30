"""fx-vixen-login-challenge (O1567): the app's stealth library must not break a page's own srcdoc iframes.

bd4 blacked 2026-09-30 04:24-04:40Z: after Cloudflare, login.vixen.com/i/blacked/login/challenge dead-ended ("Not
found", FingerprintJS font-probe glyphs left on the page) for the app login AND the operator's manual takeover.
Measured on that page (no login, no click): plain Chrome, cloakbrowser, cloakbrowser+STEALTH_JS clean up the probe;
any browser with playwright-stealth applied leaves it -- and of its 19 evasions only iframe_content_window does.
That evasion, when a page sets iframe.srcdoc before attaching the iframe (FingerprintJS's isolated measuring
frame), pins contentWindow to a proxy of the TOP window and freezes srcdoc at its old empty value: the frame's
content never loads and the script measures in (and litters) the top document, never finishing.

Real Chromium, a locally fulfilled https page, no network. The page builds a srcdoc iframe exactly that way.
"""
from __future__ import annotations

import pytest

BD_GATE_SCOPE = "module"

_PAGE = "https://bd-test.invalid/login/challenge"

_SRCDOC_IFRAME_JS = """() => new Promise(resolve => {
  const f = document.createElement('iframe');
  f.srcdoc = '<!doctype html><html><body>iframe-ok</body></html>';
  f.style.display = 'none';
  f.onload = () => {
    let body = null, isolated = null;
    try { isolated = f.contentWindow.document !== document; body = f.contentDocument.body.textContent; }
    catch (e) { body = 'ERR ' + e.name; }
    f.remove();
    resolve({isolated, body});
  };
  document.body.appendChild(f);
  setTimeout(() => resolve({timeout: true}), 5000);
})"""


def _probe(*, library):
    from playwright.sync_api import sync_playwright
    from bulk_downloader import stealth
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            context.route("**/*", lambda route: route.fulfill(
                status=200, content_type="text/html", body="<html><body>challenge</body></html>"))
            page = context.new_page()
            if library:
                applied, detail = stealth.apply_to_page(page, {"use_stealth_library": True})
                assert applied, f"premise gone -- playwright-stealth not applied: {detail}"
            page.goto(_PAGE)
            return page.evaluate(_SRCDOC_IFRAME_JS)
        finally:
            browser.close()


def test_stealth_library_leaves_a_pages_srcdoc_iframe_isolated_and_loaded():
    control = _probe(library=False)
    # positive control: without the library the srcdoc frame is its own window with its own content
    assert control == {"isolated": True, "body": "iframe-ok"}, control
    probe = _probe(library=True)
    assert probe == control, (
        "stealth.apply_to_page broke a page's srcdoc iframe (playwright-stealth iframe_content_window): "
        f"{probe} -- FingerprintJS's measuring frame never loads, vixen's login challenge dead-ends")
