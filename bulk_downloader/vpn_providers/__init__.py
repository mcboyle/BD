"""v3.43.60: VPN provider implementations.

Each module under this package implements the provider contract used by
app.py to populate the VPN config wizard:

  PROVIDER_ID:       str    - matches VALID_PROVIDERS in vpn.py
  PROVIDER_NAME:     str    - human-readable name
  SUPPORTED_BACKENDS: tuple[str, ...]  - ('wireguard',) or ('wireguard', 'openvpn') etc.

  def list_locations(credentials: dict) -> list[dict]:
      '''Return [{id, country, city, hostname, ...}, ...] of available exits.'''

  def render_config(backend: str, location: str,
                    credentials: dict, socks_port: int) -> dict:
      '''Return a config dict suitable for tunnel.config. For wireguard,
      shape matches vpn_wireguard.REQUIRED_KEYS. For openvpn, must include
      'ovpn' (full .ovpn text); may also include 'username'/'password'.
      socks_port is informational — useful if a provider's API call needs to
      know the port (e.g. for socks-only providers).'''

  def test_credentials(credentials: dict) -> tuple[bool, str]:
      '''(ok, reason). Light-touch. Falls back gracefully on network failure
      with reason 'could not reach <provider> API' so UI shows actionable text.'''

The PROVIDER_REGISTRY is built lazily on first access so a provider that
fails to import (missing optional dep) doesn't break the others.
"""
from __future__ import annotations

import importlib
from typing import Optional

# Order matters for UI listing.
_KNOWN_PROVIDERS = (
    "mullvad",
    "proton",
    "pia",
    "generic",
    "selfhosted",
)

# Populated by _build_registry().
_REGISTRY: Optional[dict] = None


def _build_registry() -> dict:
    """Load every provider module. Skip and log any that fail to import."""
    import sys
    reg = {}
    for name in _KNOWN_PROVIDERS:
        try:
            mod = importlib.import_module(f"bulk_downloader.vpn_providers.{name}")
        except Exception as e:
            sys.stderr.write(f"[vpn-providers] failed to load {name}: {e}\n")
            continue
        reg[name] = mod
    return reg


def get_provider(name: str):
    """Return the loaded provider module by id, or None."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY.get(name)


def list_providers() -> list[dict]:
    """Return a UI-ready summary of every loaded provider."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    out = []
    for name in _KNOWN_PROVIDERS:
        mod = _REGISTRY.get(name)
        if not mod:
            continue
        out.append({
            "id": getattr(mod, "PROVIDER_ID", name),
            "name": getattr(mod, "PROVIDER_NAME", name),
            "supported_backends": list(getattr(mod, "SUPPORTED_BACKENDS", ("wireguard",))),
            "credentials_schema": getattr(mod, "CREDENTIALS_SCHEMA", []),
        })
    return out


def _reset_for_tests() -> None:
    global _REGISTRY
    _REGISTRY = None


__all__ = ["get_provider", "list_providers"]
