"""Row 1066 -- egress socket pacing through the kernel's SO_MAX_PACING_RATE.

MEASURED ON BASE bc1544b7, not assumed:

    grep -rliE 'SO_MAX_PACING_RATE|ebpf' bulk_downloader/ tools/ --include=*.py -> 0 files
    python -c "import socket; socket.SO_MAX_PACING_RATE"                        -> AttributeError
      (CPython does not export the constant; Linux defines it in asm-generic/socket.h)
    grep -rliE 'pacing|token_bucket|rate_limit|throttle' bulk_downloader/ tools/ -> 87 files
    positive control, same probe shape: 'turnstile'                             -> 41 files

So the repo does shape bandwidth, and does it entirely in userspace:
``traffic_shaper.BandwidthShaper`` (Row 892) is a token bucket that slows how fast BD READS a
stream, which only throttles the peer indirectly, through the TCP receive window, after the bytes
have already crossed the wire. Nothing asks the kernel to pace the socket itself, so BD cannot
smooth its own egress and cannot tell an operator whether the kernel would honour such a request.

The first test below reads ``traffic_shaper``, which exists at base, so it fails with an
AssertionError naming the gap -- not an ImportError about the module this row adds.

SCOPE, stated because the brief's title asks for more than this cut delivers: the eBPF EDT
half is NOT built here; bulk_downloader/socket_pacing.py's module docstring says why.
"""
from __future__ import annotations

import importlib
import inspect
import json
import socket
import threading
import time

from bulk_downloader import download_egress, runner_transport, traffic_shaper

BD_GATE_SCOPE = "repo-wide"


def _pacing():
    """Import the subject lazily: on base this raises inside ONE test, not at collection."""
    return importlib.import_module('bulk_downloader.socket_pacing')


class _FakeSock:
    """A socket that records setsockopt and can echo the value back, or refuse like a kernel."""

    def __init__(self, *, supported=True, errno_on_set=None):
        self.family, self.type = socket.AF_INET, socket.SOCK_STREAM
        self._supported, self._errno = supported, errno_on_set
        self.calls, self._stored = [], {}

    def setsockopt(self, level, optname, value):
        self.calls.append((level, optname, value))
        if self._errno is not None:
            raise OSError(self._errno, "refused by fake kernel")
        if not self._supported:
            raise OSError(92, "Protocol not available")
        self._stored[(level, optname)] = value

    def getsockopt(self, level, optname, buflen=None):
        if not self._supported:
            raise OSError(92, "Protocol not available")
        return self._stored.get((level, optname), 0)


# -- 1. the row's reason, asserted against code that exists at base -----------------

def test_bandwidth_shaping_can_reach_the_socket_and_not_only_the_read_loop():
    shaper = traffic_shaper.BandwidthShaper(global_mbps=8)
    # positive control: the userspace shaper really is configured, so a missing socket path
    # below is a MISSING CAPABILITY and not a dead probe.
    assert shaper.is_enabled, "the probe did not manage to configure a bandwidth cap at all"
    assert hasattr(shaper, 'apply_to_socket'), (
        "BandwidthShaper paces only the read loop: a token bucket that slows how fast BD reads, "
        "which throttles the peer indirectly via the TCP receive window and only after the bytes "
        "have crossed the wire. Nothing asks the kernel to pace the socket, so BD cannot smooth "
        "its own egress or report whether the kernel would honour the request")


# -- 2. the constant and the capability probe --------------------------------------

def test_the_constant_is_defined_here_because_cpython_does_not_export_it():
    pacing = _pacing()
    assert not hasattr(socket, 'SO_MAX_PACING_RATE'), (
        "CPython now exports SO_MAX_PACING_RATE; prefer the stdlib constant over the local one")
    assert pacing.SO_MAX_PACING_RATE == 47, "SO_MAX_PACING_RATE is 47 in Linux asm-generic/socket.h"


def test_support_is_probed_and_never_assumed():
    """NEGATIVE CONTROL: the probe must say NO on a kernel that refuses the option. A probe that
    always answers yes would make every assertion below vacuous."""
    pacing = _pacing()
    assert pacing.pacing_supported(_FakeSock(supported=True)) is True
    assert pacing.pacing_supported(_FakeSock(supported=False)) is False
    assert pacing.pacing_supported(_FakeSock(errno_on_set=1)) is False


def test_probing_support_leaves_a_rate_already_set_in_force():
    """Asking a paced socket whether it supports pacing must not lift its ceiling: the probe
    writes back what the socket holds, so the rate set before the probe is the rate after it."""
    pacing = _pacing()
    sock = _FakeSock()
    assert pacing.set_pacing_rate(sock, 1_000_000)['applied'] is True
    assert pacing.pacing_supported(sock) is True
    held = sock.getsockopt(socket.SOL_SOCKET, pacing.SO_MAX_PACING_RATE)
    assert held == 1_000_000, f"probing for support changed the socket's pacing rate to {held}"


# -- 3. setting the rate, and reporting honestly when it did not take --------------

def test_a_rate_that_was_set_is_reported_as_applied_and_read_back():
    pacing = _pacing()
    sock = _FakeSock()
    report = pacing.set_pacing_rate(sock, 1_000_000)
    assert report['applied'] is True and report['rate_bytes_per_s'] == 1_000_000
    assert report['verified_bytes_per_s'] == 1_000_000, "the value must be read back, not assumed"
    assert report['reason'] is None
    level, optname, value = sock.calls[-1]
    assert (level, optname) == (socket.SOL_SOCKET, pacing.SO_MAX_PACING_RATE)
    assert value == 1_000_000


def test_a_rate_that_did_not_take_is_never_reported_as_applied():
    """The point of the row's honesty half: an unsupported kernel must not leave a caller
    believing its egress is paced. Silence here would be a throttle that does not throttle."""
    pacing = _pacing()
    report = pacing.set_pacing_rate(_FakeSock(supported=False), 1_000_000)
    assert report['applied'] is False
    assert report['verified_bytes_per_s'] is None
    assert 'Protocol not available' in (report['reason'] or '')
    denied = pacing.set_pacing_rate(_FakeSock(errno_on_set=1), 1_000_000)
    assert denied['applied'] is False and denied['reason']


def test_the_read_back_is_what_the_kernel_holds_not_what_was_asked():
    """verified_bytes_per_s is the kernel's answer, never an echo of the request: a kernel
    that holds a different value (clamped, or rewritten by another writer) must show it."""
    class _ClampingSock(_FakeSock):
        def getsockopt(self, level, optname, buflen=None):
            return 4096

    report = _pacing().set_pacing_rate(_ClampingSock(), 1_000_000)
    assert report['rate_bytes_per_s'] == 1_000_000
    assert report['verified_bytes_per_s'] == 4096, report


def test_a_nonsensical_rate_is_refused_before_it_reaches_the_kernel():
    pacing = _pacing()
    sock = _FakeSock()
    for bad in (-1, 0.0, None, "fast"):
        report = pacing.set_pacing_rate(sock, bad)
        assert report['applied'] is False, f"{bad!r} was accepted as a pacing rate"
        assert report['reason']
    assert sock.calls == [], "a rejected rate must not be handed to setsockopt at all"


def test_clearing_pacing_uses_the_kernels_own_unlimited_value():
    """The kernel's own "no ceiling" is the all-ones value a fresh socket holds (~0U, which the
    int getsockopt reads as -1), not 0, which is a ceiling of zero. Asked of a REAL socket: a fake
    would only echo this module's own belief back. Clearing must leave the socket as it began."""
    pacing = _pacing()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        fresh = sock.getsockopt(socket.SOL_SOCKET, pacing.SO_MAX_PACING_RATE)
        assert pacing.set_pacing_rate(sock, 1_000_000)['verified_bytes_per_s'] == 1_000_000
        report = pacing.clear_pacing(sock)
        held = sock.getsockopt(socket.SOL_SOCKET, pacing.SO_MAX_PACING_RATE)
    assert held == fresh, f"clear_pacing left the socket holding {held}; a fresh one holds {fresh}"
    assert report == {"applied": True, "rate_bytes_per_s": fresh,
                      "verified_bytes_per_s": fresh, "reason": None}, report
    # And a clear the kernel refused is reported as refused, never as done.
    refused = pacing.clear_pacing(_FakeSock(supported=False))
    assert refused["applied"] is False and "Protocol not available" in refused["reason"], refused


def test_a_read_back_the_kernel_cannot_answer_is_unknown_never_a_number():
    """None from _read_back means "unknown", which a caller must be able to tell from "wrong": a
    getsockopt that raises, or that answers something other than an int, is never a rate."""
    class _Mute(_FakeSock):
        def getsockopt(self, level, optname, buflen=None):
            raise OSError(92, "Protocol not available")

    class _Odd(_FakeSock):
        def __init__(self, answer):
            super().__init__()
            self.answer = answer

        def getsockopt(self, level, optname, buflen=None):
            return self.answer

    for sock in (_Mute(), _Odd(b"\x40\x42\x0f\x00"), _Odd(True)):
        report = _pacing().set_pacing_rate(sock, 1_000_000)
        assert (report["applied"], report["verified_bytes_per_s"]) == (True, None), report


# -- 4. the unit, owned once -------------------------------------------------------

def test_mbps_conversion_reuses_the_shapers_constant_rather_than_a_second_copy():
    pacing = _pacing()
    assert pacing.rate_from_mbps(8) == 8 * traffic_shaper.BYTES_PER_MBIT
    assert pacing.rate_from_mbps(0) == 0
    shaper = traffic_shaper.BandwidthShaper(global_mbps=8)
    sock = _FakeSock()
    report = shaper.apply_to_socket(sock)
    assert report['applied'] is True
    assert report['rate_bytes_per_s'] == 8 * traffic_shaper.BYTES_PER_MBIT
    # A shaper with no cap must not silently pace at some default.
    assert traffic_shaper.BandwidthShaper().apply_to_socket(_FakeSock())['applied'] is False


def test_a_rate_that_is_not_finite_converts_to_no_rate_instead_of_raising():
    # float() accepts "inf", "nan" and 1e308, and int() of an infinite product raises
    # OverflowError: "no limit" must come back as 0 (no rate), never as a crash.
    pacing = _pacing()
    answers = {}
    for mbps in (float("inf"), float("-inf"), float("nan"), 1e308, "1e999"):
        try:
            answers[repr(mbps)] = pacing.rate_from_mbps(mbps)
        except OverflowError as exc:
            answers[repr(mbps)] = f"OverflowError: {exc}"
    assert len(answers) == 5, answers
    assert answers == dict.fromkeys(answers, 0), answers
    # The finite path is unchanged: a large rate that fits still converts exactly.
    assert pacing.rate_from_mbps(1e6) == 1_000_000 * traffic_shaper.BYTES_PER_MBIT
    # Nor is anything that is not a number a rate: the float() failure path answers 0 as well.
    assert [pacing.rate_from_mbps(junk) for junk in ("fast", None, [8])] == [0, 0, 0]


def test_a_shaper_whose_cap_is_not_a_finite_rate_asks_the_kernel_for_nothing():
    """The shaper's setters accept an infinite cap (pace() never waits on one) and NaN;
    int() of either raised out of apply_to_socket, which must answer with a report."""
    answers = {}
    for label, shaper in (
            ("global inf", traffic_shaper.BandwidthShaper(global_mbps=float("inf"))),
            ("global json 1e999", traffic_shaper.BandwidthShaper(global_mbps=json.loads("1e999"))),
            ("site inf", traffic_shaper.BandwidthShaper(site_mbps={"s": float("inf")})),
            ("site nan", traffic_shaper.BandwidthShaper(site_mbps={"s": float("nan")}))):
        sock = _FakeSock()
        try:
            report = shaper.apply_to_socket(sock, "s")
        except (OverflowError, ValueError) as exc:
            answers[label] = f"{type(exc).__name__}: {exc}"
        else:
            answers[label] = (report["applied"], report["rate_bytes_per_s"], sock.calls)
    assert len(answers) == 4, answers
    assert answers == dict.fromkeys(answers, (False, 0, [])), answers
    # Positive control: an infinite global cap is no cap, so a finite site cap is the
    # tighter one and reaches the kernel exactly.
    shaper = traffic_shaper.BandwidthShaper(global_mbps=float("inf"), site_mbps={"s": 8})
    sock = _FakeSock()
    report = shaper.apply_to_socket(sock, "s")
    assert report["applied"] is True, report
    assert [call[2] for call in sock.calls] == [8 * traffic_shaper.BYTES_PER_MBIT]


def test_a_site_cap_that_is_not_finite_leaves_the_global_cap_in_force():
    """pace() still waits on the global bucket for a site whose own cap is infinite, so the
    kernel is asked for the global rate -- not for nothing, and never told no cap is set."""
    shaper = traffic_shaper.BandwidthShaper(global_mbps=8, site_mbps={"s": float("inf")})
    sock = _FakeSock()
    try:
        report = shaper.apply_to_socket(sock, "s")
    except (OverflowError, ValueError) as exc:
        report = {"applied": f"{type(exc).__name__}: {exc}"}
    assert report["applied"] is True, report
    assert [call[2] for call in sock.calls] == [8 * traffic_shaper.BYTES_PER_MBIT], sock.calls
    # The read loop agrees: pace() still waits on the 8 Mbit/s global bucket (200 kB: 0.2 s).
    assert shaper.pace(200_000, "s") >= 0.1


def test_the_socket_is_paced_at_the_tighter_of_the_site_and_global_caps():
    """pace() waits on the site bucket AND the global one, so a transfer never runs faster than
    the smaller cap. The kernel is handed that same ceiling, whichever of the two it is."""
    asked = {}
    for label, site_mbps, global_mbps in (("site looser", 100, 8), ("site tighter", 4, 8)):
        shaper = traffic_shaper.BandwidthShaper(global_mbps=global_mbps, site_mbps={"s": site_mbps})
        sock = _FakeSock()
        report = shaper.apply_to_socket(sock, "s")
        asked[label] = (report["applied"], [call[2] for call in sock.calls])
    assert asked == {"site looser": (True, [8 * traffic_shaper.BYTES_PER_MBIT]),
                     "site tighter": (True, [4 * traffic_shaper.BYTES_PER_MBIT])}, asked
    # The read loop agrees for the looser site: 200 kB takes 0.2 s under the 8 Mbit/s global
    # cap, where the 100 Mbit/s site bucket alone would pass it in 0.016 s.
    shaper = traffic_shaper.BandwidthShaper(global_mbps=8, site_mbps={"s": 100})
    assert shaper.pace(200_000, "s") >= 0.1


# -- 5. through the product's own caller (O1066 orphan ruling, PM 07:44Z) ----------
#
# A module plus a unit test is not a feature. The pacing request must reach a socket that
# actually carries a transfer. The only place in this product that owns such a socket AND
# knows the cap is the SOCKS carrier in download_egress, reached from
# runner_transport._hls_download_guarded -> prepare_http_proxy. These tests drive that
# real entry point against a real loopback SOCKS5 server; nothing is monkeypatched.


class _TinySocks5Server:
    """Enough of RFC 1928 to answer one CONNECT and echo. Not a proxy; a test peer."""

    def __init__(self) -> None:
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(8)
        self._listener.settimeout(5.0)
        self.port = self._listener.getsockname()[1]
        self.accepted = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            self.accepted += 1
            try:
                conn.recv(262)                                    # greeting
                conn.sendall(b"\x05\x00")                         # no auth
                conn.recv(512)                                    # CONNECT request
                conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)   # success
                while not self._stop.is_set():
                    data = conn.recv(4096)
                    if not data:
                        break
                    conn.sendall(data)
            except OSError:
                pass
            finally:
                conn.close()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._listener.close()
        except OSError:
            pass


def _connect_through(prepared) -> None:
    """Drive one HTTP CONNECT through the prepared carrier, which opens the upstream socket."""
    host, port = prepared.proxy_url.rsplit("/", 1)[-1].split(":")
    client = socket.create_connection((host, int(port)), timeout=5.0)
    try:
        client.sendall(b"CONNECT example.invalid:443 HTTP/1.1\r\n\r\n")
        assert client.recv(64).startswith(b"HTTP/1.1 200"), "the carrier refused CONNECT"
    finally:
        client.close()


def test_the_carrier_paces_the_socket_a_transfer_actually_uses():
    """The row's feature, exercised through prepare_http_proxy -- the product's own path."""
    server = _TinySocks5Server()
    prepared = download_egress.prepare_http_proxy(
        f"socks5://127.0.0.1:{server.port}", pacing_bytes_per_s=2_000_000)
    try:
        _connect_through(prepared)
        deadline = time.time() + 5.0
        while not prepared.bridge.pacing_reports and time.time() < deadline:
            time.sleep(0.05)
        assert prepared.bridge.pacing_reports, (
            "the carrier opened an upstream socket without asking the kernel to pace it")
        report = prepared.bridge.pacing_reports[-1]
        assert report["rate_bytes_per_s"] == 2_000_000
        # Honest either way: this kernel may refuse the option. What is forbidden is a
        # report that claims applied without a read-back to back it up.
        if report["applied"]:
            assert report["verified_bytes_per_s"] == 2_000_000
        else:
            assert report["reason"], "a declined pacing request must carry the kernel's reason"
    finally:
        prepared.close()
        server.stop()


def test_a_carrier_with_no_cap_asks_the_kernel_for_nothing():
    """Absence of a cap must not become a pacing request at some invented default rate."""
    server = _TinySocks5Server()
    prepared = download_egress.prepare_http_proxy(f"socks5://127.0.0.1:{server.port}")
    try:
        _connect_through(prepared)
        assert prepared.bridge.pacing_bytes_per_s == 0
        assert prepared.bridge.pacing_reports == []
    finally:
        prepared.close()
        server.stop()
    # Nor does a negative rate become one: it is "ask for nothing", like no cap at all.
    assert download_egress.SocksHttpConnectBridge(
        "socks5://127.0.0.1:9", pacing_bytes_per_s=-5).pacing_bytes_per_s == 0


def test_a_pacing_request_the_kernel_declined_is_reported_to_the_operator(capsys):
    """The row's thesis: a request the kernel refused is kept in the carrier's reports AND
    said out loud, because a throttle that silently does not throttle is the hazard."""
    bridge = download_egress.SocksHttpConnectBridge(
        "socks5://127.0.0.1:9", pacing_bytes_per_s=2_000_000)
    report = bridge._pace_upstream(_FakeSock(errno_on_set=1))
    assert report["applied"] is False and "refused by fake kernel" in report["reason"], report
    assert bridge.pacing_reports == [report]
    err = capsys.readouterr().err
    assert err.count("SO_MAX_PACING_RATE not applied (2000000 B/s)") == 1, err
    assert err.count("refused by fake kernel") == 1, err


def test_the_carrier_keeps_only_its_last_64_pacing_reports(capsys):
    """One report per upstream socket for the life of the carrier: unbounded, a long segmented
    transfer would grow the list by one entry per CONNECT for as long as it runs. A rate the
    kernel took is kept silently: only a refusal is said out loud."""
    class _Holding(_FakeSock):
        def __init__(self, n):
            super().__init__()
            self.n = n

        def getsockopt(self, level, optname, buflen=None):
            return self.n

    bridge = download_egress.SocksHttpConnectBridge(
        "socks5://127.0.0.1:9", pacing_bytes_per_s=1_000_000)
    for n in range(70):
        bridge._pace_upstream(_Holding(n))
    assert [r["verified_bytes_per_s"] for r in bridge.pacing_reports] == list(range(6, 70))
    assert capsys.readouterr().err == ""


def test_a_rate_setsockopt_cannot_carry_is_refused_before_the_syscall_not_blamed_on_the_kernel(
        capsys):
    """setsockopt passes a C int, so CPython refuses 2**31 B/s and above (a runner cap of 2048
    MB/s or more) with a TypeError before any syscall -- and the carrier then told the operator
    the KERNEL had declined. Such a rate is never handed to setsockopt, and the reason says so."""
    pacing = _pacing()
    fake = _FakeSock()
    report = pacing.set_pacing_rate(fake, 2**31)
    assert fake.calls == [], "a rate setsockopt cannot carry must not be handed to it"
    assert report["applied"] is False and "2147483647 B/s" in report["reason"], report
    # The shaper, the module's other caller: 20 000 Mbit/s is 2.5e9 B/s, refused the same way.
    shaper_report = traffic_shaper.BandwidthShaper(global_mbps=20_000).apply_to_socket(fake)
    assert fake.calls == [] and shaper_report["applied"] is False, shaper_report
    # Positive control: the largest rate the option carries still reaches the kernel exactly.
    assert pacing.set_pacing_rate(fake, 2**31 - 1)["applied"] is True
    assert [call[2] for call in fake.calls] == [2**31 - 1]
    # Through the carrier, on a real socket: the operator is told once, and not "kernel declined".
    bridge = download_egress.SocksHttpConnectBridge(
        "socks5://127.0.0.1:9", pacing_bytes_per_s=2048 * 1024 * 1024)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        carried = bridge._pace_upstream(sock)
    assert carried["applied"] is False and "TypeError" not in carried["reason"], carried
    err = capsys.readouterr().err
    assert err.count("SO_MAX_PACING_RATE not applied (2147483648 B/s)") == 1, err
    assert "kernel declined" not in err, err


def test_the_runner_hands_its_own_cap_to_the_carrier():
    """The edge that makes this reachable in production: runner_transport's HLS carrier path
    passes the site's configured cap, in BYTES, to prepare_http_proxy. Read from source so the
    test names the wiring rather than a mock of it."""
    src = inspect.getsource(runner_transport)
    call = src.split("prepared = prepare_http_proxy(")[1].split(")")[0]
    assert "pacing_bytes_per_s" in call, (
        "runner_transport opens the SOCKS carrier without handing it the configured cap, "
        "so the kernel pacing built by this row is unreachable in the product")
    assert "_current_cap_mbps" in src.split("prepared = prepare_http_proxy(")[0][-800:], (
        "the cap handed to the carrier must be the runner's own effective cap")


# -- 6. the conversion that actually runs --------------------------------------------
#
# rate_from_mbps (section 4) is a helper no product code calls. The conversion the product
# runs is the gate's own, in runner_transport._hls_download_guarded, in MEGABYTES per second.
# These tests drive that real gate on a real SiteRunner whose site proxy is SOCKS (the only
# proxy the carrier paces). A spy on prepare_http_proxy records the pacing it was handed and
# then builds the real carrier.


class _SegmentedTransfer:
    """hls_downloader as the gate reads it: ``DownloadResult`` plus a ``download`` that succeeds."""

    class DownloadResult:
        def __init__(self, ok, error="", error_detail=""):
            self.ok, self.error, self.error_detail = ok, error, error_detail

    def download(self, manifest_url, output_path, proxy_url=None, **kwargs):
        return self.DownloadResult(True)


def _drive_the_gate(monkeypatch, caps, proxy="socks5://127.0.0.1:9"):
    """Per labelled ``max_mbps``: ``(ok, [pacing handed to the carrier])``, or the exception
    that escaped a gate whose contract is to return a result and never to raise."""
    from bulk_downloader import runner as runner_mod
    from bulk_downloader import vpn_runtime

    monkeypatch.setattr(vpn_runtime, "is_vpn_required_for_site", lambda site_id: False)
    monkeypatch.setattr(vpn_runtime, "get_socks_url_for_site", lambda site_id: None)
    handed = []

    def _spy(proxy_url, pacing_bytes_per_s=0):
        handed.append(pacing_bytes_per_s)
        return download_egress.prepare_http_proxy(proxy_url, pacing_bytes_per_s=pacing_bytes_per_s)

    monkeypatch.setattr(runner_transport, "prepare_http_proxy", _spy)
    outcomes = {}
    for label, cap in caps.items():
        runner = runner_mod.SiteRunner.__new__(runner_mod.SiteRunner)
        runner.site_id = "row1066-cap"
        runner.config = {"max_mbps": cap, "proxy": proxy}
        first = len(handed)
        try:
            result = runner._hls_download_guarded(
                _SegmentedTransfer(), "https://cdn.example/master.m3u8", "/nonexistent/o.mp4")
        except Exception as exc:                  # noqa: BLE001 -- an escape IS the finding
            outcomes[label] = f"{type(exc).__name__}: {exc}"
        else:
            outcomes[label] = (result.ok, handed[first:])
    return outcomes


def test_a_cap_that_is_not_a_finite_rate_asks_the_carrier_for_no_pacing(monkeypatch):
    """float() accepts "inf" and "Infinity", json.loads reads 1e999 as inf, and 1e303 MB/s is
    finite until it is scaled to bytes. int() of each product raised OverflowError out of the
    gate, where base transferred. "No limit" must reach the carrier as 0 (ask for nothing)."""
    outcomes = _drive_the_gate(monkeypatch, {
        "5": 5, "0": 0, "inf": float("inf"), "'inf'": "inf", "'Infinity'": "Infinity",
        "json 1e999": json.loads("1e999"), "1e303": 1e303, "nan": float("nan"), "2048": 2048})
    # Positive control: a real cap still reaches the carrier exactly, in BYTES per second.
    assert outcomes.pop("5") == (True, [5 * 1024 * 1024]), outcomes
    # A finite cap too large for setsockopt is still handed on as it is; socket_pacing refuses
    # it before any syscall and the carrier says so (section 5).
    assert outcomes.pop("2048") == (True, [2**31]), outcomes
    assert outcomes == dict.fromkeys(
        ("0", "inf", "'inf'", "'Infinity'", "json 1e999", "1e303", "nan"), (True, [0])), outcomes


def test_a_cap_that_cannot_be_read_is_reported_and_the_transfer_still_runs(monkeypatch, capsys):
    """A cap the runner cannot read (a mistyped string, a list in hand-edited JSON, a JSON
    integer too large for a float) raised ValueError, TypeError or OverflowError out of the
    gate, where base transferred. Pacing is an addition to the transfer, never a precondition
    of it: the transfer runs unpaced, and the operator is told, because a cap that silently
    stopped applying is the hazard this row exists to name."""
    outcomes = _drive_the_gate(monkeypatch, {
        "'abc'": "abc", "[5]": [5], "json 10**400": json.loads("1" + "0" * 400)})
    assert outcomes == dict.fromkeys(("'abc'", "[5]", "json 10**400"), (True, [0])), outcomes
    err = capsys.readouterr().err
    assert err.count("speed cap for 'row1066-cap' is unreadable") == 3, err
    for reason in ("ValueError: could not convert string to float: 'abc'",
                   "TypeError: float() argument must be a string or a real number, not 'list'",
                   "OverflowError: int too large to convert to float"):
        assert err.count(reason) == 1, (reason, err)


def test_an_unreadable_cap_is_reported_only_where_there_is_a_carrier_to_pace(
        monkeypatch, capsys):
    """The warning says the CARRIER is not paced, and only a SOCKS proxy has a carrier. Over an
    HTTP proxy, or with no proxy at all, it would describe a socket that does not exist. The
    transfer runs unpaced either way; only the carrier's case is said out loud."""
    outcomes = _drive_the_gate(monkeypatch, {"http 'abc'": "abc"}, proxy="http://127.0.0.1:9")
    outcomes.update(_drive_the_gate(monkeypatch, {"no proxy 'abc'": "abc"}, proxy=""))
    assert outcomes == {"http 'abc'": (True, [0]), "no proxy 'abc'": (True, [0])}, outcomes
    err = capsys.readouterr().err
    assert err.count("is unreadable") == 0, err
    # Positive control: the same cap over a SOCKS carrier is reported, exactly once.
    assert _drive_the_gate(monkeypatch, {"socks 'abc'": "abc"}) == {"socks 'abc'": (True, [0])}
    assert capsys.readouterr().err.count("speed cap for 'row1066-cap' is unreadable") == 1
