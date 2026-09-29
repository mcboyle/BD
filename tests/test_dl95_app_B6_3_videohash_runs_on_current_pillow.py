"""dl95-app-B6-3: perceptual-hash dedup works on the installed Pillow.

test2 journal: 27x "[dedup_skip] ... hash skipped: AttributeError: module
'PIL.Image' has no attribute 'ANTIALIAS'" since 2026-09-28T20:00Z. videohash
2.1.9 (the newest release inside requirements' videohash>=2.1,<3.0) calls
frame.thumbnail(..., Image.ANTIALIAS) in collagemaker.py; Pillow 10 removed that
alias and requirements leave Pillow unpinned (test2 runs 12.3.0). So every
finished download's pHash failed, the registry stayed empty, and dedup never
matched anything.

Real stack end to end: real ffmpeg generates the videos at test time, the real
videohash + Pillow hash them, and the real IntegrityMixin worker registers them.
"""

import shutil
import subprocess

import pytest

BD_GATE_SCOPE = "module"


def _video(path, source):
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg, "ffmpeg is a runtime dependency of dedup; the host must provide it"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"{source}=size=320x240:rate=10",
            "-t",
            "4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    return str(path)


def test_installed_stack_is_the_failing_combination():
    # Positive control for the premise: this videohash still names the removed
    # alias, so the fix must work WITH it, not merely on an older Pillow.
    import importlib.util
    from pathlib import Path

    spec = importlib.util.find_spec("videohash")
    assert spec and spec.origin, "videohash is a declared requirement"
    src = (Path(spec.origin).parent / "collagemaker.py").read_text()
    assert "Image.ANTIALIAS" in src


def test_compute_hash_succeeds_on_a_real_video(tmp_path):
    from bulk_downloader import dedup

    a = _video(tmp_path / "a.mp4", "testsrc")
    b = _video(tmp_path / "b.mp4", "smptebars")
    ra, rb = dedup.compute_hash(a), dedup.compute_hash(b)
    assert ra.ok, f"DL95_APP_B6_3_HASH_SKIPPED error={ra.error}"
    assert rb.ok, f"DL95_APP_B6_3_HASH_SKIPPED error={rb.error}"
    assert len(ra.hash_hex) == 16 and int(ra.hash_hex, 16)
    assert ra.hash_hex != rb.hash_hex, "different videos must not share a pHash"
    assert ra.duration_sec and ra.duration_sec > 3


def test_finished_download_is_registered_not_skipped(tmp_path):
    from bulk_downloader import dedup
    from bulk_downloader.runner_integrity import IntegrityMixin

    events = []

    class _Site(IntegrityMixin):
        def __init__(self):
            self.site_id = "b6-3"
            self.config = {"dedup_db_path": str(tmp_path / "video_hashes.db")}

        def log_event(self, kind, message, **extra):
            events.append((kind, message))

    first = _video(tmp_path / "scene.mp4", "testsrc")
    copy = tmp_path / "scene-copy.mp4"
    shutil.copyfile(first, copy)
    site = _Site()
    site._dedup_hash_worker(first, "https://example.test/scene")
    site._dedup_hash_worker(str(copy), "https://example.test/scene-again")
    skips = [m for k, m in events if k == "dedup_skip"]
    assert not skips, f"DL95_APP_B6_3_DEDUP_SKIP {skips}"
    reg = dedup.get_default_registry(str(tmp_path / "video_hashes.db"))
    assert reg.find_duplicates(
        dedup.compute_hash(first).hash_hex, distance=0, exclude_path=first
    ), "the copy was not matched"
    assert any(k == "dedup_match" for k, _ in events), events


def test_pillow_alias_is_the_documented_equivalent():
    from PIL import Image

    from bulk_downloader import dedup

    assert dedup.is_videohash_available()
    # ANTIALIAS was always LANCZOS (Pillow 2.7-9.x); the compat alias must not
    # change any resampling result for code that already used the new name.
    assert Image.ANTIALIAS == Image.Resampling.LANCZOS


@pytest.mark.parametrize("attr", ["NEAREST", "BILINEAR", "BICUBIC", "LANCZOS"])
def test_existing_pillow_names_untouched(attr):
    from PIL import Image

    from bulk_downloader import dedup

    dedup.is_videohash_available()
    assert getattr(Image, attr) == getattr(Image.Resampling, attr)
