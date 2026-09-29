"""fx-scrolller-login-modal (O1567/O1568d, test2 10.0.70.95 21:32Z).

A scrolller post page's "Download" control opens a Register/Log in modal, so
the click fires nothing and the job ended needs_review ("looks like a
modal-trigger button").  The post's media is public and already on the page:
its JSON-LD VideoObject names the page (``url``) and the poster
(``thumbnailUrl``) of the ONE <video> that plays it; that video's <source>
children are the post's renditions, and the page's own post record declares
each one's height (``mediaSources``: url/width/height).  The page also carries
dozens of native-ad clips (galleryAds ``mediaSources``: quark/photon mp4) --
the earlier "PASS" downloaded one of those (quark .../ipazbmvaixyhsrf.mp4, a
fapinstructor ad), never the post.
"""
import base64
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

POST = "https://scrolller.com/yeah-fuck-it-3uxzsyef64"
OTHER_POST = "https://scrolller.com/kwiky-c44e964c-fccb-402a-bb8b-43052d1f04fa"
POSTER = "https://images.scrolller.com/pico/smack-it-dwkyqvs1m2-640x480.webp"
BEST = "https://helios.scrolller.com/UnkemptPortlyElk.mp4"
MOBILE = "https://helios.scrolller.com/UnkemptPortlyElk-mobile.mp4"
WEBM = "https://proton.scrolller.com/UnkemptPortlyElk.webm"
WEBM_MOBILE = "https://proton.scrolller.com/UnkemptPortlyElk-mobile.webm"
AD = "https://quark.scrolller.com/ipazbmvaixyhsrf.mp4"
AD2 = "https://photon.scrolller.com/scrolller/nsfw/Showoff_Autoplay-NSFW.mp4"
CONTENT = "https://meta.scrolller.com/UnkemptPortlyElk_thumb.webm"
CONTENT_ID = base64.b64encode(CONTENT.encode()).decode()


def _html(page_url=POST, poster=POSTER, vid_id="aHR0cHM6"):
    # Shapes as measured on the live page (21:3xZ): raw JSON-LD, the SSR'd
    # <video>, and the RSC stream whose JSON is backslash-escaped.
    ld = ('{"@context":"https://schema.org","name":"Yeah, fuck it!",'
          '"representativeOfPage":true,"@type":"VideoObject",'
          f'"contentUrl":"{CONTENT}",'
          f'"url":"{page_url}","thumbnailUrl":"{POSTER}","duration":"PT47S"}}')
    rsc = (r'"galleryAds":{"nsfw":[{"id":"2553","adPriority":50,"mediaSources":'
           rf'[{{\"url\":\"{AD}\",\"width\":296,\"height\":524,\"type\":\"mp4\"}}],'
           r'\"ad_type\":\"NATIVE\"}]}'
           r'\"mediaSources\":['
           rf'{{\"url\":\"{WEBM}\",\"width\":1440,\"height\":1080,\"type\":\"WEBM\"}},'
           rf'{{\"url\":\"{WEBM_MOBILE}\",\"width\":640,\"height\":480,\"type\":\"WEBM\"}},'
           rf'{{\"url\":\"{BEST}\",\"width\":1440,\"height\":1080,\"type\":\"MP4\"}},'
           rf'{{\"url\":\"{MOBILE}\",\"width\":640,\"height\":480,\"type\":\"MP4\"}}]')
    return (f'<html><head><link rel="canonical" href="{POST}"/>'
            f'<script type="application/ld+json">{ld}</script></head><body>'
            f'<video src="{AD2}" muted></video>'
            f'<video id="{vid_id}" muted preload="none"'
            + (f' poster="{poster}"' if poster else "") + ' loop>'
            f'<source src="{MOBILE}" type="video/mp4"/>'
            f'<source src="{WEBM_MOBILE}" type="video/webm"/>'
            f'<source src="{WEBM}" type="video/webm"/>'
            f'<source src="{BEST}" type="video/mp4"/></video>'
            f'<script>self.__next_f.push([1,"{rsc}"])</script></body></html>')


def test_scene_sources_are_the_jsonld_videos_own_with_declared_heights():
    from bulk_downloader import spa_media_extract as spa
    cands = spa.jsonld_scene_video_candidates(POST, _html(), job_url=POST)
    urls = [c["url"] for c in cands]
    assert set(urls) == {BEST, MOBILE, WEBM, WEBM_MOBILE}, f"SCROLLLER_SCENE_SET: {urls}"
    assert AD not in urls and AD2 not in urls, f"SCROLLLER_AD_ADMITTED: {urls}"
    heights = {c["url"]: c["height"] for c in cands}
    assert heights[BEST] == 1080 and heights[MOBILE] == 480, f"SCROLLLER_HEIGHTS: {heights}"
    assert spa.rank_candidates(cands)[0]["url"] == BEST, "SCROLLLER_RANK_NOT_MP4_1080"


def test_hydrated_player_is_named_by_its_base64_id():
    # Measured (rendered DOM, 21:5xZ): the hydrated player drops poster=; its
    # id is the base64 of the VideoObject contentUrl.
    from bulk_downloader import spa_media_extract as spa
    cands = spa.jsonld_scene_video_candidates(
        POST, _html(poster="", vid_id=CONTENT_ID), job_url=POST)
    assert spa.rank_candidates(cands)[0]["url"] == BEST, f"SCROLLLER_HYDRATED_MISS: {cands}"


def test_player_whose_source_is_the_content_url():
    # Measured (kwiky post): contentUrl IS one of the player's <source>s; the
    # preview sibling is never a candidate.
    from bulk_downloader import spa_media_extract as spa
    page = "https://scrolller.com/kwiky-c44e964c-fccb-402a-bb8b-43052d1f04fa"
    full = "https://helios.scrolller.com/video_c44e964c.mp4"
    prev = "https://helios.scrolller.com/preview_c44e964c.mp4"
    html = ('<script type="application/ld+json">{"@type":"VideoObject",'
            f'"contentUrl":"{full}","url":"{page}"}}</script>'
            f'<video id="x" preload="auto"><source src="{prev}" type="video/mp4">'
            f'<source src="{full}" type="video/mp4"></video>'
            f'"mediaSources":[{{"url":"{full}","width":480,"height":853,"type":"MP4"}}]')
    cands = spa.jsonld_scene_video_candidates(page, html, job_url=page)
    assert [(c["url"], c["height"]) for c in cands] == [(full, 480)], f"SCROLLLER_KWIKY: {cands}"


@pytest.mark.parametrize("html,job", [
    (_html(page_url=OTHER_POST), POST),   # the JSON-LD names another page
    (_html(poster="https://images.scrolller.com/other.webp"), POST),  # no video plays it
    (_html(poster="", vid_id=base64.b64encode(b"https://meta.scrolller.com/Other.webm").decode()),
     POST),                                # the id names another video
    (_html(), OTHER_POST),                 # the page moved off the job's post
], ids=["jsonld-other-page", "poster-mismatch", "id-other-video", "job-moved"])
def test_no_identity_no_candidates(html, job):
    from bulk_downloader import spa_media_extract as spa
    assert spa.jsonld_scene_video_candidates(POST, html, job_url=job) == [], "SCROLLLER_UNPROVEN"


class Page:
    url = POST

    def __init__(self, html):
        self.html = html

    def content(self):
        return self.html

    def evaluate(self, _script):
        return [AD, AD2, MOBILE, BEST]


def _runner(tmp_path, monkeypatch, minimum=1080):
    from bulk_downloader import runner_extractors as rx
    monkeypatch.setattr(rx, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **k: {})

    class Runner(rx.ExtractorsMixin):
        site_id = "scrolller-fixture"

        def __init__(self):
            self.config = {"name": "fixture", "download_dir": str(tmp_path),
                           "min_resolution": minimum}
            self.jobs = {POST: {"force_download": False}}
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


def test_click_miss_fallback_takes_the_posts_1080p_not_an_ad(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    # As runner_transport._fallback_to_page_media calls it after the modal click.
    assert runner._try_spa_api_media_extractor(POST, Page(_html()), min_height=1080,
                                               hold_below=True), \
        f"SCROLLLER_NO_TAKEOVER: {runner.states}"
    assert runner.transfers == [BEST], f"SCROLLLER_WRONG_MEDIA: {runner.transfers}"
    assert runner.states[-1][0] == "done", runner.states


def test_proven_only_precheck_admits_the_post(tmp_path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch, minimum=0)
    assert runner._try_spa_api_media_extractor(POST, Page(_html()), proven_only=True)
    assert runner.transfers == [BEST], f"SCROLLLER_PROVEN_MISS: {runner.transfers}"


def test_unproven_page_keeps_old_miss(tmp_path, monkeypatch):
    # Negative control: without the JSON-LD identity nothing new is admitted
    # under proven_only (ads stay out).
    runner = _runner(tmp_path, monkeypatch, minimum=0)
    html = _html(page_url=OTHER_POST)
    assert not runner._try_spa_api_media_extractor(POST, Page(html), proven_only=True)
    assert runner.transfers == []


def test_scene_video_below_minimum_is_held_with_its_height(tmp_path, monkeypatch):
    # The post's own 1080p is absent: its best (480p, declared) is held for
    # Approve instead of a "modal-trigger" review or a download under the bar.
    runner = _runner(tmp_path, monkeypatch, minimum=1080)
    html = (_html().replace(f'<source src="{WEBM}" type="video/webm"/>', "")
            .replace(f'<source src="{BEST}" type="video/mp4"/>', ""))
    assert runner._try_spa_api_media_extractor(POST, Page(html), proven_only=True)
    assert runner.transfers == [], f"SCROLLLER_BELOW_MIN_TRANSFERRED: {runner.transfers}"
    assert runner.states[-1][0] == "needs_review" and "480p" in runner.states[-1][1], runner.states
