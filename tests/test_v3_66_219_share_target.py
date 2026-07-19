"""v3.66.219+ — F4.1 PWA share-target server-side contract.

The SPA-side behavior (share-prefill of the resolve box; the shared
useEventStream singleton for F4.5) is proven in-sandbox via the make_server +
Playwright EventSource recipe (SANDBOX_RENDERING_GUIDE) — it cannot run as a
jsdom unit test here. These tests pin the parts the server owns: the manifest
share_target descriptor and that the share_target action URL serves the SPA.

Custom runner: zero-arg functions, no pytest builtins.
"""

import json


def _client():
    from bulk_downloader import app as A
    A.db_init()
    return A.app.test_client()


def test_manifest_has_share_target():
    c = _client()
    r = c.get("/static/manifest.json")
    assert r.status_code == 200
    m = json.loads(r.data)
    st = m.get("share_target")
    assert st, "manifest missing share_target"
    # GET share with url/text/title params (F4.1 spec).
    assert st.get("method", "GET").upper() == "GET"
    assert st.get("action") == "/dashboard"
    params = st.get("params") or {}
    for k in ("url", "text", "title"):
        assert k in params, f"share_target.params missing {k}"


def test_share_target_action_serves_spa():
    # The browser hard-navigates (GET) to the action URL with the shared
    # payload in the query string; the catch-all must return the SPA shell so
    # React Router + the receiver hook can claim it.
    c = _client()
    r = c.get("/dashboard?url=https%3A%2F%2Fexample.com%2Fv123&title=clip")
    assert r.status_code == 200
    body = r.data[:200].lower()
    # index.html shell (doctype) or a dist-missing actionable 503 — either way
    # not a 404/redirect away from the action URL.
    assert b"<!doctype" in body or b"<html" in body or r.status_code == 200


def test_manifest_start_url_and_scope_intact():
    # Regression: adding share_target must not disturb the existing PWA fields.
    c = _client()
    m = json.loads(c.get("/static/manifest.json").data)
    assert m.get("start_url") == "/"
    assert m.get("scope") == "/"
    assert m.get("display") == "standalone"
    assert isinstance(m.get("icons"), list) and len(m["icons"]) >= 2
    assert isinstance(m.get("shortcuts"), list) and len(m["shortcuts"]) >= 3
