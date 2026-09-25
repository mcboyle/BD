BD_GATE_SCOPE = "module"

"""Signed HLS manifests must reach the HLS downloader."""

from types import SimpleNamespace

import pytest

from bulk_downloader import extractors


@pytest.mark.parametrize("adapter", ["phub", "eaf"])
@pytest.mark.parametrize(
    ("media_url", "is_hls"),
    [
        ("https://cdn.example/master.m3u8", True),
        ("https://cdn.example/master.m3u8?token=abc", True),
        ("https://cdn.example/video.mp4?token=abc", False),
    ],
)
def test_library_result_routes_signed_manifest(monkeypatch, adapter, media_url, is_hls):
    if adapter == "phub":
        library = SimpleNamespace(
            Video=lambda _url: SimpleNamespace(get_direct_url=lambda: media_url)
        )
        monkeypatch.setattr(extractors, "_try_import", lambda _name: library)
        result = extractors._phub_adapter(
            "https://www.pornhub.com/view_video.php?viewkey=x", ["best"], None
        )
    else:
        video = SimpleNamespace(direct_download_url=media_url)
        library = SimpleNamespace(
            Client=lambda: SimpleNamespace(get_video=lambda _url: video)
        )
        monkeypatch.setattr(extractors, "_try_import", lambda _name: library)
        result = extractors._generic_eaf_adapter(
            "xnxx_api", "https://www.xnxx.com/video-x", ["best"], None
        )

    assert result.ok, result.error
    assert result.file_url == media_url
    assert result.is_hls is is_hls
