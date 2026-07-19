"""P0.1 (v3.66.202) — CSRF bootstrap extracted from the legacy render.

LEGACY_MIGRATION_PLAN Phase 0, P0.1: the session/CSRF bootstrap must be
an app-level mechanism, independent of the legacy template render. The
blocking proof: a client that NEVER loads the legacy shell (never GETs
"/") can complete a CSRF-protected POST.

Before this change, sessions were minted only on GET / (serve_index
inline mint + the path-gated _bootstrap_session after_request hook);
/api/csrf refused with {"ok": false, "error": "no session — ..."} when
no session cookie was present. The SPA's api-client then held a null
token and every protected POST 403'd for any client whose first contact
wasn't the legacy shell.

After: GET /api/csrf mints the session itself (same anonymous
source="csrf_bootstrap" session a cookie-less GET / receives) and sets
the bd_session cookie on its own response.

Contract re-expression note: test_security.py::TestPhase40CSRFAndPairing
::test_csrf_endpoint_no_session pinned the old refusal; it is updated in
the same cut to pin the new mint behavior (re-expressed, not dropped).

Runner notes: custom run_tests.py — zero-arg test functions, no pytest
builtins; repo root via Path(__file__).resolve().parent.parent.
"""

from pathlib import Path
import sys

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _fresh_client():
    """A brand-new test client with an empty cookie jar."""
    from bulk_downloader import app as A
    return A.app.test_client()


def test_csrf_mints_session_without_legacy_shell():
    """GET /api/csrf with no cookies returns ok + token + sets the
    bd_session cookie — without the client ever touching "/"."""
    c = _fresh_client()
    r = c.get("/api/csrf")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True, f"/api/csrf refused on a fresh client: {d}"
    assert d.get("csrf_token"), "no csrf_token in mint response"
    set_cookie = r.headers.getlist("Set-Cookie")
    joined = ";".join(set_cookie)
    assert "bd_session=" in joined, (
        f"mint response did not set the session cookie: {set_cookie}")
    assert "HttpOnly" in joined, "session cookie must stay HttpOnly"
    assert "SameSite=Lax" in joined, "session cookie must stay SameSite=Lax"


def test_protected_post_succeeds_without_legacy_shell():
    """The P0.1 blocking proof: fresh client → /api/csrf → CSRF-protected
    POST succeeds (no 403), with "/" never requested."""
    from bulk_downloader.db import db_init
    db_init()
    c = _fresh_client()
    d = c.get("/api/csrf").get_json()
    assert d["ok"] is True and d.get("csrf_token")
    r = c.post("/api/sites", json={"name": "p01-bootstrap-proof"},
               headers={"X-CSRF-Token": d["csrf_token"]})
    assert r.status_code != 403, (
        "CSRF-protected POST 403'd for a client that bootstrapped via "
        f"/api/csrf alone: {r.get_json()}")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    sid = body.get("id")
    assert sid is not None, f"site create did not return an id: {body}"
    # cleanup so repeated runs / later tests don't accrete sites
    c.delete(f"/api/sites/{sid}",
             headers={"X-CSRF-Token": d["csrf_token"]})


def test_existing_session_path_unchanged():
    """A client that already has a valid session gets the SAME token back
    (no re-mint, no extra Set-Cookie) — the pre-P0.1 happy path is
    byte-compatible."""
    c = _fresh_client()
    d1 = c.get("/api/csrf").get_json()      # mints
    r2 = c.get("/api/csrf")                  # reuses
    d2 = r2.get_json()
    assert d2["ok"] is True
    assert d2["csrf_token"] == d1["csrf_token"], (
        "token changed across calls within one session")
    assert d2.get("minted") is None, (
        "second call re-minted despite a valid session")
    assert "bd_session=" not in ";".join(r2.headers.getlist("Set-Cookie")), (
        "second call set a session cookie despite a valid session")


def test_mint_marks_source_csrf_bootstrap():
    """The minted session is the same anonymous class GET / mints —
    source == "csrf_bootstrap" (diagnostics parity, no new session
    class)."""
    from bulk_downloader import app as A
    c = _fresh_client()
    r = c.get("/api/csrf")
    assert r.get_json()["ok"] is True
    # pull the cookie value the response set
    joined = ";".join(r.headers.getlist("Set-Cookie"))
    assert "bd_session=" in joined
    token = joined.split("bd_session=", 1)[1].split(";", 1)[0]
    rec = A._sessions.get(token)
    assert rec is not None, "minted session not in the session store"
    assert rec["source"] == "csrf_bootstrap", rec
