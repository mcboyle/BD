"""Behavior coverage for virtual-display diagnostic frame recording."""

BD_GATE_SCOPE = "module"

from pathlib import Path

try:
    from bulk_downloader.render_diagnostics import record_frames
except ImportError:
    record_frames = None


class _RecordingProcess:
    def __init__(self, output: Path):
        self.output = output
        self.terminated = False
        self.waited = False
        self.returncode = None

    def wait(self, timeout=None):
        self.waited = True
        self.output.write_bytes(b"\x89PNG\r\n\x1a\nframe")
        self.returncode = 0
        return 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15


def test_record_frames_returns_a_valid_numbered_frame(tmp_path):
    """Removing x11grab output handling would return no reviewable frame."""
    created = []

    def spawn(command):
        assert command[:4] == ["/usr/bin/ffmpeg", "-f", "x11grab", "-i"]
        process = _RecordingProcess(tmp_path / "frame-000001.png")
        created.append(process)
        return process

    assert callable(record_frames)
    frames = record_frames(
        tmp_path, frame_count=1, ffmpeg_path="/usr/bin/ffmpeg", spawn=spawn
    )

    assert frames == [tmp_path / "frame-000001.png"]
    assert frames[0].read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert created[0].waited


def test_record_frames_terminates_a_process_when_capture_fails(tmp_path):
    """Removing final cleanup would leave the diagnostic child process alive."""
    class _FailingProcess(_RecordingProcess):
        def wait(self, timeout=None):
            raise TimeoutError("capture exceeded deadline")

    process = _FailingProcess(tmp_path / "frame-000001.png")

    def spawn(command):
        return process

    assert callable(record_frames)
    assert record_frames(
        tmp_path, frame_count=1, ffmpeg_path="/usr/bin/ffmpeg", spawn=spawn
    ) == []
    assert process.terminated
