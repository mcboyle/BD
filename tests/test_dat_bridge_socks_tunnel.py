"""dat-bridge: SocksHttpConnectBridge lost responses, hid aborts and had no total time limits (rows F8 F9 F10).

Loopback only: the bridge under test, a stub SOCKS5 endpoint on 127.0.0.1 and a plain client socket. The bridge's
time budgets are shortened through its own module constants. Each defect test names its row in the assertion message
and has a control beside it that passes before and after the fix.
"""

from __future__ import annotations

import select
import socket
import struct
import threading
import time

import pytest

from bulk_downloader import download_egress as de

BD_GATE_SCOPE = "module"

OK_REPLY = b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00"


def _rx(c: socket.socket, n: int) -> bytes:
    b = b""
    while len(b) < n:
        d = c.recv(n - len(b))
        if not d:
            raise OSError("eof")
        b += d
    return b


class _Socks:
    """Stub SOCKS5 endpoint. `after(conn, dest)` runs once the CONNECT is accepted; `reply_gap` drips the reply."""

    def __init__(self, after, reply_gap: float = 0.0):
        self.after, self.reply_gap = after, reply_gap
        self.dests: list[tuple[str, int]] = []
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        try:
            _v, n = _rx(c, 2)
            _rx(c, n)
            c.sendall(b"\x05\x00")
            _v, _cmd, _r, at = _rx(c, 4)
            host = socket.inet_ntoa(_rx(c, 4)) if at == 1 else _rx(c, _rx(c, 1)[0]).decode() if at == 3 else "v6"
            if at == 4:
                _rx(c, 16)
            self.dests.append((host, int.from_bytes(_rx(c, 2), "big")))
            for i in range(len(OK_REPLY)):
                c.sendall(OK_REPLY[i : i + 1])
                if self.reply_gap:
                    time.sleep(self.reply_gap)
            self.after(c, self.dests[-1])
        except OSError:
            pass
        finally:
            try:
                c.close()
            except OSError:
                pass

    def close(self):
        self.srv.close()


@pytest.fixture
def rig(monkeypatch):
    made = []

    def make(after, *, io_timeout=5.0, idle=30.0, reply_gap=0.0):
        monkeypatch.setattr(de, "_IO_TIMEOUT_S", io_timeout)
        monkeypatch.setattr(de, "_TUNNEL_IDLE_S", idle, raising=False)
        socks = _Socks(after, reply_gap)
        bridge = de.SocksHttpConnectBridge(f"socks5://127.0.0.1:{socks.port}")
        bridge.start()
        made.append((bridge, socks))
        return bridge, socks

    yield make
    for bridge, socks in made:
        bridge.stop()
        socks.close()


def _connect(bridge, target="example.test:443", send=True) -> socket.socket:
    c = socket.create_connection(("127.0.0.1", bridge.listen_port), timeout=10)
    if send:
        c.sendall(b"CONNECT " + target.encode() + b" HTTP/1.1\r\nHost: x\r\n\r\n")
    return c


def _status(c: socket.socket) -> str:
    b = b""
    while b"\r\n\r\n" not in b:
        d = c.recv(1)  # one byte at a time: tunnel bytes may follow the header in the same segment
        if not d:
            break
        b += d
    return b.split(b"\r\n", 1)[0].decode("latin1")


def _drain(c: socket.socket, seconds: float = 8.0) -> tuple[bytes, str]:
    """Everything the client receives, and how the stream ended: 'EOF', 'RESET' or 'STILL-OPEN'."""
    c.settimeout(seconds)
    got = b""
    try:
        while True:
            d = c.recv(65536)
            if not d:
                return got, "EOF"
            got += d
    except ConnectionResetError:
        return got, "RESET"
    except socket.timeout:
        return got, "STILL-OPEN"


def _hold(c, _dest):
    try:
        c.recv(1)
    except OSError:
        pass


# ── F8: one side's EOF ends one direction, not the tunnel ───────────


def _answer_after_eof(c, _dest):
    req = b""
    while True:
        d = c.recv(4096)
        if not d:
            break
        req += d
    time.sleep(0.5)
    c.sendall(b"RESPONSE-TO:" + req)


def test_f8_client_that_half_closes_still_gets_the_response(rig):
    bridge, _ = rig(_answer_after_eof)
    c = _connect(bridge)
    assert _status(c).split()[1] == "200"
    c.sendall(b"REQUEST")
    c.shutdown(socket.SHUT_WR)
    got, end = _drain(c)
    assert got == b"RESPONSE-TO:REQUEST", f"DATBR-F8-HALF-CLOSE: client received {got!r}, stream ended {end}"
    assert end == "EOF", f"DATBR-F8-HALF-CLOSE: the finished exchange ended as {end}"


def test_f8_control_without_a_half_close_the_response_arrives(rig):
    def answer(c, _dest):
        c.recv(100)
        time.sleep(0.3)
        c.sendall(b"RESPONSE")

    bridge, _ = rig(answer)
    c = _connect(bridge)
    _status(c)
    c.sendall(b"REQUEST")
    assert _drain(c) == (b"RESPONSE", "EOF")


# ── F9: an aborted upstream is not a normal end ─────────────────────


def _send_5000_then(reset: bool):
    def after(c, _dest):
        c.sendall(b"z" * 5000)
        time.sleep(0.3)
        if reset:
            c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))

    return after


def test_f9_upstream_reset_reaches_the_client_as_a_reset(rig):
    bridge, _ = rig(_send_5000_then(reset=True))
    c = _connect(bridge)
    _status(c)
    got, end = _drain(c)
    assert end == "RESET", f"DATBR-F9-RESET-AS-EOF: upstream reset after 5000 B reached the client as {end} ({len(got)} B)"


def test_f9_control_upstream_clean_close_is_a_clean_eof_with_every_byte(rig):
    bridge, _ = rig(_send_5000_then(reset=False))
    c = _connect(bridge)
    _status(c)
    got, end = _drain(c)
    assert (len(got), end) == (5000, "EOF")


# ── F10: total budgets, an idle limit, and a strict CONNECT authority ─


def _ended_within(c: socket.socket, seconds: float) -> bool:
    readable, _, _ = select.select([c], [], [], seconds)
    return bool(readable)


def test_f10_a_dripped_connect_header_is_cut_off_at_the_total_budget(rig):
    bridge, socks = rig(_hold, io_timeout=1.0)
    c = _connect(bridge, send=False)
    stop = threading.Event()

    def drip():
        for ch in b"CONNECT example.test:443 HTTP/1.1\r\nHost: x\r\n":
            if stop.is_set():
                return
            try:
                c.sendall(bytes([ch]))
            except OSError:
                return
            time.sleep(0.3)

    threading.Thread(target=drip, daemon=True).start()
    ended = _ended_within(c, 3.0)
    stop.set()
    assert ended, "DATBR-F10-HEADER-DRIP: a client dripping 1 byte / 0.3 s is still held 3 s into a 1 s budget"
    assert socks.dests == []


def test_f10_a_dripped_socks_handshake_is_refused_at_the_total_budget(rig):
    bridge, _ = rig(_hold, io_timeout=1.0, reply_gap=0.3)  # the reply alone takes 3 s
    c = _connect(bridge)
    t0 = time.monotonic()
    status = _status(c)
    took = time.monotonic() - t0
    assert status.split()[1:2] == ["502"] and took < 2.5, (
        f"DATBR-F10-SOCKS-DRIP: {status!r} after {took:.2f}s with a 1 s budget")


def test_f10_an_idle_tunnel_is_ended_and_ended_as_an_abort(rig):
    bridge, _ = rig(_hold, idle=1.0)
    c = _connect(bridge)
    assert _status(c).split()[1] == "200"
    got, end = _drain(c, seconds=4.0)
    assert end == "RESET", f"DATBR-F10-IDLE-FOREVER: a tunnel with no byte for 4 s (limit 1 s) ended as {end}"


def test_f10_control_a_tunnel_that_keeps_moving_bytes_is_not_idle(rig):
    def trickle(c, _dest):
        for _ in range(6):
            c.sendall(b"k")
            time.sleep(0.4)

    bridge, _ = rig(trickle, idle=1.0)
    c = _connect(bridge)
    _status(c)
    got, end = _drain(c, seconds=6.0)
    assert (got, end) == (b"k" * 6, "EOF"), f"DATBR-F10-IDLE-CUTS-ACTIVE: 6 bytes over 2.4 s, limit 1 s: {got!r} {end}"


@pytest.mark.parametrize("target,diag", [("u:p@h.test:443", "DATBR-F10-USERINFO-FORWARDED"),
                                         ("h.test:+443", "DATBR-F10-PORT-NOT-DIGITS"),
                                         ("h.test:4_4_3", "DATBR-F10-PORT-NOT-DIGITS")])  # fmt: skip
def test_f10_a_malformed_connect_authority_is_refused_before_any_socks_request(rig, target, diag):
    bridge, socks = rig(_hold)
    c = _connect(bridge, target)
    status = _status(c)
    time.sleep(0.2)
    assert status.split()[1:2] == ["502"] and socks.dests == [], f"{diag}: {target!r} -> {status!r}, SOCKS saw {socks.dests}"


@pytest.mark.parametrize("bad", ["u:p@h.test:443", "@h.test:443", "h.test:+443", "h.test:-1", "h.test:4_4_3", "h.test: 443",
                                 "h.test:٤٤٣", "h test:443", "h.test:", ":443", "h.test:0", "h.test:65536",
                                 "[::1]:+80", "[::1"])  # fmt: skip
def test_f10_connect_authority_refuses(bad):
    try:
        got = de._connect_authority(bad)
    except ValueError:
        return
    pytest.fail(f"DATBR-F10-AUTHORITY-ACCEPTED: {bad!r} -> {got!r}")


def test_f10_control_connect_authority_accepts_the_usual_forms():
    a = de._connect_authority
    assert a("h.test:443") == ("h.test", 443) and a("h.test") == ("h.test", 443) and a(" h.test:8443 ") == ("h.test", 8443)
    assert a("[::1]:8443") == ("::1", 8443) and a("[2001:db8::1]") == ("2001:db8::1", 443) and a("10.0.0.1:1") == ("10.0.0.1", 1)


def test_control_a_tunnel_relays_both_ways_and_stop_ends_it(rig):
    def echo(c, _dest):
        while True:
            d = c.recv(4096)
            if not d:
                return
            c.sendall(d.upper())

    bridge, socks = rig(echo)
    c = _connect(bridge, "example.test:8443")
    assert _status(c).split()[1] == "200" and socks.dests == [("example.test", 8443)]
    c.sendall(b"abc")
    c.settimeout(5)
    assert c.recv(10) == b"ABC"
    bridge.stop()
    assert _drain(c, seconds=5.0)[1] in ("EOF", "RESET")
