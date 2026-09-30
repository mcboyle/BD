"""dl95-xhamster-2: live saved 480p.h264.mp4 instead of the scene title.

B5-B p1/xhamster RESULT.md: approved 480p rendition landed 38,422,896
bytes under a reusable quality/codec route leaf. Exercise the actual learned
URL transport and harvested page title, stopping at the probe transfer
boundary; no authenticated site or media request is made.
"""
import pytest

BD_GATE_SCOPE = "module"

from bulk_downloader.runner_transport import TransportMixin, resolve_media_leaf_name
from bulk_downloader.website_title import harvest_page_title


@pytest.mark.parametrize("title,expected", [
    ("A Scene Title", "A Scene Title [480p].mp4"),
    ("", "a-scene-xhFixture [480p].mp4"),
])
def test_quality_codec_leaf_gets_a_scene_name(tmp_path, title, expected):
    from bulk_downloader import cloak

    scene_url = "https://fixture.example/videos/a-scene-xhFixture"
    class Recorder(TransportMixin):
        site_id = "fixture"
        config = {"name": "fixture", "learned": {"download": {
            "row_selectors": ["#download"], "url_attribute": "href"}}}
        def _history_title_fields(self, url):
            return {"title": self.title, "title_source": "document.title"}
        def _do_probe_fetch(self, *args):
            self.received_name = args[-1]

    with cloak.cloaked_page(headless=True, config={"browser_backend": "playwright"}) as page:
        html = f'<title>{title}</title><a id="download" href="/media/480p.h264.mp4">Download</a>'
        page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
        page.goto(scene_url)
        runner = Recorder()
        runner.title = harvest_page_title(page)[0] if title else ""
        runner._do_download(page, None, scene_url, {
            "locator": page.locator("#download"), "score": 0, "size": 0,
            "text": "Download", "_via_learned": True, "_learned_sel": "#download",
        }, tmp_path, "?", probe=True)
        assert runner.received_name == expected, f"DL95_XHAMSTER_2_ROUTE_LEAF_SAVED: {runner.received_name}"


@pytest.mark.parametrize("leaf", ["480p.h264.mp4", "720p.h265.mp4"])
def test_quality_codec_disposition_is_not_a_scene_name(leaf):
    got = resolve_media_leaf_name("mp4.mp4", disposition_name=leaf, website_title="Scene")
    assert got == "Scene.mp4", f"DL95_XHAMSTER_2_BARE_DISPOSITION: {got}"


def test_real_stem_with_codec_is_preserved():
    leaf = "a-scene.480p.h264.mp4"
    assert resolve_media_leaf_name(leaf, website_title="Other") == leaf


def test_scene_name_with_bitrate_codec_is_preserved():
    leaf = "named-scene.1080p.7700b.av1.mp4"
    assert resolve_media_leaf_name(leaf, website_title="Other", tier="1080p") == leaf
    doubled = "named-scene.1080p.7700b.av1.mp4.mp4"
    assert resolve_media_leaf_name(doubled, website_title="Other", tier="1080p") == doubled


def test_real_disposition_still_wins():
    got = resolve_media_leaf_name("480p.h264.mp4", disposition_name="named-scene.mp4", website_title="Other")
    assert got == "named-scene.mp4", f"DL95_XHAMSTER_2_DISPOSITION_IGNORED: {got}"


def test_no_title_or_scene_never_invents_a_name():
    assert resolve_media_leaf_name("480p.h264.mp4") == "480p.h264.mp4"


@pytest.mark.parametrize("title,leaf,expected", [
    ("A Scene Title", "720p.h264.mp4", "A Scene Title [720p].mp4"),
    ("", "720p.h264.mp4", "a-scene-xhFixture [720p].mp4"),
    ("A Scene Title", "1080p.7700b.av1.mp4", "A Scene Title [1080p].mp4"),
    ("A Scene Title", "1080p.7700b.av1.mp4.mp4", "A Scene Title [1080p].mp4"),
    ("A Scene Title", "named-scene.720p.h264.mp4", "named-scene.720p.h264.mp4"),
])
def test_spa_api_quality_codec_leaf_gets_scene_name(tmp_path,monkeypatch,title,leaf,expected):
    from pathlib import Path
    from bulk_downloader import cloak,runner_extractors

    class Recorder(runner_extractors.ExtractorsMixin):
        site_id = "fixture"
        _spa_api_capture = None
        # This fixture approves 720p; lane default now requires 1080p.
        config = {"name":"fixture", "min_resolution":720, "download_dir":str(tmp_path/"output")}
        def _history_title_fields(self,url):
            return {"title":title,"title_source":"document.title"}
        def _update_job(self,*args,**kwargs):
            self.outcome = args
        def log_event(self,*args,**kwargs):
            pass
        def _do_direct_http_download(self,page_url,file_url,output_path,**kwargs):
            self.output_path = Path(output_path)
            self.output_path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
            return True
        def _size_on_disk_after_tagging(self,path,fallback):
            return fallback

    monkeypatch.setattr(runner_extractors,"db_log",lambda *a,**k:None)
    scene_url = "https://fixture.example/videos/a-scene-xhFixture"
    with cloak.cloaked_page(headless=True,config={"browser_backend":"playwright"}) as page:
        html = f'<title>{title}</title><video src="https://cdn.example/{leaf}"></video>'
        page.route("**/*",lambda route:route.fulfill(status=200,content_type="text/html",body=html))
        page.goto(scene_url)
        r = Recorder()
        assert r._try_spa_api_media_extractor(scene_url,page)
    assert r.output_path.name == expected, f"DL95_XHAMSTER_2_SPA_ROUTE_LEAF_SAVED: {r.output_path.name}"
