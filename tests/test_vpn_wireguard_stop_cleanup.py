"""Regression tests for WireGuard teardown using explicit config paths."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _completed(cmd: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")


def test_posix_down_uses_exact_explicit_config_path(monkeypatch, tmp_path):
    from bulk_downloader import vpn_wireguard

    conf_path = tmp_path / "wg-explicit.conf"
    conf_path.write_text("[Interface]\n", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(vpn_wireguard, "IS_WINDOWS", False)
    monkeypatch.setattr(vpn_wireguard, "_WG_BINARY", "/usr/bin/wg-quick")
    monkeypatch.setattr(
        vpn_wireguard.subprocess,
        "run",
        lambda cmd, **kwargs: calls.append(cmd) or _completed(cmd),
    )

    vpn_wireguard._wg_down_quiet("wg-explicit", conf_path)

    assert calls == [["/usr/bin/wg-quick", "down", str(conf_path)]]


def test_windows_down_preserves_service_uninstall_command(monkeypatch, tmp_path):
    from bulk_downloader import vpn_wireguard

    calls: list[list[str]] = []
    monkeypatch.setattr(vpn_wireguard, "IS_WINDOWS", True)
    monkeypatch.setattr(vpn_wireguard, "_WG_BINARY", "wireguard.exe")
    monkeypatch.setattr(
        vpn_wireguard.subprocess,
        "run",
        lambda cmd, **kwargs: calls.append(cmd) or _completed(cmd),
    )

    vpn_wireguard._wg_down_quiet("wg-explicit", tmp_path / "missing.conf")

    assert calls == [["wireguard.exe", "/uninstalltunnelservice", "wg-explicit"]]


def test_missing_posix_config_uses_guarded_linux_link_delete(monkeypatch, tmp_path):
    from bulk_downloader import vpn_wireguard

    calls: list[list[str]] = []
    monkeypatch.setattr(vpn_wireguard, "IS_WINDOWS", False)
    monkeypatch.setattr(vpn_wireguard, "IS_LINUX", True)
    monkeypatch.setattr(
        vpn_wireguard.subprocess,
        "run",
        lambda cmd, **kwargs: calls.append(cmd) or _completed(cmd),
    )

    vpn_wireguard._wg_down_quiet("wg-cleanup1", tmp_path / "missing.conf")

    assert calls == [["ip", "link", "delete", "dev", "wg-cleanup1"]]


def test_missing_config_refuses_unsafe_interface_fallback(monkeypatch, tmp_path):
    from bulk_downloader import vpn_wireguard

    calls: list[list[str]] = []
    monkeypatch.setattr(vpn_wireguard, "IS_WINDOWS", False)
    monkeypatch.setattr(vpn_wireguard, "IS_LINUX", True)
    monkeypatch.setattr(
        vpn_wireguard.subprocess,
        "run",
        lambda cmd, **kwargs: calls.append(cmd) or _completed(cmd),
    )

    vpn_wireguard._wg_down_quiet("wg-safe;rm", tmp_path / "missing.conf")

    assert calls == []


def test_stop_passes_stored_config_path_before_unlink(monkeypatch, tmp_path):
    from bulk_downloader import vpn_wireguard

    conf_path = tmp_path / "wg-stop.conf"
    events: list[tuple[object, ...]] = []
    tunnel_id = "stop-cleanup-regression"
    vpn_wireguard._handles[tunnel_id] = {
        "iface": "wg-stopclean",
        "socks_proxy": None,
        "conf_path": conf_path,
        "bind_ip": "10.0.0.2",
    }
    monkeypatch.setattr(
        vpn_wireguard,
        "_wg_down_quiet",
        lambda *args: events.append(("down", *args)),
    )
    monkeypatch.setattr(
        vpn_wireguard,
        "_safe_unlink",
        lambda path: events.append(("unlink", path)),
    )

    class _Tunnel:
        tunnel_id = "stop-cleanup-regression"

    try:
        assert vpn_wireguard.stop(_Tunnel()) is True
    finally:
        vpn_wireguard._handles.pop(tunnel_id, None)

    assert events == [
        ("down", "wg-stopclean", conf_path),
        ("unlink", conf_path),
    ]
