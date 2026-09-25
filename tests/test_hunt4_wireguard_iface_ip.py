"""WireGuard interface health must verify the configured address exactly."""

from types import SimpleNamespace

from bulk_downloader import vpn_wireguard

BD_GATE_SCOPE = "module"


def test_iface_address_matches_exact_ipv4(monkeypatch):
    monkeypatch.setattr(vpn_wireguard, "IS_WINDOWS", False)
    monkeypatch.setattr(vpn_wireguard, "IS_LINUX", True)
    monkeypatch.setattr(vpn_wireguard, "IS_DARWIN", False)

    output = {"value": "3: wg-test inet 10.0.0.1/32 scope global wg-test\n"}

    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=output["value"])

    monkeypatch.setattr(vpn_wireguard.subprocess, "run", fake_run)

    assert vpn_wireguard._iface_has_ip("wg-test", "10.0.0.1") is True
    output["value"] = "3: wg-test inet 10.0.0.10/32 scope global wg-test\n"
    assert vpn_wireguard._iface_has_ip("wg-test", "10.0.0.1") is False


def test_iface_address_fallback_keeps_interface_and_ip_together(monkeypatch):
    monkeypatch.setattr(vpn_wireguard, "IS_WINDOWS", False)
    monkeypatch.setattr(vpn_wireguard, "IS_LINUX", True)
    monkeypatch.setattr(vpn_wireguard, "IS_DARWIN", False)

    def fake_run(args, **_kwargs):
        if "show" in args:
            return SimpleNamespace(returncode=1, stdout="")
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "3: wg-test inet 10.0.0.10/32 scope global wg-test\n"
                "4: eth0 inet 10.0.0.1/24 scope global eth0\n"
            ),
        )

    monkeypatch.setattr(vpn_wireguard.subprocess, "run", fake_run)
    assert vpn_wireguard._iface_has_ip("wg-test", "10.0.0.1") is False


# lens (B1): the FIND covers all three platforms; pin the Windows and macOS
# parsers against a neighbouring adapter / a longer address that CONTAINS the
# configured one, the same shape the Linux case pins.
_WIN_IPCONFIG = (
    "\r\nWindows IP Configuration\r\n\r\nUnknown adapter wg-test:\r\n\r\n"
    "   IPv4 Address. . . . . . . . . . . : 10.0.0.10(Preferred) \r\n"
    "   Subnet Mask . . . . . . . . . . . : 255.255.255.255\r\n\r\n"
    "Ethernet adapter Ethernet:\r\n\r\n"
    "   IPv4 Address. . . . . . . . . . . : 10.0.0.1(Preferred) \r\n"
)
_MAC_IFCONFIG = (
    "utun3: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1420\n"
    "\tinet 10.0.0.10 --> 10.0.0.10 netmask 0xffffffff\n"
)


def test_windows_adapter_section_and_exact_ipv4(monkeypatch):
    monkeypatch.setattr(vpn_wireguard, "IS_WINDOWS", True)
    monkeypatch.setattr(vpn_wireguard, "IS_LINUX", False)
    monkeypatch.setattr(vpn_wireguard, "IS_DARWIN", False)
    monkeypatch.setattr(vpn_wireguard.subprocess, "run",
                        lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=_WIN_IPCONFIG))
    assert vpn_wireguard._iface_has_ip("wg-test", "10.0.0.10") is True
    assert vpn_wireguard._iface_has_ip("wg-test", "10.0.0.1") is False


def test_darwin_exact_ipv4(monkeypatch):
    monkeypatch.setattr(vpn_wireguard, "IS_WINDOWS", False)
    monkeypatch.setattr(vpn_wireguard, "IS_LINUX", False)
    monkeypatch.setattr(vpn_wireguard, "IS_DARWIN", True)
    monkeypatch.setattr(vpn_wireguard.subprocess, "run",
                        lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=_MAC_IFCONFIG))
    assert vpn_wireguard._iface_has_ip("utun3", "10.0.0.10") is True
    assert vpn_wireguard._iface_has_ip("utun3", "10.0.0.1") is False
