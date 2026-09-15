"""Row 722 (ultrafilms, 2026-09-15): a 9.9 MB photo set saved as
`slow-living_kuromi_1000px.zip` was marked failed -- "Saved but failed
integrity check (ffprobe rc=1)" -- and moved to _failed/. The post-download
integrity check ran ffprobe on every saved file; ffprobe cannot validate a zip.

`bulk_downloader/integrity.py::verify_media_integrity` must be format-aware:
zip -> zipfile.testzip(), images -> magic header, video/audio -> ffprobe
(unchanged). Fixture files only; nothing live is touched.
"""
import io
import subprocess
import zipfile

import pytest

BD_GATE_SCOPE = "module"

from bulk_downloader import integrity


def _fake_ffprobe_ok(calls):
    def run(cmd, *a, **kw):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(
            cmd, 0, stdout=b'{"streams":[{"codec_type":"video"}]}', stderr=b"")
    return run


def test_a_valid_zip_is_not_failed_by_ffprobe(tmp_path, monkeypatch):
    """RED on the pre-fix code: ffprobe rc=1 on a zip -> quarantined photo set."""
    zpath = tmp_path / "slow-living_kuromi_1000px.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(5):
            zf.writestr(f"photo_{i:03d}.jpg", b"\xff\xd8\xff" + bytes(2048))
    # Make ffprobe "present" and honest: it rejects a zip with rc=1, as live.
    monkeypatch.setattr(integrity, "_FFPROBE", "/usr/bin/ffprobe")
    monkeypatch.setattr(integrity.subprocess, "run", lambda cmd, *a, **kw:
                        subprocess.CompletedProcess(cmd, 1, stdout=b"",
                                                    stderr=b"Invalid data found"))
    ok, reason = integrity.verify_media_integrity(zpath)
    assert ok, (
        "verify_media_integrity ran ffprobe on a .zip and failed it "
        f"({reason!r}). A zip is not a video; ffprobe cannot validate it. "
        "This is the live ultrafilms defect: a valid 9.9 MB photo set was "
        "quarantined to _failed/. Route .zip through zipfile.testzip() instead.")
    assert "ffprobe" not in reason


def test_a_corrupt_zip_is_still_failed(tmp_path, monkeypatch):
    """Negative control: format-awareness is not a free pass for zips."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("photo_000.jpg", b"\xff\xd8\xff" + bytes(4096))
    data = buf.getvalue()
    zpath = tmp_path / "truncated.zip"
    zpath.write_bytes(data[: len(data) // 2])
    monkeypatch.setattr(integrity, "_FFPROBE", "/usr/bin/ffprobe")
    monkeypatch.setattr(integrity.subprocess, "run", _fake_ffprobe_ok([]))
    ok, reason = integrity.verify_media_integrity(zpath)
    assert not ok, "a truncated .zip passed the integrity check"
    assert "zip" in reason.lower()


def test_a_zip_with_a_bad_member_crc_is_failed(tmp_path, monkeypatch):
    """Negative control for testzip(): a central directory that opens but a
    member whose bytes were corrupted in place must fail (testzip != None)."""
    zpath = tmp_path / "bitrot.zip"
    payload = b"photo bytes " * 512
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("photo_000.jpg", payload)
    raw = bytearray(zpath.read_bytes())
    off = raw.find(payload) + 100
    raw[off] ^= 0xFF
    zpath.write_bytes(bytes(raw))
    monkeypatch.setattr(integrity, "_FFPROBE", "/usr/bin/ffprobe")
    monkeypatch.setattr(integrity.subprocess, "run", _fake_ffprobe_ok([]))
    ok, reason = integrity.verify_media_integrity(zpath)
    assert not ok, "a .zip with a corrupt member CRC passed the integrity check"
    assert "corrupt" in reason.lower()


def test_a_video_path_still_goes_through_ffprobe(tmp_path, monkeypatch):
    """The video path is NOT weakened: an .mp4 must still invoke ffprobe."""
    vpath = tmp_path / "clip.mp4"
    vpath.write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes(64))
    calls = []
    monkeypatch.setattr(integrity, "_FFPROBE", "/usr/bin/ffprobe")
    monkeypatch.setattr(integrity.subprocess, "run", _fake_ffprobe_ok(calls))
    ok, reason = integrity.verify_media_integrity(vpath)
    assert calls and calls[0][0] == "/usr/bin/ffprobe" and str(vpath) in calls[0], (
        "verify_media_integrity did not run ffprobe on an .mp4; the video "
        "integrity path was weakened by the format-aware dispatch")
    assert ok and reason == ""


@pytest.mark.parametrize("name,head,expect_ok", [
    ("a.jpg", b"\xff\xd8\xff\xe0" + bytes(32), True),
    ("a.png", b"\x89PNG\r\n\x1a\n" + bytes(32), True),
    ("a.gif", b"GIF89a" + bytes(32), True),
    ("a.webp", b"RIFF\x10\x00\x00\x00WEBPVP8 " + bytes(32), True),
    ("a.jpg", b"", False),                       # empty
    ("a.png", b"\xff\xd8\xff\xe0" + bytes(32), False),  # header/extension mismatch
])
def test_images_pass_on_magic_header_without_ffprobe(tmp_path, monkeypatch,
                                                     name, head, expect_ok):
    ipath = tmp_path / name
    ipath.write_bytes(head)
    calls = []
    monkeypatch.setattr(integrity, "_FFPROBE", "/usr/bin/ffprobe")
    monkeypatch.setattr(integrity.subprocess, "run", _fake_ffprobe_ok(calls))
    ok, _ = integrity.verify_media_integrity(ipath)
    assert ok is expect_ok
    assert not calls, "ffprobe was run on a still image"
