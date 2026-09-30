"""fx-blacked-login-blocked (O1567): the app's login/takeover browser must not break a page's own
navigator.permissions.query.

bd4 live 2026-09-30 01:16Z: login.vixen.com/i/blacked/login answered the app's login with "This site
uses some functionality that was blocked by your browser ... disable any ad blockers". The page's
onSubmit collects reCAPTCHA / Castle / FingerprintJS tokens before it POSTs. Measured on the real
page (dummy credentials, POST held in the browser): plain Chrome POSTs all three tokens; the takeover
launch args + STEALTH_JS never POST. Bisected to ONE pair: --disable-notifications (it removes
window.Notification) + STEALTH_JS's permissions.query override, which read Notification.permission
synchronously -> a notifications query THREW ReferenceError instead of returning a promise.

cloak.DEFAULT_NORMALIZATION_SCRIPT already leaves permissions.query native (Row 915 REFUTE E2);
STEALTH_JS must do the same. Real Chromium, a locally fulfilled https page (secure context), no
network.
"""
from __future__ import annotations

import pytest

BD_GATE_SCOPE = "module"

_PAGE = "https://bd-test.invalid/login"

_QUERY_JS = """async () => {
  const o = {notificationType: typeof Notification,
             queryNative: Function.prototype.toString.call(navigator.permissions.query).includes('[native code]')};
  let r;
  try { r = navigator.permissions.query({name: 'notifications'}); }
  catch (e) { o.syncThrow = e.name + ': ' + e.message; return o; }
  o.returnsPromise = r instanceof Promise;
  try { const s = await r; o.state = s.state; o.isPermissionStatus = s instanceof PermissionStatus; }
  catch (e) { o.rejected = String(e); }
  return o;
}"""


def _flag_sets():
    from bulk_downloader import cloak
    from bulk_downloader.login_impl.manual import _manual_launch_kwargs
    return {
        "takeover": _manual_launch_kwargs({"use_real_chrome": False}, headless=True)["args"],
        "cloak-standard": list(cloak.STANDARD_LAUNCH_FLAGS),
        "cloak-stealth-args": cloak.get_stealth_args(),
    }


def _probe(args, *, stealth, library=False):
    from playwright.sync_api import sync_playwright
    from bulk_downloader.constants import STEALTH_JS
    from bulk_downloader import stealth as stealth_lib
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=args)
        try:
            context = browser.new_context()
            context.route("**/*", lambda route: route.fulfill(
                status=200, content_type="text/html", body="<html><body>login</body></html>"))
            if stealth:
                context.add_init_script(STEALTH_JS)
            page = context.new_page()
            if library:
                applied, detail = stealth_lib.apply_to_page(page, {"use_stealth_library": True})
                assert applied, f"premise gone -- playwright-stealth not applied: {detail}"
            page.goto(_PAGE)
            return page.evaluate(_QUERY_JS)
        finally:
            browser.close()


@pytest.mark.parametrize("flags", ["takeover", "cloak-standard", "cloak-stealth-args"])
def test_notifications_permission_query_survives_stealth_under_disable_notifications(flags):
    args = _flag_sets()[flags]
    assert "--disable-notifications" in args, f"{flags}: premise gone -- flag set no longer disables notifications"
    control = _probe(args, stealth=False)
    # positive control: without the init script this browser answers the query natively
    assert control.get("returnsPromise") is True and control.get("isPermissionStatus") is True, control
    probe = _probe(args, stealth=True)
    assert "syncThrow" not in probe, (
        f"STEALTH_JS broke navigator.permissions.query under {flags} launch flags: a notifications "
        f"query threw synchronously ({probe['syncThrow']}) -- page scripts (FingerprintJS/Castle) "
        f"abort and the login never posts its tokens")
    assert probe.get("returnsPromise") is True, probe
    assert probe.get("isPermissionStatus") is True and probe.get("state") == control["state"], (control, probe)


@pytest.mark.parametrize("flags", ["takeover", "cloak-standard"])
def test_notifications_permission_query_survives_the_stealth_library(flags):
    """bd4 live 02:20Z: with STEALTH_JS fixed the app's login browser still held blacked's submit --
    blacked sets use_stealth_library, and playwright-stealth's navigator.permissions evasion reads
    Notification.permission the same way. stealth.apply_to_page must leave the query native."""
    args = _flag_sets()[flags]
    control = _probe(args, stealth=False)
    assert control.get("returnsPromise") is True, control
    probe = _probe(args, stealth=False, library=True)
    assert "syncThrow" not in probe, (
        f"the playwright-stealth library broke navigator.permissions.query under {flags} launch flags: "
        f"a notifications query threw synchronously ({probe['syncThrow']}) -- the page's token step aborts")
    assert probe.get("returnsPromise") is True and probe.get("isPermissionStatus") is True, probe
    assert probe.get("state") == control["state"], (control, probe)


def test_stealth_leaves_permissions_query_native():
    """Same contract as cloak.DEFAULT_NORMALIZATION_SCRIPT (Row 915 acceptance 3): a replaced query
    is itself a detectable artefact (non-native toString, a fake {state:'default'} object)."""
    control = _probe([], stealth=False)
    probe = _probe([], stealth=True)
    assert control["queryNative"] is True, control
    assert probe["queryNative"] is True, probe
    assert probe.get("isPermissionStatus") is True and probe.get("state") == control["state"], (control, probe)
