"""v3.66.15 — Phase 5 P12: CSP / mixed-content awareness.

Tests the CSP policy parser, the per-source matcher, the candidate
violation detector, the mixed-content detector, and the end-to-end
wiring through `deep_detect()` (extracting `<meta http-equiv>`,
annotating candidates, emitting session warnings, P17-classifying
into structured disclaimers).

Five test classes:

  TestCSPParser
    Pure-function tests of `_parse_csp_policy`. Edge cases for the
    text → directives dict conversion.

  TestCSPSourceMatcher
    Tests `_csp_source_matches` against the spec's source-token
    forms: 'self', '*', scheme-only, host, wildcard host.

  TestCSPViolationDetector
    Tests `_candidate_violates_csp`'s directive-resolution order
    (media-src > connect-src > default-src) and the 'none' keyword.

  TestMixedContent
    Tests `_candidate_is_mixed_content` and how it interacts with
    deep_detect end-to-end.

  TestDeepDetectCSP
    End-to-end: extract CSP from a real `<meta>` tag, annotate
    candidates, verify session warning is added and gets a P17
    `csp_violation` / `mixed_content` disclaimer.

Why this design:

  The handoff says P12 should land independently of P4/P5 because
  the disclaimer pipeline (P17) is already in place. These tests
  confirm that wiring: CSP parsing → annotation → warning string →
  disclaimer entry. Pure detection is unit-tested separately so
  any future refactor of the wiring doesn't break the parser.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.bd_module_wipe


# ---------------------------------------------------------------------------
# TestCSPParser
# ---------------------------------------------------------------------------

class TestCSPParser:
    def test_single_directive(self):
        from bulk_downloader.deep_detect import _parse_csp_policy
        out = _parse_csp_policy("media-src 'self'")
        assert out == {"media-src": ["'self'"]}

    def test_multiple_directives(self):
        from bulk_downloader.deep_detect import _parse_csp_policy
        out = _parse_csp_policy(
            "media-src 'self'; script-src 'self' https://cdn.foo.com; "
            "img-src *")
        assert out["media-src"] == ["'self'"]
        assert out["script-src"] == ["'self'", "https://cdn.foo.com"]
        assert out["img-src"] == ["*"]

    def test_directive_name_lowercased(self):
        from bulk_downloader.deep_detect import _parse_csp_policy
        out = _parse_csp_policy("MEDIA-SRC 'self'")
        assert "media-src" in out
        assert "MEDIA-SRC" not in out

    def test_whitespace_tolerated(self):
        from bulk_downloader.deep_detect import _parse_csp_policy
        out = _parse_csp_policy(
            "  media-src  'self'   https://x.test  ;  script-src  'none'  ")
        assert out["media-src"] == ["'self'", "https://x.test"]
        assert out["script-src"] == ["'none'"]

    def test_empty_policy_returns_empty_dict(self):
        from bulk_downloader.deep_detect import _parse_csp_policy
        assert _parse_csp_policy("") == {}
        assert _parse_csp_policy("   ") == {}
        assert _parse_csp_policy(None) == {}
        assert _parse_csp_policy(123) == {}

    def test_directive_with_no_sources(self):
        """A directive with no sources is valid CSP syntax — represents
        an empty allow-list. We preserve that as an empty list."""
        from bulk_downloader.deep_detect import _parse_csp_policy
        out = _parse_csp_policy("media-src; script-src 'self'")
        assert out["media-src"] == []
        assert out["script-src"] == ["'self'"]

    def test_trailing_semicolon_tolerated(self):
        from bulk_downloader.deep_detect import _parse_csp_policy
        out = _parse_csp_policy("media-src 'self';")
        assert out == {"media-src": ["'self'"]}


# ---------------------------------------------------------------------------
# TestCSPSourceMatcher
# ---------------------------------------------------------------------------

class TestCSPSourceMatcher:
    def test_self_same_host(self):
        from bulk_downloader.deep_detect import _csp_source_matches
        assert _csp_source_matches(
            "'self'", "https://x.test/a.mp4", base_url="https://x.test/p")

    def test_self_different_host(self):
        from bulk_downloader.deep_detect import _csp_source_matches
        assert not _csp_source_matches(
            "'self'", "https://y.test/a.mp4", base_url="https://x.test/p")

    def test_self_no_base_url(self):
        """Without a base_url, 'self' can't match anything."""
        from bulk_downloader.deep_detect import _csp_source_matches
        assert not _csp_source_matches("'self'", "https://x.test/a", "")

    def test_wildcard_matches_everything(self):
        from bulk_downloader.deep_detect import _csp_source_matches
        assert _csp_source_matches("*", "https://any.where/x.mp4")
        assert _csp_source_matches("*", "http://any.where/x.mp4")
        assert _csp_source_matches("*", "data:image/png;base64,xxx")

    def test_scheme_only(self):
        from bulk_downloader.deep_detect import _csp_source_matches
        assert _csp_source_matches("https:", "https://x.test/a.mp4")
        assert not _csp_source_matches("https:", "http://x.test/a.mp4")
        assert _csp_source_matches("data:", "data:image/png;base64,xx")

    def test_full_scheme_and_host(self):
        from bulk_downloader.deep_detect import _csp_source_matches
        assert _csp_source_matches(
            "https://cdn.foo.com", "https://cdn.foo.com/a.mp4")
        # Wrong host
        assert not _csp_source_matches(
            "https://cdn.foo.com", "https://other.foo.com/a.mp4")
        # Wrong scheme
        assert not _csp_source_matches(
            "https://cdn.foo.com", "http://cdn.foo.com/a.mp4")

    def test_host_only(self):
        from bulk_downloader.deep_detect import _csp_source_matches
        assert _csp_source_matches("cdn.foo.com", "https://cdn.foo.com/x")
        assert _csp_source_matches("cdn.foo.com", "http://cdn.foo.com/x")
        assert not _csp_source_matches("cdn.foo.com", "https://other.com/x")

    def test_subdomain_wildcard_matches_child(self):
        from bulk_downloader.deep_detect import _csp_source_matches
        assert _csp_source_matches("*.foo.com", "https://a.foo.com/x")
        assert _csp_source_matches("*.foo.com", "https://deep.path.foo.com/x")

    def test_subdomain_wildcard_does_not_match_parent(self):
        """Per CSP spec, *.foo.com does NOT match foo.com itself."""
        from bulk_downloader.deep_detect import _csp_source_matches
        assert not _csp_source_matches("*.foo.com", "https://foo.com/x")

    def test_unsupported_keywords_return_false(self):
        from bulk_downloader.deep_detect import _csp_source_matches
        # 'unsafe-inline', 'nonce-xxx', 'sha256-xxx' aren't URL allow-list
        # tokens — they always return False since they don't apply.
        assert not _csp_source_matches("'unsafe-inline'", "https://x/a")
        assert not _csp_source_matches("'nonce-abc'", "https://x/a")
        assert not _csp_source_matches("'sha256-xxx='", "https://x/a")


# ---------------------------------------------------------------------------
# TestCSPViolationDetector
# ---------------------------------------------------------------------------

class TestCSPViolationDetector:
    def test_media_src_allowed(self):
        from bulk_downloader.deep_detect import _candidate_violates_csp
        d = {"media-src": ["'self'", "https://cdn.allowed.test"]}
        assert _candidate_violates_csp(
            "https://cdn.allowed.test/x.mp4", d,
            base_url="https://page.test/") is None

    def test_media_src_blocks_other_host(self):
        from bulk_downloader.deep_detect import _candidate_violates_csp
        d = {"media-src": ["'self'", "https://cdn.allowed.test"]}
        msg = _candidate_violates_csp(
            "https://evil.test/x.mp4", d,
            base_url="https://page.test/")
        assert msg is not None
        assert "media-src" in msg
        assert "evil.test" in msg

    def test_none_keyword_blocks_everything(self):
        from bulk_downloader.deep_detect import _candidate_violates_csp
        d = {"media-src": ["'none'"]}
        msg = _candidate_violates_csp(
            "https://any.test/x.mp4", d, base_url="https://p.test/")
        assert msg is not None
        assert "'none'" in msg

    def test_falls_back_to_connect_src(self):
        from bulk_downloader.deep_detect import _candidate_violates_csp
        d = {"connect-src": ["'self'"], "script-src": ["*"]}
        # media-src not present → falls to connect-src
        msg = _candidate_violates_csp(
            "https://evil.test/x.mp4", d,
            base_url="https://p.test/")
        assert msg is not None
        assert "connect-src" in msg

    def test_falls_back_to_default_src(self):
        from bulk_downloader.deep_detect import _candidate_violates_csp
        d = {"default-src": ["'self'"], "script-src": ["*"]}
        msg = _candidate_violates_csp(
            "https://evil.test/x.mp4", d,
            base_url="https://p.test/")
        assert msg is not None
        assert "default-src" in msg

    def test_no_applicable_directive_means_no_violation(self):
        """If only `script-src` is set, media URLs aren't governed."""
        from bulk_downloader.deep_detect import _candidate_violates_csp
        d = {"script-src": ["'self'"]}
        assert _candidate_violates_csp(
            "https://evil.test/x.mp4", d,
            base_url="https://p.test/") is None

    def test_media_src_wins_over_default_src(self):
        """When both media-src and default-src are present, media-src
        is consulted — not intersected with default-src."""
        from bulk_downloader.deep_detect import _candidate_violates_csp
        d = {
            "media-src": ["https://allowed.test"],
            "default-src": ["'self'"],
        }
        # default-src would block this; media-src allows it → no violation
        assert _candidate_violates_csp(
            "https://allowed.test/x.mp4", d,
            base_url="https://other.test/") is None

    def test_empty_directives_returns_none(self):
        from bulk_downloader.deep_detect import _candidate_violates_csp
        assert _candidate_violates_csp(
            "https://x.test/", {}, base_url="") is None
        assert _candidate_violates_csp(
            "https://x.test/", None, base_url="") is None


# ---------------------------------------------------------------------------
# TestMixedContent
# ---------------------------------------------------------------------------

class TestMixedContent:
    def test_https_page_http_candidate_is_mixed(self):
        from bulk_downloader.deep_detect import _candidate_is_mixed_content
        assert _candidate_is_mixed_content(
            "http://x.test/a.mp4", "https://page.test/")

    def test_https_page_https_candidate_is_not_mixed(self):
        from bulk_downloader.deep_detect import _candidate_is_mixed_content
        assert not _candidate_is_mixed_content(
            "https://x.test/a.mp4", "https://page.test/")

    def test_http_page_http_candidate_is_not_mixed(self):
        from bulk_downloader.deep_detect import _candidate_is_mixed_content
        assert not _candidate_is_mixed_content(
            "http://x.test/a.mp4", "http://page.test/")

    def test_http_page_https_candidate_is_not_mixed(self):
        """Upgraded transport on a candidate isn't 'mixed content' —
        it's fine. Mixed content is specifically downgraded transport."""
        from bulk_downloader.deep_detect import _candidate_is_mixed_content
        assert not _candidate_is_mixed_content(
            "https://x.test/a.mp4", "http://page.test/")

    def test_missing_inputs_return_false(self):
        from bulk_downloader.deep_detect import _candidate_is_mixed_content
        assert not _candidate_is_mixed_content("", "https://p/")
        assert not _candidate_is_mixed_content("http://x", "")
        assert not _candidate_is_mixed_content(None, None)


# ---------------------------------------------------------------------------
# TestDeepDetectCSP — end-to-end
# ---------------------------------------------------------------------------

class TestDeepDetectCSP:
    HTML_WITH_CSP = """<html><head>
<meta http-equiv="Content-Security-Policy"
      content="media-src 'self' https://cdn.allowed.test">
</head><body>
<video><source src="https://cdn.allowed.test/good_1080p.mp4"
        type="video/mp4"></video>
<video><source src="https://evil.test/bad_1080p.mp4"
        type="video/mp4"></video>
</body></html>"""

    HTML_MIXED_CONTENT = """<html><body>
<video><source src="http://insecure.test/movie_1080p.mp4"
        type="video/mp4"></video>
</body></html>"""

    HTML_NO_CSP = """<html><body>
<video><source src="https://anywhere.test/x_1080p.mp4"
        type="video/mp4"></video>
</body></html>"""

    def test_csp_field_populated_when_meta_present(self):
        from bulk_downloader.deep_detect import deep_detect
        out = deep_detect(self.HTML_WITH_CSP, base_url="https://page.test/")
        assert out["csp"] is not None
        assert "media-src" in out["csp"]["directives"]

    def test_csp_field_none_when_no_meta(self):
        from bulk_downloader.deep_detect import deep_detect
        out = deep_detect(self.HTML_NO_CSP, base_url="https://page.test/")
        assert out["csp"] is None

    def test_violating_candidate_annotated(self):
        from bulk_downloader.deep_detect import deep_detect
        out = deep_detect(self.HTML_WITH_CSP, base_url="https://page.test/")
        cands = out["buckets"]["accepted"]
        good = [c for c in cands if "cdn.allowed" in (c.get("url") or "")]
        bad = [c for c in cands if "evil" in (c.get("url") or "")]
        assert len(good) == 1 and len(bad) == 1
        assert not good[0].get("csp_violation")
        assert bad[0].get("csp_violation") is True

    def test_session_warning_emitted(self):
        from bulk_downloader.deep_detect import deep_detect
        out = deep_detect(self.HTML_WITH_CSP, base_url="https://page.test/")
        assert any("csp_violation" in w for w in out["buckets"]["warnings"])

    def test_p17_disclaimer_classification(self):
        """The session warning should flow through the P17 disclaimer
        pipeline and emerge as a {type: csp_violation, severity: warn}
        entry."""
        from bulk_downloader.deep_detect import deep_detect
        out = deep_detect(self.HTML_WITH_CSP, base_url="https://page.test/")
        types = [d["type"] for d in out.get("disclaimers", [])]
        assert "csp_violation" in types
        csp_disc = next(d for d in out["disclaimers"]
                        if d["type"] == "csp_violation")
        assert csp_disc["severity"] == "warn"

    def test_mixed_content_annotated_and_warned(self):
        from bulk_downloader.deep_detect import deep_detect
        out = deep_detect(
            self.HTML_MIXED_CONTENT, base_url="https://secure.test/page")
        cands = out["buckets"]["accepted"]
        assert any(c.get("mixed_content") for c in cands)
        assert any("mixed_content" in w for w in out["buckets"]["warnings"])
        types = [d["type"] for d in out.get("disclaimers", [])]
        assert "mixed_content" in types

    def test_no_violations_means_no_csp_warnings(self):
        from bulk_downloader.deep_detect import deep_detect
        # All candidates point at allowed origin
        html = """<html><head>
<meta http-equiv="Content-Security-Policy"
      content="media-src 'self' https://cdn.allowed.test">
</head><body>
<video><source src="https://cdn.allowed.test/x_1080p.mp4"
        type="video/mp4"></video>
</body></html>"""
        out = deep_detect(html, base_url="https://page.test/")
        # CSP present and parsed
        assert out["csp"] is not None
        # But no violations → no warnings
        assert not any("csp_violation" in w for w in out["buckets"]["warnings"])
        # And no candidate annotation
        for c in out["buckets"]["accepted"]:
            assert not c.get("csp_violation")

    def test_malformed_csp_doesnt_crash(self):
        from bulk_downloader.deep_detect import deep_detect
        html = """<html><head>
<meta http-equiv="Content-Security-Policy" content="">
</head><body>
<video><source src="https://x.test/a_1080p.mp4" type="video/mp4"></video>
</body></html>"""
        # Empty content → csp is None (no signal)
        out = deep_detect(html, base_url="https://p.test/")
        assert out["csp"] is None
        # No crash, no spurious warning
        assert not any("csp" in w.lower() for w in out["buckets"]["warnings"])

    def test_csp_only_extracted_from_meta_not_body_text(self):
        """A `<meta>` tag is the only valid source. If someone has a
        <code>Content-Security-Policy</code> blob in body text, we
        ignore it."""
        from bulk_downloader.deep_detect import deep_detect
        html = """<html><body>
<p>This page documents the meta tag: &lt;meta http-equiv="Content-Security-Policy" content="media-src 'none'"&gt;</p>
<video><source src="https://x.test/a_1080p.mp4" type="video/mp4"></video>
</body></html>"""
        out = deep_detect(html, base_url="https://p.test/")
        assert out["csp"] is None

    def test_only_first_meta_csp_is_honored(self):
        """When multiple CSP meta tags exist, we read only the first.
        Spec allows multiple (intersection); our simpler implementation
        documents this limitation explicitly."""
        from bulk_downloader.deep_detect import deep_detect
        html = """<html><head>
<meta http-equiv="Content-Security-Policy" content="media-src 'self'">
<meta http-equiv="Content-Security-Policy" content="media-src 'none'">
</head><body>
<video><source src="https://p.test/x_1080p.mp4" type="video/mp4"></video>
</body></html>"""
        out = deep_detect(html, base_url="https://p.test/")
        # First meta: media-src 'self' → same-origin allowed → no violation
        assert out["csp"]["directives"]["media-src"] == ["'self'"]
        # The candidate is same-origin, so it should NOT be flagged
        for c in out["buckets"]["accepted"]:
            if c.get("url") and "p.test" in c["url"]:
                assert not c.get("csp_violation"), (
                    "first meta CSP allows same-origin; "
                    "candidate should not be flagged")
