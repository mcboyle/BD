"""Every URL the seeder queues must be one the fixture actually serves.

`seeded_url()` built `http://127.0.0.1:8899/bdseed/clipN.mp4`, putting the
marker in the URL path. `tools/fixture_site.py` has no such route -- the string
"bdseed" appears in it zero times -- so every seeded download 404s. Measured
against a live fixture:

    /bdseed/clip0.mp4        404
    /direct/media/0.mp4      200
    /hls/scene/0.m3u8        200

The consequence is three live checks that can never clear, for a reason that
has nothing to do with the code they are testing:

    L11 end-to-end-small-download   "no completed downloads yet"
    L12 hls-dash-segmented-download "no completed downloads yet"
    L14 stash-dedup-skip            "no completed downloads in history"

Those read as BD failing to download. BD was never given a URL that resolves.

WHY THE MARKER CAN LEAVE THE PATH. Teardown does not identify seeded work by
URL. `_marked_site_ids()` reads `/api/status` and discriminates on the site
NAME (`SEED_SITE_NAME`), which is why its docstring says "Anything unmarked
belongs to the operator and is off limits". Moving the marker out of the URL
therefore costs nothing and buys URLs that exist.

This gate checks the seeder's URLs against the fixture's OWN route map rather
than against a list copied into the test. A copied list is a second
denominator: it would keep passing after someone renamed a fixture route.
"""
from __future__ import annotations

import re
import importlib.machinery
import importlib.util
from pathlib import Path
from urllib.parse import urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = REPO_ROOT / "tools" / "live_seed.py"


def _load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def seeder():
    return _load("bd_live_seed_urls", SEED_PATH)


@pytest.fixture(scope="module")
def fixture_app():
    try:
        import sys
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from tools import fixture_site
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"tools.fixture_site did not import: {exc}")
    return fixture_site.make_app()


def _seeded_urls(seeder, count: int = 3):
    return [seeder.seeded_url(i) for i in range(count)]


def test_the_seeder_still_produces_urls(seeder):
    """Denominator canary: no URLs means every assertion below is vacuous."""
    urls = _seeded_urls(seeder)
    assert urls and all(u.strip() for u in urls), (
        "seeded_url() produced nothing; the checks below would pass over an "
        "empty set"
    )


def test_every_seeded_url_matches_a_real_fixture_route(seeder, fixture_app):
    """The defect: URLs pointing at routes that do not exist."""
    adapter = fixture_app.url_map.bind("127.0.0.1")
    unservable = []
    for url in _seeded_urls(seeder):
        path = urlparse(url).path
        try:
            adapter.match(path, method="GET")
        except Exception as exc:
            unservable.append(f"{path}  ->  {type(exc).__name__}")
    assert not unservable, (
        "the seeder queues URLs tools/fixture_site.py does not serve:\n  "
        + "\n  ".join(unservable)
        + "\n\nEvery seeded download 404s, so L11/L12/L14 report 'no completed "
          "downloads' no matter how well BD works. Use routes the fixture "
          "actually defines -- /direct/media/<n>.mp4, /hls/scene/<n>.m3u8 -- "
          "and keep the seed marker in the SITE NAME, which is where "
          "_marked_site_ids() looks."
    )


def test_no_seeded_url_is_a_bare_manifest(seeder):
    """A manifest is not navigable, so seeding one exercises nothing.

    RENAMED 2026-07-28, from test_the_seed_set_includes_a_segmented_url, which
    asserted the exact opposite of what it now checks. L12's requirement did not
    change -- it still needs a segmented download -- but the way to give it one
    did: see test_a_seeded_page_offers_a_segmented_download_link below, which
    checks the download link on the seeded PAGE. A test whose name contradicts
    its assertion is its own defect, so the name moved with the predicate.
    """
    urls = _seeded_urls(seeder)
    # Test the PATH, not the whole URL: the seed marker rides in the query
    # string (it has to -- _is_seeded reads it), so every URL ends with
    # "?bdseed=1" and a naive endswith() on the full string sees no extension
    # at all. Checking the wrong span of the string is how the tool under test
    # got here in the first place.
    paths = [urlparse(u).path for u in urls]
    # CORRECTED 2026-07-28. This used to require a seeded PATH ending in .m3u8,
    # which asks for something BD can never consume: a manifest is not
    # navigable, and seeding one produced "No download button found". BD
    # navigates a PAGE and scrapes it for a link, so what L12 actually needs is
    # a seeded page whose DOWNLOAD LINK is segmented. Same error as the seed set
    # itself had -- right subject, wrong span.
    assert not any(p.endswith((".m3u8", ".mpd")) for p in paths), (
        f"a seeded URL is a bare manifest: {paths}. BD cannot navigate to one "
        f"-- measured as 'No download button found'. Seed the PAGE that links "
        f"to the manifest instead."
    )


def test_the_seed_set_includes_a_duplicate(seeder):
    """L14 needs the same URL twice or dedup has nothing to skip."""
    urls = _seeded_urls(seeder)
    assert len(urls) != len(set(urls)), (
        f"all seeded URLs are distinct: {urls}. L14 (stash-dedup-skip) tests "
        f"that a repeat is skipped, so the seed set must contain a repeat."
    )


def test_the_marker_still_identifies_seeded_work(seeder):
    """Moving the marker out of the URL must not orphan teardown.

    If the marker leaves the path, the site NAME has to keep carrying it --
    otherwise teardown cannot tell seeded work from the operator's.
    """
    assert seeder.SEED_MARKER in seeder.SEED_SITE_NAME, (
        f"SEED_SITE_NAME ({seeder.SEED_SITE_NAME!r}) no longer carries "
        f"SEED_MARKER ({seeder.SEED_MARKER!r}). _marked_site_ids() "
        f"discriminates on the name; without it teardown cannot identify what "
        f"it created and must not delete anything."
    )


# ── the seeded URL must be one BD can CONSUME, not merely one served ─────────
#
# test_every_seeded_url_resolves_against_the_fixtures_route_map above proves the
# fixture SERVES each seeded URL. Whether BD can DOWNLOAD it is a different
# subject, and the gap was invisible for as long as the seed set has existed.
# Measured against the real app on 2026-07-28:
#
#   /direct/media/0.mp4?bdseed=1 -> worker error: Page.goto: Download is starting
#   /hls/scene/0.m3u8?bdseed=1   -> No download button found
#
# BD navigates to a PAGE and scrapes it for a download link. Handed raw media it
# cannot navigate at all. So the route-map gate passed on three URLs none of
# which could ever complete, and L11/L12/L14 reported "no completed downloads"
# forever -- which reads as BD failing to download, when BD was never handed a
# URL it could consume. The instrument fixed the denominator (real url_map, not
# grep); it took the wrong predicate (served, not consumable).

_MIN_RESOLUTION_DEFAULT = 1080


def _seeded_paths(seeder):
    return [urlparse(u).path for u in _seeded_urls(seeder, len(seeder._SEED_PATHS))]


def test_every_seeded_url_is_a_page_bd_can_scrape_not_raw_media(seeder, fixture_app):
    """Raw media is unnavigable -- Playwright reports 'Download is starting'.

    The predicate is the response BD would actually receive, not the file
    extension: a page can be served from any path, and '.mp4' is not itself
    what breaks it. Asking the fixture is the only instrument that answers for
    the URL actually seeded.
    """
    client = fixture_app.test_client()
    offenders = [f"{p} -> {client.get(p).mimetype}"
                 for p in _seeded_paths(seeder)
                 if client.get(p).mimetype != "text/html"]
    assert not offenders, (
        "seeded URL(s) do not serve an HTML page, so BD cannot navigate to them "
        "and no download can ever complete: " + "; ".join(offenders)
        + ". BD scrapes a page for a download link; it never fetches media direct."
    )


def test_every_seeded_page_offers_a_download_link(seeder, fixture_app):
    """A page BD can reach but which offers nothing is equally unusable."""
    client = fixture_app.test_client()
    linkless = [p for p in _seeded_paths(seeder)
                if "download-link" not in client.get(p).get_data(as_text=True)]
    assert not linkless, (
        f"seeded page(s) carry no download link, so BD reaches them and finds "
        f"nothing to fetch: {linkless}"
    )


def test_every_seeded_page_clears_the_default_minimum_resolution(seeder, fixture_app):
    """Below the floor BD parks at needs_review, which is not 'done'.

    min_resolution defaults to 1080 (app_kernel.py). A 480p or 720p scene stops
    at "Best is 480p (below 1080p) -- Approve to force" and waits for a human,
    so it never reaches the terminal state L11 counts. Measured on the real app:
    /scene/0 and /scene/1 park; /scene/2 and /scene/3 do not.
    """
    client = fixture_app.test_client()
    too_low = []
    for path in _seeded_paths(seeder):
        html = client.get(path).get_data(as_text=True)
        found = re.search(r"data-resolution='(\d+)p'", html)
        if not found:
            too_low.append(f"{path} (advertises no resolution)")
        elif int(found.group(1)) < _MIN_RESOLUTION_DEFAULT:
            too_low.append(f"{path} ({found.group(1)}p < {_MIN_RESOLUTION_DEFAULT}p)")
    assert not too_low, (
        f"seeded page(s) advertise a resolution below the default "
        f"min_resolution ({_MIN_RESOLUTION_DEFAULT}p); BD parks these at "
        f"needs_review awaiting operator approval, so they never complete: "
        + "; ".join(too_low)
    )


def test_a_seeded_page_offers_a_segmented_download_link(seeder, fixture_app):
    """L12 needs a manifest REACHABLE FROM a seeded page.

    The replacement for the old ends-with-.m3u8 assertion. It checks what BD
    would actually follow -- the download link on the page it navigates to --
    rather than the shape of the URL handed to the queue.
    """
    client = fixture_app.test_client()
    segmented = []
    for path in _seeded_paths(seeder):
        html = client.get(path).get_data(as_text=True)
        for href in re.findall(r"class='download-link'\s+href='([^']+)'", html):
            if href.endswith((".m3u8", ".mpd")):
                segmented.append(f"{path} -> {href}")
    assert segmented, (
        f"no seeded page links to a manifest. L12 "
        f"(hls-dash-segmented-download) cannot be exercised without one. "
        f"Seeded pages: {_seeded_paths(seeder)}"
    )


def test_the_segmented_link_actually_serves_a_manifest(seeder, fixture_app):
    """A link ending .m3u8 that 404s would satisfy the gate above vacuously."""
    client = fixture_app.test_client()
    checked = []
    for path in _seeded_paths(seeder):
        html = client.get(path).get_data(as_text=True)
        for href in re.findall(r"class='download-link'\s+href='([^']+)'", html):
            if href.endswith((".m3u8", ".mpd")):
                resp = client.get(href)
                body = resp.get_data(as_text=True)[:16]
                checked.append((href, resp.status_code, body))
    assert checked, "no segmented link to verify"
    for href, status, body in checked:
        assert status == 200, f"{href} -> HTTP {status}"
        assert body.startswith("#EXTM3U"), (
            f"{href} returned {body!r}, not an HLS manifest"
        )
