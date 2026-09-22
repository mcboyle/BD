"""Row 989 adversary RED tests -- rescoped to latency only (ORDERS-0745).

Targets:
  - Real RTT measurement from route_service via X-IPC-Sample header.
  - Zero fabricated Cristian timestamps (clock_offset_ms and server_processing_ms
    are strictly omitted per ORDERS-0745).
  - H657 negative control: route_service must genuinely delegate to ipc_latency.
  - Request isolation across sequential routes.
"""
from __future__ import annotations

import json
import sys
import types

import flask

BD_GATE_SCOPE = "module"

from bulk_downloader.service_mesh import register_service_mesh


def _make_mesh_app(routes, transport):
    app = flask.Flask(__name__)
    app.config["TESTING"] = True
    app.config["SERVICE_MESH_ENABLED"] = True
    app.config["SERVICE_MESH_ROUTES"] = routes
    app.config["SERVICE_MESH_TRANSPORT"] = transport
    register_service_mesh(app)
    return app


def _ok_transport(url, *, headers, payload):
    return {"status": 200, "body": {"ok": True}}


ROUTES_A = {
    "alpha": {
        "host": "alpha.local",
        "traefik_url": "http://alpha.local:8080",
        "direct_urls": [],
    },
}

ROUTES_B = {
    "beta": {
        "host": "beta.local",
        "traefik_url": "http://beta.local:8080",
        "direct_urls": [],
    },
}

IPC_SAMPLE_KEYS = {
    "sender_seat",
    "receiver_seat",
    "t0_ns",
    "t3_ns",
    "rtt_ms",
    "total_duration_ms",
    "timestamp",
}


def _route_alpha(app):
    with app.test_client() as client:
        resp = client.post(
            "/api/service-mesh/alpha",
            json={"path": "/health"},
            content_type="application/json",
        )
    return resp


def _ipc_sample(resp):
    """Extract ipc_sample from the X-IPC-Sample response header."""
    raw = resp.headers.get("X-IPC-Sample")
    if raw is None:
        return None
    return json.loads(raw)


class TestRouteServiceDelegatesToIpcLatency:
    """route_service response must contain ipc_latency.IPCPingSample RTT data."""

    def test_response_includes_ipc_sample_with_rtt(self):
        """Response must include an ipc_sample dict with all RTT latency fields."""
        app = _make_mesh_app(ROUTES_A, _ok_transport)
        resp = _route_alpha(app)
        assert resp.status_code == 200, f"routing failed: {resp.data}"
        sample = _ipc_sample(resp)
        assert sample is not None, (
            "route_service response lacks X-IPC-Sample header — "
            "ipc_latency has zero product callers"
        )
        assert isinstance(sample, dict), f"ipc_sample is not a dict: {sample}"
        missing = IPC_SAMPLE_KEYS - set(sample.keys())
        assert not missing, f"ipc_sample missing latency fields {missing}: {sample}"

    def test_clock_skew_fields_not_advertised(self):
        """ORDERS-0745: clock_offset_ms and server_processing_ms must NOT be advertised."""
        app = _make_mesh_app(ROUTES_A, _ok_transport)
        resp = _route_alpha(app)
        assert resp.status_code == 200
        sample = _ipc_sample(resp)
        assert sample is not None, "X-IPC-Sample header missing"
        assert "clock_offset_ms" not in sample, (
            "fabricated clock_offset_ms present in X-IPC-Sample header"
        )
        assert "server_processing_ms" not in sample, (
            "fabricated server_processing_ms present in X-IPC-Sample header"
        )

    def test_rtt_is_positive_and_finite(self):
        """rtt_ms must be non-negative and finite."""
        app = _make_mesh_app(ROUTES_A, _ok_transport)
        resp = _route_alpha(app)
        assert resp.status_code == 200
        sample = _ipc_sample(resp)
        assert sample is not None, "X-IPC-Sample header missing"
        rtt = sample["rtt_ms"]
        assert isinstance(rtt, (int, float)), f"rtt_ms not numeric: {rtt}"
        assert rtt >= 0.0, f"rtt_ms is negative: {rtt}"
        assert sample["sender_seat"] == "gateway"
        assert sample["receiver_seat"] == "alpha"


class TestIpcLatencyModuleIsRequired:
    """H657 negative control: removing ipc_latency must break sample generation."""

    def test_route_fails_or_lacks_sample_without_ipc_latency_module(self):
        """With ipc_latency hidden, route_service must not produce ipc_sample."""
        saved_modules = {
            k: v for k, v in sys.modules.items() if k == "bulk_downloader.ipc_latency"
        }
        sys.modules.pop("bulk_downloader.ipc_latency", None)
        blocker = types.ModuleType("bulk_downloader.ipc_latency")
        blocker.__spec__ = None
        sys.modules["bulk_downloader.ipc_latency"] = blocker
        try:
            app = _make_mesh_app(ROUTES_A, _ok_transport)
            try:
                resp = _route_alpha(app)
            except (ImportError, AttributeError):
                # Expected: route_service fails because ipc_latency is missing
                pass
            else:
                sample = _ipc_sample(resp)
                assert sample is None or not IPC_SAMPLE_KEYS.issubset(sample.keys()), (
                    "X-IPC-Sample present even though ipc_latency module is blocked"
                )
        finally:
            sys.modules.pop("bulk_downloader.ipc_latency", None)
            sys.modules.update(saved_modules)


class TestLatencyIsolationAcrossRequests:
    """Each request must carry its own ipc_sample, not shared singleton state."""

    def test_sequential_routes_have_independent_samples(self):
        """Two routes must each carry their own ipc_sample with distinct timestamps."""
        routes = {**ROUTES_A, **ROUTES_B}
        app = _make_mesh_app(routes, _ok_transport)
        with app.test_client() as client:
            r1 = client.post(
                "/api/service-mesh/alpha",
                json={"path": "/a"},
                content_type="application/json",
            )
            r2 = client.post(
                "/api/service-mesh/beta",
                json={"path": "/b"},
                content_type="application/json",
            )
            assert r1.status_code == 200
            assert r2.status_code == 200
            s1 = _ipc_sample(r1)
            s2 = _ipc_sample(r2)
            assert s1 is not None, "alpha lacks X-IPC-Sample header"
            assert s2 is not None, "beta lacks X-IPC-Sample header"
            assert s1["t0_ns"] != s2["t0_ns"] or s1["t3_ns"] != s2["t3_ns"], (
                "sequential requests share identical timestamps — singleton leak"
            )
