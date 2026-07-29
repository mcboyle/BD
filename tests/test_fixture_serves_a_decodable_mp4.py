"""The fixture's .mp4 is not a video, so no seeded download can ever complete.

THE DEFECT, measured on the box 2026-07-29 on the first run where a seeded job
actually reached the network:

    /scene/2?bdseed=1 -> "status": "failed",
      "message": "MP4 file is incomplete (no moov atom). Download interrupted;
       retrying.",  "filename": "scene_002.mp4"

BD is right. `tools/fixture_site.py::_make_mp4` emitted exactly two boxes --
`ftyp` (28 bytes) and `free` (padding) -- with no `moov` and no `mdat`. That is
a container header and filler: no movie metadata, no media data, nothing to
decode. Parsing the fixture's own output confirmed it, and so did ffprobe:

    top-level atoms: [('ftyp', 28), ('free', 8164)]
    has moov: False | has mdat: False
    ffprobe -> rc=1, "moov atom not found"

`bulk_downloader/integrity.py::verify_media_integrity` shells out to ffprobe and
returned:

    (False, "ffprobe rc=1: ... moov atom not found")

which `friendly_error.py:106` translates into the message the box printed. So
L11 (end-to-end-small-download) could never pass against this fixture, and L14
could not either, since it needs a completed download to dedup against.

WHY A REAL ENCODED FILE RATHER THAN A HAND-BUILT ATOM TREE. A hand-rolled `moov`
would introduce a third state: bytes that look right to whoever wrote them and
that some ffprobe build rejects. The bytes embedded here were produced by ffmpeg
and accepted by BD's own validator, so the file is valid by the same standard
that judges it, not by my reading of a spec.

WHY IT STAYS SMALL AND PADDED. Per-scene size variation is part of the fixture's
contract (Content-Length differs per scene, and the range/partial routes slice
it). A trailing `free` box is skippable padding, so padding to any target size
leaves the file decodable -- verified at 4096, 6144, 8192 and 12288 bytes, all
integrity True.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="module")
def fixture_mod():
    try:
        from tools import fixture_site
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.fail(f"tools.fixture_site did not import, so this gate cannot "
                    f"verify its subject: {exc}")
    return fixture_site


def _atoms(data: bytes) -> list[tuple[str, int]]:
    off, out = 0, []
    while off + 8 <= len(data):
        size = struct.unpack(">I", data[off:off + 8][:4])[0]
        out.append((data[off + 4:off + 8].decode("ascii", "replace"), size))
        if size < 8:
            break
        off += size
    return out


# ── denominator canary ───────────────────────────────────────────────────────

def test_the_fixture_still_produces_media_bytes(fixture_mod):
    """No bytes means every assertion below is vacuous."""
    data = fixture_mod._mp4_for(2)
    assert data, "_mp4_for produced nothing; the checks below would be vacuous"


# ── the defect ───────────────────────────────────────────────────────────────

def test_the_media_has_a_moov_atom(fixture_mod):
    """Without moov there is no movie metadata and ffprobe refuses the file."""
    kinds = [k for k, _ in _atoms(fixture_mod._mp4_for(2))]
    assert "moov" in kinds, (
        f"the fixture's .mp4 has no moov atom (top-level atoms: {kinds}). "
        f"ffprobe reports 'moov atom not found', BD's integrity check fails the "
        f"job, and L11/L14 can never pass no matter how well the pipeline works."
    )


def test_the_media_has_actual_media_data(fixture_mod):
    """moov without mdat is metadata describing nothing."""
    kinds = [k for k, _ in _atoms(fixture_mod._mp4_for(2))]
    assert "mdat" in kinds, (
        f"the fixture's .mp4 carries no mdat atom (top-level atoms: {kinds}), "
        f"so there are no media samples to decode."
    )


def test_every_scene_produces_a_decodable_file(fixture_mod):
    """Judged by BD's own validator, not by reading the byte layout.

    Skips rather than lies when ffprobe is absent: integrity.py fails open in
    that case ((True, 'ffprobe not installed')), so a pass here would say
    nothing about the bytes.
    """
    integrity = pytest.importorskip("bulk_downloader.integrity")
    if not integrity._ffprobe():
        pytest.skip("ffprobe not installed; verify_media_integrity fails open, "
                    "so it cannot judge these bytes here")
    bad = []
    for sid in range(4):
        data = fixture_mod._mp4_for(sid)
        tmp = Path("/tmp") / f"bd_fixture_probe_{sid}.mp4"
        tmp.write_bytes(data)
        try:
            ok, why = integrity.verify_media_integrity(str(tmp))
            if not ok:
                bad.append(f"scene {sid} ({len(data)}B): {why[:80]}")
        finally:
            tmp.unlink(missing_ok=True)
    assert not bad, (
        "the fixture serves media BD's own integrity check rejects:\n  "
        + "\n  ".join(bad)
    )


def test_scenes_still_differ_in_size(fixture_mod):
    """Per-scene size variation is part of the fixture's contract.

    Content-Length differs per scene and the range routes slice these bytes, so
    collapsing every scene to one identical blob would quietly change what the
    partial-content paths are exercising.
    """
    sizes = {len(fixture_mod._mp4_for(sid)) for sid in range(7)}
    assert len(sizes) > 1, (
        f"every scene now serves the same number of bytes ({sizes}). The "
        f"fixture's size variation is load-bearing for the range/partial routes."
    )
