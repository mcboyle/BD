"""ssrf_transport -- the one guarded-transport seam for every httpx client BD builds (row 703).

WHY.  ``provider_resolve_impl._common._SSRFGuardedTransport`` closes the DNS
rebinding window between a pre-fetch host check and httpcore's connect-time
resolution: it resolves inside the transport, classifies every address, and
pins the connection to the vetted IP literal (SNI and Host keep the name).  It
was installed by ONE builder, ``_make_default_http_get``, while every other
``httpx.Client(...)`` in the package was constructed bare and re-resolved at
connect.  Every construction now passes ``transport=guarded_transport(...)``
through this module, and ``tests/test_row703_ssrf_transport_is_installed_everywhere.py``
derives the construction population from the tree and refuses any that does not.

TWO POLICIES, chosen per call site by what the site already promises.  This
seam only ADDS refusals; it never admits a host the base refused.

``PUBLIC_ONLY`` -- for a client whose URLs are already vetted public-only on
the executed path (an ``_is_safe_public_host`` pre-check or request hook).
Returns the established ``_SSRFGuardedTransport`` unchanged: hostnames are
re-resolved in the transport, every address must classify public, and the
connection is pinned to the vetted literal.  Literal-IP URLs are delegated as
before -- the pre-check owns them.

``PINNED`` -- for a client whose target is operator-configured (a bridge on the
LAN, an identity provider, a VPN provider API or its gateway, a site's own
check URL, a fixed GitHub endpoint) or otherwise admitted private peers on the
base.  ``PinnedTransport`` keeps that admissibility -- loopback, RFC1918, ULA
and CGNAT stay admitted -- and refuses only what can never be a legitimate HTTP
peer: unspecified, multicast, IPv4 reserved/broadcast, and link-local
(169.254/16 including the cloud-metadata endpoint, fe80::/10), judged on the
address AND on every IPv4 an IPv6 address embeds (mapped, 6to4, Teredo, NAT64).
Hostnames are resolved exactly once here and the connection is pinned to the
first vetted literal, so the address the check saw is the address the socket
opens.  Literal-IP URLs ARE classified: no pre-check exists at these sites.

WHAT INSTALLING A TRANSPORT CHANGES IN httpx 0.28.  A client built with
``transport=`` no longer reads HTTP(S)_PROXY / ALL_PROXY from the environment
(``allow_env_proxies = trust_env and transport is None``); that matches the
egress rule that ambient proxies never carry BD traffic (row 439).  An explicit
``proxy=`` on the client still mounts its own proxy transport, and a request
routed through it is resolved AT the proxy, never here -- pinning a proxied
request locally would move the lookup off the tunnel.  ``verify`` / ``cert`` /
``http2`` / ``limits`` belong to the transport once one is installed, so a
site that sets them passes them to ``guarded_transport`` as well.

FAILURE CLASS.  Every refusal raised here is ``GuardedTransportRefused``, an
``httpx.ConnectError`` -- the class httpcore raises for a failed resolution --
so the handlers each call site already has keep catching it.
"""
from __future__ import annotations

import contextlib
import ipaddress
import socket
import sys
from typing import Optional, Tuple

import httpx

PUBLIC_ONLY = "public-only"
PINNED = "pinned"
POLICIES = (PUBLIC_ONLY, PINNED)

_NAT64_WELL_KNOWN = ipaddress.ip_network("64:ff9b::/96")


class GuardedTransportRefused(httpx.ConnectError):
    """A guarded transport refused to open a socket.

    ``host`` is the URL host as requested; ``reason`` is a short token:
    ``unspecified``, ``multicast``, ``reserved``, ``link-local``,
    ``resolution-failed`` or ``no-address``.
    """

    def __init__(self, message: str, *, host: str, reason: str) -> None:
        super().__init__(message)
        self.host = host
        self.reason = reason


def embedded_ipv4(addr) -> Tuple[ipaddress.IPv4Address, ...]:
    """Every IPv4 address an IPv6 address carries inside it (mapped, 6to4,
    Teredo server and client, NAT64 well-known prefix); empty for IPv4."""
    if not isinstance(addr, ipaddress.IPv6Address):
        return ()
    found = []
    for candidate in (addr.ipv4_mapped, addr.sixtofour):
        if candidate is not None:
            found.append(candidate)
    teredo = addr.teredo
    if teredo:
        found.extend(teredo)
    if addr in _NAT64_WELL_KNOWN:
        found.append(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF))
    return tuple(found)


def never_admissible(addr) -> Optional[str]:
    """The reason ``addr`` can never be a legitimate HTTP peer, or None.

    Deliberately NOT a public-only classifier: loopback, private, ULA and
    CGNAT return None because the PINNED sites admit them on the base.
    """
    for view in (addr, *embedded_ipv4(addr)):
        if view.is_unspecified:
            return "unspecified"
        if view.is_multicast:
            return "multicast"
        if view.is_link_local:
            return "link-local"
        if isinstance(view, ipaddress.IPv4Address) and view.is_reserved:
            return "reserved"
    return None


class PinnedTransport(httpx.HTTPTransport):
    """Resolve once, refuse the never-admissible, connect to the literal the check saw."""

    policy = PINNED

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        # httpx uses this request again after the transport returns to process
        # cookies and relative/same-origin redirects.  Pin only while crossing
        # the socket boundary, then restore that logical request identity.
        original_url = request.url
        original_sni = request.extensions.get("sni_hostname", _ABSENT)
        self.pin(request)
        try:
            return super().handle_request(request)
        finally:
            request.url = original_url
            if original_sni is _ABSENT:
                request.extensions.pop("sni_hostname", None)
            else:
                request.extensions["sni_hostname"] = original_sni

    def pin(self, request: httpx.Request) -> Optional[str]:
        """Classify and pin ``request`` in place.

        Returns the literal the URL was rewritten to, or None when the host
        was absent or already a literal (classified, not rewritten).  Raises
        ``GuardedTransportRefused`` before any socket is opened.
        """
        host = request.url.host
        if not host:
            return None
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            reason = never_admissible(literal)
            if reason is not None:
                raise GuardedTransportRefused(
                    f"SSRF guard (pinned transport): refusing {reason} address {host}",
                    host=host, reason=reason)
            return None

        port = request.url.port or (443 if request.url.scheme in ("https", "wss") else 80)
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except (socket.gaierror, OSError, UnicodeError) as exc:
            raise GuardedTransportRefused(
                f"SSRF guard (pinned transport): resolution failed for {host!r}: "
                f"{type(exc).__name__}: {exc}",
                host=host, reason="resolution-failed") from exc

        chosen: Optional[str] = None
        for family, _stype, _proto, _canon, sockaddr in infos:
            if family not in (socket.AF_INET, socket.AF_INET6):
                continue
            try:
                addr = ipaddress.ip_address(sockaddr[0])
            except ValueError:
                raise GuardedTransportRefused(
                    f"SSRF guard (pinned transport): non-IP answer for {host!r}: {sockaddr[0]!r}",
                    host=host, reason="no-address")
            # One bad sibling poisons the whole answer: a retry could land on it.
            reason = never_admissible(addr)
            if reason is not None:
                raise GuardedTransportRefused(
                    f"SSRF guard (pinned transport): {host!r} resolves to {reason} address {addr}",
                    host=host, reason=reason)
            if chosen is None:
                chosen = sockaddr[0]
        if chosen is None:
            raise GuardedTransportRefused(
                f"SSRF guard (pinned transport): no usable address for {host!r}",
                host=host, reason="no-address")

        request.url = request.url.copy_with(host=chosen)
        request.extensions["sni_hostname"] = host
        return chosen


_ESTABLISHED_GUARD_CLS: Optional[type] = None
_ABSENT = object()


class _RestoresLogicalRequest:
    """Keep httpx's cookie and redirect identity after a guarded socket call."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        original_url = request.url
        original_sni = request.extensions.get("sni_hostname", _ABSENT)
        try:
            return super().handle_request(request)
        finally:
            request.url = original_url
            if original_sni is _ABSENT:
                request.extensions.pop("sni_hostname", None)
            else:
                request.extensions["sni_hostname"] = original_sni


def established_guard_cls() -> type:
    """The established ``_SSRFGuardedTransport`` class, built against the
    real ``httpx``.

    ``_SSRFGuardedTransport_factory`` imports ``httpx`` lazily, by name, and
    memoizes whatever class it builds for the rest of the process.  This
    module holds the real ``httpx`` binding -- taken at import, before any
    test double can stand in for the module in ``sys.modules`` -- so the
    factory is driven with that binding installed.  Without this a fixture's
    ``MagicMock()`` or bare ``Client`` holder standing in for ``httpx`` would
    either break the factory (nothing to subclass) or, worse, let it memoize
    a mock 'class' that every later caller in the process inherits.
    """
    global _ESTABLISHED_GUARD_CLS
    cls = _ESTABLISHED_GUARD_CLS
    if cls is None:
        from bulk_downloader.provider_resolve_impl._common import _SSRFGuardedTransport_factory
        standing_in = sys.modules.get("httpx", _ABSENT)
        if standing_in is not httpx:
            sys.modules["httpx"] = httpx
        try:
            cls = _SSRFGuardedTransport_factory()
        finally:
            if standing_in is _ABSENT:
                sys.modules.pop("httpx", None)
            elif standing_in is not httpx:
                sys.modules["httpx"] = standing_in
        if not (isinstance(cls, type) and issubclass(cls, httpx.HTTPTransport)):
            raise RuntimeError(
                "the established SSRF guard is not an httpx.HTTPTransport class "
                f"({cls!r}): a stand-in for httpx was in sys.modules when "
                "_SSRFGuardedTransport_factory first ran, and it memoized that")
        _ESTABLISHED_GUARD_CLS = cls
    return cls


def restoring_public_guard_cls() -> type:
    """Established public guard with only post-socket request restoration."""
    class RestoringPublicGuard(_RestoresLogicalRequest, established_guard_cls()):
        policy = PUBLIC_ONLY
    return RestoringPublicGuard


@contextlib.contextmanager
def owning_stream(client: httpx.Client, method: str, url, **request_kwargs):
    """``client.stream(...)`` that also owns ``client``.

    The module-level ``httpx.stream`` helper built a throwaway client with the
    DEFAULT transport and closed it on exit -- re-resolving at connect, which
    is the defect this module exists to close.  A site that wrote
    ``with httpx.stream(...) as r:`` keeps that shape with
    ``with owning_stream(httpx.Client(transport=guarded_transport(...)), ...) as r:``
    and the client, transport included, is closed when the response is.
    """
    with client, client.stream(method, url, **request_kwargs) as response:
        yield response


def guarded_transport(policy: str, **transport_kwargs) -> httpx.HTTPTransport:
    """The transport for ``policy`` (``PUBLIC_ONLY`` or ``PINNED``).

    ``transport_kwargs`` are the ``httpx.HTTPTransport`` arguments the call
    site would otherwise have handed to ``httpx.Client`` -- ``verify``,
    ``cert``, ``trust_env``, ``http1``, ``http2``, ``limits``, ``retries``,
    ``local_address``, ``uds``, ``socket_options`` -- because once a transport
    is installed httpx no longer applies them from the client.
    """
    if policy == PUBLIC_ONLY:
        return restoring_public_guard_cls()(allow_private_hosts=False, **transport_kwargs)
    if policy == PINNED:
        return PinnedTransport(**transport_kwargs)
    raise ValueError(
        f"unknown guarded-transport policy {policy!r}; expected one of {POLICIES}")
