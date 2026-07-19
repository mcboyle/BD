"""Cut 7 (Track A / Form-batch-2) — two NEW read-only endpoints.

GET /api/integrations/health
    Read-only, FAIL-OPEN, SANITIZED rollup of integration health (AI assist +
    configured-booleans for media integrations). Always returns 200 with
    ok:true; a failing sub-probe is reported as that integration being unhealthy
    rather than failing the whole call. Never leaks endpoints, tokens, or keys.

GET /api/secrets/usage
    Read-only. Reports which stored secret keys exist and which sites reference
    them — by NAME / reference only, NEVER any secret value.

RED on pristine 376: neither route exists (404).
"""
import json
import re


def _client():
    from bulk_downloader import app as A
    return A.app.test_client()


# value-shaped leakage we must never see in a sanitized response
_SECRET_VALUE_HINTS = ("token", "api_key", "password", "secret_value", "bearer")


def test_integrations_health_is_200_and_ok():
    r = _client().get("/api/integrations/health")
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d["ok"] is True
    assert isinstance(d["integrations"], dict)


def test_integrations_health_reports_ai_subsystem():
    d = _client().get("/api/integrations/health").get_json()
    ai = d["integrations"].get("ai")
    assert isinstance(ai, dict)
    assert "ok" in ai  # a boolean health flag


def test_integrations_health_is_sanitized():
    # No raw endpoint URLs / tokens / keys in the serialized body.
    raw = _client().get("/api/integrations/health").data.decode().lower()
    assert "http://" not in raw and "https://" not in raw
    assert "api_key" not in raw and "token" not in raw and "bearer" not in raw


def test_secrets_usage_is_200_and_ok():
    r = _client().get("/api/secrets/usage")
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d["ok"] is True
    # references by key name; usage maps key -> [site ids]
    assert isinstance(d["stored_keys"], list)
    assert isinstance(d["usage"], dict)


def test_secrets_usage_exposes_no_values():
    d = _client().get("/api/secrets/usage").get_json()
    body = json.dumps(d).lower()
    # the response advertises refs, not values: no value-bearing field names
    assert "secret_value" not in body and "plaintext_value" not in body
    # stored_keys are plain strings (names), never {key,value} objects
    for k in d["stored_keys"]:
        assert isinstance(k, str)
