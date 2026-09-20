"""Row 851 — loudnorm planning stays pure until the runner hook submits it."""

import importlib.util
import subprocess
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from bulk_downloader import audio_normalize


BD_GATE_SCOPE = "module"


def test_audio_normalize_module_exists():
    assert importlib.util.find_spec("bulk_downloader.audio_normalize") is not None


def test_audio_normalize_exposes_two_pass_command_builders():
    assert callable(getattr(audio_normalize, "build_analysis_argv", None))
    assert callable(getattr(audio_normalize, "build_apply_argv", None))
    assert callable(getattr(audio_normalize, "parse_measurements", None))


_LOUDNORM_REPORT = '''[Parsed_loudnorm_0 @ 0x1] {\n"input_i" : "-18.20",\n"input_tp" : "-1.00",\n"input_lra" : "7.10",\n"input_thresh" : "-28.50",\n"target_offset" : "0.12"\n}'''


def test_two_pass_argv_uses_minus14_lufs_and_measured_values():
    measurements = audio_normalize.parse_measurements(_LOUDNORM_REPORT)
    assert audio_normalize.build_analysis_argv("in.m4a", ffmpeg="ffmpeg") == [
        "ffmpeg", "-hide_banner", "-i", "in.m4a", "-map", "0:a:0", "-af",
        "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-",
    ]
    assert audio_normalize.build_apply_argv(
        "in.m4a", "out.m4a", measurements, ffmpeg="ffmpeg"
    ) == [
        "ffmpeg", "-y", "-i", "in.m4a", "-map", "0",
        "-c:v", "copy", "-c:s", "copy", "-c:d", "copy", "-c:t", "copy",
        "-filter:a:0",
        "loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=-18.20:measured_LRA=7.10:measured_TP=-1.00:measured_thresh=-28.50:offset=0.12:linear=true:print_format=summary",
        "out.m4a",
    ]
    two = audio_normalize.build_apply_argv("in.mkv", "out.mkv", [measurements, measurements], ffmpeg="ffmpeg")
    assert two.count("-filter:a:0") == 1 and two.count("-filter:a:1") == 1 and "-map" in two


def test_measurement_parser_rejects_incomplete_loudnorm_fixture():
    with pytest.raises(ValueError, match="missing loudnorm measurements: input_lra"):
        audio_normalize.parse_measurements('{"input_i":"-18.20"}')


def test_normalization_option_is_default_off():
    from bulk_downloader.app_kernel import CFG_FIELDS, DEFAULTS

    assert "use_audio_normalization" in CFG_FIELDS
    assert DEFAULTS["use_audio_normalization"] is False


def test_background_normalization_runs_both_passes_then_replaces_source(
    tmp_path, monkeypatch
):
    source = tmp_path / "clip.m4a"
    source.write_text("original")
    calls = []

    unrelated = tmp_path / "clip.loudnorm.m4a"
    unrelated.write_text("someone else's file")
    outputs = []

    def run(argv, **_kwargs):
        calls.append(argv)
        if argv[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="1\n", stderr="")
        if len(calls) == 2:
            return SimpleNamespace(returncode=0, stderr=_LOUDNORM_REPORT)
        out = Path(argv[-1])
        assert out.parent == tmp_path and out.name.startswith(".clip.loudnorm-") and out.suffix == ".m4a"
        outputs.append(out)
        out.write_text("normalized")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(audio_normalize.subprocess, "run", run)
    audio_normalize._normalize_file(str(source), "ffmpeg")

    assert calls == [
        audio_normalize.build_probe_argv(str(source), ffprobe="ffprobe"),
        audio_normalize.build_analysis_argv(str(source), ffmpeg="ffmpeg", audio_stream=0),
        audio_normalize.build_apply_argv(
            str(source), str(outputs[0]),
            [audio_normalize.parse_measurements(_LOUDNORM_REPORT)], ffmpeg="ffmpeg",
        ),
    ]
    assert source.read_text() == "normalized"
    assert not outputs[0].exists()
    assert unrelated.read_text() == "someone else's file"


def test_done_hook_submits_audio_normalization_when_enabled(tmp_path, monkeypatch):
    from bulk_downloader import ffmpeg_bin, runner

    source = tmp_path / "clip.m4a"
    source.write_text("audio")
    submitted = []

    monkeypatch.setattr(ffmpeg_bin, "ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(
        audio_normalize, "normalize_in_background",
        lambda path, *, ffmpeg: submitted.append((path, ffmpeg)),
    )

    class FakeRunner:
        site_id = "row851"
        config = {"download_dir": str(tmp_path), "use_audio_normalization": True}
        jobs = {"https://example.invalid/clip": {"status": "pending", "filename": "clip.m4a"}}
        _job_progress_samples = {}

        @contextmanager
        def _job_status_writer(self):
            yield lambda: None

        def log_event(self, *_args, **_kwargs):
            return None

    fake = FakeRunner()
    runner.SiteRunner._update_job_current(
        fake, "https://example.invalid/clip", "done", "complete"
    )

    assert submitted == [(str(source), "ffmpeg")]


# ---- fixer (O928) controls for the correctness REFUTE E1/E2/E3 -------------

def test_e1_failure_never_deletes_an_unrelated_loudnorm_file(tmp_path, monkeypatch):
    """E1: clip.loudnorm.m4a belongs to someone else; an analysis failure and an
    apply failure both leave it alone and leave no temp file behind."""
    source = tmp_path / "clip.m4a"
    source.write_text("original")
    unrelated = tmp_path / "clip.loudnorm.m4a"
    unrelated.write_text("keep me")

    def fail_analysis(argv, **_):
        if argv[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="1\n", stderr="")
        return SimpleNamespace(returncode=1, stderr="boom")

    monkeypatch.setattr(audio_normalize.subprocess, "run", fail_analysis)
    audio_normalize._normalize_file(str(source), "ffmpeg")
    assert unrelated.read_text() == "keep me" and source.read_text() == "original"

    def fail_apply(argv, **_):
        if argv[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="1\n", stderr="")
        if "-f" in argv:
            return SimpleNamespace(returncode=0, stderr=_LOUDNORM_REPORT)
        return SimpleNamespace(returncode=1, stderr="encode failed")

    monkeypatch.setattr(audio_normalize.subprocess, "run", fail_apply)
    audio_normalize._normalize_file(str(source), "ffmpeg")
    assert unrelated.read_text() == "keep me" and source.read_text() == "original"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["clip.loudnorm.m4a", "clip.m4a"]


def _streams(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout
    return [tuple(reversed(line.split(","))) for line in out.splitlines() if line.strip()]  # (codec_type, codec_name)


def test_e2_e3_real_two_track_mkv_keeps_every_stream_and_copies_video(tmp_path):
    """E2/E3 with the real ffmpeg: a synthetic MKV (FFV1 video + two audio tracks)
    comes out with the same stream layout, FFV1 still FFV1 (copied, not
    transcoded), and both audio tracks normalized (each measured on its own)."""
    import shutil
    assert shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe are required here: fail closed, never skip (FR46/T5)"
    source = tmp_path / "two_tracks.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
                    "-map", "0:v", "-map", "1:a", "-map", "2:a",
                    "-c:v", "ffv1", "-c:a", "pcm_s16le", str(source)], check=True, capture_output=True)
    before = _streams(source)
    assert before == [("video", "ffv1"), ("audio", "pcm_s16le"), ("audio", "pcm_s16le")]
    original_bytes = source.read_bytes()

    audio_normalize._normalize_file(str(source), "ffmpeg")

    after = _streams(source)
    assert [t for t, _ in after] == ["video", "audio", "audio"], after
    assert after[0] == ("video", "ffv1"), after
    assert source.read_bytes() != original_bytes, "normalization did not rewrite the file"
    assert [p.name for p in tmp_path.iterdir()] == ["two_tracks.mkv"]
    # each audio track was measured separately and now sits at the -14 LUFS target
    for index in range(2):
        rep = subprocess.run(audio_normalize.build_analysis_argv(str(source), ffmpeg="ffmpeg", audio_stream=index),
                             capture_output=True, text=True, check=False)
        m = audio_normalize.parse_measurements(rep.stderr)
        assert abs(float(m["input_i"]) - (-14.0)) < 1.5, (index, m)
