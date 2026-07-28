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


def test_the_seed_set_includes_a_segmented_url(seeder):
    """L12 needs an .m3u8 or .mpd or it can never be exercised.

    The fixture serves one at /hls/scene/<n>.m3u8; the old seeder queued three
    .mp4s, so L12 was unreachable even once the 404s were fixed.
    """
    urls = _seeded_urls(seeder)
    # Test the PATH, not the whole URL: the seed marker rides in the query
    # string (it has to -- _is_seeded reads it), so every URL ends with
    # "?bdseed=1" and a naive endswith() on the full string sees no extension
    # at all. Checking the wrong span of the string is how the tool under test
    # got here in the first place.
    paths = [urlparse(u).path for u in urls]
    assert any(p.endswith(".m3u8") or p.endswith(".mpd") for p in paths), (
        f"no segmented URL among {paths}. L12 (hls-dash-segmented-download) "
        f"cannot be exercised without one."
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
