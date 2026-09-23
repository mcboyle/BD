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
import ssl
import string
import threading
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
        # Row 1007: offer the session ticket from the last handshake with this LOGICAL host, so
        # a repeat connection resumes instead of paying a full handshake. The key is the logical
        # hostname, never the pinned literal: the literal can change between connections while
        # the peer identity -- the thing a session belongs to -- does not.
        from .tls_session_cache import remember_session, resume_session_for

        key_host = self._pinned_server_hostname or self.host
        session = resume_session_for(key_host, self.port, self._context)
        try:
            self.sock = self._context.wrap_socket(
                self.sock, server_hostname=self._pinned_server_hostname, session=session)
        except ValueError:
            # OpenSSL binds a session to the context that negotiated it and rejects a foreign
            # one outright. The key above is scoped per context so this should not happen -- and
            # if it ever does, a stale cache entry must cost a full handshake, never the request.
            # The refused socket cannot be reused (wrap_socket has already detached its fd), so
            # the connection is made again and wrapped with no session offered.
            from .tls_session_cache import REUSE_FAILURES

            REUSE_FAILURES["offer:ValueError"] += 1
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = self._create_connection(
                (self.host, self.port), self.timeout, self.source_address)
            if self._tunnel_host:
                self._tunnel()
            self.sock = self._context.wrap_socket(
                self.sock, server_hostname=self._pinned_server_hostname)
        # Store AFTER the handshake: this is the negotiated session. Under TLS 1.2 it is already
        # resumable, and a server that declined to resume hands back a fresh one here, so a
        # rejected ticket replaces itself. A TLS 1.3 full handshake's session carries no ticket
        # yet: remember_session refuses it, so it is never a "hit" that resumes nothing (G1) and
        # never evicts the ticket a concurrent connection just stored (G2). getresponse() stores
        # the ticketed one (rc5 E1); a rejected 1.3 ticket is replaced there by the server's new
        # one or, from a server that issues none, stays until its TTL (the handshake is full
        # either way).
        remember_session(key_host, self.port, getattr(self.sock, "session", None),
                         self._context)

    def getresponse(self):
        # Row 1007 (rc5 E1): a TLS 1.3 peer issues its NewSessionTicket AFTER the handshake, and
        # OpenSSL only takes it in when application data is read -- so the session connect() saw
        # carried no ticket and resumed nothing (remember_session refuses it). The ticket precedes
        # the response on the wire, so once the status line and headers are read the session is
        # the resumable one; store it now. (TLS 1.2 stores the same session twice, harmlessly.)
        # The socket is held first because a will-close response detaches it from the connection.
        sock = self.sock
        response = super().getresponse()
        from .tls_session_cache import remember_session

        remember_session(self._pinned_server_hostname or self.host, self.port,
                         getattr(sock, "session", None), self._context)
        return response


_SHARED_CONTEXT = None
_SHARED_CONTEXT_LOCK = threading.Lock()


def _shared_https_context():
    """The one verified client SSLContext every pinned HTTPS request in the process shares.

    Row 1007 re-emit (N6-A E1): a session can only be resumed on the context that negotiated it,
    and http.client builds a NEW default context for every connection it is not handed one for --
    so with a context per connection the session cache never hit. This is the context http.client
    would have built (same stdlib factory, so the same CA store and hostname checking), built once.
    It is rebuilt only if that factory is replaced, so the verification policy stays the stdlib's.
    G3: the private http.client._create_https_context exists only on CPython 3.12+; where it is
    absent (3.9-3.11, which the installers accept) the same steps are taken inline.
    """
    global _SHARED_CONTEXT
    factory = ssl._create_default_https_context
    with _SHARED_CONTEXT_LOCK:
        if _SHARED_CONTEXT is None or _SHARED_CONTEXT[0] is not factory:
            create = getattr(http.client, "_create_https_context", None)
            if create is not None:                          # CPython 3.12+
                context = create(11)
            else:
                # CPython 3.9-3.11 have no such helper: their HTTPSConnection.__init__ runs the
                # same three steps inline (3.11.16 measured), so do exactly those.
                context = factory()
                context.set_alpn_protocols(["http/1.1"])
                if context.post_handshake_auth is not None:
                    context.post_handshake_auth = True
            _SHARED_CONTEXT = (factory, context)
        return _SHARED_CONTEXT[1]


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        logical_host = getattr(req, "_pinned_logical_host", None)
        return self.do_open(
            lambda host, **kwargs: _PinnedHTTPSConnection(
                host, server_hostname=logical_host, **kwargs),
            req, context=self._context)


class _PinnedResponseHandler(urllib.request.BaseHandler):
    """Keep the public response URL logical while the socket uses a literal."""

    def http_response(self, req, response):
        response.url = getattr(req, "_pinned_logical_url", req.full_url)
        return response

    https_response = http_response


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
        # Match urllib's header-byte quoting before restoring the logical host.
        location = urllib.parse.quote(
            location, encoding="iso-8859-1", safe=string.punctuation)
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
            _PinnedRedirectHandler(self.pin_request),
            _PinnedHTTPSHandler(context=_shared_https_context()),
            _PinnedResponseHandler())
        return opener.open(pinned, timeout=timeout)
