"""Tests for C-T2 body slice (bulk_downloader.capture_bodies).

The load-bearing test is TestNoSigningMaterialSurvives — it is deliberately
ADVERSARIAL: it hides real signing material (JWTs, signed CloudFront/S3 URLs,
HLS key/end/limit segment URLs) under INNOCENT field names, and asserts none
of it survives a stored body. A happy-path test that only checks a field
literally named "token" would pass while the real .52-class bug (signing
material under a benign key) slips through, so we do not write that test.

Harness note: body capture is gated on the BD_CAPTURE_BODIES env var, read at
call time. We set/clear it INSIDE each test via the `_bodies` helper rather
than an autouse fixture, because the custom runner's autouse+env-fixture
interaction is a known-fragile pytest-shim corner (.52 lesson): a plain
assignment in the test body behaves identically under pytest and run_tests.py.

Posture: this module is record-redact-export. The tests assert redaction and
the default-OFF contract; there is no replay/reassembly behaviour to test.
"""
import json
import os
import contextlib

from bulk_downloader import capture_bodies as cb
from bulk_downloader.capture_redact import PLACEHOLDER


@contextlib.contextmanager
def _bodies(on):
    """Deterministically set/clear BD_CAPTURE_BODIES for the duration of a
    test, restoring the prior value afterwards. No fixture machinery."""
    saved = os.environ.get("BD_CAPTURE_BODIES")
    if on:
        os.environ["BD_CAPTURE_BODIES"] = "1"
    else:
        os.environ.pop("BD_CAPTURE_BODIES", None)
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("BD_CAPTURE_BODIES", None)
        else:
            os.environ["BD_CAPTURE_BODIES"] = saved


# Real-shaped signing material, NONE of it under a sensitive key name.
_JWT = ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U")
_SIGNED_CF = ("https://d111.cloudfront.net/video.mp4?Expires=1700000000"
              "&Signature=abcDEF123&Key-Pair-Id=APKAEXAMPLE")
_SIGNED_S3 = ("https://b.s3.amazonaws.com/v.mp4?X-Amz-Signature=deadbeef"
              "&X-Amz-Credential=AKIA%2F20231101%2Fus-east-1")
_HLS_SEG = "https://cdn.example/seg/12.ts?key=AAAA&end=999&limit=1"


class TestNoSigningMaterialSurvives:
    """The hard guarantee. Each body buries signing material under a benign
    key; after redaction the serialized body must contain NONE of it. The
    `_not_a_marker` check ensures the flag-on retention path actually ran (a
    bare length marker would otherwise satisfy 'secret absent + placeholder
    present' by accident)."""

    def _assert_clean(self, stored, *secrets):
        blob = stored if isinstance(stored, str) else json.dumps(stored)
        for secret in secrets:
            assert secret not in blob, f"signing material survived: {secret!r}"
        assert PLACEHOLDER in blob
        assert not (isinstance(stored, str)
                    and stored.startswith(PLACEHOLDER + "(len=")), (
            "body reduced to a length marker — flag-on path did not run")

    def test_jwt_under_benign_key_data(self):
        with _bodies(True):
            out = cb.redact_body(json.dumps({"data": _JWT, "page": 3}),
                                 "application/json")
        self._assert_clean(out, _JWT.split(".")[-1])

    def test_signed_cloudfront_url_under_key_src(self):
        with _bodies(True):
            out = cb.redact_body(json.dumps({"src": _SIGNED_CF, "title": "ok"}),
                                 "application/json")
        self._assert_clean(out, "abcDEF123")

    def test_signed_s3_url_in_nested_array(self):
        with _bodies(True):
            out = cb.redact_body(json.dumps({"items": [{"file": _SIGNED_S3}]}),
                                 "application/json")
        self._assert_clean(out, "deadbeef")

    def test_hls_signed_segment_under_key_uri(self):
        with _bodies(True):
            out = cb.redact_body(json.dumps({"uri": _HLS_SEG}),
                                 "application/json")
        self._assert_clean(out, "key=AAAA")

    def test_signed_url_in_textplain_body_scrubbed(self):
        with _bodies(True):
            out = cb.redact_body(f"redirect to {_SIGNED_CF} now", "text/plain")
        assert "abcDEF123" not in out
        assert PLACEHOLDER in out

    def test_mpegurl_playlist_retained_redacted(self):
        # v3.66.172 posture flip: HLS/DASH manifests are now retention-eligible
        # (text, not stream bytes). The body is RETAINED (not a marker) and run
        # through the text scrub, so the query-signed CloudFront URL is masked.
        playlist = f"#EXTM3U\n{_SIGNED_CF}\n"
        with _bodies(True):
            out = cb.redact_body(playlist, "application/vnd.apple.mpegurl")
        assert isinstance(out, str)
        assert not out.startswith(PLACEHOLDER + "(len="), \
            "manifest reduced to a length marker — posture-flip path did not run"
        assert "#EXTM3U" in out          # structure preserved
        assert "abcDEF123" not in out    # query signature scrubbed
        assert PLACEHOLDER in out

    def test_dash_mpd_retained(self):
        # DASH .mpd (application/dash+xml) is likewise retained, not a marker.
        mpd = '<?xml version="1.0"?><MPD><BaseURL>seg.m4s</BaseURL></MPD>'
        with _bodies(True):
            out = cb.redact_body(mpd, "application/dash+xml")
        assert isinstance(out, str)
        assert not out.startswith(PLACEHOLDER + "(len="), \
            "mpd reduced to a length marker — posture-flip path did not run"
        assert "MPD" in out

    def test_jwt_bare_in_array(self):
        with _bodies(True):
            out = cb.redact_body(json.dumps({"tokens": [_JWT]}),
                                 "application/json")
        self._assert_clean(out, _JWT.split(".")[-1])

    def test_deeply_nested_signed_url(self):
        with _bodies(True):
            out = cb.redact_body(
                json.dumps({"a": {"b": {"c": {"d": {"e": _SIGNED_CF}}}}}),
                "application/json")
        blob = out if isinstance(out, str) else json.dumps(out)
        assert "abcDEF123" not in blob


class TestSignedUrlProvenanceSurvives:
    """v3.66.58 (Reason 2): a signed media URL in a JSON body keeps its
    non-secret host/path/id and benign query (the provenance C-T2 traces)
    while only its signing params are scrubbed — instead of the pre-.58
    wholesale mask that took the id/filename down with the signature. HLS
    segments and path-signed URLs stay fully masked (posture)."""

    _SIGNED_MEDIA = ("https://dd-video.wg.com/53eb2252/7680x4320_60FPS.mp4"
                     "?download=attachment&filename=sunset-bombshell.mp4"
                     "&expires=1700000000&token=SECRETabc")

    def test_id_and_filename_survive_signing_scrubbed(self):
        with _bodies(True):
            out = cb.redact_body(
                json.dumps({"streamSources": [{"file": self._SIGNED_MEDIA,
                                               "label": "4K"}]}),
                "application/json")
        # provenance kept
        assert "53eb2252" in out
        assert "7680x4320_60FPS.mp4" in out
        assert "sunset-bombshell.mp4" in out
        # signing scrubbed
        assert "SECRETabc" not in out
        assert "1700000000" not in out
        assert "expires=<scrubbed>" in out and "token=<scrubbed>" in out

    def test_cloudfront_path_survives(self):
        with _bodies(True):
            out = cb.redact_body(json.dumps({"src": _SIGNED_CF}),
                                 "application/json")
        assert "cloudfront.net/video.mp4" in out   # provenance kept
        assert "abcDEF123" not in out               # signature scrubbed

    def test_hls_segment_still_fully_masked(self):
        # Posture: the canonical short-lived signed stream is never kept,
        # even partially — no segment path, no bounds.
        with _bodies(True):
            out = cb.redact_body(json.dumps({"uri": _HLS_SEG}),
                                 "application/json")
        assert "12.ts" not in out
        assert "key=AAAA" not in out
        assert "end=999" not in out

    def test_path_signed_url_fully_masked(self):
        # Signing tokens in the PATH (not query) → keeping the path would leak
        # them, so full mask.
        path_signed = "https://cdn.x/key=SECRETKEY,end=999/video.mp4"
        with _bodies(True):
            out = cb.redact_body(json.dumps({"src": path_signed}),
                                 "application/json")
        assert "SECRETKEY" not in out


class TestBenignContentPreserved:
    """Redaction must keep the provenance signal — non-signing values that
    let _trace_source resolve source_unknown params have to survive."""

    def test_plain_id_and_filename_kept(self):
        with _bodies(True):
            out = cb.redact_body(
                json.dumps({"video_id": "12345", "name": "clip.mp4",
                            "width": 1920}),
                "application/json")
        parsed = json.loads(out)
        assert parsed["video_id"] == "12345"
        assert parsed["name"] == "clip.mp4"
        assert parsed["width"] == 1920

    def test_sensitive_key_name_still_masked(self):
        with _bodies(True):
            out = cb.redact_body(
                json.dumps({"session_token": "short", "id": "7"}),
                "application/json")
        parsed = json.loads(out)
        assert parsed["session_token"] == PLACEHOLDER
        assert parsed["id"] == "7"


class TestDefaultOffContract:
    def test_flag_unset_is_length_marker(self):
        with _bodies(False):
            out = cb.redact_body(json.dumps({"video_id": "12345"}),
                                 "application/json")
        assert out.startswith(PLACEHOLDER)
        assert "12345" not in out

    def test_non_text_type_never_retained_even_when_on(self):
        with _bodies(True):
            out = cb.redact_body("RAWBYTESHERE", "application/octet-stream")
        assert out.startswith(PLACEHOLDER)
        assert "RAWBYTES" not in out

    def test_video_body_never_retained(self):
        with _bodies(True):
            out = cb.redact_body("\x00\x00mp4data", "video/mp4")
        assert out.startswith(PLACEHOLDER)

    def test_none_body_stays_none(self):
        with _bodies(True):
            assert cb.redact_body(None, "application/json") is None


class TestRobustness:
    def test_unparseable_json_falls_back_to_text_scrub(self):
        with _bodies(True):
            out = cb.redact_body("{not valid json " + _SIGNED_CF,
                                 "application/json")
        assert "abcDEF123" not in out

    def test_recursion_depth_capped(self):
        obj = cur = {}
        for _ in range(40):
            cur["x"] = {}
            cur = cur["x"]
        cur["x"] = _SIGNED_CF
        with _bodies(True):
            out = cb.redact_body(json.dumps(obj), "application/json")
        blob = out if isinstance(out, str) else json.dumps(out)
        assert "abcDEF123" not in blob
