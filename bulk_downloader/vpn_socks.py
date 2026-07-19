"""v3.43.60: Minimal SOCKS5 CONNECT proxy for VPN-bound outbound traffic.

Used by vpn_wireguard and vpn_openvpn backends. Each tunnel gets its own
SocksProxy instance listening on 127.0.0.1:<allocated_port>. Outbound
connections from the proxy are made with bind((tunnel_iface_ip, 0)) before
connect() — that forces the OS to route the connection through the VPN
interface, regardless of the system routing table.

Why pure Python:
  - No native deps (microsocks would need a build step / bundled binary)
  - No new pip deps
  - We only need CONNECT (no UDP ASSOCIATE, no BIND), no auth (loopback only)
  - 200 LOC handles every case workers actually use

Limitations (acceptable for this use case):
  - No SOCKS5 UDP relay (workers do HTTPS/HTTP only)
  - No SOCKS5 authentication (binds 127.0.0.1; firewall is the auth boundary)
  - Threaded, not async (one thread per connection; fine for ≤32 workers/tunnel)
  - Domain resolution happens locally (the OS, ideally with VPN's DNS configured)

DNS leak prevention note: this proxy resolves hostnames via the system
resolver. To prevent DNS leaks, the WireGuard backend writes a DNS=
directive in the .conf so the OS routes resolver queries through the
tunnel. The leak detector (step 6) verifies this end-to-end.
"""
from __future__ import annotations

import select
import socket
import struct
import sys
import threading
import time
from typing import Optional


# ─── SOCKS5 protocol constants (RFC 1928) ───────────────────────────

SOCKS_VERSION = 5
METHOD_NO_AUTH = 0x00
METHOD_NO_ACCEPTABLE = 0xFF

CMD_CONNECT = 0x01

ATYP_IPV4 = 0x01
ATYP_DOMAIN = 0x03
ATYP_IPV6 = 0x04

REPLY_SUCCESS = 0x00
REPLY_GENERAL_FAILURE = 0x01
REPLY_NOT_ALLOWED = 0x02
REPLY_NETWORK_UNREACHABLE = 0x03
REPLY_HOST_UNREACHABLE = 0x04
REPLY_CONNECTION_REFUSED = 0x05
REPLY_TTL_EXPIRED = 0x06
REPLY_COMMAND_NOT_SUPPORTED = 0x07
REPLY_ATYP_NOT_SUPPORTED = 0x08


# ─── Tunables ───────────────────────────────────────────────────────

BUFFER_SIZE = 8192
ACCEPT_BACKLOG = 64
HANDSHAKE_TIMEOUT_S = 10
CONNECT_TIMEOUT_S = 15
# Idle timeout for an established relay before we tear it down. Long
# downloads must keep data flowing; 5 min idle is plenty.
RELAY_IDLE_TIMEOUT_S = 300


# ─── SocksProxy ─────────────────────────────────────────────────────

class SocksProxy:
    """SOCKS5 CONNECT proxy on (listen_host, listen_port).

    Every outbound socket is created with bind((outbound_bind_ip, 0)) before
    connect(), forcing traffic through the specified local IP — which, when
    set to the tunnel interface IP, routes through the VPN.
    """

    def __init__(
        self,
        listen_host: str,
        listen_port: int,
        outbound_bind_ip: Optional[str] = None,
    ) -> None:
        self.listen_host = listen_host
        self.listen_port = int(listen_port)
        self.outbound_bind_ip = outbound_bind_ip
        self._server_sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # Lightweight stats for the UI/diagnostics.
        self.bytes_out = 0   # client → remote
        self.bytes_in = 0    # remote → client
        self.connections = 0
        self.failed_handshakes = 0

    def start(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((self.listen_host, self.listen_port))
        except OSError as e:
            s.close()
            raise OSError(f"socks bind {self.listen_host}:{self.listen_port}: {e}") from e
        s.listen(ACCEPT_BACKLOG)
        s.settimeout(0.5)  # so accept() wakes up to check _stop
        self._server_sock = s
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._accept_loop,
            name=f"bd-socks-{self.listen_port}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        s = self._server_sock
        self._server_sock = None
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
        t = self._thread
        self._thread = None
        if t is not None and t.is_alive():
            t.join(timeout=2.0)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def url(self) -> str:
        return f"socks5://{self.listen_host}:{self.listen_port}"

    # ─── Internals ───────────────────────────────────────────────

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            s = self._server_sock
            if s is None:
                return
            try:
                client, addr = s.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            client.settimeout(HANDSHAKE_TIMEOUT_S)
            self.connections += 1
            t = threading.Thread(
                target=self._handle_client,
                args=(client,),
                name=f"bd-socks-conn-{self.listen_port}",
                daemon=True,
            )
            t.start()

    def _handle_client(self, client: socket.socket) -> None:
        remote: Optional[socket.socket] = None
        # Audit 2026-05 / Phase 5: per-handshake deadline prevents
        # slow-loris-style abuse where an attacker dribbles 1 byte per
        # near-timeout interval and holds a worker thread for
        # nmethods+domain length × per-recv timeout (~42 min worst case).
        # settimeout() in the accept loop only bounds individual recv()
        # calls, not the whole greeting+request exchange.
        handshake_deadline = time.time() + HANDSHAKE_TIMEOUT_S
        try:
            if not self._negotiate(client, deadline=handshake_deadline):
                self.failed_handshakes += 1
                return
            remote = self._handle_request(client, deadline=handshake_deadline)
            if remote is None:
                return
            client.settimeout(None)
            remote.settimeout(None)
            self._relay(client, remote)
        except Exception as e:
            sys.stderr.write(
                f"[vpn-socks:{self.listen_port}] handler error: {type(e).__name__}: {e}\n"
            )
        finally:
            for s in (client, remote):
                if s is not None:
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    try:
                        s.close()
                    except OSError:
                        pass

    def _negotiate(self, client: socket.socket, deadline: Optional[float] = None) -> bool:
        """Greeting: VER, NMETHODS, METHODS[NMETHODS]"""
        head = _recv_exact(client, 2, deadline=deadline)
        if head is None:
            return False
        ver, nmethods = head[0], head[1]
        if ver != SOCKS_VERSION or nmethods == 0:
            return False
        methods = _recv_exact(client, nmethods, deadline=deadline)
        if methods is None:
            return False
        if METHOD_NO_AUTH not in methods:
            client.sendall(bytes([SOCKS_VERSION, METHOD_NO_ACCEPTABLE]))
            return False
        client.sendall(bytes([SOCKS_VERSION, METHOD_NO_AUTH]))
        return True

    def _handle_request(self, client: socket.socket,
                         deadline: Optional[float] = None) -> Optional[socket.socket]:
        """Request: VER, CMD, RSV, ATYP, DST.ADDR, DST.PORT"""
        head = _recv_exact(client, 4, deadline=deadline)
        if head is None:
            return None
        ver, cmd, _, atyp = head[0], head[1], head[2], head[3]
        if ver != SOCKS_VERSION:
            return None
        if cmd != CMD_CONNECT:
            self._reply(client, REPLY_COMMAND_NOT_SUPPORTED)
            return None

        # Parse destination
        if atyp == ATYP_IPV4:
            raw = _recv_exact(client, 4, deadline=deadline)
            if raw is None:
                return None
            dest_host = socket.inet_ntoa(raw)
        elif atyp == ATYP_DOMAIN:
            length_byte = _recv_exact(client, 1, deadline=deadline)
            if length_byte is None:
                return None
            length = length_byte[0]
            raw = _recv_exact(client, length, deadline=deadline)
            if raw is None:
                return None
            dest_host = raw.decode("ascii", errors="replace")
        elif atyp == ATYP_IPV6:
            raw = _recv_exact(client, 16, deadline=deadline)
            if raw is None:
                return None
            dest_host = socket.inet_ntop(socket.AF_INET6, raw)
        else:
            self._reply(client, REPLY_ATYP_NOT_SUPPORTED)
            return None

        port_raw = _recv_exact(client, 2, deadline=deadline)
        if port_raw is None:
            return None
        dest_port = struct.unpack("!H", port_raw)[0]

        # Connect outbound with the configured local bind.
        try:
            remote = self._connect_outbound(dest_host, dest_port)
        except socket.timeout:
            self._reply(client, REPLY_TTL_EXPIRED)
            return None
        except ConnectionRefusedError:
            self._reply(client, REPLY_CONNECTION_REFUSED)
            return None
        except OSError as e:
            errno = getattr(e, "errno", 0)
            # 101 = Network unreachable, 113 = No route to host (Linux)
            code = REPLY_NETWORK_UNREACHABLE if errno in (101, 113) else REPLY_HOST_UNREACHABLE
            self._reply(client, code)
            return None

        self._reply(client, REPLY_SUCCESS)
        return remote

    def _connect_outbound(self, host: str, port: int) -> socket.socket:
        # Choose family.
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        s = socket.socket(family, socket.SOCK_STREAM)
        s.settimeout(CONNECT_TIMEOUT_S)
        if self.outbound_bind_ip:
            try:
                s.bind((self.outbound_bind_ip, 0))
            except OSError as e:
                s.close()
                raise OSError(
                    f"bind to outbound IP {self.outbound_bind_ip!r} failed: {e}"
                ) from e
        s.connect((host, port))
        return s

    def _reply(self, client: socket.socket, code: int) -> None:
        # Reply: VER, REP, RSV, ATYP=IPV4, BND.ADDR=0.0.0.0, BND.PORT=0
        try:
            client.sendall(
                bytes([SOCKS_VERSION, code, 0x00, ATYP_IPV4])
                + b"\x00\x00\x00\x00\x00\x00"
            )
        except OSError:
            pass

    def _relay(self, a: socket.socket, b: socket.socket) -> None:
        """Bidirectional relay between client and remote until either side
        closes or RELAY_IDLE_TIMEOUT_S passes with no data."""
        a_fd = a.fileno()
        b_fd = b.fileno()
        if a_fd < 0 or b_fd < 0:
            return
        deadline = time.time() + RELAY_IDLE_TIMEOUT_S
        while not self._stop.is_set() and time.time() < deadline:
            try:
                ready, _, errored = select.select([a, b], [], [a, b], 1.0)
            except (ValueError, OSError):
                return
            if errored:
                return
            if not ready:
                continue
            for s in ready:
                try:
                    data = s.recv(BUFFER_SIZE)
                except OSError:
                    return
                if not data:
                    return
                peer = b if s is a else a
                try:
                    peer.sendall(data)
                except OSError:
                    return
                if s is a:
                    self.bytes_out += len(data)
                else:
                    self.bytes_in += len(data)
                deadline = time.time() + RELAY_IDLE_TIMEOUT_S


# ─── Module-level helpers ───────────────────────────────────────────

def _recv_exact(s: socket.socket, n: int,
                 deadline: Optional[float] = None) -> Optional[bytes]:
    """Receive exactly `n` bytes. Returns None on EOF, partial, or deadline.

    Audit 2026-05 / Phase 5: `deadline` is a wallclock float (time.time()
    + budget) that bounds the TOTAL time across all recv() calls, not just
    each one individually. The socket's settimeout() applies per recv;
    without this, a slow attacker dribbling 1 byte per (timeout - 0.1)s
    can hold a handler thread forever.
    """
    buf = b""
    while len(buf) < n:
        if deadline is not None:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            # Tighten per-recv timeout to whatever budget remains, so the
            # next recv() returns quickly when we're near the deadline.
            try:
                cur = s.gettimeout()
                if cur is None or cur > remaining:
                    s.settimeout(max(0.1, remaining))
            except OSError:
                return None
        try:
            chunk = s.recv(n - len(buf))
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


__all__ = ["SocksProxy"]
