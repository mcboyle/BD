"""BD-SEC-3 (row 713 residual): two token egress paths the row-713 guard missed.

D1  ``plex_advanced._connect`` built a bare ``PlexServer``, so the Plex token
    followed a 302 to whatever host the Location named.
D2  ``deep_http._check`` skipped ALL classification once a site set its
    ``*_allow_private_host`` opt-in, so link-local / cloud-metadata targets
    (169.254.169.254, fe80::/10, fd00:ec2::254, and names resolving there)
    were reachable with the token attached.

The opt-in is for a LAN media server; it never opens the metadata ranges, and
it never relaxes redirect handling. Fixtures are loopback recorders and the
REAL classifier; the only stubbed resolver answers one metadata hostname.
"""

BD_GATE_SCOPE = "module"

import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import requests

# Documented zero-entropy synthetic fixture value; not a credential.
TOKEN = "ZZZZ-BDSEC3-TOKEN-0000"
METADATA_NAME = "metadata.google.internal"
# r2: names the stub answers with IPv6 loopback, alone and beside the IMDS address.
V6_LOOPBACK_NAME = "plex-v6.bdsec3.test"
V6_LOOPBACK_AND_IMDS_NAME = "plex-v6-imds.bdsec3.test"
UNRESOLVABLE_NAME = "plex-gone.bdsec3.test"


class _Recorder(HTTPServer):
    def __init__(self, host="127.0.0.1"):
        self.requests = []
        self.redirect_to = None
        if ":" in host:
            self.address_family = socket.AF_INET6
        super().__init__((host, 0), _Handler)

    @property
    def origin(self):
        host = self.server_address[0]
        host = f"[{host}]" if ":" in host else host
        return f"http://{host}:{self.server_address[1]}"

    def token_hits(self):
        hits = 0
        for path, headers in self.requests:
            received = {k.lower(): v for k, v in headers.items()}
            hits += received.get("x-plex-token") == TOKEN
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            hits += query.get("X-Plex-Token", []).count(TOKEN)
        return hits


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append((self.path, dict(self.headers)))
        if self.server.redirect_to:
            self.send_response(302)
            self.send_header("Location", self.server.redirect_to + self.path)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = (
            b'<MediaContainer friendlyName="bdsec3" machineIdentifier="m" version="1"/>'
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def servers():
    started = []

    def _start(host="127.0.0.1"):
        server = _Recorder(host)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started.append((server, thread))
        return server

    try:
        yield _start
    finally:
        for server, thread in started:
            server.shutdown()
            thread.join()
            server.server_close()


@pytest.fixture
def metadata_name_resolves_to_imds(monkeypatch):
    """Answer ONE name with the IMDS address; every other lookup is real."""
    real = socket.getaddrinfo

    def _getaddrinfo(host, *args, **kwargs):
        imds = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))
        loop6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 0, 0, 0))
        if host == METADATA_NAME:
            return [imds]
        if host == V6_LOOPBACK_NAME:
            return [loop6]
        if host == V6_LOOPBACK_AND_IMDS_NAME:
            return [loop6, imds]
        if host == UNRESOLVABLE_NAME:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)


@pytest.fixture
def transports(monkeypatch):
    """Record anything that reaches either transport behind classification."""
    from bulk_downloader import deep_http

    reached = []

    class _Opener:
        def open(self, request, *args, **kwargs):
            reached.append(("urllib", request.full_url))
            raise AssertionError(
                f"BD-SEC-3-D2: urllib transport reached {request.full_url}"
            )

    def _send(self, prepared, **kwargs):
        reached.append(("requests", prepared.url))
        raise AssertionError(f"BD-SEC-3-D2: requests transport reached {prepared.url}")

    monkeypatch.setattr(deep_http, "_OPENER", _Opener())
    monkeypatch.setattr(requests.Session, "send", _send)
    return reached


# ── D2: link-local / metadata stay refused with the opt-in ON ─────────

NEVER_ALLOWED = [
    pytest.param("http://169.254.169.254/latest/meta-data/", id="imds-v4"),
    pytest.param("http://169.254.10.1:8096/System/Info", id="link-local-v4"),
    pytest.param("http://[fe80::1]:32400/identity", id="link-local-v6"),
    pytest.param("http://[fd00:ec2::254]/latest/meta-data/", id="imds-v6"),
    pytest.param("http://[::ffff:169.254.169.254]/", id="imds-v4-mapped"),
    pytest.param(f"http://{METADATA_NAME}/computeMetadata/v1/", id="imds-by-name"),
    pytest.param(f"http://{V6_LOOPBACK_AND_IMDS_NAME}/", id="v6-loopback-beside-imds"),
]


@pytest.mark.parametrize("url", NEVER_ALLOWED)
def test_bd_sec_3_optin_on_still_refuses_link_local_and_metadata(
    url, transports, metadata_name_resolves_to_imds
):
    from bulk_downloader import deep_http
    from bulk_downloader.provider_resolve_impl._common import SSRFBlocked

    host = urllib.parse.urlsplit(url).hostname
    for entry, call in (
        (
            "guarded_open",
            lambda: deep_http.guarded_open(
                url,
                headers={"X-Plex-Token": TOKEN},
                timeout=2.0,
                allow_private_hosts=True,
            ),
        ),
        (
            "guarded_session",
            lambda: deep_http.guarded_session(allow_private_hosts=True).get(
                url, headers={"X-Plex-Token": TOKEN}, timeout=2.0
            ),
        ),
    ):
        with pytest.raises(SSRFBlocked) as refused:
            call()
        message = str(refused.value)
        assert ("link-local" in message or "metadata" in message) and host in message, (
            f"BD-SEC-3-D2 {entry}: opt-in ON let {url} past classification "
            f"or refused it without naming host+reason: {message!r}"
        )
    assert transports == [], (
        f"BD-SEC-3-D2: a never-allowed target reached the transport: {transports}"
    )


def test_bd_sec_3_optin_on_refuses_a_name_that_does_not_resolve(
    transports, metadata_name_resolves_to_imds
):
    """r2: an unmeasurable destination is not a permitted one, opt-in or not."""
    from bulk_downloader import deep_http
    from bulk_downloader.provider_resolve_impl._common import SSRFBlocked

    url = f"http://{UNRESOLVABLE_NAME}:32400/identity"
    try:
        deep_http._check(url, True)
    except SSRFBlocked as refused:
        message = str(refused)
    else:
        message = ""
    assert "DNS resolution failed" in message and UNRESOLVABLE_NAME in message, (
        f"BD-SEC-3-R2-UNRESOLVED: opt-in ON admitted {url}: {message!r}"
    )
    assert transports == [], transports


def test_bd_sec_3_optin_on_reaches_a_loopback_server_end_to_end(servers):
    """Control: the opt-in still does its job with the REAL classifier."""
    from bulk_downloader.plex_deep import PlexClient

    lan = servers()
    PlexClient(lan.origin, TOKEN, allow_private_hosts=True)._request(
        "GET", "/identity", timeout=2.0
    )
    assert len(lan.requests) == 1 and lan.token_hits() == 1, lan.requests


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.10:32400/",
        "http://10.0.0.5:8096/",
        "http://127.0.0.1:9999/",
        "http://[::1]:32400/",
        f"http://{V6_LOOPBACK_NAME}:32400/",
    ],
)
def test_bd_sec_3_optin_on_still_accepts_rfc1918_and_loopback(
    url, metadata_name_resolves_to_imds
):
    from bulk_downloader import deep_http
    from bulk_downloader.provider_resolve_impl._common import SSRFBlocked

    try:
        deep_http._check(url, True)
    except SSRFBlocked as refused:
        pytest.fail(
            f"BD-SEC-3-R2-LOOPBACK: opt-in ON refused LAN/loopback {url}: {refused}"
        )


def test_bd_sec_3_optin_on_reaches_an_ipv6_loopback_server_end_to_end(servers):
    """r2 control (lens F1): IPv6 loopback is a LAN target, reached with the opt-in."""
    from bulk_downloader.plex_deep import PlexClient

    lan = servers("::1")
    try:
        PlexClient(lan.origin, TOKEN, allow_private_hosts=True)._request(
            "GET", "/identity", timeout=2.0
        )
    except Exception as error:  # noqa: BLE001 -- the assertion names it
        pytest.fail(f"BD-SEC-3-R2-V6LOOP: plex_deep refused {lan.origin}: {error}")
    assert len(lan.requests) == 1 and lan.token_hits() == 1, lan.requests


# ── D1: plex_advanced never carries the token across a redirect ───────


def _connect_outcome(plex_advanced, cfg):
    """(server, error): the call's result or what it raised, judged afterwards."""
    try:
        return plex_advanced._connect(cfg), None
    except Exception as error:  # noqa: BLE001 -- the assertions judge it
        return None, error


def _plex_advanced_or_fail():
    from bulk_downloader import plex_advanced

    if not plex_advanced.is_available():
        pytest.fail(
            f"plexapi unavailable, cannot measure: {plex_advanced.import_error()}"
        )
    return plex_advanced


def test_bd_sec_3_plex_advanced_refuses_a_cross_host_redirect(servers):
    plex_advanced = _plex_advanced_or_fail()
    first, other = servers("127.0.0.1"), servers("127.0.0.2")
    first.redirect_to = other.origin
    cfg = {
        "plex_url": first.origin,
        "plex_token": TOKEN,
        "plex_allow_private_host": True,
    }
    _server, error = _connect_outcome(plex_advanced, cfg)
    assert other.requests == [] and other.token_hits() == 0, (
        f"BD-SEC-3-D1: plex_advanced carried the token across a redirect to "
        f"{other.origin}: {other.token_hits()} token hit(s), {other.requests}"
    )
    assert "cross-origin redirect refused" in str(error), error
    assert len(first.requests) == 1, first.requests


def test_bd_sec_3_plex_advanced_reaches_its_lan_server_with_the_optin(servers):
    plex_advanced = _plex_advanced_or_fail()
    lan = servers()
    server = plex_advanced._connect(
        {"plex_url": lan.origin, "plex_token": TOKEN, "plex_allow_private_host": True}
    )
    assert server.friendlyName == "bdsec3"
    assert len(lan.requests) == 1 and lan.token_hits() == 1, lan.requests


def test_bd_sec_3_plex_advanced_reaches_an_ipv6_loopback_server_with_the_optin(servers):
    plex_advanced = _plex_advanced_or_fail()
    lan = servers("::1")
    server, error = _connect_outcome(
        plex_advanced,
        {"plex_url": lan.origin, "plex_token": TOKEN, "plex_allow_private_host": True},
    )
    assert error is None, (
        f"BD-SEC-3-R2-V6LOOP: plex_advanced refused {lan.origin}: {error}"
    )
    assert server.friendlyName == "bdsec3"
    assert len(lan.requests) == 1 and lan.token_hits() == 1, lan.requests


def test_bd_sec_3_plex_advanced_without_the_optin_refuses_by_name(servers):
    plex_advanced = _plex_advanced_or_fail()
    lan = servers()
    _server, error = _connect_outcome(
        plex_advanced, {"plex_url": lan.origin, "plex_token": TOKEN}
    )
    assert lan.requests == [], (
        f"BD-SEC-3-D1-OPTIN: plex_advanced sent the token to a private host "
        f"without plex_allow_private_host: {lan.requests}"
    )
    message = str(error)
    assert "plex_allow_private_host" in message and "127.0.0.1" in message, message
