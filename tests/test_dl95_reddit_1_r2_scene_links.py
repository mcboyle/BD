"""dl95-reddit-1 r2 (landing/dl95/RESULT-dl95-reddit-1-1948Z.md, 10-14 live on .95 build 1e334559).

Both live scans of reddit COMPLETED but queued COMMUNITY links (/r/<sub>/) as scenes, never a post
permalink (/r/<sub>/comments/<id>/<slug>/): 3 of 3 queued targets, 0 bytes, every job needs_review
(the community feed's first media: an image gallery / a 480p clip). A post permalink downloads
(jobs #481-495, 20.6 MB). Reddit's post card links its permalink without an image inside the link,
while the community link wraps the community icon, so the thumbnail cohort picked the communities.

Real Chromium; the saved listing is served by page.route for https://www.reddit.com/ and every other
request is answered 404 in-process -- no live site is contacted. The listing scan must queue exactly
the post permalinks. Controls: an operator-declared crawler_scene_patterns still decides alone; the
built-in reddit shape applies to reddit hosts only.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import pytest

BD_GATE_SCOPE = "module"

_POSTS = (
    ("videos", "1q0a1b2", "train_crossing_in_the_snow", "video"),
    ("gifs", "1q0c3d4", "cat_meets_a_robot_vacuum", "video"),
    ("oddlysatisfying", "1q0e5f6", "sorting_marbles_by_colour", "gallery"),
    ("aww", "1q0g7h8", "puppy_first_walk", "image"),
)
_HOME = "https://www.reddit.com/"
_SUB = "https://www.reddit.com/r/videos/"


def _permalink(sub, post_id, slug):
    return f"/r/{sub}/comments/{post_id}/{slug}/"


def _card(sub, post_id, slug, kind):
    link = _permalink(sub, post_id, slug)
    title = slug.replace("_", " ").capitalize()
    return (
        f'<shreddit-post permalink="{link}" post-type="{kind}" subreddit-prefixed-name="r/{sub}">'
        f'<span slot="credit-bar"><a href="/r/{sub}/"><img src="https://styles.redditmedia.com/'
        f'{sub}.png" alt="r/{sub} icon"></a><a href="/r/{sub}/">r/{sub}</a>'
        f' <a href="/user/poster_{post_id}/">u/poster_{post_id}</a></span>'
        f'<a slot="full-post-link" href="{link}"><faceplate-screen-reader-content>{title}'
        f"</faceplate-screen-reader-content></a>"
        f'<a slot="title" id="post-title-t3_{post_id}" href="{link}">{title}</a>'
        f'<div slot="post-media-container"><img src="https://preview.redd.it/{post_id}.jpg"'
        f' alt="{title}"></div>'
        f'<a href="{link}k{post_id[-3:]}x/">top comment</a>'
        f'<a href="{link}">{len(slug)} comments</a>'
        f"</shreddit-post>"
    )


def _listing(title, posts):
    return (
        f"<!doctype html><html><head><title>{title}</title></head><body>"
        '<nav><a href="/r/popular/">Popular</a><a href="/r/all/">All</a>'
        '<a href="/login/">Log In</a></nav>'
        f'<main>{"".join(_card(*post) for post in posts)}</main></body></html>'
    )


_SUB_POSTS = [p for p in _POSTS if p[0] == "videos"] + [
    ("videos", "1q0j9k0", "lighthouse_in_a_storm", "video"),
    ("videos", "1q0m1n2", "street_drummer_solo", "video"),
]
_PAGES = {
    "/": _listing("Reddit - Dive into anything", _POSTS),
    "/r/videos/": _listing("r/videos", _SUB_POSTS),
}


def _serve(route):
    parts = urlsplit(route.request.url)
    body = _PAGES.get(parts.path) if parts.hostname == "www.reddit.com" else None
    if body is None:
        route.fulfill(status=404, body="")
    else:
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)


@pytest.fixture(scope="module")
def page():
    from bulk_downloader import cloak

    with cloak.cloaked_page(headless=True, config={"browser_backend": "playwright"},
                            viewport={"width": 1280, "height": 720}) as browser_page:
        browser_page.route("**/*", _serve)
        yield browser_page


def _crawl(page, tmp_path, listing_url, site_id, site_config=None):
    from bulk_downloader import scene_crawler

    queued = []
    result = scene_crawler.crawl_with_page(
        page, site_id=site_id, listing_url=listing_url,
        site_config=site_config or {"name": "reddit"},
        newest_n=0, max_pages=1, max_scrolls=1, delay_s=0, title_fetch_limit=0,
        db_path=str(tmp_path / f"{site_id}.sqlite"),
        enqueue_fn=lambda sid, url: queued.append(url) or {"added": 1, "dupes": 0, "skipped": 0},
    )
    return result, queued


def _posts_of(listing_path):
    posts = _SUB_POSTS if listing_path == "/r/videos/" else _POSTS
    return sorted("https://www.reddit.com" + _permalink(*post[:3]) for post in posts)


def _diagnose(queued, expected):
    if sorted(queued) == expected:
        return
    shapes = sorted({"/".join(urlsplit(u).path.split("/")[:2] + ["<x>"]) for u in queued})
    pytest.fail(
        f"dl95-reddit-1 r2: reddit listing scan queued {len(queued)} target(s), "
        f"{sum('/comments/' in u for u in queued)} of them post permalinks "
        f"(expected {len(expected)} /r/<sub>/comments/<id>/<slug>/); queued shapes {shapes}: "
        f"{sorted(queued)[:6]}")


@pytest.mark.parametrize("listing_url", [_HOME, _SUB], ids=["home-feed", "subreddit"])
def test_reddit_listing_scan_queues_post_permalinks(page, tmp_path, listing_url):
    path = urlsplit(listing_url).path
    result, queued = _crawl(page, tmp_path, listing_url, "dl95-reddit-r2" + path.replace("/", "-"))
    assert result["state"] == "COMPLETED", result
    expected = _posts_of(path)
    _diagnose(queued, expected)
    assert result["discovered"] == len(expected), result
    assert not [u for u in queued if "/comments/" not in u], queued


def test_declared_scene_patterns_still_decide_alone(page, tmp_path):
    """Control: an operator-declared scene rule is authoritative over the built-in shape.

    The declared rule also matches the gifs post's comment permalink, which the built-in shape
    rejects: both queued proves the declared rule decided alone, the other posts absent proves
    the built-in shape was not merged in."""
    only = r"/r/gifs/comments/[a-z0-9]+/"
    result, queued = _crawl(page, tmp_path, _HOME, "dl95-reddit-r2-declared",
                            {"name": "reddit", "crawler_scene_patterns": only})
    assert result["state"] == "COMPLETED", result
    post = "https://www.reddit.com" + _permalink(*_POSTS[1][:3])
    assert sorted(queued) == [post, post + "k3d4x/"], queued


@pytest.mark.parametrize("listing_url,expected", [
    ("https://www.reddit.com/", True),
    ("https://old.reddit.com/r/videos/", True),
    ("https://reddit.com/r/videos/top/", True),
    ("https://notreddit.com/", False),
    ("https://reddit.com.example.net/", False),
    ("http://127.0.0.1:8000/r/videos/", False),
])
def test_builtin_reddit_shape_is_host_scoped(listing_url, expected):
    from bulk_downloader import scene_crawler

    assert bool(scene_crawler._builtin_scene_patterns(listing_url)) is expected


@pytest.mark.parametrize("url,expected", [
    ("https://www.reddit.com/r/videos/comments/1q0a1b2/train_crossing/", True),
    ("https://www.reddit.com/r/videos/comments/1q0a1b2/", True),
    ("https://www.reddit.com/r/videos/", False),
    ("https://www.reddit.com/user/poster_1q0a1b2/", False),
    ("https://www.reddit.com/r/videos/comments/1q0a1b2/train_crossing/kabcx/", False),
])
def test_builtin_reddit_shape_is_a_post_permalink(url, expected):
    from bulk_downloader import scene_crawler

    patterns = scene_crawler._builtin_scene_patterns("https://www.reddit.com/")
    assert any(p.search(url) for p in patterns) is expected
