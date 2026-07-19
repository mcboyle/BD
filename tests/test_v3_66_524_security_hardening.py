"""v3.66.524 -- security hardening (non-guard): VR-P10 / VR-P15 / VR-P16.

RED on pristine v3.66.523:
  * VR-P10  batch_ops._build_query interpolates a user-controlled ``table`` name
            straight into ``f"SELECT * FROM {table}"`` (reached from the API via
            POST /api/batch/{retry,delete,move} -> filter.table). Authenticated
            bounded SQL identifier injection. Fix = allowlist the identifier.
  * VR-P15  _common._classify_ip permits RFC 6598 CGNAT / shared address space
            (100.64.0.0/10): the stdlib does NOT flag it is_private/is_reserved,
            so the SSRF classifier returned safe=True. Single fix point reached by
            both _classify_ip and its wrapper _is_safe_public_host.
  * VR-P16  jwplayer._jwplayer_check_drm_markers detects per-entry
            playlist[].drm.<scheme> and top-level SIGNED markers, but misses a
            top-level data['drm'].<scheme> block (the docstring's own
            "bubble it to the top level" case) -> mis-pins an actually-DRM feed as
            unsigned when no other DRM signal is present.

Run under run_tests.py (zero-arg, in-test imports) and real pytest.
"""
from __future__ import annotations

import ipaddress


# --------------------------------------------------------------------------
# VR-P10 -- batch_ops SQL identifier injection via filter.table
# --------------------------------------------------------------------------
def test_vr_p10_build_query_allowlists_table_identifier():
    from bulk_downloader import batch_ops as bo
    # The legitimate value still works.
    sql, _ = bo._build_query(table="history")
    assert "FROM history" in sql

    # A non-allowlisted identifier must be rejected, never interpolated.
    for evil in ("sqlite_master", "history; DROP TABLE history;--", "queue UNION SELECT"):
        raised = False
        try:
            bo._build_query(table=evil)
        except ValueError:
            raised = True
        assert raised, f"VR-P10: table={evil!r} not rejected (SQL identifier injection)"


def test_vr_p10_matching_rows_is_failsafe_on_bad_table():
    """End-to-end: a malicious table in filter_dict resolves to no rows (the bad
    identifier never reaches the DB), not a crash or a foreign-table read."""
    from bulk_downloader import batch_ops as bo
    from bulk_downloader import db
    db.db_init()
    rows = bo._matching_rows({"table": "sqlite_master"})
    assert rows == [], f"VR-P10: bad table leaked rows: {rows!r}"


# --------------------------------------------------------------------------
# VR-P15 -- SSRF classifier must reject CGNAT 100.64.0.0/10 (RFC 6598)
# --------------------------------------------------------------------------
def test_vr_p15_classify_ip_rejects_cgnat():
    from bulk_downloader.provider_resolve_impl import _common as C
    for ip in ("100.64.0.1", "100.127.255.254", "100.100.100.100"):
        ok, reason = C._classify_ip(ipaddress.ip_address(ip), ip)
        assert not ok, f"VR-P15: CGNAT {ip} allowed by _classify_ip (SSRF): {reason!r}"
    # Genuine public still allowed; private still blocked (no over-broadening).
    assert C._classify_ip(ipaddress.ip_address("8.8.8.8"), "8.8.8.8")[0] is True
    assert C._classify_ip(ipaddress.ip_address("10.0.0.1"), "10.0.0.1")[0] is False
    # A boundary just outside the block stays public.
    assert C._classify_ip(ipaddress.ip_address("100.63.255.255"), "x")[0] is True
    assert C._classify_ip(ipaddress.ip_address("100.128.0.0"), "x")[0] is True


def test_vr_p15_single_fix_point_covers_wrapper():
    """The wrapper _is_safe_public_host (literal-IP path, no DNS) must inherit the
    CGNAT rejection from the shared _classify_ip predicate."""
    from bulk_downloader.provider_resolve_impl import _common as C
    ok, _ = C._is_safe_public_host("100.64.0.1")
    assert not ok, "VR-P15: _is_safe_public_host still allows CGNAT"


# --------------------------------------------------------------------------
# VR-P16 -- jwplayer top-level data['drm'].<scheme> must be detected
# --------------------------------------------------------------------------
def test_vr_p16_top_level_drm_block_detected():
    from bulk_downloader.provider_resolve_impl.jwplayer import _jwplayer_check_drm_markers as f
    # Top-level drm block, NO per-entry drm, NO signed markers -> must detect scheme.
    assert f({"drm": {"widevine": {"licenseUrl": "x"}}, "playlist": [{"file": "a.mpd"}]}) == "widevine"
    assert f({"DRM": {"playready": {}}}) == "playready"


def test_vr_p16_no_false_positive_and_regressions_hold():
    from bulk_downloader.provider_resolve_impl.jwplayer import _jwplayer_check_drm_markers as f
    # Unsigned feed still None.
    assert f({"playlist": [{"file": "a.m3u8"}]}) is None
    assert f({"drm": {}}) is None          # empty drm block is not a marker
    assert f("garbage") is None
    # Existing per-entry + signed detection still works.
    assert f({"playlist": [{"drm": {"fairplay": {}}}]}) == "fairplay"
    assert f({"signedUrls": True}) == "signed"
