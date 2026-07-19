"""Tests for _check_video_magic_bytes (v3.43.0).

Used by teach Test Download to confirm the URL resolves to real video
bytes before committing the selectors. Conservative detector — only
reports a hit on actual container magic, returns "unknown" otherwise.
"""
from bulk_downloader.runner import _check_video_magic_bytes


def test_empty_returns_unknown():
    assert _check_video_magic_bytes(b"") == "unknown"


def test_too_short_returns_unknown():
    assert _check_video_magic_bytes(b"\x00" * 4) == "unknown"
    assert _check_video_magic_bytes(b"\x00" * 15) == "unknown"


def test_mp4_ftyp_detected():
    """Real MP4 starts with size box then 'ftyp'. Bytes 4-8 == 'ftyp'."""
    data = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
    assert _check_video_magic_bytes(data) == "mp4"


def test_mp4_fragment_styp():
    """Fragmented MP4 starts with 'styp' instead of 'ftyp'."""
    data = b"\x00\x00\x00\x18stypmsdh\x00\x00\x00\x00msdhmsix" + b"\x00" * 32
    assert _check_video_magic_bytes(data) == "mp4"


def test_mp4_moof_fragment():
    """MP4 fragment may start with 'moof' box."""
    data = b"\x00\x00\x00\x18moof\x00\x00\x00\x10mfhd\x00\x00\x00\x00" + b"\x00" * 32
    assert _check_video_magic_bytes(data) == "mp4"


def test_mov_quicktime():
    """QuickTime MOV uses 'ftyp' + 'qt  ' subtype."""
    data = b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00qt  " + b"\x00" * 32
    assert _check_video_magic_bytes(data) == "mov"


def test_webm_detected():
    """WebM starts with EBML magic AND 'webm' doctype appears in header."""
    data = b"\x1A\x45\xDF\xA3\x01\x00\x00\x00\x00\x00\x00\x1F" \
           b"\x42\x86\x81\x01\x42\xF7\x81\x01\x42\xF2\x81\x04" \
           b"\x42\xF3\x81\x08\x42\x82\x84webm" + b"\x00" * 32
    assert _check_video_magic_bytes(data) == "webm"


def test_mkv_detected():
    """Plain MKV has EBML magic but doctype is 'matroska', not 'webm'."""
    data = b"\x1A\x45\xDF\xA3\x01\x00\x00\x00\x00\x00\x00\x1F" \
           b"\x42\x86\x81\x01\x42\xF7\x81\x01\x42\xF2\x81\x04" \
           b"\x42\xF3\x81\x08\x42\x82\x88matroska" + b"\x00" * 32
    assert _check_video_magic_bytes(data) == "mkv"


def test_flv_detected():
    data = b"FLV\x01\x05\x00\x00\x00\x09" + b"\x00" * 32
    assert _check_video_magic_bytes(data) == "flv"


def test_avi_detected():
    """AVI is a RIFF container with 'AVI ' fourCC at byte 8."""
    data = b"RIFF\x00\x00\x00\x00AVI LIST\x00\x00\x00\x00hdrl" + b"\x00" * 32
    assert _check_video_magic_bytes(data) == "avi"


def test_riff_not_avi_returns_unknown():
    """RIFF with non-AVI fourCC (e.g. WAVE audio) should not detect as video."""
    data = b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 32
    assert _check_video_magic_bytes(data) == "unknown"


def test_mpegts_detected():
    """MPEG-TS has 0x47 sync byte every 188 bytes."""
    data = bytearray(b"\x00" * 400)
    data[0] = 0x47
    data[188] = 0x47
    data[376] = 0x47
    assert _check_video_magic_bytes(bytes(data)) == "mpegts"


def test_mpegts_single_byte_not_enough():
    """One 0x47 byte at the start is not enough — needs the pattern."""
    data = b"\x47" + b"\x00" * 400
    assert _check_video_magic_bytes(data) == "unknown"


def test_html_error_page_returns_unknown():
    """A typical error response is HTML — must NOT be detected as video."""
    data = b"<!DOCTYPE html>\n<html><head><title>403</title></head><body>" \
           b"Forbidden</body></html>"
    assert _check_video_magic_bytes(data) == "unknown"


def test_json_error_returns_unknown():
    data = b'{"error": "expired token", "code": 403}\n\n'
    assert _check_video_magic_bytes(data + b"\x00" * 16) == "unknown"


def test_random_bytes_return_unknown():
    """Random bytes should not match anything."""
    data = bytes(range(256)) * 8
    assert _check_video_magic_bytes(data) == "unknown"
