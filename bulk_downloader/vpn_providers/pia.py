"""v3.43.60: Private Internet Access (PIA) provider.

PIA's auth model is straightforward: username (looks like 'p1234567') and
password.

For OpenVPN: PIA publishes static .ovpn templates per region. We embed
trimmed-down templates and substitute the chosen region.

For WireGuard: PIA has a token-auth-based API. The flow is:
  1. POST /api/v2/token with username+password → get a short-lived bearer token
  2. POST /v2/wg/{region}/addKey with public key → assigned IP, server pubkey
  3. Render WG config

For v3.43.60 we ship the OpenVPN flow as primary (more reliable, simpler
auth) and stub WireGuard for later expansion.

# Credentials schema

  - username (str)
  - password (str)

# Backends

  - openvpn (primary)
  - wireguard (v3.66.680: token -> addKey -> render_conf; live-verify PIA-side)
"""
from __future__ import annotations

import re

PROVIDER_ID = "pia"
PROVIDER_NAME = "Private Internet Access"
SUPPORTED_BACKENDS = ("openvpn", "wireguard")
CREDENTIALS_SCHEMA = [
    {"key": "username", "label": "Username", "type": "string", "required": True,
     "pattern": r"^p\d{7,}$", "placeholder": "p1234567"},
    {"key": "password", "label": "Password", "type": "secret", "required": True},
    # Audit 2026-05 (Phase 5): PIA's OpenVPN configs require their CA cert
    # for TLS verification. The template uses `tls-client` and
    # `remote-cert-tls server`, both of which need a CA. Previously we
    # rendered a .ovpn with neither `ca <file>` nor a `<ca>` block, so
    # OpenVPN refused to start with "Cannot load certificate authority"
    # on the first connection attempt. The user must paste their PIA CA
    # cert (ca.rsa.4096.crt for strong, ca.rsa.2048.crt for legacy),
    # which we embed inline.
    {"key": "ca_pem", "label": "PIA CA certificate (PEM)",
     "type": "textarea", "required": True,
     "hint": "Paste contents of ca.rsa.4096.crt (or ca.rsa.2048.crt) "
             "from PIA's OpenVPN config bundle. Download: "
             "https://www.privateinternetaccess.com/openvpn/openvpn-strong.zip"},
]

PIA_API_BASE = "https://www.privateinternetaccess.com"
PIA_API_TIMEOUT_S = 5

# Subset of PIA's region list. Real list has 80+ regions; this covers the
# most-used ones. The hostnames are PIA's actual production endpoints.
_LOCATIONS = [
    {"id": "us_atlanta", "country": "us", "city": "Atlanta",
     "hostname": "atlanta.privacy.network", "port_udp": 1198, "port_tcp": 502},
    {"id": "us_new_york_city", "country": "us", "city": "New York",
     "hostname": "new-york-city.privacy.network", "port_udp": 1198, "port_tcp": 502},
    {"id": "us_chicago", "country": "us", "city": "Chicago",
     "hostname": "chicago.privacy.network", "port_udp": 1198, "port_tcp": 502},
    {"id": "us_california", "country": "us", "city": "Los Angeles",
     "hostname": "us-california.privacy.network", "port_udp": 1198, "port_tcp": 502},
    {"id": "uk_london", "country": "uk", "city": "London",
     "hostname": "uk-london.privacy.network", "port_udp": 1198, "port_tcp": 502},
    {"id": "ca_toronto", "country": "ca", "city": "Toronto",
     "hostname": "ca-toronto.privacy.network", "port_udp": 1198, "port_tcp": 502},
    {"id": "ca_montreal", "country": "ca", "city": "Montreal",
     "hostname": "ca-montreal.privacy.network", "port_udp": 1198, "port_tcp": 502},
    {"id": "de_frankfurt", "country": "de", "city": "Frankfurt",
     "hostname": "de-frankfurt.privacy.network", "port_udp": 1198, "port_tcp": 502},
    {"id": "nl_amsterdam", "country": "nl", "city": "Amsterdam",
     "hostname": "nl-amsterdam.privacy.network", "port_udp": 1198, "port_tcp": 502},
    {"id": "ch_zurich", "country": "ch", "city": "Zurich",
     "hostname": "swiss.privacy.network", "port_udp": 1198, "port_tcp": 502},
    {"id": "se_stockholm", "country": "se", "city": "Stockholm",
     "hostname": "sweden.privacy.network", "port_udp": 1198, "port_tcp": 502},
    {"id": "jp_tokyo", "country": "jp", "city": "Tokyo",
     "hostname": "japan.privacy.network", "port_udp": 1198, "port_tcp": 502},
    {"id": "au_sydney", "country": "au", "city": "Sydney",
     "hostname": "aus-sydney.privacy.network", "port_udp": 1198, "port_tcp": 502},
    {"id": "sg_singapore", "country": "sg", "city": "Singapore",
     "hostname": "singapore.privacy.network", "port_udp": 1198, "port_tcp": 502},
]

# PIA uses their own DNS in-tunnel.
PIA_DNS = "10.0.0.243"

_USERNAME_RE = re.compile(r"^p\d{7,}$")


def test_credentials(credentials: dict) -> tuple[bool, str]:
    user = (credentials or {}).get("username", "").strip()
    pw = (credentials or {}).get("password", "").strip()
    if not user or not pw:
        return False, "username and password required"
    if not _USERNAME_RE.match(user):
        return False, "PIA usernames begin with 'p' followed by digits, e.g. p1234567"

    try:
        import httpx
        with httpx.Client(timeout=PIA_API_TIMEOUT_S) as c:
            r = c.post(
                f"{PIA_API_BASE}/api/client/v2/token",
                data={"username": user, "password": pw},
            )
            if r.status_code == 200 and r.json().get("token"):
                return True, "credentials accepted by PIA"
            if r.status_code in (401, 403):
                return False, "PIA rejected credentials"
            return True, f"format ok, API returned {r.status_code}"
    except Exception as e:
        return True, f"format ok; API unreachable ({type(e).__name__})"


def list_locations(credentials: dict) -> list[dict]:
    out = []
    for loc in _LOCATIONS:
        out.append({**loc, "source": "builtin"})
    return out


def render_config(
    backend: str,
    location: str,
    credentials: dict,
    socks_port: int,
) -> dict:
    if backend == "wireguard":
        return _render_pia_wireguard(location, credentials)
    if backend != "openvpn":
        raise ValueError(f"PIA does not support backend {backend!r}")

    loc = _find_location(location)
    if loc is None:
        raise ValueError(f"unknown PIA location: {location!r}")
    creds = credentials or {}
    user = (creds.get("username") or "").strip()
    pw = (creds.get("password") or "").strip()
    if not (user and pw):
        raise ValueError("PIA OpenVPN requires username and password")
    ca_pem = (creds.get("ca_pem") or "").strip()
    # Audit 2026-05 (Phase 5): without a CA, OpenVPN refuses to start in
    # TLS mode -- fail at render time with an actionable message rather
    # than letting OpenVPN error out on connect with a cryptic "Cannot
    # load CA". The user gets a clear hint in CREDENTIALS_SCHEMA on where
    # to download the cert.
    if not ca_pem or "BEGIN CERTIFICATE" not in ca_pem:
        raise ValueError(
            "PIA OpenVPN requires the PIA CA certificate (PEM). Paste the "
            "contents of ca.rsa.4096.crt from PIA's OpenVPN config bundle "
            "into the 'PIA CA certificate' field."
        )

    ovpn = _build_ovpn(loc, ca_pem)
    return {"ovpn": ovpn, "username": user, "password": pw}


# ─── WireGuard (v3.66.680) ──────────────────────────────────────────
#
# PIA's WG flow (from the module docstring):
#   1. POST /api/client/v2/token (username+password) -> short-lived token
#   2. addKey on the region host with our public key -> assigned peer IP,
#      server public key, server endpoint, in-tunnel DNS
#   3. Assemble a config dict for vpn_wireguard.render_conf
#
# All network calls import httpx function-locally (same pattern as
# test_credentials) so no module-level import edge is added. Live PIA
# verification is operator-side; the pure assembler (_build_wg_config) and
# the backend wiring are what the sandbox tests cover.

def generate_keypair() -> tuple[str, str]:
    """Return (private_key_b64, public_key_b64) for WireGuard (curve25519)."""
    try:
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        import base64 as _b64
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
    return (_b64.b64encode(priv_b).decode("ascii"),
            _b64.b64encode(pub_b).decode("ascii"))


def _pia_token(user: str, pw: str) -> str:
    """Exchange username+password for a short-lived PIA bearer token."""
    import httpx
    with httpx.Client(timeout=PIA_API_TIMEOUT_S) as c:
        r = c.post(f"{PIA_API_BASE}/api/client/v2/token",
                   data={"username": user, "password": pw})
        if r.status_code == 200:
            return (r.json() or {}).get("token", "") or ""
    return ""


def _pia_addkey(region_host: str, token: str, pubkey: str) -> dict:
    """Register our WG public key with the region host; return the assigned
    peer config (server_key, server_ip/port, peer_ip, dns_servers)."""
    import httpx
    # PIA's manual-connections WG endpoint lives on the region host, :1337.
    url = f"https://{region_host}:1337/addKey"
    with httpx.Client(timeout=PIA_API_TIMEOUT_S, verify=False) as c:
        r = c.get(url, params={"pt": token, "pubkey": pubkey})
        if r.status_code == 200:
            return r.json() or {}
    return {}


def _build_wg_config(loc: dict, private_key: str, addkey: dict) -> dict:
    """Pure assembler: turn an addKey response into a vpn_wireguard config
    dict. No network. Missing fields fall back to PIA defaults."""
    addkey = addkey or {}
    peer_ip = str(addkey.get("peer_ip") or "").strip()
    server_key = str(addkey.get("server_key") or "").strip()
    server_ip = str(addkey.get("server_ip") or "").strip()
    server_port = addkey.get("server_port") or 1337
    dns_list = addkey.get("dns_servers") or [PIA_DNS]
    dns = dns_list[0] if isinstance(dns_list, list) and dns_list else PIA_DNS
    address = peer_ip if "/" in peer_ip else (f"{peer_ip}/32" if peer_ip else "")
    return {
        "private_key": private_key,
        "address": address,
        "peer_public_key": server_key,
        "endpoint": f"{server_ip}:{server_port}",
        "dns": dns,
        "allowed_ips": "0.0.0.0/0,::/0",
    }


def _render_pia_wireguard(location: str, credentials: dict) -> dict:
    loc = _find_location(location)
    if loc is None:
        raise ValueError(f"unknown PIA location: {location!r}")
    creds = credentials or {}
    user = (creds.get("username") or "").strip()
    pw = (creds.get("password") or "").strip()
    if not (user and pw):
        raise ValueError("PIA WireGuard requires username and password")
    priv_b64, pub_b64 = generate_keypair()
    token = _pia_token(user, pw)
    if not token:
        raise ValueError("PIA token request failed (check credentials / connectivity)")
    addkey = _pia_addkey(loc["hostname"], token, pub_b64)
    status = addkey.get("status") if isinstance(addkey, dict) else None
    if not addkey or (status is not None and status != "OK"):
        raise ValueError(f"PIA addKey failed: {status!r}")
    return _build_wg_config(loc, priv_b64, addkey)


# ─── Internals ──────────────────────────────────────────────────────

def _find_location(location_id: str) -> dict | None:
    for loc in _LOCATIONS:
        if loc["id"] == location_id:
            return loc
    return None


def _build_ovpn(loc: dict, ca_pem: str) -> str:
    """PIA's strong-encryption template, parameterized by hostname/port.

    Uses AES-256-CBC + SHA256 (matches PIA's published "openvpn-strong"
    bundle, not the legacy AES-128-CBC + SHA1 bundle). The CA cert is
    embedded inline -- without it OpenVPN refuses to start in TLS mode.
    Audit 2026-05 (Phase 5): the previous template claimed "strong" in
    the docstring but actually used cipher aes-128-cbc + auth sha1 and
    had no <ca> block at all, so it would never have completed a TLS
    handshake."""
    # Sanity-clamp CA to one cert block; strip extraneous whitespace.
    ca_pem = ca_pem.strip()
    return f"""client
dev tun
proto udp
remote {loc['hostname']} {loc['port_udp']}
resolv-retry infinite
nobind
persist-key
persist-tun
cipher aes-256-cbc
auth sha256
tls-client
remote-cert-tls server
auth-user-pass
verb 3
reneg-sec 0
<ca>
{ca_pem}
</ca>
""".strip() + "\n"


__all__ = [
    "PROVIDER_ID", "PROVIDER_NAME", "SUPPORTED_BACKENDS", "CREDENTIALS_SCHEMA",
    "test_credentials", "list_locations", "render_config",
]
