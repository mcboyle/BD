"""v3.43.60: VPN tunnel manager (core).

Owns the in-memory Tunnel registry, allocates local SOCKS5 ports, dispatches
lifecycle to backend modules (vpn_wireguard, vpn_openvpn — built in steps 2/3),
and runs a periodic health-check daemon. Platform-agnostic: Windows-specific
binaries and TAP/wg-quick details live in the backend modules; netsh firewall
rules live in vpn_kill_switch_system (step 7).

# Responsibilities

  - Allocate / release SOCKS5 ports from a pool
  - Register, start, stop, cycle Tunnels (by id)
  - Dispatch backend.start / .stop / .is_running to wireguard or openvpn modules
  - Periodically health-check every "up" tunnel
  - Surface a redacted Tunnel dict for the UI (private keys never leave)

# State per tunnel (in memory only here)

    Tunnel:
      tunnel_id, name, provider, backend ('wireguard'|'openvpn')
      location, config (dict, may hold secrets)
      socks_port (allocated when starting)
      state ('down'|'starting'|'up'|'failing'|'stopping')
      started_at, last_health_check
      health_ok, failure_count, public_ip, last_error

Persistence lives in vpn_config.py (step 9). Backends own their own subprocess
handles and write/read their own .conf files under config/vpn/<tunnel_id>/.

# Threads

One daemon thread, "bd-vpn-health", runs check_health() on every "up" tunnel
every HEALTH_CHECK_INTERVAL_S seconds. Gated by BD_DISABLE_KEEPALIVE — same
convention as session_keeper, download_window, storage_tier. Tests set it
and the thread never starts.

# Concurrency

Single module-level RLock `_lock` guards `_tunnels` (the registry) and the
PortPool's internal allocation set. Backend.start/.stop are called OUTSIDE
the lock so a slow subprocess doesn't block other registry operations.

# Why not a class singleton

Matches the codebase pattern: session_keeper.py, download_window.py, and
storage_tier.py all use module-level state with module-level locks rather
than singletons. New module follows convention.

# Public API (called by app.py + runner.py)

    register_tunnel(name, provider, backend, location=None, config=None) -> str
    unregister_tunnel(tunnel_id) -> bool
    start_tunnel(tunnel_id) -> bool
    stop_tunnel(tunnel_id) -> bool
    cycle_tunnel(tunnel_id) -> bool       # stop + start
    get_tunnel(tunnel_id) -> Tunnel | None
    list_tunnels() -> list[Tunnel]
    get_socks_url(tunnel_id) -> str | None
    check_health(tunnel_id) -> dict       # stub here; full impl in step 6
    start_health_thread()                 # called from app.py startup
    shutdown()                            # atexit
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional


# ─── Constants ───────────────────────────────────────────────────────

# SOCKS5 ports live in this range. 100 ports = comfortably more than the
# realistic worker fanout (32 workers × few sites). 1080 is the IANA-
# registered SOCKS port — using it as the start of our pool is a hint to
# anyone reading netstat that these are SOCKS endpoints.
SOCKS_PORT_START = 1080
SOCKS_PORT_END = 1180

# Health-check interval. Aggressive enough to catch a dropped tunnel
# quickly; conservative enough that the IP-check API (api.ipify.org or
# similar) doesn't see us as a scraper.
HEALTH_CHECK_INTERVAL_S = 30
HEALTH_CHECK_TIMEOUT_S = 5

# After this many consecutive failed health checks, a tunnel transitions
# from "up" to "failing". The kill switch (step 6) acts on "failing".
MAX_FAILURE_COUNT = 3

# Allowed values. New providers in step 5 must register here.
VALID_BACKENDS = ("wireguard", "openvpn")
VALID_PROVIDERS = ("mullvad", "proton", "pia", "generic", "selfhosted")
VALID_STATES = ("down", "starting", "up", "failing", "stopping")

# Keys whose values get redacted in to_dict(). Substring match, case-
# insensitive. Keep wide — false positives (showing "***" for a benign
# key) are far cheaper than leaking a WireGuard private key to the UI.
# VPN-domain conservative extra (Option A, documented policy): vpn deliberately
# over-redacts anything containing these bare hints -- "false positives are
# cheaper than leaking a private key". This is UNIONed with the shared config-SoT
# floor (site_editor.is_secret_config_key), which adds the leak-closure the old
# hint tuple missed (cookies / passphrase). Net effect: strictly MORE coverage
# than before, with the deliberate conservative posture preserved.
# CANONICAL VPN secret-key hints (v3.66.773). This is the single source of truth
# for the VPN-domain redaction hints; vpn_config.py derives from it (never keeps
# its own copy) so the two can no longer drift. "account_number" was missing here
# while vpn_config had it -- a real leak: vpn._redact_config passed an
# account_number value through in cleartext (is_secret_config_key does not catch it).
_SECRET_KEY_HINTS = (
    "key", "password", "passwd", "token", "secret",
    "auth", "credential", "private", "account_number",
)


# ─── Helpers ─────────────────────────────────────────────────────────

def _now() -> float:
    return time.time()


def _redact_config(cfg: dict) -> dict:
    """Return a copy of `cfg` with secret-looking values replaced by '***'.

    REDACT-SOT: routes through the shared config-domain SoT
    (``site_editor.is_secret_config_key``) UNIONed with the VPN-domain
    conservative hints (bare key/private/auth), preserving vpn's deliberate
    over-redaction policy while gaining the shared floor's leak-closure (cookies /
    passphrase were previously missed). Lazy import avoids a static import edge."""
    from .site_editor import is_secret_config_key
    if not isinstance(cfg, dict):
        return {}
    out: dict = {}
    for k, v in cfg.items():
        kl = str(k).lower()
        if is_secret_config_key(str(k)) or any(h in kl for h in _SECRET_KEY_HINTS):
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = _redact_config(v)
        else:
            out[k] = v
    return out


# ─── Tunnel model ────────────────────────────────────────────────────

@dataclass
class Tunnel:
    """A single tunnel definition + runtime state.

    `config` holds provider-specific data: WireGuard private key, public
    key, endpoint, allowed-ips, DNS, OR OpenVPN .ovpn path + auth pair.
    Treated as opaque by this module; backends interpret it.
    """
    tunnel_id: str
    name: str
    provider: str            # one of VALID_PROVIDERS
    backend: str             # one of VALID_BACKENDS
    location: Optional[str] = None
    config: dict = field(default_factory=dict)
    socks_port: int = 0
    state: str = "down"
    started_at: Optional[float] = None
    last_health_check: Optional[float] = None
    health_ok: bool = False
    failure_count: int = 0
    public_ip: Optional[str] = None
    last_error: Optional[str] = None

    def to_dict(self, redact_secrets: bool = True) -> dict:
        """Serialize for UI / API responses. Secrets redacted by default."""
        d = asdict(self)
        if redact_secrets:
            d["config"] = _redact_config(d.get("config") or {})
        return d


# ─── Port pool ───────────────────────────────────────────────────────

class PortPool:
    """Allocate / release ports from a configured range.

    Two-stage check on allocate(): first the in-memory `_allocated` set
    (cheap, no syscall), then a bind probe (catches collisions with
    external processes holding a port we haven't allocated yet).
    """

    def __init__(self, start: int, end: int):
        if start > end:
            raise ValueError(f"port range invalid: {start}..{end}")
        self._start = start
        self._end = end
        self._allocated: set[int] = set()
        self._lock = threading.Lock()

    def allocate(self) -> Optional[int]:
        with self._lock:
            for port in range(self._start, self._end + 1):
                if port in self._allocated:
                    continue
                if not _port_bindable(port):
                    continue
                self._allocated.add(port)
                return port
            return None

    def release(self, port: int) -> None:
        with self._lock:
            self._allocated.discard(port)

    def allocated(self) -> set[int]:
        with self._lock:
            return set(self._allocated)


def _port_bindable(port: int) -> bool:
    """Return True if we can bind to 127.0.0.1:port right now."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


# ─── Module-level state ──────────────────────────────────────────────

_lock = threading.RLock()
_tunnels: dict[str, Tunnel] = {}
_port_pool = PortPool(SOCKS_PORT_START, SOCKS_PORT_END)

_health_thread: Optional[threading.Thread] = None
_health_stop = threading.Event()


# ─── Registration ────────────────────────────────────────────────────

def register_tunnel(
    name: str,
    provider: str,
    backend: str,
    location: Optional[str] = None,
    config: Optional[dict] = None,
    tunnel_id: Optional[str] = None,
) -> str:
    """Register a tunnel definition. Does not start it.

    Returns the tunnel_id. Raises ValueError on invalid backend/provider
    or duplicate tunnel_id.
    """
    if backend not in VALID_BACKENDS:
        raise ValueError(f"unknown backend: {backend!r} (valid: {VALID_BACKENDS})")
    if provider not in VALID_PROVIDERS:
        raise ValueError(f"unknown provider: {provider!r} (valid: {VALID_PROVIDERS})")
    if not name or not isinstance(name, str):
        raise ValueError("name required (non-empty string)")

    tid = tunnel_id or f"tun-{uuid.uuid4().hex[:12]}"
    with _lock:
        if tid in _tunnels:
            raise ValueError(f"tunnel {tid!r} already registered")
        _tunnels[tid] = Tunnel(
            tunnel_id=tid,
            name=name,
            provider=provider,
            backend=backend,
            location=location,
            config=dict(config or {}),
        )
    return tid


def unregister_tunnel(tunnel_id: str) -> bool:
    """Stop (if running) and remove a tunnel. Returns False if unknown.

    Audit 2026-05 / Phase 5: previously read `tunnel.state` outside the
    lock and skipped stop_tunnel for states like 'stopping' — which left
    the allocated SOCKS port leaked into the pool until process exit.
    stop_tunnel is idempotent on 'down' so we can always call it.
    """
    with _lock:
        if tunnel_id not in _tunnels:
            return False
    # Always call stop_tunnel — it's a no-op on already-down tunnels and
    # ensures the SOCKS port is returned to the pool from any other state.
    stop_tunnel(tunnel_id)
    with _lock:
        _tunnels.pop(tunnel_id, None)
    return True


# ─── Lifecycle ───────────────────────────────────────────────────────

def start_tunnel(tunnel_id: str) -> bool:
    """Allocate a SOCKS port and call backend.start(). Returns success."""
    with _lock:
        tunnel = _tunnels.get(tunnel_id)
        if not tunnel:
            sys.stderr.write(f"[vpn] start: unknown tunnel {tunnel_id!r}\n")
            return False
        if tunnel.state in ("up", "starting"):
            return True
        if tunnel.socks_port == 0:
            port = _port_pool.allocate()
            if port is None:
                tunnel.last_error = "no free SOCKS port"
                sys.stderr.write(f"[vpn] start({tunnel_id}): no free SOCKS port in "
                                 f"{SOCKS_PORT_START}..{SOCKS_PORT_END}\n")
                return False
            tunnel.socks_port = port
        tunnel.state = "starting"
        tunnel.last_error = None

    # Call backend outside the lock — wg-quick / openvpn.exe can be slow
    ok = False
    try:
        backend = _get_backend(tunnel)
        ok = bool(backend.start(tunnel))
    except Exception as e:
        sys.stderr.write(f"[vpn] start({tunnel_id}) backend raised: {e}\n")
        with _lock:
            tunnel.last_error = f"backend start raised: {e}"

    with _lock:
        # Re-fetch in case tunnel was unregistered concurrently
        t = _tunnels.get(tunnel_id)
        if t is None:
            return False
        if ok:
            t.state = "up"
            t.started_at = _now()
            t.failure_count = 0
            t.health_ok = True  # optimistic until first real check
        else:
            t.state = "down"
            if t.socks_port:
                _port_pool.release(t.socks_port)
                t.socks_port = 0
    # E1 (v3.66.494): plugin event surface. Fired through the isolated emit
    # seam AFTER the lock -- a throwing consumer never perturbs tunnel start.
    if ok:
        try:
            from . import plugins as _pl
            _t = _tunnels.get(tunnel_id)
            _pl.emit("vpn.tunnel_up",
                     {"tunnel_id": tunnel_id,
                      "socks_port": (_t.socks_port if _t else 0),
                      "ts": int(time.time())})
        except Exception:
            pass
    return ok


def stop_tunnel(tunnel_id: str) -> bool:
    """Call backend.stop() and release the SOCKS port. Idempotent."""
    with _lock:
        tunnel = _tunnels.get(tunnel_id)
        if not tunnel:
            return False
        if tunnel.state == "down":
            return True
        tunnel.state = "stopping"
        port_to_release = tunnel.socks_port

    try:
        backend = _get_backend(tunnel)
        backend.stop(tunnel)
    except Exception as e:
        sys.stderr.write(f"[vpn] stop({tunnel_id}) backend raised: {e}\n")

    with _lock:
        t = _tunnels.get(tunnel_id)
        if t is None:
            if port_to_release:
                _port_pool.release(port_to_release)
            return True
        if t.socks_port:
            _port_pool.release(t.socks_port)
            t.socks_port = 0
        t.state = "down"
        t.started_at = None
        t.health_ok = False
        t.public_ip = None
    # E1 (v3.66.494): isolated plugin event for a tunnel going down.
    try:
        from . import plugins as _pl
        _pl.emit("vpn.tunnel_down",
                 {"tunnel_id": tunnel_id, "ts": int(time.time())})
    except Exception:
        pass
    return True


def cycle_tunnel(tunnel_id: str) -> bool:
    """Stop and restart a tunnel. Provider-specific endpoint rotation
    happens inside backend.start() if the provider supports it."""
    if not stop_tunnel(tunnel_id):
        return False
    return start_tunnel(tunnel_id)


# ─── Queries ─────────────────────────────────────────────────────────

def get_tunnel(tunnel_id: str) -> Optional[Tunnel]:
    with _lock:
        return _tunnels.get(tunnel_id)


def list_tunnels() -> list[Tunnel]:
    with _lock:
        return list(_tunnels.values())


def get_socks_url(tunnel_id: str) -> Optional[str]:
    """Return a Playwright-compatible SOCKS5 URL, or None if not up."""
    with _lock:
        t = _tunnels.get(tunnel_id)
        if not t or t.state != "up" or not t.socks_port:
            return None
        return f"socks5://127.0.0.1:{t.socks_port}"


# ─── Backend dispatch ────────────────────────────────────────────────

class _StubBackend:
    """Returned when a real backend module hasn't been imported yet (e.g.
    in step 1 before vpn_wireguard.py / vpn_openvpn.py exist, or if a
    user has openvpn.exe missing). Fails closed — start() returns False,
    is_running() returns False — so callers get a clean negative."""

    @staticmethod
    def start(tunnel: Tunnel) -> bool:
        sys.stderr.write(
            f"[vpn] stub backend cannot start {tunnel.tunnel_id} "
            f"(backend={tunnel.backend!r}, provider={tunnel.provider!r}) — "
            f"backend module not installed\n"
        )
        return False

    @staticmethod
    def stop(tunnel: Tunnel) -> bool:
        return True

    @staticmethod
    def is_running(tunnel: Tunnel) -> bool:
        return False


_stub_backend = _StubBackend()


def _get_backend(tunnel: Tunnel):
    """Resolve the backend module for `tunnel`. Falls back to the stub
    if the module isn't importable (e.g. before steps 2/3 land)."""
    if tunnel.backend == "wireguard":
        try:
            from . import vpn_wireguard  # type: ignore
            return vpn_wireguard
        except ImportError:
            return _stub_backend
    if tunnel.backend == "openvpn":
        try:
            from . import vpn_openvpn  # type: ignore
            return vpn_openvpn
        except ImportError:
            return _stub_backend
    raise ValueError(f"unknown backend: {tunnel.backend!r}")


# ─── Health checks ───────────────────────────────────────────────────

def check_health(tunnel_id: str) -> dict:
    """Stub: ask the backend if its subprocess is still alive.

    Full implementation in vpn_kill_switch (step 6): IP probe through
    the SOCKS proxy, DNS leak test, latency measurement. For now we just
    confirm the backend process is up.

    Returns: {ok: bool, public_ip: str | None, dns_ok: bool | None,
              latency_ms: int | None, error: str | None}
    """
    with _lock:
        tunnel = _tunnels.get(tunnel_id)
        if not tunnel:
            return {"ok": False, "error": "unknown tunnel"}
        if tunnel.state not in ("up", "failing"):
            return {"ok": False, "error": f"state={tunnel.state}"}
        snapshot = (tunnel.backend, tunnel.public_ip)

    try:
        backend = _get_backend(tunnel)
        running = bool(backend.is_running(tunnel)) if hasattr(backend, "is_running") else True
    except Exception as e:
        sys.stderr.write(f"[vpn] check_health({tunnel_id}) backend raised: {e}\n")
        return {"ok": False, "error": f"backend check raised: {e}"}

    return {
        "ok": running,
        "public_ip": snapshot[1],
        "dns_ok": None,    # set in step 6
        "latency_ms": None,
        "error": None if running else "backend reports not running",
    }


def _record_health(tunnel_id: str, result: dict) -> None:
    """Apply a health check result to the tunnel's state. Bumps
    failure_count on error, transitions up→failing past MAX_FAILURE_COUNT."""
    with _lock:
        t = _tunnels.get(tunnel_id)
        if not t:
            return
        t.last_health_check = _now()
        if result.get("ok"):
            t.health_ok = True
            t.failure_count = 0
            ip = result.get("public_ip")
            if ip:
                t.public_ip = ip
            if t.state == "failing":
                # Recovered
                t.state = "up"
        else:
            t.health_ok = False
            t.failure_count += 1
            err = result.get("error") or "health check failed"
            t.last_error = err
            if t.state == "up" and t.failure_count >= MAX_FAILURE_COUNT:
                t.state = "failing"
                sys.stderr.write(
                    f"[vpn] tunnel {tunnel_id} → failing after "
                    f"{t.failure_count} consecutive failures: {err}\n"
                )


# ─── Health daemon ───────────────────────────────────────────────────

def _health_loop() -> None:
    """Daemon thread body. Runs check_health on every 'up' or 'failing'
    tunnel every HEALTH_CHECK_INTERVAL_S. Bare-except-free."""
    while not _health_stop.is_set():
        try:
            _run_health_pass()
        except Exception as e:
            sys.stderr.write(f"[vpn] health loop iteration failed: {e}\n")
        # Sleep with prompt wake-up on shutdown
        _health_stop.wait(HEALTH_CHECK_INTERVAL_S)


def _run_health_pass() -> None:
    with _lock:
        targets = [t.tunnel_id for t in _tunnels.values()
                   if t.state in ("up", "failing")]
    for tid in targets:
        try:
            result = check_health(tid)
        except Exception as e:
            sys.stderr.write(f"[vpn] check_health({tid}) raised: {e}\n")
            result = {"ok": False, "error": str(e)}
        _record_health(tid, result)


def start_health_thread() -> None:
    """Start the health-check daemon. Idempotent. Skipped when
    BD_DISABLE_KEEPALIVE is set (tests / CI / debug)."""
    if os.environ.get("BD_DISABLE_KEEPALIVE"):
        return
    global _health_thread
    with _lock:
        if _health_thread is not None and _health_thread.is_alive():
            return
        _health_stop.clear()
        _health_thread = threading.Thread(
            target=_health_loop, name="bd-vpn-health", daemon=True,
        )
        _health_thread.start()


def shutdown() -> None:
    """Stop every tunnel and the health thread. Safe to call multiple
    times. Intended for atexit and test teardown."""
    _health_stop.set()
    with _lock:
        ids = list(_tunnels.keys())
    for tid in ids:
        try:
            stop_tunnel(tid)
        except Exception as e:
            sys.stderr.write(f"[vpn] shutdown stop_tunnel({tid}) raised: {e}\n")


# ─── Introspection (for tests + UI) ──────────────────────────────────

def _allocated_ports_snapshot() -> set[int]:
    """Test-only helper. Returns a copy of the allocated port set."""
    return _port_pool.allocated()


def _reset_for_tests() -> None:
    """Hard reset of module state. ONLY for unit tests — never call from
    production code. After reset, register_tunnel/start_tunnel start
    from a clean slate."""
    global _health_thread
    _health_stop.set()
    if _health_thread is not None:
        # Don't .join() — daemon thread, harmless if it lingers a beat
        _health_thread = None
    with _lock:
        _tunnels.clear()
    # Rebuild the pool so prior allocations don't leak between tests
    global _port_pool
    _port_pool = PortPool(SOCKS_PORT_START, SOCKS_PORT_END)
    _health_stop.clear()
