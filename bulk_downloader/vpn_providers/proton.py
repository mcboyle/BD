"""v3.43.60: ProtonVPN provider.

Proton has two credential sets:
  - Web/app login: regular username + password (2FA on the account)
  - OpenVPN/IKEv2: per-account OpenVPN credentials (NOT the same as web)
    Generated at https://account.protonvpn.com/account → OpenVPN credentials

For WireGuard, Proton requires:
  - A device registered in the dashboard (or via their API)
  - The user downloads a WireGuard config which contains private_key, address,
    peer public_key, endpoint — exactly what vpn_wireguard wants

# Why no live API integration here

Proton's account API requires 2FA-aware OAuth which is heavy. For v3.43.60
we accept the user pasting their WireGuard config (one-time setup) and
their OpenVPN credentials. The provider validates format and renders.

A later phase can add Proton's full session API if there's demand.

# CREDENTIALS_SCHEMA

  - openvpn_username  - "abc12345"-style, generated in Proton's dashboard
  - openvpn_password
  - device_configs    - JSON map: location_id -> wireguard config dict
                        (private_key/address/peer_public_key/endpoint)
"""
from __future__ import annotations

import json
import sys
from typing import Any

PROVIDER_ID = "proton"
PROVIDER_NAME = "ProtonVPN"
SUPPORTED_BACKENDS = ("wireguard", "openvpn")
CREDENTIALS_SCHEMA = [
    {"key": "openvpn_username", "label": "OpenVPN username", "type": "string",
     "required": False, "placeholder": "from account.protonvpn.com"},
    {"key": "openvpn_password", "label": "OpenVPN password", "type": "secret",
     "required": False},
    # Audit 2026-05 (Phase 5): same issue PIA had -- the Proton OpenVPN
    # template uses `tls-client` / `remote-cert-tls server` / `verify-x509-name`,
    # all of which require a CA. Without it OpenVPN refuses to start.
    # Only required when the OpenVPN backend is rendered; WireGuard users
    # leave this blank.
    {"key": "ca_pem", "label": "Proton CA certificate (PEM, OpenVPN only)",
     "type": "textarea", "required": False,
     "hint": "Required only for OpenVPN. Paste the contents of any .ovpn "
             "file downloaded from account.protonvpn.com → Downloads → "
             "OpenVPN config, copying the section between -----BEGIN "
             "CERTIFICATE----- and -----END CERTIFICATE-----."},
    {"key": "device_configs", "label": "WireGuard configs (JSON)",
     "type": "json", "required": False,
     "hint": "Map of location_id → {private_key, address, peer_public_key, endpoint}"},
]

# A small built-in list. Full server list has 1,900+ entries; we expose the
# common regions and let users add more via custom config import.
_LOCATIONS = [
    {"id": "us-free-1", "country": "us", "city": "New York", "tier": "free",
     "hostname": "us-free-01.protonvpn.net", "endpoint": "us-free-01.protonvpn.net:51820"},
    {"id": "us-ny-01", "country": "us", "city": "New York", "tier": "plus",
     "hostname": "us-ny-01.protonvpn.net", "endpoint": "us-ny-01.protonvpn.net:51820"},
    {"id": "us-ca-01", "country": "us", "city": "Los Angeles", "tier": "plus",
     "hostname": "us-ca-01.protonvpn.net", "endpoint": "us-ca-01.protonvpn.net:51820"},
    {"id": "uk-lon-01", "country": "uk", "city": "London", "tier": "plus",
     "hostname": "uk-lon-01.protonvpn.net", "endpoint": "uk-lon-01.protonvpn.net:51820"},
    {"id": "ch-zh-01", "country": "ch", "city": "Zurich", "tier": "plus",
     "hostname": "ch-zh-01.protonvpn.net", "endpoint": "ch-zh-01.protonvpn.net:51820"},
    {"id": "de-fra-01", "country": "de", "city": "Frankfurt", "tier": "plus",
     "hostname": "de-fra-01.protonvpn.net", "endpoint": "de-fra-01.protonvpn.net:51820"},
    {"id": "nl-ams-01", "country": "nl", "city": "Amsterdam", "tier": "plus",
     "hostname": "nl-ams-01.protonvpn.net", "endpoint": "nl-ams-01.protonvpn.net:51820"},
    {"id": "is-rkv-01", "country": "is", "city": "Reykjavik", "tier": "plus",
     "hostname": "is-rkv-01.protonvpn.net", "endpoint": "is-rkv-01.protonvpn.net:51820"},
    {"id": "jp-tyo-01", "country": "jp", "city": "Tokyo", "tier": "plus",
     "hostname": "jp-tyo-01.protonvpn.net", "endpoint": "jp-tyo-01.protonvpn.net:51820"},
    {"id": "se-sto-01", "country": "se", "city": "Stockholm", "tier": "plus",
     "hostname": "se-sto-01.protonvpn.net", "endpoint": "se-sto-01.protonvpn.net:51820"},
]

PROTON_DNS = "10.2.0.1"


def test_credentials(credentials: dict) -> tuple[bool, str]:
    """ProtonVPN format validation only. Real validation happens at tunnel start."""
    ov_user = (credentials or {}).get("openvpn_username", "").strip()
    ov_pass = (credentials or {}).get("openvpn_password", "").strip()
    device_configs = (credentials or {}).get("device_configs", "")
    has_openvpn = bool(ov_user and ov_pass)
    has_wg = _device_configs_have_entries(device_configs)
    if not (has_openvpn or has_wg):
        return False, "provide OpenVPN credentials or import at least one WireGuard config"
    parts = []
    if has_openvpn:
        parts.append("OpenVPN credentials present")
    if has_wg:
        parts.append("WireGuard configs present")
    return True, "; ".join(parts)


def list_locations(credentials: dict) -> list[dict]:
    """Return the built-in Proton location list. UI shows which have WG configs imported."""
    device_configs = _parse_device_configs((credentials or {}).get("device_configs", ""))
    out = []
    for loc in _LOCATIONS:
        d = dict(loc)
        d["wg_imported"] = loc["id"] in device_configs
        d["source"] = "builtin"
        out.append(d)
    return out


def render_config(
    backend: str,
    location: str,
    credentials: dict,
    socks_port: int,
) -> dict:
    if backend not in SUPPORTED_BACKENDS:
        raise ValueError(f"proton does not support backend {backend!r}")
    loc = _find_location(location)
    if loc is None:
        raise ValueError(f"unknown proton location: {location!r}")
    if backend == "wireguard":
        return _render_wireguard(loc, credentials)
    return _render_openvpn(loc, credentials)


# ─── Internals ──────────────────────────────────────────────────────

def _find_location(location_id: str) -> dict | None:
    for loc in _LOCATIONS:
        if loc["id"] == location_id:
            return loc
    return None


def _parse_device_configs(raw: Any) -> dict:
    """Accept either a dict or a JSON string. Return dict (location_id -> wg cfg dict)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError as e:
            sys.stderr.write(f"[vpn-proton] device_configs JSON parse error: {e}\n")
    return {}


def _device_configs_have_entries(raw: Any) -> bool:
    return bool(_parse_device_configs(raw))


def _render_wireguard(loc: dict, credentials: dict) -> dict:
    configs = _parse_device_configs((credentials or {}).get("device_configs", ""))
    cfg = configs.get(loc["id"])
    if not cfg:
        raise ValueError(
            f"no WireGuard config imported for {loc['id']!r}. "
            f"Get one from account.protonvpn.com → Downloads → WireGuard config, "
            f"then paste into the device_configs field for this location."
        )
    required = ("private_key", "address", "peer_public_key", "endpoint")
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        raise ValueError(f"Proton WG config for {loc['id']!r} missing: {missing}")
    return {
        "private_key": cfg["private_key"],
        "address": cfg["address"],
        "peer_public_key": cfg["peer_public_key"],
        "endpoint": cfg["endpoint"],
        "dns": cfg.get("dns", PROTON_DNS),
        "allowed_ips": cfg.get("allowed_ips", "0.0.0.0/0,::/0"),
        "persistent_keepalive": cfg.get("persistent_keepalive", 25),
    }


def _render_openvpn(loc: dict, credentials: dict) -> dict:
    creds = credentials or {}
    ov_user = (creds.get("openvpn_username") or "").strip()
    ov_pass = (creds.get("openvpn_password") or "").strip()
    if not (ov_user and ov_pass):
        raise ValueError(
            "Proton OpenVPN requires openvpn_username and openvpn_password "
            "(generated at account.protonvpn.com → OpenVPN/IKEv2 username)"
        )
    # Audit 2026-05 (Phase 5): the template uses tls-client, remote-cert-tls
    # server, and verify-x509-name -- all of which require a CA. Without
    # one, OpenVPN refuses to start ("Cannot load certificate authority").
    # Require the user paste Proton's published CA so the .ovpn is complete.
    ca_pem = (creds.get("ca_pem") or "").strip()
    if not ca_pem or "BEGIN CERTIFICATE" not in ca_pem:
        raise ValueError(
            "Proton OpenVPN requires the Proton CA certificate (PEM). "
            "Download any .ovpn from account.protonvpn.com → Downloads → "
            "OpenVPN config, then paste the BEGIN/END CERTIFICATE block "
            "into the 'Proton CA certificate' field."
        )
    ovpn = f"""client
dev tun
proto udp
remote {loc['hostname']} 1194
resolv-retry infinite
nobind
cipher AES-256-GCM
auth SHA512
tls-version-min 1.2
verify-x509-name {loc['hostname']} name
remote-cert-tls server
auth-user-pass
verb 3
<ca>
{ca_pem}
</ca>
""".strip() + "\n"
    return {
        "ovpn": ovpn,
        "username": ov_user,
        "password": ov_pass,
    }


__all__ = [
    "PROVIDER_ID", "PROVIDER_NAME", "SUPPORTED_BACKENDS", "CREDENTIALS_SCHEMA",
    "test_credentials", "list_locations", "render_config",
]
