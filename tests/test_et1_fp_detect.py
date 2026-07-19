"""Tests for E-T1 fingerprinting detection (bulk_downloader.fp_detect).

Pins the detector's contract: presence-only detection of anti-bot vendors,
fingerprint-echo headers, and challenge responses, from capture data the
site already returned. Posture: these tests assert DETECTION, never any
evasion/replay behaviour (there is none to test — the module emits nothing
runnable).
"""
import pytest

from bulk_downloader.fp_detect import detect_fingerprinting


def _cap(entries):
    """Wrap network_log entries in a capture dict."""
    return {"network_log": entries}


class TestVendorDetection:
    # Header values are matched case-insensitively; we test both the
    # CDP list-of-dicts shape and the flat-mapping shape, since the capture
    # pipeline can produce either.

    def test_cloudflare_via_header_flat_mapping(self):
        cap = _cap([{
            "url": "https://site.example/video",
            "response_status": 200,
            "response_headers": {"CF-Ray": "abc123", "Server": "cloudflare"},
        }])
        out = detect_fingerprinting(cap)
        assert out["fingerprinting_detected"] is True
        vendors = {v["vendor"] for v in out["vendors"]}
        assert "cloudflare" in vendors
        cf = next(v for v in out["vendors"] if v["vendor"] == "cloudflare")
        # Evidence is carried, not just a verdict.
        assert "header:cf-ray" in cf["tells"]
        assert "server:cloudflare" in cf["tells"]

    def test_datadome_via_cdp_list_headers(self):
        cap = _cap([{
            "url": "https://site.example/api",
            "response_status": 200,
            "response_headers": [
                {"name": "X-DataDome", "value": "protected"},
                {"name": "Content-Type", "value": "application/json"},
            ],
        }])
        out = detect_fingerprinting(cap)
        assert {v["vendor"] for v in out["vendors"]} == {"datadome"}

    def test_cookie_prefix_match_survives_value_redaction(self):
        # The capture redacts cookie VALUES; we match on the NAME prefix, so
        # detection must still fire on a redacted Set-Cookie.
        cap = _cap([{
            "url": "https://site.example/",
            "response_status": 403,
            "response_headers": {
                "Set-Cookie": "datadome=<<REDACTED>>; Path=/; Secure",
            },
        }])
        out = detect_fingerprinting(cap)
        assert {v["vendor"] for v in out["vendors"]} == {"datadome"}
        dd = out["vendors"][0]
        assert "cookie:datadome" in dd["tells"]

    def test_akamai_bot_manager_cookies(self):
        cap = _cap([{
            "url": "https://shop.example/",
            "response_status": 200,
            "response_headers": {
                "Set-Cookie": "ak_bmsc=xyz; _abck=foo; bm_sz=bar",
            },
        }])
        out = detect_fingerprinting(cap)
        assert {v["vendor"] for v in out["vendors"]} == {"akamai"}

    def test_multiple_vendors_distinct_requests(self):
        cap = _cap([
            {"url": "https://a.example/", "response_status": 200,
             "response_headers": {"CF-Ray": "1"}},
            {"url": "https://b.example/", "response_status": 200,
             "response_headers": {"x-px-block": "1"}},
        ])
        out = detect_fingerprinting(cap)
        assert {v["vendor"] for v in out["vendors"]} == {"cloudflare", "perimeterx"}


class TestFingerprintEchoHeaders:
    def test_ja3_echo_header_detected(self):
        cap = _cap([{
            "url": "https://site.example/",
            "response_status": 200,
            "response_headers": {"x-ja3-hash": "771,4865-4866,..."},
        }])
        out = detect_fingerprinting(cap)
        assert out["fingerprinting_detected"] is True
        assert out["fp_echo_headers"][0]["header"] == "x-ja3-hash"
        # We detect presence; we do NOT parse or surface the value for reuse.
        assert "value" not in out["fp_echo_headers"][0]


class TestChallengeDetection:
    def test_status_403_flagged_as_challenge(self):
        cap = _cap([{
            "url": "https://site.example/video",
            "response_status": 403,
            "response_headers": {},
        }])
        out = detect_fingerprinting(cap)
        assert len(out["challenges"]) == 1
        assert "status=403" in out["challenges"][0]["reason"]

    def test_cloudflare_challenge_url_flagged(self):
        cap = _cap([{
            "url": "https://site.example/cdn-cgi/challenge-platform/h/b/orchestrate",
            "response_status": 200,
            "response_headers": {},
        }])
        out = detect_fingerprinting(cap)
        assert len(out["challenges"]) == 1
        assert "url~" in out["challenges"][0]["reason"]


class TestCleanAndRobustness:
    def test_clean_capture_no_detection(self):
        cap = _cap([{
            "url": "https://site.example/video.mp4",
            "response_status": 200,
            "response_headers": {"Content-Type": "video/mp4",
                                 "Server": "nginx"},
        }])
        out = detect_fingerprinting(cap)
        assert out["fingerprinting_detected"] is False
        assert out["vendors"] == []
        assert out["challenges"] == []
        assert "No fingerprinting" in out["summary"]
        assert out["requests_scanned"] == 1

    def test_malformed_entries_degrade_not_raise(self):
        # A single bad entry must not break the whole scan.
        cap = _cap([
            None,
            "not a dict",
            {"url": "https://ok.example/", "response_status": 200,
             "response_headers": {"CF-Ray": "1"}},
            {"response_headers": 12345},  # odd header shape
        ])
        out = detect_fingerprinting(cap)
        # The one good cloudflare entry is still found; no exception.
        assert {v["vendor"] for v in out["vendors"]} == {"cloudflare"}

    def test_non_dict_capture_returns_empty_finding(self):
        for bad in (None, [], "x", 42):
            out = detect_fingerprinting(bad)
            assert out["fingerprinting_detected"] is False
            assert out["requests_scanned"] == 0

    def test_summary_is_advisory_not_evasive(self):
        # Posture guard: the operator-facing summary names the risk and
        # explicitly states no evasion was performed. It must not suggest
        # rotating/impersonating anything.
        cap = _cap([{"url": "https://s.example/", "response_status": 200,
                     "response_headers": {"x-datadome": "1"}}])
        out = detect_fingerprinting(cap)
        s = out["summary"].lower()
        assert "no evasion" in s
        for banned in ("rotate", "impersonate", "bypass", "evade", "spoof"):
            assert banned not in s
