"""v3.66.20 — Phase 12 follow-up: CSP from response headers.

v3.66.15 shipped meta-tag CSP detection. This release wires the
``Content-Security-Policy`` and ``Content-Security-Policy-Report-Only``
HTTP response headers through ``deep_detect`` (via a new
``response_headers`` kwarg) and through ``deep_detect_live``.

Test classes
------------

  TestExtractCspFromHeaders
      Unit tests on ``_extract_csp_from_headers``: header name
      case-insensitivity, comma-separated multi-policy intersection,
      report-only fallback, missing / malformed inputs.

  TestMergeCsp
      Unit tests on ``_merge_csp``: meta-only, header-only,
      intersection of meta + header, deny-all on disjoint policies.

  TestDeepDetectResponseHeaders
      End-to-end: deep_detect honours response_headers and produces
      a merged CSP that annotates candidates correctly.

  TestDeepDetectLiveResponseHeaders
      Verifies deep_detect_live threads response_headers through to
      the underlying deep_detect call.

  TestBackwardCompatibility
      response_headers=None / omitted preserves the v3.66.15 meta-only
      behaviour across every callsite touched.
"""
from __future__ import annotations

import pytest

from bulk_downloader.deep_detect import (
    _extract_csp_from_headers,
    _extract_csp_from_html,
    _merge_csp,
    deep_detect,
    deep_detect_live,
)


# ---------------------------------------------------------------------------
# TestExtractCspFromHeaders
# ---------------------------------------------------------------------------

class TestExtractCspFromHeaders:

    def test_simple_enforcing_header(self):
        out = _extract_csp_from_headers({
            "Content-Security-Policy": "media-src https://cdn.example.com",
        })
        assert out is not None
        assert out["directives"]["media-src"] == ["https://cdn.example.com"]
        assert out["from"] == "header_enforcing"

    def test_header_name_case_insensitive(self):
        for name in ("content-security-policy",
                     "Content-Security-Policy",
                     "CONTENT-SECURITY-POLICY"):
            out = _extract_csp_from_headers({name: "media-src 'self'"})
            assert out is not None, name
            assert out["directives"]["media-src"] == ["'self'"], name

    def test_report_only_fallback(self):
        """No enforcing header → use report-only as the source."""
        out = _extract_csp_from_headers({
            "Content-Security-Policy-Report-Only": "media-src https://cdn.x",
        })
        assert out is not None
        assert out["from"] == "header_report_only"
        assert out["directives"]["media-src"] == ["https://cdn.x"]

    def test_enforcing_wins_over_report_only(self):
        """Both present → enforcing takes priority."""
        out = _extract_csp_from_headers({
            "Content-Security-Policy": "media-src https://enforce.x",
            "Content-Security-Policy-Report-Only": "media-src https://report.x",
        })
        assert out["from"] == "header_enforcing"
        assert out["directives"]["media-src"] == ["https://enforce.x"]

    def test_multiple_policies_in_one_header_intersect(self):
        """Comma-separated policies in one header are intersected."""
        out = _extract_csp_from_headers({
            "Content-Security-Policy":
                "media-src https://a.com https://b.com, "
                "media-src https://b.com https://c.com",
        })
        # Intersection: only b.com is in both
        assert out["directives"]["media-src"] == ["https://b.com"]

    def test_multiple_directives_one_policy(self):
        out = _extract_csp_from_headers({
            "Content-Security-Policy":
                "media-src https://m.x; connect-src https://c.x; "
                "default-src 'self'",
        })
        d = out["directives"]
        assert d["media-src"] == ["https://m.x"]
        assert d["connect-src"] == ["https://c.x"]
        assert d["default-src"] == ["'self'"]

    def test_directive_only_in_some_policies(self):
        """Directive present in only one of two policies → kept as-is
        from that one (the other implicitly allows everything for it
        via default-src fallback at match time)."""
        out = _extract_csp_from_headers({
            "Content-Security-Policy":
                "media-src https://m.x, default-src 'self'",
        })
        # media-src only in first, default-src only in second
        assert out["directives"]["media-src"] == ["https://m.x"]
        assert out["directives"]["default-src"] == ["'self'"]

    def test_empty_headers_dict(self):
        assert _extract_csp_from_headers({}) is None

    def test_none_input(self):
        assert _extract_csp_from_headers(None) is None

    def test_non_csp_headers_ignored(self):
        out = _extract_csp_from_headers({
            "Content-Type": "text/html",
            "X-Frame-Options": "DENY",
            "Cache-Control": "no-store",
        })
        assert out is None

    def test_empty_header_value(self):
        assert _extract_csp_from_headers({
            "Content-Security-Policy": "",
        }) is None
        assert _extract_csp_from_headers({
            "Content-Security-Policy": "   ",
        }) is None

    def test_non_string_value_ignored(self):
        """A header with a non-string value (rare but possible from
        some clients) is treated as absent."""
        assert _extract_csp_from_headers({
            "Content-Security-Policy": ["media-src 'self'"],  # list
        }) is None

    def test_malformed_policy_returns_none(self):
        """A header value with no parseable directives."""
        out = _extract_csp_from_headers({
            "Content-Security-Policy": ";;;",
        })
        assert out is None


# ---------------------------------------------------------------------------
# TestMergeCsp
# ---------------------------------------------------------------------------

class TestMergeCsp:

    def test_both_none(self):
        assert _merge_csp(None, None) is None

    def test_meta_only(self):
        meta = {"policy": "x", "directives": {"media-src": ["'self'"]}}
        assert _merge_csp(meta, None) is meta

    def test_header_only(self):
        header = {"policy": "x", "directives": {"media-src": ["'self'"]}}
        assert _merge_csp(None, header) is header

    def test_intersection_when_both_have_directive(self):
        meta = {"directives": {"media-src": ["https://a.x", "https://b.x"]}}
        header = {"directives": {"media-src": ["https://b.x"]}}
        out = _merge_csp(meta, header)
        assert out["directives"]["media-src"] == ["https://b.x"]

    def test_directive_only_in_meta_preserved(self):
        meta = {"directives": {"media-src": ["'self'"]}}
        header = {"directives": {"connect-src": ["'self'"]}}
        out = _merge_csp(meta, header)
        assert out["directives"]["media-src"] == ["'self'"]
        assert out["directives"]["connect-src"] == ["'self'"]

    def test_disjoint_intersection_becomes_none_token(self):
        """meta allows only a.x; header allows only b.x. Mutually
        exclusive — represented as ['\\'none\\''] so the matcher
        denies everything."""
        meta = {"directives": {"media-src": ["https://a.x"]}}
        header = {"directives": {"media-src": ["https://b.x"]}}
        out = _merge_csp(meta, header)
        assert out["directives"]["media-src"] == ["'none'"]

    def test_sources_field_preserved_for_attribution(self):
        """The merged result keeps both originals under ``sources`` so
        callers (notably the UI) can show which layer flagged a
        violation."""
        meta = {"policy": "M", "directives": {"media-src": ["'self'"]}}
        header = {"policy": "H", "directives": {"media-src": ["'self'"]}}
        out = _merge_csp(meta, header)
        assert out["sources"]["meta"] is meta
        assert out["sources"]["header"] is header


# ---------------------------------------------------------------------------
# TestDeepDetectResponseHeaders
# ---------------------------------------------------------------------------

class TestDeepDetectResponseHeaders:

    HTML_TWO_VIDS = """<html><body>
<video><source src="https://cdn.allowed.test/good_1080p.mp4"
        type="video/mp4"></video>
<video><source src="https://evil.test/bad_1080p.mp4"
        type="video/mp4"></video>
</body></html>"""

    def test_header_csp_populates_csp_field(self):
        out = deep_detect(
            self.HTML_TWO_VIDS, base_url="https://page.test/",
            response_headers={
                "Content-Security-Policy":
                    "media-src https://cdn.allowed.test",
            })
        assert out["csp"] is not None
        assert "media-src" in out["csp"]["directives"]
        assert out["csp"]["directives"]["media-src"] == [
            "https://cdn.allowed.test"
        ]

    def test_header_csp_annotates_violations(self):
        out = deep_detect(
            self.HTML_TWO_VIDS, base_url="https://page.test/",
            response_headers={
                "Content-Security-Policy":
                    "media-src https://cdn.allowed.test",
            })
        for c in out["buckets"]["accepted"]:
            url = c.get("url") or ""
            if "cdn.allowed" in url:
                assert not c.get("csp_violation")
            elif "evil.test" in url:
                assert c.get("csp_violation") is True

    def test_meta_and_header_intersected_in_report(self):
        """meta allows a.x + b.x; header allows b.x; effective is b.x
        only — a candidate at a.x should be flagged as a violation."""
        html = """<html><head>
<meta http-equiv="Content-Security-Policy"
      content="media-src https://a.test https://b.test">
</head><body>
<video><source src="https://a.test/v_1080p.mp4"
        type="video/mp4"></video>
<video><source src="https://b.test/v_1080p.mp4"
        type="video/mp4"></video>
</body></html>"""
        out = deep_detect(
            html, base_url="https://p.test/",
            response_headers={
                "Content-Security-Policy": "media-src https://b.test",
            })
        d = out["csp"]["directives"]
        # Intersection: only b.test
        assert d["media-src"] == ["https://b.test"]
        # a.test is now flagged as a violation
        a_cand = next(c for c in out["buckets"]["accepted"]
                      if "a.test" in (c.get("url") or ""))
        b_cand = next(c for c in out["buckets"]["accepted"]
                      if "b.test" in (c.get("url") or ""))
        assert a_cand.get("csp_violation") is True
        assert not b_cand.get("csp_violation")

    def test_response_headers_omitted_legacy_behaviour(self):
        """response_headers=None preserves v3.66.15 meta-only behaviour."""
        html = """<html><head>
<meta http-equiv="Content-Security-Policy"
      content="media-src https://cdn.allowed.test">
</head><body>
<video><source src="https://cdn.allowed.test/v_1080p.mp4"
        type="video/mp4"></video>
</body></html>"""
        out_meta_only = deep_detect(html, base_url="https://p.test/")
        out_explicit_none = deep_detect(
            html, base_url="https://p.test/", response_headers=None)
        # Same csp output regardless of which form
        assert out_meta_only["csp"]["directives"] == \
            out_explicit_none["csp"]["directives"]

    def test_no_meta_no_header_csp_is_none(self):
        html = """<html><body>
<video><source src="https://x.test/v_1080p.mp4"
        type="video/mp4"></video>
</body></html>"""
        out = deep_detect(html, base_url="https://p.test/")
        assert out["csp"] is None
        out2 = deep_detect(
            html, base_url="https://p.test/", response_headers={})
        assert out2["csp"] is None

    def test_report_only_header_used_when_no_enforcing(self):
        html = """<html><body>
<video><source src="https://evil.test/v_1080p.mp4"
        type="video/mp4"></video>
</body></html>"""
        out = deep_detect(
            html, base_url="https://p.test/",
            response_headers={
                "Content-Security-Policy-Report-Only":
                    "media-src https://cdn.x.test",
            })
        assert out["csp"]["from"] == "header_report_only"
        # Even report-only generates annotations — the operator sees
        # the signal; whether to act on it is their call.
        evil = next(c for c in out["buckets"]["accepted"]
                    if "evil.test" in (c.get("url") or ""))
        assert evil.get("csp_violation") is True


# ---------------------------------------------------------------------------
# TestDeepDetectLiveResponseHeaders
# ---------------------------------------------------------------------------

class TestDeepDetectLiveResponseHeaders:
    """deep_detect_live should pass response_headers through to its
    inner deep_detect call. We don't exercise the full live pipeline
    here (probes, polling, etc.) — those are covered elsewhere — we
    just verify the CSP plumbing reaches the base report."""

    HTML_WITH_VIDEO = """<html><body>
<video><source src="https://evil.test/v_1080p.mp4"
        type="video/mp4"></video>
</body></html>"""

    def test_live_threads_response_headers(self):
        out = deep_detect_live(
            self.HTML_WITH_VIDEO, base_url="https://p.test/",
            response_headers={
                "Content-Security-Policy":
                    "media-src https://safe.test",
            },
            # Disable everything network-touching so the test stays
            # offline; we only care about the CSP plumbing.
            http=None, max_probes=0, follow_meta_refresh_redirects=False,
            sniff_attachments=False, follow_manifests=False,
        )
        assert out["csp"] is not None
        assert out["csp"]["directives"]["media-src"] == ["https://safe.test"]

    def test_live_no_response_headers_preserves_legacy(self):
        out = deep_detect_live(
            self.HTML_WITH_VIDEO, base_url="https://p.test/",
            http=None, max_probes=0,
            follow_meta_refresh_redirects=False,
            sniff_attachments=False, follow_manifests=False,
        )
        assert out["csp"] is None  # no meta, no header


# ---------------------------------------------------------------------------
# TestBackwardCompatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """v3.66.15 callers that didn't know about response_headers
    should see no behaviour change."""

    HTML_META = """<html><head>
<meta http-equiv="Content-Security-Policy"
      content="media-src https://cdn.allowed.test">
</head><body>
<video><source src="https://cdn.allowed.test/v_1080p.mp4"
        type="video/mp4"></video>
<video><source src="https://evil.test/v_1080p.mp4"
        type="video/mp4"></video>
</body></html>"""

    def test_meta_only_path_unchanged(self):
        out = deep_detect(self.HTML_META, base_url="https://p.test/")
        assert out["csp"] is not None
        assert out["csp"]["directives"]["media-src"] == [
            "'self'" if False else "https://cdn.allowed.test"
        ]
        evil = next(c for c in out["buckets"]["accepted"]
                    if "evil.test" in (c.get("url") or ""))
        assert evil.get("csp_violation") is True

    def test_meta_csp_shape_preserved(self):
        """The meta-CSP result still has a ``policy`` field (raw text)
        and a ``directives`` field — v3.66.15 callers may depend on
        these keys."""
        meta = _extract_csp_from_html(self.HTML_META)
        assert "policy" in meta
        assert "directives" in meta
        assert isinstance(meta["directives"], dict)
