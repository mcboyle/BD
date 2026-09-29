"""tpl95-nubiles-porn-1 (test2, 2026-09-29 03:00-03:02Z): a user template was
applied to nubiles-porn ("User template applied: A8A nubiles-porn O1517"), yet
the run's journal showed only

  download: custom selector matched 29 option(s); picked '1920x1080 HD MP4 (5 GB) ...'

and no "learned hit via" line. The learned rows ARE tried before the site's
dl_selector, but they matched only rows inside the closed Downloads dropdown,
and hidden rows are skipped. The site's dl_selector, which takes hidden rows,
won. tpl95-porndig-1 (lane, 04:00Z) credits a learned row that names the
picked element. What was still missing:

  * a GENUINE learned miss (no learned row names the pick) was counted
    (download_misses) and never said, so a template that selected nothing
    still read "applied";
  * a hit never said whose selector won. Enabled reviewed-template rows are
    merged in ahead of the scan, so on filthykings the reviewed template's
    selector won while the status said a user template was applied.

The fix: record_learned_download_outcome prints "learned rows missed:
template=<id>: ...; won by <what>" with per-selector matched/visible/admitted
counts (detect attaches ``_learned_trace``), and every hit line names its
selector's origin.

The dropdown markup is a REAL capture of a nubiles-porn scene page
(campaign/nubiles-porn/1/page.scrubbed.html on test2; signed query tokens
replaced by st=SCRUBBED). ``.dropdown-menu{display:none}`` /
``.show{display:block}`` is Bootstrap's own rule, which the live page loads.
Rendered in local headless chromium; NO LIVE SITE IS TOUCHED.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-tpl95-np1.test"
DROPDOWN = (Path(__file__).resolve().parent / "fixtures" / "tpl95_nubiles_porn_1"
            / "scene_downloads_dropdown.html").read_text(encoding="utf-8")
BOOTSTRAP_DROPDOWN_CSS = ".dropdown-menu{display:none}.dropdown-menu.show{display:block}"

TEMPLATE_ID = "user_a8a_nubiles_porn_o1517_1790650857"
# A8-A's applied rows (download-95/A8-A/tpl-nubiles-porn/template-request.json), first three.
A8A_ROWS = ["span.dimensions", "a[href*='.mp4?st=']", "a[href*='/download/']"]
# The same template without the row that names the dropdown link: a genuine miss.
MISSING_ROWS = ["span.dimensions", "a[href*='/download/']"]
SITE_DL_SELECTOR = "a.dropdown-downloads-link"

CHROME = os.environ.get("BD_PW_CHROME", "")


def _launch(p):
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    if CHROME and os.path.exists(CHROME):
        try:
            return p.chromium.launch(headless=True, timeout=20000, args=args,
                                     executable_path=CHROME)
        except Exception:
            pass
    try:
        return p.chromium.launch(headless=True, timeout=20000, args=args)
    except Exception as e:
        pytest.fail(f"chromium not launchable here -- a launch failure is a FAILURE, not a skip (T5): {e}")


@contextmanager
def _scene(opened=False):
    from playwright.sync_api import sync_playwright
    body = DROPDOWN.replace('class="dropdown-menu dropdown-downloads"',
                            'class="dropdown-menu dropdown-downloads show"') if opened else DROPDOWN
    html = (f"<!doctype html><html><head><style>{BOOTSTRAP_DROPDOWN_CSS}</style></head>"
            f"<body><h1>Stepmom Is A Great Kisser</h1>{body}</body></html>")
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            page.route(ORIGIN + "/**", lambda route, _r: route.fulfill(
                status=200, content_type="text/html", body=html))
            page.goto(ORIGIN + "/video/watch/254861/stepmom-is-a-great-kisser", wait_until="load")
            yield page
        finally:
            browser.close()


def _config(rows):
    return {"applied_template": TEMPLATE_ID, "dl_selector": SITE_DL_SELECTOR,
            "learned": {"download": {"row_selectors": list(rows), "url_attribute": "href"}}}


def _run(rows, opened=False):
    """find_best_download then the runner's accounting, as runner.py calls them."""
    from bulk_downloader import runner_util
    from bulk_downloader.detect import find_best_download
    config = _config(rows)
    learned = dict(config["learned"]["download"])
    with _scene(opened=opened) as page:
        best = find_best_download(page, SITE_DL_SELECTOR, learned=learned, runner=None)
        runner_util.record_learned_download_outcome(config, learned, best)
    return best, config


def test_precondition_the_hidden_dropdown_rows_are_a_learned_hit_via_href():
    """tpl95-justporn-1: a closed dropdown's row that the template fetches by
    url_attribute (href) needs no click, so the learned row now wins directly
    instead of the site's dl_selector."""
    with _scene() as page:
        assert page.locator("a.dropdown-downloads-link").count() == 6
        assert not page.locator("a.dropdown-downloads-link").first.is_visible()
    best, _ = _run(A8A_ROWS)
    assert best and best.get("_via_learned"), best
    assert best.get("_learned_sel") == "a[href*='.mp4?st=']", best.get("_learned_sel")


def test_a_genuine_learned_miss_is_reported_with_the_template_and_what_won(capsys):
    best, config = _run(MISSING_ROWS)
    err = capsys.readouterr().err
    assert "learned hit via" not in err, err
    line = next((ln for ln in err.splitlines() if "learned rows missed:" in ln), "")
    assert line, err
    assert f"template={TEMPLATE_ID}" in line
    assert "[span.dimensions] matched=6 visible=0 admitted=0" in line, line
    assert "[a[href*='/download/']] matched=0 visible=0 admitted=0" in line, line
    assert f"won by custom selector [{SITE_DL_SELECTOR}]" in line, line
    assert config["learned"]["stats"]["download_misses"] == 1  # the accounting is unchanged


def test_the_credited_hit_names_the_applied_template(capsys):
    """Every hit says whose selector it is. Since tpl95-justporn-1 the closed
    dropdown's href row is a learned hit itself, not a credit to the site's pick."""
    _run(A8A_ROWS)
    err = capsys.readouterr().err
    assert ("learned hit via [a[href*='.mp4?st=']] "
            f"(applied template {TEMPLATE_ID})") in err, err
    assert "learned rows missed:" not in err


def test_positive_control_an_open_dropdown_is_a_plain_learned_hit(capsys):
    best, _ = _run(A8A_ROWS, opened=True)
    err = capsys.readouterr().err
    assert best and best.get("_via_learned"), best
    assert f"(applied template {TEMPLATE_ID})" in err and "learned rows missed:" not in err, err


def test_no_learned_rows_prints_neither_line(capsys):
    _run([])
    err = capsys.readouterr().err
    assert "learned hit via" not in err and "learned rows missed:" not in err, err


def test_a_hit_names_the_template_its_selector_came_from():
    from bulk_downloader.runner_util import learned_selector_origin
    cfg = {"applied_template": "user_a8a_filthykings_o1517_1790650983",
           "learned": {"download": {"row_selectors": ["a.mine"]}}}
    assert learned_selector_origin(cfg, "a.mine") == "applied template user_a8a_filthykings_o1517_1790650983"
    assert learned_selector_origin(cfg, ".VideoJSPlayer-Modal .VideoJSPlayer-DownloadOption-Link") == (
        "reviewed template, not applied template user_a8a_filthykings_o1517_1790650983")
    assert learned_selector_origin({"learned": {"download": {"row_selectors": ["a.mine"]}}}, "a.mine") == "site learned rows"
    assert learned_selector_origin({}, "a.x") == "reviewed template"


def test_a_miss_without_a_trace_still_names_its_selectors():
    """A pick that carries no _learned_trace (e.g. a deep-detect rescue) keeps the line."""
    from bulk_downloader.runner_util import _learned_miss_line
    line = _learned_miss_line({"applied_template": "t1"}, ["a.x"], {"text": "sweep pick"})
    assert line == ("learned rows missed: template=t1: 1 learned row selector(s) named no usable row "
                    "([a.x] matched=? visible=? admitted=?); won by the wide sweep")
