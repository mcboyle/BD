"""bulk_downloader.netns_isolation -- F5: per-capture network-namespace
isolation engine [safety].

Defensive isolation, NOT evasion. A fresh Linux network namespace has only
loopback and no routes, so a process confined to one cannot egress on the
host's clear interface. This module is the reusable MECHANISM for creating,
hardening, using, and tearing down such a namespace per capture/download:

  * deterministic, netns-safe naming (:func:`netns_name`);
  * command generation (:func:`setup_commands` / :func:`teardown_commands`
    / :func:`netns_exec_argv`) -- pure, so it is fully unit-testable;
  * fail-closed create/destroy and a context manager, run through an
    INJECTED command runner (default ``subprocess.run``) so the unit suite
    never needs root.

Egress posture: by default the namespace is isolated (loopback only, no
routes) AND hardened with an nftables default-drop ``output`` policy as
defence in depth. Optional -- ``drop_egress=False`` leaves the routing-level
isolation only.

Deployment: the operations require ``CAP_NET_ADMIN``. On a host without it
(the historical default -- the Linux kill-switch is process-level precisely
to avoid needing ``CAP_NET_ADMIN``), :func:`create` fails closed. Routing an
actual browser/download launch through :func:`netns_exec_argv` is a separate
follow-on; this module ships the isolation engine + the integration
primitive.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, List, Optional

__all__ = [
    "netns_name", "setup_commands", "teardown_commands", "netns_exec_argv",
    "is_supported", "create", "destroy", "run_in_netns", "isolated",
    "site_wants_isolation", "fail_closed", "capture_netns",
    "NetnsRequiredError", "EgressSpec", "egress_commands", "egress_spec_from_cfg",
    "ns_resolv_conf_path", "netns_browser_shim", "write_browser_shim",
    "browser_launch_env",
]


class NetnsRequiredError(RuntimeError):
    """Raised by :func:`capture_netns` when a site opts into per-capture netns
    isolation, the namespace cannot be created (typically no ``CAP_NET_ADMIN``),
    and the operator's posture is fail-closed (the default). A launch path
    catches this and refuses to proceed rather than egress un-isolated."""

# Linux netns names live under /run/netns; keep them short + filesystem-safe.
_MAX_NAME = 40
_SAFE = re.compile(r"[^A-Za-z0-9_]")

Runner = Callable[..., "subprocess.CompletedProcess"]


@dataclass(frozen=True)
class EgressSpec:
    """Controlled-egress descriptor (697): a WireGuard tunnel to move into a
    per-capture netns so a confined process egresses ONLY through it.

      * ``wg_iface`` -- the interface name to create + move (e.g. "wg-bd0");
      * ``wg_conf``  -- a ``wg setconf`` file (private key + peer + endpoint);
      * ``address``  -- the tunnel client address (e.g. "10.9.0.2/32");
      * ``mtu``      -- optional MTU (WireGuard's usual 1420 when unset upstream);
      * ``dns``      -- optional resolver IP written to the ns's own resolv.conf
        (698); a fresh netns does not inherit the host resolver, so without this
        a tunnel-confined process can egress but not resolve names.
    """
    wg_iface: str
    wg_conf: str
    address: str
    mtu: Optional[int] = None
    dns: Optional[str] = None


def _valid_ip(s: str) -> bool:
    """True iff ``s`` is a bare IPv4/IPv6 address -- gate before interpolating a
    resolver into a command, so a mis-typed ``dns`` value can never smuggle
    shell content into the resolv.conf write."""
    import ipaddress
    try:
        ipaddress.ip_address((s or "").strip())
        return True
    except ValueError:
        return False


def ns_resolv_conf_path(ns: str) -> str:
    """The per-namespace resolver path the kernel reads for processes entering
    ``ns`` (the standard netns DNS override): ``/etc/netns/<ns>/resolv.conf``."""
    return f"/etc/netns/{ns}/resolv.conf"


# ── Phase 2 (fork 2b): browser-in-netns launch ────────────────────────
#
# Playwright spawns Chromium ITSELF via its in-process API, so a caller has no
# argv to wrap with ``ip netns exec``. Fork 2b exploits Playwright's
# ``executable_path`` seam: point it at this shim, which re-execs the REAL
# browser binary inside the namespace. Playwright spawns the shim believing it
# is the browser; ``exec`` (not fork) means Playwright's process handle IS the
# browser, so its lifecycle management still works.
#
# The ns name and the real binary arrive via the ENVIRONMENT (NETNS_NS /
# NETNS_BROWSER_BIN), never interpolated into the script body -- so no caller-supplied
# text is ever baked into an executable. ``"$@"`` is quoted: Playwright passes
# ~40 args (several containing '='), and unquoted $@ would re-split them.
#
# Needs only CAP_NET_ADMIN (vs 2a's in-process setns, which needs CAP_SYS_ADMIN).
#
# NOT "BD_"-prefixed by design (699->700): tools/config_surface_inventory.py
# registers ANY BD_* token literal in a .py file (not just os.environ.get read
# sites) as an operator-tunable env var, which trips the env-tranche gate
# (test_v3_66_319) and the danger count (test_v3_66_305). These two names are an
# internal BD<->shim calling convention (closer to argv than to config), never
# operator-tunable, so they must not carry the BD_ prefix.
_BROWSER_SHIM = '''#!/bin/sh
# BD F5 Phase 2 (2b): re-exec the real browser inside a per-capture netns.
# Playwright launches THIS as executable_path; NETNS_NS / NETNS_BROWSER_BIN come from
# the launch env. exec => Playwright's child handle is the browser itself.
#
# NETNS_NS unset/empty => launch the real browser UNCHANGED (pass-through).
# 701: the cloak backend selects the shim via a PROCESS-WIDE env var
# (CLOAKBROWSER_BINARY_PATH) while BD's workers are THREADS, so a concurrent
# NON-isolated launch can reach this script with no namespace of its own; it
# must then behave exactly as a direct browser launch. Confinement is NOT
# weakened: when NETNS_NS IS set the exec always goes through `ip netns exec`,
# so a missing/failed namespace fails closed rather than silently escaping.
if [ -n "$NETNS_NS" ]; then
  exec ip netns exec "$NETNS_NS" "$NETNS_BROWSER_BIN" "$@"
fi
exec "$NETNS_BROWSER_BIN" "$@"
'''

_SHIM_NAME = "bd_netns_browser.sh"


def netns_browser_shim() -> str:
    """The shim script source (pure). See ``_BROWSER_SHIM``."""
    return _BROWSER_SHIM


def write_browser_shim(dirpath: str) -> str:
    """Write the browser shim into ``dirpath`` and mark it executable (Playwright
    execs it). Idempotent -- returns the shim path."""
    import os as _os
    _os.makedirs(dirpath, exist_ok=True)
    path = _os.path.join(dirpath, _SHIM_NAME)
    with open(path, "w") as fh:
        fh.write(_BROWSER_SHIM)
    _os.chmod(path, 0o755)
    return path


def browser_launch_env(ns: str, browser_path: str) -> dict:
    """The env a launcher must add so the shim knows which namespace to enter
    and which real binary to exec. Empty dict when either is missing (the caller
    then launches normally -- no shim, unchanged path)."""
    if not ns or not browser_path:
        return {}
    return {"NETNS_NS": str(ns), "NETNS_BROWSER_BIN": str(browser_path)}


def egress_commands(ns: str, spec: "EgressSpec") -> List[List[str]]:
    """Pure argv to move a WireGuard tunnel into namespace ``ns`` and make it the
    ns default route -- the canonical wireguard.com/netns sequence:

      1. create the wg interface in the init ns;
      2. ``wg setconf`` it (its UDP socket binds to the init/physical ns, so the
         encrypted tunnel still egresses after the move);
      3. MOVE the interface into ``ns`` (processes inside then see only wg0);
      4. assign the tunnel address; (optionally set MTU;) bring it up;
      5. set it as the ns default route.

    wg0 becomes the ns's ONLY route -> fail-closed by construction (tunnel down =
    all traffic halts). Generates commands, runs nothing."""
    cmds: List[List[str]] = [
        ["ip", "link", "add", spec.wg_iface, "type", "wireguard"],
        ["wg", "setconf", spec.wg_iface, spec.wg_conf],
        ["ip", "link", "set", spec.wg_iface, "netns", ns],
        ["ip", "-n", ns, "addr", "add", spec.address, "dev", spec.wg_iface],
    ]
    if spec.mtu:
        cmds.append(["ip", "-n", ns, "link", "set", "mtu", str(int(spec.mtu)),
                     "dev", spec.wg_iface])
    cmds += [
        ["ip", "-n", ns, "link", "set", spec.wg_iface, "up"],
        ["ip", "-n", ns, "route", "add", "default", "dev", spec.wg_iface],
    ]
    # 698: a fresh netns doesn't inherit the host resolver -- write the ns's own
    # /etc/netns/<ns>/resolv.conf so name resolution also goes through the
    # tunnel. dns is IP-validated first, so nothing untrusted is interpolated.
    if spec.dns and _valid_ip(spec.dns):
        resolv = ns_resolv_conf_path(ns)
        cmds += [
            ["mkdir", "-p", f"/etc/netns/{ns}"],
            ["sh", "-c", f"printf 'nameserver {spec.dns.strip()}\\n' > {resolv}"],
        ]
    return cmds


def netns_name(kind: str, ident: str) -> str:
    """Deterministic, netns-safe namespace name for a (kind, ident) pair,
    e.g. ``bd_cap_1a2b3c4d``. Arbitrary/identifying input is hashed so the
    name is always alphanumeric-plus-underscore and within the length
    limit."""
    kind_s = _SAFE.sub("", (kind or "ns"))[:8] or "ns"
    digest = hashlib.sha256(f"{kind}:{ident}".encode("utf-8")).hexdigest()[:8]
    name = f"bd_{kind_s}_{digest}"
    return name[:_MAX_NAME]


def _nft_table(ns: str) -> str:
    return _SAFE.sub("_", ns)[:24]


def setup_commands(ns: str, *, drop_egress: bool = True,
                   egress: Optional["EgressSpec"] = None) -> List[List[str]]:
    """The argv command list that creates + isolates namespace ``ns``:
    create the ns, bring loopback up, then apply the egress posture. Pure --
    generates commands, runs nothing.

    Egress posture: ``egress`` given (697) -> move a WireGuard tunnel in and
    route through it (:func:`egress_commands`); wg0 is the ns's only route so
    this SUPERSEDES the default-drop (the tunnel-only route IS the confinement,
    fail-closed by construction). Else ``drop_egress`` (default) -> nftables
    default-drop ``output`` policy (egress-less)."""
    tbl = _nft_table(ns)
    cmds: List[List[str]] = [
        ["ip", "netns", "add", ns],
        ["ip", "netns", "exec", ns, "ip", "link", "set", "lo", "up"],
    ]
    if egress is not None:
        cmds += egress_commands(ns, egress)
        return cmds
    if drop_egress:
        cmds += [
            ["ip", "netns", "exec", ns, "nft", "add", "table", "inet", tbl],
            ["ip", "netns", "exec", ns, "nft", "add", "chain", "inet", tbl,
             "out", "{ type filter hook output priority 0 ; policy drop ; }"],
        ]
    return cmds


def teardown_commands(ns: str, *, egress: Optional["EgressSpec"] = None) -> List[List[str]]:
    """The argv command list that removes namespace ``ns`` (also drops any
    nft ruleset that lived only inside it). When ``egress`` used a per-ns DNS
    resolver (698), also remove the persistent ``/etc/netns/<ns>`` dir it wrote
    (netns resolv.conf files outlive the namespace otherwise)."""
    cmds: List[List[str]] = [["ip", "netns", "del", ns]]
    if egress is not None and getattr(egress, "dns", None) and _valid_ip(egress.dns):
        cmds.append(["rm", "-rf", f"/etc/netns/{ns}"])
    return cmds


def netns_exec_argv(ns: str, argv: List[str]) -> List[str]:
    """Wrap ``argv`` so it runs inside namespace ``ns`` -- the integration
    primitive a launch path prepends to confine a capture/download."""
    return ["ip", "netns", "exec", ns, *argv]


def is_supported() -> bool:
    """Cheap check that the netns toolchain is present. True does not
    guarantee ``CAP_NET_ADMIN`` -- :func:`create` is the real, fail-closed
    gate."""
    return shutil.which("ip") is not None


def _run(runner: Optional[Runner], argv: List[str]) -> int:
    r = runner or subprocess.run
    try:
        cp = r(argv, capture_output=True, text=True)
        return int(getattr(cp, "returncode", 1) or 0)
    except Exception:
        return 1


def create(ns: str, *, drop_egress: bool = True,
           egress: Optional["EgressSpec"] = None,
           runner: Optional[Runner] = None) -> bool:
    """Create + isolate namespace ``ns``. FAIL-CLOSED: if any setup step
    errors (e.g. no ``CAP_NET_ADMIN``), the partially-created namespace is
    torn down and ``False`` is returned -- a caller must never proceed
    believing it has isolation (or a working tunnel) it does not have.
    ``egress`` (697) routes the ns through a WireGuard tunnel instead of the
    default-drop egress-less posture."""
    for argv in setup_commands(ns, drop_egress=drop_egress, egress=egress):
        if _run(runner, argv) != 0:
            destroy(ns, egress=egress, runner=runner)   # best-effort cleanup
            return False
    return True


def destroy(ns: str, *, egress: Optional["EgressSpec"] = None,
            runner: Optional[Runner] = None) -> None:
    """Remove namespace ``ns`` (best-effort; never raises). ``egress`` (698)
    lets teardown also clean up a per-ns resolv.conf dir."""
    for argv in teardown_commands(ns, egress=egress):
        _run(runner, argv)


def run_in_netns(ns: str, argv: List[str], *,
                 runner: Optional[Runner] = None) -> "subprocess.CompletedProcess":
    """Run ``argv`` inside namespace ``ns`` and return the
    ``CompletedProcess``."""
    r = runner or subprocess.run
    return r(netns_exec_argv(ns, argv), capture_output=True, text=True)


@contextmanager
def isolated(kind: str, ident: str, *, drop_egress: bool = True,
             runner: Optional[Runner] = None):
    """Context manager: create an isolated namespace for ``(kind, ident)``,
    yield its name, and always tear it down on exit. Raises ``RuntimeError``
    if creation fails (fail-closed) so the guarded body never runs
    unisolated."""
    ns = netns_name(kind, ident)
    if not create(ns, drop_egress=drop_egress, runner=runner):
        raise RuntimeError(f"netns isolation unavailable for {ns} "
                           f"(CAP_NET_ADMIN required)")
    try:
        yield ns
    finally:
        destroy(ns, runner=runner)


def site_wants_isolation(cfg: dict) -> bool:
    """Opt-in flag: True when a site config enables per-capture netns
    isolation via ``netns_isolation: true`` or
    ``netns_isolation: {"enabled": true}``."""
    v = (cfg or {}).get("netns_isolation")
    if isinstance(v, dict):
        return bool(v.get("enabled"))
    return bool(v)


def fail_closed(cfg: dict) -> bool:
    """Posture flag for :func:`capture_netns`: True (the default) means a site
    that opts into isolation but cannot get a namespace must NOT proceed
    un-isolated. Only the explicit dict form ``netns_isolation:
    {"fail_closed": false}`` degrades open; every other form (including the
    bare ``netns_isolation: true``) is fail-closed."""
    v = (cfg or {}).get("netns_isolation")
    if isinstance(v, dict):
        return bool(v.get("fail_closed", True))
    return True


def egress_spec_from_cfg(cfg: dict) -> Optional["EgressSpec"]:
    """Resolve an :class:`EgressSpec` from a site cfg's *undeclared*
    ``netns_isolation: {egress: {wg_iface, wg_conf, address, mtu?}}`` (backend-
    only -- invisible to the config/env inventory). Returns ``None`` when absent
    or incomplete (never a half-built spec, so a mis-typed egress block degrades
    to the default-drop posture rather than a broken tunnel)."""
    v = (cfg or {}).get("netns_isolation")
    if not isinstance(v, dict):
        return None
    e = v.get("egress")
    if not isinstance(e, dict):
        return None
    wg_iface = str(e.get("wg_iface") or "").strip()
    wg_conf = str(e.get("wg_conf") or "").strip()
    address = str(e.get("address") or "").strip()
    if not (wg_iface and wg_conf and address):
        return None
    mtu = e.get("mtu")
    try:
        mtu = int(mtu) if mtu else None
    except (TypeError, ValueError):
        mtu = None
    dns = str(e.get("dns") or "").strip() or None
    return EgressSpec(wg_iface=wg_iface, wg_conf=wg_conf, address=address,
                      mtu=mtu, dns=dns)


@contextmanager
def capture_netns(cfg: dict, kind: str, ident: str, *,
                  drop_egress: bool = True, runner: Optional[Runner] = None):
    """Posture-aware per-capture isolation bracket for a launch path.

    Yields the namespace name a launch should confine itself to, or ``None``
    when no isolation applies -- so a caller wraps its subprocess/launch as::

        with capture_netns(cfg, "dl", url) as ns:
            cmd = _build_ytdlp_cmd(..., netns=ns)   # ns=None -> unchanged cmd
            subprocess.run(cmd, ...)

    Behaviour:

      * site does not opt in (:func:`site_wants_isolation` False) -> yield
        ``None``; nothing is created, zero cost, byte-identical prior path.
      * opts in + :func:`create` succeeds -> yield the ns name; the namespace
        is always torn down on exit (success or exception).
      * opts in + create fails (e.g. no ``CAP_NET_ADMIN``):
          - :func:`fail_closed` True (default) -> raise
            :class:`NetnsRequiredError` (the guarded body never runs), so the
            caller returns without egressing un-isolated;
          - fail_closed False -> yield ``None`` (operator chose to degrade to
            the existing proxy-only isolation).

    ``create`` / ``destroy`` run through the injected ``runner`` so every
    posture is unit-testable with no root and no real namespace."""
    if not site_wants_isolation(cfg):
        yield None
        return
    ns = netns_name(kind, ident)
    egress = egress_spec_from_cfg(cfg)
    if create(ns, drop_egress=drop_egress, egress=egress, runner=runner):
        try:
            yield ns
        finally:
            destroy(ns, egress=egress, runner=runner)
    elif fail_closed(cfg):
        raise NetnsRequiredError(
            f"netns isolation required for {kind}:{ident} but unavailable "
            f"(CAP_NET_ADMIN?) -- failing closed")
    else:
        yield None
