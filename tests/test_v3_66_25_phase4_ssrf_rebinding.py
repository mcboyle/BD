"""v3.66.25 — Phase 4 P6: DNS-rebinding close on the SSRF guard.

Tests the transport-layer guard added in v3.66.25:
``_SSRFGuardedTransport``. The transport closes the TOCTOU window
between ``_is_safe_public_host``'s pre-fetch DNS resolution and
httpcore's connect-time resolution. The transport re-resolves the
hostname, classifies every returned IP, refuses on any private IP,
and rewrites the request URL to use the vetted IP literal so
httpcore cannot rebind.

What's covered
--------------

  TestTransportClassShape
      ``_SSRFGuardedTransport`` is a subclass of ``httpx.HTTPTransport``,
      the factory is memoized, opt-out flag is accepted and stored.

  TestPrepareRequestPassThroughCases
      Pass-through scenarios: ``allow_private_hosts=True``, IP-literal
      hosts (v4 and v6), empty hosts. These short-circuit before the
      transport's own getaddrinfo.

  TestPrepareRequestBlocking
      Refusal scenarios: hostname resolves to a private IP, mixed
      public/private resolution (one bad IP poisons the set),
      DNS failure (fail-closed), empty resolution result.

  TestPrepareRequestRewriting
      The successful path: request.url is rewritten to the IP
      literal, the ``Host`` header is preserved (still original
      hostname), ``extensions['sni_hostname']`` is set to the
      original hostname for TLS verification.

  TestDNSRebindingCaught
      The canonical rebinding test: monkeypatch ``socket.getaddrinfo``
      to return a public IP on first call (consumed by
      ``_is_safe_public_host``) and a private IP on the second
      (the transport's call). Verify ``SSRFBlocked``.

  TestTransportWiredIntoFactory
      The default ``_make_default_http_get()`` actually installs
      a ``_SSRFGuardedTransport`` (not the stock ``HTTPTransport``)
      and threads the opt-out flag through to it.

  TestEndToEndComposition
      Composition with the existing event-hook guard: even when
      ``_is_safe_public_host`` is monkeypatched to allow a
      hostname, the transport still refuses it if it resolves to
      a private IP. Defense in depth.

  TestOptOutShortcircuit
      With ``allow_private_hosts=True``, the transport is a pure
      pass-through: localhost fetches succeed end-to-end.
"""
from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import List, Optional, Tuple

import httpx
import pytest

from bulk_downloader import provider_resolve as pr
from bulk_downloader.provider_resolve import (
    SSRFBlocked,
    _make_default_http_get,
    _SSRFGuardedTransport_factory,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mk_request(url: str) -> httpx.Request:
    """Build an httpx.Request the way an httpx.Client would. Headers
    populated from the URL (including ``Host``). Use this in tests that
    drive ``_prepare_request`` directly."""
    return httpx.Request("GET", url)


def _aiv4(ip: str, port: int = 80) -> tuple:
    """Construct an AF_INET getaddrinfo tuple."""
    return (socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, port))


def _aiv6(ip: str, port: int = 80) -> tuple:
    """Construct an AF_INET6 getaddrinfo tuple."""
    return (socket.AF_INET6, socket.SOCK_STREAM, 0, "", (ip, port, 0, 0))


# ---------------------------------------------------------------------------
# TestTransportClassShape
# ---------------------------------------------------------------------------


class TestTransportClassShape:

    def test_factory_returns_httpx_transport_subclass(self):
        T = _SSRFGuardedTransport_factory()
        assert issubclass(T, httpx.HTTPTransport)

    def test_factory_is_memoized(self):
        """Repeated factory calls return the SAME class object.
        Tests must be able to do ``isinstance(x, T)`` regardless of
        whether T was fetched before or after another caller used
        the factory."""
        T1 = _SSRFGuardedTransport_factory()
        T2 = _SSRFGuardedTransport_factory()
        assert T1 is T2

    def test_allow_private_hosts_flag_stored(self):
        T = _SSRFGuardedTransport_factory()
        guarded = T()
        try:
            assert guarded._allow_private_hosts is False
        finally:
            guarded.close()

        relaxed = T(allow_private_hosts=True)
        try:
            assert relaxed._allow_private_hosts is True
        finally:
            relaxed.close()

    def test_has_prepare_request_method(self):
        """The split-out helper is part of the contract — tests
        exercise it directly to avoid going to the network."""
        T = _SSRFGuardedTransport_factory()
        assert hasattr(T, "_prepare_request")
        assert callable(T._prepare_request)


# ---------------------------------------------------------------------------
# TestPrepareRequestPassThroughCases
# ---------------------------------------------------------------------------


class TestPrepareRequestPassThroughCases:
    """Scenarios where ``_prepare_request`` returns without doing
    anything — the request reaches the base transport unmodified."""

    def test_opt_out_skips_all_processing(self, monkeypatch):
        T = _SSRFGuardedTransport_factory()
        t = T(allow_private_hosts=True)
        try:
            # Sentinel: if getaddrinfo gets called, raise — but it
            # shouldn't because opt-out short-circuits.
            def bomb(*a, **kw):
                raise AssertionError(
                    "getaddrinfo must not be called when "
                    "allow_private_hosts=True"
                )
            monkeypatch.setattr(socket, "getaddrinfo", bomb)
            req = _mk_request("https://example.com/path")
            t._prepare_request(req)
            # URL must not be rewritten.
            assert req.url.host == "example.com"
            assert "sni_hostname" not in req.extensions
        finally:
            t.close()

    def test_ipv4_literal_skips_resolution(self, monkeypatch):
        """A URL with an IPv4 literal goes through unchanged — the
        event hook already classified it pre-fetch."""
        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            def bomb(*a, **kw):
                raise AssertionError("getaddrinfo not expected")
            monkeypatch.setattr(socket, "getaddrinfo", bomb)
            req = _mk_request("http://8.8.8.8/")
            t._prepare_request(req)
            # URL host unchanged.
            assert req.url.host == "8.8.8.8"
            assert "sni_hostname" not in req.extensions
        finally:
            t.close()

    def test_ipv6_literal_skips_resolution(self, monkeypatch):
        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            def bomb(*a, **kw):
                raise AssertionError("getaddrinfo not expected")
            monkeypatch.setattr(socket, "getaddrinfo", bomb)
            req = _mk_request("http://[2001:db8::1]/")
            t._prepare_request(req)
            # httpx strips IPv6 brackets from .host.
            assert req.url.host == "2001:db8::1"
            assert "sni_hostname" not in req.extensions
        finally:
            t.close()

    def test_empty_host_skips_processing(self, monkeypatch):
        """A URL with no host (unusual but defensible — e.g. some
        edge cases of file:/// or unix sockets that slip through)
        must not crash the transport. Let the base transport
        diagnose it."""
        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            called = []

            def fake_gai(host, *a, **kw):
                called.append(host)
                return [_aiv4("8.8.8.8")]
            monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
            # Construct a request whose URL has an empty host. httpx
            # accepts this through manual URL construction.
            url = httpx.URL(scheme="http", host="", path="/")
            req = httpx.Request("GET", url)
            t._prepare_request(req)
            assert called == []  # never reached resolution
        finally:
            t.close()


# ---------------------------------------------------------------------------
# TestPrepareRequestBlocking
# ---------------------------------------------------------------------------


class TestPrepareRequestBlocking:
    """Refusal scenarios. The transport must raise SSRFBlocked with
    a diagnostic message that includes the offending host."""

    def test_resolves_to_loopback_blocks(self, monkeypatch):
        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            monkeypatch.setattr(
                socket, "getaddrinfo",
                lambda *a, **kw: [_aiv4("127.0.0.1")],
            )
            req = _mk_request("https://attacker.example/")
            with pytest.raises(SSRFBlocked) as excinfo:
                t._prepare_request(req)
            msg = str(excinfo.value)
            assert "transport" in msg
            assert "attacker.example" in msg
            assert "127.0.0.1" in msg
        finally:
            t.close()

    def test_resolves_to_rfc1918_blocks(self, monkeypatch):
        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            monkeypatch.setattr(
                socket, "getaddrinfo",
                lambda *a, **kw: [_aiv4("10.0.0.5")],
            )
            req = _mk_request("https://attacker.example/")
            with pytest.raises(SSRFBlocked) as excinfo:
                t._prepare_request(req)
            assert "10.0.0.5" in str(excinfo.value)
        finally:
            t.close()

    def test_resolves_to_link_local_blocks(self, monkeypatch):
        """169.254.169.254 — AWS metadata. The big one."""
        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            monkeypatch.setattr(
                socket, "getaddrinfo",
                lambda *a, **kw: [_aiv4("169.254.169.254")],
            )
            req = _mk_request("https://meta.attacker.example/")
            with pytest.raises(SSRFBlocked) as excinfo:
                t._prepare_request(req)
            assert "169.254.169.254" in str(excinfo.value)
        finally:
            t.close()

    def test_ipv6_loopback_blocks(self, monkeypatch):
        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            monkeypatch.setattr(
                socket, "getaddrinfo",
                lambda *a, **kw: [_aiv6("::1")],
            )
            req = _mk_request("https://attacker.example/")
            with pytest.raises(SSRFBlocked) as excinfo:
                t._prepare_request(req)
            assert "::1" in str(excinfo.value)
        finally:
            t.close()

    def test_mixed_public_private_resolution_blocks(self, monkeypatch):
        """Resolution returns one public + one private IP — refuse.
        Matches the fail-closed policy of ``_is_safe_public_host``:
        any private IP in the set poisons the set."""
        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            monkeypatch.setattr(
                socket, "getaddrinfo",
                lambda *a, **kw: [
                    _aiv4("8.8.8.8"),
                    _aiv4("10.0.0.1"),
                ],
            )
            req = _mk_request("https://multihomed.example/")
            with pytest.raises(SSRFBlocked) as excinfo:
                t._prepare_request(req)
            assert "10.0.0.1" in str(excinfo.value)
        finally:
            t.close()

    def test_private_first_then_public_still_blocks(self, monkeypatch):
        """Order doesn't matter — the first private IP encountered
        triggers refusal."""
        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            monkeypatch.setattr(
                socket, "getaddrinfo",
                lambda *a, **kw: [
                    _aiv4("10.0.0.1"),
                    _aiv4("8.8.8.8"),
                ],
            )
            req = _mk_request("https://multihomed.example/")
            with pytest.raises(SSRFBlocked):
                t._prepare_request(req)
        finally:
            t.close()

    def test_dns_failure_blocks_fail_closed(self, monkeypatch):
        """DNS lookup failure → refuse with diagnostic. Same
        defensive posture as ``_is_safe_public_host``."""
        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            def fail(*a, **kw):
                raise socket.gaierror(
                    -2, "Name or service not known")
            monkeypatch.setattr(socket, "getaddrinfo", fail)
            req = _mk_request("https://nope.invalid/")
            with pytest.raises(SSRFBlocked) as excinfo:
                t._prepare_request(req)
            msg = str(excinfo.value)
            assert "transport" in msg
            assert "DNS resolution failed" in msg
            assert "nope.invalid" in msg
            assert "gaierror" in msg
        finally:
            t.close()

    def test_empty_resolution_blocks_fail_closed(self, monkeypatch):
        """getaddrinfo returns [] (unusual but possible) → refuse."""
        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            monkeypatch.setattr(socket, "getaddrinfo",
                                lambda *a, **kw: [])
            req = _mk_request("https://weird.example/")
            with pytest.raises(SSRFBlocked) as excinfo:
                t._prepare_request(req)
            assert "no addresses" in str(excinfo.value)
        finally:
            t.close()

    def test_multicast_blocks(self, monkeypatch):
        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            monkeypatch.setattr(
                socket, "getaddrinfo",
                lambda *a, **kw: [_aiv4("224.0.0.1")],
            )
            req = _mk_request("https://mcast.example/")
            with pytest.raises(SSRFBlocked):
                t._prepare_request(req)
        finally:
            t.close()


# ---------------------------------------------------------------------------
# TestPrepareRequestRewriting
# ---------------------------------------------------------------------------


class TestPrepareRequestRewriting:
    """Successful resolution: ensure the request mutations are
    exactly what httpx/httpcore need to (a) connect to the vetted IP
    and (b) verify TLS against the original hostname."""

    def test_url_host_rewritten_to_ip_literal(self, monkeypatch):
        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            monkeypatch.setattr(
                socket, "getaddrinfo",
                lambda *a, **kw: [_aiv4("93.184.216.34")],
            )
            req = _mk_request("https://example.com/path?x=1")
            t._prepare_request(req)
            assert req.url.host == "93.184.216.34"
            # Path and query preserved.
            assert req.url.path == "/path"
            assert b"x=1" in req.url.query
            # Scheme preserved.
            assert req.url.scheme == "https"
        finally:
            t.close()

    def test_host_header_preserved_as_original_hostname(
        self, monkeypatch,
    ):
        """The Host header was populated by httpx.Request from the
        ORIGINAL URL at construction time. Our URL rewrite must not
        cause httpx to regenerate it from the IP — virtual-hosting
        servers depend on the original hostname."""
        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            monkeypatch.setattr(
                socket, "getaddrinfo",
                lambda *a, **kw: [_aiv4("93.184.216.34")],
            )
            req = _mk_request("https://example.com/")
            t._prepare_request(req)
            host_header = req.headers.get("Host", "")
            assert host_header == "example.com"
            # Sanity: it didn't get rewritten to the IP.
            assert "93.184.216.34" not in host_header
        finally:
            t.close()

    def test_sni_extension_set_to_original_hostname(
        self, monkeypatch,
    ):
        """httpcore reads request.extensions['sni_hostname'] when
        calling start_tls. Setting it to the original hostname
        preserves cert verification despite the IP-literal URL."""
        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            monkeypatch.setattr(
                socket, "getaddrinfo",
                lambda *a, **kw: [_aiv4("93.184.216.34")],
            )
            req = _mk_request("https://example.com/")
            t._prepare_request(req)
            assert req.extensions.get("sni_hostname") == "example.com"
        finally:
            t.close()

    def test_ipv6_resolution_rewrites_correctly(self, monkeypatch):
        """An IPv6 resolution result must also work — copy_with
        handles the bracket-wrapping in the URL itself."""
        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            monkeypatch.setattr(
                socket, "getaddrinfo",
                lambda *a, **kw: [_aiv6("2606:2800:220:1:248:1893:25c8:1946")],
            )
            req = _mk_request("https://example.com/")
            t._prepare_request(req)
            # httpx strips brackets in .host but the URL string
            # reflects them.
            assert req.url.host == "2606:2800:220:1:248:1893:25c8:1946"
            assert "[2606:2800:" in str(req.url)
            # Host header still original.
            assert req.headers.get("Host", "") == "example.com"
            # SNI still original.
            assert req.extensions.get("sni_hostname") == "example.com"
        finally:
            t.close()

    def test_first_public_ip_chosen_when_multiple(self, monkeypatch):
        """When multiple public IPs are returned, the first one
        wins — deterministic and stable."""
        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            monkeypatch.setattr(
                socket, "getaddrinfo",
                lambda *a, **kw: [
                    _aiv4("93.184.216.34"),
                    _aiv4("8.8.8.8"),
                ],
            )
            req = _mk_request("https://example.com/")
            t._prepare_request(req)
            assert req.url.host == "93.184.216.34"
        finally:
            t.close()

    def test_port_preserved_in_rewrite(self, monkeypatch):
        """Non-default port in the URL must survive the rewrite."""
        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            monkeypatch.setattr(
                socket, "getaddrinfo",
                lambda *a, **kw: [_aiv4("93.184.216.34")],
            )
            req = _mk_request("https://example.com:8443/path")
            t._prepare_request(req)
            assert req.url.host == "93.184.216.34"
            assert req.url.port == 8443
        finally:
            t.close()


# ---------------------------------------------------------------------------
# TestDNSRebindingCaught
# ---------------------------------------------------------------------------


class TestDNSRebindingCaught:
    """The canonical rebinding test: simulate an attacker DNS server
    that returns a public IP to ``_is_safe_public_host``'s lookup
    (so the pre-fetch hook lets the request through) and a private
    IP to the transport's lookup (the moment of truth). The
    transport must refuse."""

    def test_rebinding_attack_blocked_by_transport(self, monkeypatch):
        """Construct a stateful fake ``getaddrinfo`` that returns
        a public IP on the first call (for ``_is_safe_public_host``)
        and a private IP on the second (for the transport). The
        transport must catch the rebind and raise."""
        call_log: List[str] = []

        def rebinding_gai(host, *a, **kw):
            call_log.append(host)
            if len(call_log) == 1:
                return [_aiv4("93.184.216.34")]  # public
            return [_aiv4("169.254.169.254")]  # private, rebound

        monkeypatch.setattr(socket, "getaddrinfo", rebinding_gai)

        # Pre-fetch: classify the hostname. This is what
        # _is_safe_public_host does (called by the http_get pre-fetch
        # check).
        ok, _ = pr._is_safe_public_host("attacker.example")
        assert ok, "first resolution must be 'safe' to simulate rebind"
        assert len(call_log) == 1

        # Transport: now simulate the second hop. Build the same
        # request the http_get would have built and feed it through.
        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            req = _mk_request("https://attacker.example/")
            with pytest.raises(SSRFBlocked) as excinfo:
                t._prepare_request(req)
            assert len(call_log) == 2, (
                "transport must have done its OWN getaddrinfo"
            )
            assert "transport" in str(excinfo.value)
            assert "169.254.169.254" in str(excinfo.value)
        finally:
            t.close()

    def test_rebinding_to_loopback_blocked(self, monkeypatch):
        """Same pattern but the rebind target is 127.0.0.1
        (classic 'point at your local admin server')."""
        call_count = {"n": 0}

        def rebinding_gai(host, *a, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return [_aiv4("8.8.8.8")]
            return [_aiv4("127.0.0.1")]

        monkeypatch.setattr(socket, "getaddrinfo", rebinding_gai)

        ok, _ = pr._is_safe_public_host("rebinder.example")
        assert ok

        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            req = _mk_request("https://rebinder.example/")
            with pytest.raises(SSRFBlocked) as excinfo:
                t._prepare_request(req)
            assert "127.0.0.1" in str(excinfo.value)
        finally:
            t.close()

    def test_consistent_resolution_succeeds(self, monkeypatch):
        """Negative control: when the attacker DOESN'T rebind (same
        public IP on both calls), the transport allows the request
        through. Proves the transport isn't refusing every name."""
        monkeypatch.setattr(
            socket, "getaddrinfo",
            lambda *a, **kw: [_aiv4("93.184.216.34")],
        )

        ok, _ = pr._is_safe_public_host("legit.example")
        assert ok

        T = _SSRFGuardedTransport_factory()
        t = T()
        try:
            req = _mk_request("https://legit.example/")
            t._prepare_request(req)  # must NOT raise
            assert req.url.host == "93.184.216.34"
        finally:
            t.close()


# ---------------------------------------------------------------------------
# TestTransportWiredIntoFactory
# ---------------------------------------------------------------------------


class TestTransportWiredIntoFactory:
    """The transport must actually be installed by
    ``_make_default_http_get``. Without this wiring the rebinding
    defense is dead code."""

    @staticmethod
    def _intercepted_client(monkeypatch, capture: dict):
        """Replace ``httpx.Client`` with a fake that records the
        kwargs (so we can assert on the transport=) and short-
        circuits ``client.get`` with a sentinel exception. Returns
        nothing — works via side effect on ``capture``."""
        class _Sentinel(Exception):
            pass

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                capture["transport"] = kwargs.get("transport")
                capture["event_hooks"] = kwargs.get("event_hooks")
                capture["follow_redirects"] = kwargs.get(
                    "follow_redirects")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                # Make sure the transport gets closed properly even
                # though we never used it for a real request.
                t = capture.get("transport")
                if t is not None:
                    try:
                        t.close()
                    except Exception:
                        pass
                return False

            def get(self, *args, **kwargs):
                raise _Sentinel("intercepted")

        monkeypatch.setattr(httpx, "Client", _FakeClient)
        return _Sentinel

    def test_factory_installs_guarded_transport(self, monkeypatch):
        T = _SSRFGuardedTransport_factory()
        captured: dict = {}
        Sentinel = self._intercepted_client(monkeypatch, captured)
        # Public-resolving host so the pre-fetch hook lets us
        # reach the Client construction.
        monkeypatch.setattr(
            socket, "getaddrinfo",
            lambda *a, **kw: [_aiv4("93.184.216.34")],
        )
        g = _make_default_http_get()
        with pytest.raises(Sentinel):
            g("https://example.com/")
        assert "transport" in captured
        assert isinstance(captured["transport"], T)

    def test_factory_threads_opt_out_through(self, monkeypatch):
        T = _SSRFGuardedTransport_factory()
        captured: dict = {}
        Sentinel = self._intercepted_client(monkeypatch, captured)
        # With opt-out, the pre-fetch hook lets anything through —
        # including a literal that would normally be refused.
        g = _make_default_http_get(allow_private_hosts=True)
        with pytest.raises(Sentinel):
            g("http://127.0.0.1/")
        assert isinstance(captured["transport"], T)
        assert captured["transport"]._allow_private_hosts is True

    def test_default_transport_has_guard_active(self, monkeypatch):
        T = _SSRFGuardedTransport_factory()
        captured: dict = {}
        Sentinel = self._intercepted_client(monkeypatch, captured)
        monkeypatch.setattr(
            socket, "getaddrinfo",
            lambda *a, **kw: [_aiv4("8.8.8.8")],
        )
        g = _make_default_http_get()
        with pytest.raises(Sentinel):
            g("https://example.com/")
        assert isinstance(captured["transport"], T)
        assert captured["transport"]._allow_private_hosts is False

    def test_event_hooks_still_installed_alongside_transport(
        self, monkeypatch,
    ):
        """Defense in depth: the event-hook layer must STILL be
        present even though the transport layer now exists. A
        future refactor that drops the event hooks ('the transport
        catches everything now') would be a regression — the hook
        is the cheap first line of defense and runs before any
        connection setup."""
        captured: dict = {}
        Sentinel = self._intercepted_client(monkeypatch, captured)
        monkeypatch.setattr(
            socket, "getaddrinfo",
            lambda *a, **kw: [_aiv4("8.8.8.8")],
        )
        g = _make_default_http_get()
        with pytest.raises(Sentinel):
            g("https://example.com/")
        hooks = captured.get("event_hooks") or {}
        assert "request" in hooks
        assert len(hooks["request"]) >= 1


# ---------------------------------------------------------------------------
# TestEndToEndComposition
# ---------------------------------------------------------------------------


class TestEndToEndComposition:
    """The transport layer composes with the existing event-hook
    layer. Even if the event-hook layer is fooled (by a rebind, or
    by a misconfiguration), the transport layer catches the bad IP
    at connect time."""

    def test_transport_catches_when_predicate_is_lied_to(
        self, monkeypatch,
    ):
        """Simulate the rebind end-to-end through
        ``_make_default_http_get``. We monkeypatch
        ``_is_safe_public_host`` to always say 'safe' (the
        attacker has fooled the predicate / pre-fetch hook).
        The transport must still refuse based on its own
        resolution."""
        monkeypatch.setattr(
            pr, "_is_safe_public_host",
            lambda host: (True, ""),
        )
        monkeypatch.setattr(
            socket, "getaddrinfo",
            lambda *a, **kw: [_aiv4("127.0.0.1")],
        )

        g = _make_default_http_get()
        with pytest.raises(SSRFBlocked) as excinfo:
            g("https://attacker.example/")
        assert "transport" in str(excinfo.value)
        assert "127.0.0.1" in str(excinfo.value)

    def test_event_hook_still_first_line_of_defense(
        self, monkeypatch,
    ):
        """Negative: with the predicate doing its real job, an
        obvious private literal is refused by the event-hook
        layer BEFORE the transport sees it. Sentinel-by-bombing
        the transport's getaddrinfo proves the transport wasn't
        the one that refused."""
        # The pre-fetch event hook calls _is_safe_public_host on
        # the URL hostname directly; for a literal like 127.0.0.1
        # that classifies as loopback without calling getaddrinfo.
        # So putting a bomb in getaddrinfo would NEVER fire even
        # if everything worked correctly, because the literal path
        # in _is_safe_public_host short-circuits.
        #
        # Instead: monkeypatch the transport class's _prepare_request
        # to record its call, and verify it ISN'T reached when the
        # event hook refuses first.
        T = _SSRFGuardedTransport_factory()
        called = []
        orig_prepare = T._prepare_request

        def spy_prepare(self, req):
            called.append(req.url.host)
            return orig_prepare(self, req)

        monkeypatch.setattr(T, "_prepare_request", spy_prepare)

        g = _make_default_http_get()
        with pytest.raises(SSRFBlocked):
            g("http://127.0.0.1/")
        # The pre-fetch hook caught it before any Client.get()
        # was issued, so the transport's _prepare_request was
        # never invoked.
        assert called == []


# ---------------------------------------------------------------------------
# TestOptOutShortcircuit
# ---------------------------------------------------------------------------


# Reusable localhost-server fixture (similar to v3.66.24).


class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        body = b'{"ok": true}'
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a, **k):
        pass


@pytest.fixture
def localhost_server():
    server = HTTPServer(("127.0.0.1", 0), _OkHandler)
    port = server.server_port
    thread = threading.Thread(
        target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield ("127.0.0.1", port)
    finally:
        server.shutdown()
        thread.join(timeout=2.0)


class TestOptOutShortcircuit:
    """When ``allow_private_hosts=True`` the transport is a pure
    pass-through. End-to-end: localhost fetches actually succeed.
    This exercises the real network stack (no monkeypatching of
    socket.getaddrinfo), so it also implicitly verifies the IP-
    literal short-circuit path works against a real httpcore."""

    def test_opt_out_fetches_localhost_via_ip_literal(
        self, localhost_server,
    ):
        host, port = localhost_server
        g = _make_default_http_get(allow_private_hosts=True)
        status, _, body = g(f"http://{host}:{port}/")
        assert status == 200
        assert body == b'{"ok": true}'

    def test_opt_out_with_localhost_hostname(
        self, localhost_server,
    ):
        """Same but with the ``localhost`` name instead of the IP
        literal — exercises the resolution-shortcircuit path when
        the opt-out is on."""
        _, port = localhost_server
        g = _make_default_http_get(allow_private_hosts=True)
        status, _, body = g(f"http://localhost:{port}/")
        assert status == 200
        assert body == b'{"ok": true}'


# ---------------------------------------------------------------------------
# Module-level: __all__ unchanged check
# ---------------------------------------------------------------------------


def test_module_exports_unchanged_for_public_api():
    """v3.66.25's additions are internal (transport class is
    underscore-prefixed) — that release added nothing to __all__.
    The frozen set below is the public surface, updated as the API
    legitimately grows: v3.66.48 (C2) added ``build_signing_callback``
    (a real public helper, asserted present by
    test_v3_66_48_c2_signing_callback::test_in_public_api). When a
    new public symbol is intentionally exported, add it here in the
    same commit."""
    expected = {
        "HttpGet",
        "CacheWrite",
        "DEFAULT_CACHE_TTL_SECONDS",
        "SSRFBlocked",
        "resolve_provider_embed",
        "resolve_vimeo",
        "resolve_youtube",
        "resolve_brightcove",
        "resolve_wistia",
        "resolve_jwplayer",
        "build_signing_callback",  # v3.66.48 (C2)
    }
    assert set(pr.__all__) == expected
