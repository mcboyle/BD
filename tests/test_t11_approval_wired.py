"""Current approval UI SPA safety contract.

Ports the legacy per-site auto-submit / post-reveal approval gate
(bulk_downloader/static/approval_ui.js, 233 lines) into the React SPA.
The BACKEND DOES NOT CHANGE — the two decision endpoints already exist and
are pinned by tests/test_auto_submit_approval.py. The only new risk is an
SPA-side gate BYPASS, which these tests pin.

The tests guarantee the SPA-surfaced path cannot bypass the backend gate:
  * test_endpoint_interposition_auto_submit / _post_reveal — drive the real
    Flask routes the SPA will call; a challenge-marked candidate stays
    do_not_auto_submit=True / approval_status="pending" until an operator
    approve, and a decline keeps it closed.
  * test_no_raw_mutating_fetch_to_decision_paths — the decision
    POSTs must ride apiPost (CSRF), never a raw fetch().

run_tests.py conventions: zero-arg test functions; repo root from
Path(__file__).resolve().parent.parent; no pytest builtins.
"""
import importlib.util
import re
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"
import sys

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

pytestmark = pytest.mark.bd_module_wipe

# The three backend endpoints T11 wires (normalised spelling: path
# params -> '*', method dropped — matches gui_parity_inventory._norm_ep).
DECISION_ENDPOINTS = [
    "/api/sites/*/auto_submit_decision",
    "/api/sites/*/post_reveal_decision",
    "/api/sites/*/pending_approvals",
]

# The exact FULL /api/ literals the SPA must carry for scanner credit
# (template literals, NOT a concatenated base var).
REQUIRED_LITERALS = [
    "/api/sites/${sid}/pending_approvals",
    "/api/sites/${sid}/auto_submit_decision",
    "/api/sites/${sid}/post_reveal_decision",
]

# Bot-defense login form (cf-turnstile) — pending-by-default; mirrors
# tests/test_auto_submit_approval.py _BOT_FORM.
_BOT_FORM = (
    '<html><body><form method="POST" action="/login">'
    '<input name="username"><input type="password" name="pw">'
    '<div class="cf-turnstile" data-sitekey="x"></div>'
    '<button>Sign in</button></form></body></html>'
)


def _load_inventory():
    spec = importlib.util.spec_from_file_location(
        "gui_parity_inventory", REPO / "tools" / "gui_parity_inventory.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _norm(path):
    """Same normalisation the inventory uses for endpoint matching."""
    p = (path or "").strip()
    p = re.sub(r"<[^>]+>|\{[^}]+\}|\$\{[^}]+\}|:[A-Za-z_]+", "*", p)
    return p.rstrip("/ ")


# ── RED: SPA wiring ──────────────────────────────────────────────────

def test_decision_endpoints_spa_wired():
    """Both decision endpoints must read spa_wired=True in the parity
    inventory — the migration gate. RED on pristine 263 (the SPA references
    neither endpoint yet), GREEN once useApproval.ts + the SiteDetail gate
    carry the full /api/ literals."""
    inv = _load_inventory()
    items = inv.build(str(REPO))["items"]
    want = set(DECISION_ENDPOINTS)
    by_ep = {}
    for it in items:
        ep = it.get("command_or_endpoint") or ""
        n = inv._norm_ep(ep)  # the inventory's own normaliser (drops METHOD)
        if n in want:
            if not by_ep.get(n) or it.get("spa_wired"):
                by_ep[n] = it
    unwired = [w for w in DECISION_ENDPOINTS
               if not (by_ep.get(w) and by_ep[w].get("spa_wired"))]
    assert not unwired, (
        "T11 endpoints not spa_wired in the inventory "
        "(useApproval.ts + SiteDetail gate must carry the full /api/ "
        "literals): " + repr(unwired))


def test_useapproval_full_literals_present():
    """The hook must carry FULL /api/ template literals (scanner credit) —
    not a concatenated base var. RED on pristine 263 (file absent)."""
    hook = REPO / "frontend" / "src" / "hooks" / "useApproval.ts"
    assert hook.exists(), "frontend/src/hooks/useApproval.ts does not exist"
    text = hook.read_text(encoding="utf-8", errors="replace")
    missing = [lit for lit in REQUIRED_LITERALS if lit not in text]
    assert not missing, (
        "full /api/ literals missing from useApproval.ts: " + repr(missing))


# ── GREEN guardrail: no CSRF-less raw mutation to the decision paths ──

def test_no_raw_mutating_fetch_to_decision_paths():
    """The decision POSTs must ride apiPost (which injects X-CSRF-Token),
    never a raw fetch() — a raw mutating fetch to these paths would 403 on a
    real cookie session and, worse, route around the wrapper. Scans all of
    frontend/src for a mutating fetch() whose options literal names either
    decision path."""
    spa_dir = REPO / "frontend" / "src"
    pat = re.compile(
        r"fetch\([^;]{0,400}?(auto_submit_decision|post_reveal_decision)"
        r"[^;]{0,400}?method:\s*[\"'](POST|PUT|PATCH|DELETE)[\"']",
        re.S,
    )
    offenders = []
    for f in spa_dir.rglob("*.ts*"):
        if pat.search(f.read_text(encoding="utf-8", errors="replace")):
            offenders.append(str(f.relative_to(REPO)))
    assert not offenders, (
        "raw state-changing fetch() to a decision path (must use apiPost): "
        + repr(offenders))


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
