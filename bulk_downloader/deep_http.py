"""Guarded stdlib/requests HTTP for token-bearing deep integrations.

``allow_private_hosts`` relaxes host classification for deliberate LAN
integrations only.  It never relaxes the same-origin-only redirect policy,
and it never admits link-local / cloud-metadata, CGNAT, reserved, multicast
or unspecified targets (BD-SEC-3): with it on, the repository's LAN
classifier ``hooks._host_ok_for_hook`` judges every resolved address.

SUBSTITUTION SEAM.  :func:`guarded_open` classifies the target and only then
delegates the send to the module-level opener ``_OPENER``.  That opener is the
single substitutable point: a test that needs a fake transport replaces
``deep_http._OPENER``, and classification still runs ahead of it, because
``_check`` executes before the opener is reached on every hop.  There is no
branch in this module that skips ``_check``, no hostname is special-cased and
no environment variable relaxes anything, so a host that fails to resolve is
refused whether or not a test is running.
"""
from __future__ import annotations

import functools
import ipaddress
import urllib.error
import urllib.parse
import urllib.request

import requests

from bulk_downloader.provider_resolve_impl._common import SSRFBlocked, _is_safe_public_host
from bulk_downloader.provider_resolve_impl._common import _classify_ip
from bulk_downloader.urllib_ssrf import (PinnedUrlOpener, PinnedUrlRefused,
                                         _PinnedHTTPSHandler,
                                         _PinnedResponseHandler)

_MAX_REDIRECTS = 5
_IPV6_LOOPBACK = ipaddress.ip_address("::1")


def refusal_message(reason, setting_name: str) -> str:
    """The refusal an operator actually reads.  It NAMES the per-site setting
    that re-enables a LAN target, because an operator whose own media server
    stops answering has to be told how to allow it here, not in a changelog."""
    return (f"{reason}; set {setting_name}=true on this site to allow a "
            "private address")


def _check(url: str, allow_private_hosts: bool) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise SSRFBlocked(f"unsupported URL scheme: {parsed.scheme or 'missing'}")
    if not allow_private_hosts:
        safe, reason = _is_safe_public_host(parsed.hostname or "")
        if not safe:
            raise SSRFBlocked(str(reason))
        return
    # The opt-in admits a LAN server (RFC 1918, loopback), never the metadata
    # endpoint: 169.254.169.254 named by IP, by mapped/embedded IPv6 or by a
    # hostname that resolves there is refused with the switch on as well.
    from bulk_downloader.hooks import _host_ok_for_hook
    host = parsed.hostname or ""
    ok, reason = _host_ok_for_hook(host)
    if ok:
        return
    # BD-SEC-3 r2: hooks judges is_reserved before its loopback allowance, and
    # Python classes ::1 as reserved (::/8), so IPv6 loopback was refused. Re-judge
    # with the ::1 answers removed; every other answer is still judged by hooks.
    answers = _answers(host)
    rest = [a for a in answers if a != _IPV6_LOOPBACK]
    if not answers:
        # hooks' DNS-failure reason does not carry the host; name it.
        raise SSRFBlocked(f"{reason} ({host})")
    if len(rest) == len(answers):
        raise SSRFBlocked(reason)
    ok, reason = _host_ok_for_hook(host, _resolved=rest)
    if not ok:
        raise SSRFBlocked(reason)


def _answers(host: str) -> list:
    """The addresses ``host`` names (itself if a literal); [] when unresolvable."""
    import socket
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return [ipaddress.ip_address(info[4][0]) for info in infos
                if info[0] in (socket.AF_INET, socket.AF_INET6)]
    except (OSError, UnicodeError, ValueError):
        return []


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urllib.parse.urlsplit(url)
    return (parsed.scheme.lower(), (parsed.hostname or "").lower(),
            parsed.port or (443 if parsed.scheme.lower() == "https" else 80))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects are decided by :func:`guarded_open`, never by urllib, whose
    own handler copies every header (token included) to the new host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _PinToVetted(urllib.request.BaseHandler):
    """Rebind pin for :func:`guarded_open`: the send connects to the address
    judged here, from one lookup, never to a second lookup of the name."""

    handler_order = 100   # before urllib fills the Host header from the URL

    def http_request(self, req):
        policy = (_lan_address_allowed
                  if getattr(req, "_bd_allow_private_hosts", False)
                  else public_address_allowed)
        try:
            pinned = PinnedUrlOpener(policy).pin_request(req)
        except PinnedUrlRefused as exc:
            raise SSRFBlocked(f"rebind pin: {exc.reason}") from exc
        pinned.timeout = req.timeout
        return pinned

    https_request = http_request


# The transport every guarded send goes through, AFTER classification.
_OPENER = urllib.request.build_opener(_NoRedirect, _PinToVetted(),
                                      _PinnedHTTPSHandler(),
                                      _PinnedResponseHandler())


def guarded_open(url, *, data=None, method="GET", headers=None, timeout,
                 allow_private_hosts=False):
    """Open a URL after classification; follow at most five same-origin hops."""
    current = url
    request_data = data
    request_method = method
    request_headers = dict(headers or {})
    for _hop in range(_MAX_REDIRECTS + 1):
        _check(current, allow_private_hosts)
        request = urllib.request.Request(current, data=request_data,
                                         method=request_method,
                                         headers=request_headers)
        request._bd_allow_private_hosts = allow_private_hosts
        try:
            from . import http_client
            return http_client.proxy_open(request, timeout=timeout,
                                          direct_open=_OPENER.open)
        except urllib.error.HTTPError as exc:
            if exc.code not in {301, 302, 303, 307, 308}:
                raise
            target = urllib.parse.urljoin(current, exc.headers.get("Location", ""))
            if not exc.headers.get("Location") or _origin(target) != _origin(current):
                raise SSRFBlocked(f"cross-origin redirect refused: {current} -> {target}")
            current = target
            if exc.code == 303 or (exc.code in {301, 302} and request_method == "POST"):
                request_data, request_method = None, "GET"
    raise SSRFBlocked("redirect hop limit exhausted")


class _GuardedSession(requests.Session):
    def __init__(self, *, allow_private_hosts=False):
        super().__init__()
        self._allow_private_hosts = allow_private_hosts

    def request(self, method, url, **kwargs):
        kwargs["allow_redirects"] = False
        current = url
        for _hop in range(_MAX_REDIRECTS + 1):
            _check(current, self._allow_private_hosts)
            response = super().request(method, current, **kwargs)
            location = response.headers.get("Location")
            if not (response.is_redirect and location):
                return response
            target = urllib.parse.urljoin(current, location)
            if _origin(target) != _origin(current):
                raise SSRFBlocked(
                    f"cross-origin redirect refused: {current} -> {target}")
            if response.status_code == 303 or (
                    response.status_code in {301, 302}
                    and method.upper() == "POST"):
                method = "GET"
                for body_kwarg in ("data", "json", "files"):
                    kwargs.pop(body_kwarg, None)
            current = target
        raise SSRFBlocked("redirect hop limit exhausted")


def guarded_session(*, allow_private_hosts=False):
    """Return a session which classifies every request and never redirects."""
    return _GuardedSession(allow_private_hosts=allow_private_hosts)


# -- DNS-rebinding pin --------------------------------------------------------
# The checks above judge a host by name and the transport then looked the name
# up again to connect, so a name answering a public address to the check and a
# refused one to the connect (TTL-0 rebinding) reached the refused address.
# Below, the lookup a connect uses is the lookup that is judged.

def public_address_allowed(address, host):
    """The public-only policy: ``_is_safe_public_host``'s per-address predicate."""
    return _classify_ip(address, host)


def hook_address_allowed(address, host):
    """``hooks``' LAN policy: loopback and RFC 1918 admitted; link-local and
    cloud metadata, CGNAT, reserved, multicast and unspecified refused."""
    from bulk_downloader.hooks import _hook_address_allowed
    return _hook_address_allowed(address, host)


def _lan_address_allowed(address, host):
    # As _check: the opt-in admits ::1, which hooks classes as reserved.
    if address == _IPV6_LOOPBACK:
        return True, ""
    return hook_address_allowed(address, host)


def _vetted_address(host, port, address_allowed) -> str:
    """Resolve ``host`` once, judge every answer, return the first as a literal."""
    import socket
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise SSRFBlocked(
            f"rebind pin: DNS resolution failed for {host!r}: {exc}") from exc
    literals = []
    for family, _type, _proto, _canon, sockaddr in infos:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        try:
            address = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            raise SSRFBlocked(
                f"rebind pin: non-IP DNS answer {sockaddr[0]!r} for {host!r}")
        ok, reason = address_allowed(address, host)
        if not ok:
            raise SSRFBlocked(f"rebind pin: {reason}")
        literals.append(str(address))
    if not literals:
        raise SSRFBlocked(
            f"rebind pin: DNS returned no usable addresses for {host!r}")
    return literals[0]


class _PinnedConnectionMixin:
    """urllib3 connection that connects to the vetted literal.  ``host`` (the
    Host header, TLS SNI and certificate check) stays the name."""

    _address_allowed = None

    def _new_conn(self):
        logical = self._dns_host
        self._dns_host = _vetted_address(logical, self.port,
                                         type(self)._address_allowed)
        try:
            return super()._new_conn()
        finally:
            self._dns_host = logical


@functools.lru_cache(maxsize=None)
def _pinned_pool_classes(address_allowed):
    from urllib3 import connection, connectionpool
    policy = {"_address_allowed": address_allowed}
    http_conn = type("_PinnedHTTPConnection",
                     (_PinnedConnectionMixin, connection.HTTPConnection), policy)
    https_conn = type("_PinnedHTTPSConnection",
                      (_PinnedConnectionMixin, connection.HTTPSConnection), policy)
    return {
        "http": type("_PinnedHTTPConnectionPool",
                     (connectionpool.HTTPConnectionPool,),
                     {"ConnectionCls": http_conn}),
        "https": type("_PinnedHTTPSConnectionPool",
                      (connectionpool.HTTPSConnectionPool,),
                      {"ConnectionCls": https_conn}),
    }


class _PinnedAdapter(requests.adapters.HTTPAdapter):
    """Direct connections go only to an address ``address_allowed`` admitted.
    A configured proxy resolves the name itself and is used as before."""

    def __init__(self, address_allowed, **kwargs):
        self._pool_classes = _pinned_pool_classes(address_allowed)
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = self._pool_classes


def pinned_session(address_allowed):
    """A ``requests`` session whose http and https connects carry the rebind pin."""
    session = requests.Session()
    adapter = _PinnedAdapter(address_allowed)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
