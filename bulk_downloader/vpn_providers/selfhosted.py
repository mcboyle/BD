"""v3.43.60: Self-hosted VPN provider.

For users running their own WireGuard or OpenVPN exit on a VPS / colo box.
Distinct from "generic" in that we *build* the config from form fields
rather than parsing a pasted file — better UX when the user just spun up
a fresh wg-quick on Hetzner and has the four fields handy.

# CREDENTIALS_SCHEMA (WireGuard side; OpenVPN side accepts a pasted .ovpn)

  wg_private_key  - this client's private key
  wg_address      - this client's tunnel IP, CIDR
  wg_peer_pubkey  - server's public key
  wg_endpoint     - host:port of the server
  wg_dns          - optional, in-tunnel DNS
  wg_preshared    - optional
  ovpn_config     - optional, paste a full .ovpn here for OpenVPN
  ovpn_username   - optional
  ovpn_password   - optional

# Locations

Self-hosted has one logical "location" per server: "self-hosted-<endpoint-host>".
We expose it as a single entry so the existing per-site selector works.
"""
from __future__ import annotations

import re

PROVIDER_ID = "selfhosted"
PROVIDER_NAME = "Self-hosted"
SUPPORTED_BACKENDS = ("wireguard", "openvpn")
CREDENTIALS_SCHEMA = [
    {"key": "wg_private_key", "label": "WG private key", "type": "secret",
     "required": False, "hint": "base64, 44 chars; required for wireguard"},
    {"key": "wg_address", "label": "WG client address", "type": "string",
     "required": False, "placeholder": "10.0.0.2/32"},
    {"key": "wg_peer_pubkey", "label": "WG server public key", "type": "string",
     "required": False, "hint": "base64, 44 chars"},
    {"key": "wg_endpoint", "label": "WG server endpoint", "type": "string",
     "required": False, "placeholder": "vpn.example.com:51820"},
    {"key": "wg_dns", "label": "WG DNS (optional)", "type": "string", "required": False},
    {"key": "wg_preshared", "label": "WG preshared key (optional)", "type": "secret",
     "required": False},
    {"key": "ovpn_config", "label": "OpenVPN .ovpn text", "type": "textarea",
     "required": False},
    {"key": "ovpn_username", "label": "OpenVPN username (optional)", "type": "string",
     "required": False},
    {"key": "ovpn_password", "label": "OpenVPN password (optional)", "type": "secret",
     "required": False},
]

_WG_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")  # base64, 256-bit


def test_credentials(credentials: dict) -> tuple[bool, str]:
    creds = credentials or {}
    has_wg = _has_wg(creds)
    has_ovpn = bool(creds.get("ovpn_config", "").strip())
    if not (has_wg or has_ovpn):
        return False, "fill the wg_ fields OR paste an .ovpn config"
    parts = []
    if has_wg:
        ok, why = _validate_wg(creds)
        if not ok:
            return False, f"WireGuard: {why}"
        parts.append("WireGuard config valid")
    if has_ovpn:
        parts.append("OpenVPN config present")
    return True, "; ".join(parts)


def list_locations(credentials: dict) -> list[dict]:
    creds = credentials or {}
    out = []
    ep = creds.get("wg_endpoint", "").strip()
    if ep:
        host = ep.split(":")[0]
        out.append({
            "id": f"self-{host}",
            "country": "",
            "city": host,
            "kind": "wireguard",
            "source": "form",
        })
    ovpn = creds.get("ovpn_config", "").strip()
    if ovpn:
        # try to extract remote hostname
        m = re.search(r"^\s*remote\s+(\S+)", ovpn, re.MULTILINE)
        host = m.group(1) if m else "openvpn"
        out.append({
            "id": f"self-{host}-ovpn",
            "country": "",
            "city": host,
            "kind": "openvpn",
            "source": "form",
        })
    return out


def render_config(
    backend: str,
    location: str,
    credentials: dict,
    socks_port: int,
) -> dict:
    creds = credentials or {}
    if backend == "wireguard":
        ok, why = _validate_wg(creds)
        if not ok:
            raise ValueError(f"self-hosted WireGuard incomplete: {why}")
        cfg = {
            "private_key": creds["wg_private_key"].strip(),
            "address": creds["wg_address"].strip(),
            "peer_public_key": creds["wg_peer_pubkey"].strip(),
            "endpoint": creds["wg_endpoint"].strip(),
            "allowed_ips": creds.get("wg_allowed_ips", "0.0.0.0/0,::/0"),
            "persistent_keepalive": int(creds.get("wg_persistent_keepalive", 25)),
        }
        dns = creds.get("wg_dns", "").strip()
        if dns:
            cfg["dns"] = dns
        psk = creds.get("wg_preshared", "").strip()
        if psk:
            cfg["preshared_key"] = psk
        return cfg

    if backend == "openvpn":
        ovpn = creds.get("ovpn_config", "").strip()
        if not ovpn:
            raise ValueError("self-hosted OpenVPN requires ovpn_config (paste your .ovpn)")
        out: dict = {"ovpn": ovpn}
        u = creds.get("ovpn_username", "").strip()
        p = creds.get("ovpn_password", "").strip()
        if u:
            out["username"] = u
        if p:
            out["password"] = p
        return out

    raise ValueError(f"self-hosted does not support backend {backend!r}")


# ─── Internals ──────────────────────────────────────────────────────

def _has_wg(creds: dict) -> bool:
    return any(creds.get(k, "").strip() for k in
               ("wg_private_key", "wg_address", "wg_peer_pubkey", "wg_endpoint"))


def _validate_wg(creds: dict) -> tuple[bool, str]:
    priv = creds.get("wg_private_key", "").strip()
    addr = creds.get("wg_address", "").strip()
    pub = creds.get("wg_peer_pubkey", "").strip()
    ep = creds.get("wg_endpoint", "").strip()

    if not priv:
        return False, "wg_private_key required"
    if not _WG_KEY_RE.match(priv):
        return False, "wg_private_key must be base64 of a 256-bit key (44 chars ending in =)"
    if not addr:
        return False, "wg_address required (e.g. 10.0.0.2/32)"
    if "/" not in addr:
        return False, "wg_address must include CIDR netmask"
    if not pub:
        return False, "wg_peer_pubkey required"
    if not _WG_KEY_RE.match(pub):
        return False, "wg_peer_pubkey must be base64 of a 256-bit key"
    if not ep:
        return False, "wg_endpoint required (host:port)"
    if ":" not in ep:
        return False, "wg_endpoint must be host:port"
    try:
        host, port = ep.rsplit(":", 1)
        int(port)
    except ValueError:
        return False, "wg_endpoint port not numeric"
    return True, "ok"


__all__ = [
    "PROVIDER_ID", "PROVIDER_NAME", "SUPPORTED_BACKENDS", "CREDENTIALS_SCHEMA",
    "test_credentials", "list_locations", "render_config",
]
