"""Pinned urllib opener for the three SSRF-guarded urllib seams (row 728).

``urllib`` normally resolves the name in a request again when it opens the
socket.  This module owns resolution, lets the caller judge every returned
address, then rewrites only the connect target to the selected literal.  The
logical Host header and HTTPS SNI remain the original hostname.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request


class PinnedUrlRefused(urllib.error.URLError):
    """The address set for a guarded urllib request was not admissible."""


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to the literal URL host while authenticating the logical host."""

    def __init__(self, host, *, server_hostname, **kwargs):
        self._pinned_server_hostname = server_hostname
        super().__init__(host, **kwargs)

    def connect(self):
        self.sock = self._create_connection(
            (self.host, self.port), self.timeout, self.source_address)
        if self._tunnel_host:
            self._tunnel()
        self.sock = self._context.wrap_socket(
            self.sock, server_hostname=self._pinned_server_hostname)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        logical_host = getattr(req, "_pinned_logical_host", None)
        return self.do_open(
            lambda host, **kwargs: _PinnedHTTPSConnection(
                host, server_hostname=logical_host, **kwargs),
            req, context=self._context)


class _PinnedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, pin_request):
        super().__init__()
        self._pin_request = pin_request

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # urllib makes a relative Location absolute from the pinned literal.
        # Rebase from the logical request instead, so the next hop is judged
        # and pinned as its hostname, not inherited as an opaque literal.
        logical_base = getattr(req, "_pinned_logical_url", req.full_url)
        location = headers.get("Location") or newurl
        logical_url = urllib.parse.urljoin(logical_base, location)
        redirected = super().redirect_request(
            req, fp, code, msg, headers, logical_url)
        if redirected is None:
            return None
        try:
            return self._pin_request(redirected)
        except PinnedUrlRefused as exc:
            raise urllib.error.URLError(
                f"SSRF redirect blocked: {exc.reason}") from exc


class PinnedUrlOpener:
    """Resolve once and open only the vetted literal for an urllib request.

    ``address_allowed(address, logical_host)`` must return ``(ok, reason)``.
    It is called for every DNS answer before a single address is selected.
    """

    def __init__(self, address_allowed):
        self._address_allowed = address_allowed

    @staticmethod
    def _host_header(parts):
        host = parts.hostname
        if not host:
            raise PinnedUrlRefused("pinned urllib: URL has no hostname")
        rendered = f"[{host}]" if ":" in host else host
        if parts.port is not None:
            rendered += f":{parts.port}"
        return rendered

    @staticmethod
    def _literal_netloc(parts, literal):
        rendered = f"[{literal}]" if ":" in literal else literal
        if parts.port is not None:
            rendered += f":{parts.port}"
        if parts.username is not None:
            userinfo = urllib.parse.quote(parts.username, safe="")
            if parts.password is not None:
                userinfo += ":" + urllib.parse.quote(parts.password, safe="")
            rendered = userinfo + "@" + rendered
        return rendered

    def pin_request(self, request):
        logical_url = getattr(request, "_pinned_logical_url", request.full_url)
        try:
            parts = urllib.parse.urlsplit(logical_url)
            host = parts.hostname
            port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
        except ValueError as exc:
            raise PinnedUrlRefused(f"pinned urllib: invalid URL: {exc}") from exc
        if parts.scheme.lower() not in ("http", "https") or not host:
            raise PinnedUrlRefused("pinned urllib: URL must be http(s) with a host")

        try:
            literal = str(ipaddress.ip_address(host))
        except ValueError:
            try:
                infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            except (socket.gaierror, OSError, UnicodeError) as exc:
                raise PinnedUrlRefused(
                    f"pinned urllib: DNS resolution failed for {host!r}: {exc}") from exc
            literals = []
            for family, _type, _proto, _canon, sockaddr in infos:
                if family not in (socket.AF_INET, socket.AF_INET6):
                    continue
                try:
                    address = ipaddress.ip_address(sockaddr[0])
                except ValueError as exc:
                    raise PinnedUrlRefused(
                        f"pinned urllib: non-IP DNS answer {sockaddr[0]!r}") from exc
                ok, reason = self._address_allowed(address, host)
                if not ok:
                    raise PinnedUrlRefused(str(reason))
                literals.append(str(address))
            if not literals:
                raise PinnedUrlRefused(
                    f"pinned urllib: DNS returned no usable addresses for {host!r}")
            literal = literals[0]
        else:
            ok, reason = self._address_allowed(ipaddress.ip_address(literal), host)
            if not ok:
                raise PinnedUrlRefused(str(reason))

        pinned_url = urllib.parse.urlunsplit(parts._replace(
            netloc=self._literal_netloc(parts, literal)))
        headers = dict(request.header_items())
        headers.pop("Host", None)
        headers.pop("host", None)
        pinned = urllib.request.Request(
            pinned_url, data=request.data, headers=headers,
            method=request.get_method())
        pinned.add_unredirected_header("Host", self._host_header(parts))
        pinned._pinned_logical_url = logical_url
        pinned._pinned_logical_host = host
        return pinned

    def open(self, request, timeout=15):
        pinned = (request if hasattr(request, "_pinned_logical_url")
                  else self.pin_request(request))
        opener = urllib.request.build_opener(
            _PinnedRedirectHandler(self.pin_request), _PinnedHTTPSHandler())
        return opener.open(pinned, timeout=timeout)
