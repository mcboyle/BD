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
  * all three named scopes are mintable and their exact cumulative permitted
    action sets contain 8, 10, and 16 route/method pairs respectively,
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


# Literal, independently reviewed authorization denominator.  The live gate
# consumes app._API_TOKEN_ROUTE_POLICY; this expectation must not be built from
# that object or a missing policy entry would disappear from both sides.
_EXPECTED_ACTIONS_BY_SCOPE = {
    "read": {
        ("GET", "/api/capacity"),
        ("HEAD", "/api/capacity"),
        ("GET", "/api/dashboard"),
        ("HEAD", "/api/dashboard"),
        ("GET", "/api/queue/v2"),
        ("HEAD", "/api/queue/v2"),
        ("GET", "/api/history"),
        ("HEAD", "/api/history"),
        ("GET", "/api/discovery/scenes/status"),
        ("HEAD", "/api/discovery/scenes/status"),
    },
    "enqueue": {
        ("GET", "/api/capacity"),
        ("HEAD", "/api/capacity"),
        ("GET", "/api/dashboard"),
        ("HEAD", "/api/dashboard"),
        ("GET", "/api/queue/v2"),
        ("HEAD", "/api/queue/v2"),
        ("GET", "/api/history"),
        ("HEAD", "/api/history"),
        ("GET", "/api/discovery/scenes/status"),
        ("HEAD", "/api/discovery/scenes/status"),
        ("POST", "/api/queue/v2/add_url"),
        ("POST", "/api/sites/<sid>/queue_url"),
        ("POST", "/api/discovery/scenes/start"),
    },
    "admin": {
        ("GET", "/api/capacity"),
        ("HEAD", "/api/capacity"),
        ("GET", "/api/dashboard"),
        ("HEAD", "/api/dashboard"),
        ("GET", "/api/queue/v2"),
        ("HEAD", "/api/queue/v2"),
        ("GET", "/api/history"),
        ("HEAD", "/api/history"),
        ("GET", "/api/discovery/scenes/status"),
        ("HEAD", "/api/discovery/scenes/status"),
        ("POST", "/api/queue/v2/add_url"),
        ("POST", "/api/sites/<sid>/queue_url"),
        ("POST", "/api/discovery/scenes/start"),
        ("GET", "/api/retention/preview/<sid>"),
        ("HEAD", "/api/retention/preview/<sid>"),
        ("POST", "/api/retention/apply"),
        ("GET", "/api/api_tokens"),
        ("POST", "/api/api_tokens"),
        ("DELETE", "/api/api_tokens/<token_id>"),
    },
}


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
    res = t.create_token(scope="read")
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


def test_admin_token_allowed_on_destructive_and_management_routes():
    """Admin is a real API-token capability, while enqueue stays below it."""
    from bulk_downloader import app as a, api_tokens as t
    os.environ["BD_AUTH_TOKEN"] = _MASTER
    try:
        admin_result = t.create_token(scope="admin")
        assert admin_result["ok"] is True, admin_result
        assert t.verify_token(admin_result["token"])["scope"] == "admin"
        enqueue_token = _mint("enqueue")
        c = a.app.test_client()
        denied = c.get("/api/api_tokens", headers=_hdr(enqueue_token))
        assert denied.status_code == 403
        assert denied.get_json() == {
            "error": "insufficient token scope",
            "required_scope": "admin",
        }
        allowed = c.get("/api/api_tokens",
                        headers=_hdr(admin_result["token"]))
        assert allowed.status_code == 200
        assert allowed.get_json()["ok"] is True
    finally:
        os.environ.pop("BD_AUTH_TOKEN", None)


def test_each_scope_has_the_exact_permitted_action_set(monkeypatch):
    """Exercise every declared route/method through the real auth hook.

    Handlers are replaced only after their Flask rules are proven present, so
    the authorization hook and route matching stay real while domain writes are
    kept out of this boundary test.
    """
    from bulk_downloader import app as a, api_tokens as t

    assert t.SCOPES == {"read": 1, "enqueue": 2, "admin": 3}
    assert len(a._API_TOKEN_ROUTE_POLICY) == 12
    assert sum(len(methods) for methods, _ in
               a._API_TOKEN_ROUTE_POLICY.values()) == 19

    actual_actions = {}
    for scope in ("read", "enqueue", "admin"):
        actual_actions[scope] = {
            (method, rule)
            for rule, (methods, required_scope)
            in a._API_TOKEN_ROUTE_POLICY.items()
            if t.scope_level(scope) >= t.scope_level(required_scope)
            for method in methods
        }
    assert actual_actions == _EXPECTED_ACTIONS_BY_SCOPE
    assert {scope: len(actions) for scope, actions in actual_actions.items()} == {
        "read": 10,
        "enqueue": 13,
        "admin": 19,
    }

    registered_actions = {
        (method, rule.rule)
        for rule in a.app.url_map.iter_rules()
        if rule.rule in {path for _, path in
                         _EXPECTED_ACTIONS_BY_SCOPE["admin"]}
        for method in rule.methods
        if (method, rule.rule) in _EXPECTED_ACTIONS_BY_SCOPE["admin"]
    }
    assert len(registered_actions) == 19
    assert registered_actions == _EXPECTED_ACTIONS_BY_SCOPE["admin"]

    minted = {}
    for scope in ("read", "enqueue", "admin"):
        result = t.create_token(scope=scope, label=f"matrix-{scope}")
        assert result["ok"] is True, (
            f"{scope} issuance failed: {result.get('error', '<no error>')}"
        )
        assert result["scope"] == scope
        assert result["token"].startswith("bdapi_")
        verified = t.verify_token(result["token"])
        assert verified["ok"] is True
        assert verified["scope"] == scope
        assert verified["scope_level"] == t.SCOPES[scope]
        minted[scope] = result["token"]
    assert t.MINTABLE_SCOPES == {"read", "enqueue", "admin"}
    assert len({t.verify_token(token)["token_id"]
                for token in minted.values()}) == 3

    policy_paths = {path for _, path in
                    _EXPECTED_ACTIONS_BY_SCOPE["admin"]}
    policy_endpoints = {
        rule.endpoint
        for rule in a.app.url_map.iter_rules()
        if rule.rule in policy_paths
    }
    handler_calls = []

    def reached_handler(**_values):
        handler_calls.append((a.request.method, a.request.url_rule.rule))
        return "", 204

    for endpoint in policy_endpoints:
        monkeypatch.setitem(a.app.view_functions, endpoint, reached_handler)

    monkeypatch.setenv("BD_AUTH_TOKEN", _MASTER)
    client = a.app.test_client()
    all_actions = _EXPECTED_ACTIONS_BY_SCOPE["admin"]
    expected_counts = {
        "read": {"allowed": 10, "denied": 9},
        "enqueue": {"allowed": 13, "denied": 6},
        "admin": {"allowed": 19, "denied": 0},
    }
    for scope in ("read", "enqueue", "admin"):
        handler_calls.clear()
        allowed_statuses = []
        denied_results = []
        for method, rule in sorted(all_actions):
            path = (rule.replace("<sid>", "matrix-site")
                    .replace("<token_id>", "matrix-token"))
            response = client.open(
                path,
                method=method,
                headers=_hdr(minted[scope]),
                json={},
            )
            action = (method, rule)
            if action in _EXPECTED_ACTIONS_BY_SCOPE[scope]:
                allowed_statuses.append(response.status_code)
            else:
                required_scope = (
                    "enqueue"
                    if action in _EXPECTED_ACTIONS_BY_SCOPE["enqueue"]
                    else "admin"
                )
                denied_results.append((
                    action,
                    response.status_code,
                    response.get_json(silent=True),
                    response.headers.get("X-BD-Required-Scope"),
                    required_scope,
                ))
        counts = expected_counts[scope]
        assert len(allowed_statuses) == counts["allowed"]
        assert allowed_statuses == [204] * counts["allowed"]
        assert len(denied_results) == counts["denied"]
        assert denied_results == [
            (
                action,
                403,
                (None if action[0] == "HEAD" else {
                    "error": "insufficient token scope",
                    "required_scope": required_scope,
                }),
                required_scope,
                required_scope,
            )
            for action, _, _, _, required_scope in denied_results
        ]
        assert len(handler_calls) == counts["allowed"]
        assert set(handler_calls) == _EXPECTED_ACTIONS_BY_SCOPE[scope]


def test_lesser_scope_refusal_names_admin_and_never_calls_handler(monkeypatch):
    """Negative control: a valid enqueue token reaches the scope decision,
    receives the distinctive missing-scope diagnostic, and stops there."""
    from bulk_downloader import app as a, api_tokens as t

    assert t.SCOPES == {"read": 1, "enqueue": 2, "admin": 3}
    assert a._API_TOKEN_ROUTE_POLICY["/api/api_tokens"] == (
        {"GET", "POST"}, "admin")
    post_rules = [
        rule for rule in a.app.url_map.iter_rules()
        if rule.rule == "/api/api_tokens" and "POST" in rule.methods
    ]
    assert len(post_rules) == 1

    enqueue_result = t.create_token(scope="enqueue", label="negative-control")
    assert enqueue_result["ok"] is True, enqueue_result
    verified = t.verify_token(enqueue_result["token"])
    assert verified["ok"] is True
    assert verified["scope"] == "enqueue"
    assert verified["scope_level"] == 2

    handler_calls = []

    def reached_handler():
        handler_calls.append("called")
        return "", 204

    monkeypatch.setitem(
        a.app.view_functions, post_rules[0].endpoint, reached_handler)
    monkeypatch.setenv("BD_AUTH_TOKEN", _MASTER)
    response = a.app.test_client().post(
        "/api/api_tokens",
        headers=_hdr(enqueue_result["token"]),
        json={"scope": "read"},
    )
    assert response.status_code == 403
    assert response.get_json() == {
        "error": "insufficient token scope",
        "required_scope": "admin",
    }
    assert response.headers.get("X-BD-Required-Scope") == "admin"
    assert len(handler_calls) == 0


def test_admin_token_can_issue_exactly_one_child_token(monkeypatch):
    from bulk_downloader import app as a, api_tokens as t

    assert t.SCOPES == {"read": 1, "enqueue": 2, "admin": 3}
    assert a._API_TOKEN_ROUTE_POLICY["/api/api_tokens"] == (
        {"GET", "POST"}, "admin")
    post_rules = [
        rule for rule in a.app.url_map.iter_rules()
        if rule.rule == "/api/api_tokens" and "POST" in rule.methods
    ]
    assert len(post_rules) == 1

    admin_result = t.create_token(scope="admin", label="issuer")
    assert admin_result["ok"] is True, (
        "admin issuance precondition failed: "
        f"{admin_result.get('error', '<no error>')}"
    )
    verified_admin = t.verify_token(admin_result["token"])
    assert verified_admin["ok"] is True
    assert verified_admin["scope"] == "admin"
    assert verified_admin["scope_level"] == 3
    assert len(t.list_tokens()) == 1

    monkeypatch.setenv("BD_AUTH_TOKEN", _MASTER)
    response = a.app.test_client().post(
        "/api/api_tokens",
        headers=_hdr(admin_result["token"]),
        json={"scope": "read", "label": "issued-by-admin"},
    )
    assert response.status_code == 200
    child = response.get_json()
    assert child["ok"] is True
    assert child["scope"] == "read"
    assert child["token"].startswith("bdapi_")
    assert t.verify_token(child["token"])["scope"] == "read"
    rows = t.list_tokens()
    assert len(rows) == 2
    assert len([row for row in rows
                if row["token_id"] == child["token_id"]]) == 1


def test_valid_token_on_unlisted_route_is_403():
    """A route absent from the policy is unreachable by every scope,
    including admin, until deliberately added (fail-closed)."""
    from bulk_downloader import app as a
    os.environ["BD_AUTH_TOKEN"] = _MASTER
    try:
        tok = _mint("admin")
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
        res = t.create_token(scope="read")
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
