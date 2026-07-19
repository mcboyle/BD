"""Authoritative blocked-term list for reusable URL/API material.

Single source of truth shared by:
  * the promote gate (``tools/promote_template.py``) — rejects a candidate whose
    ``network_patterns`` or ``api`` values contain a blocked term;
  * the inventory diagnostic (``tools/template_inventory.py``) — flags the same;
  * the network-pattern scrubber (``bulk_downloader.pattern_hygiene``) — drops a
    pattern containing a blocked term at normalize time.

Because all three consume this one list with the same case-insensitive substring
test, the scrubber is a *superset* of the gate: any pattern the scrubber keeps is
guaranteed not to be rejected by the promote gate on blocked-term grounds
(scrubber ⊇ gate). This closes the historical divergence where the scrubber kept
``cdnjs``/``cloudflare`` patterns that the gate would then reject.

Blocked substrings cover telemetry / ads / CDN-noise hosts and signed/credential
query fragments. They are matched case-insensitively as substrings of a pattern's
full text (host + path + query), mirroring the promote gate's check exactly.

This module is pure (stdlib only, no imports of project modules, no side effects)
so it is safe to import from both ``bulk_downloader`` and ``tools``.
"""
from __future__ import annotations

from typing import List, Optional

# Blocked substrings in reusable URL/API material (telemetry, ads, CDN noise,
# and signed/credential query fragments). review_notes/safety_notes are NOT
# scanned by consumers (they may legitimately mention removed terms).
BAD_TERMS: List[str] = [
    "sentry", "cloudflare", "cdnjs", "tsyndicate", "doubleclick",
    "google-analytics", "googletagmanager", "event-log", "exclusive-offers",
    "experiments", "banners", "comments", "votes", "active-subscriptions",
    "token=", "signature=", "expires=", "auth=", "sig=",
]


def first_bad_term(text: object) -> Optional[str]:
    """Return the first :data:`BAD_TERMS` entry contained (case-insensitive
    substring) in ``text``, or ``None``. This is the exact membership test the
    promote gate applies, exposed so the scrubber can match it byte-for-byte."""
    low = str(text).lower()
    for bad in BAD_TERMS:
        if bad in low:
            return bad
    return None


def contains_bad_term(text: object) -> bool:
    """True iff ``text`` contains any blocked term (see :func:`first_bad_term`)."""
    return first_bad_term(text) is not None
