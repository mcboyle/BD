"""F4.3 — scoped programmatic API tokens (v3.66.227).

Two layers:

  1. The ``api_tokens`` module primitives (mint / verify / revoke / list)
     and the scope taxonomy — exercised directly, deterministic, no app.

  2. The FAIL-CLOSED enforcement in ``app._check_token`` against
     ``_API_TOKEN_ROUTE_POLICY`` — exercised through the Flask test client
     with global auth enabled (BD_AUTH_TOKEN), restored in ``finally``.

The security contract being pinned:
  * a valid token reaches ONLY (route, method) pairs in the policy for its
    scope level; anything else → 403 (authenticated-but-not-authorized),
  * read < enqueue < admin (a lower scope is 403 on a higher-scope route),
  * an invalid / revoked / expired token never grants and falls through to
    401 (so it cannot downgrade a request carrying other valid auth),
  * the OPERATOR path (master bearer / session) is unaffected — it is
    authorized earlier and never hits the token policy gate.
"""
from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.bd_module_wipe

_MASTER = "test-master-bearer-227"


# ── layer 1: module primitives (no app) ───────────────────────────────

def test_scope_hierarchy_ordering():
    from bulk_downloader import api_tokens as t
    assert t.scope_level("read") < t.scope_level("enqueue")
    assert t.scope_level("enqueue") < t.scope_level("admin")
    assert t.scope_level("nonsense") == 0  # deny-by-default


def test_create_rejects_bad_scope():
    from bulk_downloader import api_tokens as t
    res = t.create_token(scope="superuser")
    assert res["ok"] is False
    assert "scope" in res["error"]


def test_create_and_verify_round_trip():
    from bulk_downloader import api_tokens as t
    res = t.create_token(scope="enqueue", label="ci")
    assert res["ok"] is True
    assert res["token"].startswith("bdapi_")
    assert res["scope"] == "enqueue"
    v = t.verify_token(res["token"])
    assert v["ok"] is True
    assert v["scope"] == "enqueue"
    assert v["scope_level"] == t.scope_level("enqueue")
    assert v["token_id"] == res["token_id"]


def test_verify_rejects_non_bdapi_string():
    from bulk_downloader import api_tokens as t
    # a share-style token (no bdapi_ prefix) must NOT validate here
    assert t.verify_token("abc.def.ghi")["ok"] is False
    assert t.verify_token("")["ok"] is False


def test_verify_rejects_tampered_signature():
    from bulk_downloader import api_tokens as t
    res = t.create_token(scope="read")  # any mintable scope (DEC-2: not admin)
    tok = res["token"]
    # flip the last char of the signature
    bad = tok[:-1] + ("0" if tok[-1] != "0" else "1")
    assert t.verify_token(bad)["ok"] is False


def test_verify_rejects_revoked():
    from bulk_downloader import api_tokens as t
    res = t.create_token(scope="read")
    assert t.revoke_token(res["token_id"]) is True
    v = t.verify_token(res["token"])
    assert v["ok"] is False
    assert v["reason"] == "revoked"


def test_verify_rejects_expired():
    from bulk_downloader import api_tokens as t
    from bulk_downloader import db as _db
    res = t.create_token(scope="read")
    # force expiry into the past directly in the DB
    with _db.db_conn() as cx:
        cx.execute("UPDATE api_auth_tokens SET expires_at=? WHERE token_id=?",
                   (time.time() - 10, res["token_id"]))
    v = t.verify_token(res["token"])
    assert v["ok"] is False
    assert v["reason"] == "expired"


def test_list_returns_metadata_not_secret():
    from bulk_downloader import api_tokens as t
    res = t.create_token(scope="enqueue", label="visible")
    rows = t.list_tokens()
    me = [r for r in rows if r["token_id"] == res["token_id"]]
    assert len(me) == 1
    row = me[0]
    assert row["scope"] == "enqueue"
    assert row["label"] == "visible"
    assert row["status"] == "active"
    # the full secret token must NEVER appear in listing output
    blob = repr(rows)
    assert res["token"] not in blob
    assert "bdapi_" not in blob


# ── layer 2: fail-closed enforcement through the app ───────────────────

def _mint(scope):
    from bulk_downloader import api_tokens as t
    return t.create_token(scope=scope)["token"]


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_gate_active_requires_auth_when_configured():
    from bulk_downloader import app as a
    os.environ["BD_AUTH_TOKEN"] = _MASTER
    try:
        c = a.app.test_client()
        r = c.get("/api/queue/v2")           # in policy, but no token sent
        assert r.status_code == 401
    finally:
        os.environ.pop("BD_AUTH_TOKEN", None)


def test_operator_master_bearer_unaffected_on_admin_route():
    """The operator (master bearer) must reach an admin route directly —
    the new token policy restricts API tokens only, never the operator."""
    from bulk_downloader import app as a
    os.environ["BD_AUTH_TOKEN"] = _MASTER
    try:
        c = a.app.test_client()
        r = c.get("/api/api_tokens", headers=_hdr(_MASTER))
        assert r.status_code == 200
        assert r.get_json()["ok"] is True
    finally:
        os.environ.pop("BD_AUTH_TOKEN", None)


def test_read_token_allowed_on_read_route():
    from bulk_downloader import app as a
    os.environ["BD_AUTH_TOKEN"] = _MASTER
    try:
        tok = _mint("read")
        c = a.app.test_client()
        r = c.get("/api/queue/v2", headers=_hdr(tok))
        # gate must NOT reject; handler may return 200 (empty queue ok)
        assert r.status_code not in (401, 403)
    finally:
        os.environ.pop("BD_AUTH_TOKEN", None)


def test_read_token_denied_on_admin_route():
    from bulk_downloader import app as a
    os.environ["BD_AUTH_TOKEN"] = _MASTER
    try:
        tok = _mint("read")
        c = a.app.test_client()
        r = c.get("/api/api_tokens", headers=_hdr(tok))
        assert r.status_code == 403
        assert r.get_json().get("required_scope") == "admin"
    finally:
        os.environ.pop("BD_AUTH_TOKEN", None)


def test_enqueue_token_allowed_to_enqueue_denied_to_admin():
    from bulk_downloader import app as a
    os.environ["BD_AUTH_TOKEN"] = _MASTER
    try:
        tok = _mint("enqueue")
        c = a.app.test_client()
        # passes the gate on the enqueue route (handler may 400 on body —
        # that's fine, it is NOT a gate rejection)
        r1 = c.post("/api/queue/v2/add_url", headers=_hdr(tok), json={})
        assert r1.status_code not in (401, 403)
        # but cannot reach a destructive admin route
        r2 = c.post("/api/retention/apply", headers=_hdr(tok),
                    json={"dry_run": True})
        assert r2.status_code == 403
        assert r2.get_json().get("required_scope") == "admin"
    finally:
        os.environ.pop("BD_AUTH_TOKEN", None)


def test_admin_routes_unreachable_by_api_token_dec2():
    """DEC-2: admin is reserved/unmintable, so NO API token can reach a
    destructive/management route — only the operator (master bearer / session)
    can. The enqueue token (highest mintable scope) is 403 on both. (Replaces
    the pre-DEC-2 ``test_admin_token_allowed_on_destructive_and_mgmt``, whose
    premise — a mintable admin token — no longer exists.)"""
    from bulk_downloader import app as a, api_tokens as t
    os.environ["BD_AUTH_TOKEN"] = _MASTER
    try:
        assert t.create_token(scope="admin")["ok"] is False   # cannot mint
        tok = _mint("enqueue")
        c = a.app.test_client()
        r1 = c.post("/api/retention/apply", headers=_hdr(tok),
                    json={"dry_run": True})
        assert r1.status_code == 403
        assert r1.get_json().get("required_scope") == "admin"
        r2 = c.get("/api/api_tokens", headers=_hdr(tok))
        assert r2.status_code == 403
        assert r2.get_json().get("required_scope") == "admin"
        # operator path is unaffected — still reaches the admin route
        r3 = c.get("/api/api_tokens", headers=_hdr(_MASTER))
        assert r3.status_code == 200
        assert r3.get_json()["ok"] is True
    finally:
        os.environ.pop("BD_AUTH_TOKEN", None)


def test_valid_token_on_unlisted_route_is_403():
    """A route absent from the policy is unreachable by ANY mintable scope —
    even the highest (enqueue) — until deliberately added (fail-closed).
    (admin is reserved/unmintable per DEC-2, so enqueue is the ceiling here.)"""
    from bulk_downloader import app as a
    os.environ["BD_AUTH_TOKEN"] = _MASTER
    try:
        tok = _mint("enqueue")
        c = a.app.test_client()
        r = c.get("/api/palette/commands", headers=_hdr(tok))
        assert r.status_code == 403
        assert "not authorized for this route" in r.get_json().get("error", "")
    finally:
        os.environ.pop("BD_AUTH_TOKEN", None)


def test_revoked_token_falls_through_to_401():
    """A revoked token does not 403 (which would confirm scope); it falls
    through to the normal 401 so it can't downgrade other auth."""
    from bulk_downloader import app as a, api_tokens as t
    os.environ["BD_AUTH_TOKEN"] = _MASTER
    try:
        res = t.create_token(scope="read")  # any mintable scope (DEC-2: not admin)
        assert t.revoke_token(res["token_id"]) is True
        c = a.app.test_client()
        r = c.get("/api/api_tokens", headers=_hdr(res["token"]))
        assert r.status_code == 401
    finally:
        os.environ.pop("BD_AUTH_TOKEN", None)


def test_api_token_via_x_header_also_works():
    from bulk_downloader import app as a
    os.environ["BD_AUTH_TOKEN"] = _MASTER
    try:
        tok = _mint("read")
        c = a.app.test_client()
        r = c.get("/api/queue/v2", headers={"X-BD-API-Token": tok})
        assert r.status_code not in (401, 403)
    finally:
        os.environ.pop("BD_AUTH_TOKEN", None)


def test_mgmt_create_list_revoke_round_trip_via_operator():
    from bulk_downloader import app as a
    os.environ["BD_AUTH_TOKEN"] = _MASTER
    try:
        c = a.app.test_client()
        cr = c.post("/api/api_tokens", headers=_hdr(_MASTER),
                    json={"scope": "enqueue", "label": "round-trip"})
        assert cr.status_code == 200
        body = cr.get_json()
        assert body["ok"] is True and body["token"].startswith("bdapi_")
        tid = body["token_id"]
        # bad scope rejected
        bad = c.post("/api/api_tokens", headers=_hdr(_MASTER),
                     json={"scope": "root"})
        assert bad.status_code == 400
        # listed
        lr = c.get("/api/api_tokens", headers=_hdr(_MASTER))
        assert any(x["token_id"] == tid for x in lr.get_json()["tokens"])
        # revoked
        dr = c.delete(f"/api/api_tokens/{tid}", headers=_hdr(_MASTER))
        assert dr.status_code == 200 and dr.get_json()["ok"] is True
    finally:
        os.environ.pop("BD_AUTH_TOKEN", None)
