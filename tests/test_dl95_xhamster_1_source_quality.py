"""dl95-xhamster-1: current scene dropdown sources outrank an unlabelled 480p control.
Shape reduced from B5-B/public-scene-xhsZHCl.html, 2026-09-29; no signed URLs or credentials.
Only actually published source URLs count; maxQuality alone never creates a tier.
"""
import ast
import json
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from tests.test_row722_spa_api_media_extraction import _make_runner

BD_GATE_SCOPE = "module"
SCENE = "https://xhamster.com/videos/fixture-xhScene"
CDN = "https://video5.xhcdn.com/fixture/"


def state(heights=(144, 240, 480, 720)):
    return {"videoModel": {"id": 123, "pageURL": SCENE},
            "downloadDropdownComponent": {"videoId": 123, "sources": {
                "mp4": {f"{h}p": f"{CDN}{h}p.h264.mp4?fixture=1" for h in heights}}},
            "xplayerSettings": {"videoId": 123, "maxQuality": "2160p",
                                "sources": {"standard": {"h264": [{"url": "encrypted", "quality": "auto"}]}}}}


@contextmanager
def page(data=None, url=SCENE):
    data = state() if data is None else data
    html = ('<html><body><a href="'+CDN+'480p.h264.mp4">Download</a>'
            '<video preload="none" src="'+CDN+'480p.h264.mp4"></video>'
            '<script>window.initials='+json.dumps(data)+'</script></body></html>')
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            pg = browser.new_page()
            pg.route("**/*", lambda r: r.fulfill(content_type="text/html", body=html)
                     if r.request.url == url else r.abort())
            pg.goto(url, wait_until="load")
            yield pg
        finally:
            browser.close()


def run(tmp_path, monkeypatch, data=None, minimum=0, url=SCENE, forced=False):
    r = _make_runner(tmp_path, monkeypatch)
    r.config["min_resolution"] = minimum
    r.states = r.jobs
    r.jobs = {url: {"force_download": forced}}
    r._lock = threading.RLock()
    r._update_job = lambda _url, status, message, **extra: r.states.append((status, message, extra))
    r._screenshot = lambda *_: "fixture.png"
    with page(data, url) as pg:
        assert pg.get_by_text("Download", exact=True).is_visible(), "XH1_FIXTURE_NO_CONTROL"
        assert r._try_spa_api_media_extractor(url, pg), "XH1_EXTRACTOR_DID_NOT_HANDLE"
    return r


def test_public_source_map_ranks_720_above_bound_480(tmp_path, monkeypatch):
    r = run(tmp_path, monkeypatch)
    assert [t["file_url"] for t in r.transfers] == [CDN+"720p.h264.mp4?fixture=1"], f"XH1_HIGHER_SOURCE_MISSED {r.transfers}"


def test_actual_account_tier_is_used_only_when_listed(tmp_path, monkeypatch):
    r = run(tmp_path, monkeypatch, state((480, 720, 1080, 2160)), 1080)
    assert [t["file_url"] for t in r.transfers] == [CDN+"2160p.h264.mp4?fixture=1"], f"XH1_ACCOUNT_SOURCE_MISSED {r.states}"


def test_public_below_minimum_names_720_not_advertised_2160(tmp_path, monkeypatch):
    r = run(tmp_path, monkeypatch, minimum=1080)
    assert not r.transfers and any(s == "needs_review" and "720p" in m for s, m, _ in r.states), f"XH1_REVIEW_WRONG_QUALITY {r.states}"


@pytest.mark.parametrize("defect", ["other_host", "other_scene", "other_id", "no_sources", "foreign_media"])
def test_unbound_source_map_does_not_replace_page_media(tmp_path, monkeypatch, defect):
    d = state(); url = SCENE
    if defect == "other_host":
        url = "https://xhamster.com.example.test/videos/fixture-xhScene"
    elif defect == "other_scene":
        d["videoModel"]["pageURL"] = "https://xhamster.com/videos/someone-else"
    elif defect == "other_id":
        d["downloadDropdownComponent"]["videoId"] = 999
    elif defect == "no_sources":
        d["downloadDropdownComponent"]["sources"] = {}
    else:
        d["downloadDropdownComponent"]["sources"]["mp4"] = {"2160p": "https://ads.example/promo.mp4"}
    r = run(tmp_path, monkeypatch, d, url=url)
    assert [t["file_url"] for t in r.transfers] == [CDN+"480p.h264.mp4"], f"XH1_UNBOUND_SOURCE_TAKEN {r.transfers}"


def test_runner_checks_source_list_before_dom_scorer():
    path = Path(__file__).parents[1] / "bulk_downloader/runner.py"
    tree = ast.parse(path.read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_process_one")
    scorer = min(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "find_best_download")
    guards = [n for n in ast.walk(fn) if isinstance(n, ast.If) and isinstance(n.test, ast.Call)
              and isinstance(n.test.func, ast.Attribute) and n.test.func.attr == "_try_spa_api_media_extractor"
              and any(k.arg == "source_list_only" and isinstance(k.value, ast.Constant) and k.value.value is True
                      for k in n.test.keywords)]
    assert any(n.lineno < scorer and any(isinstance(x, ast.Return) for x in n.body) for n in guards), "XH1_DOM_CONTROL_PREEMPTS_SOURCE_LIST"


def test_approve_forces_only_the_best_published_public_file(tmp_path, monkeypatch):
    r = run(tmp_path, monkeypatch, minimum=1080, forced=True)
    assert [t["file_url"] for t in r.transfers] == [CDN+"720p.h264.mp4?fixture=1"], f"XH1_FORCE_INVENTED_TIER {r.transfers}"
