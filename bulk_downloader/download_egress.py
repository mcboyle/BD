"""Fail-closed proxy selection and subprocess-compatible egress carriers.

The browser path (``runner`` launch / ``vpn_runtime.playwright_proxy_for_site``)
already routes chromium through the per-site VPN tunnel and fails closed when a
``vpn_required`` site's tunnel is unavailable. The in-process download clients
(``curl_cffi`` / ``httpx`` / ``multi_conn``) historically derived their proxy
*only* from the explicit per-site ``proxy`` config and ignored the tunnel -- so a
``vpn_required`` site whose tunnel was down could egress the payload bytes on the
clear interface.

``effective_download_proxy`` mirrors the browser's selection for the download
clients by reusing the SAME resolver (``vpn_runtime.get_socks_url_for_site``),
which already encodes the operator's posture. The resolver is injected so this
stays a pure, side-effect-free decision -- the production resolver brings the
tunnel up on demand and is therefore not safe to call from unit tests.
"""
import ipaddress
import os
import select
import socket
import threading
from typing import Callable, Optional
from urllib.parse import urlsplit


_PROXY_ENV_VARS = (
    "http_proxy", "https_proxy", "all_proxy", "no_proxy", "ftp_proxy",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "FTP_PROXY",
)
_HEADER_LIMIT = 65536
_IO_TIMEOUT_S = 15.0


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise OSError("SOCKS peer closed during handshake")
        data.extend(chunk)
    return bytes(data)


def _connect_authority(value: str) -> tuple[str, int]:
    text = value.strip()
    if text.startswith("["):
        close = text.find("]")
        if close < 0:
            raise ValueError("malformed IPv6 CONNECT authority")
        host = text[1:close]
        suffix = text[close + 1:]
        port = int(suffix[1:]) if suffix.startswith(":") else 443
    else:
        host, marker, port_text = text.rpartition(":")
        if not marker:
            host, port = text, 443
        else:
            port = int(port_text)
    if not host or not 1 <= port <= 65535:
        raise ValueError("invalid CONNECT destination")
    return host, port


class SocksHttpConnectBridge:
    """Loopback HTTP CONNECT listener whose outbound socket uses SOCKS5."""

    def __init__(self, socks_url: str) -> None:
        parsed = urlsplit((socks_url or "").strip())
        if parsed.scheme.lower() not in {"socks5", "socks5h"}:
            raise ValueError("upstream proxy is not SOCKS5")
        if parsed.username or parsed.password or not parsed.hostname or not parsed.port:
            raise ValueError("SOCKS carrier requires host and port without credentials")
        self._socks_host = parsed.hostname
        self._socks_port = parsed.port
        self._listener: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._clients: set[socket.socket] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.listen_port = 0

    def start(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen(64)
            listener.settimeout(0.2)
        except Exception:
            listener.close()
            raise
        self.listen_port = int(listener.getsockname()[1])
        self._listener = listener
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._accept_loop,
            name=f"bd-http-socks-{self.listen_port}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                client.close()
            except OSError:
                pass
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def url(self) -> str:
        if not self.listen_port:
            raise RuntimeError("HTTP-to-SOCKS bridge has not started")
        return f"http://127.0.0.1:{self.listen_port}"

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            listener = self._listener
            if listener is None:
                return
            try:
                client, _address = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            client.settimeout(_IO_TIMEOUT_S)
            with self._lock:
                self._clients.add(client)
            threading.Thread(
                target=self._handle_client, args=(client,),
                name=f"bd-http-socks-client-{self.listen_port}",
                daemon=True).start()

    def _read_connect(self, client: socket.socket) -> tuple[str, int, bytes]:
        payload = bytearray()
        while b"\r\n\r\n" not in payload:
            chunk = client.recv(min(8192, _HEADER_LIMIT + 1 - len(payload)))
            if not chunk:
                raise OSError("HTTP proxy client closed before CONNECT")
            payload.extend(chunk)
            if len(payload) > _HEADER_LIMIT:
                raise ValueError("HTTP proxy header exceeds limit")
        header, remainder = bytes(payload).split(b"\r\n\r\n", 1)
        method, target, _version = header.split(b"\r\n", 1)[0].decode("ascii").split(" ", 2)
        if method.upper() != "CONNECT":
            raise ValueError("SOCKS carrier accepts HTTP CONNECT only")
        host, port = _connect_authority(target)
        return host, port, remainder

    def _socks_connect(self, host: str, port: int) -> socket.socket:
        upstream = socket.create_connection(
            (self._socks_host, self._socks_port), timeout=_IO_TIMEOUT_S)
        try:
            upstream.sendall(b"\x05\x01\x00")
            if _recv_exact(upstream, 2) != b"\x05\x00":
                raise OSError("SOCKS endpoint refused no-auth negotiation")
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                encoded = host.encode("idna")
                if not encoded or len(encoded) > 255:
                    raise ValueError("SOCKS destination hostname is invalid")
                packed = b"\x03" + bytes([len(encoded)]) + encoded
            else:
                packed = (b"\x01" if address.version == 4 else b"\x04") + address.packed
            upstream.sendall(b"\x05\x01\x00" + packed + port.to_bytes(2, "big"))
            reply = _recv_exact(upstream, 4)
            if reply[:2] != b"\x05\x00":
                raise OSError(f"SOCKS CONNECT failed with reply {reply[1]}")
            lengths = {1: 4, 4: 16}
            if reply[3] == 3:
                _recv_exact(upstream, _recv_exact(upstream, 1)[0])
            elif reply[3] in lengths:
                _recv_exact(upstream, lengths[reply[3]])
            else:
                raise OSError("SOCKS endpoint returned an unknown address type")
            _recv_exact(upstream, 2)
            upstream.settimeout(None)
            return upstream
        except Exception:
            upstream.close()
            raise

    def _handle_client(self, client: socket.socket) -> None:
        upstream = None
        connected = False
        try:
            host, port, remainder = self._read_connect(client)
            upstream = self._socks_connect(host, port)
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            connected = True
            if remainder:
                upstream.sendall(remainder)
            client.settimeout(None)
            peers = (client, upstream)
            while not self._stop.is_set():
                readable, _, _ = select.select(peers, (), (), 0.2)
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (upstream if source is client else client).sendall(data)
        except Exception:
            if not connected:
                try:
                    client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                except OSError:
                    pass
        finally:
            for peer in (client, upstream):
                if peer is not None:
                    try:
                        peer.close()
                    except OSError:
                        pass
            with self._lock:
                self._clients.discard(client)

__all__ = [
    "EgressCarrierError", "PreparedHttpProxy", "effective_download_proxy",
    "prepare_http_proxy",
]


class EgressCarrierError(RuntimeError):
    """A resolved proxy cannot safely be carried by an HTTP-only subprocess."""


class PreparedHttpProxy:
    """HTTP proxy URL plus the optional loopback bridge that owns it."""

    def __init__(self, proxy_url: Optional[str], bridge=None) -> None:
        self.proxy_url = proxy_url
        self._bridge = bridge

    def close(self) -> None:
        bridge, self._bridge = self._bridge, None
        if bridge is not None:
            bridge.stop()

    def subprocess_env(self) -> dict:
        """Return a spawn environment containing only this prepared proxy."""
        env = dict(os.environ)
        for name in _PROXY_ENV_VARS:
            env.pop(name, None)
        if self.proxy_url:
            env["http_proxy"] = self.proxy_url
            env["https_proxy"] = self.proxy_url
        return env


def prepare_http_proxy(proxy_url: Optional[str]) -> PreparedHttpProxy:
    """Make a resolved egress proxy usable by an HTTP-proxy-only subprocess.

    HTTP(S) and empty proxy decisions pass through.  SOCKS5 decisions start a
    loopback HTTP proxy whose outbound CONNECT is made through the exact SOCKS
    endpoint.  Any unsupported scheme or bridge startup uncertainty refuses;
    callers must not fall back to an unproxied spawn.
    """
    proxy = (proxy_url or "").strip()
    if not proxy or proxy.lower().startswith(("http://", "https://")):
        return PreparedHttpProxy(proxy or None)
    if proxy.lower().startswith(("socks5://", "socks5h://")):
        bridge = None
        try:
            bridge = SocksHttpConnectBridge(proxy)
            bridge.start()
            if not bridge.is_alive():
                raise OSError("local HTTP-to-SOCKS bridge did not stay alive")
            return PreparedHttpProxy(bridge.url(), bridge)
        except Exception as exc:
            if bridge is not None:
                try:
                    bridge.stop()
                except Exception:
                    pass
            raise EgressCarrierError(
                "SOCKS egress is required but its local HTTP carrier is "
                f"unavailable ({type(exc).__name__}: {exc}). Configure an "
                "explicit http:// proxy for this site; the transfer remains "
                "refused until a carrier is available."
            ) from exc
    raise EgressCarrierError(
        f"unsupported egress proxy scheme in {proxy!r}; configure an explicit "
        "http:// proxy for this site"
    )


def effective_download_proxy(
    explicit_proxy: Optional[str],
    site_id: str,
    socks_for_site: Optional[Callable[[str], Optional[str]]],
) -> Optional[str]:
    """Return the proxy URL an in-process download client should use.

    Precedence / posture (the VPN behavior is inherited from ``socks_for_site``):

      * A non-empty ``explicit_proxy`` always wins -- preserves the pre-Track-K
        per-site proxy behavior, and lets an operator override the tunnel.
      * ``socks_for_site is None`` (VPN runtime unavailable / degraded import):
        return the explicit proxy or ``None`` -- behave exactly as before.
      * Otherwise return ``socks_for_site(site_id)``, which the VPN runtime
        defines as:
          - a tunnel SOCKS url   when the site's tunnel is up,
          - ``None``             when no tunnel is configured / the site is not
                                 ``vpn_required`` (degrade open -- the operator's
                                 opt-in posture), and
          - *raises* ``VPNRequiredError`` when the site IS ``vpn_required`` but
            the tunnel is down/killed. This function lets that exception
            propagate so the caller fails closed and never builds an unproxied
            client. The payload bytes never touch the clear interface.
    """
    explicit = (explicit_proxy or "").strip()
    if explicit:
        return explicit
    if socks_for_site is None:
        return None
    return socks_for_site(site_id)
