"""The fixture's HLS segments are sync bytes and zeros, so ffmpeg refuses them.

CUT C of #19, and the piece that actually closes it. #72 stopped a manifest being
recorded as a finished download; #75 routed a scraped manifest to the segmented
downloader instead of clicking it and waiting 60s. With both of those correct the
seeded HLS URL STILL does not download, and this is why:

    ffmpeg: Error when loading first segment
            'http://127.0.0.1:8899/hls/seg/2_0.ts'
            Invalid data found when processing input
    _stream_route -> correct url + name
    hls_downloader.download -> ok=False error='ffmpeg_failed' bytes_written=0
    no file on disk

`fixture_site.py` serves every segment as

    (b"\\x47" + b"\\x00" * 187) * 16

-- 3008 bytes of TS sync bytes followed by zeros. It has the shape of a transport
stream and none of the substance: no PAT, no PMT, no PES headers, no codec data.
ffmpeg is right to reject it.

THIS IS THE EXACT TWIN OF TASK #8, and that history is why this file exists
rather than a one-line byte swap. #8 was: the fixture emitted `ftyp` + `free` --
a container header and padding, no moov, no mdat -- so every seeded download
failed with "MP4 file is incomplete (no moov atom)" and L11 and L14 could never
pass however healthy the pipeline was. Synthetic bytes shaped like a container
that nothing can decode, in both cases.

#8's fix is the precedent followed here, including its reasoning:

    "These bytes are valid by the same standard that judges them: ffmpeg produced
     them and BD's own verify_media_integrity returns (True, '') for them. A
     hand-built moov would only be valid by someone's reading of the spec."

So the segment is REAL: produced by ffmpeg, embedded as base64 so the fixture
needs no ffmpeg at runtime and every host serves byte-identical media -- the same
three properties #8's comment claims for the MP4.

MEASURED before writing any of this:

    segment                     1316 bytes (one black 64x64 H.264 frame)
    4-segment playlist -> mux   3498 bytes, head '\\0\\0\\0 f t y p i s o m'
    ffprobe on the result       h264,64,64
    verify_media_integrity      (True, '')

KEYFRAME-ONLY, deliberately. A real HLS segment has to be independently
decodable -- a player joining mid-stream starts at a segment boundary -- so the
encode pins keyint=1. A segment that only decodes as a continuation of its
predecessor would work for a whole-playlist mux and fail any partial fetch, which
is the subtler version of the same defect.

RED-first: every assertion below fails on pristine source.
"""
from __future__ import annotations

import base64
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_FIXTURE = ROOT / "tools" / "fixture_site.py"


def _ffmpeg():
    from bulk_downloader import ffmpeg_bin
    return ffmpeg_bin.ffmpeg()


def _segment_bytes():
    """The bytes the fixture would serve for one segment, without HTTP."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_fx", _FIXTURE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "_make_ts_segment"), (
        "tools/fixture_site.py has no _make_ts_segment(). The segments are still "
        "built inline as (b'\\x47' + b'\\x00'*187)*16, which is sync bytes and "
        "zeros -- shaped like a transport stream, decodable by nothing.")
    return mod._make_ts_segment()


# ── the bytes themselves ─────────────────────────────────────────────────────

def test_the_segment_is_not_sync_bytes_and_zeros():
    """THE DEFECT, at its narrowest.

    A run of 0x47 every 188 bytes with nothing between them is what the fixture
    served. 0x47 IS the TS sync byte, so the data passes a shape check and fails
    every decoder.
    """
    seg = _segment_bytes()
    assert seg[:1] == b"\x47", "a transport stream still has to start with 0x47"
    naive = (b"\x47" + b"\x00" * 187) * 16
    assert seg != naive, (
        "the segment is still the synthetic pattern -- sync bytes and zeros")
    # Substance, not shape: a real segment carries PAT/PMT and PES payload, so it
    # cannot be almost entirely zero.
    zeros = seg.count(0)
    assert zeros < len(seg) * 0.9, (
        f"{zeros} of {len(seg)} bytes are zero. A stream with no PAT, no PMT and "
        f"no codec data has the shape of media and none of the substance.")


def test_the_segment_is_a_whole_number_of_ts_packets():
    """188 bytes each, and every packet starts with the sync byte. A truncated
    final packet is a real-world source of decoder failures."""
    seg = _segment_bytes()
    assert len(seg) % 188 == 0, (
        f"{len(seg)} bytes is not a whole number of 188-byte TS packets")
    bad = [i for i in range(0, len(seg), 188) if seg[i] != 0x47]
    assert not bad, f"packets at byte offsets {bad[:5]} do not start with 0x47"


def test_the_segment_is_embedded_not_generated_at_runtime():
    """#8's property, and it is load-bearing for the fixture's job: no ffmpeg
    dependency at runtime, and every host serves byte-identical media. A fixture
    that shells out to ffmpeg to build its own media would make the bytes -- and
    therefore Content-Length, and therefore the /range routes -- vary by host.
    """
    src = _FIXTURE.read_text(encoding="utf-8")
    assert "_REAL_TS_B64" in src, (
        "no embedded segment constant; the fixture must carry real bytes the way "
        "_REAL_MP4_B64 does")
    # Checked on the SEGMENT BUILDER specifically, not the whole module: the
    # fixture legitimately imports subprocess for unrelated routes.
    import ast
    fn = next((n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.FunctionDef) and n.name == "_make_ts_segment"),
              None)
    assert fn is not None, "_make_ts_segment not found"
    body = ast.unparse(fn)
    # STRUCTURAL: no subprocess, no import of one. The first draft asserted
    # `"ffmpeg" not in body.lower()`, which a mutation walked straight through --
    # ast.unparse drops `#` comments, and an `import subprocess` line contains no
    # occurrence of the word "ffmpeg" at all. The property is "spawns nothing",
    # and that is a Call and an Import, not a word.
    spawned = [ast.unparse(n) for n in ast.walk(fn)
               if isinstance(n, ast.Call)
               and any(getattr(x, "attr", getattr(x, "id", "")) in
                       ("run", "Popen", "check_output", "call", "check_call",
                        "system")
                       for x in [n.func])]
    assert not spawned, (
        f"_make_ts_segment spawns a process: {spawned}. The served bytes would "
        f"then depend on the host's encoder version, so Content-Length -- and "
        f"the /range routes that slice these bytes -- would vary by host.")
    imports = [ast.unparse(n) for n in ast.walk(fn)
               if isinstance(n, (ast.Import, ast.ImportFrom))
               and "subprocess" in ast.unparse(n)]
    assert not imports, (
        f"_make_ts_segment imports subprocess: {imports}. Nothing in a builder "
        f"that decodes a constant needs one.")
    assert "b64decode" in body, (
        "_make_ts_segment does not decode embedded bytes")


# ── the standard that judges them ────────────────────────────────────────────

@pytest.mark.skipif(not _ffmpeg(), reason="ffmpeg not on PATH")
def test_a_playlist_of_these_segments_muxes_to_a_valid_mp4():
    """VALID BY THE SAME STANDARD THAT JUDGES IT -- #8's phrase.

    Not "ffmpeg accepts the segment" but "the whole path works": four segments,
    a playlist, `-c copy` into MP4, and then BD's OWN verify_media_integrity over
    the result. That is what the runner will do and what will decide the verdict.
    """
    from bulk_downloader import integrity
    seg = _segment_bytes()
    with tempfile.TemporaryDirectory() as d:
        dd = Path(d)
        for i in range(4):
            (dd / f"s{i}.ts").write_bytes(seg)
        lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:1",
                 "#EXT-X-MEDIA-SEQUENCE:0"]
        for i in range(4):
            lines += ["#EXTINF:1.0,", f"s{i}.ts"]
        lines.append("#EXT-X-ENDLIST")
        (dd / "p.m3u8").write_text("\n".join(lines) + "\n")
        out = dd / "out.mp4"
        r = subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error",
                            "-i", str(dd / "p.m3u8"), "-c", "copy",
                            "-f", "mp4", str(out), "-y"],
                           capture_output=True, text=True)
        assert r.returncode == 0, (
            f"ffmpeg refused a playlist of the fixture's own segments "
            f"(exit {r.returncode}): {r.stderr[:300]}")
        assert out.exists() and out.stat().st_size > 0, "no output produced"
        head = out.read_bytes()[:12]
        assert head[4:8] == b"ftyp", f"output is not an MP4 container: {head!r}"
        ok, why = integrity.verify_media_integrity(str(out))
        assert ok, (
            f"BD's own verify_media_integrity rejects the muxed result: {why!r}. "
            f"That is the check that decides the runner's verdict, so a segment "
            f"ffmpeg tolerates but BD rejects would still fail every download.")


@pytest.mark.skipif(not _ffmpeg(), reason="ffmpeg not on PATH")
def test_a_single_segment_decodes_on_its_own():
    """KEYFRAME-ONLY. A real HLS segment must be independently decodable -- a
    player joining mid-stream starts at a segment boundary. A segment that only
    decodes as a continuation of its predecessor would pass the whole-playlist
    mux above and fail any partial fetch.
    """
    seg = _segment_bytes()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "one.ts"
        p.write_bytes(seg)
        r = subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error",
                            "-i", str(p), "-frames:v", "1",
                            "-f", "null", "-"],
                           capture_output=True, text=True)
        assert r.returncode == 0, (
            f"one segment alone does not decode (exit {r.returncode}): "
            f"{r.stderr[:300]}")


# ── the served surface ───────────────────────────────────────────────────────

def test_every_segment_route_serves_the_real_bytes():
    """The route, not just the helper. A helper nothing calls is the shape of
    fix that leaves the box exactly as it was."""
    import ast
    src = _FIXTURE.read_text(encoding="utf-8")
    fn = next((n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.FunctionDef) and "hls_segment" in n.name),
              None)
    assert fn is not None, "the /hls/seg route handler was not found"
    body = ast.unparse(fn)
    assert "_make_ts_segment" in body, (
        f"the segment route does not serve _make_ts_segment():\n{body[:300]}")
    naive = '\\x47'
    assert naive not in body, (
        f"the route still builds the synthetic pattern inline:\n{body[:300]}")


def test_the_content_type_is_still_mp2t():
    """Unchanged, and asserted so the fix does not quietly alter the surface the
    manifest promises."""
    import ast
    src = _FIXTURE.read_text(encoding="utf-8")
    fn = next((n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.FunctionDef) and "hls_segment" in n.name),
              None)
    assert "video/mp2t" in ast.unparse(fn), (
        "the segment route no longer declares video/mp2t")
