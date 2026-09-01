"""Row 499 -- an HTML5 ``header``/``footer`` inside a card is not site chrome.

``_has_navigation_ancestor`` asked the browser for ``e.closest`` over
``nav, header, footer, [role=navigation]`` plus three class tokens.  But
``header`` and ``footer`` are SECTIONING content: valid and common inside
``article``, ``section`` and ``aside``, where they scope a CARD and not the
site.  Every download control sitting in a scene card's own header or footer
was therefore judged navigation chrome and deleted -- silently, with no
counter, event or log line, so the operator saw only "No download button
found" while ``selector_drift`` recorded template breakage.

The partial case is worse than the total one: if only the TOP tier sits inside
a card header, BD downloads a lower tier and reports ``done`` -- a wrong file
under a correct-looking record, the 2026-08-29 shape in CLAUDE.md A7.

CONTRACT: a ``header``/``footer`` proves site chrome only when it is a
DOCUMENT-LEVEL landmark (no ``article``/``aside``/``main``/``nav``/``section``
between it and the root); and a run whose chrome filter deleted candidates
emits exactly one summary carrying an exact nonzero count, so a silent chrome
deletion reads UNKNOWN rather than a clean no-candidates verdict.
"""

BD_GATE_SCOPE = "module"

from contextlib import contextmanager

import pytest

from bulk_downloader.detect import (
    _DL_WORD_RE,
    find_best_download,
    parse_size_bytes,
    res_score,
    work_affinity,
)
from bulk_downloader import candidate_filter

_LABEL = "6K"
_HREF = "/quality/6K"
_CONTROL = f'<a class="q" href="{_HREF}">{_LABEL}</a>'

# The four arms differ in EXACTLY one token: the wrapper element name.
_WRAPPERS = ["div", "section", "header", "footer"]


def _arm(wrapper):
    return (
        '<!doctype html><html><body>'
        '<article class="scene">'
        f'<{wrapper} class="scene-head">{_CONTROL}</{wrapper}>'
        '</article></body></html>'
    )


# Negative controls -----------------------------------------------------
_BODY_LEVEL_NAV = (
    '<!doctype html><html><body><nav class="site">'
    f'{_CONTROL}</nav></body></html>')
_DOCUMENT_LEVEL_HEADER = (
    '<!doctype html><html><body><header class="ant-layout-header">'
    f'{_CONTROL}</header></body></html>')
_NESTED_CARD_HEADER_INSIDE_SITE_HEADER = (
    '<!doctype html><html><body><header class="ant-layout-header">'
    '<article class="promo"><header class="card-head">'
    f'{_CONTROL}</header></article></header></body></html>')

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

_GALLERY = f"""<!doctype html><html class="js video no-touchevents"><body>
{_NAV}
<main class="gallery">
  <h1>Stunned By Each Other</h1>
  <a href="/gallery/x15b9ab4/photo/1"><img src="/img/a.jpg" alt="still one"></a>
  <a href="/gallery/x15b9ab4/photo/2"><img src="/img/b.jpg" alt="still two"></a>
  <a href="/gallery/x15b9ab4/photo/3"><img src="/img/c.jpg" alt="still three"></a>
</main></body></html>"""

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


class _RecordingRunner:
    def __init__(self):
        self.events = []

    def log_event(self, kind, message, **kwargs):
        self.events.append({"kind": kind, "message": message,
                            "extra": kwargs.get("extra")})


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


def _summaries(runner):
    return [e for e in runner.events
            if e["kind"] == "candidate_admission_filtered"]


def test_precondition_every_earlier_fail_open_is_proven_not_to_decide():
    """No arm can be decided by anything except the wrapper element name."""
    assert res_score(_LABEL) == 3160
    assert res_score(_LABEL) >= 0
    assert parse_size_bytes(_LABEL) == 0
    assert _DL_WORD_RE.search(_LABEL) is None
    assert not work_affinity("about:blank", _HREF)
    strong = {"media_extension", "manifest_url", "download_path",
              "api_pattern"}
    assert not strong.intersection(
        candidate_filter.positive_signals(_HREF, f"{_LABEL} {_HREF}", "")), (
        "the href already carries a strong positive signal, so the ghost "
        "filter would fail open before the ancestry branch is reached")

    for wrapper in _WRAPPERS:
        with _page(_arm(wrapper)) as page:
            control = page.locator("a.q")
            assert control.count() == 1, wrapper
            assert control.first.is_visible(), wrapper
            assert control.first.get_attribute("href") == _HREF, wrapper
            assert control.first.inner_text().strip() == _LABEL, wrapper
            assert page.locator("article.scene").count() == 1, wrapper


@pytest.mark.parametrize("wrapper", _WRAPPERS)
@pytest.mark.parametrize("learned", [None, {"row_selectors": ["a"]}],
                         ids=["wide", "broad-learned"])
def test_a_card_scoped_sectioning_wrapper_keeps_its_control(wrapper, learned):
    """RED on the defective parent: only div and section survive."""
    with _page(_arm(wrapper)) as page:
        best = find_best_download(page, learned=learned)
        assert best is not None, (
            f"<{wrapper}> inside <article> deleted the only control")
        assert best["score"] == 3160, best["score"]
        assert best["locator"].get_attribute("href") == _HREF
        assert len(best.get("_all_candidates") or []) == 1, (
            best.get("_all_candidates"))


def test_a_chrome_deletion_is_reported_with_an_exact_nonzero_count():
    """RED on the defective parent: the deletion is completely silent."""
    runner = _RecordingRunner()
    with _page(_GALLERY) as page:
        best = find_best_download(page, learned=None, runner=runner)
    assert best is None, "the gallery fixture stopped reproducing"
    summaries = _summaries(runner)
    assert len(summaries) == 1, runner.events
    extra = summaries[0]["extra"] or {}
    assert extra.get("chrome_ghost", 0) >= 1, extra
    assert extra.get("count", 0) == sum(
        value for key, value in extra.items()
        if key != "count" and isinstance(value, int)), extra
    assert "candidate_admission_filtered" in summaries[0]["message"]
    assert f"count={extra['count']}" in summaries[0]["message"]


def test_a_run_with_no_filtered_candidate_stays_silent(capsys):
    """The summary must never fire on a clean page."""
    runner = _RecordingRunner()
    with _page(_arm("div")) as page:
        best = find_best_download(page, learned=None, runner=runner)
    assert best is not None
    assert _summaries(runner) == []
    assert "candidate_admission_filtered" not in capsys.readouterr().err


def test_negative_control_body_level_nav_is_still_chrome():
    with _page(_BODY_LEVEL_NAV) as page:
        assert page.locator("body > nav").count() == 1
        assert find_best_download(page, learned=None) is None
        assert find_best_download(
            page, learned={"row_selectors": ["a"]}) is None


def test_negative_control_document_level_header_is_still_chrome():
    """The reptyle ``ant-layout-header`` shape: a direct child of body."""
    with _page(_DOCUMENT_LEVEL_HEADER) as page:
        assert page.locator("body > header.ant-layout-header").count() == 1
        assert page.locator(
            "header :is(article,aside,main,nav,section)").count() == 0
        assert find_best_download(page, learned=None) is None
        assert find_best_download(
            page, learned={"row_selectors": ["a"]}) is None


def test_negative_control_a_card_header_inside_site_chrome_is_still_chrome():
    """Nesting a card header inside the site header must not launder it."""
    with _page(_NESTED_CARD_HEADER_INSIDE_SITE_HEADER) as page:
        assert page.locator("header.ant-layout-header header.card-head"
                            ).count() == 1
        assert find_best_download(page, learned=None) is None


def test_negative_control_the_row399_gallery_still_returns_none():
    with _page(_GALLERY) as page:
        assert page.locator(
            "nav,header,footer,[role='navigation']").count() == 0, (
            "the fixture must reach the class-token branch, not header/footer")
        assert page.locator(".ps_main_menu .main_menu ul.navigation a"
                            ).count() == 5
        assert find_best_download(page, learned=None) is None
        assert find_best_download(
            page, learned={"row_selectors": ["a"]}) is None


@pytest.mark.parametrize("learned", [None, {"row_selectors": ["a"]}],
                         ids=["wide", "broad-learned"])
def test_negative_control_the_scene_keeps_exactly_four_tiers(learned):
    with _page(_SCENE) as page:
        best = find_best_download(page, learned=learned)
        assert best is not None
        hrefs = [
            (c["locator"].get_attribute("href") or "")
            for c in (best.get("_all_candidates") or [])
        ]
        media = [href for href in hrefs if href.endswith(".mp4")]
        assert len(media) == 4, media
        assert all("/films-" not in href for href in hrefs), hrefs
        assert "7680x4320" in (best["locator"].get_attribute("href") or "")


_CHROME_PLUS_REAL_CONTROL = (
    '<!doctype html><html><body>'
    f'<nav class="site">{_CONTROL}</nav>'
    '<main><a class="tier" href="https://cdn.example/dl/2160p.mp4">2160p</a>'
    '</main></body></html>')


def test_a_summary_is_emitted_on_the_learned_hit_exit_too():
    """The report must not depend on WHICH exit the call takes.

    A learned hit returns before the wide sweep, so a counter emitted only at
    the end of the wide sweep would reproduce the silence it was written to
    prevent on exactly the path the operator's own teaching takes.
    """
    runner = _RecordingRunner()
    with _page(_CHROME_PLUS_REAL_CONTROL) as page:
        assert page.locator("nav.site a.q").count() == 1
        assert page.locator("main a.tier").count() == 1
        best = find_best_download(
            page, learned={"row_selectors": ["a"]}, runner=runner)
        assert best is not None
        assert best.get("_via_learned") is True, best
        assert best["locator"].get_attribute("href") == \
            "https://cdn.example/dl/2160p.mp4"
    summaries = _summaries(runner)
    assert len(summaries) == 1, runner.events
    extra = summaries[0]["extra"] or {}
    assert extra.get("chrome_ghost") == 1, extra
    assert extra.get("count") == 1, extra
