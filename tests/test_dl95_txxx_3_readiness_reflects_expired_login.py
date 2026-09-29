"""dl95-txxx-3: the site page's readiness card read green "Ready" while the site's login was dead.

Measured on test2 (B7-B p1/txxx RESULT.md + scan_not_logged_in__1.png): scan -> NOT_LOGGED_IN; POST /login ->
ok=false "Manual login required: Couldn't submit form: form method is GET -- refused ..."; /api/sites/v2
auth_state "expired" -- yet GET /api/sites/<sid>/readiness said level green (Login URL configured, Download
directory writable): its auth check read only cookie_health, which has no entry for a site whose login never
landed. GREEN: readiness takes the runner's own auth bucket (_m2_auth_state, the Sites list's source), so an
expired login is a red "Auth health" check carrying the last login message, with the re-login fix.
Real Flask test client on the conftest's isolated BD_HOME; no network, no browser.
"""
import sys
import time

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

LOGIN_MSG = ("Manual login required: Couldn't submit form: form method is GET -- refused: "
             "submitting would put the credentials in the URL")


@pytest.fixture(scope="module", autouse=True)
def _restore_bd_modules_after_file():
    """Put the bulk_downloader module table back when this file finishes (1034 leaker census)."""
    saved = {m: mod for m, mod in sys.modules.items() if m == "bulk_downloader" or m.startswith("bulk_downloader.")}
    try:
        yield
    finally:
        for m in [m for m in sys.modules if m == "bulk_downloader" or m.startswith("bulk_downloader.")]:
            del sys.modules[m]
        sys.modules.update(saved)


def _site(cookies, login_status=""):
    for mod in list(sys.modules):
        if mod.startswith("bulk_downloader"):
            del sys.modules[mod]
    from bulk_downloader import app as a
    c = a.app.test_client()
    sid = c.post("/api/sites", json={"name": "txxx", "login_url": "https://member.txxx.com/login"}).get_json()["id"]
    runner = a.runners[sid]
    runner.cookies = cookies
    if login_status:
        runner._set_login_status(login_status)
    auth = next(s["auth_state"] for s in c.get("/api/sites/v2").get_json()["sites"] if s["site_id"] == sid)
    return c.get(f"/api/sites/{sid}/readiness").get_json(), auth


def _auth_checks(body):
    return [ch for ch in body["checks"] if ch["key"] == "auth_health"]


def test_an_expired_login_is_red_with_the_last_login_message():
    body, auth = _site([{"name": "PHPSESSID", "value": "x", "expires": time.time() - 3600}], LOGIN_MSG)
    assert auth == "expired"  # precondition: the Sites list says so (the finding's auth_state)
    assert body["level"] == "red", body
    (chk,) = _auth_checks(body)
    assert chk["status"] == "fail" and LOGIN_MSG in chk["detail"], chk
    assert "Re-login / refresh this site's credentials." in body["fixes"], body


def test_a_live_login_adds_no_auth_failure():
    """Control: an in-date cookie is not flagged."""
    body, auth = _site([{"name": "PHPSESSID", "value": "x", "expires": time.time() + 86400}])
    assert auth == "ok"
    assert not [ch for ch in _auth_checks(body) if ch["status"] == "fail"], body
    assert body["level"] != "red", body


def test_no_cookie_jar_is_not_called_expired():
    """Control: 'unknown' (never logged in) keeps the old behaviour -- no invented failure."""
    body, auth = _site([])
    assert auth == "unknown"
    assert not [ch for ch in _auth_checks(body) if ch["status"] == "fail"], body
