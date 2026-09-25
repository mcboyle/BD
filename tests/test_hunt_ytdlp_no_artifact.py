"""A fallback command must produce a file before the job is complete."""

from contextlib import nullcontext
from types import SimpleNamespace

from bulk_downloader import runner_extractors as rx
from bulk_downloader import ytdlp_updater

BD_GATE_SCOPE = "module"


def test_ytdlp_zero_exit_requires_output_file(monkeypatch, tmp_path):
    monkeypatch.setattr(ytdlp_updater, "resolve_ytdlp_argv", lambda: ("yt-dlp",))
    monkeypatch.setattr(rx.netns_isolation, "capture_netns", lambda *a: nullcontext(None))
    stdout = [""]
    monkeypatch.setattr(
        rx.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=stdout[0], stderr=""),
    )
    runner = SimpleNamespace(
        config={"use_ytdlp_fallback": True, "download_dir": str(tmp_path)},
        site_id="test",
        _download_proxy_url=lambda: None,
        log_event=lambda *a, **k: None,
    )
    url = "https://example.org/video"
    output = tmp_path / "video.mp4"
    output.write_bytes(b"abcd")
    stdout[0] = f"[download] Destination: {output}"
    assert rx.ExtractorsMixin._try_ytdlp_fallback(runner, url) == (
        True, "Downloaded via yt-dlp fallback", str(output), 4, 4
    )

    stdout[0] = "[download] Nothing to do"
    result = rx.ExtractorsMixin._try_ytdlp_fallback(runner, url)
    assert result[0] is False
    assert result[2:] == (None, 0, 0)

    output.unlink()
    stdout[0] = f"[download] Destination: {output}"
    result = rx.ExtractorsMixin._try_ytdlp_fallback(runner, url)
    assert result[0] is False


def test_ytdlp_merged_download_reports_the_merged_file(monkeypatch, tmp_path):
    monkeypatch.setattr(ytdlp_updater, "resolve_ytdlp_argv", lambda: ("yt-dlp",))
    monkeypatch.setattr(rx.netns_isolation, "capture_netns", lambda *a: nullcontext(None))
    merged = tmp_path / "clip-abc.mp4"
    merged.write_bytes(b"abcdef")
    # yt-dlp deletes the format parts after merging; only the merged file exists.
    stdout = "\n".join([
        f"[download] Destination: {tmp_path / 'clip-abc.f137.mp4'}",
        f"[download] Destination: {tmp_path / 'clip-abc.f140.m4a'}",
        f'[Merger] Merging formats into "{merged}"',
        f"Deleting original file {tmp_path / 'clip-abc.f137.mp4'} (pass -k to keep)",
    ])
    monkeypatch.setattr(
        rx.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=stdout, stderr=""),
    )
    runner = SimpleNamespace(
        config={"use_ytdlp_fallback": True, "download_dir": str(tmp_path)},
        site_id="test",
        _download_proxy_url=lambda: None,
        log_event=lambda *a, **k: None,
    )
    assert rx.ExtractorsMixin._try_ytdlp_fallback(runner, "https://example.org/v") == (
        True, "Downloaded via yt-dlp fallback", str(merged), 6, 6
    )
