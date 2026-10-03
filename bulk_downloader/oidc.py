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

import hashlib
import logging
import secrets
import time
import unicodedata
from typing import Optional
from urllib.parse import urlencode

_DISCO_CACHE: dict = {}
_log = logging.getLogger(__name__)


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
    from bulk_downloader.ssrf_transport import guarded_transport, PINNED
    with httpx.Client(timeout=10, transport=guarded_transport(PINNED)) as c:
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
    from bulk_downloader.ssrf_transport import guarded_transport, PINNED
    with httpx.Client(timeout=10, transport=guarded_transport(PINNED)) as c:
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
    from bulk_downloader.ssrf_transport import guarded_transport, PINNED
    with httpx.Client(timeout=10, transport=guarded_transport(PINNED)) as c:
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
    email, then the opaque subject. The chosen claim is used EXACTLY: one with
    surrounding whitespace or a control character raises (-> sso_error) instead
    of being normalised, so "admin " can never log in as BD user "admin" (O1698)."""
    if not isinstance(claims, dict):
        return ""
    raw = (claims.get("preferred_username")
           or claims.get("email")
           or claims.get("sub") or "")
    if not isinstance(raw, str):
        raise ValueError("username claim is not a string")
    if raw != raw.strip() or any(unicodedata.category(ch) == "Cc" for ch in raw):
        raise ValueError("username claim is not exact (surrounding whitespace or control character)")
    return raw


def _oidc_subject(claims: dict) -> tuple[str, str]:
    """The verified (iss, sub) pair a login is bound to. sub is kept EXACTLY as
    the token carries it (OIDC: case-sensitive, compared as-is) -- trimming would
    let "victim " reuse "victim"'s account. iss is compared the way
    verify_id_token accepted it (trailing slash stripped, nothing else)."""
    iss, sub = claims.get("iss"), claims.get("sub")
    if not isinstance(iss, str) or not isinstance(sub, str) or not iss or not sub:
        raise ValueError("no iss/sub claim to bind the login to")
    return iss.rstrip("/"), sub


def provision_user(claims: dict) -> str:
    """Ensure a BD user exists for these claims; return the username. First
    login creates an operator with a random (unused) local password, bound to
    the token's (iss, sub).

    O1717 rebind migration (ahead of a07): an existing user is reused only when
    bound to this exact (iss, sub). One with no or a legacy (partial) binding --
    admins too (O1720) -- is bound to it now if iss is the configured issuer,
    under the store lock, and logged at WARN. A user bound to any other
    (iss, sub), or an unbound one under another issuer, is refused -- the
    callback turns the ValueError into sso_error -- and the store is unchanged."""
    from . import user_accounts as _ua
    username = claims_to_username(claims)
    if not username:
        raise ValueError("no username claim (preferred_username/email/sub)")
    subject = _oidc_subject(claims)
    if _ua.get_user(username) is None:
        ok, msg = _ua.create_user(username, secrets.token_urlsafe(32),
                                  role="operator", oidc_binding=subject)
        if ok:
            return username
        if _ua.get_user(username) is None:
            raise ValueError(f"could not provision {username!r}: {msg}")
        # a concurrent first login created it: its binding decides below
    prior: dict = {}
    ok, msg = _ua.bind_oidc_login(username, subject, rebind_issuer=oidc_config()["issuer"], prior=prior)
    if not ok:
        raise ValueError(f"OIDC_ACCOUNT_BINDING_REFUSED: local user {username!r}: {msg}")
    if msg == "rebound":
        # one audit line per legacy rebind: the replaced iss (repr'd; "none" if the record had none) -> the new one
        old_iss = prior.get("iss")
        _log.warning("OIDC_ACCOUNT_REBOUND user=%r old_iss=%s iss=%r sub_sha256=%s at=%s", username,
                     "none" if old_iss is None else repr(old_iss), subject[0],
                     hashlib.sha256(subject[1].encode("utf-8")).hexdigest()[:16],
                     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return username
