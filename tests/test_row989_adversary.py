"""Row 989 adversary RED tests -- bd-worker-W2-A (D2 deep lane, strengthened).

Targets c3 refute findings:
  R1 (O1223 orphan): ipc_latency.py has ZERO product callers.
  R2 (synthetic RED): all 10 prior tests fail with ImportError, not wrong answer.
  R3 (singleton leak): _DEFAULT_MONITOR is process-global without lifecycle.

Strengthened per RULING-row989-inlined-adversary.md (H657): the test MUST fail
when ipc_latency is removed or renamed.  route_service must delegate to
ipc_latency and the response must include Cristian-algorithm fields
(clock_offset_ms, network_rtt_ms) that only ipc_latency.IPCPingSample produces.

On base, route_service returns {"route": ..., "body": ...} with no IPC sample
data — a wrong answer, not a missing import.
"""
import sys
import types

import flask
import pytest

BD_GATE_SCOPE = "module"

from bulk_downloader.service_mesh import register_service_mesh, service_mesh_bp


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
    "sender_seat", "receiver_seat",
    "t0_ns", "t1_ns", "t2_ns", "t3_ns",
    "total_duration_ms", "server_processing_ms",
    "network_rtt_ms", "clock_offset_ms", "timestamp",
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
    import json as _json
    raw = resp.headers.get("X-IPC-Sample")
    if raw is None:
        return None
    return _json.loads(raw)


class TestRouteServiceDelegatesToIpcLatency:
    """R1+R2: route_service response must contain ipc_latency.IPCPingSample data.

    The response must include Cristian-algorithm fields (clock_offset_ms,
    network_rtt_ms, server_processing_ms) that only ipc_latency produces.
    A bare time.monotonic_ns() inline cannot satisfy this.
    """

    def test_response_includes_ipc_sample_with_cristian_fields(self):
        """Response must include an ipc_sample dict with all IPCPingSample fields."""
        app = _make_mesh_app(ROUTES_A, _ok_transport)
        resp = _route_alpha(app)
        assert resp.status_code == 200, f"routing failed: {resp.data}"
        sample = _ipc_sample(resp)
        assert sample is not None, (
            f"route_service response lacks X-IPC-Sample header (c3 R1: "
            f"ipc_latency has zero product callers)"
        )
        assert isinstance(sample, dict), f"ipc_sample is not a dict: {sample}"
        missing = IPC_SAMPLE_KEYS - set(sample.keys())
        assert not missing, (
            f"ipc_sample missing Cristian-algorithm fields {missing}: {sample}"
        )

    def test_cristian_fields_are_numerically_consistent(self):
        """network_rtt_ms + server_processing_ms <= total_duration_ms."""
        app = _make_mesh_app(ROUTES_A, _ok_transport)
        resp = _route_alpha(app)
        assert resp.status_code == 200
        sample = _ipc_sample(resp)
        assert sample is not None, "X-IPC-Sample header missing"
        total = sample["total_duration_ms"]
        net_rtt = sample["network_rtt_ms"]
        srv_proc = sample["server_processing_ms"]
        assert total >= 0, f"total_duration_ms negative: {total}"
        assert net_rtt >= 0, f"network_rtt_ms negative: {net_rtt}"
        assert srv_proc >= 0, f"server_processing_ms negative: {srv_proc}"
        assert net_rtt + srv_proc <= total + 1e-6, (
            f"rtt({net_rtt}) + proc({srv_proc}) > total({total})"
        )

    def test_clock_offset_is_finite(self):
        """clock_offset_ms must be a finite float (Cristian's formula output)."""
        app = _make_mesh_app(ROUTES_A, _ok_transport)
        resp = _route_alpha(app)
        assert resp.status_code == 200
        sample = _ipc_sample(resp)
        assert sample is not None, "X-IPC-Sample header missing"
        import math
        offset = sample["clock_offset_ms"]
        assert isinstance(offset, (int, float)), f"clock_offset_ms not numeric: {offset}"
        assert math.isfinite(offset), f"clock_offset_ms not finite: {offset}"


class TestIpcLatencyModuleIsRequired:
    """H657 negative control: removing ipc_latency must break route_service.

    If deleting the module leaves the test green, the test measures the
    caller's inline code, not the feature.
    """

    def test_route_fails_or_lacks_sample_without_ipc_latency_module(self):
        """With ipc_latency hidden, route_service must not produce ipc_sample."""
        saved = sys.modules.pop("bulk_downloader.ipc_latency", None)
        blocker = types.ModuleType("bulk_downloader.ipc_latency")
        blocker.__spec__ = None
        sys.modules["bulk_downloader.ipc_latency"] = blocker
        try:
            app = _make_mesh_app(ROUTES_A, _ok_transport)
            resp = _route_alpha(app)
            if resp.status_code == 200:
                sample = _ipc_sample(resp)
                assert sample is None or not IPC_SAMPLE_KEYS.issubset(sample.keys()), (
                    "X-IPC-Sample present with full Cristian fields even though "
                    "ipc_latency module is blocked — delegation is not real"
                )
        except (ImportError, AttributeError):
            pass
        finally:
            del sys.modules["bulk_downloader.ipc_latency"]
            if saved is not None:
                sys.modules["bulk_downloader.ipc_latency"] = saved


class TestLatencyIsolationAcrossRequests:
    """R3: each request must carry its own ipc_sample, not shared singleton state."""

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
