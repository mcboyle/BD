"""RFC 6238 TOTP helper (row895).

Deterministic time-based verification codes for a single operator-owned
test2 target's MFA endpoint, so an automated test pipeline can complete
its own login without a human present. This module never touches a
real third-party site: it generates codes from a secret the operator
already configured for their own test target, the same way any
authenticator app would.

Secrets are namespaced separately from site passwords in secrets_store
(``bulkdl-totp-<site_id>``, not ``bulkdl-site-<site_id>``) so a TOTP
seed can never be confused with, or silently substitute for, a login
password.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time

DEFAULT_DIGITS = 6
DEFAULT_PERIOD = 30
DEFAULT_ALGORITHM = "sha1"

_ALGORITHMS = {
    "sha1": hashlib.sha1,
    "sha256": hashlib.sha256,
    "sha512": hashlib.sha512,
}

_TOTP_KEY_PREFIX = "bulkdl-totp-"


class MfaConfigError(Exception):
    """Raised when a TOTP secret is missing, malformed, or unresolvable.

    Kept distinct from a wrong/expired code (a verification return of
    False): this is specifically "there is nothing usable to check
    against", which a caller must not treat as just another failed
    attempt against a live endpoint.
    """


def _decode_secret(secret: str) -> bytes:
    if not secret or not isinstance(secret, str):
        raise MfaConfigError("TOTP secret is missing or empty")
    # Authenticator apps display/accept base32 without padding; normalize
    # to uppercase and restore padding before decoding.
    normalized = secret.strip().replace(" ", "").upper()
    padded = normalized + "=" * (-len(normalized) % 8)
    try:
        return base64.b32decode(padded, casefold=True)
    except Exception as exc:
        raise MfaConfigError(f"TOTP secret is not valid base32: {exc}") from exc


def _hotp(key: bytes, counter: int, *, digits: int, algorithm: str) -> str:
    digestmod = _ALGORITHMS.get(algorithm.lower())
    if digestmod is None:
        raise MfaConfigError(f"unsupported TOTP algorithm: {algorithm!r}")
    counter_bytes = struct.pack(">Q", counter)
    digest = hmac.new(key, counter_bytes, digestmod).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    code = truncated % (10 ** digits)
    return str(code).zfill(digits)


def generate_totp(
    secret: str,
    *,
    timestamp: float | None = None,
    digits: int = DEFAULT_DIGITS,
    period: int = DEFAULT_PERIOD,
    algorithm: str = DEFAULT_ALGORITHM,
) -> str:
    """Return the TOTP code for *secret* at *timestamp* (default: now).

    Raises :class:`MfaConfigError` for a missing/malformed secret or an
    unsupported algorithm -- never for a code simply not matching.
    """
    key = _decode_secret(secret)
    when = time.time() if timestamp is None else timestamp
    counter = int(when) // period
    return _hotp(key, counter, digits=digits, algorithm=algorithm)


def verify_totp(
    secret: str,
    code: str,
    *,
    timestamp: float | None = None,
    digits: int = DEFAULT_DIGITS,
    period: int = DEFAULT_PERIOD,
    algorithm: str = DEFAULT_ALGORITHM,
    window: int = 1,
) -> bool:
    """Whether *code* is valid for *secret* within +/- *window* steps.

    The window compensates for clock drift between the machine running
    the pipeline and the target's own time source -- the same
    tolerance every TOTP verifier implements, not a widening of what
    counts as a match at a single instant.
    """
    if not code or not isinstance(code, str):
        return False
    when = time.time() if timestamp is None else timestamp
    key = _decode_secret(secret)
    base_counter = int(when) // period
    for offset in range(-window, window + 1):
        candidate = _hotp(
            key, base_counter + offset, digits=digits, algorithm=algorithm
        )
        if hmac.compare_digest(candidate, code):
            return True
    return False


def site_totp_key(site_id: str) -> str:
    """Namespaced secrets_store key for *site_id*'s TOTP seed."""
    return f"{_TOTP_KEY_PREFIX}{site_id}"


def resolve_totp_secret(site_id: str) -> str:
    """Look up the TOTP seed configured for *site_id* via secrets_store.

    Raises :class:`MfaConfigError` when no seed has been configured, so
    a caller cannot silently proceed with a login flow it cannot
    actually complete.
    """
    from .. import secrets_store

    key = site_totp_key(site_id)
    try:
        backend = secrets_store.get_backend()
        secret = backend.get(key)
    except Exception as exc:
        raise MfaConfigError(
            f"could not read TOTP configuration for {site_id!r}: {exc}"
        ) from exc
    if not secret:
        raise MfaConfigError(
            f"no TOTP secret configured for {site_id!r}; set one before "
            "running an MFA-gated login for this target"
        )
    return secret
