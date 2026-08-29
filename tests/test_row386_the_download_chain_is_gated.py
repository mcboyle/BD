"""Row 386 -- nothing in the suite downloaded a file, so nothing judged the mission.

MEASURED 2026-08-29. The affected band for a cut ran 623 files and 8,528 tests,
all green, while the DEPLOYED app on test6 could not complete a single download
on two of its 32 sites. Eight scene attempts failed between 13:24 and 14:41 and
the failure was invisible for EIGHTY MINUTES; a human found it, not a gate.
Later the same day the suite was green again while the app downloaded THE WRONG
SCENE and recorded it under the right title. The band judges the TREE. Nothing
judged the MISSION.

An operator harness (bd-mission-check.py) now covers the live case, but it lives
outside the repository: not in CI, not versioned with the code it judges, not
durable. CLAUDE.md A5 -- a gate CI does not run does not exist.

WHAT THIS GATE IS. The real chain, executed against RECORDED FIXTURES served on
an ephemeral loopback port, end to end:

    page DOM  ->  candidate discovery (detect.find_best_download)
              ->  ranking and the chosen element's own href
              ->  direct-URL resolution (TransportMixin._direct_media_route)
              ->  suggested filename
              ->  a REAL transfer (TransportMixin._do_direct_http_download)
              ->  bytes on disk
              ->  db.db_log(status='done')  ->  a history row AND a library row
                  carrying the title harvested from the page itself.

NO LIVE NETWORK, ever: CLAUDE.md A6 forbids formal tests against the live
service or an authenticated site. Both the pages and the media bytes come from
tests/fixtures/ over 127.0.0.1, and the transfer's proxy resolution is asserted
to be None so the payload cannot leave loopback.

THE THREE DEFECTS IT MUST CATCH, each a design target and each a fixture case:

  1. A layout WRAPPER outranking the leaf control it contains (row 380 /
     v3.66.1341). gather_text reads inner_text, so div.content_download
     .video_downloads inherited its 7680x4320 child's label and TIED at 4325,
     while parse_size_bytes read the FIRST size in the wrapper (the 1080p
     tier's 1.99GB) and the real 8K anchor's size sat in a sibling and parsed
     0. The (score,size) sort put the wrapper first and clicking a <div> fires
     no download event.
  2. A PHOTO-SET dimension read as a video resolution (row 381 / v3.66.1342).
     res_score's (\\d{3,4})[x](\\d{3,4}) rule took group 2 as a height, so
     "Large 6000x4000px" scored 4000 and outranked a real 2160p anchor.
  3. A direct media href CLICKED instead of fetched (row 384 / v3.66.1346). The
     direct-URL fast path was gated on `_via_learned`, set only in detect.py's
     learned branch, so a wide-sweep winner fell through to expect_download and
     waited 60s for an event a cross-host signed .mp4 never fires.

Each was RED-reproduced against this gate before the cut froze; the exact
messages are in the cut's evidence.

UNKNOWN IS NOT A PASS. A missing browser, an unreadable fixture, a case that
raises -- each is recorded as an UNKNOWN result and FAILS the denominator test
by name. Nothing here skips: pytest.importorskip would turn "the gate could not
see its subject" back into a green tick, which is the entire defect this row is
about. ci.yml says the same about Chromium in its own words.

DELIBERATELY NOT COVERED. Listing and login discovery are row 374's subject and
its gate (tests/test_row374_scene_crawler.py) already exercises them against
these same fixtures; the login boundary appears here only as a negative control
-- a logged-out page must yield no candidate and therefore no completion. The
related-scene case (row 388, in flight) asserts the contract and is xfail,
non-strict, so this gate turns green on its own when that row lands and cannot
go red on merge order.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

# The classifier in tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py
# parses a module-level ASSIGNMENT, not a docstring line. This gate's subject is
# the tree's ability to complete a download at all, so it is repo-wide and must
# run on every PR regardless of what the diff touched -- the measured failure
# was invisible to a 623-file band.
BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
MANIFEST = FIXTURES / "row386_download_chain" / "manifest.json"

# The denominator, pinned here and not derived from the manifest it checks.
# A gate that silently exercises zero fixtures passes for the wrong reason.
_EXPECTED_CASES = 5
_EXPECTED_DOWNLOAD_CASES = 4      # every case but the logged-out control
_EXPECTED_STRICT_CASES = 3        # ... minus the row-388 xfail case

_MEDIA = bytes(range(256)) * 32   # 8192 deterministic bytes, served for /dl/*
_MEDIA_SHA256 = hashlib.sha256(_MEDIA).hexdigest()


def _load_manifest():
    """Read the hand-written expectations. Any failure here is UNKNOWN, and
    UNKNOWN fails -- an unreadable manifest must never present as zero work."""
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cases = raw["cases"]
    assert isinstance(cases, list) and cases, "manifest declares no cases"
    return raw, cases


_MANIFEST, _CASES = _load_manifest()
_BY_ID = {c["id"]: c for c in _CASES}
_DOWNLOAD_IDS = [c["id"] for c in _CASES if c["expect_winner"]]
_STRICT_IDS = [c["id"] for c in _CASES
               if c["expect_winner"] and not c.get("xfail_row")]


# ── the fixture server: pages AND media, on loopback only ────────────────────

class _Handler(BaseHTTPRequestHandler):
    pages: dict = {}

    def do_GET(self):  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if path.startswith("/dl/"):
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(_MEDIA)))
            self.end_headers()
            self.wfile.write(_MEDIA)
            return
        raw = self.pages.get(path)
        if raw is not None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self.send_error(404)

    def log_message(self, _fmt, *_args):
        return


def _transport():
    """The minimum a TransportMixin transfer needs, and nothing more.

    `_do_direct_http_download` is production code and is called unmodified, and
    so is its proxy resolution -- the chain asserts that resolution returns
    None for this configuration, which is what keeps the transfer on loopback.
    """
    from bulk_downloader.runner_transport import TransportMixin

    class _Harness(TransportMixin):
        def __init__(self):
            self.site_id = "row386"
            self.config = {}
            self._stop = threading.Event()
            self.job_updates = []

        def _update_job(self, url, status, message, **extra):
            self.job_updates.append((url, status, message))

    return _Harness()


def _blank(case_id, unknown=None):
    """Every result carries every key, so a case that never ran fails on the
    denominator rather than raising KeyError somewhere further down."""
    return {"id": case_id, "unknown": unknown, "winner_tag": None,
            "winner_href": None, "winner_score": None, "candidates": [],
            "n_candidates": 0, "anchors": 0, "title": "", "title_source": "",
            "media_url": None, "suggested": None, "downloaded_path": None,
            "downloaded_bytes": None, "downloaded_sha256": None,
            "proxy": "unset", "wrapper_count": None,
            "wrapper_descendant_controls": None}


def _case_result(browser, base, case, download_root):
    """Run one fixture through the whole chain. Never raises: a failure is an
    UNKNOWN result, which the denominator test then fails by name."""
    out = _blank(case["id"])
    try:
        from bulk_downloader.detect import find_best_download
        from bulk_downloader.runner_transport import TransportMixin
        from bulk_downloader.website_title import harvest_page_title

        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            page.goto(base + case["page_path"], wait_until="load")
            out["anchors"] = page.locator("a").count()
            sel = case.get("wrapper_selector")
            if sel:
                out["wrapper_count"] = page.locator(sel).count()
                if out["wrapper_count"]:
                    out["wrapper_descendant_controls"] = (
                        page.locator(sel).first.locator("a[href]").count())
            title, source = harvest_page_title(page)
            out["title"], out["title_source"] = title, source

            best = find_best_download(page, "", learned=None, runner=None)
            if best is None:
                return out
            loc = best["locator"]
            out["winner_tag"] = loc.evaluate("e => e.tagName")
            try:
                out["winner_href"] = loc.get_attribute("href")
            except Exception:
                out["winner_href"] = None
            out["winner_score"] = best.get("score")
            for cand in best.get("_all_candidates") or []:
                href = None
                try:
                    href = cand["locator"].get_attribute("href")
                except Exception:
                    href = None
                out["candidates"].append(
                    {"text": cand.get("text", ""), "score": cand.get("score"),
                     "size": cand.get("size"), "href": href})
            out["n_candidates"] = len(out["candidates"])

            media_url, suggested = TransportMixin._direct_media_route(
                out["winner_href"], page.url)
            out["media_url"], out["suggested"] = media_url, suggested
            if not media_url:
                return out

            harness = _transport()
            out["proxy"] = harness._download_proxy_url()
            dest = Path(download_root) / case["id"] / (suggested or "x.bin")
            dest.parent.mkdir(parents=True, exist_ok=True)
            ok = harness._do_direct_http_download(
                page.url, media_url, str(dest), referer=page.url)
            if not ok:
                out["unknown"] = (
                    "the transfer returned False -- the chain reached the "
                    "fetch and did not complete it")
                return out
            blob = dest.read_bytes()
            out["downloaded_path"] = str(dest)
            out["downloaded_bytes"] = len(blob)
            out["downloaded_sha256"] = hashlib.sha256(blob).hexdigest()
        finally:
            page.close()
    except Exception as exc:                      # noqa: BLE001 - recorded, not hidden
        out["unknown"] = f"{type(exc).__name__}: {exc}"
    return out


@pytest.fixture(scope="module")
def chain(tmp_path_factory):
    """Execute every recorded fixture once, in one browser, and hand back the
    measurements. The count is asserted BEFORE any browsing, so a manifest that
    lost a case cannot present as a completed run."""
    download_root = str(tmp_path_factory.mktemp("row386_dl"))

    assert len(_CASES) == _EXPECTED_CASES, (
        f"the manifest declares {len(_CASES)} cases, not {_EXPECTED_CASES}: "
        "the download chain would be judged over a denominator nobody pinned")

    pages = {}
    for case in _CASES:
        raw = (FIXTURES / case["fixture"]).read_bytes()
        assert raw.strip(), f"{case['fixture']} is empty"
        pages[case["page_path"]] = raw
    assert len(pages) == _EXPECTED_CASES, "two cases share one page path"

    handler = type("_Row386Handler", (_Handler,), {"pages": pages})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()

    results = {}
    try:
        # NO importorskip and NO skip on a missing browser. An absent Chromium
        # is unavailable evidence, never grounds for a pass.
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                for case in _CASES:
                    results[case["id"]] = _case_result(
                        browser, base, case, download_root)
            finally:
                browser.close()
    except Exception as exc:                      # noqa: BLE001
        for case in _CASES:
            results.setdefault(case["id"], _blank(
                case["id"],
                f"browser unavailable: {type(exc).__name__}: {exc}"))
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=10)
    return {"base": base, "results": results}


# ── the denominator ──────────────────────────────────────────────────────────

def test_the_recorded_fixtures_are_present_and_parseable():
    """Runs without a browser, so an unreadable fixture is reported as itself
    rather than as a browser failure."""
    assert len(_CASES) == _EXPECTED_CASES
    assert len({c["id"] for c in _CASES}) == _EXPECTED_CASES, "duplicate case id"
    assert len(_DOWNLOAD_IDS) == _EXPECTED_DOWNLOAD_CASES
    assert len(_STRICT_IDS) == _EXPECTED_STRICT_CASES
    assert len(_MEDIA) == _MANIFEST["media_bytes"], (
        f"the served media is {len(_MEDIA)} bytes and the manifest declares "
        f"{_MANIFEST['media_bytes']}; the transfer assertions compare against "
        "a length nobody agreed on")
    required = {"id", "fixture", "page_path", "expect_winner", "min_candidates",
                "expected_title", "expected_title_source"}
    for case in _CASES:
        missing = required - set(case)
        assert not missing, f"UNKNOWN: case {case.get('id')} omits {missing}"
        if case["expect_winner"]:
            for key in ("winner_tag", "winner_href", "suggested_filename",
                        "must_harvest_href"):
                assert case.get(key), (
                    f"UNKNOWN: case {case['id']} expects a download but "
                    f"declares no {key}")
        path = FIXTURES / case["fixture"]
        assert path.is_file(), f"UNKNOWN: fixture {case['fixture']} is missing"
        raw = path.read_text(encoding="utf-8")
        assert "<html" in raw.lower(), (
            f"UNKNOWN: {case['fixture']} does not parse as an HTML page")


def test_every_fixture_was_actually_exercised(chain):
    """THE assertion this whole row exists for. A gate that silently exercised
    zero fixtures reports green for the wrong reason."""
    results = chain["results"]
    assert len(results) == _EXPECTED_CASES, (
        f"{len(results)} of {_EXPECTED_CASES} cases produced a result")
    unknown = {k: v.get("unknown") for k, v in results.items() if v.get("unknown")}
    assert not unknown, f"UNKNOWN, and UNKNOWN fails: {unknown}"

    for case in _CASES:
        got = results[case["id"]]
        n = got["n_candidates"]
        if case["expect_winner"]:
            assert n >= case["min_candidates"], (
                f"{case['id']}: {n} candidates, fewer than the "
                f"{case['min_candidates']} controls this page carries")
            hrefs = [c["href"] for c in got["candidates"]]
            for required in case.get("must_harvest_href", []):
                assert required in hrefs, (
                    f"{case['id']}: {required} was never harvested; "
                    f"candidates={hrefs}")
        else:
            assert n == 0, f"{case['id']}: expected no candidate, got {n}"
            assert got["anchors"] >= 1, (
                f"{case['id']}: the page carried no anchors at all, so zero "
                "candidates proves nothing about the filter")

    total = sum(results[i]["n_candidates"] for i in _DOWNLOAD_IDS)
    assert total >= sum(_BY_ID[i]["min_candidates"] for i in _DOWNLOAD_IDS)


# ── defect 1: a wrapper must not outrank the leaf it contains ────────────────

def test_a_layout_wrapper_never_outranks_the_control_it_contains(chain):
    case = _BY_ID["wowgirls_wrapper_vs_leaf"]
    got = chain["results"][case["id"]]
    assert got["wrapper_count"] == 1, (
        "the fixture no longer presents the wrapper shape; this test's "
        "premise is gone")
    assert got["wrapper_descendant_controls"] == 4, (
        "the wrapper must CONTAIN the leaf controls for the nesting defect "
        "to exist")
    assert got["winner_tag"] == "A", (
        f"the winner is a <{got['winner_tag']}>, not the clickable leaf: a "
        "layout wrapper inherited its children's labels and won the "
        "(score,size) sort -- clicking it fires no download event (row 380)")
    assert got["winner_href"] == case["winner_href"], (
        f"winner href {got['winner_href']!r}: the highest tier the page "
        "offers must win")


# ── defect 2: a photo dimension is not a video resolution ────────────────────

def test_a_photo_set_dimension_never_outranks_the_video_anchor(chain):
    case = _BY_ID["nubilefilms_photo_vs_video"]
    got = chain["results"][case["id"]]
    by_href = {c["href"]: c for c in got["candidates"]}
    for photo in case["outranked_href"]:
        assert photo in by_href, (
            f"{photo} was not harvested at all, so this test could not see "
            "its subject -- the photo control must be a real candidate for "
            "the ranking claim to mean anything")
    assert got["winner_href"] == case["winner_href"], (
        f"winner href {got['winner_href']!r}: a photo-set caption "
        "('Large 6000x4000px') was read as a video height and outranked the "
        "real 2160p anchor (row 381)")
    for photo in case["outranked_href"]:
        assert by_href[photo]["score"] < got["winner_score"], (
            f"{photo} scored {by_href[photo]['score']} against the video "
            f"anchor's {got['winner_score']}")


# ── defect 3: a direct media href is fetched, not clicked ────────────────────

@pytest.mark.parametrize("case_id", _DOWNLOAD_IDS)
def test_the_winning_href_routes_to_a_direct_fetch(chain, case_id):
    got = chain["results"][case_id]
    assert got["media_url"], (
        f"{case_id}: the chosen href {got['winner_href']!r} did not route to "
        "a direct fetch, so the runner clicks it and expect_download waits 60s "
        "for an event a cross-host signed .mp4 never fires (row 384)")
    assert got["media_url"].startswith(chain["base"] + "/dl/"), (
        f"{case_id}: a relative href must be resolved against the page")
    assert got["suggested"] and not got["suggested"].endswith("/"), (
        f"{case_id}: no usable destination filename")


@pytest.mark.parametrize("case_id", _STRICT_IDS)
def test_the_suggested_filename_is_the_one_the_site_intends(chain, case_id):
    case = _BY_ID[case_id]
    got = chain["results"][case_id]
    assert got["winner_href"] == case["winner_href"]
    assert got["suggested"] == case["suggested_filename"], (
        f"{case_id}: destination name {got['suggested']!r} -- the dl= "
        "parameter names the file the site intends, and it is what "
        "skip_if_exists compares on the next run")


def test_the_direct_route_is_reachable_from_the_click_path():
    """Defect 3's ROOT was reachability, not the routing function. The chain
    above calls _direct_media_route itself, so re-gating the call site on
    `_via_learned` would leave every behavioral assertion green.

    Asserted over the whole mixin rather than a byte offset inside
    _do_download, so a restructure of that function (row 388 may touch it)
    cannot silently drop the wiring or trip this on a move."""
    src = (ROOT / "bulk_downloader" / "runner_transport.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    mixin = next((n for n in tree.body
                  if isinstance(n, ast.ClassDef) and n.name == "TransportMixin"),
                 None)
    assert mixin is not None, "UNKNOWN: TransportMixin not found"

    call_sites = []          # (function name, list of enclosing If tests)
    order = {}               # route name -> first call lineno inside _do_download

    def _walk(node, fn_name, guards):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _walk(child, child.name, [])
                continue
            if isinstance(child, ast.If):
                _walk(child, fn_name, guards + [ast.dump(child.test)])
                continue
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                attr = child.func.attr
                if attr == "_direct_media_route":
                    call_sites.append((fn_name, guards))
                if attr in ("_direct_media_route", "_stream_route") \
                        and fn_name == "_do_download":
                    order.setdefault(attr, child.lineno)
            _walk(child, fn_name, guards)

    for fn in mixin.body:
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _walk(fn, fn.name, [])

    assert call_sites, (
        "nothing in TransportMixin calls _direct_media_route, so a wide-sweep "
        "winner with a direct media href is clicked and never fetched")
    reachable = [(fn, g) for fn, g in call_sites
                 if not any("_via_learned" in test for test in g)]
    assert reachable, (
        "every _direct_media_route call site is guarded by `_via_learned`, "
        "which detect.py sets only on the learned branch -- a wide-sweep "
        f"winner can never reach it. Sites: {call_sites}")
    assert any(fn == "_do_download" for fn, _g in reachable), (
        f"_do_download does not reach _direct_media_route: {call_sites}")
    assert set(order) == {"_stream_route", "_direct_media_route"}, (
        f"_do_download consults {sorted(order)}; both routes must be asked, "
        "and the ordering claim below is meaningless without each of them")
    assert order["_stream_route"] < order["_direct_media_route"], (
        "the manifest route must be consulted FIRST; otherwise a .m3u8 could "
        "be claimed as a direct fetch and handed to httpx instead of ffmpeg")


# ── the mission: real bytes, then a history row and a library title ──────────

@pytest.mark.parametrize("case_id", _DOWNLOAD_IDS)
def test_the_chain_puts_real_bytes_on_disk(chain, case_id):
    got = chain["results"][case_id]
    assert got["proxy"] is None, (
        f"{case_id}: the transfer resolved proxy {got['proxy']!r}; this gate "
        "must stay on loopback")
    assert got["downloaded_bytes"] == len(_MEDIA), (
        f"{case_id}: {got['downloaded_bytes']} bytes on disk, expected "
        f"{len(_MEDIA)}")
    assert got["downloaded_sha256"] == _MEDIA_SHA256, (
        f"{case_id}: the bytes on disk are not the bytes served")
    assert Path(got["downloaded_path"]).name == got["suggested"]


@pytest.mark.parametrize("case_id", _DOWNLOAD_IDS)
def test_a_completed_download_records_history_and_a_library_title(
        chain, case_id, tmp_path, monkeypatch):
    """bd-mission-check's own verdict, moved into the repository: a terminal
    'done' row AND a library row carrying a title. 84 library rows once carried
    0 titles (row 1339) while every history row said done."""
    from bulk_downloader import db as _db, migrations as _m
    case = _BY_ID[case_id]
    got = chain["results"][case_id]
    assert got["title"], f"{case_id}: no title was harvested from the page"
    if not case.get("xfail_row"):
        assert got["title"] == case["expected_title"]
        assert got["title_source"] == case["expected_title_source"]

    dbp = tmp_path / "history.db"
    monkeypatch.setattr(_db, "DB_PATH", str(dbp))
    _db.db_init()
    _m.apply_pending(backup_first=False)

    _db.db_log("row386", "Row 386 fixture", chain["base"] + case["page_path"],
               "done", got["suggested"], got["downloaded_bytes"],
               "fixture completion", bytes_fetched=got["downloaded_bytes"],
               transfer_mode="direct", file_path=got["downloaded_path"],
               title=got["title"], title_source=got["title_source"])

    cx = sqlite3.connect(str(dbp))
    try:
        hist = cx.execute(
            "SELECT status, filename, file_size FROM history").fetchall()
        lib = cx.execute(
            "SELECT file_path, title, file_exists FROM library").fetchall()
    finally:
        cx.close()
    assert len(hist) == 1 and hist[0][0] == "done", f"history rows: {hist}"
    assert hist[0][1] == got["suggested"]
    assert hist[0][2] == len(_MEDIA)
    assert len(lib) == 1, (
        f"{case_id}: {len(lib)} library rows for one completion: {lib}")
    assert lib[0][0] == got["downloaded_path"]
    assert lib[0][1] == got["title"], (
        f"{case_id}: the library row carries title {lib[0][1]!r} -- a "
        "completion with no title is what the operator sees as a missing scene")
    assert lib[0][2] == 1, "the library row does not believe its own file exists"


def test_a_logged_out_page_produces_no_completion(chain):
    """The login boundary as a negative control: no candidate, no route, no
    bytes. A tour page must never be laundered into a finished download."""
    case = _BY_ID["logged_out_is_not_a_download"]
    got = chain["results"][case["id"]]
    assert got["anchors"] >= 1, "the logged-out fixture did not even load"
    assert got["title"] == case["expected_title"], (
        "the page did load and its title was read, so the absence of a "
        "candidate below is the filter's answer and not a blank page")
    assert got["title_source"] == case["expected_title_source"]
    assert got["winner_tag"] is None and got["winner_href"] is None
    assert got["media_url"] is None
    assert got["downloaded_bytes"] is None


@pytest.mark.xfail(
    reason="row 388 (in flight) owns same-scene candidate ranking; this gate "
           "asserts the contract without implementing it, and is non-strict so "
           "it turns green on its own when 388 lands, whatever the merge order",
    strict=False)
def test_the_winner_belongs_to_the_scene_being_downloaded(chain):
    case = _BY_ID["related_grid_belongs_to_another_scene"]
    got = chain["results"][case["id"]]
    assert got["winner_href"] == case["winner_href"], (
        f"the chosen href {got['winner_href']!r} belongs to a related-videos "
        "card, not to the scene on this page -- the app downloads the WRONG "
        "SCENE and records it under the right title (row 388)")


# ── the gate must actually run, with a browser, in CI ────────────────────────

def test_this_gate_is_scheduled_in_ci_on_a_shard_that_has_chromium():
    """A gate CI does not run does not exist -- and a Chromium-driven gate on a
    shard without Chromium is the same thing with a longer traceback. The
    shard-coverage gate proves declaration and scheduling; nothing else checks
    that the browser install step reaches the shard, the way
    test_v3_66_1218 checks the node condition."""
    try:
        import yaml
    except ImportError as exc:                    # pragma: no cover - see below
        # NOT importorskip. PyYAML is in requirements-test.txt and CI installs
        # it; if it is absent this gate cannot read its own CI wiring, and
        # "could not measure" is UNKNOWN, which fails.
        pytest.fail(f"UNKNOWN: PyYAML is unavailable ({exc}), so this gate "
                    "cannot check that CI schedules it on a browser shard")
    wf = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job = wf["jobs"]["gate-suites"]
    me = "tests/" + Path(__file__).name
    shards = [s for s in job["strategy"]["matrix"]["include"]
              if me in s["suites"].split()]
    assert len(shards) == 1, (
        f"{me} appears in {len(shards)} gate-suites shards; it must be in "
        "exactly one or it runs nowhere / twice")
    name = shards[0]["name"]
    steps = [s for s in job["steps"]
             if "playwright install" in str(s.get("run", ""))]
    assert steps, "no step installs a browser on any gate shard"
    covered = [s for s in steps if name in str(s.get("if", ""))]
    assert covered, (
        f"shard {name!r} runs this gate but no 'playwright install' step's "
        f"condition names it, so every case would fail on a missing browser")
