"""v3.66.276 — robust row selector (auto-scope) + auto-detect row groups.

Two related capture-frontier improvements, RED-first:

PART 1 — robust picked row selector
  1a. element_pick.bdPickSelector auto-SCOPES the group_selector to the visible
      responsive container when group_count > group_visible (lg+md+mob inflation),
      instead of only surfacing the raw/visible counts. Falls back to the bare
      selector + group_scoped=false when no stable ancestor scopes cleanly.
  1b. detect.find_best_download's learned fast-path caps at 30 *visible* rows
      scored, not 30 *raw* iterations — so a back-loaded visible block (the
      responsive-duplicate hidden block coming first in DOM order) is not missed.

PART 2 — auto-detect row groups (no operator click)
  2a. element_pick.AUTO_ROW_GROUPS_JS (bdAutoRowGroups) ranks the dominant
      repeating, visible, download-shaped tile signature on a live page and
      returns ranked candidates (scoped via the Part-1 logic). Heuristic +
      operator-gated: it RECOMMENDS, it never promotes.
  2b. build_template_from_wacz._generic_row_selectors_from_html derives the same
      dominant-repeating-tile candidate OFFLINE from captured HTML, as a
      setdefault fallback when no modal/timeline rows were derived.

Sandbox: Part 1a/2a run in Playwright headless against DOM fixtures (pure in-page
JS, no network — set_content is fine). 1b builds a fixture page and drives the
real find_best_download. 2b is pure-Python against an HTML string. The LIVE
auto-stage wiring (SPA + capture bridge) only proves on stash.
"""

import os
import tempfile
from contextlib import contextmanager

CHROME = os.environ.get(
    "BD_PW_CHROME",
    "/home/claude/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome",
)


def _launch(p):
    import os as _os
    _args = ["--no-sandbox", "--disable-dev-shm-usage"]
    if CHROME and _os.path.exists(CHROME):
        try:
            return p.chromium.launch(headless=True, timeout=20000, args=_args,
                                     executable_path=CHROME)
        except Exception:
            pass
    try:
        return p.chromium.launch(headless=True, timeout=20000, args=_args)
    except Exception as e:
        import pytest
        pytest.skip(f"chromium not launchable here: {e}")


@contextmanager
def _pw_page(html=None):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        br = _launch(p)
        try:
            pg = br.new_page(viewport={"width": 1000, "height": 760})
            if html is not None:
                pg.set_content(html, wait_until="load")
            yield pg
        finally:
            br.close()


def _derive(html, test_sel):
    from bulk_downloader.element_pick import PICK_SELECTOR_JS
    with _pw_page(html) as pg:
        return pg.evaluate(
            """(ts) => {
                %s
                const el = document.querySelector(ts);
                if (!el) throw new Error('test hook not found: ' + ts);
                el.removeAttribute('data-test');
                return bdPickSelector(el);
            }""" % PICK_SELECTOR_JS,
            test_sel,
        )


def _auto(html):
    from bulk_downloader.element_pick import PICK_SELECTOR_JS, AUTO_ROW_GROUPS_JS
    with _pw_page(html) as pg:
        return pg.evaluate(
            """() => {
                %s
                %s
                return bdAutoRowGroups(document, {max: 5});
            }""" % (PICK_SELECTOR_JS, AUTO_ROW_GROUPS_JS),
        )


def _matches(html, sel):
    with _pw_page(html) as pg:
        return pg.eval_on_selector_all(sel, "els => els.length")


# ── PART 1a: responsive-inflated grid ──────────────────────────────────────
# Three responsive wrappers each render the SAME 3-tile grid; only the lg
# wrapper is visible (md/mob are display:none, the classic lg+md+mob pattern).
# 9 raw `a.tile`, 3 visible. The nearest stable scoping ancestor is .view-lg.
_INFLATED = """
<!doctype html><html><body>
  <div class="grid-view view-lg">
    <a class="tile" href="/v/1" data-test="lg1">1080p clip one</a>
    <a class="tile" href="/v/2">720p clip two</a>
    <a class="tile" href="/v/3">480p clip three</a>
  </div>
  <div class="grid-view view-md" style="display:none">
    <a class="tile" href="/v/1">1080p clip one</a>
    <a class="tile" href="/v/2">720p clip two</a>
    <a class="tile" href="/v/3">480p clip three</a>
  </div>
  <div class="grid-view view-mob" style="display:none">
    <a class="tile" href="/v/1">1080p clip one</a>
    <a class="tile" href="/v/2">720p clip two</a>
    <a class="tile" href="/v/3">480p clip three</a>
  </div>
</body></html>
"""

# A single grid, no responsive duplication → group_selector stays the bare one.
_SINGLE = """
<!doctype html><html><body>
  <div class="cards">
    <a class="card" href="/v/1" data-test="c1">1080p one</a>
    <a class="card" href="/v/2">720p two</a>
    <a class="card" href="/v/3">480p three</a>
  </div>
</body></html>
"""


def test_pick_autoscopes_group_selector_to_visible_container():
    r = _derive(_INFLATED, "[data-test='lg1']")
    assert r["group_count"] == 9, r          # raw still reported
    assert r["group_visible"] == 3, r        # visible still reported
    assert r["group_scoped"] is True, r      # auto-scoped (inflation present)
    gsel = r["group_selector"]
    # the scoped selector now matches exactly the visible 3, not the inflated 9
    assert _matches(_INFLATED, gsel) == 3, (gsel, _matches(_INFLATED, gsel))
    # and it is genuinely scoped to the visible wrapper
    assert "view-lg" in gsel, gsel
    assert gsel.endswith("a.tile"), gsel


def test_pick_no_inflation_keeps_bare_group_selector():
    r = _derive(_SINGLE, "[data-test='c1']")
    assert r["group_count"] == 3, r
    assert r["group_visible"] == 3, r
    # no inflation → scoping not applicable; bare repeating selector retained
    assert r["group_scoped"] in (None, False), r
    assert r["group_selector"] == "a.card", r["group_selector"]
    assert _matches(_SINGLE, r["group_selector"]) == 3


def test_pick_reports_raw_group_selector_for_review():
    # The unscoped signature is always available so the SPA can show
    # "matched N / visible M" even after auto-scoping.
    r = _derive(_INFLATED, "[data-test='lg1']")
    assert r.get("group_raw_selector") == "a.tile", r.get("group_raw_selector")


# ── PART 1b: visibility-gated cap in the learned fast path ──────────────────
# 40 hidden a.row come FIRST in DOM order, then 3 VISIBLE a.row carrying real
# resolution text. The old cap (min(count,30) RAW) scans only the 30 hidden and
# scores nothing → no learned hit. The visibility-gated cap finds the visible 3.
def _backloaded_html():
    hidden = "".join(
        '<a class="row" href="/h/%d" style="display:none">hidden %d</a>' % (i, i)
        for i in range(40)
    )
    visible = "".join(
        '<a class="row" href="/v/%d">%dp download</a>' % (i, r)
        for i, r in enumerate((1080, 720, 480))
    )
    return "<!doctype html><html><body><div id='grid'>%s%s</div></body></html>" % (
        hidden, visible)


def test_learned_fastpath_finds_backloaded_visible_rows():
    from bulk_downloader.detect import find_best_download
    learned = {"row_selectors": ["a.row"]}
    with _pw_page(_backloaded_html()) as pg:
        best = find_best_download(pg, "", learned=learned)
    assert best is not None, "back-loaded visible rows must still be found"
    assert best.get("_via_learned") is True, best
    # the winner is a real (visible, resolution-bearing) row, not a hidden decoy
    assert "download" in (best.get("text") or "").lower(), best


# ── PART 2a: bdAutoRowGroups live ranking ──────────────────────────────────
# A realistic page: site chrome (nav) + a download-shaped tile grid + a
# non-download repeating sidebar menu. The detector must rank the dl-shaped grid
# top and must NOT rank the one-off header or treat the menu as the row group.
_MIXED = """
<!doctype html><html><body>
  <nav class="site-nav"><a class="nav-link" href="/">Home</a>
    <a class="nav-link" href="/browse">Browse</a></nav>
  <h1 class="page-title" data-test="title">My Videos</h1>
  <main class="results-grid">
    <a class="video-tile" href="/dl/1.mp4">Scene 1 — 1080p</a>
    <a class="video-tile" href="/dl/2.mp4">Scene 2 — 1080p</a>
    <a class="video-tile" href="/dl/3.mp4">Scene 3 — 720p</a>
    <a class="video-tile" href="/dl/4.mp4">Scene 4 — 720p</a>
    <a class="video-tile" href="/dl/5.mp4">Scene 5 — 480p</a>
  </main>
  <aside class="sidebar">
    <ul><li class="menu-item"><a href="/c/1">Category A</a></li>
        <li class="menu-item"><a href="/c/2">Category B</a></li>
        <li class="menu-item"><a href="/c/3">Category C</a></li>
        <li class="menu-item"><a href="/c/4">Category D</a></li></ul>
  </aside>
</body></html>
"""


def test_autodetect_ranks_download_grid_top():
    groups = _auto(_MIXED)
    assert isinstance(groups, list) and groups, groups
    top = groups[0]
    # the dl-shaped repeating tile wins
    assert "video-tile" in top["selector"], top
    assert top["visible"] == 5, top
    assert top["has_dl_shape"] is True, top


def test_autodetect_does_not_rank_one_off_or_pure_chrome_as_rows():
    groups = _auto(_MIXED)
    sels = " ".join(g["selector"] for g in groups)
    # a single <h1> is not a repeating row group
    assert "page-title" not in sels, sels
    # a non-download repeating menu may appear but must rank BELOW the dl grid;
    # the dl grid is strictly first
    assert "video-tile" in groups[0]["selector"], groups


def test_autodetect_returns_empty_on_no_repeating_group():
    html = ("<!doctype html><html><body><h1>Only</h1>"
            "<p class='lead'>one of everything</p></body></html>")
    groups = _auto(html)
    assert groups == [], groups


# ── PART 2b: offline generic-grid derivation in the builder ─────────────────
def test_builder_generic_row_selectors_from_html():
    from tools.build_template_from_wacz import _generic_row_selectors_from_html
    html = """
    <div class="cards">
      <a class="card" href="/v/1.mp4">1080p one</a>
      <a class="card" href="/v/2.mp4">720p two</a>
      <a class="card" href="/v/3.mp4">480p three</a>
    </div>
    <nav><a class="nl" href="/">Home</a></nav>
    """
    sels = _generic_row_selectors_from_html(html)
    assert isinstance(sels, list) and sels, sels
    # the dominant repeating download-shaped tile, scoped to its container
    assert any("card" in s for s in sels), sels
    # the one-off nav link is not a row group
    assert not any(s.strip() == "a.nl" for s in sels), sels


def test_builder_generic_rows_ignored_when_no_repeat():
    from tools.build_template_from_wacz import _generic_row_selectors_from_html
    html = "<div><a class='solo' href='/x.mp4'>only 1080p</a></div>"
    assert _generic_row_selectors_from_html(html) == []


# ── PART 2 live bridge: AUTO_ROW sentinel + suggest_rows_capture ────────────
def test_autorow_sentinel_roundtrip():
    import json as _j
    from bulk_downloader import element_pick as ep
    d = tempfile.mkdtemp()
    assert ep.consume_autorows(d) is None            # nothing yet
    assert ep.request_autorows(d) is True
    assert ep.autorow_req_path(d).exists()
    # capture writes a result
    ep.autorow_result_path(d).write_text(
        _j.dumps([{"selector": "a.tile", "count": 9, "visible": 3,
                   "has_dl_shape": True, "score": 1003}]), encoding="utf-8")
    got = ep.consume_autorows(d)
    assert got and got[0]["selector"] == "a.tile", got
    assert ep.consume_autorows(d) is None            # read-and-delete


class _FakePage:
    def __init__(self, groups):
        self._groups = groups
    def evaluate(self, *_a, **_k):
        return self._groups


def test_maybe_suggest_rows_services_request():
    from bulk_downloader import element_pick as ep
    d = tempfile.mkdtemp()
    ep.request_autorows(d)
    groups = [{"selector": "main.results-grid a.video-tile", "count": 5,
               "visible": 5, "has_dl_shape": True, "score": 1005}]
    out = ep.maybe_suggest_rows([_FakePage(groups)], d)
    assert out == groups, out
    assert not ep.autorow_req_path(d).exists()       # request cleared
    assert ep.autorow_result_path(d).exists()        # result staged


def test_maybe_suggest_rows_noop_without_request():
    from bulk_downloader import element_pick as ep
    d = tempfile.mkdtemp()
    assert ep.maybe_suggest_rows([_FakePage([{"selector": "x"}])], d) is None
    assert not ep.autorow_result_path(d).exists()


def test_suggest_rows_capture_arm_and_poll():
    import json as _j
    import tools.cockpit_core as cc
    d = tempfile.mkdtemp()
    _orig = cc.get_task
    try:
        cc.get_task = lambda tid: {"category": "capture", "out_dir": d}
        armed = cc.suggest_rows_capture("t1", action="arm")
        assert armed["requested"] is True, armed
        # before a result lands, poll is empty
        assert cc.suggest_rows_capture("t1", action="poll")["groups"] is None
        # capture writes a result
        from bulk_downloader import element_pick as ep
        ep.autorow_result_path(d).write_text(
            _j.dumps([{"selector": "a.tile", "count": 3, "visible": 3,
                       "has_dl_shape": True, "score": 1003}]), encoding="utf-8")
        polled = cc.suggest_rows_capture("t1", action="poll")
        assert polled["groups"] and polled["groups"][0]["selector"] == "a.tile"
    finally:
        cc.get_task = _orig


def test_suggest_rows_capture_rejects_non_capture_task():
    import tools.cockpit_core as cc
    _orig = cc.get_task
    try:
        cc.get_task = lambda tid: {"category": "download", "out_dir": "/tmp"}
        raised = False
        try:
            cc.suggest_rows_capture("t1", action="arm")
        except cc.ValidationError:
            raised = True
        assert raised
    finally:
        cc.get_task = _orig


# ── SPA wiring (source-scan; live e2e is stash-only) ───────────────────────
def _spa_src():
    import pathlib
    p = pathlib.Path(__file__).resolve().parent.parent / \
        "frontend/src/routes/CaptureWorkflow.tsx"
    return p.read_text(encoding="utf-8")


def test_spa_has_suggest_rows_button_and_endpoint():
    src = _spa_src()
    assert "Suggest rows" in src, "Suggest rows button missing"
    assert "/api/captures/suggest_rows" in src, "root endpoint not called"
    assert "suggestRows" in src, "suggestRows handler missing"


def test_spa_autoprefills_rows_once_on_session_live():
    src = _spa_src()
    assert "autoSuggestedRef" in src, "auto-prefill guard ref missing"
    assert "suggestRows(true)" in src, "auto-prefill call missing"


def test_app_exposes_suggest_rows_root_route():
    # P4 (v3.66.426+): the captures route group was extracted from
    # app.py onto the app_captures blueprint, so read the app core plus
    # every extracted app_*.py blueprint as one aggregate — the route
    # surface is the union, not app.py alone.
    import pathlib, glob
    root = pathlib.Path(__file__).resolve().parent.parent / "bulk_downloader"
    parts = [(root / "app.py").read_text(encoding="utf-8")]
    for f in sorted(glob.glob(str(root / "app_*.py"))):
        parts.append(pathlib.Path(f).read_text(encoding="utf-8"))
    app = "\n".join(parts)
    assert '"/api/captures/suggest_rows"' in app, "root route missing in app.py"
    assert "suggest_rows_capture" in app, "delegate not called from app.py"
