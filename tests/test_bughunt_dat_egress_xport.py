"""dat-egress-xport: deep-adapters-transport rows T F11 + F12 (bulk_downloader/egress_transport.py).

A real loopback HTTP server (threads, one port per test module) and the real UnifiedEgressClient / HttpxEgressTransport.

F11  the timeout is the budget for the whole exchange (a trickled body no longer runs to its end); responses are capped
     (max_response_bytes, declared or streamed, decoded bytes); a timeout that is not a finite number > 0 is refused as
     EgressTransportError. Control, not a defect: a close-delimited body has no length to check and is a success.
F12  an unknown policy is refused at construction; execute(timeout=None) uses the configured timeout; overlapping
     temporary_egress_client scopes restore the global client whatever the exit order; the first
     get_unified_egress_client() builds one client under concurrency.
r2 (lens R47-B REFUTE):
F1   the cap bounds MEMORY: the body is decoded here, every Content-Encoding layer in pieces of 64 KiB with its total
     output capped, so nested gzip of a few hundred bytes is refused without ever being inflated (peak traced memory
     asserted); encodings other than gzip / deflate, more than 3 layers, corrupt and truncated streams are errors.
F2   a timeout above 86400 s or one that does not fit a float (1e10, 1e300, 10**400) is EgressTransportError too.
F3   redirect hops share the one budget, and headers that arrive after the deadline are an error, not a late 200.
"""
from __future__ import annotations

import gzip
import tracemalloc
import zlib
import http.server
import socket
import socketserver
import threading
import time

import pytest

from bulk_downloader import egress_transport as et
from bulk_downloader.ssrf_transport import PINNED, PUBLIC_ONLY

BD_GATE_SCOPE = "module"
MIB = 1024 * 1024


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # noqa: D102 - quiet
        pass

    def _fixed(self, body: bytes, extra: dict | None = None) -> None:
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", str(3 * MIB))
        self.end_headers()

    def do_GET(self):  # noqa: N802
        p = self.path
        try:
            if p == "/ok":
                self._fixed(b'{"a":1}')
            elif p.startswith("/enc/"):
                enc, wire = payload(p[5:])
                self._fixed(wire, {"Content-Encoding": enc} if enc else None)
            elif p == "/echo-ae":
                self._fixed("|".join(self.headers.get_all("Accept-Encoding") or []).encode())
            elif p.startswith("/r/"):  # /r/<n>: n more redirects, 0.4 s each, then /ok
                n = int(p[3:])
                time.sleep(0.4)
                self.send_response(302)
                self.send_header("Location", f"/r/{n - 1}" if n else "/ok")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif p.startswith("/rs/"):  # /rs/<n>: n redirects of 0.4 s, then one to a peer that never answers
                n = int(p[4:])
                time.sleep(0.4)
                self.send_response(302)
                self.send_header("Location", f"/rs/{n - 1}" if n else "/stall")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif p == "/loop":
                self.send_response(302)
                self.send_header("Location", "/loop")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif p == "/hdrtrickle":  # the header block takes 3 s to arrive, a byte at a time
                self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nX-Pad: ")
                for _ in range(10):
                    self.wfile.write(b"a")
                    self.wfile.flush()
                    time.sleep(0.3)
                self.wfile.write(b"\r\n\r\n")
            elif p.startswith("/size/"):
                self._fixed(b"z" * int(p.rsplit("/", 1)[1]))
            elif p == "/stall":
                time.sleep(8)
            elif p == "/trickle":  # 12 bytes over 6 s
                self.send_response(200)
                self.send_header("Content-Length", "12")
                self.end_headers()
                for _ in range(12):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.5)
            elif p == "/declared-big":  # says 3 MiB, sends nothing more than a little
                self.send_response(200)
                self.send_header("Content-Length", str(3 * MIB))
                self.end_headers()
                self.wfile.write(b"y" * 1024)
                self.wfile.flush()
                time.sleep(3)
            elif p == "/chunked-big":  # 3 MiB, no Content-Length
                self.send_response(200)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                blk = b"c" * 65536
                for _ in range(48):
                    self.wfile.write(b"%x\r\n" % len(blk) + blk + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
            elif p == "/gzip-big":  # ~3 KiB on the wire, 3 MiB decoded
                self._fixed(gzip.compress(b"\0" * (3 * MIB)), {"Content-Encoding": "gzip"})
            elif p == "/short":  # Content-Length 1000, closed at 100
                self.send_response(200)
                self.send_header("Content-Length", "1000")
                self.end_headers()
                self.wfile.write(b"y" * 100)
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)
            elif p == "/shortchunk":
                self.send_response(200)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.wfile.write(b"64\r\n" + b"y" * 100 + b"\r\n")
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)
            elif p == "/closedelim":  # no length at all: the close IS the end of the body
                self.send_response(200)
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b"y" * 100)
                self.close_connection = True
        except OSError:
            pass


    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(303)
        self.send_header("Location", "/ok")
        self.send_header("Content-Length", "0")
        self.end_headers()


BOMB = 64 * MIB
PLAIN = bytes(range(256)) * 800  # 200 KiB that does not compress to nothing: several decode pieces
_PAYLOADS: dict = {}


def _gz(data: bytes) -> bytes:
    return gzip.compress(data, 9)


def _raw_deflate(data: bytes) -> bytes:
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    return c.compress(data) + c.flush()


def payload(name: str):
    """name -> (Content-Encoding header, wire bytes); built once."""
    if name not in _PAYLOADS:
        zeros = b"\0" * BOMB
        _PAYLOADS[name] = {
            "bomb-gzip": lambda: ("gzip", _gz(zeros)),
            "bomb-gzip-gzip": lambda: ("gzip, gzip", _gz(_gz(zeros))),
            "bomb-deflate-gzip": lambda: ("deflate, gzip", _gz(zlib.compress(zeros, 9))),
            "bomb-raw-deflate": lambda: ("deflate", _raw_deflate(zeros)),
            # the MIDDLE is 64 MiB (an empty gzip member + 64 MiB of trailing bytes), the final body is empty
            "bomb-middle": lambda: ("gzip, gzip", _gz(_gz(b"") + zeros)),
            "gzip": lambda: ("gzip", _gz(PLAIN)),
            "x-gzip": lambda: ("x-gzip", _gz(PLAIN)),
            "deflate": lambda: ("deflate", zlib.compress(PLAIN, 9)),
            "raw-deflate": lambda: ("deflate", _raw_deflate(PLAIN)),
            "gzip-deflate": lambda: ("gzip, deflate", zlib.compress(_gz(PLAIN), 9)),
            "rawdeflate-gzip": lambda: ("deflate, gzip", _gz(_raw_deflate(PLAIN))),  # only right when undone last-first
            "identity": lambda: ("identity", PLAIN),
            "plain": lambda: ("", PLAIN),
            "br": lambda: ("br", b"not brotli"),
            "four-layers": lambda: ("gzip, gzip, gzip, gzip", _gz(_gz(_gz(_gz(b"x"))))),
            "corrupt": lambda: ("gzip", _gz(PLAIN)[:500] + b"\xff" * 64 + _gz(PLAIN)[564:]),
            "truncated": lambda: ("gzip", _gz(PLAIN)[:-20]),
            "not-gzip": lambda: ("gzip", b"plainly not gzip at all"),
        }[name]()
    return _PAYLOADS[name]


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


@pytest.fixture(scope="module")
def base():
    srv = _Server(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    probe = et.UnifiedEgressClient()
    try:
        assert probe.get(url + "/ok", timeout=5).content == b'{"a":1}', "COULD NOT LOOK: loopback sink not reachable"
    finally:
        probe.close()
    yield url
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def client():
    c = et.UnifiedEgressClient()
    yield c
    c.close()


def capped(n: int) -> et.UnifiedEgressClient:
    return et.UnifiedEgressClient(et.EgressTransportConfig(max_response_bytes=n))


# ------------------------------------------------------------------------------------------------- F11 total deadline
def test_f11_a_trickled_body_is_cut_off_at_the_timeout(base, client):
    t0 = time.monotonic()
    with pytest.raises(et.EgressTransportError) as ei:
        client.get(base + "/trickle", timeout=1)
    took = time.monotonic() - t0
    assert took < 3.5, f"TRICKLE_RAN_PAST_THE_TIMEOUT: timeout=1 took {took:.1f} s"
    assert "not complete within 1 s" in str(ei.value) and ei.value.url == base + "/trickle"


def test_f11_control_a_body_inside_the_budget_is_returned_whole(base, client):
    r = client.get(base + "/trickle", timeout=20)
    assert r.status_code == 200 and r.content == b"x" * 12


# ------------------------------------------------------------------------------------------------------ F11 size cap
def test_f11_default_cap_is_64_mib():
    assert et.EgressTransportConfig().max_response_bytes == 64 * MIB == et.DEFAULT_MAX_RESPONSE_BYTES
    c = et.UnifiedEgressClient()
    try:
        assert c.transport.max_response_bytes == 64 * MIB
    finally:
        c.close()


def test_f11_declared_length_over_the_cap_is_refused_before_the_body(base):
    c = capped(MIB)
    try:
        t0 = time.monotonic()
        with pytest.raises(et.EgressTransportError, match="Content-Length 3145728 exceeds the 1048576-byte cap"):
            c.get(base + "/declared-big", timeout=10)
        assert time.monotonic() - t0 < 2, "OVERSIZE_BODY_WAITED_FOR: the declared length alone decides"
    finally:
        c.close()


@pytest.mark.parametrize("path", ["/chunked-big", "/gzip-big"])
def test_f11_streamed_or_decoded_bytes_over_the_cap_are_refused(base, path):
    c = capped(MIB)
    try:
        with pytest.raises(et.EgressTransportError, match="body exceeds the 1048576-byte cap"):
            c.get(base + path, timeout=20)
    finally:
        c.close()


def test_f11_cap_boundary_and_head(base):
    c = capped(4096)
    try:
        assert len(c.get(base + "/size/4096", timeout=10).content) == 4096
        with pytest.raises(et.EgressTransportError, match="exceeds the 4096-byte cap"):
            c.get(base + "/size/4097", timeout=10)
        r = c.request("HEAD", base + "/ok", timeout=10)  # declares 3 MiB, carries no body
        assert r.status_code == 200 and r.content == b""
    finally:
        c.close()


@pytest.mark.parametrize("bad", [0, -1, True, None, "4096", 1.5])
def test_f11_a_cap_that_is_not_a_positive_int_is_refused_at_construction(bad):
    with pytest.raises(ValueError, match="max_response_bytes"):
        et.UnifiedEgressClient(et.EgressTransportConfig(max_response_bytes=bad))


# ------------------------------------------------------------------------------------------------ F11 short bodies
@pytest.mark.parametrize("path", ["/short", "/shortchunk"])
def test_f11_control_a_body_shorter_than_its_framing_is_an_error(base, client, path):
    with pytest.raises(et.EgressTransportError):
        client.get(base + path, timeout=5)


def test_f11_control_close_delimited_body_is_complete_at_the_close(base, client):
    # FOUND NONE as a defect: no Content-Length and no chunking, so the close is the only end marker HTTP gives.
    r = client.get(base + "/closedelim", timeout=5)
    assert r.status_code == 200 and r.content == b"y" * 100


# --------------------------------------------------------------------------------------------- F11 timeout contract
BAD_TIMEOUTS = [-1, 0, float("nan"), float("inf"), float("-inf"), "2", True, [2], b"2"]


@pytest.mark.parametrize("bad", BAD_TIMEOUTS, ids=[repr(b) for b in BAD_TIMEOUTS])
def test_f11_an_invalid_timeout_is_an_egress_transport_error(base, client, bad):
    for call in (lambda: client.get(base + "/ok", timeout=bad),
                 lambda: client.transport.execute("GET", base + "/ok", timeout=bad)):
        with pytest.raises(et.EgressTransportError, match="invalid timeout") as ei:
            call()
        assert ei.value.url == base + "/ok"


def test_f11_an_invalid_configured_timeout_is_an_egress_transport_error(base):
    c = et.UnifiedEgressClient(et.EgressTransportConfig(timeout="2"))
    try:
        with pytest.raises(et.EgressTransportError, match="invalid timeout '2'"):
            c.get(base + "/ok")
        with pytest.raises(et.EgressTransportError, match="invalid timeout '2'"):
            c.transport.execute("GET", base + "/ok")
    finally:
        c.close()


def test_f11_the_client_refuses_an_invalid_timeout_before_any_transport_sees_it():
    seen = []

    class Recording(et.BaseEgressTransport):
        def execute(self, method, url, headers=None, data=None, timeout=None):
            seen.append(timeout)
            return et.EgressResponse(200, {}, b"", url)

    c = et.UnifiedEgressClient(transport=Recording())
    sink = "http://192.0.2.1/x"  # TEST-NET-1 literal: no hostname, so nothing here can reach a resolver
    for bad in (-1, float("nan"), "2"):
        with pytest.raises(et.EgressTransportError, match="invalid timeout"):
            c.get(sink, timeout=bad)
    assert seen == []
    c.get(sink)
    assert seen == [30.0]


@pytest.mark.parametrize("good", [1, 2.5, 30])
def test_f11_control_valid_timeouts_work(base, client, good):
    assert client.get(base + "/ok", timeout=good).content == b'{"a":1}'


# ------------------------------------------------------------------------------------------------------- F12 policy
def test_f12_an_unknown_policy_is_refused_at_construction():
    with pytest.raises(ValueError, match="unknown egress policy 'nonsense'"):
        et.UnifiedEgressClient(et.EgressTransportConfig(policy="nonsense"))
    with pytest.raises(ValueError, match="unknown egress policy 'PINNED'"):
        et.HttpxEgressTransport(policy="PINNED")  # the constant's NAME is not the policy


@pytest.mark.parametrize("policy", [PINNED, PUBLIC_ONLY])
def test_f12_control_known_policies_build_and_report_themselves(policy):
    c = et.UnifiedEgressClient(et.EgressTransportConfig(policy=policy))
    try:
        assert c.transport.policy == policy
    finally:
        c.close()


# ------------------------------------------------------------------------------------------- F12 configured timeout
def test_f12_execute_without_a_timeout_uses_the_configured_one(base):
    c = et.UnifiedEgressClient(et.EgressTransportConfig(timeout=1.0))
    try:
        assert c.transport.default_timeout == 1.0
        t0 = time.monotonic()
        with pytest.raises(et.EgressTransportError):
            c.transport.execute("GET", base + "/stall")
        took = time.monotonic() - t0
        assert took < 4, f"CONFIGURED_TIMEOUT_IGNORED: execute(timeout=None) took {took:.1f} s with config.timeout=1"
    finally:
        c.close()


# ---------------------------------------------------------------------------------------------- F12 global client
class _Fake(et.BaseEgressTransport):
    def execute(self, method, url, headers=None, data=None, timeout=None):
        return et.EgressResponse(200, {}, b"", url)


def _fake() -> et.UnifiedEgressClient:
    return et.UnifiedEgressClient(transport=_Fake())


@pytest.fixture()
def global_state(monkeypatch):
    g0 = _fake()
    monkeypatch.setattr(et, "_GLOBAL_CLIENT", g0)
    yield g0
    assert getattr(et, "_OVERRIDES", []) == [], "a temporary scope was left registered"


@pytest.mark.parametrize("first_out", ["a", "b"])
def test_f12_overlapping_scopes_in_two_threads_restore_the_global_client(global_state, first_out):
    g0, a, b = global_state, _fake(), _fake()
    go = {"a": threading.Event(), "b": threading.Event()}
    a_in, b_in = threading.Event(), threading.Event()

    def ta():
        with et.temporary_egress_client(a):
            a_in.set()
            go["a"].wait(10)

    def tb():
        a_in.wait(10)
        with et.temporary_egress_client(b):
            b_in.set()
            go["b"].wait(10)

    th = {"a": threading.Thread(target=ta), "b": threading.Thread(target=tb)}
    for t in th.values():
        t.start()
    assert b_in.wait(10)
    assert et.get_unified_egress_client() is b, "the newest open scope decides"
    go[first_out].set()
    th[first_out].join(10)
    survivor = b if first_out == "a" else a
    assert et.get_unified_egress_client() is survivor, f"SCOPE_STILL_OPEN_LOST_ITS_CLIENT after {first_out} exited"
    other = "b" if first_out == "a" else "a"
    go[other].set()
    th[other].join(10)
    assert et.get_unified_egress_client() is g0, "STALE_OVERRIDE_LEFT_INSTALLED after both scopes exited"


def test_f12_control_nested_scopes_and_an_exception_restore_in_order(global_state):
    g0, a, b = global_state, _fake(), _fake()
    with et.temporary_egress_client(a):
        with pytest.raises(RuntimeError):
            with et.temporary_egress_client(b):
                assert et.get_unified_egress_client() is b
                raise RuntimeError("boom")
        assert et.get_unified_egress_client() is a
        with et.temporary_egress_client(a):  # the same client twice: two scopes, told apart
            assert et.get_unified_egress_client() is a
        assert et.get_unified_egress_client() is a
    assert et.get_unified_egress_client() is g0


def test_f12_control_scope_over_an_unbuilt_global_restores_unbuilt(monkeypatch):
    monkeypatch.setattr(et, "_GLOBAL_CLIENT", None)
    a = _fake()
    with et.temporary_egress_client(a):
        assert et.get_unified_egress_client() is a
    assert et._GLOBAL_CLIENT is None


def test_f12_first_get_builds_one_client_under_concurrency(monkeypatch):
    built = []
    real_init = et.UnifiedEgressClient.__init__

    def slow_init(self, config=None, transport=None):
        built.append(self)
        time.sleep(0.05)  # hold every racing builder inside the constructor
        real_init(self, config=config, transport=transport or _Fake())

    monkeypatch.setattr(et, "_GLOBAL_CLIENT", None)
    monkeypatch.setattr(et.UnifiedEgressClient, "__init__", slow_init)
    got = [None] * 16
    barrier = threading.Barrier(16)

    def mk(i):
        barrier.wait(10)
        got[i] = et.get_unified_egress_client()

    th = [threading.Thread(target=mk, args=(i,)) for i in range(16)]
    for t in th:
        t.start()
    for t in th:
        t.join(10)
    assert len(built) == 1 and len({id(g) for g in got}) == 1, f"GLOBAL_CLIENT_BUILT_{len(built)}_TIMES by 16 first callers"


# ------------------------------------------------------------------------------- r2 F1: the cap bounds memory
BOMBS = ["bomb-gzip", "bomb-gzip-gzip", "bomb-deflate-gzip", "bomb-raw-deflate", "bomb-middle"]


@pytest.mark.parametrize("name", BOMBS)
def test_r2_f1_a_compression_bomb_is_refused_without_being_inflated(base, name):
    enc, wire = payload(name)
    assert len(wire) < MIB, "fixture: the bomb is small on the wire"
    c = capped(MIB)
    try:
        c.get(base + "/ok", timeout=10)  # connection and imports warm before the measurement
        tracemalloc.start()
        try:
            with pytest.raises(et.EgressTransportError, match="exceeds the 1048576-byte cap"):
                c.get(base + "/enc/" + name, timeout=60)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
    finally:
        c.close()
    assert peak < 8 * MIB, f"CAP_DID_NOT_BOUND_MEMORY {name} ({enc}): peak {peak / MIB:.0f} MiB traced for a 1 MiB cap"


@pytest.mark.parametrize("name", ["gzip", "x-gzip", "deflate", "raw-deflate", "gzip-deflate", "rawdeflate-gzip", "identity", "plain"])
def test_r2_f1_control_encoded_bodies_decode_to_the_same_bytes(base, client, name):
    r = client.get(base + "/enc/" + name, timeout=20)
    assert r.status_code == 200 and r.content == PLAIN, f"DECODE_MISMATCH {name}: {len(r.content)} bytes"


def test_r2_f1_control_decoded_size_exactly_at_the_cap_is_returned(base):
    c = capped(len(PLAIN))
    try:
        assert c.get(base + "/enc/gzip", timeout=20).content == PLAIN
    finally:
        c.close()
    c = capped(len(PLAIN) - 1)
    try:
        with pytest.raises(et.EgressTransportError, match="exceeds the 204799-byte cap"):
            c.get(base + "/enc/gzip", timeout=20)
    finally:
        c.close()


@pytest.mark.parametrize("name,why", [("br", "unsupported Content-Encoding 'br'"), ("four-layers", "4 stacked Content-Encodings refused"),
                                      ("corrupt", "undecodable gzip body"), ("not-gzip", "undecodable gzip body"),
                                      ("truncated", "truncated gzip body")])
def test_r2_f1_an_encoding_that_cannot_be_decoded_safely_is_an_error(base, client, name, why):
    with pytest.raises(et.EgressTransportError, match=why):
        client.get(base + "/enc/" + name, timeout=20)
    assert client.get(base + "/ok", timeout=10).status_code == 200, "the client stays usable after a refusal"


def test_r2_f1_only_decodable_encodings_are_asked_for_unless_the_caller_says(base, client):
    # as if this httpx advertised encodings the bounded decoder cannot undo (it does when brotli / zstd are installed)
    client.transport._client.headers["Accept-Encoding"] = "gzip, deflate, br, zstd"
    assert client.get(base + "/echo-ae", timeout=10).content == b"gzip, deflate"
    for key in ("accept-encoding", "Accept-Encoding"):  # the caller's own choice goes out alone, whatever its case
        assert client.get(base + "/echo-ae", headers={key: "br"}, timeout=10).content == b"br"


# ------------------------------------------------------------------------------ r2 F2: timeouts a float cannot carry
HUGE = [1e10, 1e19, 1e300, 10 ** 400, 86400.5, 10 ** 6]


@pytest.mark.parametrize("bad", HUGE, ids=["1e10", "1e19", "1e300", "10**400", "86400.5", "10**6"])
def test_r2_f2_a_timeout_past_the_bound_is_an_egress_transport_error(base, client, bad):
    for call in (lambda: client.get(base + "/ok", timeout=bad),
                 lambda: client.transport.execute("GET", base + "/ok", timeout=bad)):
        with pytest.raises(et.EgressTransportError, match="invalid timeout"):
            call()
    c = et.UnifiedEgressClient(et.EgressTransportConfig(timeout=bad))
    try:
        with pytest.raises(et.EgressTransportError, match="invalid timeout"):
            c.get(base + "/ok")
    finally:
        c.close()


def test_r2_f2_control_the_bound_itself_and_an_unset_configured_timeout_work(base):
    assert et.MAX_TIMEOUT_SECONDS == 86400.0
    c = et.UnifiedEgressClient(et.EgressTransportConfig(timeout=None))  # BASE behaviour: None = the 30 s default
    try:
        assert c.get(base + "/ok").status_code == 200
        assert c.get(base + "/ok", timeout=et.MAX_TIMEOUT_SECONDS).status_code == 200
        assert c.transport.execute("GET", base + "/ok").status_code == 200
    finally:
        c.close()


# ------------------------------------------------------------------- r2 F3: hops and late headers inside the budget
def test_r2_f3_redirect_hops_share_one_budget(base, client):
    t0 = time.monotonic()
    with pytest.raises(et.EgressTransportError, match="not complete within 1 s|Transport failure"):
        client.get(base + "/r/6", timeout=1)  # 7 hops x 0.4 s
    took = time.monotonic() - t0
    assert took < 2.0, f"REDIRECT_HOPS_EACH_GOT_A_FULL_TIMEOUT: timeout=1 took {took:.1f} s"


def test_r2_f3_a_hop_that_stalls_gets_only_what_is_left_of_the_budget(base, client):
    t0 = time.monotonic()
    with pytest.raises(et.EgressTransportError):
        client.get(base + "/rs/1", timeout=1)  # 0.4 s + 0.4 s of redirects, then a peer that never answers
    took = time.monotonic() - t0
    assert took < 1.5, f"STALLED_HOP_GOT_A_FULL_TIMEOUT: timeout=1 took {took:.1f} s"


def test_r2_f3_headers_that_arrive_after_the_deadline_are_an_error(base, client):
    with pytest.raises(et.EgressTransportError, match="not complete within 1 s"):
        client.get(base + "/hdrtrickle", timeout=1)
    r = client.get(base + "/hdrtrickle", timeout=20)  # control: inside the budget it is a 200 (with an empty body)
    assert r.status_code == 200 and r.content == b""


def test_r2_f3_control_redirects_are_still_followed(base, client):
    r = client.get(base + "/r/2", timeout=20)
    assert r.status_code == 200 and r.content == b'{"a":1}' and r.url == base + "/ok"
    r = client.post(base + "/anything", data=b"x=1", timeout=20)  # 303 -> GET /ok
    assert r.status_code == 200 and r.url == base + "/ok"
    with pytest.raises(et.EgressTransportError, match="more than 20 redirects"):
        client.get(base + "/loop", timeout=5)
