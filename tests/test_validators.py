"""Tests for pure validator functions — fastest tier of the suite.

These don't need the Flask app or any I/O. They protect against
regressions in security-critical paths (path traversal, endpoint privacy,
JSON extraction) and invariants used throughout the app (resolution
normalization, retry schedule parsing).
"""
import pytest


# ── Phase 27: AI endpoint validator (privacy critical) ────────────────
class TestEndpointValidator:
    """The endpoint validator MUST refuse public IPs. A regression here
    means AI calls could silently leak to a cloud endpoint."""

    def test_loopback_allowed(self, aiassist_module):
        assert aiassist_module.validate_endpoint("http://localhost:11434")[0]
        assert aiassist_module.validate_endpoint("http://127.0.0.1:11434")[0]

    def test_rfc1918_allowed(self, aiassist_module):
        assert aiassist_module.validate_endpoint("http://10.0.70.20:11434")[0]
        assert aiassist_module.validate_endpoint("http://192.168.1.100:11434")[0]
        assert aiassist_module.validate_endpoint("http://172.16.5.10:11434")[0]
        # Edge: 172.32.x.x is OUTSIDE the 172.16/12 range — public
        ok, _ = aiassist_module.validate_endpoint("http://172.32.0.1:11434")
        assert not ok

    def test_lan_suffixes_allowed(self, aiassist_module):
        for host in ("stash.local", "server.lan", "server.home",
                     "server.internal", "server.intranet"):
            ok, msg = aiassist_module.validate_endpoint(f"http://{host}:11434")
            assert ok, f"{host} should be allowed: {msg}"

    def test_public_ips_refused(self, aiassist_module):
        # Pin a known public IP rather than relying on DNS for stability
        for url in ("http://8.8.8.8:11434",
                    "http://1.1.1.1:80",
                    "http://200.50.30.10:8080"):
            ok, msg = aiassist_module.validate_endpoint(url)
            assert not ok
            assert "public" in msg.lower() or "refusing" in msg.lower()

    def test_malformed_refused(self, aiassist_module):
        for url in ("", "not-a-url", "ftp://localhost",
                    "http://", "http:///path"):
            ok, _ = aiassist_module.validate_endpoint(url)
            assert not ok, f"{url!r} should have been refused"

    def test_non_http_schemes_refused(self, aiassist_module):
        ok, _ = aiassist_module.validate_endpoint("ftp://localhost:11434")
        assert not ok
        ok, _ = aiassist_module.validate_endpoint("file:///etc/passwd")
        assert not ok


# ── Phase 27: JSON extraction (robustness against vision-model prose) ──
class TestJsonExtraction:
    def test_clean_json(self, aiassist_module):
        assert aiassist_module.extract_json('{"x": 1}') == {"x": 1}

    def test_markdown_fenced(self, aiassist_module):
        raw = '```json\n{"x": 1, "y": 2}\n```'
        assert aiassist_module.extract_json(raw) == {"x": 1, "y": 2}

    def test_markdown_no_lang(self, aiassist_module):
        raw = '```\n{"x": 1}\n```'
        assert aiassist_module.extract_json(raw) == {"x": 1}

    def test_prose_wrapped(self, aiassist_module):
        raw = ('Sure! Here is the JSON you requested:\n'
               '```json\n{"x": 1}\n```\n'
               'Let me know if you need anything else.')
        assert aiassist_module.extract_json(raw) == {"x": 1}

    def test_bare_embedded(self, aiassist_module):
        raw = 'The answer is {"x": 1} as shown above.'
        assert aiassist_module.extract_json(raw) == {"x": 1}

    def test_nested(self, aiassist_module):
        raw = '{"a": 1, "b": {"c": 2, "d": [3, 4]}}'
        assert aiassist_module.extract_json(raw) == {"a": 1, "b": {"c": 2, "d": [3, 4]}}

    def test_garbage(self, aiassist_module):
        assert aiassist_module.extract_json("garbage") is None
        assert aiassist_module.extract_json("") is None
        assert aiassist_module.extract_json(None) is None

    def test_array(self, aiassist_module):
        # Arrays are valid JSON top-level
        result = aiassist_module.extract_json('[1, 2, 3]')
        assert result == [1, 2, 3]


# ── Phase 27: Resolution normalization (regex fast path) ──────────────
class TestResolutionNormalization:
    @pytest.mark.parametrize("filename,exp_resolution,exp_label,exp_width", [
        ("Movie.4K.2160p.x265.mkv",     "2160p", "4K",   3840),
        ("whatever_7680x4320_master.mp4","4320p","8K",   7680),
        ("Episode.S01E03.1080p.WEB-DL.mp4","1080p","1080",1920),
        ("untitled.720p.mp4",            "720p",  "720",  1280),
        ("Movie.2K.QHD.1440p.mkv",       "1440p", "2K",   2560),
        ("weird.480p.mp4",               "480p",  "SD",   854),
        ("no_resolution_here.mp4",       None,    None,   0),
    ])
    def test_regex_fast_path(self, aiassist_module, filename, exp_resolution,
                              exp_label, exp_width):
        aiassist_module.configure(enabled=False)  # force regex-only
        r = aiassist_module.normalize_resolution(filename)
        assert r["ok"]
        assert r["resolution"] == exp_resolution
        assert r["label"] == exp_label
        assert r["width"] == exp_width

    def test_empty_filename(self, aiassist_module):
        r = aiassist_module.normalize_resolution("")
        assert r["ok"]
        assert r["resolution"] is None
        assert r["via"] == "empty"

    def test_8k_uppercase(self, aiassist_module):
        # Should be case-insensitive
        aiassist_module.configure(enabled=False)
        r = aiassist_module.normalize_resolution("Movie 8K Blu-ray.mkv")
        assert r["label"] == "8K"


# ── Phase 30: Retry schedule parser ────────────────────────────────────
class TestRetrySchedule:
    @pytest.fixture(autouse=True)
    def setup(self):
        # Use the parser as a free function — we don't need a real
        # SiteRunner since the method has no `self` deps
        from bulk_downloader.runner import SiteRunner
        self.parse = SiteRunner._parse_retry_schedule.__get__(object())

    def test_default(self):
        assert self.parse("") == [3600, 14400, 86400]
        assert self.parse(None) == [3600, 14400, 86400]

    def test_basic_units(self):
        assert self.parse("1h,4h,24h") == [3600, 14400, 86400]
        assert self.parse("30m,2h,12h,3d") == [1800, 7200, 43200, 259200]

    def test_seconds_bare(self):
        assert self.parse("30,60,90") == [30, 60, 90]

    def test_garbage_skipped(self):
        # Skips unparseable entries, returns valid ones
        assert self.parse("garbage,1h,bogus") == [3600]

    def test_all_garbage_returns_default(self):
        # No valid entries → falls back to default
        assert self.parse("garbage,bogus") == [3600, 14400, 86400]

    def test_fractional(self):
        assert self.parse("1.5h") == [5400]
        assert self.parse("0.5m") == [30]

    def test_whitespace(self):
        assert self.parse("  1h , 2h , 3h  ") == [3600, 7200, 10800]


# ── Phase 23.4: Site template suggestions ──────────────────────────────
class TestTemplates:
    def test_vimeo_pattern(self):
        from bulk_downloader.templates import suggest_for_url
        assert "vimeo_progressive" in suggest_for_url("https://vimeo.com/123456")
        assert "vimeo_progressive" in suggest_for_url("vimeo.com/foo")

    def test_no_match_returns_empty(self):
        from bulk_downloader.templates import suggest_for_url
        assert suggest_for_url("https://random.example.com") == []
        assert suggest_for_url("") == []
        assert suggest_for_url(None) == []

    def test_template_lookup(self):
        from bulk_downloader.templates import get, list_templates
        # Known IDs resolve
        assert get("video_js") is not None
        assert get("html5_native") is not None
        # Unknown returns None, not exception
        assert get("nonexistent") is None
        # list_templates returns the metadata view (no internal fields)
        items = list_templates()
        assert len(items) >= 7
        for t in items:
            assert "id" in t and "name" in t and "row_count" in t
            assert "learned" not in t  # private internals not exposed
