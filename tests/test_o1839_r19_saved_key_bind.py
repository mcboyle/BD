"""O1839 R19: /api/ai/models must not send the SAVED API key to a
different provider/endpoint identity. A blank, absent or "<configured>"
draft key may only fall back to the saved key when the draft provider and
endpoint are the saved ones; any other identity needs an explicit key.

Dummy keys only; ai_provider.urlopen is stubbed, so nothing leaves the
process."""
import io
import json

import pytest
from flask import Flask

from bulk_downloader import ai_provider, aiassist
from bulk_downloader.app_ai import ai_bp

BD_GATE_SCOPE = "module"

SAVED = "https://api.openai.com"
OTHER = "https://attacker.example"
KEY = "DUMMY-ZERO-ENTROPY-R19-SAVED-KEY"
DIAG = "O1839-R19-SAVED-KEY-CROSS-IDENTITY"


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setattr(aiassist, "_config", {
        "provider": "openai", "endpoint": SAVED, "api_key": KEY})
    seen = []

    class Response(io.BytesIO):
        status = 200

    def stub(req, *a, **kw):
        seen.append((req.full_url, dict(req.header_items())))
        return Response(json.dumps(
            {"data": [{"id": "gpt-r19"}],
             "models": [{"name": "models/gemini-r19"}]}).encode())

    monkeypatch.setattr(ai_provider, "urlopen", stub)
    app = Flask("o1839-r19")
    app.register_blueprint(ai_bp)
    return app.test_client(), seen


def _carries_saved_key(seen):
    return [s for s in seen
            if KEY in s[0] or any(KEY in str(v) for v in s[1].values())]


def test_saved_config_positive_control(harness):
    client, seen = harness
    r = client.post("/api/ai/models", json={})
    assert r.status_code == 200 and r.json["ok"]
    assert [u for u, _ in seen] == [SAVED + "/v1/models"]
    assert seen[0][1].get("Authorization") == "Bearer " + KEY


@pytest.mark.parametrize("key", ["", "<configured>", None])
@pytest.mark.parametrize("endpoint", [SAVED, SAVED + "/"])
def test_same_identity_keeps_saved_key(harness, key, endpoint):
    client, seen = harness
    body = {"provider": "openai", "endpoint": endpoint}
    if key is not None:
        body["api_key"] = key
    r = client.post("/api/ai/models", json=body)
    assert r.status_code == 200 and r.json["ok"]
    assert len(_carries_saved_key(seen)) == 1


@pytest.mark.parametrize("key", ["", "<configured>", None])
def test_different_endpoint_must_not_inherit_saved_key(harness, key):
    client, seen = harness
    body = {"provider": "openai", "endpoint": OTHER}
    if key is not None:
        body["api_key"] = key
    r = client.post("/api/ai/models", json=body)
    assert r.status_code == 200
    assert _carries_saved_key(seen) == [], DIAG + " " + repr(seen)


@pytest.mark.parametrize("key", ["", "<configured>", None])
def test_different_provider_must_not_inherit_saved_key(harness, key):
    client, seen = harness
    body = {"provider": "gemini"}
    if key is not None:
        body["api_key"] = key
    r = client.post("/api/ai/models", json=body)
    assert r.status_code == 200
    assert _carries_saved_key(seen) == [], DIAG + " " + repr(seen)


def test_direct_call_different_endpoint_not_inherit(harness):
    _client, seen = harness
    aiassist.list_available_models(provider="openai", endpoint=OTHER)
    assert _carries_saved_key(seen) == [], DIAG + " " + repr(seen)


def test_explicit_dummy_key_positive_control(harness):
    client, seen = harness
    r = client.post("/api/ai/models", json={
        "provider": "openai", "endpoint": OTHER,
        "api_key": "DUMMY-EXPLICIT-KEY"})
    assert r.status_code == 200 and r.json["ok"]
    assert [u for u, _ in seen] == [OTHER + "/v1/models"]
    assert seen[0][1].get("Authorization") == "Bearer DUMMY-EXPLICIT-KEY"


def test_invalid_endpoint_negative_control(harness):
    client, seen = harness
    r = client.post("/api/ai/models", json={
        "provider": "openai", "endpoint": "not-a-url"})
    assert r.status_code == 200 and r.json["ok"] is False
    assert seen == []
