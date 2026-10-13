"""dl95-xvideos-2: a tube scene page's RELATED-video links are not download candidates.

O1508 rerun on test2 (harness-work/UIUX-20260928/download-95/A1-A/FINDINGS-A1-A.md, A12; shots xvideos/09_end__0.png,
hqporner/09_end__0.png): xvideos history 228 (/video.ompcckuc5f1/56047537/...) went needs_review "no dl event; scored ok
but no download fired; saw: 1440p(?):1440p /video.uhvfabd90c8/man_h | auto(?):720p /video.kuibbvk50bb/hot_bl" and
hqporner history 220 (/hdporn/128028-...) "saw: 4K(?):Honey, I Need A Stretch, 4K /h | ..." -- every candidate was a
link to ANOTHER scene of the same site, scored on its thumbnail badge or its title.

The rule: a plain same-site link whose path has the current scene page's shape (same first-segment family) but none
of its id-bearing segments, and that carries no download affordance, is another scene -> admission "other_scene".
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

BD_GATE_SCOPE = "module"

XV = "https://www.xvideos.com/video.ompcckuc5f1/56047537/0/some_scene_title"
HQ = "https://hqporner.com/hdporn/128028-a_scene_title.html"


class _El:
    def __init__(self, **attrs):
        self.attrs = attrs

    def get_attribute(self, name):
        return self.attrs.get(name)


def _admit(href, text, page_url, **attrs):
    from bulk_downloader import detect
    return detect._candidate_admission(_El(href=href, **attrs), text, page_url, label=text.replace(href, " ").strip(),
                                       full_length_requested=False)


@pytest.mark.parametrize("href,text,page_url,refusals", [
    ("/video.uhvfabd90c8/man_h", "1440p /video.uhvfabd90c8/man_h", XV, {"other_scene"}),
    ("https://www.xvideos.com/video.kuibbvk50bb/hot_bl", "720p https://www.xvideos.com/video.kuibbvk50bb/hot_bl", XV,
     {"other_scene"}),
    # A title's trailing "4K" is not a resolution: main refuses this card earlier as no_signal; either refusal holds.
    ("/hdporn/128511-honey_i_need_a_stretch.html", "Honey, I Need A Stretch, 4K /hdporn/128511-honey_i_need_a_stretch.html",
     HQ, {"other_scene", "no_signal"}),
    # The canonical xvideos route shares the bare "/0/" segment with this page.
    ("/video.uhvfabd90c8/12345678/0/man_h", "1440p /video.uhvfabd90c8/12345678/0/man_h", XV, {"other_scene"}),
])
def test_a_link_to_another_scene_is_refused(href, text, page_url, refusals):
    reason = _admit(href, text, page_url)
    assert reason in refusals, f"dl95-xvideos-2: related scene admitted as a download: {href} (admission={reason!r})"


@pytest.mark.parametrize("href,text,attrs", [
    ("/video.ompcckuc5f1/56047537/1080", "1080p /video.ompcckuc5f1/56047537/1080", {}),     # same scene id: a quality link
    ("/video.uhvfabd90c8/man_h.mp4", "1080p /video.uhvfabd90c8/man_h.mp4", {}),             # media file
    ("/video.uhvfabd90c8/man_h", "Download 1080p /video.uhvfabd90c8/man_h", {}),            # visible download intent
    ("/video.uhvfabd90c8/man_h", "1080p /video.uhvfabd90c8/man_h", {"download": ""}),       # download attribute
    ("/dl/video.uhvfabd90c8/1080", "1080p /dl/video.uhvfabd90c8/1080", {}),                 # dl path
    ("/get/uhvfabd90c8/1080", "1080p /get/uhvfabd90c8/1080", {}),                           # other path family
    ("https://cdn.example.net/video.uhvfabd90c8/x", "1080p https://cdn.example.net/video.uhvfabd90c8/x", {}),  # other host
])
def test_download_shaped_or_same_scene_links_are_kept(href, text, attrs):
    assert _admit(href, text, XV, **attrs) != "other_scene", f"dl95-xvideos-2: over-refused {href} {attrs}"


def test_a_page_without_an_id_segment_cannot_judge_and_keeps_the_link():
    # The other-scene rule fails open here; other admission rules may still refuse the link on their own grounds.
    from bulk_downloader import detect
    href, page_url = "/videos/other-slug", "https://example.com/videos/this-slug"
    assert detect._is_other_scene_link(_El(href=href), f"1080p {href}", page_url) is False
    assert _admit(href, f"1080p {href}", page_url) != "other_scene"


# ---- end to end through find_best_download on a loopback tube page ---------------------------------------------------
_PAGE = (
    "<!doctype html><html><head><title>Some scene - XVIDEOS.COM</title></head><body>"
    "<div id='player'><video src='blob:x'></video></div>"
    "<div class='related'>"
    "<div class='thumb-block'><a href='/video.uhvfabd90c8/man_h'><img src='/t1.jpg'><span>1440p</span></a></div>"
    "<div class='thumb-block'><a href='/video.kuibbvk50bb/hot_bl'><img src='/t2.jpg'><span>720p</span></a></div>"
    "<div class='thumb-block'><a href='/video.qzrrcx0e7d2/12345678/0/man_h'><img src='/t3.jpg'><span>1440p</span></a></div>"
    "</div></body></html>")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib handler API
        raw = _PAGE.encode() if urlsplit(self.path).path.startswith("/video.ompcckuc5f1/") else b"<html></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, _fmt, *_args):
        return


@pytest.fixture(scope="module")
def tube_page():
    from bulk_downloader import cloak

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with cloak.cloaked_page(headless=True, config={"browser_backend": "playwright"},
                                viewport={"width": 1280, "height": 720}) as page:
            page.goto(f"http://127.0.0.1:{server.server_port}/video.ompcckuc5f1/56047537/0/some_scene_title")
            yield page
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_find_best_download_never_picks_a_related_scene(tube_page):
    from bulk_downloader import detect

    best = detect.find_best_download(tube_page, full_length_requested=False)
    picked = [] if best is None else [best["text"]] + [c.get("text", "") for c in best.get("_all_candidates") or []]
    related = ("/video.uhvfabd90c8/", "/video.kuibbvk50bb/", "/video.qzrrcx0e7d2/")
    assert not any(r in t for t in picked for r in related), (
        f"dl95-xvideos-2: related-video links ranked as downloads: {picked}")
