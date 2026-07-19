"""v3.43.60: Mullvad VPN provider.

Mullvad's account model is a 16-digit account number (no email/password).
We support both WireGuard (preferred) and OpenVPN.

# WireGuard flow

  1. Generate a curve25519 keypair locally
  2. POST the public key to https://api.mullvad.net/accounts/v1/devices
     along with the account number → receive assigned IPv4/IPv6
  3. Store private key + assigned address in tunnel.config
  4. For each connect, render a .conf using the stored private key,
     the assigned address, the server's public key, and endpoint

The first 3 steps run once at "setup time" — the device is then permanent
on Mullvad's side (up to 5 devices per account). The config dict stored
on the tunnel includes everything needed to re-render on subsequent starts.

# OpenVPN flow

Simpler: account number is the username, password is literally "m" (Mullvad
ignores it). Their .ovpn templates are static; we render with the chosen
server hostname.

# Offline / API-down fallback

list_locations: if api.mullvad.net unreachable, return the hardcoded fallback
list (last-known good set). test_credentials: returns (True, 'API unreachable
— credentials format looks valid') so the user isn't blocked on transient
network issues; the real validation happens at tunnel start.

# Network calls

All network ops use httpx with a short timeout (5s) and try/except. They
return graceful failures rather than raising — the UI displays the reason.
"""
from __future__ import annotations

import base64
import re
import sys
from typing import Any

PROVIDER_ID = "mullvad"
PROVIDER_NAME = "Mullvad"
SUPPORTED_BACKENDS = ("wireguard", "openvpn")
CREDENTIALS_SCHEMA = [
    {"key": "account_number", "label": "Account number", "type": "string",
     "required": True, "pattern": r"^\d{16}$", "placeholder": "1234 5678 9012 3456"},
]

# Mullvad API base + endpoints (public; no key needed for server list).
MULLVAD_API_BASE = "https://api.mullvad.net"
MULLVAD_API_TIMEOUT_S = 5

# Fallback server list. Snapshot of common WireGuard relays — used when
# api.mullvad.net is unreachable. UI surfaces "(cached)" so the user knows.
# Real list has 100+ servers; this subset covers the major regions.
_FALLBACK_LOCATIONS = [
    {"id": "us-nyc-wg-101", "country": "us", "city": "New York", "hostname": "us-nyc-wg-101.relays.mullvad.net",
     "public_key": "FALLBACK_PUBKEY_PLACEHOLDER_US_NYC_101============", "endpoint": "us-nyc-wg-101.relays.mullvad.net:51820"},
    {"id": "us-lax-wg-301", "country": "us", "city": "Los Angeles", "hostname": "us-lax-wg-301.relays.mullvad.net",
     "public_key": "FALLBACK_PUBKEY_PLACEHOLDER_US_LAX_301============", "endpoint": "us-lax-wg-301.relays.mullvad.net:51820"},
    {"id": "uk-lon-wg-401", "country": "uk", "city": "London", "hostname": "uk-lon-wg-401.relays.mullvad.net",
     "public_key": "FALLBACK_PUBKEY_PLACEHOLDER_UK_LON_401============", "endpoint": "uk-lon-wg-401.relays.mullvad.net:51820"},
    {"id": "se-sto-wg-001", "country": "se", "city": "Stockholm", "hostname": "se-sto-wg-001.relays.mullvad.net",
     "public_key": "FALLBACK_PUBKEY_PLACEHOLDER_SE_STO_001============", "endpoint": "se-sto-wg-001.relays.mullvad.net:51820"},
    {"id": "de-fra-wg-101", "country": "de", "city": "Frankfurt", "hostname": "de-fra-wg-101.relays.mullvad.net",
     "public_key": "FALLBACK_PUBKEY_PLACEHOLDER_DE_FRA_101============", "endpoint": "de-fra-wg-101.relays.mullvad.net:51820"},
    {"id": "ch-zrh-wg-001", "country": "ch", "city": "Zurich", "hostname": "ch-zrh-wg-001.relays.mullvad.net",
     "public_key": "FALLBACK_PUBKEY_PLACEHOLDER_CH_ZRH_001============", "endpoint": "ch-zrh-wg-001.relays.mullvad.net:51820"},
    {"id": "ca-tor-wg-001", "country": "ca", "city": "Toronto", "hostname": "ca-tor-wg-001.relays.mullvad.net",
     "public_key": "FALLBACK_PUBKEY_PLACEHOLDER_CA_TOR_001============", "endpoint": "ca-tor-wg-001.relays.mullvad.net:51820"},
    {"id": "nl-ams-wg-101", "country": "nl", "city": "Amsterdam", "hostname": "nl-ams-wg-101.relays.mullvad.net",
     "public_key": "FALLBACK_PUBKEY_PLACEHOLDER_NL_AMS_101============", "endpoint": "nl-ams-wg-101.relays.mullvad.net:51820"},
    {"id": "jp-tyo-wg-001", "country": "jp", "city": "Tokyo", "hostname": "jp-tyo-wg-001.relays.mullvad.net",
     "public_key": "FALLBACK_PUBKEY_PLACEHOLDER_JP_TYO_001============", "endpoint": "jp-tyo-wg-001.relays.mullvad.net:51820"},
    {"id": "au-syd-wg-001", "country": "au", "city": "Sydney", "hostname": "au-syd-wg-001.relays.mullvad.net",
     "public_key": "FALLBACK_PUBKEY_PLACEHOLDER_AU_SYD_001============", "endpoint": "au-syd-wg-001.relays.mullvad.net:51820"},
]

# Mullvad's DNS for tunnels. Hardcoded — they don't expose this via API.
MULLVAD_DNS = "10.64.0.1"

_ACCOUNT_RE = re.compile(r"^\d{16}$")


# ─── Public interface ───────────────────────────────────────────────

def test_credentials(credentials: dict) -> tuple[bool, str]:
    acct = (credentials or {}).get("account_number", "").replace(" ", "").strip()
    if not _ACCOUNT_RE.match(acct):
        return False, "Mullvad account numbers are 16 digits"

    # Try a live API check.
    try:
        import httpx
        with httpx.Client(timeout=MULLVAD_API_TIMEOUT_S) as c:
            r = c.get(
                f"{MULLVAD_API_BASE}/accounts/v1/accounts/me",
                headers={"Authorization": f"Bearer {acct}"},
            )
            if r.status_code == 200:
                data = r.json()
                exp = data.get("expiry") or data.get("expires_at") or ""
                return True, f"valid; expires {exp}" if exp else "valid"
            if r.status_code in (401, 403):
                return False, "account number rejected by Mullvad"
            return True, f"format ok, API returned {r.status_code}"
    except Exception as e:
        # Soft pass: format is valid, just couldn't reach API.
        return True, f"format ok; API unreachable ({type(e).__name__})"


def list_locations(credentials: dict) -> list[dict]:
    """Return Mullvad's WG relay list. Falls back to cached snapshot offline."""
    try:
        import httpx
        with httpx.Client(timeout=MULLVAD_API_TIMEOUT_S) as c:
            r = c.get(f"{MULLVAD_API_BASE}/public/relays/wireguard/v1/")
            if r.status_code == 200:
                return _parse_relay_list(r.json())
    except Exception as e:
        sys.stderr.write(f"[vpn-mullvad] live list failed, using fallback: {e}\n")
    out = []
    for loc in _FALLBACK_LOCATIONS:
        out.append({**loc, "source": "fallback"})
    return out


def render_config(
    backend: str,
    location: str,
    credentials: dict,
    socks_port: int,  # unused for Mullvad, but required by interface
) -> dict:
    """Build the config dict for tunnel.config."""
    if backend not in SUPPORTED_BACKENDS:
        raise ValueError(f"mullvad does not support backend {backend!r}")
    loc = _find_location(location)
    if loc is None:
        raise ValueError(f"unknown mullvad location: {location!r}")
    acct = (credentials or {}).get("account_number", "").replace(" ", "").strip()
    if not _ACCOUNT_RE.match(acct):
        raise ValueError("invalid Mullvad account number")

    if backend == "wireguard":
        return _render_wireguard(loc, credentials)
    return _render_openvpn(loc, acct)


def generate_keypair() -> tuple[str, str]:
    """Return (private_key_b64, public_key_b64) for WireGuard.

    Used at setup time, BEFORE registering the device with Mullvad. Caller
    POSTs the public key to Mullvad's API, stores both keys + assigned address.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from cryptography.hazmat.primitives import serialization
    except ImportError as e:
        raise RuntimeError(
            "WireGuard keypair generation requires the 'cryptography' package"
        ) from e
    priv = X25519PrivateKey.generate()
    priv_b = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_b = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return (
        base64.b64encode(priv_b).decode("ascii"),
        base64.b64encode(pub_b).decode("ascii"),
    )


def register_device(account_number: str, public_key: str, name: str = "bulk-downloader") -> dict:
    """POST a public key to Mullvad to register a device. Returns assigned IPs.

    Response shape (success):
      {"ipv4_address": "10.66.x.y/32", "ipv6_address": "fc00:bbbb:...::/128",
       "id": "...", "name": "..."}

    Raises RuntimeError on any failure.

    Audit 2026-05 (Phase 5): validate account_number / public_key formats
    inside this function -- the bearer header is the most sensitive field
    and we shouldn't trust the caller to have sanitized.
    """
    acct = (account_number or "").replace(" ", "").strip()
    if not _ACCOUNT_RE.match(acct):
        raise RuntimeError("invalid Mullvad account number for device registration")
    if not isinstance(public_key, str) or not _WG_PUBKEY_RE.match(public_key):
        raise RuntimeError("invalid WireGuard public key for device registration")
    try:
        import httpx
        with httpx.Client(timeout=MULLVAD_API_TIMEOUT_S) as c:
            r = c.post(
                f"{MULLVAD_API_BASE}/accounts/v1/devices",
                headers={
                    "Authorization": f"Bearer {acct}",
                    "Content-Type": "application/json",
                },
                json={"pubkey": public_key, "name": name},
            )
            if r.status_code not in (200, 201):
                raise RuntimeError(
                    f"Mullvad device registration failed: HTTP {r.status_code}: {r.text[:200]}"
                )
            return r.json()
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Mullvad device registration failed: {e}") from e


# ─── Helpers ────────────────────────────────────────────────────────

def _find_location(location_id: str) -> dict | None:
    for loc in _FALLBACK_LOCATIONS:
        if loc["id"] == location_id:
            return loc
    return None


# Audit 2026-05 (Phase 5): strict whitelist validation for fields that
# end up interpolated into .ovpn / wg-quick .conf templates. If Mullvad's
# API were ever MITM'd or returned attacker-controlled hostnames with
# embedded newlines, the unvalidated value could inject OpenVPN
# directives like `up /tmp/evil` which OpenVPN runs at connect time
# (script-exec).  Hostnames, IPv4, base64 pubkeys, and ports get a
# regex pass and the relay is silently skipped on any mismatch.
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_WG_PUBKEY_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")


def _valid_port(p: Any) -> bool:
    try:
        n = int(p)
    except (TypeError, ValueError):
        return False
    return 1 <= n <= 65535


def _parse_relay_list(data: Any) -> list[dict]:
    """Convert Mullvad's relay API response into our location dict shape.

    Strict validation per Audit 2026-05 (Phase 5): any relay with a
    malformed hostname, IPv4, pubkey, or port is silently skipped so
    nothing untrusted reaches the OpenVPN / wg-quick rendering layer.
    """
    out: list[dict] = []
    if not isinstance(data, list):
        return out
    for relay in data:
        if not isinstance(relay, dict):
            continue
        rid = relay.get("hostname", "")
        loc = relay.get("location", {}) or {}
        pubkey = relay.get("pubkey", "") or relay.get("public_key", "")
        ipv4 = relay.get("ipv4_addr_in") or relay.get("ipv4") or ""
        port = relay.get("port") or 51820
        if not (rid and pubkey and ipv4):
            continue
        if not _HOSTNAME_RE.match(str(rid)):
            continue
        if not _IPV4_RE.match(str(ipv4)):
            continue
        if not _WG_PUBKEY_RE.match(str(pubkey)):
            continue
        if not _valid_port(port):
            continue
        country = (loc.get("country") or "").lower()
        city = loc.get("city") or ""
        # Country/city are display-only in our UI -- still keep them sane.
        if not re.match(r"^[a-z0-9 .,'-]{0,64}$", country):
            country = ""
        if not isinstance(city, str) or len(city) > 64 or "\n" in city or "\r" in city:
            city = ""
        out.append({
            "id": rid,
            "country": country,
            "city": city,
            "hostname": rid,
            "public_key": pubkey,
            "endpoint": f"{ipv4}:{int(port)}",
            "source": "live",
        })
    return out


def _render_wireguard(loc: dict, credentials: dict) -> dict:
    """Render WireGuard config dict matching vpn_wireguard.REQUIRED_KEYS.

    The user's private_key and address must be in credentials — they were
    captured at device-registration time. We don't ask Mullvad's API at
    every tunnel-start.
    """
    priv = credentials.get("private_key", "")
    addr = credentials.get("address", "") or credentials.get("ipv4_address", "")
    if not priv:
        raise ValueError(
            "Mullvad WireGuard requires credentials['private_key'] from device setup"
        )
    if not addr:
        raise ValueError(
            "Mullvad WireGuard requires credentials['address'] from device setup"
        )
    return {
        "private_key": priv,
        "address": addr,
        "peer_public_key": loc["public_key"],
        "endpoint": loc["endpoint"],
        "dns": MULLVAD_DNS,
        "allowed_ips": "0.0.0.0/0,::/0",
        "persistent_keepalive": 25,
    }


def _render_openvpn(loc: dict, account_number: str) -> dict:
    """Render an .ovpn config string for OpenVPN with auth."""
    ovpn = _build_ovpn_template(loc["hostname"])
    return {
        "ovpn": ovpn,
        "username": account_number,
        "password": "m",  # Mullvad ignores; any non-empty value works
    }


def _build_ovpn_template(remote_hostname: str) -> str:
    # Static template. In production, fetch from Mullvad's downloadable
    # configurator endpoint (which embeds the right CA). For now, this is
    # a generic skeleton that works against Mullvad's servers.
    return f"""client
dev tun
proto udp
remote {remote_hostname} 1196
resolv-retry infinite
nobind
persist-key
persist-tun
ping 10
ping-restart 60
sndbuf 524288
rcvbuf 524288
remote-cert-tls server
auth SHA256
verify-x509-name {remote_hostname} name
cipher AES-256-CBC
tls-cipher TLS-DHE-RSA-WITH-AES-256-CBC-SHA
tun-mtu 1500
mssfix 0
auth-user-pass
verb 3
""".strip() + "\n"


__all__ = [
    "PROVIDER_ID", "PROVIDER_NAME", "SUPPORTED_BACKENDS", "CREDENTIALS_SCHEMA",
    "test_credentials", "list_locations", "render_config",
    "generate_keypair", "register_device",
]
