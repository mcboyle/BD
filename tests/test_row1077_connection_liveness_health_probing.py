"""Row 1077: Connection Liveness Monitoring & Health Probing.

Tests verify:
1. Positive control on multi_conn baseline and semantic failure on missing capability.
2. Endpoint target registration, parsing, and normalization.
3. Active socket probe execution, state resolution, and latency measurement.
4. Unreachable endpoint failure handling and fail-closed state recording.
5. Hysteresis threshold state transitions (HEALTHY, DEGRADED, DOWN, UNKNOWN).
6. State transition event listener callback dispatch.
7. Background monitoring lifecycle, graceful termination, and thread safety.
8. Caller integration through bulk_downloader.multi_conn.
9. Summary metrics aggregation and telemetry reporting.
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
import pytest

# Repo-wide: it pins the SSRF refusal of the raw TCP probe and the fail-closed
# liveness gate on the transfer path (runner_transport._try_multi_conn_download),
# which a change to multi_conn, runner_transport, _classify_ip or the row 1065
# router can break without touching connection_liveness.py.
BD_GATE_SCOPE = "repo-wide"


def test_row1077_positive_control_and_capability() -> None:
    """Proves probe can say YES on positive control and fails on base for missing capability."""
    repo_root = Path(__file__).resolve().parents[1]
    multi_conn_py = repo_root / "bulk_downloader" / "multi_conn.py"
    assert multi_conn_py.is_file(), f"Positive control failed: {multi_conn_py} does not exist"

    content = multi_conn_py.read_text(encoding="utf-8")
    assert "plan_chunks" in content, "Positive control failed: plan_chunks not found in multi_conn.py"

    try:
        from bulk_downloader.connection_liveness import (  # type: ignore[import-not-found]
            ConnectionLivenessMonitor,
            EndpointTarget,
            LivenessState,
            ProbeResult,
            get_connection_liveness_monitor,
        )
    except (ImportError, ModuleNotFoundError) as exc:
        raise AssertionError(
            "Row 1077 capability missing: Connection Liveness Monitoring & "
            "Health Probing not implemented in bulk_downloader.connection_liveness"
        ) from exc


def test_endpoint_target_parsing_and_normalization() -> None:
    """Verify endpoint target parsing from URLs, host:port strings, and defaults."""
    from bulk_downloader.connection_liveness import EndpointTarget

    t1 = EndpointTarget.from_target("https://cdn.example.com:8443/download/chunk_01.bin")
    assert t1.host == "cdn.example.com"
    assert t1.port == 8443
    assert t1.key == "cdn.example.com:8443"

    t2 = EndpointTarget.from_target("http://media.internal/stream.mp4")
    assert t2.host == "media.internal"
    assert t2.port == 80
    assert t2.key == "media.internal:80"

    t3 = EndpointTarget.from_target("127.0.0.1:9092")
    assert t3.host == "127.0.0.1"
    assert t3.port == 9092
    assert t3.key == "127.0.0.1:9092"

    t4 = EndpointTarget.from_target("192.168.1.100", default_port=443)
    assert t4.host == "192.168.1.100"
    assert t4.port == 443


def test_active_socket_probe_healthy_and_latency() -> None:
    """Verify active TCP socket probing succeeds against a listening port and measures latency."""
    from bulk_downloader.connection_liveness import (
        ConnectionLivenessMonitor,
        LivenessState,
    )

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    host, port = srv.getsockname()

    stop_event = threading.Event()

    def _acceptor():
        srv.settimeout(0.2)
        while not stop_event.is_set():
            try:
                conn, _ = srv.accept()
                conn.close()
            except socket.timeout:
                continue
            except OSError:
                break

    th = threading.Thread(target=_acceptor, daemon=True)
    th.start()

    try:
        mon = ConnectionLivenessMonitor(allow_private=True)
        target_key = f"{host}:{port}"
        mon.register_target(target_key)

        res = mon.probe_target(target_key)
        assert res.state == LivenessState.HEALTHY
        assert res.consecutive_failures == 0
        assert res.consecutive_successes >= 1
        assert res.latency_ms >= 0.0
        assert res.error is None
    finally:
        stop_event.set()
        srv.close()
        th.join(timeout=1.0)


def test_active_socket_probe_down_on_unreachable_endpoint() -> None:
    """Verify probe records DOWN state and error without crashing on unreachable port."""
    from bulk_downloader.connection_liveness import (
        ConnectionLivenessMonitor,
        LivenessState,
    )

    # Pick a port that is guaranteed closed
    probe_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe_sock.bind(("127.0.0.1", 0))
    closed_port = probe_sock.getsockname()[1]
    probe_sock.close()

    mon = ConnectionLivenessMonitor(allow_private=True)
    target_key = f"127.0.0.1:{closed_port}"
    mon.register_target(target_key, failure_threshold=1)

    res = mon.probe_target(target_key)
    assert res.state == LivenessState.DOWN
    assert res.consecutive_failures == 1
    assert res.consecutive_successes == 0
    assert res.error is not None


def test_hysteresis_state_transitions() -> None:
    """Verify failure and recovery thresholds prevent oscillation."""
    from bulk_downloader.connection_liveness import (
        ConnectionLivenessMonitor,
        EndpointTarget,
        LivenessState,
        ProbeResult,
    )

    mon = ConnectionLivenessMonitor()
    target = "test-host:80"
    mon.register_target(
        target,
        failure_threshold=3,
        recovery_threshold=2,
        latency_threshold_ms=100.0,
    )

    # Initial state is UNKNOWN
    status = mon.get_target_status(target)
    assert status.state == LivenessState.UNKNOWN

    # Mock probe: 1 success -> HEALTHY
    mon._apply_probe_result(
        ProbeResult(
            target=target,
            state=LivenessState.HEALTHY,
            latency_ms=10.0,
            timestamp=time.time(),
            error=None,
        )
    )
    assert mon.get_target_status(target).state == LivenessState.HEALTHY

    # 1 failure -> DEGRADED (below failure_threshold=3)
    mon._apply_probe_result(
        ProbeResult(
            target=target,
            state=LivenessState.DOWN,
            latency_ms=0.0,
            timestamp=time.time(),
            error="Connection timed out",
        )
    )
    assert mon.get_target_status(target).state == LivenessState.DEGRADED

    # 2nd failure -> still DEGRADED
    mon._apply_probe_result(
        ProbeResult(
            target=target,
            state=LivenessState.DOWN,
            latency_ms=0.0,
            timestamp=time.time(),
            error="Connection timed out",
        )
    )
    assert mon.get_target_status(target).state == LivenessState.DEGRADED

    # 3rd failure -> DOWN
    mon._apply_probe_result(
        ProbeResult(
            target=target,
            state=LivenessState.DOWN,
            latency_ms=0.0,
            timestamp=time.time(),
            error="Connection timed out",
        )
    )
    assert mon.get_target_status(target).state == LivenessState.DOWN

    # Recovery: 1st success -> DEGRADED (below recovery_threshold=2)
    mon._apply_probe_result(
        ProbeResult(
            target=target,
            state=LivenessState.HEALTHY,
            latency_ms=12.0,
            timestamp=time.time(),
            error=None,
        )
    )
    assert mon.get_target_status(target).state == LivenessState.DEGRADED

    # 2nd success -> HEALTHY
    mon._apply_probe_result(
        ProbeResult(
            target=target,
            state=LivenessState.HEALTHY,
            latency_ms=11.0,
            timestamp=time.time(),
            error=None,
        )
    )
    assert mon.get_target_status(target).state == LivenessState.HEALTHY


def test_state_change_event_callbacks() -> None:
    """Verify callback notifications on state transitions."""
    from bulk_downloader.connection_liveness import (
        ConnectionLivenessMonitor,
        LivenessState,
        ProbeResult,
    )

    events: list[tuple[str, LivenessState, LivenessState]] = []

    def on_change(target: str, old_state: LivenessState, new_state: LivenessState, res: ProbeResult):
        events.append((target, old_state, new_state))

    mon = ConnectionLivenessMonitor()
    mon.add_state_listener(on_change)
    target = "callback-test:443"
    mon.register_target(target, failure_threshold=1)

    # UNKNOWN -> HEALTHY
    mon._apply_probe_result(
        ProbeResult(
            target=target,
            state=LivenessState.HEALTHY,
            latency_ms=5.0,
            timestamp=time.time(),
            error=None,
        )
    )
    assert len(events) == 1
    assert events[0] == (target, LivenessState.UNKNOWN, LivenessState.HEALTHY)

    # HEALTHY -> DOWN
    mon._apply_probe_result(
        ProbeResult(
            target=target,
            state=LivenessState.DOWN,
            latency_ms=0.0,
            timestamp=time.time(),
            error="Connection refused",
        )
    )
    assert len(events) == 2
    assert events[1] == (target, LivenessState.HEALTHY, LivenessState.DOWN)


def test_background_monitor_lifecycle_and_thread_safety() -> None:
    """Verify background monitor thread starts, reports is_running, and stops cleanly."""
    from bulk_downloader.connection_liveness import ConnectionLivenessMonitor

    mon = ConnectionLivenessMonitor(poll_interval_s=0.05)
    assert not mon.is_running

    mon.start_background()
    assert mon.is_running

    # Concurrently access methods while running
    threads = []
    for _ in range(5):
        t = threading.Thread(target=lambda: [mon.summary() for _ in range(20)])
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=1.0)

    mon.stop_background()
    assert not mon.is_running


def test_caller_integration_multi_conn() -> None:
    """Verify bulk_downloader.multi_conn caller exports and can probe target."""
    import bulk_downloader.multi_conn as mc

    assert hasattr(mc, "check_connection_liveness"), (
        "bulk_downloader.multi_conn must export check_connection_liveness"
    )
    assert hasattr(mc, "get_connection_liveness_monitor"), (
        "bulk_downloader.multi_conn must export get_connection_liveness_monitor"
    )

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    host, port = srv.getsockname()

    stop_event = threading.Event()

    def _srv():
        srv.settimeout(0.2)
        while not stop_event.is_set():
            try:
                c, _ = srv.accept()
                c.close()
            except socket.timeout:
                continue
            except OSError:
                break

    th = threading.Thread(target=_srv, daemon=True)
    th.start()

    from bulk_downloader.connection_liveness import reset_connection_liveness_monitor

    reset_connection_liveness_monitor()
    try:
        res = mc.check_connection_liveness(f"{host}:{port}", timeout_s=1.0)
        assert isinstance(res, dict)
        assert res.get("target") == f"{host}:{port}"
        # A live loopback listener: only the SSRF guard can make this non-healthy.
        assert res.get("state") != "healthy", res
        assert "non-public" in (res.get("error") or ""), res
    finally:
        reset_connection_liveness_monitor()
        stop_event.set()
        srv.close()
        th.join(timeout=1.0)


def test_summary_metrics_aggregation() -> None:
    """Verify summary() aggregates target counts, health states, and average latency."""
    from bulk_downloader.connection_liveness import (
        ConnectionLivenessMonitor,
        LivenessState,
        ProbeResult,
    )

    mon = ConnectionLivenessMonitor()
    mon.register_target("h1:80")
    mon.register_target("h2:80")
    mon.register_target("h3:80")

    mon._apply_probe_result(
        ProbeResult(
            target="h1:80",
            state=LivenessState.HEALTHY,
            latency_ms=20.0,
            timestamp=time.time(),
            error=None,
        )
    )
    mon._apply_probe_result(
        ProbeResult(
            target="h2:80",
            state=LivenessState.HEALTHY,
            latency_ms=40.0,
            timestamp=time.time(),
            error=None,
        )
    )

    s = mon.summary()
    assert s["total_targets"] == 3
    assert s["healthy"] == 2
    assert s["unknown"] == 1
    assert s["down"] == 0
    assert 29.0 <= s["avg_latency_ms"] <= 31.0


def _listener():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    srv.settimeout(0.2)
    stop = threading.Event()

    def _accept():
        while not stop.is_set():
            try:
                c, _ = srv.accept()
                c.close()
            except socket.timeout:
                continue
            except OSError:
                break

    th = threading.Thread(target=_accept, daemon=True)
    th.start()

    def close():
        stop.set()
        srv.close()
        th.join(timeout=1.0)

    return srv.getsockname(), close


def test_monitor_refuses_non_public_target_by_default() -> None:
    """E2: a live loopback listener is refused, not probed, unless allow_private."""
    from bulk_downloader.connection_liveness import ConnectionLivenessMonitor, LivenessState

    (host, port), close = _listener()
    try:
        res = ConnectionLivenessMonitor().probe_target(f"{host}:{port}")
        assert res.state != LivenessState.HEALTHY, res
        assert "non-public" in (res.error or ""), res.error
        # Negative control: the same listener is HEALTHY once private targets are allowed.
        ok = ConnectionLivenessMonitor(allow_private=True).probe_target(f"{host}:{port}")
        assert ok.state == LivenessState.HEALTHY, ok
    finally:
        close()


def test_probe_socket_refuses_link_local_metadata_without_connecting(monkeypatch) -> None:
    """E2 (P3): 169.254.169.254 is refused before any socket is opened."""
    import bulk_downloader.connection_liveness as cl

    def _no_socket(*a, **k):
        raise AssertionError("row1077: socket opened for 169.254.169.254")

    monkeypatch.setattr(cl.socket, "socket", _no_socket)
    ok, latency, err = cl.probe_socket("169.254.169.254", 80, timeout_s=0.5)
    assert ok is False
    assert "non-public" in (err or ""), err


def test_ipv6_literal_target_parses() -> None:
    """E3 (P4): '[::1]:8080' is host ::1 port 8080, not host '[' port 80."""
    from bulk_downloader.connection_liveness import EndpointTarget

    t = EndpointTarget.from_target("[::1]:8080")
    assert (t.host, t.port) == ("::1", 8080), t
    t2 = EndpointTarget.from_target("Example.COM:8443")
    assert (t2.host, t2.port) == ("example.com", 8443), t2
    t3 = EndpointTarget.from_target("example.com", default_port=443)
    assert (t3.host, t3.port) == ("example.com", 443), t3


def test_slow_probe_is_degraded(monkeypatch) -> None:
    """E4: an up-but-slow endpoint (latency above threshold) is DEGRADED."""
    import bulk_downloader.connection_liveness as cl

    monkeypatch.setattr(cl, "probe_socket", lambda host, port, timeout_s=2.0, allow_private=False: (True, 900.0, None))
    mon = cl.ConnectionLivenessMonitor()
    mon.register_target("slow.example:80", latency_threshold_ms=500.0)
    res = mon.probe_target("slow.example:80")
    assert res.state == cl.LivenessState.DEGRADED, res
    monkeypatch.setattr(cl, "probe_socket", lambda host, port, timeout_s=2.0, allow_private=False: (True, 20.0, None))
    mon2 = cl.ConnectionLivenessMonitor()
    mon2.register_target("fast.example:80", latency_threshold_ms=500.0)
    assert mon2.probe_target("fast.example:80").state == cl.LivenessState.HEALTHY


def _runner_multi_conn(monkeypatch, liveness, proxy_url=None, router=None):
    """Drive TransportMixin._try_multi_conn_download with a fake multi_conn module."""
    import types
    from bulk_downloader import multi_homed_egress, runner_transport

    # Row 1065's egress router is process-global; pin it (default: no interface
    # registered = inert) so no other test's registration decides this gate.
    monkeypatch.setattr(multi_homed_egress, "_GLOBAL_MULTI_HOMED_ROUTER",
                        router or multi_homed_egress.MultiHomedEgressRouter())

    calls = {"liveness": [], "download": 0}

    class FakeMultiConn:
        @staticmethod
        def probe(*a, **k):
            return types.SimpleNamespace(ok=True, content_length=500 * 1024 * 1024,
                                         accept_ranges=True, final_url="https://cdn.example/f.bin",
                                         error="")

        @staticmethod
        def should_use_multi_conn(*a, **k):
            return True

        @staticmethod
        def check_connection_liveness(target, *a, **k):
            calls["liveness"].append(target)
            if isinstance(liveness, Exception):
                raise liveness
            return liveness

        @staticmethod
        def download(*a, **k):
            calls["download"] += 1
            raise RuntimeError("row1077: download reached")

    monkeypatch.setattr(runner_transport, "_MULTI_CONN_AVAILABLE", True)
    monkeypatch.setattr(runner_transport, "_mconn", FakeMultiConn)
    stub = types.SimpleNamespace(config={}, site_id="row1077",
                                 log_event=lambda *a, **k: None)
    out = runner_transport.TransportMixin._try_multi_conn_download(
        stub, "https://site.example/p", "https://site.example/f.bin", "/nonexistent/row1077.part",
        headers={}, proxy_url=proxy_url)
    return out, calls


@pytest.mark.parametrize("state", ["down", "degraded", "unknown"])
def test_runner_multi_conn_falls_back_unless_endpoint_healthy(monkeypatch, state) -> None:
    """E1: the multi-connection plan is gated by liveness; not HEALTHY -> no fan-out."""
    out, calls = _runner_multi_conn(monkeypatch, {"state": state, "error": "x"})
    assert out is False
    assert calls["liveness"] == ["https://cdn.example/f.bin"], calls
    assert calls["download"] == 0, calls


def test_runner_multi_conn_liveness_error_falls_back(monkeypatch) -> None:
    """E1 fail-closed: a liveness check that could not run does not permit fan-out."""
    out, calls = _runner_multi_conn(monkeypatch, OSError("row1077 probe broke"))
    assert out is False
    assert calls["download"] == 0, calls


def test_runner_multi_conn_proceeds_when_healthy(monkeypatch) -> None:
    """E1 negative control: a HEALTHY endpoint reaches the multi-connection download."""
    out, calls = _runner_multi_conn(monkeypatch, {"state": "healthy", "error": None})
    assert calls["liveness"] == ["https://cdn.example/f.bin"], calls
    assert calls["download"] == 1, calls


def test_runner_multi_conn_proxied_never_probes_direct(monkeypatch) -> None:
    """E1: a proxied download is never probed by a direct TCP connect (no egress bypass)."""
    out, calls = _runner_multi_conn(monkeypatch, {"state": "down", "error": "x"},
                                    proxy_url="socks5://127.0.0.1:1080")
    assert calls["liveness"] == [], calls
    assert calls["download"] == 1, calls


def test_runner_multi_conn_interface_bound_never_probes_direct(monkeypatch) -> None:
    """Row 1065: once an egress interface is registered, every download socket is
    bound to it (and refused while none is healthy). A raw liveness connect would
    leave by the OS route instead, so the gate is skipped as when proxied; the
    guarded HTTP probe above already went out through the router."""
    from bulk_downloader import multi_homed_egress

    router = multi_homed_egress.MultiHomedEgressRouter()
    router.register_interface("eth-row1077", "192.0.2.10", "192.0.2.1")
    assert router.is_configured()
    out, calls = _runner_multi_conn(monkeypatch, {"state": "down", "error": "x"},
                                    router=router)
    assert calls["liveness"] == [], calls
    assert calls["download"] == 1, calls


def _state_probe(ok: bool, target: str):
    from bulk_downloader.connection_liveness import LivenessState, ProbeResult

    return ProbeResult(
        target=target,
        state=LivenessState.HEALTHY if ok else LivenessState.DOWN,
        latency_ms=5.0 if ok else 0.0,
        timestamp=time.time(),
        error=None if ok else "Connection refused",
    )


def test_probe_by_url_keeps_the_registered_thresholds(monkeypatch) -> None:
    """N6-A E1 (P7): register(url, thresholds) then probe(url) probes under that registration."""
    import bulk_downloader.connection_liveness as cl

    seen: list[tuple[str, int]] = []

    def _refused(host, port, timeout_s=2.0, allow_private=False):
        seen.append((host, port))
        return False, 0.0, "row1077: connection refused"

    monkeypatch.setattr(cl, "probe_socket", _refused)
    mon = cl.ConnectionLivenessMonitor()
    url = "https://CDN.Example.com:8443/files/big.bin"
    reg = mon.register_target(url, failure_threshold=1, latency_threshold_ms=50.0)
    assert reg.key == "cdn.example.com:8443", reg

    res = mon.probe_target(url)
    # failure_threshold=1: the first refused probe is DOWN; a default re-registration says DEGRADED.
    assert res.state == cl.LivenessState.DOWN, res
    assert seen == [("cdn.example.com", 8443)], seen
    assert list(mon._targets) == ["cdn.example.com:8443"], mon._targets
    kept = mon._targets["cdn.example.com:8443"]
    assert (kept.failure_threshold, kept.latency_threshold_ms) == (1, 50.0), kept
    # Another spelling of the same endpoint, registered with defaults, keeps the registration.
    assert mon.register_target("cdn.example.com:8443") is kept


def test_gate_probes_the_default_port_it_was_given(monkeypatch) -> None:
    """N6-A note: multi_conn.check_connection_liveness(default_port=...) reaches the probe."""
    import bulk_downloader.connection_liveness as cl
    import bulk_downloader.multi_conn as mc

    seen: list[tuple[str, int]] = []

    def _up(host, port, timeout_s=2.0, allow_private=False):
        seen.append((host, port))
        return True, 5.0, None

    monkeypatch.setattr(cl, "probe_socket", _up)
    cl.reset_connection_liveness_monitor()
    try:
        res = mc.check_connection_liveness("cdn.example.com", default_port=8443, timeout_s=0.5)
    finally:
        cl.reset_connection_liveness_monitor()
    assert seen == [("cdn.example.com", 8443)], seen
    assert (res["target"], res["state"]) == ("cdn.example.com:8443", "healthy"), res


def test_a_success_clears_the_failure_streak() -> None:
    """N6-A E2 (m3): a success resets consecutive_failures; the next lone failure is DEGRADED."""
    from bulk_downloader.connection_liveness import ConnectionLivenessMonitor, LivenessState

    mon = ConnectionLivenessMonitor()
    target = "streak.example:80"
    mon.register_target(target, failure_threshold=3)

    fails = [mon._apply_probe_result(_state_probe(False, target)).consecutive_failures for _ in range(2)]
    assert fails == [1, 2], fails
    r = mon._apply_probe_result(_state_probe(True, target))
    assert (r.state, r.consecutive_failures, r.consecutive_successes) == (LivenessState.HEALTHY, 0, 1), r
    r = mon._apply_probe_result(_state_probe(False, target))
    assert (r.state, r.consecutive_failures, r.consecutive_successes) == (LivenessState.DEGRADED, 1, 0), r


def test_https_url_without_a_port_is_probed_on_443() -> None:
    """N6-A E2 (m4): the scheme decides a missing port (https 443, http 80) before default_port."""
    from bulk_downloader.connection_liveness import EndpointTarget

    t = EndpointTarget.from_target("https://cdn.example.com/download/chunk_01.bin")
    assert (t.host, t.port, t.key) == ("cdn.example.com", 443, "cdn.example.com:443"), t
    t_upper = EndpointTarget.from_target("HTTPS://CDN.Example.com/x", default_port=8080)
    assert (t_upper.port, t_upper.key) == (443, "cdn.example.com:443"), t_upper
    t_http = EndpointTarget.from_target("http://cdn.example.com/x", default_port=443)
    assert t_http.port == 80, t_http


def test_probe_socket_reports_a_failed_close(monkeypatch) -> None:
    """A socket that cannot be closed is a failed probe carrying the error, not a silent success."""
    import bulk_downloader.connection_liveness as cl

    closes: list[int] = []

    class _CloseFails:
        def __init__(self, *a, **k):
            pass

        def settimeout(self, timeout):
            pass

        def connect(self, sockaddr):
            pass

        def close(self):
            closes.append(1)
            raise OSError("row1077: close failed")

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.close()

    monkeypatch.setattr(cl.socket, "socket", _CloseFails)
    ok, latency, err = cl.probe_socket("127.0.0.1", 9, timeout_s=0.5, allow_private=True)
    assert (ok, err) == (False, "row1077: close failed"), (ok, latency, err)
    assert closes == [1], closes


def test_raising_listener_is_counted_and_the_others_still_hear() -> None:
    """A raising state listener neither stops dispatch nor vanishes: summary() counts it."""
    from bulk_downloader.connection_liveness import ConnectionLivenessMonitor, LivenessState

    heard: list[LivenessState] = []

    def _broken(target, old_state, new_state, res):
        raise RuntimeError("row1077 listener broke")

    def _recorder(target, old_state, new_state, res):
        heard.append(new_state)

    mon = ConnectionLivenessMonitor()
    mon.add_state_listener(_broken)
    mon.add_state_listener(_recorder)
    target = "listener.example:80"
    mon.register_target(target, failure_threshold=1)
    mon._apply_probe_result(_state_probe(True, target))
    mon._apply_probe_result(_state_probe(False, target))

    assert heard == [LivenessState.HEALTHY, LivenessState.DOWN], heard
    s = mon.summary()
    assert s.get("error_count") == 2, s
    assert s.get("last_error") == "state listener: RuntimeError: row1077 listener broke", s


def test_background_poll_failure_is_counted_and_the_loop_survives() -> None:
    """A background poll that raises is recorded for summary() and the daemon keeps polling."""
    from bulk_downloader.connection_liveness import ConnectionLivenessMonitor

    mon = ConnectionLivenessMonitor(poll_interval_s=0.01)
    polls: list[int] = []
    polled_again = threading.Event()

    def _flaky_probe_all():
        polls.append(1)
        if len(polls) == 1:
            raise RuntimeError("row1077 poll broke")
        polled_again.set()
        return {}

    mon.probe_all = _flaky_probe_all  # type: ignore[method-assign]
    mon.start_background()
    try:
        assert polled_again.wait(5.0), "row1077: the background loop stopped after one failed poll"
    finally:
        mon.stop_background()
    s = mon.summary()
    assert s.get("error_count") == 1, s
    assert s.get("last_error") == "background probe: RuntimeError: row1077 poll broke", s


def test_summary_uptime_counts_degraded_endpoints_as_up() -> None:
    """P1-A E5 (uptime ratio): a DEGRADED endpoint still answers, so uptime is (healthy + degraded) / total."""
    from bulk_downloader.connection_liveness import ConnectionLivenessMonitor

    mon = ConnectionLivenessMonitor()
    for target in ("up.example:80", "flaky.example:80", "gone.example:80"):
        mon.register_target(target, failure_threshold=2)
    mon._apply_probe_result(_state_probe(True, "up.example:80"))
    mon._apply_probe_result(_state_probe(False, "flaky.example:80"))  # 1 of 2 failures: DEGRADED
    for _ in range(2):
        mon._apply_probe_result(_state_probe(False, "gone.example:80"))  # 2 of 2 failures: DOWN

    s = mon.summary()
    assert (s["healthy"], s["degraded"], s["down"], s["total_targets"]) == (1, 1, 1, 3), s
    assert s["uptime_ratio"] == round(2 / 3, 4), s


def test_unregister_leaves_no_status_behind() -> None:
    """P1-A E5 (unregister cleanup): an unregistered endpoint drops out of every status report."""
    from bulk_downloader.connection_liveness import ConnectionLivenessMonitor, LivenessState

    mon = ConnectionLivenessMonitor()
    mon.register_target("keep.example:80")
    mon.register_target("drop.example:80", failure_threshold=1)
    mon._apply_probe_result(_state_probe(False, "drop.example:80"))
    assert mon.get_target_status("drop.example:80").state == LivenessState.DOWN

    # Any spelling of the endpoint unregisters it.
    assert mon.unregister_target("http://DROP.example/x") is True
    assert sorted(mon.get_all_statuses()) == ["keep.example:80"], mon.get_all_statuses()
    s = mon.summary()
    assert (s["total_targets"], s["down"], s["unknown"]) == (1, 0, 1), s
    assert mon.get_target_status("drop.example:80").state == LivenessState.UNKNOWN


def test_a_result_that_carries_an_error_is_a_failed_probe() -> None:
    """P1-A E5 (error gating): a result that carries an error is a failure even when its state says HEALTHY."""
    from bulk_downloader.connection_liveness import ConnectionLivenessMonitor, LivenessState, ProbeResult

    mon = ConnectionLivenessMonitor()
    target = "err.example:80"
    mon.register_target(target, failure_threshold=3)
    raw = ProbeResult(
        target=target,
        state=LivenessState.HEALTHY,
        latency_ms=5.0,
        timestamp=time.time(),
        error="row1077: reset after connect",
    )
    r = mon._apply_probe_result(raw)
    assert (r.state, r.consecutive_failures, r.consecutive_successes) == (LivenessState.DEGRADED, 1, 0), r
    assert r.error == "row1077: reset after connect", r


def test_probe_applies_the_requested_socket_timeout_before_connecting(monkeypatch) -> None:
    """P1-A E5 (socket timeout): probe_target's timeout reaches the socket before connect()."""
    import bulk_downloader.connection_liveness as cl

    calls: list[tuple[str, object]] = []

    class _RecordingSocket:
        def __init__(self, *a, **k):
            pass

        def settimeout(self, timeout):
            calls.append(("settimeout", timeout))

        def connect(self, sockaddr):
            calls.append(("connect", tuple(sockaddr[:2])))

        def close(self):
            calls.append(("close", None))

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.close()

    monkeypatch.setattr(cl.socket, "socket", _RecordingSocket)
    # TEST-NET-1 literal: getaddrinfo answers without DNS and the fake socket sends nothing.
    mon = cl.ConnectionLivenessMonitor(allow_private=True)
    res = mon.probe_target("192.0.2.10:8443", timeout_s=0.25)

    assert res.state == cl.LivenessState.HEALTHY, res
    assert calls == [("settimeout", 0.25), ("connect", ("192.0.2.10", 8443)), ("close", None)], calls


def test_a_recovery_threshold_above_two_is_honoured() -> None:
    """Self-lens W9-R1: after DOWN an endpoint needs recovery_threshold consecutive successes
    before it is HEALTHY again -- for any configured threshold, not only the default 2."""
    from bulk_downloader.connection_liveness import ConnectionLivenessMonitor, LivenessState

    mon = ConnectionLivenessMonitor()
    target = "recover.example:80"
    mon.register_target(target, failure_threshold=1, recovery_threshold=3)
    states = [mon._apply_probe_result(_state_probe(ok, target)).state for ok in (False, True, True, True)]
    assert states == [
        LivenessState.DOWN,
        LivenessState.DEGRADED,  # 1 of 3 successes
        LivenessState.DEGRADED,  # 2 of 3 successes
        LivenessState.HEALTHY,
    ], states


def test_a_failure_while_recovering_restarts_the_recovery_count() -> None:
    """Self-lens W9-R1: a failure below failure_threshold while recovering from DOWN keeps the
    endpoint recovering (the success count restarts); once recovered, a blip is an ordinary one."""
    from bulk_downloader.connection_liveness import ConnectionLivenessMonitor, LivenessState

    mon = ConnectionLivenessMonitor()
    target = "flap.example:80"
    mon.register_target(target)  # failure_threshold=3, recovery_threshold=2
    seq = (False, False, False, True, False, True, True, False, True)
    states = [mon._apply_probe_result(_state_probe(ok, target)).state for ok in seq]
    assert states == [
        LivenessState.DEGRADED, LivenessState.DEGRADED, LivenessState.DOWN,  # 3 failures: DOWN
        LivenessState.DEGRADED, LivenessState.DEGRADED,  # 1 success, then a failure: still recovering
        LivenessState.DEGRADED, LivenessState.HEALTHY,  # 2 consecutive successes: recovered
        LivenessState.DEGRADED, LivenessState.HEALTHY,  # a blip after recovery: 1 success is enough
    ], states


def test_unregister_forgets_a_pending_recovery() -> None:
    """Self-lens W9-R1: unregister_target drops an endpoint's pending recovery with the rest of
    its state, so registering the same endpoint again starts clean."""
    from bulk_downloader.connection_liveness import ConnectionLivenessMonitor, LivenessState

    mon = ConnectionLivenessMonitor()
    target = "again.example:80"
    mon.register_target(target, failure_threshold=1)
    assert mon._apply_probe_result(_state_probe(False, target)).state == LivenessState.DOWN
    assert mon.unregister_target(target) is True
    mon.register_target(target, failure_threshold=1)
    assert mon._apply_probe_result(_state_probe(True, target)).state == LivenessState.HEALTHY


def _dual_stack(monkeypatch, refusals):
    """Resolve every name to one IPv6 then one IPv4 answer (documentation ranges; the
    callers pass allow_private=True) and fake the sockets: connect() to an address in
    ``refusals`` raises that error. Returns the connected addresses, in order."""
    import bulk_downloader.connection_liveness as cl

    v6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::10", 443, 0, 0))
    v4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", 443))
    monkeypatch.setattr(cl.socket, "getaddrinfo", lambda *a, **k: [v6, v4])
    connects: list[str] = []

    class _Sock:
        def __init__(self, *a, **k):
            pass

        def settimeout(self, timeout):
            pass

        def connect(self, sockaddr):
            connects.append(sockaddr[0])
            if sockaddr[0] in refusals:
                raise refusals[sockaddr[0]]

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.close()

    monkeypatch.setattr(cl.socket, "socket", _Sock)
    return connects


def test_probe_tries_the_next_address_when_the_first_is_unreachable(monkeypatch) -> None:
    """M1 (P1-A NOTE P6): a dual-stack host whose first answer is unreachable is up when a
    later answer connects; the answers are tried in resolver order."""
    import bulk_downloader.connection_liveness as cl

    connects = _dual_stack(monkeypatch, {"2001:db8::10": OSError(101, "Network is unreachable")})
    ok, latency, err = cl.probe_socket("dual.example", 443, timeout_s=0.5, allow_private=True)
    assert (ok, err) == (True, None), (ok, latency, err)
    assert connects == ["2001:db8::10", "192.0.2.10"], connects


def test_probe_is_down_only_when_every_address_failed(monkeypatch) -> None:
    """M1: when every answer fails the probe fails naming each failure in order; a first
    answer that connects opens no second socket."""
    import bulk_downloader.connection_liveness as cl

    connects = _dual_stack(monkeypatch, {
        "2001:db8::10": OSError(101, "Network is unreachable"),
        "192.0.2.10": OSError(111, "Connection refused"),
    })
    assert cl.probe_socket("dual.example", 443, timeout_s=0.5, allow_private=True) == (
        False, 0.0, "[Errno 101] Network is unreachable; [Errno 111] Connection refused")
    assert connects == ["2001:db8::10", "192.0.2.10"], connects

    connects = _dual_stack(monkeypatch, {})
    ok, _, err = cl.probe_socket("dual.example", 443, timeout_s=0.5, allow_private=True)
    assert (ok, err) == (True, None), (ok, err)
    assert connects == ["2001:db8::10"], connects


def test_probe_reports_a_resolver_failure_without_connecting(monkeypatch) -> None:
    """A name that does not resolve, or resolves to nothing, is a failed probe carrying the
    resolver's answer; no socket is opened."""
    import bulk_downloader.connection_liveness as cl

    def _nxdomain(*a, **k):
        raise socket.gaierror(-2, "Name or service not known")

    def _no_socket(*a, **k):
        raise AssertionError("row1077: socket opened for an unresolved name")

    monkeypatch.setattr(cl.socket, "socket", _no_socket)
    monkeypatch.setattr(cl.socket, "getaddrinfo", _nxdomain)
    assert cl.probe_socket("nx.example", 443, allow_private=True) == (
        False, 0.0, "[Errno -2] Name or service not known")
    monkeypatch.setattr(cl.socket, "getaddrinfo", lambda *a, **k: [])
    assert cl.probe_socket("empty.example", 443, allow_private=True) == (
        False, 0.0, "getaddrinfo returned no addresses")


def test_latency_is_the_connect_time_not_the_resolver_time(monkeypatch) -> None:
    """M2: the latency timer starts after name resolution: a 600 ms resolver answer and a
    5 ms connect are a 5 ms probe -- HEALTHY under the 500 ms threshold, not DEGRADED."""
    import types

    import bulk_downloader.connection_liveness as cl

    now = [1000.0]
    monkeypatch.setattr(cl, "time", types.SimpleNamespace(perf_counter=lambda: now[0], time=time.time))

    def _slow_resolver(host, port, *a, **k):
        now[0] += 0.600
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", port))]

    class _FiveMsSocket:
        def __init__(self, *a, **k):
            pass

        def settimeout(self, timeout):
            pass

        def connect(self, sockaddr):
            now[0] += 0.005

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.close()

    monkeypatch.setattr(cl.socket, "getaddrinfo", _slow_resolver)
    monkeypatch.setattr(cl.socket, "socket", _FiveMsSocket)
    ok, latency, err = cl.probe_socket("slowdns.example", 443, allow_private=True)
    assert (ok, err) == (True, None), (ok, latency, err)
    assert latency == pytest.approx(5.0, abs=0.01), latency
    res = cl.ConnectionLivenessMonitor(allow_private=True).probe_target("slowdns.example:443")
    assert res.state == cl.LivenessState.HEALTHY, res


def test_a_bare_ipv6_literal_is_one_host() -> None:
    """M3: a bare IPv6 literal is one host, never host:port -- '2001:db8::7' parsed as host
    '2001' (the IPv4 shorthand for 0.0.7.209) and '::1' as host ''."""
    from bulk_downloader.connection_liveness import EndpointTarget

    t = EndpointTarget.from_target("2001:db8::7", default_port=8443)
    assert (t.host, t.port, t.key) == ("2001:db8::7", 8443, "[2001:db8::7]:8443"), t
    t2 = EndpointTarget.from_target("::1")
    assert (t2.host, t2.port, t2.key) == ("::1", 80, "[::1]:80"), t2
    # The key names the same endpoint when it is parsed again.
    assert EndpointTarget.from_target(t.key).key == t.key


def test_a_target_without_a_host_is_refused() -> None:
    """M3 (N3-B NOTE): a target with no host raises ValueError instead of registering as
    ':<port>' -- there is nothing to probe."""
    from bulk_downloader.connection_liveness import ConnectionLivenessMonitor, EndpointTarget

    for target in ("http:///path", "http://:8080/x", "  "):
        with pytest.raises(ValueError, match="no host"):
            EndpointTarget.from_target(target)
    mon = ConnectionLivenessMonitor()
    with pytest.raises(ValueError, match="no host"):
        mon.probe_target("http:///path")
    assert mon.get_all_statuses() == {}, mon.get_all_statuses()
