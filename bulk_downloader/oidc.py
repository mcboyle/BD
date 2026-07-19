"""v3.66.681 (B2/P6): OIDC / SSO login.

A single configurable OIDC provider using standard discovery. Config lives in
global_config: oidc_enabled, oidc_issuer, oidc_client_id, oidc_client_secret,
oidc_redirect_uri, oidc_scopes. The OIDC subject is mapped to a BD user,
provisioned as an operator on first login (with a random local password that
is never used — they authenticate via the provider). id_token signatures are
verified with authlib.jose against the provider's JWKS.

Network + JOSE imports are function-local: this module adds no module-level
import edge and stays importable when authlib isn't installed (is_enabled()
and URL building work without it; only the live callback needs authlib).
"""
from __future__ import annotations

import secrets
from typing import Optional
from urllib.parse import urlencode

_DISCO_CACHE: dict = {}


def oidc_config() -> dict:
    try:
        from .global_config import get_config
        cfg = get_config() or {}
    except Exception:
        cfg = {}
    return {
        "enabled": bool(cfg.get("oidc_enabled")),
        "issuer": (cfg.get("oidc_issuer") or "").rstrip("/"),
        "client_id": cfg.get("oidc_client_id") or "",
        "client_secret": cfg.get("oidc_client_secret") or "",
        "redirect_uri": cfg.get("oidc_redirect_uri") or "",
        "scopes": cfg.get("oidc_scopes") or "openid email profile",
    }


def is_enabled() -> bool:
    c = oidc_config()
    return bool(c["enabled"] and c["issuer"] and c["client_id"])


def new_state() -> str:
    return secrets.token_urlsafe(24)


def discover(issuer: str) -> dict:
    """Fetch + cache the provider's OpenID configuration document."""
    issuer = (issuer or "").rstrip("/")
    if not issuer:
        raise ValueError("no OIDC issuer configured")
    if issuer in _DISCO_CACHE:
        return _DISCO_CACHE[issuer]
    import httpx
    with httpx.Client(timeout=10) as c:
        r = c.get(issuer + "/.well-known/openid-configuration")
        r.raise_for_status()
        doc = r.json()
    _DISCO_CACHE[issuer] = doc
    return doc


def build_authorize_url(*, state: str, nonce: str,
                        disco: Optional[dict] = None) -> str:
    """Construct the provider authorize URL. `disco` may be injected (tests /
    caching); otherwise it's fetched via discovery."""
    cfg = oidc_config()
    if disco is None:
        disco = discover(cfg["issuer"])
    ep = disco.get("authorization_endpoint")
    if not ep:
        raise ValueError("provider has no authorization_endpoint")
    q = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "scope": cfg["scopes"],
        "state": state,
        "nonce": nonce,
    }
    sep = "&" if "?" in ep else "?"
    return f"{ep}{sep}{urlencode(q)}"


def exchange_code(code: str, *, disco: Optional[dict] = None) -> dict:
    """Exchange an authorization code for tokens at the token endpoint."""
    cfg = oidc_config()
    if disco is None:
        disco = discover(cfg["issuer"])
    ep = disco.get("token_endpoint")
    if not ep:
        raise ValueError("provider has no token_endpoint")
    import httpx
    with httpx.Client(timeout=10) as c:
        r = c.post(ep, data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": cfg["redirect_uri"],
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
        })
        r.raise_for_status()
        return r.json()


def verify_id_token(id_token: str, *, nonce: Optional[str] = None,
                    disco: Optional[dict] = None) -> dict:
    """Verify id_token signature (against provider JWKS) + iss/aud/nonce.
    Returns the validated claims. Requires authlib (function-local import)."""
    cfg = oidc_config()
    if disco is None:
        disco = discover(cfg["issuer"])
    if not id_token:
        raise ValueError("no id_token")
    from authlib.jose import jwt as _jwt
    import httpx
    jwks_uri = disco.get("jwks_uri")
    if not jwks_uri:
        raise ValueError("provider has no jwks_uri")
    with httpx.Client(timeout=10) as c:
        jwks = c.get(jwks_uri).json()
    claims = _jwt.decode(id_token, jwks)
    claims.validate()  # exp / iat / nbf
    if (claims.get("iss") or "").rstrip("/") != cfg["issuer"]:
        raise ValueError("issuer mismatch")
    aud = claims.get("aud")
    if isinstance(aud, list):
        if cfg["client_id"] not in aud:
            raise ValueError("aud mismatch")
    elif aud != cfg["client_id"]:
        raise ValueError("aud mismatch")
    if nonce is not None and claims.get("nonce") != nonce:
        raise ValueError("nonce mismatch")
    return dict(claims)


def claims_to_username(claims: dict) -> str:
    """Map OIDC claims to a BD username, preferring preferred_username, then
    email, then the opaque subject."""
    if not isinstance(claims, dict):
        return ""
    return (claims.get("preferred_username")
            or claims.get("email")
            or claims.get("sub") or "").strip()


def provision_user(claims: dict) -> str:
    """Ensure a BD user exists for these claims; return the username. First
    login creates an operator with a random (unused) local password."""
    from . import user_accounts as _ua
    username = claims_to_username(claims)
    if not username:
        raise ValueError("no username claim (preferred_username/email/sub)")
    if _ua.get_user(username) is None:
        _ua.create_user(username, secrets.token_urlsafe(32), role="operator")
    return username
