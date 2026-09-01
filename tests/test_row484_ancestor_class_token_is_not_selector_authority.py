"""Row 484 -- a class token on an ANCESTOR is not authority over the leaf.

``_selector_direct_authority`` declared a selector "precise" whenever an
unescaped ``.``/``#`` token appeared ANYWHERE in the masked selector.  The
token's position in the compound chain was never examined, so a class-scoped
but structurally broad descendant selector such as ``ul.navigation li a`` was
treated as precise: ``_learned_candidate_requires_signal`` returned False, the
learned loop skipped both the own-affordance gate and the media-evidence clause
of ``_candidate_is_rankable``, and the site-navigation chrome anchors of the
row-399 scene fixture were ranked as download candidates.  The learned fast
path then RETURNED one of them before the wide sweep ever ran, so the four real
``a.ct_dl_button`` tier controls on the same page were never reached.

CONTRACT: a class or id token waives per-element media evidence only when it
constrains the element the selector actually RETURNS.  A token naming only an
ancestor scope decides nothing, and an undecidable selector stays conservative.
"""

BD_GATE_SCOPE = "module"

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from bulk_downloader.detect import (
    _learned_candidate_requires_signal,
    _selector_branch_has_authority,
    find_best_download,
)

_REPO = Path(__file__).resolve().parents[1]

# The row-399 scene shape: five visible chrome anchors under a class-scoped
# navigation block, and four real download controls elsewhere on the page.
_NAV = """
<section class="ps_main_menu">
  <section><div class="main_menu"><ul class="navigation">
    <li class="mi_films_4K"><a href="/films-4K/">4K</a></li>
    <li class="mi_films_5K"><a href="/films-5K/">5K</a></li>
    <li class="mi_films_6K"><a href="/films-6K/">6K</a></li>
    <li class="mi_girls"><a href="/models/">Models</a></li>
    <li class="mi_home"><a href="/">Home</a></li>
  </ul></div></section>
</section>
"""

_SCENE = f"""<!doctype html><html><body>
{_NAV}
<div class="content_download video_downloads">
  <div class="row">
    <a class="ct_dl_button" href="https://cdn.example/dl/1280x720_60FPS.mp4">1280 x 720</a>
    <div class="caption">HD &bull; H264 &bull; 60fps &bull; 395MB</div>
  </div>
  <div class="row">
    <a class="ct_dl_button" href="https://cdn.example/dl/1920x1080_60FPS.mp4">1920 x 1080</a>
    <div class="caption">FHD &bull; H264 &bull; 60fps &bull; 1.99GB</div>
  </div>
  <div class="row">
    <a class="ct_dl_button" href="https://cdn.example/dl/3840x2160_60FPS.mp4">3840 x 2160</a>
    <div class="caption">4K &bull; H264 &bull; 60fps &bull; 3.44GB</div>
  </div>
  <div class="row">
    <a class="ct_dl_button" href="https://cdn.example/dl/7680x4320_60FPS.mp4">7680 x 4320</a>
    <div class="caption">8K &bull; HEVC &bull; 60fps &bull; 3.01GB</div>
  </div>
</div>
</body></html>"""

_OPAQUE = """<!doctype html><html><body><main>
  <a class="opaque-control" data-signed-url-key="opaque-asset-token">
    Original
  </a>
</main></body></html>"""

# Every top-level branch whose only class/id token names an ANCESTOR scope.
_ANCESTOR_SCOPED = ["ul.navigation li a", ".navigation a", ".ps_main_menu a"]


@contextmanager
def _page(html):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api").sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_content(html, wait_until="load")
            yield page
        finally:
            browser.close()


def test_precondition_the_fixture_carries_both_populations():
    """The seam is nonzero: chrome the selector reaches, controls it does not."""
    with _page(_SCENE) as page:
        chrome = page.locator("ul.navigation li a")
        assert chrome.count() == 5, "the ancestor-scoped selector lost its subject"
        assert all(chrome.nth(i).is_visible() for i in range(5))
        controls = page.locator("a.ct_dl_button")
        assert controls.count() == 4, "the four real tier controls are missing"
        # The controls are NOT reachable by the taught selector, so a learned
        # hit on this selector can only be chrome.
        assert page.locator("ul.navigation li a.ct_dl_button").count() == 0

        # The wide sweep on the same page finds the real 8K control.
        wide = find_best_download(page, learned=None)
        assert wide is not None
        assert wide["score"] == 4325, wide["score"]
        assert "7680x4320" in (wide["locator"].get_attribute("href") or "")


@pytest.mark.parametrize("selector", _ANCESTOR_SCOPED)
def test_ancestor_scoped_class_token_does_not_waive_media_evidence(selector):
    """RED on the defective parent: chrome is returned as a learned hit."""
    with _page(_SCENE) as page:
        matched = page.locator(selector)
        assert matched.count() == 5, (selector, matched.count())

        # The seam itself: every element this selector returns must still be
        # asked for its own media evidence.
        signals = [
            _learned_candidate_requires_signal(matched.nth(i), selector)
            for i in range(5)
        ]
        assert signals == [True] * 5, (
            f"{selector!r} waived per-element evidence for "
            f"{signals.count(False)} of 5 chrome anchors")

        best = find_best_download(page, learned={"row_selectors": [selector]})
        assert best is not None, "the page's real controls were all dropped"
        assert not best.get("_via_learned"), (
            f"{selector!r} produced a learned hit: "
            f"{best.get('text')!r} score={best.get('score')}")
        href = best["locator"].get_attribute("href") or ""
        assert "7680x4320" in href, href
        assert "/films-" not in href and "/models/" not in href, href


def test_negative_control_precise_leaf_class_token_keeps_authority():
    """A class token ON the returned element still waives media evidence.

    This is the discriminating control: ``a.opaque-control`` matches no
    authority word, its harvested text scores res_score -1, and
    ``_candidate_is_rankable`` with require_signal True refuses it -- so
    deleting class-token authority wholesale turns this RED.
    """
    with _page(_OPAQUE) as page:
        element = page.locator("a.opaque-control")
        assert element.count() == 1
        assert _learned_candidate_requires_signal(
            element.first, "a.opaque-control") is False

        best = find_best_download(
            page, learned={"row_selectors": ["a.opaque-control"]})
        assert best is not None, "the precise taught control was dropped"
        assert best.get("_via_learned") is True
        assert best["locator"].get_attribute(
            "data-signed-url-key") == "opaque-asset-token"


def test_negative_control_reviewed_row_selectors_keep_their_verdicts():
    """The operator's reviewed selectors must not change meaning.

    Denominator is read from the tracked reviewed templates rather than
    retyped here, and asserted nonzero before any verdict.
    """
    reviewed = sorted((_REPO / "templates" / "reviewed").glob("*.json"))
    assert len(reviewed) == 2, [p.name for p in reviewed]
    selectors = []
    for path in reviewed:
        stack = [json.loads(path.read_text())]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "row_selectors" and isinstance(value, list):
                        selectors.extend(
                            item for item in value if isinstance(item, str))
                    else:
                        stack.append(value)
            elif isinstance(node, list):
                stack.extend(node)
    assert len(selectors) == 5, selectors
    verdicts = {sel: _selector_branch_has_authority(sel) for sel in selectors}
    assert all(verdicts.values()), (
        "a reviewed selector lost its authority: "
        f"{[s for s, ok in verdicts.items() if not ok]}")
