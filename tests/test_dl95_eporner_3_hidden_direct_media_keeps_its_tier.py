"""dl95-eporner-3 -- a hidden anchor whose href IS the file keeps its resolution.

LIVE (test2 v3.66.1710, 2026-09-29 01:41-01:45Z, harness-work/DOT95-LANE/live-dl95-eporner-1/
tplproof-eporner.json): the eporner scene ended needs_review "Best is 720p (below 1080p) ... Saw:
720p(?):[VV] @cc£$$ https://www.eporne | auto(264.7 MB):MP4 (1080p, h264, 264.66 MB) | auto(264.0 MB):
MP4 (720p, h264, 263.99 MB) | ...". Every real tier was listed as "auto" (score 0).

WHY. eporner renders its ten /dload/<id>/<h>/<file>.mp4 anchors inside div#downloaddiv, which is
display:none until the operator opens it (tests/fixtures/eporner_dload_anchors.json, captured in live
Chromium). Row 759 zeroes the resolution of any candidate Playwright reports not visible, because a hidden
quality CELL cannot be clicked. That is right for an hrefless modal cell. It is wrong for an anchor whose
href is the media file: the transport never clicks such a winner, it fetches the href directly
(TransportMixin._stream_route / _direct_media_route, rows 384 and 819). With every real tier zeroed, one
visible junk link carrying "720p" outranked the page's 1080p file and tripped the min-resolution hold.

THE RULE. Row 759 zeroes hidden tiers so that a VISIBLE offering wins. A hidden candidate whose own href
routes to a direct fetch (the transport's own pure routing functions) keeps its tier only when the page has
NO visible candidate that routes to a direct fetch -- then the hidden files are the page's only files. With
a visible direct file present (row 759 section 1: visible 1080.mp4 beside hidden 2160.mp4) the hidden ones
stay zeroed, and hidden cells without a file href are zeroed as before (controls below).

Mock-page pattern of tests/test_row759_hidden_quality_cell_is_not_a_candidate.py: no browser, no network.
The eporner anchors come from the real capture, not invented text.
"""

# The gate parses a module-level ASSIGNMENT, not a docstring line.
BD_GATE_SCOPE = "module"

import json
from pathlib import Path

from bulk_downloader.detect import find_best_download, res_score

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "eporner_dload_anchors.json").read_text(encoding="utf-8"))
PAGE_URL = "https://www.eporner.com/video-tipTUUUwIEI/best-columbian-tits-ever/"


class _Loc:
    """Playwright-locator stub: only what the wide sweep calls (no evaluate, no locator)."""

    def __init__(self, *, text, visible, attrs=None):
        self._text = text
        self._visible = visible
        self._attrs = attrs or {}

    def is_visible(self, timeout=None):
        return self._visible

    def inner_text(self, timeout=None):
        return self._text

    def get_attribute(self, name):
        return self._attrs.get(name)


class _LocList:
    def __init__(self, elements):
        self._elements = list(elements)

    def all(self):
        return list(self._elements)

    def count(self):
        return len(self._elements)

    @property
    def first(self):
        return _LocList(self._elements[:1])

    def nth(self, i):
        return self._elements[i]


class _Page:
    def __init__(self, selector_map, url=PAGE_URL):
        self._map = selector_map
        self.url = url

    def locator(self, sel):
        return _LocList(self._map.get(sel, []))


def _eporner_anchors():
    return [_Loc(text=a["text"], visible=a["visible"], attrs={"href": a["href"]})
            for a in FIXTURE["anchors"]]


# The visible junk the live run saw: text carries a 720p token, the href is a page, not a file.
_JUNK = _Loc(text="[VV] @cc£$$ https://www.eporner.com/cat/720p/", visible=True,
             attrs={"href": "https://www.eporner.com/cat/720p/"})


def _live_page():
    return _Page({"a[href*='.mp4']": _eporner_anchors(), "a[href*='/cat/']": [_JUNK],
                  "a": _eporner_anchors() + [_JUNK]})


class TestTheCaptureItself:
    def test_every_real_tier_is_hidden_in_a_shut_panel(self):
        assert FIXTURE["download_panel"] == {"id": "downloaddiv", "display": "none"}
        assert len(FIXTURE["anchors"]) == 10
        assert not any(a["visible"] for a in FIXTURE["anchors"])
        assert all(a["href"].startswith("/dload/") and a["href"].endswith(".mp4")
                   for a in FIXTURE["anchors"])

    def test_the_labels_parse_as_tiers(self):
        best = max(res_score(a["text"]) for a in FIXTURE["anchors"])
        assert best == 1080, [a["text"] for a in FIXTURE["anchors"]]


class TestHiddenDirectMediaKeepsItsTier:
    def test_the_page_s_1080p_file_wins_over_visible_junk(self):
        best = find_best_download(_live_page())
        seen = [(c["score"], c["text"][:40]) for c in best.get("_all_candidates", [])]
        assert best["score"] == 1080, f"DL95-EPORNER-3: winner {best['score']} {best['text'][:60]!r}; saw {seen}"
        assert "1080p" in best["text"], best["text"]
        assert best["locator"].get_attribute("href").startswith("/dload/tipTUUUwIEI/1080/")

    def test_every_hidden_dload_candidate_keeps_its_parsed_tier(self):
        # _all_candidates is the ranked head of the list, so check every /dload row it reports.
        best = find_best_download(_live_page())
        rows = [c for c in best.get("_all_candidates", []) if "/dload/" in c["text"]]
        assert len(rows) >= 4, [c["text"][:40] for c in best.get("_all_candidates", [])]
        for c in rows:
            label = next(a["text"] for a in FIXTURE["anchors"] if c["text"].startswith(a["text"]))
            assert c["score"] == res_score(label), (c["text"][:60], c["score"])

    def test_a_hidden_stream_manifest_keeps_its_tier(self):
        cell = _Loc(text="1080p", visible=False,
                    attrs={"href": "https://cdn.example/hls/1080p/master.m3u8"})
        label = _Loc(text="720p", visible=True, attrs={"href": "https://cdn.example/v/720p.html"})
        best = find_best_download(_Page({"a[href*='download']": [label, cell]}))
        assert best["score"] == 1080, best["text"]


class TestRow759StillHolds:
    def test_a_visible_direct_file_keeps_hidden_files_zeroed(self):
        # Row 759 section 1, with the eporner capture: add one VISIBLE direct .mp4 (720p) and the hidden
        # /dload files must stay zeroed, so the visible file wins.
        visible = _Loc(text="Download 720p", visible=True,
                       attrs={"href": "https://www.eporner.com/dload/tipTUUUwIEI/720/visible-720p.mp4"})
        page = _Page({"a[href*='.mp4']": [visible] + _eporner_anchors()})
        best = find_best_download(page)
        assert best["locator"] is visible, best["text"]
        hidden_scores = {c["score"] for c in best.get("_all_candidates", []) if c["locator"] is not visible}
        assert hidden_scores == {0}, hidden_scores

    def test_a_hidden_cell_without_a_file_href_is_still_zeroed(self):
        # Negative control: the modal cell row 759 exists for (href is a page anchor, not a file).
        label = _Loc(text="1080p FHD", visible=True, attrs={"href": "#"})
        cell = _Loc(text="2160p", visible=False, attrs={"href": "#quality"})
        best = find_best_download(_Page({"[role='menuitem']": [label, cell]}))
        assert best["text"].startswith("1080p FHD") and best["score"] == 1080, best["text"]
