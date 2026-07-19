"""Redaction profile — the single, env/config-driven source for the *tunable*
redaction categories (v3.66.171).

The F2 **floor** — cookies, ``Authorization``/bearer + the ``SENSITIVE_HEADER``
credential set, password-type input values, and raw opaque session / Turnstile
blobs — is intentionally NOT represented here and is never tunable in the
release: those are scrubbed unconditionally by ``capture_redactor`` /
``capture_redact`` / ``capture_artifact_redact``. Only the ``bd_dev_inspect``
override (``BD_CAPTURE_RAW``, excluded from every release zip) can cross the
floor — isolation by absence of the implementation.

This module governs only the **grey** categories, where function vs. safety is a
balance the operator tunes while finding the right setting:

  * ``network_signed_urls``      — how ``network_log[].url`` query signing is handled
  * ``dom_embedded_urls``        — how URLs embedded in ``dom_log`` node attributes
                                   (``src``/``href``/``srcset``/``poster``) are handled
  * ``emails``                   — redact rendered/URL email addresses, or keep
  * ``custom_sensitive_headers`` — ADDITIVE header-name scrub list (you may add a
                                   non-standard credential header like ``instance``;
                                   you can never remove a floor name)

Defaults are the v3.66.170 behaviour with two safety-positive shifts, both
called out below. Everything is read at call time, so a per-run env change takes
effect without a restart.

(Response-body retention — incl. HLS/DASH manifest bodies — is intentionally
NOT governed here yet: retaining manifest bodies flips a documented capture
posture with its own regression test and is a separately-gated decision.)

Posture: configuration of redaction *depth* only. It changes what a capture
*shows* in the grey categories; it never relaxes the floor and never
reconstructs, replays, or synthesises a signed stream.
"""
from __future__ import annotations

import os
from typing import Dict, Tuple

# ── URL handling modes ───────────────────────────────────────────────────────
KEEP_STRUCTURE = "keep_structure"   # host/path/name kept, signing params scrubbed
STRIP_ALL = "strip_all"             # host/path kept, the WHOLE query collapsed
KEEP_FULL = "keep_full"             # no signing redaction (test only; stamps reduced)
_URL_MODES = (KEEP_STRUCTURE, STRIP_ALL, KEEP_FULL)

# Defaults. Two deliberate shifts vs v3.66.170, each documented:
#   dom_embedded_urls: keep_full -> keep_structure  (closes the DOM-embedded-URL
#       gap: signed media/thumbnail URLs in the rrweb DOM log were never redacted;
#       keep_structure scrubs the signing but keeps the URL usable)
#   emails:            keep      -> redact          (account email is F2; the
#       rendered/URL email survived capture-time redaction; no function cost)
_DEFAULTS: Dict[str, object] = {
    "network_signed_urls": KEEP_STRUCTURE,    # == v3.66.170 (redact_query)
    "dom_embedded_urls": KEEP_STRUCTURE,      # shift
    "emails": "redact",                       # shift
    "custom_sensitive_headers": ("instance",),  # additive; floor names always scrubbed
}


def _url_mode(env_key: str, default: str, store_key: str = "") -> str:
    v = (os.environ.get(env_key) or "").strip().lower()
    # v3.66.308 (CLI→GUI parity): when a store_key is given, the global_config
    # value overrides the env seed (read at call time, lazy import, fail-safe).
    if store_key:
        try:
            from bulk_downloader import global_config as _gc
            _sv = _gc.get(store_key, None)
            if _sv:
                v = str(_sv).strip().lower()
        except Exception:
            pass
    return v if v in _URL_MODES else default


def _emails_mode() -> str:
    # v3.66.315 (CLI->GUI parity): store key `redact_emails` overrides the env seed.
    sv = ""
    try:
        from bulk_downloader import global_config as _gc
        _s = _gc.get("redact_emails", None)
        if _s:
            sv = str(_s).strip().lower()
    except Exception:
        pass
    v = sv or (os.environ.get("BD_REDACT_EMAILS") or "").strip().lower()
    if v in ("keep", "off", "0", "false", "no"):
        return "keep"
    if v in ("redact", "on", "1", "true", "yes"):
        return "redact"
    return _DEFAULTS["emails"]  # type: ignore[return-value]


def _custom_headers() -> Tuple[str, ...]:
    # v3.66.315 (CLI->GUI parity): store key `redact_extra_headers` (comma list)
    # overrides the env seed.
    try:
        from bulk_downloader import global_config as _gc
        _s = _gc.get("redact_extra_headers", None)
        if _s not in (None, ""):
            return tuple(h.strip().lower() for h in str(_s).split(",") if h.strip())
    except Exception:
        pass
    raw = os.environ.get("BD_REDACT_EXTRA_HEADERS")
    if raw is None:
        return _DEFAULTS["custom_sensitive_headers"]  # type: ignore[return-value]
    return tuple(h.strip().lower() for h in raw.split(",") if h.strip())


def current_profile() -> Dict[str, object]:
    """Resolve the active redaction profile from the environment.

    Config-file and per-capture-CLI layers wrap this later; for now env is the
    one guard-safe source. The floor is NOT in this dict — it is unconditional.
    """
    return {
        "network_signed_urls": _url_mode("BD_REDACT_NETWORK_URLS",
                                         _DEFAULTS["network_signed_urls"],
                                         store_key="redact_network_urls"),  # type: ignore[arg-type]
        "dom_embedded_urls": _url_mode("BD_REDACT_DOM_URLS",
                                       _DEFAULTS["dom_embedded_urls"],
                                       store_key="redact_dom_urls"),  # type: ignore[arg-type]
        "emails": _emails_mode(),
        "custom_sensitive_headers": _custom_headers(),
    }


def reduced_redaction(profile: Dict[str, object] | None = None) -> bool:
    """True if any grey category is dialed BELOW its default-safe setting — i.e.
    a capture made under this profile retains more than the defaults. Drives the
    ``reduced_redaction`` / ``local_only`` stamp so a relaxed capture is
    self-identifying (it must NEVER be circulated/fixtured/committed)."""
    p = profile or current_profile()
    if any(p.get(k) == KEEP_FULL for k in ("network_signed_urls", "dom_embedded_urls")):
        return True
    if p.get("emails") == "keep":
        return True
    return False
