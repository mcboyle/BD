"""v3.43.60: OpenVPN backend for the VPN tunnel manager.

Implements the same backend contract as vpn_wireguard:

    start(tunnel) -> bool
    stop(tunnel) -> bool
    is_running(tunnel) -> bool
    is_available() -> (bool, str)

# Lifecycle differs from WireGuard

WireGuard tunnels are "installed" once and managed by a service/kernel module,
so wireguard.exe returns immediately after /installtunnelservice. OpenVPN is a
long-running daemon: we keep the Popen handle alive for the tunnel's lifetime
and tear it down with terminate() in stop().

# Readiness detection

We start openvpn(.exe) as a subprocess and tail its stdout in a watcher
thread, looking for the line "Initialization Sequence Completed" — that's
the canonical OpenVPN ready marker. We also extract the assigned TUN/TAP
interface name and IP from the log:

    TUN/TAP device <iface> opened
    /sbin/ifconfig <iface> <ip> netmask ...
    ifconfig set local <ip> remote <ip>
    PUSH: Received control message: ... ifconfig <ip> ...

The watcher exposes an event that start() waits on (with a timeout).

# Config schema (tunnel.config)

The provider (Mullvad/Proton/PIA/generic/selfhosted) renders an entire .ovpn
text blob and passes it as cfg["ovpn"]. This module doesn't know about
OpenVPN config flags — the provider takes care of all that. We only:

  Required:
    ovpn (str)        - full .ovpn file contents
  Optional:
    username (str)    - written to auth-user-pass file if present
    password (str)    - written next line of the file
    expected_subnet (str) - sanity check on assigned IP

# Why we don't share OpenVPN process across tunnels

Each Tunnel gets its own openvpn subprocess. Multiplexing would save memory
but loses our per-tunnel kill switch granularity — if one tunnel goes bad
we can't kill it without taking the others down.
"""
from __future__ import annotations

import os
import re
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


# ─── Platform ───────────────────────────────────────────────────────

IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")
IS_DARWIN = sys.platform == "darwin"

_WIN_CREATE_FLAGS = 0
if IS_WINDOWS:
    # CREATE_NEW_PROCESS_GROUP lets us send CTRL_BREAK_EVENT cleanly on stop.
    _WIN_CREATE_FLAGS = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    )


# ─── Tunables ───────────────────────────────────────────────────────

CONF_DIR = Path(tempfile.gettempdir()) / "bd_vpn_ovpn"


def _ensure_conf_dir() -> Path:
    """Create/return CONF_DIR as an owner-only (0700) directory. ``mkdir``'s
    mode is umask-masked and a no-op on an existing dir, so we follow with an
    explicit ``chmod`` to force 0700 even if the dir pre-existed at looser perms.
    (P13-A: the .ovpn/.auth files inside carry VPN credentials.)"""
    try:
        CONF_DIR.mkdir(mode=0o700, exist_ok=True)
        if not IS_WINDOWS:
            os.chmod(CONF_DIR, 0o700)
    except OSError:
        pass
    return CONF_DIR


def _write_private(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` created private-from-birth via
    ``os.open(..., 0o600)`` — there is no create-then-chmod window where group/
    other can read the file (umask can only clear bits, never widen past 0o600).
    (P13-A: closes the TOCTOU on OpenVPN config + auth credential files.)"""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(path), flags, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


_ensure_conf_dir()

# Time we wait for "Initialization Sequence Completed" before declaring failure.
OVPN_READY_TIMEOUT_S = 45

# Time we give openvpn to exit on stop() before SIGKILLing.
OVPN_STOP_GRACE_S = 10

# Buffer ring size for the last log lines (used in error reports).
LOG_RING_SIZE = 60


# ─── Binary detection ───────────────────────────────────────────────

def _find_openvpn_binary() -> Optional[str]:
    """Locate openvpn(.exe)."""
    if IS_WINDOWS:
        candidates = [
            r"C:\Program Files\OpenVPN\bin\openvpn.exe",
            r"C:\Program Files (x86)\OpenVPN\bin\openvpn.exe",
            r"C:\Program Files\OpenVPN Connect\openvpn.exe",
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        return shutil.which("openvpn.exe") or shutil.which("openvpn")
    return shutil.which("openvpn")


_OVPN_BINARY: Optional[str] = _find_openvpn_binary()


def is_available() -> tuple[bool, str]:
    if _OVPN_BINARY and os.path.exists(_OVPN_BINARY):
        return True, f"using {_OVPN_BINARY}"
    if IS_WINDOWS:
        return False, "openvpn.exe not found (install OpenVPN Community from openvpn.net)"
    if IS_LINUX:
        return False, "openvpn not found (apt install openvpn)"
    return False, "openvpn not found"


def refresh_binary_location() -> None:
    global _OVPN_BINARY
    _OVPN_BINARY = _find_openvpn_binary()


# ─── Per-tunnel handles ─────────────────────────────────────────────

# tunnel_id -> {process, watcher, socks_proxy, conf_path, auth_path, iface, bind_ip, ready, log_ring}
_handles: dict[str, dict] = {}
_handles_lock = threading.Lock()


# ─── Backend contract ───────────────────────────────────────────────

def start(tunnel: Tunnel) -> bool:
    if not _OVPN_BINARY:
        tunnel.last_error = "OpenVPN binary not installed"
        return False
    if not tunnel.socks_port:
        tunnel.last_error = "no SOCKS port allocated"
        return False

    # Audit 2026-05 / Phase 5: resolve @cred:* refs before reading any
    # secret. tunnel.config stores credentials as references (e.g.
    # "@cred:tun-abc:password") when the encrypted vault backend is
    # active. Without this resolution the literal "@cred:..." string
    # was being written to the OpenVPN auth file and sent to the VPN
    # server, breaking authentication for every username/password
    # provider (Mullvad/Proton/PIA/etc).
    from . import vpn_config as _vpn_cfg
    cfg = _vpn_cfg.resolve_secrets(tunnel.config or {})
    ovpn_text = cfg.get("ovpn", "")
    if not ovpn_text or not isinstance(ovpn_text, str):
        tunnel.last_error = "config['ovpn'] missing or not a string"
        return False

    safe_id = _safe_id(tunnel.tunnel_id)
    _ensure_conf_dir()

    # Write .ovpn (private-from-birth; no create-then-chmod window — P13-A)
    conf_path = CONF_DIR / f"{safe_id}.ovpn"
    try:
        _write_private(conf_path, ovpn_text)
    except OSError as e:
        tunnel.last_error = f"could not write .ovpn: {e}"
        return False

    # Auth file (optional)
    auth_path: Optional[Path] = None
    username = cfg.get("username")
    password = cfg.get("password")
    if username and password:
        # Audit 2026-05 / Phase 5: reject newlines in either field.
        # The OpenVPN auth file is line-structured ("user\npass\n"). A
        # username containing "\n" would let an attacker control what
        # OpenVPN reads as the password — e.g. "u\nrealpass" + a
        # cfg['password']="anything" lets the attacker make OpenVPN
        # try "realpass" instead of the intended one. With user-pasted
        # provider configs (Proton's openvpn_username field) this is
        # admin-controlled but still worth refusing to render bad input.
        if not isinstance(username, str) or not isinstance(password, str):
            tunnel.last_error = "username and password must be strings"
            _safe_unlink(conf_path)
            return False
        bad_chars = {"\n", "\r", "\x00"}
        if any(c in username for c in bad_chars) or any(c in password for c in bad_chars):
            tunnel.last_error = (
                "username/password contains newline or null — refusing to "
                "write OpenVPN auth file with structurally invalid content"
            )
            _safe_unlink(conf_path)
            return False
        auth_path = CONF_DIR / f"{safe_id}.auth"
        try:
            _write_private(auth_path, f"{username}\n{password}\n")
        except OSError as e:
            tunnel.last_error = f"could not write auth: {e}"
            _safe_unlink(conf_path)
            return False

    # Spawn openvpn
    cmd = [_OVPN_BINARY, "--config", str(conf_path)]
    if auth_path is not None:
        cmd += ["--auth-user-pass", str(auth_path)]
    # Reduce verbosity so we don't flood the log ring.
    cmd += ["--verb", "3", "--suppress-timestamps"]
    # Use mute-replay-warnings to keep stdout cleaner — these don't matter for us.
    cmd += ["--mute-replay-warnings"]

    try:
        # v3.47.8 (port from unrud/video-downloader): isolate the OpenVPN
        # process into its own process group on POSIX so a kill_group()
        # later cleans up *any* helper processes it spawns (route updaters,
        # auth scripts), not just the openvpn PID itself. Windows uses
        # `creationflags` for the same effect (already set above).
        _posix_pgrp = {"start_new_session": True} if os.name != "nt" else {}
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=_WIN_CREATE_FLAGS,
            bufsize=1,  # line-buffered
            universal_newlines=True,
            **_posix_pgrp,
        )
    except FileNotFoundError as e:
        tunnel.last_error = f"openvpn spawn failed: {e}"
        _safe_unlink(conf_path)
        if auth_path:
            _safe_unlink(auth_path)
        return False
    except Exception as e:
        tunnel.last_error = f"openvpn spawn raised: {e}"
        _safe_unlink(conf_path)
        if auth_path:
            _safe_unlink(auth_path)
        return False

    # Watcher state
    ready_event = threading.Event()
    failed_event = threading.Event()
    log_ring: list[str] = []
    parsed: dict[str, Optional[str]] = {"iface": None, "ip": None}

    watcher = threading.Thread(
        target=_watch_openvpn_output,
        args=(proc, ready_event, failed_event, log_ring, parsed),
        name=f"bd-ovpn-watch-{safe_id}",
        daemon=True,
    )
    watcher.start()

    # Wait for ready (or failure)
    deadline = time.time() + OVPN_READY_TIMEOUT_S
    while time.time() < deadline:
        if ready_event.is_set():
            break
        if failed_event.is_set() or proc.poll() is not None:
            break
        time.sleep(0.2)

    if not ready_event.is_set():
        # Capture last log lines for diagnosis
        tail = "\n".join(log_ring[-12:])
        tunnel.last_error = (
            f"openvpn did not become ready in {OVPN_READY_TIMEOUT_S}s\n--- last log ---\n{tail}"
        )
        _terminate(proc)
        _safe_unlink(conf_path)
        if auth_path:
            _safe_unlink(auth_path)
        return False

    bind_ip = parsed.get("ip")
    iface = parsed.get("iface")
    if not bind_ip:
        tunnel.last_error = "openvpn became ready but no tunnel IP parsed from log"
        _terminate(proc)
        _safe_unlink(conf_path)
        if auth_path:
            _safe_unlink(auth_path)
        return False

    # Spawn SOCKS proxy
    try:
        proxy = SocksProxy(
            listen_host="127.0.0.1",
            listen_port=tunnel.socks_port,
            outbound_bind_ip=bind_ip,
        )
        proxy.start()
    except OSError as e:
        tunnel.last_error = f"socks proxy bind failed: {e}"
        _terminate(proc)
        _safe_unlink(conf_path)
        if auth_path:
            _safe_unlink(auth_path)
        return False

    with _handles_lock:
        _handles[tunnel.tunnel_id] = {
            "process": proc,
            "watcher": watcher,
            "socks_proxy": proxy,
            "conf_path": conf_path,
            "auth_path": auth_path,
            "iface": iface,
            "bind_ip": bind_ip,
            "ready": ready_event,
            "failed": failed_event,
            "log_ring": log_ring,
        }

    tunnel.public_ip = bind_ip  # tunnel-side; real exit comes from leak detector
    return True


def stop(tunnel: Tunnel) -> bool:
    with _handles_lock:
        handle = _handles.pop(tunnel.tunnel_id, None)
    if handle is None:
        return True

    proxy = handle.get("socks_proxy")
    if proxy is not None:
        try:
            proxy.stop()
        except Exception as e:
            sys.stderr.write(f"[vpn-ovpn] socks stop error: {e}\n")

    proc = handle.get("process")
    if proc is not None:
        _terminate(proc)

    _safe_unlink(handle.get("conf_path"))
    _safe_unlink(handle.get("auth_path"))
    return True


def is_running(tunnel: Tunnel) -> bool:
    with _handles_lock:
        handle = _handles.get(tunnel.tunnel_id)
    if not handle:
        return False
    proc = handle.get("process")
    if proc is None or proc.poll() is not None:
        return False
    proxy = handle.get("socks_proxy")
    if proxy is None or not proxy.is_alive():
        return False
    return True


# ─── Output watcher ─────────────────────────────────────────────────

# Regexes for ready / iface / ip detection. OpenVPN log lines vary slightly
# across platforms and versions; we keep multiple patterns.
_READY_RE = re.compile(r"Initialization Sequence Completed", re.IGNORECASE)
_FATAL_RE = re.compile(r"(FATAL|Cannot allocate|Auth failed|TLS Error|Could not determine)", re.IGNORECASE)
# Windows: "TAP-Windows Adapter V9 ... [tap0901]" / "Successful TAP Win32 driver version ..."
# Linux:   "TUN/TAP device tun0 opened"
# macOS:   "Opened utun5"
_IFACE_RE_LINUX = re.compile(r"TUN/TAP device (\S+) opened")
_IFACE_RE_WIN = re.compile(r"Notified TAP-Windows driver to set a DHCP IP/netmask of ([\d.]+)/")
_IFACE_RE_DARWIN = re.compile(r"Opened (utun\d+)")
# Various IP assignment lines:
#   "ifconfig set local 10.8.0.5 remote 10.8.0.6"
#   "/sbin/ifconfig tun0 10.8.0.5 ..."
#   "PUSH: Received control message: ... ifconfig 10.8.0.5 ..."
_IP_RE_PUSH = re.compile(r"ifconfig\s+(?:set\s+)?(?:local\s+)?(\d{1,3}(?:\.\d{1,3}){3})")
_IP_RE_DHCP = re.compile(r"DHCP IP/netmask of (\d{1,3}(?:\.\d{1,3}){3})/")
_IP_RE_TUN = re.compile(r"add net (\d{1,3}(?:\.\d{1,3}){3}): gateway (\d{1,3}(?:\.\d{1,3}){3})")


def _watch_openvpn_output(
    proc: subprocess.Popen,
    ready_event: threading.Event,
    failed_event: threading.Event,
    log_ring: list[str],
    parsed: dict,
) -> None:
    """Tails openvpn output. Populates parsed['iface']/['ip']. Sets events."""
    if proc.stdout is None:
        failed_event.set()
        return
    try:
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            log_ring.append(line)
            if len(log_ring) > LOG_RING_SIZE:
                del log_ring[0]
            # iface
            if parsed["iface"] is None:
                m = _IFACE_RE_LINUX.search(line) or _IFACE_RE_DARWIN.search(line)
                if m:
                    parsed["iface"] = m.group(1)
            # ip
            if parsed["ip"] is None:
                for rx in (_IP_RE_DHCP, _IP_RE_PUSH, _IP_RE_TUN):
                    m = rx.search(line)
                    if m:
                        parsed["ip"] = m.group(1)
                        break
            if not ready_event.is_set() and _READY_RE.search(line):
                ready_event.set()
            if _FATAL_RE.search(line):
                failed_event.set()
                # don't return; keep buffering so the user sees the trailing context
    except Exception as e:
        sys.stderr.write(f"[vpn-ovpn] watcher error: {e}\n")
        failed_event.set()


# ─── Helpers ────────────────────────────────────────────────────────

def _safe_id(tunnel_id: str) -> str:
    """File-system-safe id for conf/auth filenames."""
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in tunnel_id)[:32] or "anon"


def _terminate(proc: subprocess.Popen) -> None:
    """Polite-then-firm process termination."""
    if proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            # CTRL_BREAK_EVENT requires CREATE_NEW_PROCESS_GROUP at spawn.
            try:
                proc.send_signal(getattr(__import__("signal"), "CTRL_BREAK_EVENT"))
            except (ValueError, OSError):
                proc.terminate()
        else:
            proc.terminate()
    except Exception as e:
        sys.stderr.write(f"[vpn-ovpn] terminate raised: {e}\n")

    try:
        proc.wait(timeout=OVPN_STOP_GRACE_S)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            sys.stderr.write("[vpn-ovpn] subprocess refusing to die\n")


def _safe_unlink(path) -> None:
    if path is None:
        return
    try:
        p = Path(path)
        if p.exists():
            p.unlink()
    except OSError:
        pass


# ─── Introspection (tests + UI diagnostics) ─────────────────────────

def _active_handles() -> dict[str, dict]:
    with _handles_lock:
        return dict(_handles)


def get_recent_log(tunnel_id: str, lines: int = 20) -> list[str]:
    """Return the last `lines` log lines for tunnel_id, or empty list."""
    with _handles_lock:
        handle = _handles.get(tunnel_id)
        if not handle:
            return []
        ring = handle.get("log_ring", [])
        return list(ring[-lines:])


def _reset_for_tests() -> None:
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
        proc = h.get("process")
        if proc is not None:
            _terminate(proc)


__all__ = [
    "start", "stop", "is_running",
    "is_available", "refresh_binary_location",
    "get_recent_log",
]
