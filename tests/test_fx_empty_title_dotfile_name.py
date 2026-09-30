"""fx-empty-title-dotfile-name: T175 sweep saved bd4 whoreshub/porn00 as ".mp4.mp4".

bd4 whoreshub_T175_evidence-C1-C.json / porn00_T175_evidence-C1-C.json: the
API/media path landed the prior PASS bytes and resolution under the hidden
dotfile "<site>/.mp4.mp4". Both are KVS sites: the media URL's path ends in
"<id>_<tier>.mp4/" (empty leaf) and no page title was harvested, so the name
fell back to the scene URL -- whose last segment is "?t175=1" when the scene
URL is ".../<slug>/?t175=1". The prior PASS URL had no query string and saved
"<slug>.mp4". An empty title must never yield an empty stem.
"""
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

KVS_MEDIA = "https://cdn.example/get_file/1/0abc/738000/738247/738247_1080p.mp4/?br=5000"


@pytest.mark.parametrize("scene_url", [
    "https://fixture.example/videos/738247/a-scene-slug/?t175=1",
    "https://fixture.example/videos/738247/a-scene-slug/",
])
@pytest.mark.parametrize("landed", [0, 720])
def test_empty_title_kvs_leaf_never_saves_a_dotfile(tmp_path, monkeypatch, scene_url, landed):
    from bulk_downloader import cloak, runner_extractors

    class Recorder(runner_extractors.ExtractorsMixin):
        site_id = "fixture"
        _spa_api_capture = None
        config = {"name": "fixture", "min_resolution": 720, "download_dir": str(tmp_path / "output")}
        def _history_title_fields(self, url):
            return {"title": "", "title_source": ""}
        def _update_job(self, *args, **kwargs):
            self.outcome = args
        def log_event(self, *args, **kwargs):
            pass
        def _do_direct_http_download(self, page_url, file_url, output_path, **kwargs):
            Path(output_path).write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
            return True
        def _size_on_disk_after_tagging(self, path, fallback):
            return fallback

    monkeypatch.setattr(runner_extractors, "db_log", lambda *a, **k: None)
    # landed=720: the labelled 1080p re-renders the name from the measured tier.
    monkeypatch.setattr(runner_extractors, "_landed_video_height", lambda p: landed)
    with cloak.cloaked_page(headless=True, config={"browser_backend": "playwright"}) as page:
        html = f'<title></title><video src="{KVS_MEDIA}"></video>'
        page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
        page.goto(scene_url)
        r = Recorder()
        assert r._try_spa_api_media_extractor(scene_url, page)
    saved = sorted(p.name for p in (tmp_path / "output").iterdir())
    assert saved == ["a-scene-slug.mp4"], f"FX_EMPTY_TITLE_DOTFILE_NAME: saved {saved}"
