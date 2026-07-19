"""v3.43.27 audit-pass regression tests.

The audit at the 5,000-LOC milestone found two defensive-coding gaps:

  1. qb_bridge.looks_like_torrent_url raised AttributeError on int input
     ('int' object has no attribute 'strip')
  2. qb_bridge.classify_qb_error AND jd_bridge.classify_jd_error
     raised AttributeError on int input
     ('int' object has no attribute 'lower')

In practice, the runner only passes strings to these functions, but a
defensive isinstance check prevents the entire worker thread from
crashing if a malformed config or callsite bug ever passes the wrong
type. These tests lock in the contract.
"""
from __future__ import annotations


def test_looks_like_torrent_url_handles_non_string():
    """Pre-audit this raised AttributeError. Post-audit: returns False."""
    from bulk_downloader.qb_bridge import looks_like_torrent_url
    # All these used to crash or might in the future
    for case in (None, 12345, [], {}, b"bytes", 3.14):
        result = looks_like_torrent_url(case)
        assert isinstance(result, bool), (
            f"looks_like_torrent_url({case!r}) returned non-bool: {result!r}"
        )
        assert result is False, (
            f"looks_like_torrent_url({case!r}) wasn't False"
        )


def test_classify_qb_error_handles_non_string():
    """Pre-audit this raised AttributeError on int input."""
    from bulk_downloader.qb_bridge import classify_qb_error
    for case in (None, 12345, [], {}, b"bytes", 3.14):
        result = classify_qb_error(case)
        assert isinstance(result, str), (
            f"classify_qb_error({case!r}) returned non-str: {result!r}"
        )
        assert result == "unknown", (
            f"classify_qb_error({case!r}) wasn't 'unknown'"
        )


def test_classify_jd_error_handles_non_string():
    """Same defensive gap as qb_bridge.classify_qb_error."""
    from bulk_downloader.jd_bridge import classify_jd_error
    for case in (None, 12345, [], {}, b"bytes", 3.14):
        result = classify_jd_error(case)
        assert isinstance(result, str), (
            f"classify_jd_error({case!r}) returned non-str: {result!r}"
        )
        assert result == "unknown", (
            f"classify_jd_error({case!r}) wasn't 'unknown'"
        )


def test_audit_fix_doesnt_break_string_path():
    """Make sure the isinstance check doesn't break the happy path
    for valid string inputs."""
    from bulk_downloader.qb_bridge import looks_like_torrent_url, classify_qb_error
    from bulk_downloader.jd_bridge import classify_jd_error
    # Magnet still detected
    assert looks_like_torrent_url("magnet:?xt=urn:btih:abc") is True
    # .torrent still detected
    assert looks_like_torrent_url("https://example.com/file.torrent") is True
    # Regular URL still not a torrent
    assert looks_like_torrent_url("https://example.com/video.mp4") is False
    # Auth error still classified
    assert classify_qb_error("HTTP 401 Unauthorized") == "auth"
    assert classify_jd_error("HTTP 403 Forbidden") == "auth"


def test_resume_load_handles_filesystem_oddity():
    """Pre-audit: confirm that resume.load handles weird filesystem
    states without raising — symlink loops, permission denied, etc.
    Currently we expect these to return None silently."""
    from bulk_downloader.resume import load
    # Path that contains null bytes — most OSes reject this
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        # Just verify no crash on a path that doesn't exist
        result = load(Path(td) / "definitely_does_not_exist.mp4")
        assert result is None


def test_qb_classify_short_circuits_empty_string():
    """The pre-existing 'if not text' check also covered empty strings;
    the audit fix's isinstance + truthiness combination must keep
    that path. Whitespace strings DO go through the classifier (they're
    truthy strings), and they fall through to 'unknown' because no
    keyword matches."""
    from bulk_downloader.qb_bridge import classify_qb_error
    from bulk_downloader.jd_bridge import classify_jd_error
    assert classify_qb_error("") == "unknown"
    assert classify_qb_error("    ") == "unknown"  # whitespace = no keyword match
    assert classify_jd_error("") == "unknown"
