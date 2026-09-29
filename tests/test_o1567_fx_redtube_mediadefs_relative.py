"""O1567 fx-redtube-mediadefs-relative (fresh149, 2026-09-29 20:2xZ): live
redtube writes its indirect mediaDefinitions entries as RELATIVE paths
("videoUrl":"\\/media\\/mp4?s=..."), not the absolute URLs the tpl95 fixture
carries. resolve_indirect_definitions compared the entry's own hostname (none)
with the page's registrable domain, skipped every entry, and the extractor
logged ``aylo_extract_failed flashvars: empty_mediadefs``; the runner fell back
to page media and saved a 29 s 720p preview of a 9:30 scene that the
/media/mp4 endpoint lists at 1080p.

Contract: a relative indirect entry is resolved against the page URL and
fetched through the page's session; an entry on another registrable domain is
still never fetched.
"""
from __future__ import annotations

from bulk_downloader import extractors_aylo as aylo

BD_GATE_SCOPE = "module"

PAGE_URL = "https://www.redtube.com/191397851"
REL_MP4 = "/media/mp4?s=eyJ2a2V5IjoxOTEzOTc4NTF9"
REL_HLS = "/media/hls?s=eyJ2a2V5IjoxOTEzOTc4NTF9"
FILE_1080 = "https://ev-ph.rdtcdn.com/videos/202411/11/460383961/1080P_4000K_460383961.mp4"
FILE_720 = "https://ev-ph.rdtcdn.com/videos/202411/11/460383961/720P_4000K_460383961.mp4"

# The shape of the live scene page (captured on fresh149): only relative
# indirect entries, JSON-escaped slashes.
LIVE_HTML = (
    '<html><script>var playerObjList = {}; var page_params = {"mediaDefinitions":['
    '{"format":"hls","videoUrl":"' + REL_HLS.replace("/", "\\/") + '","remote":true},'
    '{"format":"mp4","videoUrl":"' + REL_MP4.replace("/", "\\/") + '","remote":true}'
    '],"video_unavailable_country":"false"};</script></html>')


class _Page:
    def __init__(self, url=PAGE_URL, html=LIVE_HTML):
        self.url = url
        self._html = html
        self.fetched = []

    def content(self):
        return self._html

    def evaluate(self, _js, u):
        self.fetched.append(u)
        if "/media/mp4" in u:
            return [{"quality": "1080", "format": "mp4", "videoUrl": FILE_1080},
                    {"quality": "720", "format": "mp4", "videoUrl": FILE_720}]
        return None


def test_relative_indirect_entry_is_resolved_against_the_page():
    page = _Page()
    defs = aylo.extract_media_definitions(page.content())["mediaDefinitions"]
    out = aylo.resolve_indirect_definitions(page, defs, PAGE_URL)
    assert "https://www.redtube.com" + REL_MP4 in page.fetched, (
        "relative /media/mp4 entry was never fetched: %r" % page.fetched)
    assert [x["videoUrl"] for x in out] == [FILE_1080, FILE_720]


def test_live_redtube_page_extracts_the_1080p_scene_file():
    result = aylo.extract_from_page(_Page())
    assert result.ok, "aylo extract failed: %s %s" % (result.error, result.error_detail)
    assert result.variant.url == FILE_1080
    assert result.variant.quality == 1080


def test_negative_control_foreign_domain_entry_is_never_fetched():
    page = _Page(html=LIVE_HTML.replace(REL_MP4.replace("/", "\\/"),
                                        "https:\\/\\/evil.example\\/media\\/mp4?s=x"))
    defs = aylo.extract_media_definitions(page.content())["mediaDefinitions"]
    aylo.resolve_indirect_definitions(page, defs, PAGE_URL)
    assert not any("evil.example" in u for u in page.fetched), page.fetched


def test_negative_control_relative_entry_without_a_page_url_is_not_fetched():
    page = _Page(url="")
    defs = aylo.extract_media_definitions(page.content())["mediaDefinitions"]
    assert aylo.resolve_indirect_definitions(page, defs, "") == []
    assert page.fetched == []
