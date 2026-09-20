"""Row 841: service-mesh routing remains opt-in and deterministic."""

from __future__ import annotations

import importlib
import importlib.util
import sys

from flask import Flask


BD_GATE_SCOPE = "module"


def _service_mesh():
    spec = importlib.util.find_spec("bulk_downloader.service_mesh")
    assert spec is not None, "service mesh blueprint is unavailable"
    return importlib.import_module("bulk_downloader.service_mesh")


def _app(enabled: bool, transport):
    app = Flask(__name__)
    app.config.update(
        SERVICE_MESH_ENABLED=enabled,
        SERVICE_MESH_TRANSPORT=transport,
        SERVICE_MESH_ROUTES={
            "embeddings": {
                "host": "embeddings.localhost",
                "traefik_url": "http://127.0.0.1:80",
                "direct_urls": ["http://10.0.70.21:9000", "http://10.0.70.22:9000"],
            }
        },
    )
    register = getattr(_service_mesh(), "register_service_mesh", None)
    assert callable(register), "service mesh registration is not uniquely named"
    register(app)
    return app


def test_gateway_is_absent_when_option_is_off():
    app = _app(False, lambda *_args: (_ for _ in ()).throw(AssertionError("transport called")))

    response = app.test_client().post("/api/service-mesh/embeddings", json={"path": "/embed"})

    assert response.status_code == 404


def test_gateway_sends_traefik_host_header_to_configured_port():
    calls = []

    def transport(url, *, headers, payload):
        calls.append((url, headers, payload))
        return {"status": 200, "body": {"node": "primary"}}

    app = _app(True, transport)

    response = app.test_client().post("/api/service-mesh/embeddings", json={"path": "/embed", "value": "x"})

    assert response.status_code == 200
    assert calls == [
        ("http://127.0.0.1:80/embed", {"Host": "embeddings.localhost"}, {"value": "x"})
    ]
    assert response.get_json()["route"] == "traefik"


def test_gateway_uses_second_direct_node_when_primary_is_unhealthy():
    calls = []

    def transport(url, *, headers, payload):
        calls.append((url, headers, payload))
        if url == "http://127.0.0.1:80/embed":
            raise OSError("traefik unavailable")
        if url == "http://10.0.70.21:9000/embed":
            raise OSError("primary unhealthy")
        return {"status": 200, "body": {"node": "secondary"}}

    app = _app(True, transport)

    response = app.test_client().post("/api/service-mesh/embeddings", json={"path": "/embed"})

    assert response.status_code == 200
    assert [call[0] for call in calls] == [
        "http://127.0.0.1:80/embed",
        "http://10.0.70.21:9000/embed",
        "http://10.0.70.22:9000/embed",
    ]
    assert response.get_json()["route"] == "direct:1"


def test_app_registers_gateway_only_when_its_option_is_enabled(monkeypatch):
    monkeypatch.setenv("SERVICE_MESH_ENABLED", "1")
    monkeypatch.delitem(sys.modules, "bulk_downloader.app", raising=False)
    app_module = importlib.import_module("bulk_downloader.app")

    assert "/api/service-mesh/<service>" in {rule.rule for rule in app_module.app.url_map.iter_rules()}


def test_default_transport_sends_json_and_decodes_response(monkeypatch):
    observed = {}

    class Response:
        status = 201

        def read(self):
            return b'{"node":"primary"}'

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(outbound, *, timeout):
        observed["url"] = outbound.full_url
        observed["host"] = outbound.get_header("Host")
        observed["payload"] = outbound.data
        observed["timeout"] = timeout
        return Response()

    service_mesh = _service_mesh()
    monkeypatch.setattr(service_mesh, "urlopen", fake_urlopen)

    result = service_mesh._default_transport(
        "http://127.0.0.1:80/embed", headers={"Host": "embeddings.localhost"}, payload={"value": "x"}
    )

    assert result == {"status": 201, "body": {"node": "primary"}}
    assert observed == {
        "url": "http://127.0.0.1:80/embed",
        "host": "embeddings.localhost",
        "payload": b'{"value": "x"}',
        "timeout": 2,
    }


# ── correctness REFUTE E1/E2 (2026-09-20) ──

def test_a_healthy_backends_own_error_answer_is_passed_through_not_failed_over():
    """E1: a 404/422 from the primary is the SERVICE's answer; it must reach the client from that
    route, not be replayed to every node and turned into 503. Only 502/503/504 fail over."""
    from urllib.error import HTTPError
    import io
    calls = []

    def transport(url, *, headers, payload):
        calls.append(url)
        if url.startswith("http://127.0.0.1:80"):
            raise OSError("traefik unavailable")
        raise HTTPError(url, 422, "Unprocessable", {}, io.BytesIO(b'{"detail": "bad embed"}'))

    app = _app(True, transport)
    response = app.test_client().post("/api/service-mesh/embeddings", json={"path": "/embed"})
    assert response.status_code == 422, response.get_json()
    assert response.get_json() == {"route": "direct:0", "body": {"detail": "bad embed"}}
    assert calls == ["http://127.0.0.1:80/embed", "http://10.0.70.21:9000/embed"]  # no replay past the answer

    # control: a 503 from the primary IS a dead route and the secondary is used
    calls.clear()

    def gateway_errors(url, *, headers, payload):
        calls.append(url)
        if url.startswith("http://10.0.70.22"):
            return {"status": 200, "body": {"node": "secondary"}}
        raise HTTPError(url, 503, "Service Unavailable", {}, io.BytesIO(b""))

    response = _app(True, gateway_errors).test_client().post("/api/service-mesh/embeddings", json={"path": "/embed"})
    assert response.status_code == 200 and response.get_json()["route"] == "direct:1"
    assert len(calls) == 3

    # control 2 (skeptic M6): a transport that RETURNS a 502/503/504 dict (as _default_transport never
    # does, but a custom one may) is the same dead route -- the gateway must not hand that 503 to the
    # client but try direct:0
    for gateway_status in (502, 503, 504):
        calls.clear()

        def returns_gateway_status(url, *, headers, payload, _status=gateway_status):
            calls.append(url)
            if url.startswith("http://127.0.0.1:80"):
                return {"status": _status, "body": ""}
            return {"status": 200, "body": {"node": "primary"}}

        response = _app(True, returns_gateway_status).test_client().post(
            "/api/service-mesh/embeddings", json={"path": "/embed"})
        assert response.status_code == 200, (gateway_status, response.status_code, response.get_json())
        assert response.get_json() == {"route": "direct:0", "body": {"node": "primary"}}
        assert calls == ["http://127.0.0.1:80/embed", "http://10.0.70.21:9000/embed"], calls


def test_default_transport_returns_the_services_error_status_and_body(monkeypatch):
    """E1 at the real transport: urlopen raising HTTPError 404 -> {"status": 404, body}; 502 -> RouteUnavailable (an OSError)."""
    from urllib.error import HTTPError
    import io
    import pytest
    mesh = _service_mesh()

    def raise_404(req, timeout):
        raise HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b'{"error": "no such thing"}'))
    monkeypatch.setattr(mesh, "urlopen", raise_404)
    assert mesh._default_transport("http://10.0.70.21:9000/x", headers={}, payload={}) == {"status": 404, "body": {"error": "no such thing"}}

    def raise_502(req, timeout):
        raise HTTPError(req.full_url, 502, "Bad Gateway", {}, io.BytesIO(b""))
    monkeypatch.setattr(mesh, "urlopen", raise_502)
    with pytest.raises(OSError):
        mesh._default_transport("http://10.0.70.21:9000/x", headers={}, payload={})


def test_paths_with_spaces_or_control_characters_are_rejected_with_400():
    """E2: an invalid path never reaches urlopen (InvalidURL is not an OSError; it surfaced as 500)."""
    calls = []

    def transport(url, *, headers, payload):
        calls.append(url)
        return {"status": 200, "body": {}}

    client = _app(True, transport).test_client()
    rejected = ("/em bed", "/embed\r\nHost: evil", "/embed\x00", "/embed\t", "//embed", "embed",
                "/\u00eb", "/embed?q=1", "/embed#frag", "/em<bed>", "/embed\\x")
    for bad in rejected:
        response = client.post("/api/service-mesh/embeddings", json={"path": bad})
        assert response.status_code == 400, (bad, response.status_code)
        assert response.get_json() == {"error": "unknown service or invalid path"}
    assert calls == []
    # control: every RFC 3986 pchar class is accepted and reaches the transport verbatim
    good = "/api/v1/embed.x_y~z:w@%20!$&'()*+,;=-"
    assert client.post("/api/service-mesh/embeddings", json={"path": good}).status_code == 200
    assert calls == [f"http://127.0.0.1:80{good}"]


def test_a_custom_transports_httperror_without_a_body_stream_still_passes_through():
    """A transport re-raising urllib's HTTPError with fp=None (no .read()) must not become a 500.

    HTTPError(url, code, msg, hdrs, None) silently attaches an empty BytesIO on this Python (skeptic
    M7), so the stream is removed AFTER construction and the fixture proves it is really gone."""
    from urllib.error import HTTPError

    def transport(url, *, headers, payload):
        err = HTTPError(url, 409, "Conflict", {}, None)
        err.fp = None  # what a transport that consumed/closed the stream leaves behind
        raise err

    # shape: the error object this test raises really has no .fp stream (the attribute _error_body
    # reads); a fresh HTTPError(..., None) would have one, which is why fp is cleared afterwards
    fresh = HTTPError("http://10.0.70.21:9000/embed", 409, "Conflict", {}, None)
    assert fresh.fp is not None, "this Python no longer attaches a stream: fixture rationale changed"
    fresh.fp = None
    assert getattr(fresh, "fp", None) is None
    with __import__("pytest").raises(AttributeError):
        fresh.fp.read()  # the guard in _error_body is the only thing between this and a 500

    response = _app(True, transport).test_client().post("/api/service-mesh/embeddings", json={"path": "/embed"})
    assert response.status_code == 409
    assert response.get_json() == {"route": "traefik", "body": ""}

    # control: the same error WITH a stream carries its body through the same path
    import io

    def with_stream(url, *, headers, payload):
        raise HTTPError(url, 409, "Conflict", {}, io.BytesIO(b'{"detail": "taken"}'))

    response = _app(True, with_stream).test_client().post("/api/service-mesh/embeddings", json={"path": "/embed"})
    assert response.status_code == 409
    assert response.get_json() == {"route": "traefik", "body": {"detail": "taken"}}


# ── skeptic re-judgement (2026-09-20): D1 UnicodeDecodeError over-catch, M11 non-dict JSON body ──

def test_a_non_utf8_success_body_is_the_services_answer_not_an_invalid_path(monkeypatch):
    """D1: the real transport's 200 answer whose body is not UTF-8 must reach the client as 200 on
    route=traefik with U+FFFD replacement -- not be caught as a ValueError and answered 400
    'unknown service or invalid path' (the valid path blamed, the upstream answer lost)."""
    mesh = _service_mesh()
    observed = []

    class Response:
        status = 200

        def read(self):
            return b"\xff\xfe"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(outbound, *, timeout):
        observed.append(outbound.full_url)
        return Response()

    monkeypatch.setattr(mesh, "urlopen", fake_urlopen)
    # shape: the bytes really are not UTF-8, so a strict decode would raise the over-caught ValueError
    with __import__("pytest").raises(UnicodeDecodeError):
        b"\xff\xfe".decode("utf-8")

    app = _app(True, None)
    del app.config["SERVICE_MESH_TRANSPORT"]  # the runtime default transport, not a test double
    response = app.test_client().post("/api/service-mesh/embeddings", json={"path": "/embed"})

    assert observed == ["http://127.0.0.1:80/embed"], observed  # urlopen was reached with the valid path
    assert response.status_code == 200, (response.status_code, response.get_json())
    assert response.get_json() == {"route": "traefik", "body": "\ufffd\ufffd"}

    # control: a UTF-8 JSON body through the same fake decodes to the object
    Response.read = lambda self: b'{"node": "primary"}'
    response = app.test_client().post("/api/service-mesh/embeddings", json={"path": "/embed"})
    assert response.status_code == 200
    assert response.get_json() == {"route": "traefik", "body": {"node": "primary"}}


def test_non_object_request_bodies_route_to_the_root_path_with_an_empty_payload():
    """M11: a JSON list body or a text/plain body is not a mapping -- the gateway must treat it as
    {} (path "/" and an empty payload), not crash with TypeError/AttributeError -> 500."""
    calls = []

    def transport(url, *, headers, payload):
        calls.append((url, payload))
        return {"status": 200, "body": {"node": "primary"}}

    client = _app(True, transport).test_client()

    response = client.post("/api/service-mesh/embeddings", json=["/embed", 1])
    assert response.status_code == 200, (response.status_code, response.get_json())
    assert response.get_json() == {"route": "traefik", "body": {"node": "primary"}}
    assert calls == [("http://127.0.0.1:80/", {})], calls

    calls.clear()
    response = client.post("/api/service-mesh/embeddings", data="path=/embed", content_type="text/plain")
    assert response.status_code == 200, (response.status_code, response.get_json())
    assert calls == [("http://127.0.0.1:80/", {})], calls

    # control: a JSON object body still selects its path and forwards the remaining keys as the payload
    calls.clear()
    response = client.post("/api/service-mesh/embeddings", json={"path": "/embed", "value": "x"})
    assert response.status_code == 200
    assert calls == [("http://127.0.0.1:80/embed", {"value": "x"})], calls


def test_a_transport_coding_bug_surfaces_as_500_and_is_never_failed_over():
    """Shape lens (row841-local REFUTE, mutant 3): widening the failover catch from `except OSError`
    to `except Exception` escaped the suite. The boundary: a non-OSError from a transport is the
    transport's own bug and must surface (500, one call), not be swallowed as "route is down"."""
    calls = []

    def transport(url, *, headers, payload):
        calls.append(url)
        raise TypeError("transport bug")

    app = _app(True, transport)
    app.config["PROPAGATE_EXCEPTIONS"] = False  # the gateway's own answer, not a re-raise into pytest

    response = app.test_client().post("/api/service-mesh/embeddings", json={"path": "/embed"})

    assert response.status_code == 500, (response.status_code, response.get_data(as_text=True))
    assert calls == ["http://127.0.0.1:80/embed"], calls  # exactly one route tried: no failover

    # control: the same shape raising an OSError IS failed over (the catch still does its job)
    calls.clear()

    def down_then_up(url, *, headers, payload):
        calls.append(url)
        if len(calls) == 1:
            raise OSError("route down")
        return {"status": 200, "body": {"node": "next"}}

    response = _app(True, down_then_up).test_client().post("/api/service-mesh/embeddings", json={"path": "/embed"})
    assert response.status_code == 200
    assert len(calls) == 2, calls
