"""695 (v3.66.695) -- route a plugin @extractor's HLS/DASH manifest through
hls_downloader (the GH-2a follow-on).

691 wired the plugin @extractor dispatch; GH-2a's mapper returns
{"video_url": <manifest>, "is_hls": True} for an HLS/DASH-only source, but the
dispatch deliberately *fell through* on is_hls (deferred). This cut routes that
manifest through the existing hls_downloader (the same ffmpeg path jsonapi/vixen
use) instead of falling through -- so a plugin extractor that yields only an HLS
master can now actually download.

RED-first on pristine v3.66.694: ``_try_plugin_extractor`` returns False on an
is_hls result WITHOUT ever calling hls_downloader.download -> the HLS tests RED
(download never invoked / returns False), while the progressive path is
unchanged (regression guard stays green).
"""
from __future__ import annotations

import threading
import types


class _FakeHls:
    """Stand-in for bulk_downloader.hls_downloader, injected via monkeypatch."""
    def __init__(self, ok=True, available=True):
        self._ok = ok
        self._available = available
        self.calls = []

    def is_available(self):
        return self._available

    def download(self, manifest_url, output_path, **kw):
        self.calls.append({"manifest_url": manifest_url,
                           "output_path": output_path, "kw": kw})
        # Simulate a written file on success so downstream size checks pass.
        if self._ok:
            try:
                open(output_path, "w").close()
            except Exception:
                pass
        return types.SimpleNamespace(
            ok=self._ok, output_path=output_path, bytes_written=(123 if self._ok else 0),
            error=("" if self._ok else "ffmpeg_failed"),
            error_detail=("" if self._ok else "ffmpeg exit 1"))


def _fake_runner(site_id, config, http_calls):
    def _ddhd(page_url, file_url, output_path, referer=""):
        http_calls.append(file_url)
        try:
            open(output_path, "w").close()
        except Exception:
            pass
        return True
    ns = types.SimpleNamespace(
        site_id=site_id, config=config,
        _do_direct_http_download=_ddhd,
        _update_job=lambda *a, **k: None,
        log_event=lambda *a, **k: None,
        _stop=threading.Event(),
    )
    # Row 439: the HLS arm now reaches hls_downloader only through the
    # fail-closed egress gate. Bind the REAL gate (and the REAL proxy
    # resolution it depends on) onto the stand-in rather than stubbing them,
    # so this test still exercises the production seam end to end -- a stub
    # here would quietly re-open the very bypass row 439 closed.
    from bulk_downloader.runner_transport import TransportMixin
    ns._download_proxy_url = TransportMixin._download_proxy_url.__get__(ns)
    ns._hls_download_guarded = TransportMixin._hls_download_guarded.__get__(ns)
    return ns


def _call(fs, url):
    from bulk_downloader.runner_extractors import ExtractorsMixin
    return ExtractorsMixin._try_plugin_extractor(fs, url)


def test_hls_manifest_routed_through_hls_downloader(tmp_path, monkeypatch):
    from bulk_downloader import plugins as P
    import bulk_downloader.hls_downloader as real_hls
    fake = _FakeHls(ok=True)
    monkeypatch.setattr(real_hls, "download", fake.download)
    monkeypatch.setattr(real_hls, "is_available", fake.is_available)
    P.register_extractor("hlssite", lambda u, ctx: {
        "video_url": "https://cdn/master.m3u8", "is_hls": True, "title": "live"})
    try:
        http = []
        fs = _fake_runner("hlssite", {"download_dir": str(tmp_path)}, http)
        ok = _call(fs, "https://ex/v")
        assert ok is True
        assert http == [], "HLS must NOT go through the direct-http path"
        assert len(fake.calls) == 1
        assert fake.calls[0]["manifest_url"] == "https://cdn/master.m3u8"
        # remuxed container -> .mp4 output, referer threaded
        assert fake.calls[0]["output_path"].endswith(".mp4")
        assert fake.calls[0]["kw"].get("referer") == "https://ex/v"
    finally:
        P.unregister_extractor("hlssite")


def test_hls_download_failure_returns_false(tmp_path, monkeypatch):
    from bulk_downloader import plugins as P
    import bulk_downloader.hls_downloader as real_hls
    fake = _FakeHls(ok=False)
    monkeypatch.setattr(real_hls, "download", fake.download)
    monkeypatch.setattr(real_hls, "is_available", fake.is_available)
    P.register_extractor("hlsfail", lambda u, ctx: {
        "video_url": "https://cdn/master.m3u8", "is_hls": True})
    try:
        fs = _fake_runner("hlsfail", {"download_dir": str(tmp_path)}, [])
        assert _call(fs, "https://ex/v") is False
        assert len(fake.calls) == 1     # it DID attempt the HLS download
    finally:
        P.unregister_extractor("hlsfail")


def test_hls_unavailable_falls_through(tmp_path, monkeypatch):
    from bulk_downloader import plugins as P
    import bulk_downloader.hls_downloader as real_hls
    fake = _FakeHls(ok=True, available=False)   # ffmpeg not on PATH
    monkeypatch.setattr(real_hls, "download", fake.download)
    monkeypatch.setattr(real_hls, "is_available", fake.is_available)
    P.register_extractor("hlsnoff", lambda u, ctx: {
        "video_url": "https://cdn/master.m3u8", "is_hls": True})
    try:
        fs = _fake_runner("hlsnoff", {"download_dir": str(tmp_path)}, [])
        assert _call(fs, "https://ex/v") is False
        assert fake.calls == [], "no ffmpeg -> no download attempt"
    finally:
        P.unregister_extractor("hlsnoff")


def test_progressive_path_unchanged(tmp_path, monkeypatch):
    """Regression: a non-HLS result still uses the direct-http path, untouched
    by the HLS branch."""
    from bulk_downloader import plugins as P
    import bulk_downloader.hls_downloader as real_hls
    fake = _FakeHls(ok=True)
    monkeypatch.setattr(real_hls, "download", fake.download)
    monkeypatch.setattr(real_hls, "is_available", fake.is_available)
    P.register_extractor("progsite", lambda u, ctx: {
        "video_url": "https://cdn/clip.mp4", "title": "clip", "ext": "mp4"})
    try:
        http = []
        fs = _fake_runner("progsite", {"download_dir": str(tmp_path)}, http)
        ok = _call(fs, "https://ex/v")
        assert ok is True
        assert http == ["https://cdn/clip.mp4"]
        assert fake.calls == [], "progressive must NOT touch hls_downloader"
    finally:
        P.unregister_extractor("progsite")
