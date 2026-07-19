"""T10 (Tier 3) — D-35 magic-bytes checker + D-36 MP4 metadata
inspector. Two read-only pipeline-inspection tools in Group D. Both
read a finished file's bytes; both route the caller's path through
app._validate_path so the path_allowlist is honoured.
"""
import contextlib
import os
import struct

from bulk_downloader import dev_suite as ds


# ── helpers ────────────────────────────────────────────────────────

def _atom(name, payload=b""):
    """Build one top-level MP4 atom: 4-byte big-endian size, 4-byte
    ASCII name, payload."""
    size = 8 + len(payload)
    return struct.pack(">I", size) + name.encode("ascii") + payload


_FTYP = _atom("ftyp", b"isom\x00\x00\x02\x00isomiso2avc1mp41")
_MINIMAL_MOOV = _atom("moov", b"\x00" * 64)
_MINIMAL_MDAT = _atom("mdat", b"\x00" * 128)


def _write(path, data):
    path.write_bytes(data)
    return str(path)


@contextlib.contextmanager
def _permissive_paths():
    """Empty path_allowlist == permissive. Most tests write into a
    per-test tmp_path that is NOT under the app's first-run-seeded
    allowlist (the seed only covers the chdir, not the pytest tmpdir),
    so they need permissive mode to even reach the inspector's logic.
    Helper function (not a pytest fixture) — the custom test runner
    auto-injects only conftest fixtures, not file-local ones.
    """
    from bulk_downloader import app as bd_app
    saved = bd_app._app_cfg.get("path_allowlist")
    bd_app._app_cfg["path_allowlist"] = []
    try:
        yield
    finally:
        bd_app._app_cfg["path_allowlist"] = saved


# ── D-35 magic_bytes_check ─────────────────────────────────────────

def test_magic_bytes_detects_png(clean_workdir, tmp_path):
    with _permissive_paths():
        f = _write(tmp_path / "thumb.png",
                   b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        r = ds.magic_bytes_check(f)
    assert r["ok"] is True
    assert r["detected_label"] == "PNG image"
    assert r["container_vs_filename_mismatch"] is False
    assert "PNG" in r["verdict"]


def test_magic_bytes_detects_jpeg(clean_workdir, tmp_path):
    with _permissive_paths():
        f = _write(tmp_path / "cover.jpg",
                   b"\xff\xd8\xff\xe0" + b"\x00" * 32)
        r = ds.magic_bytes_check(f)
    assert r["detected_label"] == "JPEG image"
    assert r["container_vs_filename_mismatch"] is False


def test_magic_bytes_detects_mp4(clean_workdir, tmp_path):
    with _permissive_paths():
        f = _write(tmp_path / "video.mp4", _FTYP + _MINIMAL_MOOV)
        r = ds.magic_bytes_check(f)
    assert r["detected_label"] == "MP4 / ISO-BMFF"
    assert r["container_vs_filename_mismatch"] is False


def test_magic_bytes_flags_mismatch_webm_named_mp4(clean_workdir,
                                                     tmp_path):
    # the bytes are WebM but the filename ends .mp4 — the
    # operationally-important failure mode for this tool
    with _permissive_paths():
        f = _write(tmp_path / "video.mp4",
                   b"\x1a\x45\xdf\xa3" + b"\x00" * 60)
        r = ds.magic_bytes_check(f)
    assert r["detected_label"] == "WebM / Matroska"
    assert r["container_vs_filename_mismatch"] is True
    assert "MISMATCH" in r["verdict"]


def test_magic_bytes_disambiguates_webp_from_wav(clean_workdir,
                                                   tmp_path):
    # the RIFF prefix is shared; the chunk-type at offset 8 decides
    with _permissive_paths():
        webp = _write(tmp_path / "img.webp",
                      b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 32)
        wav = _write(tmp_path / "snd.wav",
                     b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 32)
        r_webp = ds.magic_bytes_check(webp)
        r_wav = ds.magic_bytes_check(wav)
    assert r_webp["detected_label"] == "WebP image"
    assert r_wav["detected_label"] == "WAV audio"


def test_magic_bytes_html_soft_block(clean_workdir, tmp_path):
    # a CDN returning an HTML challenge page instead of the video —
    # the magic-byte check is the fastest way to spot it
    with _permissive_paths():
        f = _write(tmp_path / "video.mp4",
                   b"<!DOCTYPE html><html><body>blocked</body></html>")
        r = ds.magic_bytes_check(f)
    assert "HTML" in r["detected_label"]
    assert r["container_vs_filename_mismatch"] is True


def test_magic_bytes_unknown_format(clean_workdir, tmp_path):
    with _permissive_paths():
        f = _write(tmp_path / "weird.bin", b"\x00" * 64)
        r = ds.magic_bytes_check(f)
    assert r["detected_label"] is None
    assert "unknown" in r["verdict"]


def test_magic_bytes_missing_path_errors(clean_workdir):
    r = ds.magic_bytes_check("")
    assert r["ok"] is False
    assert "path required" in r["error"]


def test_magic_bytes_nonexistent_path_errors(clean_workdir):
    with _permissive_paths():
        r = ds.magic_bytes_check("/tmp/does_not_exist_t10_xyz_qq77")
    assert r["ok"] is False
    assert "not found" in r["error"]


def test_magic_bytes_respects_path_allowlist(clean_workdir, tmp_path):
    # set an allowlist that EXCLUDES tmp_path — the call must fail
    # before any read, with a clear allowlist message
    from bulk_downloader import app as bd_app
    f = _write(tmp_path / "thumb.png",
               b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    saved = bd_app._app_cfg.get("path_allowlist")
    try:
        bd_app._app_cfg["path_allowlist"] = ["/no/such/root"]
        r = ds.magic_bytes_check(f)
        assert r["ok"] is False
        assert "allowlist" in r["error"]
    finally:
        bd_app._app_cfg["path_allowlist"] = saved


# ── D-36 mp4_metadata_inspect ──────────────────────────────────────

def test_mp4_inspect_well_formed(clean_workdir, tmp_path):
    with _permissive_paths():
        f = _write(tmp_path / "video.mp4",
                   _FTYP + _MINIMAL_MOOV + _MINIMAL_MDAT)
        r = ds.mp4_metadata_inspect(f)
    assert r["ok"] is True
    assert r["has_ftyp"] is True
    assert r["has_moov"] is True
    assert r["has_mdat"] is True
    assert r["mdat_before_moov"] is False
    assert r["major_brand"] == "isom"
    assert r["atom_names"] == ["ftyp", "moov", "mdat"]
    assert r["findings"] == []


def test_mp4_inspect_missing_moov_flagged(clean_workdir, tmp_path):
    # incomplete download — ftyp + mdat but moov never written
    with _permissive_paths():
        f = _write(tmp_path / "incomplete.mp4", _FTYP + _MINIMAL_MDAT)
        r = ds.mp4_metadata_inspect(f)
    assert r["has_moov"] is False
    assert any("NO MOOV" in fnd for fnd in r["findings"])
    assert "issues" in r["verdict"]


def test_mp4_inspect_mdat_before_moov_flagged(clean_workdir, tmp_path):
    # mdat before moov — web-unfriendly; needs +faststart remux
    with _permissive_paths():
        f = _write(tmp_path / "slow.mp4",
                   _FTYP + _MINIMAL_MDAT + _MINIMAL_MOOV)
        r = ds.mp4_metadata_inspect(f)
    assert r["mdat_before_moov"] is True
    assert any("mdat precedes moov" in fnd for fnd in r["findings"])


def test_mp4_inspect_rejects_non_mp4(clean_workdir, tmp_path):
    with _permissive_paths():
        f = _write(tmp_path / "not_mp4.bin", b"RANDOMDATA" * 8)
        r = ds.mp4_metadata_inspect(f)
    assert r["ok"] is False
    assert "not an MP4" in r["error"]


def test_mp4_inspect_too_small(clean_workdir, tmp_path):
    with _permissive_paths():
        f = _write(tmp_path / "tiny.mp4", b"abc")
        r = ds.mp4_metadata_inspect(f)
    assert r["ok"] is False
    assert "too small" in r["error"]


def test_mp4_inspect_respects_path_allowlist(clean_workdir, tmp_path):
    from bulk_downloader import app as bd_app
    f = _write(tmp_path / "v.mp4", _FTYP + _MINIMAL_MOOV)
    saved = bd_app._app_cfg.get("path_allowlist")
    try:
        bd_app._app_cfg["path_allowlist"] = ["/no/such/root"]
        r = ds.mp4_metadata_inspect(f)
        assert r["ok"] is False
        assert "allowlist" in r["error"]
    finally:
        bd_app._app_cfg["path_allowlist"] = saved


# ── dev routes ─────────────────────────────────────────────────────

def test_magic_bytes_route(fresh_app, tmp_path):
    with _permissive_paths():
        f = _write(tmp_path / "thumb.png",
                   b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        j = fresh_app.get(
            f"/api/dev/magic_bytes?path={f}").get_json()
    assert j["ok"] is True
    assert j["detected_label"] == "PNG image"


def test_mp4_metadata_route(fresh_app, tmp_path):
    with _permissive_paths():
        f = _write(tmp_path / "v.mp4",
                   _FTYP + _MINIMAL_MOOV + _MINIMAL_MDAT)
        j = fresh_app.get(
            f"/api/dev/mp4_metadata?path={f}").get_json()
    assert j["ok"] is True
    assert j["has_moov"] is True


def test_t10_routes_are_dev_gated():
    os.environ.pop("BD_DEV_MODE", None)
    os.environ["BD_DEV_MODE_DISABLE"] = "1"
    try:
        from bulk_downloader.app import app
        c = app.test_client()
        for ep in ("/api/dev/magic_bytes", "/api/dev/mp4_metadata"):
            assert c.get(ep + "?path=/tmp/x").status_code == 404
    finally:
        os.environ.pop("BD_DEV_MODE_DISABLE", None)
