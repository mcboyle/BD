"""dl95-pegasproductions-2 (download-95/B6-B/p1/pegasproductions/RESULT.md, HIGH).

On test2, pegasproductions (a login site, auth ok) saved /trailers/game-hockey-f-en-1080p.mp4 as the scene
https://www.pegasproductions.com/game-hockey-1. That file is 81.8 MB and 84 s long, and it is the public trailer that the
logged-out page links. The job closed done under the scene's title, so dedupe now blocks the real scene. The anchor
reads a clean "1080p" and its URL is a .mp4. The only thing that says "promo" is its /trailers/ DIRECTORY, and
detect's URL-shape refusal did not read directories.

Drives the real detect.find_best_download (wide sweep and learned row_selectors) on a real chromium page (BD's cloak
wrapper, set_content; no network). Controls: row 508's contract that a /preview/ path stays admissible, and that a
trailer-ish host or file name outside a trailers directory is not refused.
"""
from __future__ import annotations

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://www.pegasproductions.com"
TRAILER = f"{ORIGIN}/trailers/game-hockey-f-en-1080p.mp4"
MEMBER = f"{ORIGIN}/members/download/game-hockey-720p.mp4"


@pytest.fixture(scope="module")
def page():
    from bulk_downloader import cloak

    with cloak.cloaked_page(headless=True, config={"browser_backend": "playwright"},
                            viewport={"width": 1280, "height": 720}) as p:
        yield p


def _best(page, anchors, learned=None):
    from bulk_downloader import detect

    body = "".join(f'<a class="tier" href="{h}">{label}</a>' for h, label in anchors)
    page.set_content(f"<!doctype html><html><body><main><h1>The Hat Trick</h1>{body}</main></body></html>")
    return detect.find_best_download(page, learned=learned)


def _href(best):
    return best["locator"].get_attribute("href") if best else None


@pytest.mark.parametrize("learned", [None, {"row_selectors": ["a.tier"]}], ids=["wide", "learned"])
def test_a_trailers_directory_file_is_never_the_scene(page, learned):
    best = _best(page, [(TRAILER, "1080p")], learned=learned)
    assert not best, f"dl95-pegasproductions-2: the public trailer was chosen as the scene: {_href(best)} score={best.get('score')}"


@pytest.mark.parametrize("learned", [None, {"row_selectors": ["a.tier"]}], ids=["wide", "learned"])
def test_the_member_rendition_wins_over_a_higher_tier_trailer(page, learned):
    best = _best(page, [(TRAILER, "1080p"), (MEMBER, "720p")], learned=learned)
    assert best and _href(best) == MEMBER, f"dl95-pegasproductions-2: chose {_href(best) if best else best}"


@pytest.mark.parametrize("href", [
    "https://cdn.example/preview/1080p.mp4",                 # row 508 path-preview arm: must stay admissible
    "https://trailers-cdn.example/members/scene-1080p.mp4",  # word in the HOST, not a directory
    f"{ORIGIN}/members/game-hockey-trailer-cut-1080p.mp4",   # word in the FILE name, not a directory
    f"{ORIGIN}/members/dl/game-hockey-1080p.mp4?from=/trailers/game-hockey",  # a query VALUE, not the file's path
], ids=["row508-preview-path", "host", "filename", "query-value"])
def test_controls_outside_a_trailers_directory_stay_candidates(page, href):
    best = _best(page, [(href, "1080p")])
    assert best and _href(best) == href and best["score"] == 1080, best
