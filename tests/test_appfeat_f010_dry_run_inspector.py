"""F010 (APPFEAT-fix): /sites/<id>/inspect -- the dry-run inspector.

AGY F010-agy-feat-26-r2 (2026-10-13): a <video src=...mp4> was REJECTED "no url, no trigger signal" (the candidate
walker read href/data-* but never src), Test template showed "NO TEMPLATE" beside a green "a safe candidate would be
selected", and a selected row showed a bare "-15". Backend half here; UI half in
frontend/src/routes/DryRunInspector.f010.test.tsx (delegated below).
"""
from bulk_downloader.dry_run import inspect_candidates
from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "module"

AGY_HTML = '<video src="https://example.com/media/direct_video.mp4">Direct Video</video>'
PAGE = "https://example.com/watch/1"


def _rows_for(res, tag_url):
    return [r for r in res["candidates"] if r["url"] == tag_url]


def test_video_src_is_an_accepted_download_candidate():
    res = inspect_candidates(AGY_HTML, page_url=PAGE)
    assert res["ok"] is True
    url = "https://example.com/media/direct_video.mp4"
    rows = _rows_for(res, url)
    assert len(rows) == 1, f"F010_VIDEO_SRC_MISSING candidates={res['candidates']!r}"
    assert rows[0]["accepted"] and rows[0]["kind"] == "download", f"F010_VIDEO_SRC_REJECTED {rows[0]!r}"
    assert res["winner"] and res["winner"]["url"] == url, f"F010_VIDEO_NOT_WINNER {res['winner']!r}"
    assert res["safe_candidate_available"] is True


def test_source_child_without_text_resolves_against_the_page():
    html = '<video controls><source src="/v/2.mp4" type="video/mp4"></video>'
    res = inspect_candidates(html, page_url="https://example.test/watch/2")
    rows = _rows_for(res, "https://example.test/v/2.mp4")
    assert len(rows) == 1 and rows[0]["accepted"], f"F010_SOURCE_SRC_MISSING candidates={res['candidates']!r}"


def test_controls_unchanged_anchor_and_non_media_tags():
    html = ('<a href="/v/1.mp4">get</a><a href="/page/2">next</a><img src="/t/3.jpg">'
            '<script src="/app.js"></script>')
    res = inspect_candidates(html, page_url="https://example.test/watch/3")
    urls = sorted(r["url"] for r in res["candidates"])
    assert urls == ["/page/2", "https://example.test/v/1.mp4"], f"F010_CONTROL_DRIFT {urls!r}"
    assert res["n_accepted"] == 1 and res["n_rejected"] == 1


def test_video_without_src_or_text_is_still_not_a_candidate():
    res = inspect_candidates("<video controls></video>", page_url=PAGE)
    assert res["n_candidates"] == 0, f"F010_EMPTY_VIDEO_COUNTED {res['candidates']!r}"


def test_media_src_read_only_on_video_and_source():
    # r2 (D139 M2): an <img> walked for its data-video attribute keeps its src unread.
    html = '<img data-video="1" src="/t/3.jpg"><video src="/v/4.mp4">clip</video>'
    res = inspect_candidates(html, page_url="https://example.test/watch/4")
    urls = [r["url"] for r in res["candidates"]]
    assert not [u for u in urls if "/t/3.jpg" in u], f"F010_IMG_SRC_READ {urls!r}"
    assert "https://example.test/v/4.mp4" in urls, f"F010_VIDEO_SRC_MISSING {urls!r}"


def test_two_textless_source_siblings_both_listed():
    # r2 (D139 M4): the dedup key carries src, so sibling renditions do not collapse.
    html = '<video controls><source src="/v/720.mp4"><source src="/v/1080.mp4"></video>'
    res = inspect_candidates(html, page_url="https://example.test/watch/5")
    urls = sorted(r["url"] for r in res["candidates"])
    assert urls == ["https://example.test/v/1080.mp4", "https://example.test/v/720.mp4"], f"F010_SOURCE_DEDUP {urls!r}"


# r2 (D139 R1): template extraction must not see <video>/<source> src -- its rows carry one url_attribute ("href"),
# so a src row would be unfetchable and would push the real download buttons out. Pinned = BASE b0ea77a6 output.
PLAYER_HTML = ('<div class="player"><video src="/trailers/t.mp4">Your browser does not support video</video></div>'
               '<div class="downloads"><button class="download-btn">Download 1080p</button>'
               '<button class="download-btn">Download 4K</button></div>')
ANCHOR_HTML = ('<div class="player"><video src="/trailers/t.mp4">Your browser does not support video</video></div>'
               '<div class="downloads"><a class="dl-btn" href="/download/1080.mp4">Download 1080p</a>'
               '<a class="dl-btn" href="/download/2160.mp4">Download 4K</a></div>')
BASE_TEMPLATES = {
    "player": {"_hidden_rows_dropped": [], "_top_candidate_score": 105, "min_resolution": 2160, "name": "Site",
               "review_required": True, "row_selectors": ["button.download-btn", "button.download-btn", "video"],
               "trigger_selectors": [], "url_attribute": "href", "url_patterns": "site\\.test"},
    "anchors": {"_hidden_rows_dropped": [], "_top_candidate_score": 105, "min_resolution": 2160, "name": "Site",
                "review_required": False, "row_selectors": ["a.dl-btn", "a.dl-btn"],
                "trigger_selectors": [], "url_attribute": "href", "url_patterns": "site\\.test"},
}


def test_template_extraction_unchanged_from_base():
    from bulk_downloader.template_extractor_impl.candidates import extract_from_html
    for name, html in (("player", PLAYER_HTML), ("anchors", ANCHOR_HTML)):
        got = extract_from_html(html, page_url="https://site.test/watch/1")["template"]
        assert got == BASE_TEMPLATES[name], f"F010_TEMPLATE_DRIFT {name}: {got!r}"


def test_extractor_walk_does_not_read_media_src():
    from bs4 import BeautifulSoup
    from bulk_downloader.template_extractor_impl.candidates import _walk_for_candidates
    rows = _walk_for_candidates(BeautifulSoup(PLAYER_HTML, "html.parser"))
    assert not [r for r in rows if "src" in r], f"F010_EXTRACTOR_READS_SRC {[r.get('tag') for r in rows]!r}"


def test_f010_ui_runtime_contract():
    spec = "src/routes/DryRunInspector.f010.test.tsx"
    receipt = run_vitest(spec, expected_tests=3)
    assert receipt["files_passed"] == receipt["files_collected"] == 1
    assert receipt["tests_passed"] == receipt["tests_collected"] == 3
