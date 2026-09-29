"""dl95-beeg-2-live-1: ranked variant playlist preferred over multi master.

LIVE on test2 (.95 build 2f161614, live-dl95-beeg-2/LIVE-RESULT-B6-B.md):
beeg https://beeg.com/-0127012409542942 saw:
  media=hls4A/multi=426x240:240p
  media=hls4A/1080p
  media=hls4A/240p
The fetch must use the ranked 1080p playlist (media=hls4A/av1_1080p/... or an h264
1080p variant when present) instead of the multi=426x240 playlist; if the only
1080p is AV1 and AV1 is not allowed, say that in the hold.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from bulk_downloader import runner_extractors, spa_media_extract

BD_GATE_SCOPE = "module"

SCENE_1 = "https://beeg.com/-0127012409542942"
SCENE_2 = "https://beeg.com/-0151293413383291"

CDN_PREFIX_1 = "https://video.beeg.com/key=K1,end=1790743042,limit=10/data=0dd355c036/media=hls4A/"
CDN_PREFIX_2 = "https://video.beeg.com/key=K2,end=1790743616,limit=10/data=d51b7c2cc8/media=hls4A/"

# Scene 1 manifests from live run 04:38Z
MULTI_1 = CDN_PREFIX_1 + "multi=426x240:240p:YXZjMS42NDAwMUUsbXA0YS40MC4y,1920x1080:1080p:YXZjMS42NDAwMkEsbXA0YS40MC4y/_TPL_/127012409542942.mp4.m3u8"
PLAIN_1080_1 = CDN_PREFIX_1 + "1080p/127012409542942.mp4.m3u8"
PLAIN_240_1 = CDN_PREFIX_1 + "240p/127012409542942.mp4.m3u8"

# Scene 2 manifests from live run 04:47Z
MULTI_2 = CDN_PREFIX_2 + "multi=426x240:240p:YXZjMS42NDAwMUUsbXA0YS40MC4y,1920x1080:1080p:YXZjMS42NDAwMkEsbXA0YS40MC4y/_TPL_/151293413383291.mp4.m3u8"
AV1_1080_2 = CDN_PREFIX_2 + "av1_1080p/151293413383291.mp4.m3u8"
AV1_720_2 = CDN_PREFIX_2 + "av1_720p/151293413383291.mp4.m3u8"
AV1_480_2 = CDN_PREFIX_2 + "av1_480p/151293413383291.mp4.m3u8"


def test_plain_1080p_variant_outranks_multi_master():
    """LIVE repro 1: media=hls4A/1080p existed alongside multi=426x240:240p.
    The direct 1080p variant must be ranked first."""
    urls = [MULTI_1, PLAIN_1080_1, PLAIN_240_1]
    cands = spa_media_extract.scene_stream_candidates(SCENE_1, urls)
    ranked = spa_media_extract.rank_candidates(cands)
    assert ranked[0]["url"] == PLAIN_1080_1, f"Expected plain 1080p, got {ranked[0]}"


def test_av1_1080p_variant_outranks_multi_master():
    """LIVE repro 2: media=hls4A/av1_1080p existed alongside multi=426x240:240p.
    The direct av1_1080p variant must be ranked before multi."""
    urls = [MULTI_2, AV1_1080_2, AV1_720_2, AV1_480_2]
    cands = spa_media_extract.scene_stream_candidates(SCENE_2, urls)
    ranked = spa_media_extract.rank_candidates(cands)
    assert ranked[0]["url"] == AV1_1080_2, f"Expected av1_1080p, got {ranked[0]}"


def test_h264_1080p_outranks_av1_1080p():
    """When both H264 and AV1 1080p variants exist, H264 must be ranked first."""
    urls = [AV1_1080_2, PLAIN_1080_1]
    cands = spa_media_extract.scene_stream_candidates(SCENE_1, urls)
    # Give them the same scene ID so both qualify
    cands = [
        {"url": PLAIN_1080_1, "height": 1080, "source": "scene-stream", "codec": "h264", "is_multi": False},
        {"url": AV1_1080_2, "height": 1080, "source": "scene-stream", "codec": "av1", "is_multi": False},
    ]
    ranked = spa_media_extract.rank_candidates(cands)
    assert ranked[0]["url"] == PLAIN_1080_1, f"Expected h264 1080p, got {ranked[0]}"


def test_manifest_text_js_has_cors_fallback():
    """MANIFEST_TEXT_JS must try include first (for cookie-gated manifests)
    and omit as fallback when include is rejected across origins."""
    js = spa_media_extract.MANIFEST_TEXT_JS
    assert "credentials: 'include'" in js or 'credentials: "include"' in js
    assert "credentials: 'omit'" in js or 'credentials: "omit"' in js
    inc_pos = js.find("credentials: 'include'") if "credentials: 'include'" in js else js.find('credentials: "include"')
    omit_pos = js.find("credentials: 'omit'") if "credentials: 'omit'" in js else js.find('credentials: "omit"')
    assert inc_pos < omit_pos, "MANIFEST_TEXT_JS must try include before omit"


class _Stub:
    site_id = "dl95beeg2live1"

    def __init__(self, tmp_path, job, manifests, allow_av1=True):
        self.config = {
            "name": "beeg",
            "download_dir": str(tmp_path / "dl"),
            "min_resolution": 1080,
            "allow_av1": allow_av1,
        }
        self.jobs = {job: {"force_download": False}}
        self.manifest_urls = [{"url": u} for u in manifests]
        self.updates, self.events, self.segmented = [], [], []

    def _update_job(self, url, status, message, **extra):
        self.updates.append((status, message))

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))

    def _screenshot(self, page, url):
        return ""

    def _do_direct_http_download(self, *a, **k):
        return False

    def _hls_download_guarded(self, _hls, manifest_url, output_path, **kwargs):
        self.segmented.append(manifest_url)
        res = MagicMock()
        res.bytes_written = 1000
        return res

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


def test_runner_fetches_plain_1080p_variant_over_multi(tmp_path, monkeypatch):
    """When both multi and plain 1080p manifests are observed, runner fetches the plain 1080p variant."""
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(runner_extractors, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(runner_extractors, "_landed_video_height", lambda path: 1080)

    stub = type("StubRunner", (_Stub, runner_extractors.ExtractorsMixin), {})(
        tmp_path, SCENE_1, [MULTI_1, PLAIN_1080_1, PLAIN_240_1]
    )
    page = MagicMock()
    page.url = SCENE_1
    page.content.return_value = "<html><body></body></html>"
    page.evaluate.return_value = []

    res = stub._try_spa_api_media_extractor(SCENE_1, page)
    assert res is True
    assert stub.segmented == [PLAIN_1080_1], f"Expected {PLAIN_1080_1}, got {stub.segmented}"


def test_av1_not_allowed_holds_with_explanatory_message(tmp_path, monkeypatch):
    """When allow_av1 is False and the only 1080p stream is AV1, hold in needs_review."""
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(runner_extractors, "db_log", lambda *a, **k: None)

    stub = type("StubRunner", (_Stub, runner_extractors.ExtractorsMixin), {})(
        tmp_path, SCENE_2, [MULTI_2, AV1_1080_2, AV1_720_2], allow_av1=False
    )
    page = MagicMock()
    page.url = SCENE_2
    page.content.return_value = "<html><body></body></html>"
    page.evaluate.return_value = []

    res = stub._try_spa_api_media_extractor(SCENE_2, page)
    assert res is True
    assert len(stub.updates) > 0
    status, msg = stub.updates[-1]
    assert status == "needs_review", f"Expected needs_review, got {status}: {msg}"
    assert "AV1" in msg and "not allowed" in msg, f"Expected hold message to state AV1 not allowed, got: {msg}"


def test_unknown_height_scene_master_is_fetched_as_its_tallest_variant(tmp_path, monkeypatch):
    """B18-B R1: an unknown-height master without =WxH segment must have its variants resolved."""
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(runner_extractors, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(runner_extractors, "_landed_video_height", lambda path: 0)

    bare_master = CDN_PREFIX_1 + "127012409542942.mp4.m3u8"
    v240 = CDN_PREFIX_1 + "v/240.m3u8"
    v1080 = CDN_PREFIX_1 + "v/1080.m3u8"
    master_text = (
        '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=426x240,CODECS="avc1.64001E,mp4a.40.2"\n'
        + v240
        + '\n#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080,CODECS="avc1.64002A,mp4a.40.2"\n'
        + v1080
        + "\n"
    )

    stub = type("StubRunner", (_Stub, runner_extractors.ExtractorsMixin), {})(
        tmp_path, SCENE_1, [bare_master]
    )
    stub.config["min_resolution"] = 0
    page = MagicMock()
    page.url = SCENE_1
    page.content.return_value = "<html></html>"
    page.evaluate.side_effect = lambda js, *a: master_text if js == spa_media_extract.MANIFEST_TEXT_JS else []

    res = stub._try_spa_api_media_extractor(SCENE_1, page)
    assert res is True
    assert stub.segmented == [v1080], f"Expected {v1080}, got {stub.segmented}"


def test_av1_not_allowed_uses_compliant_h264_at_min_resolution(tmp_path, monkeypatch):
    """B18-B R3: allow_av1=False uses compliant non-AV1 stream when it meets min_resolution."""
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(runner_extractors, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(runner_extractors, "_landed_video_height", lambda path: 720)

    av1 = CDN_PREFIX_1 + "av1_1080p/127012409542942.mp4.m3u8"
    h264_720 = CDN_PREFIX_1 + "720p/127012409542942.mp4.m3u8"

    stub = type("StubRunner", (_Stub, runner_extractors.ExtractorsMixin), {})(
        tmp_path, SCENE_1, [av1, h264_720], allow_av1=False
    )
    stub.config["min_resolution"] = 720
    page = MagicMock()
    page.url = SCENE_1
    page.content.return_value = "<html></html>"
    page.evaluate.side_effect = lambda js, *a: "" if js == spa_media_extract.MANIFEST_TEXT_JS else []

    res = stub._try_spa_api_media_extractor(SCENE_1, page)
    assert res is True
    assert stub.segmented == [h264_720], f"Expected {h264_720}, got {stub.segmented}"


def test_page_media_master_with_a_height_in_its_url_is_resolved(tmp_path, monkeypatch):
    """B18-B R1: a page-media HLS master whose URL names a height has its variants resolved."""
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(runner_extractors, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(runner_extractors, "_landed_video_height", lambda path: 0)

    scene = "https://www.example-tube.com/videos/some-scene-title"
    master = "https://cdn.example-tube.com/v/1080p/master.m3u8"
    v240 = "https://cdn.example-tube.com/v/240.m3u8"
    v1080 = "https://cdn.example-tube.com/v/1080.m3u8"
    master_text = (
        '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=426x240,CODECS="avc1.64001E,mp4a.40.2"\n'
        + v240
        + '\n#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080,CODECS="avc1.64002A,mp4a.40.2"\n'
        + v1080
        + "\n"
    )

    stub = type("StubRunner", (_Stub, runner_extractors.ExtractorsMixin), {})(tmp_path, scene, [])
    stub.config["min_resolution"] = 0
    page = MagicMock()
    page.url = scene
    page.content.return_value = "<html></html>"

    def _eval(js, *a):
        if js == spa_media_extract.PAGE_MEDIA_JS:
            return [master]
        if js == spa_media_extract.MANIFEST_TEXT_JS:
            return master_text
        return []

    page.evaluate.side_effect = _eval
    assert stub._try_spa_api_media_extractor(scene, page) is True, stub.updates
    assert stub.segmented == [v1080], f"Expected {v1080}, got {stub.segmented}"
