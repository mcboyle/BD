"""RED-first guard for F-COREBD17-01.

capture_redact is the capture-time single-source-of-truth URL/header/body
redactor (so credentials never hit disk). SENSITIVE_QS_KEY missed 'csrf' and
'bearer' as query-param keys, and SENSITIVE_HEADER missed a bare (unprefixed)
'csrf' header, so ?csrf=<tok> / ?bearer=<tok> in a captured URL -- or a header
literally named 'csrf' -- survived into the persisted capture. The fix adds
csrf/xsrf/bearer to the SoT (the same replay-enabling floor class the module
already scrubs for challenge/captcha/cf_chl).

Pre-fix: the query forms below survive redact_query unredacted -> fails.
"""
from bulk_downloader.capture_redact import (
    redact_query, SENSITIVE_QS_KEY, SENSITIVE_HEADER, PLACEHOLDER)

_LEAK = "SECRETVALUE0123456789"


def _masked(url, key):
    return f"{key}={PLACEHOLDER}" in redact_query(url)


# ── floor coverage: csrf / xsrf / bearer query values must be masked ─────

def test_csrf_query_value_masked():
    assert _masked(f"https://x/cb?csrf={_LEAK}", "csrf")


def test_bearer_query_value_masked():
    assert _masked(f"https://x/a?bearer={_LEAK}", "bearer")


def test_xsrf_query_value_masked():
    assert _masked(f"https://x/a?xsrf={_LEAK}", "xsrf")


def test_underscore_csrf_and_variants_masked():
    # substring form also covers _csrf and x-xsrf (not just the bare key)
    assert _LEAK not in redact_query(f"https://x/a?_csrf={_LEAK}")
    assert _LEAK not in redact_query(f"https://x/a?x-xsrf={_LEAK}")


def test_bare_csrf_header_matched():
    assert SENSITIVE_HEADER.search("csrf")          # the bare-header gap
    assert SENSITIVE_HEADER.search("x-csrf-token")  # existing coverage kept
    assert SENSITIVE_HEADER.search("bearer")        # existing coverage kept


def test_qs_sot_membership():
    for hit in ("csrf", "xsrf", "bearer"):
        assert SENSITIVE_QS_KEY.search(hit), f"{hit} should match"


# ── anchoring/benign guard: the addition must not mask look-alikes ───────

def test_benign_lookalikes_preserved():
    for key, val in [("geocode", "40.7"), ("zipcode", "10001"), ("estate", "manor"),
                     ("channel", "3"), ("chapter", "5"), ("ujc", "vxn"), ("ref", "feed")]:
        out = redact_query(f"https://x/p?{key}={val}")
        assert f"{key}={val}" in out, f"benign key '{key}' wrongly masked: {out}"


def test_signed_query_controls_unchanged():
    # the pre-existing token / Signature floor still masks (no regression)
    out = redact_query(f"https://cdn/f.mp4?token={_LEAK}&Signature=ZZZ999&keep=me")
    assert _LEAK not in out
    assert "ZZZ999" not in out
    assert "keep=me" in out
