"""Regression tests for the redaction recursion fix in capture_redact.

A signed URL carried URL-encoded inside another request's query value
(e.g. ``mediaResource=https%3A%2F%2F...%26Signature%3D...``) used to slip
past the flat top-level query redactor. These pin the recursive behavior
and the URL-valued-header handling.
"""

import urllib.parse as up

import pytest

from bulk_downloader.capture_redact import (
    redact_query,
    scrub_headers,
    PLACEHOLDER,
)

_SIGNED = ("https://cdn.x.com/v.mp4?Policy=eyJ0ZXN0IjoxfQ"
           "&Signature=SIGTOKEN123ABC&Key-Pair-Id=KPID456&dim=720")


class TestNestedRedaction:

    def test_top_level_unchanged_behavior(self):
        # Plain sensitive/non-sensitive split still works.
        out = redact_query("https://x/a?id=10&token=SECRET")
        assert "id=10" in out
        assert "token=" + PLACEHOLDER in out
        assert "SECRET" not in out

    def test_nested_encoded_signed_url_inner_params_redacted(self):
        enc = up.quote(_SIGNED, safe="")
        out = redact_query(f"https://api.x.com/p?mediaResource={enc}&v=1")
        decoded = up.unquote(out)
        # Inner signing params gone…
        assert "SIGTOKEN123ABC" not in out and "SIGTOKEN123ABC" not in decoded
        assert "KPID456" not in decoded
        assert "eyJ0ZXN0IjoxfQ" not in decoded
        # …but the nested host/path is preserved (recon value kept)…
        assert "cdn.x.com/v.mp4" in decoded
        # …and the outer non-sensitive param survives.
        assert out.endswith("&v=1")

    def test_nested_non_signed_url_left_intact(self):
        plain = up.quote("https://cdn.x.com/v.mp4?quality=720&n=2", safe="")
        out = redact_query(f"https://api.x.com/p?u={plain}")
        # No sensitive inner params → value returned unchanged.
        assert out == f"https://api.x.com/p?u={plain}"

    def test_no_query_returned_unchanged(self):
        assert redact_query("https://x/a/b") == "https://x/a/b"
        assert redact_query("not a url") == "not a url"

    def test_recursion_is_bounded(self):
        # Doubly-nested signed URL terminates and still strips the secret.
        inner = up.quote(_SIGNED, safe="")
        mid = up.quote(f"https://m.x/r?next={inner}", safe="")
        out = redact_query(f"https://api.x/p?u={mid}")
        assert "SIGTOKEN123ABC" not in up.unquote(up.unquote(out))


class TestUrlValuedHeaders:

    def test_referer_with_signed_url_redacted_list_form(self):
        hdrs = [{"name": "Referer", "value": _SIGNED},
                {"name": "Accept", "value": "*/*"}]
        out = scrub_headers(hdrs)
        ref = next(h for h in out if h["name"] == "Referer")
        acc = next(h for h in out if h["name"] == "Accept")
        assert "SIGTOKEN123ABC" not in ref["value"]
        assert "Signature=" + PLACEHOLDER in ref["value"]
        assert acc["value"] == "*/*"  # non-URL value untouched

    def test_cookie_header_still_fully_redacted(self):
        out = scrub_headers([{"name": "Cookie", "value": "sid=abc"}])
        assert out[0]["value"] == PLACEHOLDER

    def test_dict_form_url_value_redacted(self):
        out = scrub_headers({"Referer": _SIGNED, "X-Other": "plain"})
        assert "SIGTOKEN123ABC" not in out["Referer"]
        assert out["X-Other"] == "plain"
