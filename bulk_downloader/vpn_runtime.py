"""v3.43.60: VPN runtime integration.

The bridge between the VPN modules (vpn, vpn_kill_switch, vpn_config) and
the runner. Keeps the VPN-specific logic out of runner.py — runner.py only
calls three functions: init(), maybe_wait_for_vpn(), and get_socks_url_for_site().

# How sites declare VPN intent

Each site in sites_config.json may have a "vpn" field:

  {
    "site_id": "wowgirls",
    ...
    "vpn": {
      "tunnel_id": "tun-mullvad-us-1",
      "required": true,            // if true, workers refuse to run without the tunnel
      "kill_switch_strict": true   // if true, kill on any leak; else only critical
    }
  }

  // null or missing → no VPN for this site (workers use the system network)

If sites_config.json has a `global_vpn` field (top-level), that's the
default for sites with no explicit vpn config.

# Lifecycle

  init() — called by app.py at startup.
  - Loads vpn_config (calls vpn_config.load() if not already)
  - Registers loaded tunnels with vpn.py
  - Hooks our kill-switch callback so we can pause SiteRunners
  - Starts the leak-test monitor and vpn health monitor

  get_socks_url_for_site(site_id) — called by runner._launch_browser
  - Returns "socks5://127.0.0.1:PORT" if a tunnel is configured + up
  - Returns None if no VPN configured for the site (use system network)
  - Raises VPNError if VPN is required-and-missing or required-and-killed

  maybe_wait_for_vpn(site_id) — called by runner._worker_loop on each iter
  - Returns immediately if VPN not configured or tunnel healthy
  - Blocks (with backoff) if the tunnel is killed — releases when cleared

# Pausing SiteRunners on kill

We can't directly call SiteRunner.pause() from this module (would create
a circular import), so init() accepts a `siterunner_pauser` callable that
takes a site_id. The kill switch callback then calls back into runner.py
via that callable.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Callable, Optional


# ─── Module state ───────────────────────────────────────────────────

# site_id -> tunnel_id map. Loaded from sites_config at init().
_site_to_tunnel: dict[str, str] = {}

# Global default tunnel (used for sites without their own vpn field).
_global_tunnel_id: Optional[str] = None

# site_id -> 'required' flag
_site_required: dict[str, bool] = {}

# Callable that pauses a SiteRunner by site_id. Injected at init().
_siterunner_pauser: Optional[Callable[[str], None]] = None
_siterunner_resumer: Optional[Callable[[str], None]] = None

_initialized = False
_init_lock = threading.RLock()

# Disable gate. Read at CALL time (not import time) so a var set after this
# module is imported -- e.g. a live .env / env write via the Bucket-2 editor --
# still takes effect. Freezing it at import was the deferred-from-Bucket-1 bug:
# a one-time import-time boot-gate a live getter could not retroactively flip.
def _is_disabled() -> bool:
    return os.environ.get("BD_DISABLE_VPN_RUNTIME", "").strip() not in ("", "0", "false", "False")


# ─── Errors ─────────────────────────────────────────────────────────

class VPNRequiredError(Exception):
    """Raised when a site has vpn.required=True but the tunnel isn't available."""


# ─── Init / config loading ──────────────────────────────────────────

def init(
    sites_config: dict,
    siterunner_pauser: Optional[Callable[[str], None]] = None,
    siterunner_resumer: Optional[Callable[[str], None]] = None,
    start_monitors: bool = True,
) -> dict:
    """Wire up VPN runtime. Returns a status dict for logging.

    `sites_config` is the parsed sites_config.json. We read its `sites`
    array and optional `global_vpn` field. Sites without a vpn field
    inherit the global default.

    `siterunner_pauser(site_id)` should pause the SiteRunner instance for
    site_id (existing runner.py SiteRunner has a .pause() method we want
    to call). Same shape for resumer.

    On startup we:
      1. vpn_config.load() — read tunnels.json from disk
      2. vpn_config.register_loaded_tunnels() — register them with vpn.py
      3. Map sites → tunnels from sites_config
      4. Subscribe to kill-switch events
      5. Start health + leak monitors (unless start_monitors=False)
    """
    global _initialized, _siterunner_pauser, _siterunner_resumer
    if _is_disabled():
        return {"ok": True, "skipped": True, "reason": "BD_DISABLE_VPN_RUNTIME"}

    with _init_lock:
        if _initialized:
            return {"ok": True, "skipped": True, "reason": "already initialized"}

        _siterunner_pauser = siterunner_pauser
        _siterunner_resumer = siterunner_resumer

        # 1+2: load + register
        from . import vpn_config, vpn, vpn_kill_switch
        try:
            vpn_config.load()
            count, errors = vpn_config.register_loaded_tunnels()
        except Exception as e:
            sys.stderr.write(f"[vpn-runtime] load/register failed: {e}\n")
            count, errors = 0, [str(e)]

        # 3: build site → tunnel map
        global _global_tunnel_id
        gv = sites_config.get("global_vpn") if isinstance(sites_config, dict) else None
        if isinstance(gv, dict):
            _global_tunnel_id = gv.get("tunnel_id")
        _site_to_tunnel.clear()
        _site_required.clear()
        sites = sites_config.get("sites", []) if isinstance(sites_config, dict) else []
        for s in sites:
            if not isinstance(s, dict):
                continue
            sid = s.get("site_id")
            if not sid:
                continue
            vpncfg = s.get("vpn")
            if isinstance(vpncfg, dict):
                tid = vpncfg.get("tunnel_id")
                if tid:
                    _site_to_tunnel[sid] = tid
                    _site_required[sid] = bool(vpncfg.get("required", False))

        # 4: subscribe to kill-switch
        vpn_kill_switch.register_kill_callback(_on_kill_switch_event)

        # 5: start monitors
        if start_monitors and not os.environ.get("BD_DISABLE_KEEPALIVE"):
            try:
                vpn.start_health_thread()
            except Exception as e:
                sys.stderr.write(f"[vpn-runtime] could not start vpn health thread: {e}\n")
            try:
                from . import vpn_leak_tests
                interval = vpn_config.get_global_settings().get("leak_test_interval_s", 1800)
                vpn_leak_tests.start_periodic_monitor(interval_s=int(interval))
            except Exception as e:
                sys.stderr.write(f"[vpn-runtime] could not start leak monitor: {e}\n")

        _initialized = True
        return {
            "ok": True,
            "tunnels_registered": count,
            "tunnel_register_errors": errors,
            "site_to_tunnel": dict(_site_to_tunnel),
            "global_tunnel_id": _global_tunnel_id,
        }


def shutdown() -> None:
    """Stop monitors, tear down tunnels. atexit-safe."""
    if _is_disabled():
        return
    try:
        from . import vpn_leak_tests
        vpn_leak_tests.stop_periodic_monitor()
    except Exception:
        pass
    try:
        from . import vpn
        vpn.shutdown()
    except Exception:
        pass


# ─── Public API used by runner.py ───────────────────────────────────

def get_tunnel_for_site(site_id: str) -> Optional[str]:
    """Return the tunnel_id that this site should use, or None."""
    if _is_disabled():
        return None
    return _site_to_tunnel.get(site_id) or _global_tunnel_id


def is_vpn_required_for_site(site_id: str) -> bool:
    return bool(_site_required.get(site_id, False))


def get_socks_url_for_site(site_id: str) -> Optional[str]:
    """Return the SOCKS5 URL for this site's tunnel, bringing it up if needed.

    Returns None if no tunnel is configured for the site.
    Raises VPNRequiredError if the tunnel is configured-but-required and not available.
    """
    if _is_disabled():
        return None
    tid = get_tunnel_for_site(site_id)
    if not tid:
        return None

    from . import vpn, vpn_kill_switch
    tunnel = vpn.get_tunnel(tid)
    if tunnel is None:
        if is_vpn_required_for_site(site_id):
            raise VPNRequiredError(
                f"site {site_id!r} requires tunnel {tid!r} but it's not registered"
            )
        sys.stderr.write(f"[vpn-runtime] site {site_id} configured for tunnel {tid} but tunnel missing\n")
        return None

    if tunnel.state != "up":
        # Try to bring it up.
        try:
            ok = vpn.start_tunnel(tid)
        except Exception as e:
            sys.stderr.write(f"[vpn-runtime] could not start tunnel {tid}: {e}\n")
            ok = False
        if not ok and is_vpn_required_for_site(site_id):
            raise VPNRequiredError(
                f"site {site_id!r} requires tunnel {tid!r} but it failed to start: "
                f"{tunnel.last_error or 'unknown'}"
            )
        if not ok:
            return None

    if vpn_kill_switch.is_killed(tid):
        if is_vpn_required_for_site(site_id):
            raise VPNRequiredError(
                f"site {site_id!r}: tunnel {tid!r} kill switch tripped: "
                f"{(vpn_kill_switch.get_kill_state(tid) or {}).get('reason', '')}"
            )
        return None

    return vpn.get_socks_url(tid)


def maybe_wait_for_vpn(site_id: str, timeout: float = 30.0) -> bool:
    """Called by worker loops every iteration. Returns True if work may
    proceed, False if it should skip this iteration (and recheck next time).

    Blocks for up to `timeout` seconds if the site's tunnel is killed but
    has auto-recovery scheduled — gives the cycle a chance to land.
    """
    if _is_disabled():
        return True
    tid = get_tunnel_for_site(site_id)
    if not tid:
        return True

    from . import vpn_kill_switch
    if not vpn_kill_switch.is_killed(tid):
        return True

    # Tunnel is killed. Wait for it to clear or for timeout.
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1.0)
        if not vpn_kill_switch.is_killed(tid):
            return True
    return False


# ─── Internal: kill-switch handler ──────────────────────────────────

def _on_kill_switch_event(tunnel_id: str, new_state: str) -> None:
    """Subscribed at init(). When a tunnel transitions to killed, pause
    every SiteRunner that's using that tunnel. On clear, resume them."""
    affected_sites = [sid for sid, tid in _site_to_tunnel.items() if tid == tunnel_id]
    # Global tunnel: if a site uses the global, also affected when nothing site-specific.
    if tunnel_id == _global_tunnel_id:
        # We can't know all site_ids without sites_config in hand — but the
        # runner.py side handles this via maybe_wait_for_vpn anyway.
        pass

    if new_state == "killed":
        sys.stderr.write(
            f"[vpn-runtime] kill switch tripped on {tunnel_id}; "
            f"affected sites: {affected_sites}\n"
        )
        if _siterunner_pauser is not None:
            for sid in affected_sites:
                try:
                    _siterunner_pauser(sid)
                except Exception as e:
                    sys.stderr.write(f"[vpn-runtime] could not pause {sid}: {e}\n")
    elif new_state == "cleared":
        sys.stderr.write(
            f"[vpn-runtime] kill switch cleared on {tunnel_id}; "
            f"resuming sites: {affected_sites}\n"
        )
        if _siterunner_resumer is not None:
            for sid in affected_sites:
                try:
                    _siterunner_resumer(sid)
                except Exception as e:
                    sys.stderr.write(f"[vpn-runtime] could not resume {sid}: {e}\n")
    # "cycling" — informational only; no action.


# ─── Browser context proxy helper ───────────────────────────────────

def playwright_proxy_for_site(site_id: str) -> Optional[dict]:
    """Return a Playwright BrowserContext.proxy argument for this site,
    or None if no VPN is configured.

    Shape: {"server": "socks5://127.0.0.1:PORT"} — Playwright
    accepts SOCKS5 with the socks5:// scheme.
    """
    url = get_socks_url_for_site(site_id)
    if not url:
        return None
    return {"server": url}


# ─── Test helpers ───────────────────────────────────────────────────

def _reset_for_tests() -> None:
    global _initialized, _siterunner_pauser, _siterunner_resumer, _global_tunnel_id
    _site_to_tunnel.clear()
    _site_required.clear()
    _siterunner_pauser = None
    _siterunner_resumer = None
    _global_tunnel_id = None
    _initialized = False


__all__ = [
    "init", "shutdown",
    "get_tunnel_for_site", "is_vpn_required_for_site",
    "get_socks_url_for_site", "maybe_wait_for_vpn",
    "playwright_proxy_for_site",
    "VPNRequiredError",
]
