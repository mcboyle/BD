"""v3.43.60: Unit tests for the VPN tunnel feature.

Covers:
  - vpn.py                 (Tunnel registry, PortPool, lifecycle dispatch, redaction)
  - vpn_socks.py           (SOCKS5 listen/handshake/relay; no real outbound)
  - vpn_wireguard.py       (render_conf, _derive_iface_name, availability)
  - vpn_openvpn.py         (log-line parsing, _safe_id, ready/iface/ip detection)
  - vpn_providers/*        (registry, format validation, render_config shapes)
  - vpn_leak_tests.py      (probe classification, history ring, external WebRTC)
  - vpn_kill_switch.py     (kill/clear/cycle lifecycle, callbacks, auto-clear streak)
  - vpn_kill_switch_system.py (plan generation, dry-run, allowlist, _safe_id)

These tests avoid:
  - Real subprocess calls (wireguard.exe / openvpn / netsh)
  - Real network calls (httpx is monkeypatched or absent path is exercised)
  - Background threads with real timers (kill switch auto-recover is disabled per-test)

Run with: pytest tests/test_v3_43_60_vpn_backends.py
"""
from __future__ import annotations

import os
import socket
import sys
# [SAST 3:13pm 13 may] removed unused: import threading
import time

import pytest

# Tests must work without network. Disable any keepalive/monitor threads.
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")


# ── Network isolation ───────────────────────────────────────────────────
# The provider tests below assert the SOFT-PASS / FALLBACK behaviour that
# happens when the VPN API is unreachable. They were silently dependent on
# the test machine having no network - which is true in CI / sandboxes but
# NOT on a developer's networked Windows box, where the real Mullvad / PIA
# API answers and (correctly) rejects the fake test accounts. This helper
# makes the offline path deterministic everywhere by forcing httpx to fail.
import contextlib as _contextlib


@_contextlib.contextmanager
def _force_offline():
    """Within this block, any httpx.Client() use raises - so provider
    code deterministically takes its 'API unreachable' fallback path.

    If httpx isn't installed at all, the provider already takes the
    offline path on its own, so this is a no-op."""
    try:
        import httpx
    except ImportError:
        yield
        return

    class _OfflineClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def _boom(self, *a, **k):
            raise httpx.ConnectError("forced offline by test")

        get = post = _boom

    real = httpx.Client
    httpx.Client = _OfflineClient
    try:
        yield
    finally:
        httpx.Client = real


# ════════════════════════════════════════════════════════════════════
#  vpn.py — core registry, port pool, redaction, lifecycle stubs
# ════════════════════════════════════════════════════════════════════

class TestPortPool:
    def setup_method(self):
        from bulk_downloader.vpn import PortPool
        # Use a high port range that's unlikely to be in real use.
        self.pool = PortPool(53000, 53005)

    def test_allocates_first_available(self):
        port = self.pool.allocate()
        assert port is not None
        assert 53000 <= port <= 53005

    def test_allocations_are_unique(self):
        ports = {self.pool.allocate() for _ in range(6)}
        assert len(ports) == 6
        assert None not in ports

    def test_exhaustion_returns_none(self):
        for _ in range(6):
            self.pool.allocate()
        assert self.pool.allocate() is None

    def test_release_makes_available(self):
        p1 = self.pool.allocate()
        # Drain everything else first so we're forced back to p1.
        for _ in range(5):
            self.pool.allocate()
        self.pool.release(p1)
        p2 = self.pool.allocate()
        assert p2 == p1

    def test_release_unknown_port_is_noop(self):
        self.pool.release(99999)  # not in our range; should not raise

    def test_invalid_range_rejected(self):
        from bulk_downloader.vpn import PortPool
        with pytest.raises(ValueError):
            PortPool(53010, 53000)


class TestTunnelRegistration:
    def setup_method(self):
        from bulk_downloader import vpn
        vpn._reset_for_tests()

    def teardown_method(self):
        from bulk_downloader import vpn
        vpn._reset_for_tests()

    def test_register_returns_id(self):
        from bulk_downloader import vpn
        tid = vpn.register_tunnel(
            name="test", provider="mullvad", backend="wireguard",
            location="us-nyc", config={"private_key": "abc"},
        )
        assert tid.startswith("tun-")
        assert vpn.get_tunnel(tid) is not None

    def test_register_unknown_backend_rejected(self):
        from bulk_downloader import vpn
        with pytest.raises(ValueError, match="backend"):
            vpn.register_tunnel(name="t", provider="mullvad", backend="ipsec")

    def test_register_unknown_provider_rejected(self):
        from bulk_downloader import vpn
        with pytest.raises(ValueError, match="provider"):
            vpn.register_tunnel(name="t", provider="nordvpn", backend="wireguard")

    def test_register_requires_name(self):
        from bulk_downloader import vpn
        with pytest.raises(ValueError, match="name"):
            vpn.register_tunnel(name="", provider="mullvad", backend="wireguard")

    def test_list_tunnels_returns_all(self):
        from bulk_downloader import vpn
        vpn.register_tunnel(name="a", provider="mullvad", backend="wireguard")
        vpn.register_tunnel(name="b", provider="proton", backend="openvpn")
        assert len(vpn.list_tunnels()) == 2


class TestTunnelRedaction:
    def setup_method(self):
        from bulk_downloader import vpn
        vpn._reset_for_tests()
        self.tid = vpn.register_tunnel(
            name="r", provider="mullvad", backend="wireguard",
            config={
                "private_key": "SECRET_PRIVATE_KEY",
                "peer_public_key": "PUBLIC_FINE",
                "endpoint": "1.2.3.4:51820",
                "password": "hunter2",
                "auth_token": "TOKEN",
                "nested": {"private": "deep_secret", "ok_field": "fine"},
            },
        )

    def teardown_method(self):
        from bulk_downloader import vpn
        vpn._reset_for_tests()

    def test_secrets_redacted_by_default(self):
        from bulk_downloader import vpn
        d = vpn.get_tunnel(self.tid).to_dict()
        cfg = d["config"]
        assert cfg["private_key"] == "***"
        assert cfg["password"] == "***"
        assert cfg["auth_token"] == "***"

    def test_non_secrets_preserved(self):
        from bulk_downloader import vpn
        d = vpn.get_tunnel(self.tid).to_dict()
        # endpoint is plainly non-secret — preserved.
        assert d["config"]["endpoint"] == "1.2.3.4:51820"
        # NOTE: 'peer_public_key' is intentionally redacted by vpn.py — the
        # _SECRET_KEY_HINTS substring match catches anything containing 'key',
        # which over-redacts public keys but is the documented policy
        # ("false positives are cheaper than leaking a private key").
        assert d["config"]["peer_public_key"] == "***"

    def test_nested_secrets_redacted(self):
        from bulk_downloader import vpn
        d = vpn.get_tunnel(self.tid).to_dict()
        assert d["config"]["nested"]["private"] == "***"
        assert d["config"]["nested"]["ok_field"] == "fine"

    def test_unredacted_form_available_for_backend(self):
        from bulk_downloader import vpn
        d = vpn.get_tunnel(self.tid).to_dict(redact_secrets=False)
        assert d["config"]["private_key"] == "SECRET_PRIVATE_KEY"


class TestStubBackend:
    """Without vpn_wireguard / vpn_openvpn installed at start, vpn.py
    must dispatch to a stub that fails closed."""

    def setup_method(self):
        from bulk_downloader import vpn
        vpn._reset_for_tests()

    def teardown_method(self):
        from bulk_downloader import vpn
        vpn._reset_for_tests()

    def test_stub_backend_start_fails_closed(self):
        # Use a tunnel with the *real* backend module not importable would
        # take a lot of monkeypatching; instead, hit the StubBackend class.
        from bulk_downloader.vpn import _StubBackend, Tunnel
        stub = _StubBackend()
        t = Tunnel(tunnel_id="tx", name="x", provider="mullvad", backend="wireguard")
        assert stub.start(t) is False
        assert stub.stop(t) is True
        assert stub.is_running(t) is False


# ════════════════════════════════════════════════════════════════════
#  vpn_socks.py — SOCKS5 server
# ════════════════════════════════════════════════════════════════════

class TestSocksProxy:
    def setup_method(self):
        from bulk_downloader.vpn_socks import SocksProxy
        self.proxy = SocksProxy("127.0.0.1", 53100, outbound_bind_ip=None)
        self.proxy.start()
        time.sleep(0.1)

    def teardown_method(self):
        self.proxy.stop()

    def test_start_listens(self):
        assert self.proxy.is_alive()
        # We should be able to connect to the listen port.
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(("127.0.0.1", 53100))
        s.close()

    def test_url_format(self):
        assert self.proxy.url() == "socks5://127.0.0.1:53100"

    def test_stop_is_idempotent(self):
        self.proxy.stop()
        self.proxy.stop()
        assert not self.proxy.is_alive()

    def test_unsupported_socks_version_disconnects(self):
        s = socket.socket()
        s.settimeout(2)
        s.connect(("127.0.0.1", 53100))
        s.sendall(b"\x04\x01\x00")  # SOCKS4 — wrong version
        try:
            reply = s.recv(2)
        except socket.timeout:
            reply = b""
        except (ConnectionResetError, OSError):
            # Windows resets the connection (WinError 10054) where
            # Linux gives a clean empty read. A reset IS a disconnect,
            # which is exactly the rejection this test wants to see.
            reply = b""
        s.close()
        # Either disconnect (b"") or no acceptable methods response
        assert reply == b"" or reply[0:1] == b"\x05"

    def test_no_auth_method_accepted(self):
        s = socket.socket()
        s.settimeout(2)
        s.connect(("127.0.0.1", 53100))
        # Greeting: SOCKS5, 1 method, NO_AUTH
        s.sendall(b"\x05\x01\x00")
        reply = s.recv(2)
        assert reply == b"\x05\x00"
        s.close()


def test_socks_recv_exact_returns_none_on_eof():
    from bulk_downloader.vpn_socks import _recv_exact
    a, b = socket.socketpair()
    a.close()  # immediate EOF on b
    out = _recv_exact(b, 4)
    b.close()
    assert out is None


# ════════════════════════════════════════════════════════════════════
#  vpn_wireguard.py — config rendering, iface naming
# ════════════════════════════════════════════════════════════════════

class TestWireguardRenderConf:
    def test_full_config_renders(self):
        from bulk_downloader.vpn_wireguard import render_conf
        cfg = {
            "private_key": "AAAA",
            "address": "10.0.0.2/32",
            "peer_public_key": "BBBB",
            "endpoint": "vpn.example.com:51820",
            "dns": "10.0.0.1",
            "preshared_key": "CCCC",
            "mtu": 1420,
        }
        out = render_conf(cfg)
        assert "[Interface]" in out
        assert "PrivateKey = AAAA" in out
        assert "Address = 10.0.0.2/32" in out
        assert "DNS = 10.0.0.1" in out
        assert "MTU = 1420" in out
        assert "[Peer]" in out
        assert "PublicKey = BBBB" in out
        assert "PresharedKey = CCCC" in out
        assert "Endpoint = vpn.example.com:51820" in out
        assert "AllowedIPs = 0.0.0.0/0,::/0" in out
        assert "PersistentKeepalive = 25" in out

    def test_minimal_config_renders(self):
        from bulk_downloader.vpn_wireguard import render_conf
        cfg = {
            "private_key": "A", "address": "10.0.0.2/32",
            "peer_public_key": "B", "endpoint": "x.example:51820",
        }
        out = render_conf(cfg)
        assert "DNS =" not in out
        assert "MTU =" not in out
        assert "PresharedKey =" not in out

    def test_missing_keys_raises(self):
        from bulk_downloader.vpn_wireguard import render_conf
        with pytest.raises(ValueError, match="missing required keys"):
            render_conf({"private_key": "A", "address": "10.0.0.2/32"})

    def test_empty_dict_raises(self):
        from bulk_downloader.vpn_wireguard import render_conf
        with pytest.raises(ValueError):
            render_conf({})

    def test_non_dict_raises(self):
        from bulk_downloader.vpn_wireguard import render_conf
        with pytest.raises(ValueError):
            render_conf("not a dict")  # type: ignore[arg-type]


class TestWireguardIfaceName:
    def test_derives_safe_name(self):
        from bulk_downloader.vpn_wireguard import _derive_iface_name
        assert _derive_iface_name("tun-abc123") == "wg-abc123"

    def test_strips_invalid_chars(self):
        from bulk_downloader.vpn_wireguard import _derive_iface_name
        n = _derive_iface_name("tun-ab!@#$%cd")
        assert all(c.isalnum() or c == "-" for c in n)

    def test_caps_at_15_chars(self):
        from bulk_downloader.vpn_wireguard import _derive_iface_name
        n = _derive_iface_name("tun-" + "a" * 100)
        assert len(n) <= 15

    def test_empty_after_sanitization_uses_anon(self):
        from bulk_downloader.vpn_wireguard import _derive_iface_name
        n = _derive_iface_name("!!!!!!")
        assert n.startswith("wg-")


def test_wireguard_is_available_returns_tuple():
    from bulk_downloader import vpn_wireguard
    ok, msg = vpn_wireguard.is_available()
    assert isinstance(ok, bool)
    assert isinstance(msg, str)
    assert len(msg) > 0


# ════════════════════════════════════════════════════════════════════
#  vpn_openvpn.py — log parsing, safe_id
# ════════════════════════════════════════════════════════════════════

class TestOpenVPNLogParsing:
    def test_safe_id_strips_dangerous_chars(self):
        from bulk_downloader.vpn_openvpn import _safe_id
        out = _safe_id("tun-/abc/../bad")
        assert "/" not in out
        assert ".." not in out

    def test_safe_id_caps_length(self):
        from bulk_downloader.vpn_openvpn import _safe_id
        assert len(_safe_id("x" * 1000)) <= 32

    def test_safe_id_empty_becomes_anon(self):
        from bulk_downloader.vpn_openvpn import _safe_id
        assert _safe_id("!!!!") == "____"  # underscores preserved by our rule

    def test_ready_regex_matches_canonical(self):
        from bulk_downloader.vpn_openvpn import _READY_RE
        assert _READY_RE.search("2024-01-01 Initialization Sequence Completed")
        assert _READY_RE.search("INITIALIZATION sequence COMPLETED")

    def test_iface_regex_linux(self):
        from bulk_downloader.vpn_openvpn import _IFACE_RE_LINUX
        m = _IFACE_RE_LINUX.search("TUN/TAP device tun0 opened")
        assert m is not None
        assert m.group(1) == "tun0"

    def test_iface_regex_darwin(self):
        from bulk_downloader.vpn_openvpn import _IFACE_RE_DARWIN
        m = _IFACE_RE_DARWIN.search("Opened utun5 successfully")
        assert m is not None
        assert m.group(1) == "utun5"

    def test_ip_regex_push(self):
        from bulk_downloader.vpn_openvpn import _IP_RE_PUSH
        m = _IP_RE_PUSH.search("ifconfig set local 10.8.0.5 remote 10.8.0.6")
        assert m is not None
        assert m.group(1) == "10.8.0.5"

    def test_ip_regex_dhcp(self):
        from bulk_downloader.vpn_openvpn import _IP_RE_DHCP
        m = _IP_RE_DHCP.search("Notified TAP-Windows driver to set a DHCP IP/netmask of 10.8.0.5/255.255.0.0")
        assert m is not None
        assert m.group(1) == "10.8.0.5"

    def test_fatal_regex_catches(self):
        from bulk_downloader.vpn_openvpn import _FATAL_RE
        assert _FATAL_RE.search("FATAL: cannot allocate")
        assert _FATAL_RE.search("AUTH Failed")
        assert _FATAL_RE.search("TLS Error: handshake")


def test_openvpn_is_available_returns_tuple():
    from bulk_downloader import vpn_openvpn
    ok, msg = vpn_openvpn.is_available()
    assert isinstance(ok, bool) and isinstance(msg, str)


# ════════════════════════════════════════════════════════════════════
#  vpn_providers — registry, format validation, config shapes
# ════════════════════════════════════════════════════════════════════

class TestProviderRegistry:
    def setup_method(self):
        from bulk_downloader import vpn_providers
        vpn_providers._reset_for_tests()

    def test_registry_lists_all_five(self):
        from bulk_downloader.vpn_providers import list_providers
        ids = {p["id"] for p in list_providers()}
        assert ids == {"mullvad", "proton", "pia", "generic", "selfhosted"}

    def test_get_provider_returns_module(self):
        from bulk_downloader.vpn_providers import get_provider
        mod = get_provider("mullvad")
        assert mod is not None
        assert mod.PROVIDER_ID == "mullvad"
        assert "wireguard" in mod.SUPPORTED_BACKENDS

    def test_get_provider_unknown_returns_none(self):
        from bulk_downloader.vpn_providers import get_provider
        assert get_provider("nordvpn") is None


class TestMullvadProvider:
    def test_account_number_format_validation(self):
        from bulk_downloader.vpn_providers import mullvad
        ok, _ = mullvad.test_credentials({"account_number": "abc"})
        assert ok is False
        # Bad format is rejected before any network call. The valid-
        # format case soft-passes only when the API is unreachable, so
        # force the offline path to make this deterministic.
        with _force_offline():
            ok, _ = mullvad.test_credentials(
                {"account_number": "1234567890123456"})
        assert ok is True

    def test_account_number_with_spaces_normalized(self):
        from bulk_downloader.vpn_providers import mullvad
        with _force_offline():
            ok, _ = mullvad.test_credentials(
                {"account_number": "1234 5678 9012 3456"})
        assert ok is True

    def test_list_locations_returns_fallback_when_offline(self):
        from bulk_downloader.vpn_providers import mullvad
        with _force_offline():
            locs = mullvad.list_locations({})
        assert isinstance(locs, list)
        assert len(locs) >= 5
        assert "country" in locs[0]
        assert "city" in locs[0]

    def test_render_wireguard_requires_private_key(self):
        from bulk_downloader.vpn_providers import mullvad
        with pytest.raises(ValueError, match="private_key"):
            mullvad.render_config(
                "wireguard",
                "us-nyc-wg-101",
                {"account_number": "1234567890123456"},
                0,
            )

    def test_render_wireguard_with_full_creds(self):
        from bulk_downloader.vpn_providers import mullvad
        cfg = mullvad.render_config(
            "wireguard",
            "us-nyc-wg-101",
            {
                "account_number": "1234567890123456",
                "private_key": "MY_PRIV",
                "address": "10.66.1.2/32",
            },
            0,
        )
        assert cfg["private_key"] == "MY_PRIV"
        assert cfg["address"] == "10.66.1.2/32"
        assert cfg["dns"] == mullvad.MULLVAD_DNS
        assert "peer_public_key" in cfg
        assert "endpoint" in cfg

    def test_render_openvpn_uses_account_as_username(self):
        from bulk_downloader.vpn_providers import mullvad
        cfg = mullvad.render_config(
            "openvpn",
            "us-nyc-wg-101",
            {"account_number": "1234567890123456"},
            0,
        )
        assert cfg["username"] == "1234567890123456"
        assert cfg["password"] == "m"
        assert "client" in cfg["ovpn"]
        assert "remote" in cfg["ovpn"]

    def test_render_unknown_location_raises(self):
        from bulk_downloader.vpn_providers import mullvad
        with pytest.raises(ValueError, match="unknown"):
            mullvad.render_config(
                "wireguard", "nowhere-xx", {"account_number": "1234567890123456"}, 0,
            )

    def test_unknown_backend_raises(self):
        from bulk_downloader.vpn_providers import mullvad
        with pytest.raises(ValueError):
            mullvad.render_config(
                "ipsec", "us-nyc-wg-101", {"account_number": "1234567890123456"}, 0,
            )


class TestPIAProvider:
    def test_username_format_validation(self):
        from bulk_downloader.vpn_providers import pia
        ok, _ = pia.test_credentials({"username": "wrongformat", "password": "x"})
        assert ok is False
        # Valid format soft-passes only when the API is unreachable.
        with _force_offline():
            ok, _ = pia.test_credentials(
                {"username": "p1234567", "password": "x"})
        assert ok is True

    def test_missing_creds_fail(self):
        from bulk_downloader.vpn_providers import pia
        ok, _ = pia.test_credentials({})
        assert ok is False

    def test_wireguard_now_supported(self):
        # v3.66.680: PIA WireGuard is implemented (token -> addKey -> render).
        # It is no longer NotImplementedError; with bad creds / no PIA
        # connectivity it fails at the token step with a ValueError.
        from bulk_downloader.vpn_providers import pia
        assert "wireguard" in pia.SUPPORTED_BACKENDS
        with pytest.raises(ValueError):
            pia.render_config(
                "wireguard", "us_atlanta",
                {"username": "p1234567", "password": "x"}, 0,
            )

    def test_openvpn_render(self):
        from bulk_downloader.vpn_providers import pia
        # Audit 2026-05: ca_pem field is now required at render time because
        # the .ovpn template uses TLS mode and OpenVPN refuses to start
        # without a CA. Pre-audit version built the .ovpn without an inline
        # CA, which silently produced configs that failed at connect time.
        fake_ca = "-----BEGIN CERTIFICATE-----\nMIIFAKE\n-----END CERTIFICATE-----\n"
        cfg = pia.render_config(
            "openvpn", "us_atlanta",
            {"username": "p1234567", "password": "x", "ca_pem": fake_ca}, 0,
        )
        assert "atlanta.privacy.network" in cfg["ovpn"]
        assert cfg["username"] == "p1234567"


class TestGenericProvider:
    SAMPLE_WG = (
        "[Interface]\n"
        "PrivateKey = AAA\n"
        "Address = 10.0.0.2/32\n"
        "[Peer]\n"
        "PublicKey = BBB\n"
        "Endpoint = vpn.example.com:51820\n"
        "AllowedIPs = 0.0.0.0/0\n"
    )

    def test_parses_pasted_json_format(self):
        from bulk_downloader.vpn_providers import generic
        import json
        creds = {"configs": json.dumps({"home": self.SAMPLE_WG})}
        ok, msg = generic.test_credentials(creds)
        assert ok is True
        assert "1 WireGuard" in msg

    def test_lists_each_imported_config_as_location(self):
        from bulk_downloader.vpn_providers import generic
        import json
        creds = {"configs": json.dumps({"home": self.SAMPLE_WG, "work": self.SAMPLE_WG})}
        locs = generic.list_locations(creds)
        assert len(locs) == 2
        assert {l["id"] for l in locs} == {"home", "work"}

    def test_renders_wireguard_from_parsed_conf(self):
        from bulk_downloader.vpn_providers import generic
        import json
        creds = {"configs": json.dumps({"home": self.SAMPLE_WG})}
        cfg = generic.render_config("wireguard", "home", creds, 0)
        assert cfg["private_key"] == "AAA"
        assert cfg["address"] == "10.0.0.2/32"
        assert cfg["peer_public_key"] == "BBB"
        assert cfg["endpoint"] == "vpn.example.com:51820"

    def test_detect_kind_works(self):
        from bulk_downloader.vpn_providers.generic import _detect_kind
        assert _detect_kind(self.SAMPLE_WG) == "wireguard"
        assert _detect_kind("client\ndev tun\nremote x.example 1194\n") == "openvpn"
        assert _detect_kind("hello world") == ""

    def test_kind_mismatch_raises(self):
        from bulk_downloader.vpn_providers import generic
        import json
        creds = {"configs": json.dumps({"home": self.SAMPLE_WG})}
        with pytest.raises(ValueError, match="wireguard config"):
            generic.render_config("openvpn", "home", creds, 0)


class TestSelfHostedProvider:
    def test_validates_wg_key_format(self):
        from bulk_downloader.vpn_providers import selfhosted
        # Real WG private keys are 44 chars of base64 ending in =
        ok, why = selfhosted.test_credentials({
            "wg_private_key": "shortkey",
            "wg_address": "10.0.0.2/32",
            "wg_peer_pubkey": "B" * 43 + "=",
            "wg_endpoint": "vpn.example.com:51820",
        })
        assert ok is False
        assert "private_key" in why.lower()

    def test_validates_endpoint_format(self):
        from bulk_downloader.vpn_providers import selfhosted
        ok, why = selfhosted.test_credentials({
            "wg_private_key": "A" * 43 + "=",
            "wg_address": "10.0.0.2/32",
            "wg_peer_pubkey": "B" * 43 + "=",
            "wg_endpoint": "noport",
        })
        assert ok is False
        assert "endpoint" in why.lower()

    def test_full_valid_config_passes(self):
        from bulk_downloader.vpn_providers import selfhosted
        ok, _ = selfhosted.test_credentials({
            "wg_private_key": "A" * 43 + "=",
            "wg_address": "10.0.0.2/32",
            "wg_peer_pubkey": "B" * 43 + "=",
            "wg_endpoint": "vpn.example.com:51820",
        })
        assert ok is True

    def test_renders_wireguard(self):
        from bulk_downloader.vpn_providers import selfhosted
        creds = {
            "wg_private_key": "A" * 43 + "=",
            "wg_address": "10.0.0.2/32",
            "wg_peer_pubkey": "B" * 43 + "=",
            "wg_endpoint": "vpn.example.com:51820",
            "wg_dns": "10.0.0.1",
        }
        cfg = selfhosted.render_config("wireguard", "self-vpn.example.com", creds, 0)
        assert cfg["dns"] == "10.0.0.1"
        assert cfg["endpoint"] == "vpn.example.com:51820"


class TestProtonProvider:
    def test_requires_some_creds(self):
        from bulk_downloader.vpn_providers import proton
        ok, _ = proton.test_credentials({})
        assert ok is False

    def test_openvpn_creds_only_passes(self):
        from bulk_downloader.vpn_providers import proton
        ok, _ = proton.test_credentials({
            "openvpn_username": "abc12345",
            "openvpn_password": "secret",
        })
        assert ok is True

    def test_render_openvpn(self):
        from bulk_downloader.vpn_providers import proton
        # Audit 2026-05: ca_pem required since the rendered .ovpn uses
        # tls-client + remote-cert-tls + verify-x509-name, all of which
        # OpenVPN refuses to load without a CA.
        fake_ca = "-----BEGIN CERTIFICATE-----\nMIIFAKE\n-----END CERTIFICATE-----\n"
        cfg = proton.render_config(
            "openvpn", "us-ny-01",
            {"openvpn_username": "u", "openvpn_password": "p", "ca_pem": fake_ca}, 0,
        )
        assert "us-ny-01.protonvpn.net" in cfg["ovpn"]
        assert cfg["username"] == "u"

    def test_render_wireguard_without_imports_raises(self):
        from bulk_downloader.vpn_providers import proton
        with pytest.raises(ValueError, match="no WireGuard config imported"):
            proton.render_config(
                "wireguard", "us-ny-01",
                {"openvpn_username": "u", "openvpn_password": "p"}, 0,
            )


# ════════════════════════════════════════════════════════════════════
#  vpn_leak_tests.py — probe classification, history, external recording
# ════════════════════════════════════════════════════════════════════

class TestLeakClassification:
    def test_critical_probes_set(self):
        from bulk_downloader.vpn_leak_tests import CRITICAL_PROBES, ProbeId
        assert ProbeId.DNS.value in CRITICAL_PROBES
        assert ProbeId.IPV4.value in CRITICAL_PROBES
        assert ProbeId.IPV6.value in CRITICAL_PROBES
        assert ProbeId.WEBRTC.value in CRITICAL_PROBES
        assert ProbeId.GEO.value not in CRITICAL_PROBES
        assert ProbeId.TIMEZONE.value not in CRITICAL_PROBES

    def test_all_probes_includes_all_six(self):
        from bulk_downloader.vpn_leak_tests import ALL_PROBES
        assert len(ALL_PROBES) == 6

    def test_severity_for(self):
        from bulk_downloader.vpn_leak_tests import _severity_for, Severity
        assert _severity_for("ipv4_leak") == Severity.CRITICAL.value
        assert _severity_for("geolocation_leak") == Severity.WARNING.value


class TestLeakWebRTCExternal:
    def setup_method(self):
        from bulk_downloader import vpn_leak_tests
        vpn_leak_tests._reset_for_tests()

    def test_no_recorded_result_returns_stale_failure(self):
        from bulk_downloader.vpn_leak_tests import _probe_webrtc
        r = _probe_webrtc("tun-x")
        assert r.passed is False
        assert "stale" in (r.error or "")

    def test_recorded_result_returns_success(self):
        from bulk_downloader import vpn_leak_tests
        vpn_leak_tests.record_external_probe_result(
            "tun-x", "webrtc_leak",
            {"passed": True, "details": {"public_ips": []}, "duration_ms": 100},
        )
        r = vpn_leak_tests._probe_webrtc("tun-x")
        assert r.passed is True

    def test_old_recorded_result_is_stale(self):
        from bulk_downloader import vpn_leak_tests
        vpn_leak_tests.record_external_probe_result(
            "tun-x", "webrtc_leak", {"passed": True, "duration_ms": 100},
        )
        # Manually backdate the recorded time
        bucket = vpn_leak_tests._external_results["tun-x"]
        result, _ = bucket["webrtc_leak"]
        bucket["webrtc_leak"] = (result, time.time() - 10000)
        r = vpn_leak_tests._probe_webrtc("tun-x")
        assert r.passed is False
        assert "stale" in (r.error or "")

    def test_webrtc_probe_js_is_self_invoking(self):
        from bulk_downloader.vpn_leak_tests import WEBRTC_PROBE_JS
        assert WEBRTC_PROBE_JS.startswith("(async ()")
        assert "RTCPeerConnection" in WEBRTC_PROBE_JS


class TestLeakHistory:
    def setup_method(self):
        from bulk_downloader import vpn_leak_tests
        vpn_leak_tests._reset_for_tests()

    def test_history_starts_empty(self):
        from bulk_downloader.vpn_leak_tests import get_history, get_latest
        assert get_history("tun-x") == []
        assert get_latest("tun-x") is None

    def test_push_history_caps_at_HISTORY_SIZE(self):
        from bulk_downloader import vpn_leak_tests
        from bulk_downloader.vpn_leak_tests import AggregateResult
        for i in range(vpn_leak_tests.HISTORY_SIZE + 10):
            vpn_leak_tests._push_history("tun-x", AggregateResult(
                tunnel_id="tun-x", timestamp=time.time(),
                probes=[], passed=True, summary=f"#{i}",
            ))
        h = vpn_leak_tests.get_history("tun-x", limit=999)
        assert len(h) == vpn_leak_tests.HISTORY_SIZE

    def test_country_tz_prefix(self):
        from bulk_downloader.vpn_leak_tests import _country_tz_prefix
        assert _country_tz_prefix("us") == "America/"
        assert _country_tz_prefix("DE") == "Europe/"
        assert _country_tz_prefix("jp") == "Asia/"
        assert _country_tz_prefix(None) is None
        assert _country_tz_prefix("xx") is None


# ════════════════════════════════════════════════════════════════════
#  vpn_kill_switch.py — process-level
# ════════════════════════════════════════════════════════════════════

class TestKillSwitch:
    def setup_method(self):
        from bulk_downloader import vpn_kill_switch
        vpn_kill_switch._reset_for_tests()
        vpn_kill_switch.set_auto_recover(False)

    def teardown_method(self):
        from bulk_downloader import vpn_kill_switch
        vpn_kill_switch._reset_for_tests()

    def test_starts_cleared(self):
        from bulk_downloader.vpn_kill_switch import is_killed, get_kill_state
        assert is_killed("t1") is False
        assert get_kill_state("t1") is None

    def test_kill_then_check(self):
        from bulk_downloader.vpn_kill_switch import kill_tunnel, is_killed, get_kill_state
        kill_tunnel("t1", reason="manual test")
        assert is_killed("t1") is True
        s = get_kill_state("t1")
        assert s["state"] == "killed"
        assert s["reason"] == "manual test"

    def test_clear_works(self):
        from bulk_downloader.vpn_kill_switch import kill_tunnel, clear_kill, is_killed
        kill_tunnel("t1", reason="x")
        clear_kill("t1")
        assert is_killed("t1") is False

    def test_callback_fires_on_kill_and_clear(self):
        from bulk_downloader.vpn_kill_switch import (
            kill_tunnel, clear_kill, register_kill_callback,
        )
        events = []
        register_kill_callback(lambda tid, st: events.append((tid, st)))
        kill_tunnel("t1", reason="x")
        clear_kill("t1")
        assert events == [("t1", "killed"), ("t1", "cleared")]

    def test_double_kill_idempotent(self):
        from bulk_downloader.vpn_kill_switch import (
            kill_tunnel, register_kill_callback, get_kill_state,
        )
        events = []
        register_kill_callback(lambda tid, st: events.append((tid, st)))
        kill_tunnel("t1", reason="first")
        kill_tunnel("t1", reason="second")
        # Only one "killed" event despite two kills
        assert events == [("t1", "killed")]

    def test_auto_clear_streak(self):
        """After AUTO_CLEAR_THRESHOLD consecutive passing probes, killed tunnel auto-clears."""
        from bulk_downloader import vpn_kill_switch
        from bulk_downloader.vpn_kill_switch import AUTO_CLEAR_THRESHOLD, kill_tunnel, is_killed

        class _FakeAgg:
            def __init__(self, crit, summary=""):
                self.critical_failures = crit
                self.summary = summary

        kill_tunnel("t1", reason="x")
        # Submit AUTO_CLEAR_THRESHOLD passing probes
        for _ in range(AUTO_CLEAR_THRESHOLD):
            vpn_kill_switch.notify_leak_test_result("t1", _FakeAgg(0))
        assert is_killed("t1") is False

    def test_notify_with_critical_leak_kills(self):
        from bulk_downloader import vpn_kill_switch
        from bulk_downloader.vpn_kill_switch import is_killed

        class _FakeAgg:
            critical_failures = 2
            summary = "DNS leak detected"

        vpn_kill_switch.notify_leak_test_result("t1", _FakeAgg())
        assert is_killed("t1") is True


# ════════════════════════════════════════════════════════════════════
#  vpn_kill_switch_system.py — Windows firewall (logic-only on non-Win)
# ════════════════════════════════════════════════════════════════════

class TestKillSwitchSystem:
    def setup_method(self):
        from bulk_downloader import vpn_kill_switch_system
        vpn_kill_switch_system._reset_for_tests()

    def teardown_method(self):
        from bulk_downloader import vpn_kill_switch_system
        vpn_kill_switch_system._reset_for_tests()

    def test_available_on_non_windows_returns_false(self):
        from bulk_downloader.vpn_kill_switch_system import available
        ok, why = available()
        if sys.platform.startswith("win"):
            # On real Windows, may be True or False depending on netsh+admin
            assert isinstance(ok, bool)
        else:
            assert ok is False
            assert "Windows" in why

    def test_plan_returns_commands(self):
        from bulk_downloader.vpn_kill_switch_system import plan
        cmds = plan("t1", "1.2.3.4:51820")
        assert isinstance(cmds, list)
        assert len(cmds) >= 8  # vpn + loopback + lan + N allows + catch-all

    def test_plan_includes_vpn_endpoint(self):
        from bulk_downloader.vpn_kill_switch_system import plan
        cmds = plan("t1", "1.2.3.4:51820")
        assert any("1.2.3.4" in c for c in cmds)

    def test_plan_includes_catchall_block(self):
        from bulk_downloader.vpn_kill_switch_system import plan
        cmds = plan("t1", "1.2.3.4:51820")
        assert any("action=block" in c for c in cmds)

    def test_plan_includes_rdp_by_default(self):
        from bulk_downloader.vpn_kill_switch_system import plan
        cmds = plan("t1", "1.2.3.4:51820")
        assert any("3389" in c for c in cmds)

    def test_plan_includes_plex_by_default(self):
        from bulk_downloader.vpn_kill_switch_system import plan
        cmds = plan("t1", "1.2.3.4:51820")
        assert any("32400" in c for c in cmds)

    def test_plan_includes_stash_by_default(self):
        from bulk_downloader.vpn_kill_switch_system import plan
        cmds = plan("t1", "1.2.3.4:51820")
        assert any("9999" in c for c in cmds)

    def test_custom_allowlist_replaces_default(self):
        from bulk_downloader.vpn_kill_switch_system import plan
        cmds = plan("t1", "1.2.3.4:51820", allow_ports={"web": (443, "tcp")})
        joined = " ".join(cmds)
        assert "443" in joined
        assert "3389" not in joined  # rdp not in custom allow

    def test_apply_dry_run_does_not_execute(self):
        from bulk_downloader.vpn_kill_switch_system import apply, is_active
        result = apply("t1", "1.2.3.4:51820", dry_run=True)
        assert result["ok"] is True
        assert result["applied"] is False
        assert is_active("t1") is False

    def test_apply_without_endpoint_fails(self):
        from bulk_downloader.vpn_kill_switch_system import apply
        result = apply("t1", "", dry_run=True)
        assert result["ok"] is False
        assert "vpn_endpoint" in result["error"]

    def test_safe_id_strips_unsafe_chars(self):
        from bulk_downloader.vpn_kill_switch_system import _safe_id
        out = _safe_id("tun-/abc;rm -rf /")
        assert "/" not in out
        assert ";" not in out
        assert "rm" in out  # 'rm' is safe alphanumeric


# ════════════════════════════════════════════════════════════════════
#  Cross-module integration smoke
# ════════════════════════════════════════════════════════════════════

class TestCrossModuleSmoke:
    """End-to-end-ish: register a tunnel, kill switch flows through."""

    def setup_method(self):
        from bulk_downloader import vpn, vpn_kill_switch
        vpn._reset_for_tests()
        vpn_kill_switch._reset_for_tests()
        vpn_kill_switch.set_auto_recover(False)

    def teardown_method(self):
        from bulk_downloader import vpn, vpn_kill_switch
        vpn._reset_for_tests()
        vpn_kill_switch._reset_for_tests()

    def test_kill_switch_kills_named_tunnel_via_leak_notify(self):
        from bulk_downloader import vpn, vpn_kill_switch
        tid = vpn.register_tunnel(
            name="x", provider="mullvad", backend="wireguard",
            config={"private_key": "p", "address": "10.0.0.2/32",
                    "peer_public_key": "q", "endpoint": "x.example:51820"},
        )

        class _Agg:
            critical_failures = 1
            summary = "IPv4 leak"

        vpn_kill_switch.notify_leak_test_result(tid, _Agg())
        assert vpn_kill_switch.is_killed(tid)
