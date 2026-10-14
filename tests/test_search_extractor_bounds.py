"""search-extractor-bounds r1 (O1789 extension-bridge F9, finder probe S30).

search_site evaluates its link-extraction JS in the search page's own world,
so a hostile page controls every field it returns. A non-string title must
drop that one hit (not fail the whole site search); thumbnail_url must be
http(s) (or relative to the page) and bounded, else dropped from the hit.
Controls: off-site / javascript: / data: hit URLs stay filtered.
"""
import pytest

from bulk_downloader import search_extractor as se

BD_GATE_SCOPE = "module"

CAP = 2048  # pinned literal: a moved constant must fail here

T = {"search_url_pattern": "https://www.example.com/search?q={query}"}


class _Resp:
    status = 200


class _Page:
    """Fake Playwright page: evaluate() returns the canned anchors."""

    def __init__(self, anchors):
        self.anchors = anchors
        self.url = ""

    def goto(self, url, **_kw):
        self.url = url
        return _Resp()

    def evaluate(self, _js, *_a):
        return self.anchors


def _search(anchors):
    return se.search_site(_Page(anchors), "s", "q", T)


def _hit(n, title="ok", thumb=""):
    return {"url": "https://www.example.com/video/%d" % n, "title": title, "thumb": thumb}


@pytest.mark.parametrize("title", [12345, None, {"x": 1}, ["a"]])
def test_non_string_title_drops_only_that_hit(title):
    try:
        r = _search([_hit(1), _hit(2, title=title), _hit(3)])
    except Exception as e:
        pytest.fail("F9-TITLE: %s title %r failed the whole site search: %s"
                    % (type(title).__name__, title, type(e).__name__))
    assert r.ok, r.error
    urls = [h.url for h in r.hits]
    assert urls == ["https://www.example.com/video/1",
                    "https://www.example.com/video/3"], (
        "F9-TITLE: expected only the bad-title hit dropped: %r" % urls)
    assert all(isinstance(h.title, str) for h in r.hits)


@pytest.mark.parametrize("thumb", [
    "javascript:alert(1)", "JAVASCRIPT:alert(1)", " javascript:alert(1)",
    "java\tscript:alert(1)", "data:image/png;base64,AAAA", "vbscript:x",
    "file:///etc/passwd", 7, None])
def test_thumbnail_non_http_scheme_dropped_hit_kept(thumb):
    r = _search([_hit(1, thumb=thumb)])
    assert r.ok and len(r.hits) == 1, (r.ok, r.error, r.hits)
    assert r.hits[0].thumbnail_url == "", (
        "F9-THUMB-SCHEME: %r kept as thumbnail_url" % (thumb,))


def test_thumbnail_over_cap_dropped():
    big = "https://cdn.example.com/t.jpg?" + "a" * 5000
    data = "data:image/png;base64," + "A" * 200000
    r = _search([_hit(1, thumb=big), _hit(2, thumb=data)])
    assert len(r.hits) == 2, r.hits
    lens = [len(h.thumbnail_url) for h in r.hits]
    assert lens == [0, 0], "F9-THUMB-CAP: oversized thumbnail kept, lengths %r" % lens


@pytest.mark.parametrize("thumb", [
    "https://cdn.example.com/t/1.jpg", "http://cdn.example.com/1.jpg",
    "HTTPS://cdn.example.com/1.jpg", "/img/1.jpg", "//cdn.example.com/1.jpg", ""])
def test_thumbnail_http_and_relative_kept(thumb):
    r = _search([_hit(1, thumb=thumb)])
    assert [h.thumbnail_url for h in r.hits] == [thumb], (
        "F9-THUMB-CONTROL: legitimate thumbnail %r altered" % thumb)


def test_cap_boundary():
    pre = "https://cdn.example.com/"
    at = pre + "a" * (CAP - len(pre))
    r = _search([_hit(1, thumb=at), _hit(2, thumb=at + "a")])
    lens = [len(h.thumbnail_url) for h in r.hits]
    assert lens == [CAP, 0], "F9-THUMB-BOUNDARY: cap is %d, lengths %r" % (CAP, lens)


def test_hostile_hit_urls_still_filtered():
    r = _search([
        {"url": "javascript:alert(1)//example.com/video/1", "title": "js", "thumb": ""},
        {"url": "data:text/html,<b>x</b>/video/2", "title": "data", "thumb": ""},
        {"url": "https://evil.test/video/3", "title": "off", "thumb": ""},
        _hit(4)])
    assert [h.url for h in r.hits] == ["https://www.example.com/video/4"], (
        "F9-CONTROL: hostile hit URL admitted: %r" % [h.url for h in r.hits])
