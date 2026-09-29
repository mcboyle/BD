"""dl95-eporner-2: "Finding download button..." took 8 min 04 s on one scene.

MEASURED on test2 2026-09-29 (evidence
``harness-work/UIUX-20260928/download-95/B6-B/p1/eporner/stages.json``): job
0c546602 on https://www.eporner.com/video-tipTUUUwIEI/... entered "Finding
download button..." at 00:34:47Z and clicked at 00:42:51Z.  The test2 journal
for that window (``harness-work/FIX/dl95-eporner-2-bd-worker-B13-B/
journal-test2-003430-004310.txt``) carries no line for the job between
00:34:40Z and 00:42:40Z; the next is the ``candidate_admission_filtered:
count=2 chrome_ghost=2`` summary that ``find_best_download`` emits on exit.

THE MECHANISM IS THE WIDE SWEEP'S PER-ELEMENT HARVEST.  Stage 2 of
``_find_best_download`` walks every ``a``, ``button``, ``[onclick]``,
``[data-src]``... on the page and, for EACH element, calls ``inner_text()``
and ``get_attribute()`` for all 24 ``_WIDE_SCAN_ATTRS`` -- 25 Playwright round
trips -- only to discard the element when neither the download-word nor the
resolution regex matches.  The captured scene page (226 anchors) replayed
offline on base bb4817ae cost 10 315 such round trips and 25 s on an idle hub
(``.../MEASURE-base-roundtrips.txt``); on test2 the same walk shared one
browser process with 16-20 running jobs.

The fixture below re-hosts eporner's download-block shape on ``.example`` and
pads it with plain related-scene links.  The contract: the sweep's per-element
DOM round trips must track the CANDIDATES on the page, not its link count, and
the winner must not change.
"""

# The gate parses a module-level ASSIGNMENT, not a docstring line.
BD_GATE_SCOPE = "module"

from contextlib import contextmanager

import pytest

from bulk_downloader import detect

_PAGE = "https://www.eporner.example/video-tipTUUUwIEI/best-scene/"

# eporner's download block, as served (the dload hrefs and label shapes of
# the captured page; sizes as recorded).
_DOWNLOADS = (
    '<div id="downloaddiv"><span class="download-av1">'
    '<a href="/dload/tipTUUUwIEI/480/17592192-480p-av1.mp4">'
    'Download MP4 (480p, AV1, 52.10 MB)</a></span>'
    '<span class="download-h264"> or '
    '<a href="/dload/tipTUUUwIEI/480/17592192-480p.mp4">'
    ' MP4 (480p, h264, 90.02 MB)</a></span><br>'
    '<span class="download-av1">'
    '<a href="/dload/tipTUUUwIEI/1080/17592192-1080p-av1.mp4">'
    'Download MP4 (1080p, AV1, 106.28 MB)</a></span>'
    '<span class="download-h264"> or '
    '<a href="/dload/tipTUUUwIEI/1080/17592192-1080p.mp4">'
    ' MP4 (1080p, h264, 264.66 MB)</a></span></div>'
)


def _scene_page(n_related):
    related = "".join(
        f'<li><a href="/video-rel{i:04d}/related-scene-{i}/" '
        f'title="Related scene {i}">Related scene {i}</a></li>'
        for i in range(n_related))
    return ("<!doctype html><html><body><h1>Best scene</h1>"
            + _DOWNLOADS + "<ul>" + related + "</ul></body></html>")


@contextmanager
def _page(html):
    """Serve the fixture at its own URL; every other request is refused, so
    nothing can reach a real host."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            pg = br.new_page()
            pg.route("**/*", lambda route: route.fulfill(
                status=200, content_type="text/html", body=html)
                if route.request.url == _PAGE else route.abort())
            pg.goto(_PAGE, wait_until="load")
            assert pg.url == _PAGE, (
                "route interception did not serve the fixture at its own URL")
            yield pg
        finally:
            br.close()


def _sweep(monkeypatch, n_related):
    """Run find_best_download; return (winner href, candidate texts, per-element
    DOM round trips).  The winner is read while its page is still open."""
    from playwright.sync_api import Locator
    calls = {"n": 0}
    for name in ("inner_text", "get_attribute"):
        orig = getattr(Locator, name)

        def counted(self, *a, _orig=orig, **k):
            calls["n"] += 1
            return _orig(self, *a, **k)
        monkeypatch.setattr(Locator, name, counted)
    with _page(_scene_page(n_related)) as pg:
        best = detect.find_best_download(pg)
        n = calls["n"]
        monkeypatch.undo()
        href = best["locator"].get_attribute("href") if best else None
        texts = {c["text"] for c in (best or {}).get("_all_candidates") or []}
    return href, texts, n


def test_sweep_round_trips_do_not_scale_with_non_candidate_links(monkeypatch):
    href_small, _t, small = _sweep(monkeypatch, 50)
    href_big, _t, big = _sweep(monkeypatch, 350)
    extra = big - small
    # 300 more links that are not candidates.  Base pays ~25 round trips per
    # link per selector that matches it (7 500+); one per link is the ceiling.
    assert extra < 300, (
        f"DL95-EP2: the wide sweep made {extra} more per-element DOM round "
        f"trips for 300 more non-candidate links ({small} at 50 links, {big} "
        "at 350) -- harvest cost scales with the page, not its candidates")
    assert href_small and href_big


@pytest.mark.parametrize("n_related", [0, 200])
def test_the_winner_is_the_scene_s_own_best_tier(monkeypatch, n_related):
    """Control: the harvest change must not move the choice.  Holds on base."""
    href, texts, _n = _sweep(monkeypatch, n_related)
    assert href == "/dload/tipTUUUwIEI/1080/17592192-1080p.mp4", (
        f"DL95-EP2 control: winner moved to {href!r} ({texts!r})")
    assert not any("Related scene" in t for t in texts), texts


def test_a_candidate_only_an_attribute_reveals_is_still_found(monkeypatch):
    """Control: the filter still reads attributes, not just visible text."""
    html = ("<!doctype html><html><body><h1>Best scene</h1>"
            '<button data-quality="2160p" data-href="/dload/x/2160/x.mp4">'
            "Get</button>"
            + "".join(f'<a href="/video-r{i}/">Related scene {i}</a>'
                      for i in range(20))
            + "</body></html>")
    with _page(html) as pg:
        best = detect.find_best_download(pg)
        assert best, "DL95-EP2 control: attribute-only 2160p control was lost"
        assert best["locator"].get_attribute("data-quality") == "2160p"
