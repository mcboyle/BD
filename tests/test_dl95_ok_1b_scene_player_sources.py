"""Recover the captured scene player's signed MP4 routes, never its preroll."""
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe
SCENE = "https://fixture.example/video/785100/"
PREFIX = "https://cdn.example/get_file/13/fixture/785000/785100/"
BEST = PREFIX + "785100_720p.mp4/?view=fixture"
AD = "https://ads.example/creative_2160p.mp4"
HTML = f'''<video src="{AD}"></video><video class="video-js">
<source type="video/mp4" src="{PREFIX}785100_360p.mp4/">
<source type="video/mp4" src="{PREFIX}785100.mp4/">
<source type="video/mp4" src="{BEST}">
<source type="video/mp4" src="{PREFIX}785100_preview1080p.mp4/">
<source type="video/mp4" src="https://cdn.example/get_file/13/fixture/785000/785999/785999_4320p.mp4/">
</video>'''


class Page:
    url = SCENE

    def __init__(self, html=HTML, media=None):
        self.html = html
        self.media = [AD, BEST] if media is None else media

    def content(self):
        return self.html

    def evaluate(self, _script):
        return self.media


def _runner(tmp_path, monkeypatch, minimum=0, force=False):
    from bulk_downloader import runner_extractors as rx
    monkeypatch.setattr(rx, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **k: {})

    class Runner(rx.ExtractorsMixin):
        site_id = "ok1b-fixture"

        def __init__(self):
            self.config = {"name": "fixture", "download_dir": str(tmp_path),
                           "min_resolution": minimum}
            self.jobs = {SCENE: {"force_download": force}}
            self.states = []
            self.transfers = []

        def _update_job(self, _url, status, message, **_extra):
            self.states.append((status, message))

        def log_event(self, *_a, **_k):
            pass

        def _screenshot(self, *_a):
            return "fixture.png"

        def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
            self.transfers.append(file_url)
            Path(output_path).write_bytes(b"fixture media")
            return True

        def _size_on_disk_after_tagging(self, _path, fallback):
            return fallback

    return Runner()


def test_same_scene_signed_source_wins_without_changing_url(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    assert runner._try_spa_api_media_extractor(SCENE, Page())
    assert runner.transfers == [BEST], f"OK1B_AD_OR_FOREIGN_SOURCE: {runner.transfers}"
    assert runner.states[-1][0] == "done"


def test_scene_player_respects_minimum_before_transfer(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch, minimum=1080)
    assert runner._try_spa_api_media_extractor(SCENE, Page())
    assert not runner.transfers, "OK1B_BELOW_MINIMUM_TRANSFERRED"
    assert runner.states[-1][0] == "needs_review"
    assert "720" in runner.states[-1][1]


def test_explicit_approval_uses_scene_source_not_ad(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch, minimum=1080, force=True)
    assert runner._try_spa_api_media_extractor(SCENE, Page())
    assert runner.transfers == [BEST], "OK1B_APPROVAL_SELECTED_AD"


def test_bare_rendition_is_unknown_not_assumed_top_quality(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch, minimum=1080)
    html = f'<video class="video-js"><source src="{PREFIX}785100.mp4/" type="video/mp4"></video>'
    assert runner._try_spa_api_media_extractor(SCENE, Page(html))
    assert not runner.transfers, "OK1B_UNKNOWN_QUALITY_TRANSFERRED"
    assert runner.states[-1][0] == "needs_review"


def test_existing_non_scene_media_path_still_works(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    plain = "https://cdn.example/movie_1080p.mp4"
    page = Page("<main>fixture</main>", [plain])
    page.url = "https://fixture.example/members/watch/fixture"
    assert runner._try_spa_api_media_extractor(page.url, page)
    assert runner.transfers == [plain]


def test_source_cohort_preserves_signature_and_deduplicates():
    from bulk_downloader.spa_media_extract import scene_player_candidates
    signed = PREFIX + "785100_720p.mp4/?sig=a%2Fb%2Bc&expires=2160#fragment"
    html = (f'<video class="video-js"><source src="{signed}">'
            f'<source src="{signed}"></video>')
    candidates = scene_player_candidates(SCENE, html)
    assert len(candidates) == 1
    assert candidates[0]["url"] == signed
    assert candidates[0]["height"] == 720
    assert candidates[0]["source"] == "scene-player"


@pytest.mark.parametrize("html", [
    f'<video class="video-js" src="{BEST}"></video>',
    f'<video><source src="{BEST}"></video>',
    f'<video class="video-js"><source src="{PREFIX}785100_preview1080p.mp4/"></video>',
    f'<video class="video-js"><source src="{PREFIX.replace("785100/", "785999/")}785100_720p.mp4/"></video>',
    f'<video class="video-js"><source src="{PREFIX}785999_720p.mp4/"></video>',
])
def test_scene_identity_requires_child_source_and_matching_route(html):
    from bulk_downloader.spa_media_extract import scene_player_candidates
    assert scene_player_candidates(SCENE, html) == []


def test_unknown_source_does_not_infer_quality_from_query_or_label():
    from bulk_downloader.spa_media_extract import scene_player_candidates
    html = (f'<video class="video-js"><source label="2160p" '
            f'src="{PREFIX}785100.mp4/?quality=4320p"></video>')
    assert scene_player_candidates(SCENE, html)[0]["height"] == 0
