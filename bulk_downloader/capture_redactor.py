"""Redactor seam for session capture.

All capture-time scrubbing in :class:`bulk_downloader.session_capture.
SessionCapture` routes through the *active* redactor returned by
:func:`active_redactor`. The release ships exactly one redactor — the real
:class:`Redactor`, which always redacts — and never swaps it: nothing in the
release reads an env var or config that changes the active redactor, so a
production capture is *always* redacted.

A dev-only raw-inspection mode (an UNREDACTED capture for local
troubleshooting and hand-fixing) is implemented by installing an alternate
redactor through the inert ``_override`` hook below. That alternate redactor
and the code that activates it live ONLY in the separate ``bd_dev_inspect``
dev package, which is excluded from the release manifest
(``dev_suite._MANIFEST_EXCLUDE_NAMES``) and asserted absent from every release
zip by ``build_release``. The release therefore *physically contains no code*
that can emit an unredacted capture — ``_override`` is ``None`` here and only
the dev package ever sets it. Isolation is by absence of the implementation,
not by a password on a shipped capability.

Posture: this is an *inspection* seam. It can change what a capture **shows**
(redacted vs raw, dev-only); it never changes what the tool **does**. There is
no replay, reconstruction, or signed-stream synthesis here or in the dev
package — those remain out of scope pending a separate, explicit decision.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from .capture_bodies import redact_body
from .capture_redact import (PLACEHOLDER, apply_url_mode, body_marker,
                             redact_query, scrub_headers)
from . import redaction_profile as _rp


class Redactor:
    """The real, always-on redactor — the production default.

    Wraps the :mod:`bulk_downloader.capture_redact` / ``capture_bodies``
    primitives so that routing capture-time scrubbing through a redactor
    object is byte-for-byte identical to calling them directly. ``unredacted``
    is False; a capture scrubbed by this redactor carries no raw-capture
    stamp.
    """

    name = "redact"
    unredacted = False

    def query(self, url: str) -> str:
        # v3.66.171: mode from the active profile. Default keep_structure ==
        # redact_query (byte-identical to v3.66.170).
        return apply_url_mode(url, _rp.current_profile()["network_signed_urls"])

    def headers(self, headers: Any) -> Any:
        # v3.66.171: additive custom-header scrub from the profile. The floor
        # (Cookie/Authorization/SENSITIVE_HEADER) is enforced inside scrub_headers
        # regardless; the profile set can only widen it.
        return scrub_headers(headers, _rp.current_profile()["custom_sensitive_headers"])

    def request_body(self, body: Any) -> Any:
        return body_marker(body)

    def response_body(self, body: Any, content_type: Optional[str]) -> Any:
        return redact_body(body, content_type)

    def cookies(self, cookies: Any) -> Any:
        return PLACEHOLDER


_REAL = Redactor()


class _PassThrough(Redactor):
    """Raw pass-through redactor — returns every field UNCHANGED. v3.66.308:
    installed when the operator enables ``capture_raw`` (global_config store key,
    seeded by the ``BD_CAPTURE_RAW`` env var). This is the operator's explicit
    raw-retention override; per the operator directive it does NOT fail closed
    and does NOT force a reduced-redaction stamp. The build-time release-zip
    exclusion of raw ``.wacz`` artifacts is unaffected.
    """
    name = "raw"
    unredacted = True

    def query(self, url):  # noqa: D401
        return url

    def headers(self, headers):
        return headers

    def request_body(self, body):
        return body

    def response_body(self, body, content_type):
        return body

    def cookies(self, cookies):
        return cookies


_PASSTHROUGH = _PassThrough()


def _capture_raw_enabled() -> bool:
    """True iff the operator has opted into raw (un-redacted) capture via the
    global_config store key ``capture_raw`` (store > ``BD_CAPTURE_RAW`` env seed).
    Read at call time; lazy import; fail-safe to redacting on any error."""
    env_on = os.environ.get("BD_CAPTURE_RAW", "").strip() in ("1", "true", "True", "yes")
    try:
        from bulk_downloader import global_config as _gc
        v = _gc.get("capture_raw", env_on)
    except Exception:
        return env_on
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")

# Inert in the release: nothing here ever assigns to it. Only the separate
# bd_dev_inspect dev package (excluded from release zips) sets this, and only
# when BD_CAPTURE_RAW is explicitly enabled, to install a pass-through
# redactor for local inspection.
_override: Optional[Redactor] = None


def active_redactor() -> Redactor:
    """Return the redactor SessionCapture should use.

    Precedence: an explicit dev ``_override`` (bd_dev_inspect, excluded from
    release) wins; else the operator's ``capture_raw`` opt-in installs the raw
    pass-through (v3.66.308); else :data:`_REAL` (the default — redaction on).
    """
    if _override is not None:
        return _override
    if _capture_raw_enabled():
        return _PASSTHROUGH
    return _REAL
