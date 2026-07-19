"""RED-first guard for F-TOOLS_OTHER07-01.

scrub_capture() is the last-line credential scrubber run before a bd-recon
capture becomes a committed test fixture. It redacted each script tag's `src`
query but explicitly LEFT the inline `content` field untouched -- contradicting
its own docstring -- so embedded signed URLs / API keys / bearer tokens leaked
into committed fixtures. The fix runs inline content through the same
query-redactor + I0008 floor classifier.

Pre-fix: the content-embedded secrets survive scrub_capture -> fails.
"""
import json
from tools.scrub_recon import scrub_capture


def _cap():
    return {
        "url": "https://members.example.com/watch?token=SECRET_TOP",
        "script_tags_of_interest": [
            {"id": "ld1", "type": "application/ld+json",
             "src": "https://cdn.example.com/x.js?sig=SIGVAL",
             "content": '{"contentUrl":"https://cdn.example.com/v.m3u8'
                        '?token=eyJLEAKED&Signature=ABCDEF&Policy=XYZ"}',
             "content_length": 90, "content_truncated": False},
            {"id": "cfg", "type": "text/javascript", "src": None,
             "content": 'window.__CFG__={"apiKey":"AIzaLEAKEDKEY",'
                        '"bearer":"Bearer sk-live-LEAK"};',
             "content_length": 70, "content_truncated": False},
        ],
    }


def test_scrub_recon_redacts_script_content():
    blob = json.dumps(scrub_capture(_cap()))
    survivors = [t for t in ("eyJLEAKED", "Signature=ABCDEF",
                             "AIzaLEAKEDKEY", "sk-live-LEAK") if t in blob]
    assert survivors == [], f"content-embedded secrets survived: {survivors}"
    # controls: the pre-existing redactions must still hold
    assert "SECRET_TOP" not in blob, "top-level url token leaked"
    assert "sig=SIGVAL" not in blob, "script src query leaked"


def test_scrub_recon_preserves_benign_script_content():
    cap = {"script_tags_of_interest": [
        {"id": "b", "type": "application/ld+json", "src": None,
         "content": '{"name":"VideoThumbnail","width":320,'
                    '"contentUrl":"https://cdn.example.com/thumb.webp"}'}]}
    blob = json.dumps(scrub_capture(cap))
    # non-secret page data (structure + non-signed URL) is preserved
    assert "VideoThumbnail" in blob
    assert "width" in blob and "320" in blob
    assert "thumb.webp" in blob


def test_scrub_recon_content_absent_or_nonstring_is_safe():
    # entries without a content field, or with a non-string content, don't crash
    cap = {"script_tags_of_interest": [
        {"id": "n", "src": None},
        {"id": "x", "content": None},
        {"id": "y", "content": 42},
    ]}
    out = scrub_capture(cap)  # must not raise
    assert isinstance(out.get("script_tags_of_interest"), list)
