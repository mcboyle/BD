"""O1789 bug-hunt dax-pages: playlist / hydration / search / gallery-dl updater rows.

Findings: harness-work/O1789/bughunt/deep-adapters-extractors/FINDINGS.md (finder bd-worker-S01-A, BASE b0ea77a6);
each test is the finder's repro (ev/probe_*.out) asserting the correct result, with its control beside it.
Offline: stub pages, a fake gallery-dl executable in tmp_path, no network, no browser.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import json
import types

import pytest

from bulk_downloader import gallerydl_updater as GU
from bulk_downloader import playlist_extractor as PX
from bulk_downloader import search_extractor as SX
from bulk_downloader.hydration_extractor import HydrationExtractor


# ---- playlist_extractor -----------------------------------------------------------------------------------------
class _ListingPage:
    def __init__(self, links_by_url=None):
        self.gone, self.url, self.links_by_url = [], "", links_by_url or {}

    def goto(self, url, **_kw):
        self.gone.append(url)
        self.url = url

    def evaluate(self, _js, *_a):
        return self.links_by_url.get(self.url.rstrip("/"), [])

    def wait_for_timeout(self, *_a):
        pass


def test_p1_pages_continue_from_the_listing_page():
    page = _ListingPage()
    PX.extract_playlist_urls(page, "https://x.com/cat/?page=5", max_pages=3)
    assert page.gone == ["https://x.com/cat/?page=5", "https://x.com/cat/?page=6", "https://x.com/cat/?page=7"], (
        f"P1: visited {page.gone}")


def test_p1_control_first_page_walk():
    page = _ListingPage()
    PX.extract_playlist_urls(page, "https://x.com/cat/", max_pages=3)
    assert page.gone == ["https://x.com/cat/", "https://x.com/cat/?page=2", "https://x.com/cat/?page=3"]


@pytest.mark.parametrize("max_pages,visits", [("3", 3), ("abc", 0)])
def test_p2_max_pages_text_is_normalised_never_raises(max_pages, visits):
    page = _ListingPage()
    r = PX.extract_playlist_urls(page, "https://x.com/cat/", max_pages=max_pages)
    assert len(page.gone) == visits, f"P2: max_pages={max_pages!r} visited {page.gone}"
    if visits == 0:
        assert r.ok is False and r.error == "bad_max_pages"


@pytest.mark.parametrize("url", ["https://x.com/video/225731", "https://x.com/video/helping-hand",
                                 "https://x.com/video/searching-for-love", "https://x.com/video/1?ref=/category/a"])
def test_p3_scene_slug_is_not_a_route_word(url):
    assert PX._looks_like_scene_url(url) is True, f"P3: {url} refused as non-scene"


@pytest.mark.parametrize("url", ["https://x.com/video/12345"])
def test_p3_control_plain_scene(url):
    assert PX._looks_like_scene_url(url) is True


@pytest.mark.parametrize("url", ["https://x.com/help", "https://x.com/2257", "https://x.com/login.php",
                                 "https://x.com/search?q=a", "https://x.com/category/a/video/1",
                                 "https://x.com/account/video/1", "mailto:a@x.com", "#top"])
def test_p3_control_route_words_still_refuse(url):
    assert PX._looks_like_scene_url(url) is False, f"P3 control: {url} admitted"


def test_p4_expansion_never_navigates_session_or_account_routes(monkeypatch):
    root = "https://x.com"
    links = ["https://x.com/logout", "https://x.com/account/billing", "https://x.com/upgrade",
             "https://x.com/sign-out", "https://x.com/models/", "https://x.com/video/12345"]
    page = _ListingPage({root: [{"url": u, "title": ""} for u in links]})

    def fake_topology(pg, url, **_kw):
        pg.goto(url)
        agg = url.rstrip("/") == root
        return PX.TopologyResult(ok=True, base_url=url, is_aggregate=agg, card_count=5 if agg else 0)

    monkeypatch.setattr(PX, "classify_dataset_topology", fake_topology)
    PX.route_dataset_page(page, root + "/", max_depth=2)
    bad = [u for u in page.gone if any(w in u for w in ("logout", "billing", "upgrade", "sign-out"))]
    assert not bad, f"P4: navigated {bad}"
    assert "https://x.com/models/" in page.gone, f"P4 control: listing child not classified: {page.gone}"


def test_p9_non_numeric_page_value_is_replaced():
    assert PX._guess_next_page_url("https://x.com/c?page=abc", 2) == "https://x.com/c?page=2"
    assert PX._guess_next_page_url("https://x.com/c?page=4&s=1", 5) == "https://x.com/c?page=5&s=1"  # control


# ---- hydration_extractor ----------------------------------------------------------------------------------------
def _next(state) -> str:
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(state)}</script>'


def _hx(html):
    return HydrationExtractor().extract(html)


def test_h1_page_url_key_does_not_hide_the_media_src():
    m = _hx(_next({"props": {"title": "Scene", "url": "https://site.com/scene/1", "src": "https://cdn.x/a.mp4"}}))
    assert m.stream_urls == ["https://cdn.x/a.mp4"], f"H1: {m.stream_urls}"


def test_h2_a_page_link_is_not_a_stream():
    m = _hx(_next({"props": {"title": "About", "url": "https://site.com/videos/about"}}))
    assert m.stream_urls == [], f"H2: {m.stream_urls}"


def test_h2_control_media_url_and_descriptor_keys_still_count():
    m = _hx(_next({"a": {"url": "https://cdn.x/v/a.mp4"}, "b": {"url": "https://cdn.x/play/1", "quality": "1080p"},
                   "c": {"src": "https://cdn.x/video/1"}}))
    assert m.stream_urls == ["https://cdn.x/v/a.mp4", "https://cdn.x/play/1", "https://cdn.x/video/1"]


def test_h3_deeply_nested_state_is_not_a_crash():
    deep = "[" * 100000 + "]" * 100000
    html = f'<script id="__NEXT_DATA__" type="application/json">{deep}</script>'
    assert _hx(html) is None  # H3: was RecursionError


def test_h4_nuxt_string_holding_key_colon_text_parses():
    html = ('<script>window.__NUXT__ = { data: [{ title: "Ep 2, time: 10", src: "https://cdn.x/a.m3u8" }] }'
            '</script>')
    m = _hx(html)
    assert m is not None and m.title == "Ep 2, time: 10", f"H4: {m}"
    assert m.stream_urls == ["https://cdn.x/a.m3u8"]


def test_h4_control_plain_nuxt_title():
    m = _hx('<script>window.__NUXT__ = { data: [{ title: "Ep two", src: "https://cdn.x/a.m3u8", }] }</script>')
    assert m.title == "Ep two" and m.stream_urls == ["https://cdn.x/a.m3u8"]


@pytest.mark.parametrize("raw", ["true", "1e400"])
def test_h6_bool_or_infinite_duration_is_not_a_duration(raw):
    html = ('<script id="__NEXT_DATA__" type="application/json">'
            f'{{"title": "Scene", "duration": {raw}, "src": "https://cdn.x/a.mp4"}}</script>')
    assert _hx(html).duration is None, f"H6: duration {raw} accepted"


def test_h6_control_numeric_duration():
    assert _hx(_next({"title": "Scene", "duration": 1820, "src": "https://cdn.x/a.mp4"})).duration == 1820.0


def test_h8_protocol_relative_stream_is_https():
    m = _hx(_next({"title": "Scene", "src": "//cdn.x/a.m3u8"}))
    assert m.stream_urls == ["https://cdn.x/a.m3u8"], f"H8: {m.stream_urls}"


@pytest.mark.parametrize("bad", ["///etc/a.mp4", "// x.mp4", "//", "//\tcdn.x/a.mp4", "//:80/a.mp4",
                                 "//cdn.x:abc/a.mp4", "//-cdn.x/a.mp4", "//cdn.x/a b.mp4"])
def test_h8_protocol_relative_without_host_is_dropped(bad):
    # r2 (REFUTE D177): "//" + no valid host must not become "https:" + junk, for streams or thumbnails.
    m = _hx(_next({"title": "Scene", "src": bad, "thumbnail": bad}))
    assert m.stream_urls == [] and m.thumbnail_url == "", f"H8 r2: {bad!r} -> {m.stream_urls} / {m.thumbnail_url!r}"


def test_h8_protocol_relative_thumbnail_and_port_kept():
    m = _hx(_next({"title": "Scene", "src": "//cdn.x:8443/a.m3u8?t=1", "thumbnail": "//img.cdn.x/t.jpg"}))
    assert m.stream_urls == ["https://cdn.x:8443/a.m3u8?t=1"] and m.thumbnail_url == "https://img.cdn.x/t.jpg"


def test_h9_one_character_cjk_title_is_kept():
    assert _hx(_next({"title": "愛", "src": "https://cdn.x/a.mp4"})).title == "愛"
    assert _hx(_next({"title": "ok", "src": "https://cdn.x/a.mp4"})).title == ""  # control: 2 ASCII chars stay junk


# ---- search_extractor -------------------------------------------------------------------------------------------
T = {"search_url_pattern": "https://x.com/search?q={query}"}


class _SearchPage:
    def __init__(self, hits, status=200, landed=None):
        self.hits, self.status, self.landed, self.url = hits, status, landed, ""

    def goto(self, url, **_kw):
        self.url = self.landed or url
        return types.SimpleNamespace(status=self.status, ok=self.status < 400)

    def evaluate(self, _js, *_a):
        return self.hits

    def close(self):
        pass


H3 = [{"url": f"https://x.com/video/{i}", "title": f"t{i}", "thumb": ""} for i in range(3)]


def test_s1_rate_limited_search_is_a_failure():
    r = SX.search_site(_SearchPage([], status=429), "x", "q", T)
    assert (r.ok, r.error) == (False, "http_429"), f"S1: {r.ok} {r.error!r}"


def test_s1_control_ok_search():
    r = SX.search_site(_SearchPage(H3), "x", "q", T)
    assert r.ok and r.count == 3


@pytest.mark.parametrize("cap,count", [(0, 0), (2, 2)])
def test_s2_max_results_is_respected(cap, count):
    assert SX.search_site(_SearchPage(H3), "x", "q", T, max_results=cap).count == count


def test_s4_whitespace_query_builds_no_url():
    assert SX.build_search_url(T, "   ") == ""
    assert SX.build_search_url(T, "a b") == "https://x.com/search?q=a+b"  # control


def test_s6_off_site_redirect_is_reported():
    r = SX.search_site(_SearchPage([{"url": "https://x.net/video/1", "title": "a", "thumb": ""}],
                                   landed="https://x.net/search?q=q"), "x", "q", T)
    assert r.ok is False and r.error == "redirected_off_site:x.net", f"S6: {r.ok} {r.error!r}"


def test_s7_fragment_and_tracking_copies_are_one_hit():
    urls = ("https://x.com/video/1", "https://x.com/video/1#c", "https://x.com/video/1?utm_source=a",
            "https://x.com/watch?v=2", "https://x.com/watch?v=3")
    r = SX.search_site(_SearchPage([{"url": u, "title": "a", "thumb": ""} for u in urls]), "x", "q", T)
    assert [h.url for h in r.hits] == ["https://x.com/video/1", "https://x.com/watch?v=2", "https://x.com/watch?v=3"], (
        f"S7: {[h.url for h in r.hits]}")


@pytest.mark.parametrize("flt,sites", [([], []), (["x"], ["x"]), (None, ["x", "y"])])
def test_s9_empty_filter_selects_no_site(flt, sites):
    got = SX.search_all_sites(lambda: _SearchPage(H3), "q", {"x": T, "y": T}, sites_filter=flt)
    assert sorted(got) == sites, f"S9: sites_filter={flt!r} searched {sorted(got)}"


# ---- gallerydl_updater ------------------------------------------------------------------------------------------
@pytest.fixture
def fake_gdl(tmp_path, monkeypatch):
    monkeypatch.setattr(GU, "_VERSION_CACHE", {"ts": 0.0, "version": None, "path": None})
    monkeypatch.setattr(GU, "_LAST_UPDATE_CHECK", {})

    def make(stdout_printf: str):
        exe = tmp_path / "gallery-dl"
        exe.write_text(f"#!/bin/sh\nprintf '{stdout_printf}'\n")
        exe.chmod(0o755)
        return str(exe)
    return make


@pytest.mark.parametrize("out", [r"\n", r"  \n", r"\377 1.30\n"])
def test_g1_g2_unreadable_version_is_none(fake_gdl, out):
    assert GU.current_version(fake_gdl(out)) is None, f"G1/G2: {out!r}"


def test_g1_control_version(fake_gdl):
    assert GU.current_version(fake_gdl(r"1.30.2\n")) == "1.30.2"


def _forced_updates(monkeypatch, pip_rc):
    monkeypatch.setattr(GU, "_LAST_UPDATE_CHECK", {})
    monkeypatch.setattr(GU.shutil, "which", lambda _n: "/fake/gallery-dl")
    launches = []

    def fake_run(cmd, **_kw):
        if "pip" in cmd:
            launches.append(cmd)
            return types.SimpleNamespace(returncode=pip_rc, stdout="", stderr="network down")
        return types.SimpleNamespace(returncode=0, stdout="1.30.2\n", stderr="")
    monkeypatch.setattr(GU.subprocess, "run", fake_run)
    clock = [1_000_000.0]
    monkeypatch.setattr(GU, "time", types.SimpleNamespace(time=lambda: clock[0]))
    first = GU.maybe_update(force=True)
    clock[0] += 301
    second = GU.maybe_update(force=True)
    return first, second, launches


def test_g5_failed_update_can_be_retried_without_waiting_24h(monkeypatch):
    first, second, launches = _forced_updates(monkeypatch, pip_rc=1)
    assert first[0] is False and len(launches) == 2, f"G5: retry 5 min after a failure -> {second} ({len(launches)} pip)"


def test_g5_control_successful_update_stays_rate_limited(monkeypatch):
    first, second, launches = _forced_updates(monkeypatch, pip_rc=0)
    assert first[0] is True and second == (False, "update check rate-limited (24h)") and len(launches) == 1
