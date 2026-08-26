"""T11 approval caller behavior plus real backend interposition pins."""

from pathlib import Path
import sys

import pytest

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

pytestmark = pytest.mark.bd_module_wipe

# Bot-defense login form (cf-turnstile) — pending-by-default; mirrors
# tests/test_auto_submit_approval.py _BOT_FORM.
_BOT_FORM = (
    '<html><body><form method="POST" action="/login">'
    '<input name="username"><input type="password" name="pw">'
    '<div class="cf-turnstile" data-sitekey="x"></div>'
    '<button>Sign in</button></form></body></html>'
)

def test_approval_caller_runtime_contract():
    spec = "src/routes/ApprovalGate.wired.test.tsx"
    receipt = run_vitest(spec, expected_tests=3)
    expected = {
        "spec": spec,
        "files_passed": 1,
        "files_collected": 1,
        "tests_passed": 3,
        "tests_collected": 3,
    }
    assert receipt == expected, (
        "Vitest delegation evidence missing or mismatched for ApprovalGate: "
        f"expected={expected!r}, observed={receipt!r}"
    )


# ── GREEN regression pins: the SPA-surfaced path still interposes ────

def _client_with_site(sid="t11.test"):
    """Boot the app test-client and register a site in s_cfg so the
    decision endpoints resolve it (cfg = s_cfg.get(sid))."""
    from bulk_downloader.db import db_init
    from bulk_downloader import app as A
    db_init()
    A.s_cfg[sid] = {"url": "https://%s/" % sid}
    c = A.app.test_client()
    return A, c, sid


def _csrf(c):
    d = c.get("/api/csrf").get_json()
    assert d.get("ok") and d.get("csrf_token"), d
    return d["csrf_token"]


def test_endpoint_interposition_auto_submit():
    """Drive the REAL /api/sites/<sid>/auto_submit_decision route the SPA
    will call: a bot-defense form is pending + closed by default; after an
    operator APPROVE through the endpoint the next analysis reports approved
    + the gate opens; a DECLINE keeps it closed. Pins the interposition the
    SPA port must not bypass."""
    from bulk_downloader import deep_detect as dd
    from bulk_downloader import learn
    A, c, sid = _client_with_site()
    try:
        best = dd.score_login_page(
            _BOT_FORM, base_url="https://t11.test/")["best"]
        assert best["approval_status"] == "pending"
        assert best["do_not_auto_submit"] is True
        key = best["approval_key"]

        # unknown site -> 404
        tok = _csrf(c)
        r404 = c.post("/api/sites/nope/auto_submit_decision",
                      json={"key": key, "decision": "approve"},
                      headers={"X-CSRF-Token": tok})
        assert r404.status_code == 404, r404.get_json()

        # bad decision -> 400
        r400 = c.post("/api/sites/%s/auto_submit_decision" % sid,
                      json={"key": key, "decision": "maybe"},
                      headers={"X-CSRF-Token": tok})
        assert r400.status_code == 400, r400.get_json()

        # APPROVE through the endpoint
        r = c.post("/api/sites/%s/auto_submit_decision" % sid,
                   json={"key": key, "decision": "approve"},
                   headers={"X-CSRF-Token": tok})
        assert r.status_code == 200, r.get_json()
        assert r.get_json() == {"ok": True, "decision": "approve", "key": key}

        sm = learn.deep_detect_site_memory(A.s_cfg[sid])
        again = dd.score_login_page(
            _BOT_FORM, base_url="https://t11.test/", site_memory=sm)["best"]
        assert again["approval_status"] == "approved"
        assert again["do_not_auto_submit"] is False
    finally:
        A.s_cfg.pop(sid, None)


def test_endpoint_interposition_decline_stays_closed():
    """A DECLINE through the endpoint keeps the gate closed."""
    from bulk_downloader import deep_detect as dd
    from bulk_downloader import learn
    A, c, sid = _client_with_site(sid="t11decl.test")
    try:
        best = dd.score_login_page(
            _BOT_FORM, base_url="https://t11decl.test/")["best"]
        key = best["approval_key"]
        tok = _csrf(c)
        r = c.post("/api/sites/%s/auto_submit_decision" % sid,
                   json={"key": key, "decision": "decline"},
                   headers={"X-CSRF-Token": tok})
        assert r.status_code == 200, r.get_json()
        sm = learn.deep_detect_site_memory(A.s_cfg[sid])
        again = dd.score_login_page(
            _BOT_FORM, base_url="https://t11decl.test/", site_memory=sm)["best"]
        assert again["approval_status"] == "declined"
        assert again["do_not_auto_submit"] is True
    finally:
        A.s_cfg.pop(sid, None)


def test_auto_submit_decision_requires_csrf():
    """A cookie-backed session that omits the token is rejected (the gate is
    a state change; the SPA rides apiPost which carries the token)."""
    A, c, sid = _client_with_site(sid="t11csrf.test")
    try:
        c.get("/api/csrf")  # mint session + bd_session cookie, then omit token
        r = c.post("/api/sites/%s/auto_submit_decision" % sid,
                   json={"key": "k", "decision": "approve"})
        assert r.status_code == 403, (
            "decision POST from a cookie session without the token should "
            "403, got %s: %s" % (r.status_code, r.get_json()))
    finally:
        A.s_cfg.pop(sid, None)
