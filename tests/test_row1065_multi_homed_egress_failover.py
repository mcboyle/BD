"""Row 1065: Multi-Homed Physical Egress Routing & Autonomous Interface Failover.

The download path's two socket factories -- the guarded httpx transport's
network backend and the SOCKS carrier bridge -- connect through the egress
router: with an interface registered, the connection leaves from that
interface's address; with every interface tripped it is refused. The router's
health comes from those connects, trips and recovers with hysteresis and a
damping window, and reports (never swallows) a device pin it could not apply.
"""

from __future__ import annotations

import errno
import socket
import threading
import types

import pytest

BD_GATE_SCOPE = "module"

EGRESS_IP = "127.0.0.2"  # on Linux all of 127/8 is local, so this is a real second source address


def _listener():
    """TCP listener on 127.0.0.1 that records each accepted peer address."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(0.2)
    peers: list = []
    stop = threading.Event()

    def _accept():
        while not stop.is_set():
            try:
                conn, peer = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            peers.append(peer[0])
            conn.close()

    th = threading.Thread(target=_accept, daemon=True)
    th.start()

    def close():
        stop.set()
        srv.close()
        th.join(timeout=1.0)

    return srv.getsockname()[1], peers, close


def _wait_peer(peers, n=1):
    for _ in range(50):
        if len(peers) >= n:
            return
        threading.Event().wait(0.02)


@pytest.fixture
def egress(monkeypatch):
    """A fresh global router, or None where the capability does not exist (base)."""
    try:
        import bulk_downloader.multi_homed_egress as mhe
    except ImportError:
        return None
    router = mhe.MultiHomedEgressRouter()
    monkeypatch.setattr(mhe, "_GLOBAL_MULTI_HOMED_ROUTER", router)
    # The test listener lives on loopback; let the router treat it as a remote peer.
    monkeypatch.setattr(mhe, "_is_loopback", lambda host: False)
    return router


def _backend_connect(port):
    from bulk_downloader import ssrf_transport

    backend = ssrf_transport._HappyEyeballsBackend(types.SimpleNamespace(_vetted_siblings={}))
    return backend.connect_tcp("127.0.0.1", port, timeout=2.0, socket_options=[])


def test_positive_control_transport_backend_connects_unbound() -> None:
    """Positive control (passes at base): the backend connects and the listener sees the peer."""
    port, peers, close = _listener()
    try:
        stream = _backend_connect(port)
        stream.close()
        _wait_peer(peers)
        assert peers == ["127.0.0.1"], peers
    finally:
        close()


def test_transport_backend_connects_from_active_egress_interface(egress) -> None:
    """E1 (caller): guarded httpx transport sockets leave from the active interface address."""
    if egress is not None:
        egress.register_interface("lo-row1065", EGRESS_IP, "127.0.0.1", priority=10)
    port, peers, close = _listener()
    try:
        stream = _backend_connect(port)
        stream.close()
        _wait_peer(peers)
        assert peers == [EGRESS_IP], f"row1065: transport connected from {peers}, not the egress interface"
    finally:
        close()


def test_socks_bridge_upstream_connects_from_active_egress_interface(egress) -> None:
    """E1 (caller): the SOCKS carrier's upstream socket leaves from the active interface."""
    from bulk_downloader.download_egress import SocksHttpConnectBridge

    if egress is not None:
        egress.register_interface("lo-row1065", EGRESS_IP, "127.0.0.1", priority=10)
    port, peers, close = _listener()
    try:
        bridge = SocksHttpConnectBridge(f"socks5://127.0.0.1:{port}")
        with pytest.raises(OSError):
            bridge._socks_connect("example.com", 443)  # listener hangs up mid-handshake
        _wait_peer(peers)
        assert peers == [EGRESS_IP], f"row1065: SOCKS upstream connected from {peers}"
    finally:
        close()


def test_transport_backend_fails_closed_when_every_interface_is_down(egress) -> None:
    """E1 fail-closed: with every interface tripped the transport refuses and never connects."""
    import httpcore
    from bulk_downloader.multi_homed_egress import FailoverPolicy

    egress.failover_policy = FailoverPolicy(max_consecutive_failures=1)
    egress.register_interface("lo-row1065", EGRESS_IP, "127.0.0.1", priority=10)
    egress.record_probe_result("lo-row1065", success=False)
    port, peers, close = _listener()
    try:
        with pytest.raises(httpcore.ConnectError, match="No healthy egress interface"):
            _backend_connect(port)
        _wait_peer(peers)
        assert peers == []
    finally:
        close()


def test_unconfigured_router_leaves_egress_untouched(egress) -> None:
    """Negative control: no registered interface -> plain unbound connect, as before the row."""
    port, peers, close = _listener()
    try:
        _backend_connect(port).close()
        _wait_peer(peers)
        assert peers == ["127.0.0.1"], peers
    finally:
        close()


def test_loopback_carrier_is_not_bound_to_a_physical_interface(egress, monkeypatch) -> None:
    """A loopback peer (local SOCKS/VPN carrier) is connected unbound even on a multi-homed host."""
    import bulk_downloader.multi_homed_egress as mhe

    monkeypatch.undo()  # restore the real _is_loopback; keep the fresh router below
    monkeypatch.setattr(mhe, "_GLOBAL_MULTI_HOMED_ROUTER", egress)
    egress.register_interface("lo-row1065", EGRESS_IP, "127.0.0.1", priority=10)
    port, peers, close = _listener()
    try:
        mhe.connect_via_egress(("127.0.0.1", port), timeout=2.0).close()
        _wait_peer(peers)
        assert peers == ["127.0.0.1"], peers
    finally:
        close()


def test_multi_homed_interface_registration_and_priority_selection() -> None:
    from bulk_downloader.multi_homed_egress import MultiHomedEgressRouter

    router = MultiHomedEgressRouter()
    router.register_interface(name="eth0", ip_address="192.168.10.2", gateway="192.168.10.1", priority=10)
    router.register_interface(name="eth1", ip_address="192.168.20.2", gateway="192.168.20.1", priority=20)
    active = router.get_active_interface()
    assert (active.name, active.ip_address) == ("eth0", "192.168.10.2")


def test_autonomous_failover_on_threshold_failures() -> None:
    from bulk_downloader.multi_homed_egress import FailoverPolicy, MultiHomedEgressRouter

    router = MultiHomedEgressRouter(FailoverPolicy(max_consecutive_failures=3, recovery_success_threshold=2))
    router.register_interface(name="eth0", ip_address="192.168.10.2", gateway="192.168.10.1", priority=10)
    router.register_interface(name="eth1", ip_address="192.168.20.2", gateway="192.168.20.1", priority=20)
    assert router.record_probe_result("eth0", success=False) is False
    assert router.record_probe_result("eth0", success=False) is False
    assert router.get_active_interface().name == "eth0"
    assert router.record_probe_result("eth0", success=False) is True
    assert router.get_active_interface().name == "eth1"


def test_default_damping_holds_the_failover_route() -> None:
    """E3: under the DEFAULT policy a tripped primary does not recover/fail back inside 5s."""
    from bulk_downloader.multi_homed_egress import MultiHomedEgressRouter

    now = [1000.0]
    router = MultiHomedEgressRouter(clock=lambda: now[0])
    router.register_interface(name="eth0", ip_address="192.168.10.2", gateway="192.168.10.1", priority=10)
    router.register_interface(name="eth1", ip_address="192.168.20.2", gateway="192.168.20.1", priority=20)
    for _ in range(3):
        router.record_probe_result("eth0", success=False)
    assert router.get_active_interface().name == "eth1"
    now[0] += 1.0
    router.record_probe_result("eth0", success=True)
    router.record_probe_result("eth0", success=True)
    assert not router.interfaces["eth0"].is_healthy, "row1065: recovered inside the damping window"
    assert router.get_active_interface().name == "eth1"
    now[0] += 5.0
    router.record_probe_result("eth0", success=True)  # recovers now; must still hold 5s before failback
    assert router.interfaces["eth0"].is_healthy
    assert router.get_active_interface().name == "eth1", "row1065: failed back to a just-recovered link"
    now[0] += 5.0
    router.record_probe_result("eth0", success=True)
    assert router.get_active_interface().name == "eth0"


@pytest.mark.parametrize("damping,max_changes", [(3600.0, 1), (0.0, 6)])
def test_damping_window_bounds_route_flaps(damping, max_changes) -> None:
    """E3 (probe P1): an alternating link flips the route at most once under a 3600s window.

    The 0s row is the control: the same sequence flips every probe without damping.
    """
    from bulk_downloader.multi_homed_egress import FailoverPolicy, MultiHomedEgressRouter

    now = [0.0]
    router = MultiHomedEgressRouter(
        FailoverPolicy(max_consecutive_failures=1, recovery_success_threshold=1,
                       flapping_damping_seconds=damping),
        clock=lambda: now[0])
    router.register_interface(name="eth0", ip_address="192.168.10.2", gateway="192.168.10.1", priority=10)
    router.register_interface(name="eth1", ip_address="192.168.20.2", gateway="192.168.20.1", priority=20)
    changes = 0
    for i in range(6):
        now[0] += 1.0
        changes += router.record_probe_result("eth0", success=bool(i % 2))
    assert changes == max_changes, changes


def test_bind_explicit_tripped_interface_is_refused() -> None:
    """E4 (probe P2): an explicitly named but tripped interface is not bound."""
    from bulk_downloader.multi_homed_egress import EgressBindRefused, FailoverPolicy, MultiHomedEgressRouter

    router = MultiHomedEgressRouter(FailoverPolicy(max_consecutive_failures=1))
    router.register_interface(name="lo0", ip_address="127.0.0.1", gateway="127.0.0.1", priority=10)
    router.register_interface(name="lo1", ip_address=EGRESS_IP, gateway="127.0.0.1", priority=20)
    router.record_probe_result("lo0", success=False)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(EgressBindRefused, match="not healthy"):
            router.bind_socket_egress(sock, interface_name="lo0")
        assert sock.getsockname()[1] == 0, "row1065: socket was bound to the tripped interface"
        with pytest.raises(EgressBindRefused, match="not registered"):
            router.bind_socket_egress(sock, interface_name="nope0")
        router.bind_socket_egress(sock, interface_name="lo1")  # control: healthy one binds
        assert sock.getsockname()[0] == EGRESS_IP


def test_bind_with_no_interface_raises_and_leaves_socket_unbound() -> None:
    from bulk_downloader.multi_homed_egress import EgressBindRefused, MultiHomedEgressRouter

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(EgressBindRefused):
            MultiHomedEgressRouter().bind_socket_egress(sock)
        assert sock.getsockname()[1] == 0


def test_reregister_keeps_health_history() -> None:
    """E4 (probe P3): re-registering a tripped interface neither heals it nor reroutes."""
    from bulk_downloader.multi_homed_egress import FailoverPolicy, MultiHomedEgressRouter

    router = MultiHomedEgressRouter(FailoverPolicy(max_consecutive_failures=2))
    router.register_interface(name="eth0", ip_address="192.168.10.2", gateway="192.168.10.1", priority=10)
    router.register_interface(name="wwan0", ip_address="10.64.0.2", gateway="10.64.0.1", priority=50)
    router.record_probe_result("eth0", success=False)
    router.record_probe_result("eth0", success=False)
    assert router.get_active_interface().name == "wwan0"
    router.register_interface(name="eth0", ip_address="192.168.10.3", gateway="192.168.10.1", priority=10)
    eth0 = router.interfaces["eth0"]
    assert (eth0.is_healthy, eth0.consecutive_failures, eth0.ip_address) == (False, 2, "192.168.10.3")
    assert router.get_active_interface().name == "wwan0"


class _RecordingSocket:
    def __init__(self, pin_error=None):
        self.bound = None
        self.opts = []
        self._pin_error = pin_error

    def bind(self, addr):
        self.bound = addr

    def setsockopt(self, level, opt, value):
        if self._pin_error is not None:
            raise self._pin_error
        self.opts.append((level, opt, value))


@pytest.mark.skipif(not hasattr(socket, "SO_BINDTODEVICE"), reason="Linux-only socket option")
def test_bind_pins_device_and_reports_bound() -> None:
    """E2: the device pin is attempted with the interface name and reported as 'bound'."""
    from bulk_downloader.multi_homed_egress import BIND_BOUND, MultiHomedEgressRouter

    router = MultiHomedEgressRouter()
    router.register_interface(name="eth0", ip_address="192.168.10.2", gateway="192.168.10.1")
    sock = _RecordingSocket()
    assert router.bind_socket_egress(sock) == BIND_BOUND
    assert sock.bound == ("192.168.10.2", 0)
    assert sock.opts == [(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b"eth0")]


@pytest.mark.skipif(not hasattr(socket, "SO_BINDTODEVICE"), reason="Linux-only socket option")
def test_bind_device_pin_failure_is_reported_not_swallowed() -> None:
    """E2 (probe P3): a refused SO_BINDTODEVICE is 'ip-only' + logged event, or raises when required."""
    from bulk_downloader.multi_homed_egress import BIND_IP_ONLY, EgressBindRefused, MultiHomedEgressRouter

    router = MultiHomedEgressRouter()
    router.register_interface(name="eth0", ip_address="192.168.10.2", gateway="192.168.10.1")
    denied = PermissionError(errno.EPERM, "Operation not permitted")
    assert router.bind_socket_egress(_RecordingSocket(denied)) == BIND_IP_ONLY
    events = [e["event"] for e in router.get_egress_status()["recent_events"]]
    assert "bind_ip_only" in events, events
    with pytest.raises(EgressBindRefused, match="SO_BINDTODEVICE"):
        router.bind_socket_egress(_RecordingSocket(denied), require_device=True)


def test_connect_outcomes_drive_link_health(egress, monkeypatch) -> None:
    """Link health input: unreachable-network connects trip the interface; a far-end refusal does not."""
    import bulk_downloader.multi_homed_egress as mhe

    egress.failover_policy = mhe.FailoverPolicy(max_consecutive_failures=1)
    egress.register_interface("lo-row1065", EGRESS_IP, "127.0.0.1", priority=10)
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    closed_port = probe.getsockname()[1]
    probe.close()
    with pytest.raises(ConnectionRefusedError):
        mhe.connect_via_egress(("127.0.0.1", closed_port), timeout=2.0)
    assert egress.interfaces["lo-row1065"].is_healthy, "row1065: far-end refusal tripped the link"

    real_socket = socket.socket

    class _Unreachable(real_socket):
        def connect(self, addr):
            raise OSError(errno.ENETUNREACH, "Network is unreachable")

    monkeypatch.setattr(mhe.socket, "socket", _Unreachable)
    with pytest.raises(OSError):
        mhe.connect_via_egress(("127.0.0.1", closed_port), timeout=2.0)
    assert not egress.interfaces["lo-row1065"].is_healthy
    assert egress.get_active_interface() is None


@pytest.mark.parametrize("host, unbound", [
    ("127.0.0.1", True), ("127.8.8.8", True), ("::1", True), ("[::1]", True), ("localhost", True),
    ("::ffff:127.0.0.1", True),
    # Not loopback -> bound to the active interface (fail-closed when none is healthy): CGNAT,
    # RFC 1918 and link-local peers are egress like any remote peer, never exempted.
    ("100.64.0.1", False), ("10.0.0.1", False), ("169.254.1.1", False), ("8.8.8.8", False),
    ("::ffff:10.0.0.1", False), ("example.test", False),
])
def test_only_a_loopback_carrier_escapes_the_egress_binding(host, unbound) -> None:
    import bulk_downloader.multi_homed_egress as mhe

    assert mhe._is_loopback(host) is unbound, (
        f"row1065: _is_loopback({host!r}) -> {not unbound}; an IPv4-mapped loopback carrier must "
        f"stay unbound and every non-loopback peer must be bound")


def _closed_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def _resolves_to(monkeypatch, ports):
    """Make every name resolve to 127.0.0.1 at each of ``ports``, in order (a multi-address host)."""
    def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", p)) for p in ports]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def test_n2b_e1_socks_upstream_falls_back_to_the_next_resolved_address(egress, monkeypatch) -> None:
    """N2-B E1: a multi-address proxy host whose first address refuses is reached on the second,
    from the egress interface, like socket.create_connection -- and the link stays healthy."""
    from bulk_downloader.download_egress import SocksHttpConnectBridge

    egress.register_interface("lo-row1065", EGRESS_IP, "127.0.0.1", priority=10)
    port, peers, close = _listener()
    try:
        _resolves_to(monkeypatch, [_closed_port(), port])
        bridge = SocksHttpConnectBridge(f"socks5://proxy.row1065.test:{port}")
        with pytest.raises(OSError) as info:
            bridge._socks_connect("example.com", 443)  # listener hangs up mid-handshake
        assert not isinstance(info.value, ConnectionRefusedError), (
            "N2-B E1: the SOCKS upstream stopped at the first (refused) address; the second was never tried")
        _wait_peer(peers)
        assert peers == [EGRESS_IP], f"N2-B E1: SOCKS upstream reached {peers}"
    finally:
        close()
    iface = egress.interfaces["lo-row1065"]
    assert iface.is_healthy and iface.consecutive_failures == 0


def test_n2b_e1_every_address_refused_raises_the_refusal(egress, monkeypatch) -> None:
    """Negative control for E1: when every resolved address refuses, the refusal propagates."""
    import bulk_downloader.multi_homed_egress as mhe

    egress.register_interface("lo-row1065", EGRESS_IP, "127.0.0.1", priority=10)
    _resolves_to(monkeypatch, [_closed_port(), _closed_port()])
    with pytest.raises(ConnectionRefusedError):
        mhe.connect_via_egress(("peer.row1065.test", 443), timeout=2.0)
    assert egress.interfaces["lo-row1065"].is_healthy, "a far-end refusal is not a link failure"

