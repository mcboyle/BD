"""dev_suite.vpn_diag -- VPN diagnostics

Split from the dev_suite.py monolith (v3.66.395, pure code motion; surface preserved
via dev_suite/__init__.py). See kb/decomp/dev_suite/.
"""


from __future__ import annotations
import os
import sys
import threading
from pathlib import Path
import re as _sec_re
import json as _cfg_json
import re as _cfg_re
import os as _dl_os
import re as _dl_re



# ── 63. VPN config renderer + provider-rotation viewer (T13) ────────
#
# D-41 — render every configured VPN tunnel through vpn._redact_config
# so the operator can audit what's stored (with secret-looking keys
# masked). Reuses the existing redaction helper rather than building
# a parallel one. Reports per-tunnel provider/backend/location/state
# and current runtime status from vpn.list_tunnels().
#
# D-43 — provider-rotation viewer. There is no "provider rotation"
# symbol in the codebase; what exists is per-tunnel cycle_tunnel +
# per-tunnel health. The viewer groups configured tunnels by provider
# and reports each tunnel's runtime state + last health, so the
# operator can see which provider's tunnels are healthy and which
# would next be picked for cycling. Read-only.

def vpn_config_render():
    """D-41 — render stored VPN tunnel configs with secrets redacted
    (read-only). Pairs each stored config with the live runtime
    Tunnel (if registered) so the operator sees both the on-disk
    definition and the in-process state.
    """
    out = {"tool": "vpn_config_render", "ok": True, "tunnels": []}
    try:
        from bulk_downloader import vpn as _vpn
        from bulk_downloader import vpn_config as _vpn_cfg
    except Exception as e:
        return {"tool": "vpn_config_render", "ok": False,
                "error": f"vpn modules unavailable: {str(e)[:140]}"}

    try:
        stored = _vpn_cfg.list_tunnel_configs()
    except Exception as e:
        return {"tool": "vpn_config_render", "ok": False,
                "error": f"list_tunnel_configs failed: "
                         f"{str(e)[:140]}"}

    # build a quick lookup of live tunnels by id
    try:
        live_by_id = {t.tunnel_id: t for t in _vpn.list_tunnels()}
    except Exception:
        live_by_id = {}

    for cfg in stored:
        tid = cfg.get("tunnel_id") or "?"
        provider = cfg.get("provider") or "?"
        backend = cfg.get("backend") or "?"
        location = cfg.get("location")
        inner_config = cfg.get("config") or {}
        # reuse the canonical redaction helper — never re-implement
        redacted_config = _vpn._redact_config(inner_config)
        live = live_by_id.get(tid)
        live_view = None
        if live is not None:
            try:
                live_view = live.to_dict(redact_secrets=True)
            except Exception:
                live_view = {"state": getattr(live, "state", "?")}
        out["tunnels"].append({
            "tunnel_id": tid,
            "name": cfg.get("name") or tid,
            "provider": provider,
            "backend": backend,
            "location": location,
            "redacted_config": redacted_config,
            "registered_live": live is not None,
            "live": live_view,
        })

    out["total_tunnels"] = len(out["tunnels"])
    out["live_tunnels"] = sum(1 for t in out["tunnels"]
                                if t["registered_live"])
    out["verdict"] = (f"{out['total_tunnels']} configured tunnel(s); "
                      f"{out['live_tunnels']} registered live; "
                      "secrets redacted")
    return out



def vpn_provider_rotation_view():
    """D-43 — group configured tunnels by provider, report each
    tunnel's runtime state + last health, identify which tunnel
    per provider would be the next cycle target. Read-only — does
    not start, stop, or cycle anything.
    """
    out = {"tool": "vpn_provider_rotation_view", "ok": True,
           "providers": []}
    try:
        from bulk_downloader import vpn as _vpn
        from bulk_downloader import vpn_config as _vpn_cfg
    except Exception as e:
        return {"tool": "vpn_provider_rotation_view", "ok": False,
                "error": f"vpn modules unavailable: {str(e)[:140]}"}

    try:
        stored = _vpn_cfg.list_tunnel_configs()
    except Exception as e:
        return {"tool": "vpn_provider_rotation_view", "ok": False,
                "error": f"list_tunnel_configs failed: "
                         f"{str(e)[:140]}"}

    try:
        live_by_id = {t.tunnel_id: t for t in _vpn.list_tunnels()}
    except Exception:
        live_by_id = {}

    # group by provider
    by_provider: dict = {}
    for cfg in stored:
        prov = cfg.get("provider") or "unknown"
        by_provider.setdefault(prov, []).append(cfg)

    total_healthy = total_down = 0
    for prov, configs in sorted(by_provider.items()):
        tunnels_info = []
        healthy = down = 0
        for cfg in configs:
            tid = cfg.get("tunnel_id") or "?"
            live = live_by_id.get(tid)
            if live is None:
                state = "not-registered"
                health_ok = False
                last_hc = None
                fails = 0
            else:
                state = getattr(live, "state", "down")
                health_ok = bool(getattr(live, "health_ok", False))
                last_hc = getattr(live, "last_health_check", None)
                fails = int(getattr(live, "failure_count", 0) or 0)
            if health_ok and state == "up":
                healthy += 1
            else:
                down += 1
            tunnels_info.append({
                "tunnel_id": tid,
                "name": cfg.get("name") or tid,
                "location": cfg.get("location"),
                "backend": cfg.get("backend"),
                "state": state,
                "health_ok": health_ok,
                "failure_count": fails,
                "last_health_check": last_hc,
            })
        # "next cycle target" = the healthy tunnel with the
        # oldest last_health_check (least-recently-validated). If
        # none healthy, fall back to most-failed.
        next_target = None
        healthy_tunnels = [t for t in tunnels_info if t["health_ok"]]
        if healthy_tunnels:
            healthy_tunnels.sort(
                key=lambda t: (t["last_health_check"] or 0))
            next_target = healthy_tunnels[0]["tunnel_id"]
        elif tunnels_info:
            tunnels_info_sorted = sorted(
                tunnels_info, key=lambda t: -t["failure_count"])
            next_target = tunnels_info_sorted[0]["tunnel_id"]
        out["providers"].append({
            "provider": prov,
            "tunnel_count": len(configs),
            "healthy_count": healthy,
            "down_count": down,
            "next_cycle_target": next_target,
            "tunnels": tunnels_info,
        })
        total_healthy += healthy
        total_down += down

    out["provider_count"] = len(out["providers"])
    out["total_tunnels"] = sum(p["tunnel_count"]
                                 for p in out["providers"])
    out["total_healthy"] = total_healthy
    out["total_down"] = total_down
    out["verdict"] = (
        f"{out['provider_count']} provider(s), "
        f"{out['total_tunnels']} tunnel(s); "
        f"{total_healthy} healthy / {total_down} down")
    return out



# ── 64. VPN connectivity probe + egress-IP monitor (T14: D-42 + D-49)
#
# D-42 — point-in-time connectivity check for each registered tunnel.
# Calls the existing vpn.check_health() (which already covers backend-
# alive + public-IP cache + dns_ok + latency in the full impl) and
# reports per-tunnel. Read-only: never starts, stops, cycles, or
# alters state.
#
# D-49 — egress-IP monitor. Walks registered tunnels, reports each
# tunnel's last known public IP and how recent the health check
# was. Flags tunnels whose last_health_check is older than a
# configurable threshold (default 5 min) as "stale".

def vpn_connectivity_probe():
    """D-42 — read-only per-tunnel connectivity report. Calls
    vpn.check_health() for each registered tunnel and aggregates;
    never alters state. SOCKS URL surfaced where available."""
    out = {"tool": "vpn_connectivity_probe", "ok": True,
           "tunnels": []}
    try:
        from bulk_downloader import vpn as _vpn
    except Exception as e:
        return {"tool": "vpn_connectivity_probe", "ok": False,
                "error": f"vpn module unavailable: {str(e)[:140]}"}
    try:
        live = list(_vpn.list_tunnels())
    except Exception as e:
        return {"tool": "vpn_connectivity_probe", "ok": False,
                "error": f"list_tunnels failed: {str(e)[:140]}"}

    healthy = unhealthy = 0
    for t in live:
        tid = t.tunnel_id
        try:
            hc = _vpn.check_health(tid)
        except Exception as e:
            hc = {"ok": False,
                  "error": f"check_health raised: {str(e)[:120]}"}
        try:
            socks_url = _vpn.get_socks_url(tid)
        except Exception:
            socks_url = None
        ok = bool(hc.get("ok"))
        if ok:
            healthy += 1
        else:
            unhealthy += 1
        out["tunnels"].append({
            "tunnel_id": tid,
            "name": t.name,
            "provider": t.provider,
            "state": t.state,
            "health_ok": ok,
            "public_ip": hc.get("public_ip"),
            "dns_ok": hc.get("dns_ok"),
            "latency_ms": hc.get("latency_ms"),
            "error": hc.get("error"),
            "socks_url": socks_url,
        })
    out["registered_tunnels"] = len(live)
    out["healthy_count"] = healthy
    out["unhealthy_count"] = unhealthy
    out["verdict"] = (
        f"{len(live)} registered tunnel(s); "
        f"{healthy} healthy / {unhealthy} unhealthy")
    return out



def egress_ip_monitor(stale_after_sec=300):
    """D-49 — walk registered tunnels, report last known public IP
    and freshness of last_health_check. Flags tunnels whose last
    check is older than `stale_after_sec` (default 5 min). Pure
    read; calls no probe."""
    import time as _time
    try:
        stale_after_sec = max(1, int(stale_after_sec))
    except Exception:
        stale_after_sec = 300
    out = {"tool": "egress_ip_monitor", "ok": True,
           "stale_after_sec": stale_after_sec, "tunnels": []}
    try:
        from bulk_downloader import vpn as _vpn
    except Exception as e:
        return {"tool": "egress_ip_monitor", "ok": False,
                "error": f"vpn module unavailable: {str(e)[:140]}"}
    try:
        live = list(_vpn.list_tunnels())
    except Exception as e:
        return {"tool": "egress_ip_monitor", "ok": False,
                "error": f"list_tunnels failed: {str(e)[:140]}"}

    now = _time.time()
    by_ip: dict = {}
    no_ip = 0
    stale = 0
    for t in live:
        ip = t.public_ip
        last = t.last_health_check
        age = (now - last) if last else None
        is_stale = (age is None) or (age > stale_after_sec)
        if not ip:
            no_ip += 1
        else:
            by_ip.setdefault(ip, []).append(t.tunnel_id)
        if is_stale and t.state == "up":
            stale += 1
        out["tunnels"].append({
            "tunnel_id": t.tunnel_id,
            "name": t.name,
            "provider": t.provider,
            "state": t.state,
            "public_ip": ip,
            "last_health_check": last,
            "age_sec": round(age, 1) if age is not None else None,
            "is_stale": is_stale,
        })
    # any IP shared by >1 tunnel = a finding (means VPN didn't
    # actually change the egress, or tunnels collided)
    shared_ips = {ip: ids for ip, ids in by_ip.items()
                  if len(ids) > 1}
    out["registered_tunnels"] = len(live)
    out["tunnels_with_ip"] = sum(1 for t in out["tunnels"]
                                   if t["public_ip"])
    out["tunnels_without_ip"] = no_ip
    out["stale_tunnels_up"] = stale
    out["shared_ips"] = shared_ips
    out["verdict"] = (
        f"{len(live)} registered tunnel(s); "
        f"{out['tunnels_with_ip']} with known IP, "
        f"{no_ip} without; {stale} stale (state=up); "
        f"{len(shared_ips)} shared-IP finding(s)")
    return out
