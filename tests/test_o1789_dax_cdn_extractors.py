"""O1789 bughunt deep-adapters-extractors (finder S01-A) -- dax-cdn: the D*, V*, A* rows.

extractors_dl8 (Badoink / dl8-video), extractors_vixen and extractors_aylo, fixed row by row; every test names its
row in the assertion message (DAX-<row>-...). Offline: literal HTML / JSON fixtures, stub pages, httpx.MockTransport
in place of the pinned transport. No real site, app or DB is touched.

Not in this cut (see its DONE.md): A6 / V11 (is quality_pref a cap?) -- a product decision.
"""

from __future__ import annotations

import json
import types

import httpx
import pytest

from bulk_downloader import extractors_aylo as aylo
from bulk_downloader import extractors_dl8 as dl8
from bulk_downloader import extractors_vixen as vixen

BD_GATE_SCOPE = "module"
BADOINK_PAGE = "https://badoinkvr.com/vr-video/scene-1"


# ═════════════════════════════ extractors_dl8 ═════════════════════════════
def _dl8(sources: str, attrs: str = 'title="t"') -> str:
    return f"<dl8-video {attrs}>{sources}</dl8-video>"


def _src(url: str, quality: str) -> str:
    return f'<source src="{url}" quality="{quality}">'


THREE = _dl8(_src("https://badoinkvr.com/a_8k.mp4", "8K") + _src("https://badoinkvr.com/a_4k.mp4", "4K")
             + _src("https://badoinkvr.com/a_1080.mp4", "1080p"))


def test_d1_a_gt_inside_a_quoted_attribute_does_not_end_the_tag():
    r = dl8.extract_from_html(_dl8(_src("https://badoinkvr.com/a_8k.mp4", "8K"), 'title="Tom > Jerry" poster="p.jpg"'),
                              page_url=BADOINK_PAGE, predict_badoink=False)
    assert (r.ok, r.title, r.poster) == (True, "Tom > Jerry", "p.jpg"), f"DAX-D1-TITLE-LOST: title={r.title!r} poster={r.poster!r}"
    r = dl8.extract_from_html(_dl8('<source src="https://badoinkvr.com/a.mp4?x=1>2" quality="4K"/>'), predict_badoink=False)
    assert r.url == "https://badoinkvr.com/a.mp4?x=1>2" and r.tier == 2160, f"DAX-D1-SOURCE-CUT: {r.url!r} tier={r.tier}"


@pytest.mark.parametrize("pref,want", [("1080P", "a_1080"), ("4K", "a_4k"), (1080.0, "a_1080"), ("1080p", "a_1080"),
                                       (1080, "a_1080"), (" 2160 ", "a_4k"), ("best", "a_8k"), ("nonsense", "a_8k"),
                                       (True, "a_8k"), (float("nan"), "a_8k")])
def test_d2_quality_preference_spellings(pref, want):
    r = dl8.extract_from_html(THREE, page_url=BADOINK_PAGE, quality_pref=[pref], predict_badoink=False)
    assert want in r.url, f"DAX-D2-PREF-IGNORED: quality_pref=[{pref!r}] picked {r.url}, expected the {want} source"


def test_d4_an_off_site_trailer_seeds_no_member_area_candidates():
    off = dl8.extract_from_html(_dl8(_src("https://evil.example/content/v_trailer.mp4", "1080p")), page_url=BADOINK_PAGE)
    hosts = sorted({httpx.URL(c.url).host for c in off.badoink_candidates})
    assert off.badoink_candidates == [] and off.via != "badoink_predict", (
        f"DAX-D4-CROSS-HOST-CANDIDATES: {len(off.badoink_candidates)} candidates on {hosts} from an off-site trailer")
    on = dl8.extract_from_html(_dl8(_src("https://cdn.badoinkvr.com/content/v_trailer.mp4", "1080p")), page_url=BADOINK_PAGE)
    assert on.via == "badoink_predict" and len(on.badoink_candidates) == 18, f"DAX-D4-CONTROL: {on.via} {len(on.badoink_candidates)}"
    assert {httpx.URL(c.url).host for c in on.badoink_candidates} == {"cdn.badoinkvr.com"}
    both = dl8.extract_from_html(_dl8(_src("https://evil.example/v_trailer.mp4", "8K")
                                      + _src("https://badoinkvr.com/v_trailer.mp4", "1080p")), page_url=BADOINK_PAGE)
    assert {httpx.URL(c.url).host for c in both.badoink_candidates} == {"badoinkvr.com"}, "DAX-D4-CROSS-HOST-CANDIDATES: mixed page"


def _probe(monkeypatch, handler, candidates, **kw):
    seen: list = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.host, request.headers.get("cookie", "")))
        return handler(request)

    monkeypatch.setattr("bulk_downloader.ssrf_transport.guarded_transport", lambda *a, **k: httpx.MockTransport(record))
    return dl8.probe_badoink_candidates(candidates, **kw), seen


def _cands(host: str, n: int = 3) -> list:
    return dl8.predict_badoink_filenames(f"https://{host}/content/v_trailer.mp4")[:n]


def test_d4_probe_never_sends_cookies_off_the_badoink_network(monkeypatch):
    cands = _cands("evil.example")
    got, seen = _probe(monkeypatch, lambda r: httpx.Response(200), cands, cookies={"auth": "s"})
    assert seen == [("HEAD", "evil.example", "")], f"DAX-D4-COOKIE-SENT-OFF-NETWORK: {seen}"
    assert got is cands[0]  # the probe itself still answers for any URL it is handed (landed contract)

    def redirecting(request):
        if request.url.host == "badoinkvr.com":
            return httpx.Response(302, headers={"location": "https://evil.example/landing"})
        return httpx.Response(200)

    _got, seen = _probe(monkeypatch, redirecting, _cands("badoinkvr.com", 1), cookies={"auth": "s"})
    assert seen[0] == ("HEAD", "badoinkvr.com", "auth=s"), f"DAX-D4-CONTROL: on-network request lost its cookie: {seen[:1]}"
    leaked = [s for s in seen if s[1] != "badoinkvr.com" and s[2]]
    assert len(seen) == 2 and not leaked, f"DAX-D4-COOKIE-LEAKED-ON-REDIRECT: {seen}"


def test_d5_d6_source_urls_are_resolved_and_non_http_schemes_dropped():
    r = dl8.extract_from_html(_dl8(_src("/v/x_1080.mp4", "1080p")), page_url="https://tmwvrnet.com/scene/1")
    assert r.url == "https://tmwvrnet.com/v/x_1080.mp4", f"DAX-D5-RELATIVE-SRC-KEPT: {r.url!r}"
    r = dl8.extract_from_html(_dl8(_src("//cdn.tmwvrnet.com/x_1080.mp4", "1080p")), page_url="https://tmwvrnet.com/scene/1")
    assert r.url == "https://cdn.tmwvrnet.com/x_1080.mp4", f"DAX-D5-RELATIVE-SRC-KEPT: {r.url!r}"
    assert dl8.extract_from_html(_dl8(_src("/v/x_1080.mp4", "1080p"))).url == "/v/x_1080.mp4"  # no page: nothing to resolve against
    r = dl8.extract_from_html(_dl8(_src("javascript:alert(1)", "8K") + _src("https://tmwvrnet.com/a_1080.mp4", "1080p")),
                              page_url="https://tmwvrnet.com/scene/1")
    assert r.url == "https://tmwvrnet.com/a_1080.mp4" and r.available_tiers == [1080], f"DAX-D6-NON-HTTP-SCHEME-CHOSEN: {r.url!r}"
    for bad in ("javascript:alert(1)", "data:video/mp4;base64,AAAA", "file:///etc/passwd"):
        r = dl8.extract_from_html(_dl8(_src(bad, "8K")), page_url="https://tmwvrnet.com/scene/1")
        assert (r.ok, r.url, r.error) == (False, "", "no_pickable_source"), f"DAX-D6-NON-HTTP-SCHEME-CHOSEN: {bad} -> {r.url!r}"


@pytest.mark.parametrize("html,want", [
    ('<a class="video-tag"><span>8K</span></a>', 4320),
    ('<a class="video-tag">8K</a>', 4320),
    ('<div class="tags"><a class="tag video-tag"> <b>5K</b> </a><a class="video-tag">4K</a></div>', 2880),
    ('<a class="not-video-tag"><span>8K</span></a>', 0),
    ('<a class="video-tag"></a><p class="x">8K</p>', 0),
    ('<li class="video-tag">1080p<br>', 1080),
])
def test_d8_quality_tag_text_inside_a_child_element(html, want):
    assert dl8.detect_max_quality_tag(html) == want, f"DAX-D8-NESTED-TAG-TEXT: {html} -> {dl8.detect_max_quality_tag(html)}, expected {want}"


def test_d9_entities_are_decoded_once():
    assert dl8._parse_attrs('src="a&amp;quot;b"') == {"src": "a&quot;b"}, f"DAX-D9-DOUBLE-DECODE: {dl8._parse_attrs('src=&quot;a&amp;quot;b&quot;')}"
    assert dl8._parse_attrs('src="a.mp4?x=1&amp;y=2" title="&quot;q&quot;"') == {"src": "a.mp4?x=1&y=2", "title": '"q"'}


def test_d10_auth_failure_on_the_range_get_aborts_the_cascade(monkeypatch):
    def head405_get401(request):
        return httpx.Response(405 if request.method == "HEAD" else 401)

    got, seen = _probe(monkeypatch, head405_get401, _cands("badoinkvr.com", 10), max_attempts=10)
    assert got is None and [s[0] for s in seen] == ["HEAD", "GET"], (
        f"DAX-D10-NO-ABORT-AFTER-GET-401: {len(seen)} requests {[s[0] for s in seen][:6]}")
    cands = _cands("badoinkvr.com", 3)
    got, seen = _probe(monkeypatch, lambda r: httpx.Response(405 if r.method == "HEAD" else 206), cands)
    assert got is cands[0] and len(seen) == 2, "DAX-D10-CONTROL: HEAD 405 then GET 206 must return the candidate"
    answers = iter([404, 200])
    got, seen = _probe(monkeypatch, lambda r: httpx.Response(next(answers)), cands)
    assert got is cands[1] and len(seen) == 2, "DAX-D10-CONTROL: a 404 moves on to the next candidate"
    got, seen = _probe(monkeypatch, lambda r: httpx.Response(405 if r.method == "HEAD" else 404), cands)
    assert got is None and len(seen) == 6, f"DAX-D10-CONTROL: a 404 on the GET must not abort: {len(seen)} requests"


# ═════════════════════════════ extractors_vixen ═════════════════════════════
@pytest.mark.parametrize("url", ["https://cdn.vixen.com.evil.net/a.mp4", "https://vixen.com@evil.net/a.mp4",
                                 "https://evil.net/?r=https://cdn.vixen.com", "https://evil.net/https://cdn.vixen.com/a.mp4",
                                 "ftp://cdn.vixen.com/a.mp4", "https://notvixen.com/a.mp4", "https://vixen.com.evil.net:443/a"])
def test_v1_a_brand_name_in_the_url_is_not_a_vixen_host(url):
    assert vixen._looks_like_vixen_cdn(url) is False, f"DAX-V1-HOST-NOT-ANCHORED: {url} accepted as a Vixen CDN"


@pytest.mark.parametrize("url", ["https://cdn.vixen.com/a.mp4", "http://members.blacked.com/x", "https://a.b.tushy.com/x",
                                 "https://m1.vixencdn.com/mp4_2160/x.mp4", "HTTPS://CDN.VIXEN.COM/a.mp4", "https://deeper.com/x"])
def test_v1_controls_real_vixen_hosts(url):
    assert vixen._looks_like_vixen_cdn(url) is True, f"DAX-V1-CONTROL: {url} refused"


def test_v1_an_off_network_video_src_is_not_the_scene():
    evil = '<video src="https://cdn.vixen.com.evil.net/x_2160P.mp4"></video>'
    assert vixen.find_video_src_in_html(evil) is None, f"DAX-V1-HOST-NOT-ANCHORED: {vixen.find_video_src_in_html(evil)}"
    assert vixen.extract_from_html(evil).ok is False
    record = {"title": "t", "videoUrl": "https://cdn.vixen.com.evil.net/x.mp4", "sources": [{"src": "https://cdn.vixen.com/mp4_1080/x.mp4"}]}
    r = vixen.extract_from_graphql_response({"data": {"findOneVideo": record}})
    assert r.url == "https://cdn.vixen.com/mp4_1080/x.mp4", f"DAX-V1-HOST-NOT-ANCHORED: picked {r.url}"


def _vx(**fields):
    record = {"title": "t", "sources": [{"src": "https://cdn.vixen.com/mp4_1080/x.mp4"}]}
    record.update(fields)
    return vixen.extract_from_graphql_response({"data": {"findOneVideo": record}})


def test_v3_v6_runtime_and_fractional_durations():
    assert _vx(runtime=1820).duration_sec == 1820, f"DAX-V3-RUNTIME-IGNORED: {_vx(runtime=1820).duration_sec}"
    assert _vx(runtime="30:20").duration_sec == 1820 and _vx(runtime="1:00:05").duration_sec == 3605, "DAX-V3-RUNTIME-IGNORED: clock form"
    assert _vx(duration=1820).duration_sec == 1820 and _vx(duration=60, runtime=99).duration_sec == 60
    got = [vixen._int_or_zero(v) for v in (1820.5, "1820.5", "1820", "1080p", "1080P", None, True, float("nan"), float("inf"), "x", "1e400")]
    assert got == [1820, 1820, 1820, 1080, 1080, 0, 0, 0, 0, 0, 0], f"DAX-V6-FRACTION-IS-ZERO: {got}"
    assert _vx(duration=1820.5).duration_sec == 1820


def test_v4_a_signed_manifest_url_is_hls():
    r = _vx(sources=[{"src": "https://cdn.vixen.com/hls/index.m3u8?t=1"}])
    assert r.is_hls is True, f"DAX-V4-SIGNED-M3U8-NOT-HLS: {r.url}"
    assert _vx().is_hls is False and _vx(sources=[{"src": "https://cdn.vixen.com/x/master.m3u8"}]).is_hls is True
    assert _vx(sources=[{"src": "https://cdn.vixen.com/x.mp4?next=a.m3u8"}]).is_hls is False, "DAX-V4-CONTROL: m3u8 only in the query"
    assert vixen.extract_from_html('<video src="https://cdn.vixen.com/h/i.m3u8?t=1"></video>').is_hls is True, "DAX-V4-SIGNED-M3U8-NOT-HLS: video_src path"


def test_v5_v8_never_raise_on_hostile_input():
    deep = '<script id="__NEXT_DATA__" type="application/json">' + "[" * 100_000 + "</script>"
    try:
        assert vixen.extract_next_data(deep) is None and vixen.extract_from_html(deep).ok is False
    except RecursionError:
        pytest.fail("DAX-V5-RECURSION-RAISED: extract_next_data raised RecursionError on a deeply nested blob")
    try:
        assert vixen.find_video_src_in_html(b"<video src='https://cdn.vixen.com/a.mp4'>") is None
        assert vixen.extract_metadata_from_html(b"<title>x</title>") == {}
    except TypeError as e:
        pytest.fail(f"DAX-V8-BYTES-RAISED: {e}")


def _gql_page(status: int, body):
    resp = types.SimpleNamespace(status=status, json=lambda: body)
    return types.SimpleNamespace(url="https://www.vixen.com/videos/slug-1", request=types.SimpleNamespace(post=lambda *a, **k: resp))


def test_v7_a_graphql_error_answer_is_not_no_record():
    r = vixen.extract_via_graphql(_gql_page(429, {"errors": [{"message": "rate limited"}]}))
    assert (r.ok, r.error, r.error_detail) == (False, "graphql_http_error", "HTTP 429"), f"DAX-V7-ERROR-READ-AS-NO-RECORD: {r.error} {r.error_detail}"
    r = vixen.extract_via_graphql(_gql_page(200, {"errors": [{"message": "Variable $site invalid"}], "data": None}))
    assert (r.error, r.error_detail) == ("graphql_errors", "Variable $site invalid"), f"DAX-V7-ERROR-READ-AS-NO-RECORD: {r.error}"
    assert vixen.extract_via_graphql(_gql_page(200, {"data": {"findOneVideo": None}})).error == "graphql_no_record"
    ok = vixen.extract_via_graphql(_gql_page(200, {"data": {"findOneVideo": {"title": "t", "videoManifestUrl": "https://cdn.vixen.com/m/master.m3u8"}}}))
    assert ok.ok and ok.is_hls, "DAX-V7-CONTROL: a good answer still extracts"


def test_v9_v10_tier_from_the_path_only_and_poster_lists():
    assert vixen._tier_from_url("https://cdn.vixen.com/x.mp4?ref=mp4_2160") == 0, "DAX-V9-TIER-FROM-QUERY"
    assert vixen._tier_from_url("https://cdn.vixen.com/mp4_1080/x.mp4?ref=mp4_2160#mp4_480") == 1080, "DAX-V9-TIER-FROM-QUERY"
    assert vixen._tier_from_url("https://cdn.vixen.com/x_720P.mp4") == 720
    assert _vx(poster=["https://i/1.jpg", "https://i/2.jpg"]).thumbnail_url == "https://i/1.jpg", f"DAX-V10-POSTER-LIST-REPR: {_vx(poster=['https://i/1.jpg']).thumbnail_url!r}"
    assert _vx(poster=[{"url": "https://i/d.jpg"}]).thumbnail_url == "https://i/d.jpg" and _vx(poster=[]).thumbnail_url == ""
    assert _vx(poster=5).thumbnail_url == "", f"DAX-V10-POSTER-LIST-REPR: non-string poster 5 -> {_vx(poster=5).thumbnail_url!r}"
    assert _vx(poster="https://i/s.jpg").thumbnail_url == "https://i/s.jpg"


def test_vixen_preference_spellings():
    urls = [("https://cdn.vixen.com/mp4_2160/x.mp4", 2160), ("https://cdn.vixen.com/mp4_1080/x.mp4", 1080)]
    for pref in ("1080P", 1080.0, "1080p", 1080):
        assert vixen._pick_url_by_preference(urls, [pref])[1] == 1080, f"DAX-D2-PREF-IGNORED: vixen pref {pref!r}"


# ═════════════════════════════ extractors_aylo ═════════════════════════════
GOOD = {"format": "mp4", "videoUrl": "https://cv.phncdn.com/v/720P.mp4", "quality": "720"}


def _fv(block_id="259384716", **fields) -> str:
    cfg = {"mediaDefinitions": [GOOD]}
    cfg.update(fields)
    return f"<script>var flashvars_{block_id} = {json.dumps(cfg)};</script>"


def _ay(**fields):
    return aylo.extract_from_html(_fv(**fields))


def test_a1_a5_never_raise_on_malformed_config():
    try:
        assert aylo.extract_from_html(_fv(mediaDefinitions=5)).ok is False
        page = types.SimpleNamespace(content=lambda: _fv(mediaDefinitions=5), url="https://www.pornhub.com/v", evaluate=lambda *a: None)
        assert aylo.extract_from_page(page).ok is False
    except TypeError as e:
        pytest.fail(f"DAX-A1-NON-LIST-MEDIADEFS-RAISED: {e}")
    try:
        assert aylo.extract_from_html("<script>var flashvars_1 = {\"mediaDefinitions\": " + "[" * 100_000 + "};</script>").ok is False
        assert aylo.extract_from_html("<script>x = {mediaDefinitions: " + "[" * 100_000 + "]" * 100_000 + "}</script>").ok is False
        enclosing = '<script>cfg = {"junk": ' + "[" * 100_000 + "]" * 100_000 + ', "mediaDefinitions": [%s]};</script>' % json.dumps(GOOD)
        assert aylo.extract_from_html(enclosing).variant.url == GOOD["videoUrl"]  # the array alone is still usable
    except RecursionError:
        pytest.fail("DAX-A5-RECURSION-RAISED: extract_from_html raised RecursionError")


def test_a2_video_duration_forms():
    nan_html = '<script>var flashvars_1 = {"mediaDefinitions": [%s], "video_duration": NaN};</script>' % json.dumps(GOOD)
    try:
        got = [_ay(video_duration=v).duration_sec for v in ("30:20", "1:02:03", "1820", 1820, 1820.9, "x", None, True)]
        got.append(aylo.extract_from_html(nan_html).duration_sec)
    except ValueError as e:
        pytest.fail(f"DAX-A2-DURATION-RAISED: {e}")
    assert got == [1820, 3723, 1820, 1820, 1820, 0, 0, 0, 0], f"DAX-A2-DURATION-WRONG: {got}"


def test_a3_a4_one_malformed_entry_does_not_sink_the_good_one():
    for row, bad in (("A3", {"format": "mp4", "videoUrl": 12345, "quality": "1080"}), ("A4", {"format": 1, "videoUrl": "https://cv.phncdn.com/v/x", "quality": "1080"}),
                     ("A3", {"format": "mp4", "videoUrl": ["u"], "quality": "1080"}), ("A4", {"format": None, "videoUrl": None})):
        try:
            r = aylo.extract_from_html(_fv(mediaDefinitions=[bad, GOOD]))
        except AttributeError as e:
            pytest.fail(f"DAX-{row}-BAD-ENTRY-RAISED: {bad} -> {e}")
        assert r.ok and r.variant.url == GOOD["videoUrl"], f"DAX-{row}-BAD-ENTRY-RAISED: {bad} -> {r.error}"


def test_a7_a_relative_direct_url_is_resolved_against_the_page():
    html = _fv(mediaDefinitions=[{"format": "mp4", "videoUrl": "/v/scene_720p.mp4", "quality": "720"}])
    page = types.SimpleNamespace(content=lambda: html, url="https://www.pornhub.com/view_video.php?viewkey=1", evaluate=lambda *a: None)
    r = aylo.extract_from_page(page)
    assert r.ok and r.variant.url == "https://www.pornhub.com/v/scene_720p.mp4", f"DAX-A7-RELATIVE-URL-KEPT: {r.variant and r.variant.url!r}"
    assert aylo.extract_from_html(html).variant.url == "/v/scene_720p.mp4"  # no page URL: nothing to resolve against
    page_abs = types.SimpleNamespace(content=lambda: _fv(), url="https://www.pornhub.com/x", evaluate=lambda *a: None)
    assert aylo.extract_from_page(page_abs).variant.url == GOOD["videoUrl"]


@pytest.mark.parametrize("quality,want", [("1080p60", 1080), ("1080P", 1080), ("1080", 1080), (1080, 1080), ("2160p", 2160),
                                          ("", 0), ("hd", 0), (None, 0), (True, 0)])
def test_a8_quality_labels_with_a_frame_rate(quality, want):
    hi = {"format": "mp4", "videoUrl": "https://cv.phncdn.com/v/HI.mp4", "quality": quality}
    r = aylo.extract_from_html(_fv(mediaDefinitions=[hi, GOOD]))
    picked = "HI" if want > 720 else "720P"
    assert r.variant.quality == max(want, 720) and picked in r.variant.url, (
        f"DAX-A8-QUALITY-LABEL-ZERO: quality {quality!r} -> picked {r.variant.url} ({r.variant.quality})")
    assert aylo._max_numeric_quality([hi]) == want


def test_a9_embed_id_falls_back_to_the_flashvars_block_id():
    assert _ay().embed_id == "259384716", f"DAX-A9-EMBED-ID-EMPTY: {_ay().embed_id!r}"
    assert _ay(video_id="ph5f").embed_id == "ph5f" and _ay(video_id=77).embed_id == "77"
    hi = {"format": "mp4", "videoUrl": "https://cv.phncdn.com/v/1080P.mp4", "quality": "1080"}
    assert aylo.extract_from_html(_fv(block_id="42", mediaDefinitions=[hi])).embed_id == "42", "DAX-A9-EMBED-ID-EMPTY: 1080 block"
    bare = "<script>cfg = {\"mediaDefinitions\": [%s]};</script>" % json.dumps(GOOD)
    assert aylo.extract_from_html(bare).embed_id == ""  # no flashvars_<id>: no id to borrow


def test_a10_a_signed_manifest_without_a_format_is_hls():
    r = aylo.extract_from_html(_fv(mediaDefinitions=[{"videoUrl": "https://ev.phncdn.com/hls/index.m3u8?t=1", "quality": [1080, 720]}]))
    assert r.ok and r.variant.format == "hls" and r.variant.quality == 1080, f"DAX-A10-SIGNED-M3U8-DROPPED: {r.error}"
    r = aylo.extract_from_html(_fv(mediaDefinitions=[{"videoUrl": "https://cv.phncdn.com/v/a.mp4?t=1", "quality": "480"}]))
    assert r.ok and r.variant.format == "mp4"
    r = aylo.extract_from_html(_fv(mediaDefinitions=[{"videoUrl": "https://cv.phncdn.com/get?f=a.mp4", "quality": "480"}]))
    assert r.ok is False, "DAX-A10-CONTROL: an extension only in the query is not a format"


def test_aylo_preference_spellings():
    hi = {"format": "mp4", "videoUrl": "https://cv.phncdn.com/v/1080P.mp4", "quality": "1080"}
    for pref in ("720P", 720.0, "720p", 720):
        assert aylo.pick_best_variant([hi, GOOD], [pref]).quality == 720, f"DAX-D2-PREF-IGNORED: aylo pref {pref!r}"
