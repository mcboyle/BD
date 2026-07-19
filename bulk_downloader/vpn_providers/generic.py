"""v3.43.60: Generic provider — accepts user-supplied .conf / .ovpn files.

For any VPN provider not in our built-in list. The user pastes their
existing config file and we parse it. One "location" per imported config.

# CREDENTIALS_SCHEMA

  - configs (str)  - newline-separated entries: NAME|::|CONFIG_TEXT
                     OR JSON dict {name: config_text}

A single config can be either a WireGuard .conf or an OpenVPN .ovpn —
we detect by content (looks for [Interface] / [Peer] vs OpenVPN directives
like 'client', 'dev tun', 'remote').

# Backends

Both. Each imported config is auto-classified.

# Use cases

  - User has a "bring your own VPN" provider (NordVPN, Surfshark, AirVPN, etc.)
    that exports OpenVPN configs
  - User has a WireGuard server somewhere (Hetzner, DigitalOcean) and just
    wants to point us at the .conf
  - Testing with a single ad-hoc config
"""
from __future__ import annotations

import json
import re
import sys
from typing import Any

PROVIDER_ID = "generic"
PROVIDER_NAME = "Generic (BYO config)"
SUPPORTED_BACKENDS = ("wireguard", "openvpn")
CREDENTIALS_SCHEMA = [
    {"key": "configs", "label": "Config files (JSON or NAME|::|TEXT lines)",
     "type": "textarea", "required": True,
     "hint": "JSON: {\"my-server\": \"<config text>\"} or text: lines of NAME|::|CONFIG"},
]

# Detection patterns.
_WG_SECTION_RE = re.compile(r"^\s*\[(Interface|Peer)\]\s*$", re.MULTILINE)
_OVPN_HINT_RE = re.compile(r"^\s*(client|dev\s+tun|dev\s+tap|remote\s+\S+|proto\s+(udp|tcp))\b",
                           re.MULTILINE | re.IGNORECASE)


def test_credentials(credentials: dict) -> tuple[bool, str]:
    parsed = _parse_configs((credentials or {}).get("configs", ""))
    if not parsed:
        return False, "no configs found — paste at least one .conf or .ovpn"
    wg_count = sum(1 for _, kind, _ in parsed if kind == "wireguard")
    ovpn_count = sum(1 for _, kind, _ in parsed if kind == "openvpn")
    return True, f"loaded {wg_count} WireGuard + {ovpn_count} OpenVPN config(s)"


def list_locations(credentials: dict) -> list[dict]:
    parsed = _parse_configs((credentials or {}).get("configs", ""))
    out = []
    for name, kind, text in parsed:
        loc = {"id": name, "country": "", "city": name, "kind": kind, "source": "imported"}
        if kind == "wireguard":
            wg = _parse_wg_conf(text)
            if wg.get("endpoint"):
                # extract country code from hostname if it looks like xx-yyy-zz.host.com
                m = re.match(r"^([a-z]{2})-", wg["endpoint"].split(":")[0].lower())
                if m:
                    loc["country"] = m.group(1)
        out.append(loc)
    return out


def render_config(
    backend: str,
    location: str,
    credentials: dict,
    socks_port: int,
) -> dict:
    parsed = _parse_configs((credentials or {}).get("configs", ""))
    target = None
    for name, kind, text in parsed:
        if name == location:
            target = (kind, text)
            break
    if target is None:
        raise ValueError(f"no imported config named {location!r}")
    kind, text = target

    if backend == "wireguard" and kind == "wireguard":
        return _parse_wg_conf(text)
    if backend == "openvpn" and kind == "openvpn":
        return {"ovpn": text}
    raise ValueError(
        f"config {location!r} is a {kind} config but caller requested {backend}"
    )


# ─── Parsing ────────────────────────────────────────────────────────

def _parse_configs(raw: Any) -> list[tuple[str, str, str]]:
    """Return list of (name, kind, text) where kind in {'wireguard','openvpn'}."""
    if not raw:
        return []
    out: list[tuple[str, str, str]] = []

    # Try JSON first.
    if isinstance(raw, dict):
        items = raw
    else:
        items = None
        if isinstance(raw, str):
            stripped = raw.strip()
            if stripped.startswith("{"):
                try:
                    items = json.loads(stripped)
                    if not isinstance(items, dict):
                        items = None
                except json.JSONDecodeError as e:
                    sys.stderr.write(f"[vpn-generic] JSON parse error: {e}\n")
                    items = None

    if items is not None:
        for name, text in items.items():
            if not isinstance(text, str):
                continue
            kind = _detect_kind(text)
            if kind:
                out.append((str(name), kind, text))
        return out

    # Otherwise, treat as NAME|::|TEXT entries separated by blank-line-then-NAME.
    if isinstance(raw, str):
        for chunk in re.split(r"\n\s*\n(?=[^\s])", raw):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "|::|" in chunk:
                name, _, text = chunk.partition("|::|")
                name = name.strip()
                text = text.strip()
            else:
                name, text = f"config-{len(out) + 1}", chunk
            kind = _detect_kind(text)
            if kind:
                out.append((name, kind, text))
    return out


def _detect_kind(text: str) -> str:
    """Return 'wireguard', 'openvpn', or '' if undetectable."""
    if _WG_SECTION_RE.search(text):
        return "wireguard"
    if _OVPN_HINT_RE.search(text):
        return "openvpn"
    return ""


def _parse_wg_conf(text: str) -> dict:
    """Parse a wg-quick .conf into our config dict shape.

    Returns dict with private_key, address, peer_public_key, endpoint, etc.
    Raises ValueError on missing required fields.
    """
    section = None
    interface: dict = {}
    peer: dict = {}
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].lower()
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip()
        bucket = interface if section == "interface" else peer if section == "peer" else None
        if bucket is None:
            continue
        bucket[key] = value

    out = {}
    if "privatekey" in interface:
        out["private_key"] = interface["privatekey"]
    if "address" in interface:
        # wg-quick allows comma-separated; take the first
        out["address"] = interface["address"].split(",")[0].strip()
    if "dns" in interface:
        out["dns"] = interface["dns"]
    if "mtu" in interface:
        try:
            out["mtu"] = int(interface["mtu"])
        except ValueError:
            pass
    if "publickey" in peer:
        out["peer_public_key"] = peer["publickey"]
    if "presharedkey" in peer:
        out["preshared_key"] = peer["presharedkey"]
    if "endpoint" in peer:
        out["endpoint"] = peer["endpoint"]
    if "allowedips" in peer:
        out["allowed_ips"] = peer["allowedips"]
    if "persistentkeepalive" in peer:
        try:
            out["persistent_keepalive"] = int(peer["persistentkeepalive"])
        except ValueError:
            pass

    missing = [k for k in ("private_key", "address", "peer_public_key", "endpoint")
               if not out.get(k)]
    if missing:
        raise ValueError(f"imported wg config missing required fields: {missing}")
    return out


__all__ = [
    "PROVIDER_ID", "PROVIDER_NAME", "SUPPORTED_BACKENDS", "CREDENTIALS_SCHEMA",
    "test_credentials", "list_locations", "render_config",
]
