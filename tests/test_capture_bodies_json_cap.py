"""Size cap for retained JSON bodies (capture_bodies, rec #3).

When body capture is opted in (``BD_CAPTURE_BODIES=1``), the text path caps the
stored body at ``_MAX_TEXT_LEN``, but the JSON path parsed + redacted +
re-serialized with NO size bound — a retained JSON body was size-unbounded
(memory/disk blowup on a large API payload). This caps the JSON path too: an
oversize JSON body (raw string over the cap, or a redacted result over the cap)
falls back to the length marker, exactly like an ineligible body. Real JSON
under the cap is still parsed, redacted, and retained unchanged.

RED on pristine (the oversize body is retained verbatim); GREEN after the cap.
"""

import os

from bulk_downloader.capture_bodies import (redact_body, body_marker,
                                            _MAX_JSON_LEN)


def _run_with_bodies(fn):
    old = os.environ.get("BD_CAPTURE_BODIES")
    os.environ["BD_CAPTURE_BODIES"] = "1"
    try:
        fn()
    finally:
        if old is None:
            os.environ.pop("BD_CAPTURE_BODIES", None)
        else:
            os.environ["BD_CAPTURE_BODIES"] = old


def test_oversize_json_body_falls_back_to_marker():
    def body():
        huge = '{"blob":"' + ("a" * (_MAX_JSON_LEN + 4096)) + '"}'
        out = redact_body(huge, "application/json")
        # The full payload must NOT be retained; it collapses to the marker.
        assert out == body_marker(huge), type(out)
        assert "aaaaaaaaaaaaaaaa" not in str(out)
    _run_with_bodies(body)


def test_normal_json_body_still_retained_and_redacted():
    def body():
        b = '{"id": 123, "token": "eyJhbGciOiJIUzI1NiJ9.payloadpart.sigpart"}'
        out = redact_body(b, "application/json")
        s = str(out)
        # JWT value redacted, non-secret id retained, body NOT collapsed to marker.
        assert "eyJhbG" not in s, s
        assert "123" in s, s
        assert out != body_marker(b), s
    _run_with_bodies(body)


def test_text_path_cap_unchanged():
    # The pre-existing text cap behaviour is untouched: a huge text body is
    # truncated (not markered) and scrubbed.
    def body():
        from bulk_downloader.capture_bodies import _MAX_TEXT_LEN
        big = "x " * _MAX_TEXT_LEN  # well over the char cap
        out = redact_body(big, "text/plain")
        assert isinstance(out, str)
        assert len(out) <= _MAX_TEXT_LEN + 8
    _run_with_bodies(body)
