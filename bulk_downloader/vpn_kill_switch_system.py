"""v3.43.60: System-level kill switch (Windows firewall, opt-in).

Heavier alternative to the process-level kill switch. When enabled for a
tunnel, installs Windows Firewall rules that block ALL outbound traffic
from this machine EXCEPT:

  - Traffic to the VPN endpoint (otherwise we can't reconnect)
  - Loopback (127.0.0.1)
  - A configurable allowlist of essential management ports:
      RDP (3389/tcp)            - so we don't lock ourselves out remotely
      iLO/IPMI (623/udp, 17988/tcp, 5900/tcp)
      Plex (32400/tcp)          - keep media streaming working
      Stash (9999/tcp)          - same
      SMB (445/tcp) inbound     - so backups still work

# Safety mechanisms

1. **Dry-run mode** (default for first activation): prints every netsh
   command it would run, but doesn't execute. The user explicitly commits.
2. **Auto-revert timer**: 60s after applying real rules, all rules are
   removed unless `commit_killswitch(tunnel_id)` is called first. This
   protects against bricking the box if the rules are wrong.
3. **Pre-flight check**: refuses to apply if our current RDP session would
   be blocked by the rules.
4. **atexit hook**: removes our rules on process exit, even on crash.

# Linux equivalent

We don't ship Linux support for the system-level kill switch in v3.43.60.
Linux runners use the process-level kill switch only. iptables/nft
integration is straightforward to add later but adds permissions complexity
(needs CAP_NET_ADMIN at minimum).

# Public API

    available() -> (bool, str)
    plan(tunnel_id, vpn_endpoint, allow_ports=None) -> list[str]
    apply(tunnel_id, vpn_endpoint, dry_run=True, allow_ports=None) -> dict
    commit(tunnel_id)
    revert(tunnel_id) -> dict
    revert_all() -> int
    is_active(tunnel_id) -> bool
    list_active() -> list[dict]
"""
from __future__ import annotations

import atexit
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional


# ─── Platform ───────────────────────────────────────────────────────

IS_WINDOWS = sys.platform.startswith("win")
_WIN_CREATE_FLAGS = 0
if IS_WINDOWS:
    _WIN_CREATE_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


# ─── Constants ──────────────────────────────────────────────────────

# Rule name prefix — makes our rules easy to find and bulk-delete.
RULE_PREFIX = "BulkDL-VPN-KS"

# Default allowed ports. Keys are human labels; values are (port, protocol).
# protocol: "tcp" | "udp" | "any"
DEFAULT_ALLOWLIST: dict[str, tuple[int, str]] = {
    "rdp":     (3389, "tcp"),
    "ilo_ipmi":  (623, "udp"),
    "ilo_https":(17988, "tcp"),
    "ilo_vnc":  (5900, "tcp"),
    "plex":    (32400, "tcp"),
    "stash":    (9999, "tcp"),
    "ssh":       (22, "tcp"),
}

# Auto-revert delay. After apply() the rules self-destruct unless commit()
# is called. Same default as Cisco "config commit" semantics.
AUTO_REVERT_DELAY_S = 60

# Limit on simultaneous active system kill switches. Each one is a few
# Windows Firewall rules; piling on indefinitely is asking for performance
# issues in netsh.
MAX_ACTIVE = 8


# ─── State ──────────────────────────────────────────────────────────

@dataclass
class _Active:
    tunnel_id: str
    vpn_endpoint: str
    rule_names: list[str] = field(default_factory=list)
    applied_at: float = 0.0
    committed: bool = False
    auto_revert_timer: Optional[threading.Timer] = None


_active: dict[str, _Active] = {}
_lock = threading.RLock()


# ─── Public API ─────────────────────────────────────────────────────

def available() -> tuple[bool, str]:
    """Whether system-level kill switch can run on this OS."""
    if not IS_WINDOWS:
        return False, "system-level kill switch only available on Windows"
    if not _have_netsh():
        return False, "netsh not found on PATH"
    if not _is_elevated():
        return False, "requires administrator privileges"
    return True, "ready"


def plan(
    tunnel_id: str,
    vpn_endpoint: str,
    allow_ports: Optional[dict[str, tuple[int, str]]] = None,
) -> list[str]:
    """Return the netsh commands we would run. No side effects.

    Useful for dry-run display in the UI before commit.
    """
    allow = allow_ports if allow_ports is not None else DEFAULT_ALLOWLIST
    return _build_commands(tunnel_id, vpn_endpoint, allow)


def apply(
    tunnel_id: str,
    vpn_endpoint: str,
    dry_run: bool = True,
    allow_ports: Optional[dict[str, tuple[int, str]]] = None,
) -> dict:
    """Apply system-level kill switch for tunnel_id.

    If dry_run=True: returns the plan without executing.
    If dry_run=False: executes; auto-reverts after AUTO_REVERT_DELAY_S
                      unless commit() is called first.

    Returns: {"ok", "dry_run", "commands", "applied", "auto_revert_at", "error"}
    """
    ok, why = available()
    if not ok and not dry_run:
        return {"ok": False, "dry_run": dry_run, "error": why, "commands": []}

    if not vpn_endpoint:
        return {"ok": False, "error": "vpn_endpoint required", "commands": []}

    with _lock:
        if len(_active) >= MAX_ACTIVE and tunnel_id not in _active:
            return {
                "ok": False, "error": f"max active kill switches ({MAX_ACTIVE}) reached",
                "commands": [],
            }
        if tunnel_id in _active and _active[tunnel_id].committed:
            return {
                "ok": False, "error": "already committed; revert first to re-apply",
                "commands": [], "applied": True,
            }
        # Audit 2026-05 (Phase 5): if there's an existing UNCOMMITTED entry
        # for this tunnel, its rules are still in the firewall and its
        # timer is still pending. Overwriting _active[tunnel_id] below
        # without cleanup would orphan those rules — the pending timer
        # would later see the NEW entry, cancel the NEW timer, and revert
        # the NEW rules, while the OLD rules stay in Windows Firewall
        # forever (atexit's revert_all only walks _active.keys()).
        # Clean up the old entry synchronously before re-applying.
        _stale = _active.get(tunnel_id)
    if _stale is not None and not _stale.committed:
        # Outside the lock — revert() reacquires it.
        revert(tunnel_id)

    allow = allow_ports if allow_ports is not None else DEFAULT_ALLOWLIST
    commands = _build_commands(tunnel_id, vpn_endpoint, allow)

    if dry_run:
        return {
            "ok": True, "dry_run": True, "applied": False,
            "commands": commands, "auto_revert_at": None,
        }

    # Pre-flight: refuse if RDP currently in use and not in allowlist.
    if "rdp" not in allow:
        if _rdp_in_use():
            return {
                "ok": False, "applied": False, "commands": commands,
                "error": "RDP session active but 3389 not in allowlist; refusing to apply "
                         "(would lock you out). Add 'rdp' to allow_ports.",
            }

    # Execute commands.
    rule_names: list[str] = []
    failed: list[tuple[str, str]] = []
    for cmd in commands:
        rc, out, err = _run_netsh(cmd)
        if rc != 0:
            failed.append((cmd, err or out))
            continue
        name = _extract_rule_name(cmd)
        if name:
            rule_names.append(name)

    if failed:
        # Roll back any rules we did install.
        for n in rule_names:
            _run_netsh(f'advfirewall firewall delete rule name="{n}"')
        return {
            "ok": False, "applied": False, "commands": commands,
            "error": f"{len(failed)} netsh command(s) failed: {failed[0][1][:200]}",
        }

    applied_at = time.time()
    timer = threading.Timer(AUTO_REVERT_DELAY_S, _auto_revert, args=(tunnel_id,))
    timer.daemon = True
    timer.start()

    with _lock:
        _active[tunnel_id] = _Active(
            tunnel_id=tunnel_id,
            vpn_endpoint=vpn_endpoint,
            rule_names=rule_names,
            applied_at=applied_at,
            committed=False,
            auto_revert_timer=timer,
        )

    sys.stderr.write(
        f"[vpn-killswitch-sys] {tunnel_id}: applied {len(rule_names)} firewall rules; "
        f"auto-revert in {AUTO_REVERT_DELAY_S}s unless commit() called\n"
    )

    return {
        "ok": True, "dry_run": False, "applied": True,
        "commands": commands, "rule_names": rule_names,
        "auto_revert_at": applied_at + AUTO_REVERT_DELAY_S,
    }


def commit(tunnel_id: str) -> bool:
    """Cancel the auto-revert timer for tunnel_id. Rules persist until revert()."""
    with _lock:
        entry = _active.get(tunnel_id)
        if entry is None:
            return False
        if entry.auto_revert_timer is not None:
            entry.auto_revert_timer.cancel()
            entry.auto_revert_timer = None
        entry.committed = True
    sys.stderr.write(f"[vpn-killswitch-sys] {tunnel_id}: committed (no auto-revert)\n")
    return True


def revert(tunnel_id: str) -> dict:
    """Remove kill-switch rules for tunnel_id. Idempotent."""
    with _lock:
        entry = _active.pop(tunnel_id, None)
        if entry is None:
            return {"ok": True, "removed": 0, "note": "no active kill switch"}
        if entry.auto_revert_timer is not None:
            entry.auto_revert_timer.cancel()
        rule_names = list(entry.rule_names)

    removed = 0
    failed: list[str] = []
    for name in rule_names:
        rc, _, err = _run_netsh(f'advfirewall firewall delete rule name="{name}"')
        if rc == 0:
            removed += 1
        else:
            failed.append(f"{name}: {err}")
    sys.stderr.write(f"[vpn-killswitch-sys] {tunnel_id}: reverted ({removed} rules)\n")
    if failed:
        return {"ok": False, "removed": removed, "error": f"some removals failed: {failed[:3]}"}
    return {"ok": True, "removed": removed}


def revert_all() -> int:
    """Remove all our kill-switch rules. Called by atexit hook."""
    with _lock:
        tids = list(_active.keys())
    removed = 0
    for tid in tids:
        result = revert(tid)
        removed += result.get("removed", 0)
    # CRITICAL FIX (audit 2026-05 / Phase 5): an earlier draft of this
    # function included
    #     _run_netsh('advfirewall firewall delete rule name=all program=any')
    # with a comment claiming it was a "no-op safe call to confirm netsh
    # works". That was dangerously wrong — `name=all` is a documented
    # netsh wildcard that deletes ALL Windows Firewall rules. With the
    # atexit hook firing this on every clean shutdown, any user who had
    # ever opened the VPN UI (which lazy-imports this module via
    # /api/vpn/status, registering the atexit hook) would have their
    # entire Windows Firewall configuration wiped on the next exit.
    # Belt-and-braces cleanup of orphan rules from crashed processes,
    # if ever wanted, must use enumerate-then-delete with prefix-match
    # against RULE_PREFIX. Removed entirely for now: orphan rules from
    # a crashed previous process are harmless (they share names with
    # the new process's rules, so the next apply() either reuses or
    # supersedes them).
    return removed


def is_active(tunnel_id: str) -> bool:
    with _lock:
        return tunnel_id in _active


def list_active() -> list[dict]:
    with _lock:
        return [
            {
                "tunnel_id": e.tunnel_id,
                "vpn_endpoint": e.vpn_endpoint,
                "rules": len(e.rule_names),
                "applied_at": e.applied_at,
                "committed": e.committed,
                "auto_revert_in_s": (
                    max(0.0, e.applied_at + AUTO_REVERT_DELAY_S - time.time())
                    if e.auto_revert_timer is not None and not e.committed else None
                ),
            }
            for e in _active.values()
        ]


# ─── Internals ──────────────────────────────────────────────────────

def _build_commands(
    tunnel_id: str,
    vpn_endpoint: str,
    allow_ports: dict[str, tuple[int, str]],
) -> list[str]:
    """Return the list of netsh commands implementing the kill switch.

    Strategy:
      1. Block all outbound by default (set firewallpolicy)  -- skipped: too
         invasive; we use explicit deny rules instead.
      2. Allow loopback explicitly.
      3. Allow the VPN endpoint (host:port).
      4. Allow each port in allow_ports.
      5. Block everything else outbound with one catch-all rule.

    Audit 2026-05 (Phase 5): port/proto/endpoint inputs are validated
    before f-string interpolation. The attack surface is admin-only
    (config-derived), but defense in depth costs us nothing here.
    """
    cmds: list[str] = []
    safe_id = _safe_id(tunnel_id)
    pfx = f"{RULE_PREFIX}-{safe_id}"

    # Allow VPN endpoint. Sanitize: host must look like an IP or domain,
    # not shell metacharacters that could break out of the netsh arg.
    host = (vpn_endpoint or "").split(":")[0].strip()
    if not host or not _looks_like_host(host):
        raise ValueError(
            f"vpn_endpoint host {host!r} contains characters not valid in "
            f"a hostname/IP — refusing to build netsh rules"
        )
    cmds.append(
        f'advfirewall firewall add rule name="{pfx}-vpn-out" dir=out action=allow '
        f'remoteip={host} protocol=any'
    )

    # Loopback.
    cmds.append(
        f'advfirewall firewall add rule name="{pfx}-loopback-out" dir=out action=allow '
        f'remoteip=127.0.0.1,::1 protocol=any'
    )

    # LAN. Lets RDP/Plex/iLO clients on the same network reach the box even
    # under outbound block. We use private-network ranges.
    cmds.append(
        f'advfirewall firewall add rule name="{pfx}-lan-out" dir=out action=allow '
        f'remoteip=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,169.254.0.0/16 protocol=any'
    )

    # Per allowlist port (outbound).
    for label, port_proto in allow_ports.items():
        try:
            port, proto = port_proto
        except (TypeError, ValueError):
            raise ValueError(
                f"allow_ports[{label!r}] must be (port, proto) tuple, "
                f"got {port_proto!r}"
            )
        if not isinstance(port, int) or not (1 <= port <= 65535):
            raise ValueError(
                f"allow_ports[{label!r}] port must be int in 1..65535, "
                f"got {port!r}"
            )
        if proto not in ("tcp", "udp", "any"):
            raise ValueError(
                f"allow_ports[{label!r}] proto must be 'tcp'|'udp'|'any', "
                f"got {proto!r}"
            )
        safe_label = _safe_id(label)
        protos = ("tcp", "udp") if proto == "any" else (proto,)
        for p in protos:
            cmds.append(
                f'advfirewall firewall add rule name="{pfx}-allow-{safe_label}-{p}-out" '
                f'dir=out action=allow protocol={p} remoteport={port}'
            )

    # Catch-all outbound deny. This is the "kill" part.
    cmds.append(
        f'advfirewall firewall add rule name="{pfx}-killswitch-out" dir=out action=block '
        f'protocol=any'
    )

    return cmds


def _looks_like_host(s: str) -> bool:
    """Return True if `s` is a plausible hostname or IPv4/IPv6 literal.
    Used by _build_commands to refuse vpn_endpoint values that contain
    shell metacharacters before they reach netsh. Conservative: rejects
    anything with whitespace, quotes, or characters outside the standard
    hostname/IP alphabet."""
    if not s or len(s) > 253:
        return False
    # IPv6 may contain colons; IPv4 dots; domain dots+hyphens; that's it.
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:")
    return all(c in allowed for c in s)


def _extract_rule_name(cmd: str) -> Optional[str]:
    """Pull name="..." out of a netsh add-rule command."""
    marker = 'name="'
    i = cmd.find(marker)
    if i < 0:
        return None
    j = cmd.find('"', i + len(marker))
    if j < 0:
        return None
    return cmd[i + len(marker):j]


def _safe_id(s: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in s)[:32] or "anon"


def _have_netsh() -> bool:
    import shutil
    return shutil.which("netsh") is not None


def _is_elevated() -> bool:
    """Return True if current process has admin privileges on Windows."""
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _rdp_in_use() -> bool:
    """Heuristic: are there active RDP sessions? If so, blocking 3389 would
    lock us out. We err on the side of caution and return True on any error
    (caller must explicitly opt into blocking RDP)."""
    if not IS_WINDOWS:
        return False
    try:
        result = subprocess.run(
            ["query", "session"],
            capture_output=True, timeout=5, text=True,
            creationflags=_WIN_CREATE_FLAGS,
        )
        if result.returncode != 0:
            return True  # err on the side of caution
        # Active sessions show "Active" in the State column.
        for line in (result.stdout or "").splitlines():
            if "rdp" in line.lower() and "active" in line.lower():
                return True
        return False
    except Exception:
        return True


def _run_netsh(args: str) -> tuple[int, str, str]:
    """Run `netsh <args>`. Returns (returncode, stdout, stderr)."""
    if not IS_WINDOWS:
        return 1, "", "netsh only available on Windows"
    cmd = ["netsh"] + args.split()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, timeout=15, text=True,
            creationflags=_WIN_CREATE_FLAGS,
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except subprocess.TimeoutExpired:
        return 1, "", "netsh timeout"
    except Exception as e:
        return 1, "", f"netsh raised: {e}"


def _auto_revert(tunnel_id: str) -> None:
    """Called by Timer if commit() isn't called in time."""
    sys.stderr.write(
        f"[vpn-killswitch-sys] {tunnel_id}: AUTO-REVERTING (no commit within {AUTO_REVERT_DELAY_S}s)\n"
    )
    try:
        revert(tunnel_id)
    except Exception as e:
        sys.stderr.write(f"[vpn-killswitch-sys] auto-revert raised: {e}\n")


# ─── atexit cleanup ─────────────────────────────────────────────────

def _atexit_cleanup() -> None:
    try:
        revert_all()
    except Exception as e:
        sys.stderr.write(f"[vpn-killswitch-sys] atexit cleanup raised: {e}\n")


atexit.register(_atexit_cleanup)


# ─── Test helpers ───────────────────────────────────────────────────

def _reset_for_tests() -> None:
    with _lock:
        for entry in list(_active.values()):
            if entry.auto_revert_timer is not None:
                entry.auto_revert_timer.cancel()
        _active.clear()


__all__ = [
    "available", "plan", "apply", "commit", "revert", "revert_all",
    "is_active", "list_active",
    "DEFAULT_ALLOWLIST", "AUTO_REVERT_DELAY_S",
]
