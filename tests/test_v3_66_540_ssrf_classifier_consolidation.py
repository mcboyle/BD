"""RED-first guard for the v3.66.540 SSRF stale-classifier consolidation.

F-APP01-01 / F-RUN01-01: app.py ``_is_url_public`` and runner.py
``SiteRunner._scrape_listing_urls`` each hand-rolled an IP denylist that MISSED
RFC 6598 CGNAT (100.64.0.0/10). The canonical
``provider_resolve_impl._common._is_safe_public_host`` / ``_classify_ip`` (fixed
under VR-P15 @ v3.66.524) rejects CGNAT; these two copies did not -- so an
authenticated user could drive an outbound fetch at carrier-internal 100.64/10
space via /api/scrape_listing or the subscription scanner. This cut routes both
through the single canonical predicate.

RED on the pre-540 tree:
  * ``app._is_url_public("http://100.64.0.1/")`` returns True (CGNAT accepted).
  * ``runner.py`` still hand-rolls the inline denylist (no ``_is_safe_public_host``).
GREEN once both delegate to the canonical guard.

Runner convention: zero-arg fns; module globals restored in try/finally.
"""
import io
import os
import re

import bulk_downloader.app as a

_RUNNER = os.path.join(os.path.dirname(a.__file__), "runner.py")


# ---- F-APP01-01: app.py _is_url_public delegates to the canonical guard ----

def test_app_is_url_public_rejects_cgnat():
    # 100.64.0.1 is RFC 6598 CGNAT (carrier-internal). Must be refused.
    assert a._is_url_public("http://100.64.0.1/list") is False, \
        "CGNAT 100.64.0.1 accepted -- app._is_url_public still diverges from _classify_ip"


def test_app_is_url_public_preserves_public_and_private_verdicts():
    # regression: delegation must not change existing accept/reject behavior.
    assert a._is_url_public("http://8.8.8.8/") is True          # public unicast literal
    assert a._is_url_public("http://127.0.0.1/") is False       # loopback
    assert a._is_url_public("http://10.0.0.5/") is False        # RFC1918
    assert a._is_url_public("http://169.254.1.1/") is False     # link-local
    assert a._is_url_public("ftp://8.8.8.8/") is False          # non-http scheme
    assert a._is_url_public("not a url") is False               # garbage -> fail closed


def test_app_is_url_public_agrees_with_canonical_guard_on_cgnat():
    from bulk_downloader.provider_resolve_impl._common import _is_safe_public_host
    ok, _reason = _is_safe_public_host("100.64.0.1")
    assert ok is False, "canonical guard should already reject CGNAT (VR-P15 @524)"
    assert a._is_url_public("http://100.64.0.1/") is False, \
        "app wrapper must agree with the canonical guard"


# ---- F-RUN01-01: runner.py routes the listing scrape through the canonical guard ----

def test_runner_scrape_listing_delegates_to_canonical_guard():
    src = io.open(_RUNNER, "r", encoding="utf-8").read()
    # the fix: _scrape_listing_urls uses the shared predicate ...
    assert "_is_safe_public_host" in src, \
        "runner.py must route the listing-scrape SSRF check through _is_safe_public_host"
    # ... and no longer hand-rolls the stale 6-predicate denylist inline.
    stale = re.search(r"is_private\s+or\s+ip_obj\.is_loopback"
                      r"[\s\S]{0,160}?is_unspecified", src)
    assert stale is None, \
        "runner.py still hand-rolls the inline IP denylist (the stale CGNAT-missing copy)"
