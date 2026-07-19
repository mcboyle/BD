"""Recursion-depth guard for deep_detect's JSON media walk.

``_walk_json_for_media`` (and the ``extract_state_blob_urls`` path that feeds it
a page's parsed ``__NEXT_DATA__`` / state-blob JSON) recurse once per JSON
nesting level with no bound. A site returning a deeply-nested JSON body or state
blob (an API response or a hostile/pathological page) drove it past Python's
recursion limit -> ``RecursionError``. In the runtime deep-detect fallback the
runner wraps the call (so it degrades to "fallback skipped"), but the walker is
also reached from other callers; bounding the walker itself makes it robust
everywhere — the same fix shape as the v3.66.x export-redaction deep-DOM cap.

Real API responses / state blobs nest only a handful of levels, so the cap is far
beyond any real structure and media URLs near the top are still found; only a
pathological tree is cut off (and no longer crashes).

RED on pristine (deep JSON raises RecursionError); GREEN after the cap.
"""

import json

from bulk_downloader.deep_detect import (_walk_json_for_media,
                                         extract_state_blob_urls)


def _deep_json(n):
    root = {}
    cur = root
    for _ in range(n):
        nxt = {}
        cur["a"] = nxt
        cur = nxt
    cur["url"] = "https://cdn.example/video.mp4"
    return root


def test_walk_json_for_media_is_bounded():
    try:
        _walk_json_for_media(_deep_json(4000))
    except RecursionError:
        raise AssertionError(
            "_walk_json_for_media raised RecursionError on deeply-nested JSON — "
            "bound the recursion depth")


def test_extract_state_blob_urls_bounded_on_deep_blob():
    html = ('<script type="application/json" id="__NEXT_DATA__">'
            + json.dumps(_deep_json(4000)) + '</script>')
    try:
        extract_state_blob_urls(html)
    except RecursionError:
        raise AssertionError(
            "extract_state_blob_urls raised RecursionError on a deep state blob")


def test_shallow_json_media_still_found():
    # A realistic shallow structure: the media URL must still be discovered.
    data = {"props": {"pageProps": {"video": {"src": "https://cdn/x/movie.mp4"}}}}
    found = _walk_json_for_media(data)
    urls = [r.get("url") for r in found]
    assert any("movie.mp4" in (u or "") for u in urls), found


def test_extract_jsonld_media_bounded_on_deep_chain():
    # A deeply-nested JSON-LD hasPart chain (acyclic, so the id()-cycle guard
    # does NOT stop it) must not RecursionError — same class/fix as the JSON walk.
    from bulk_downloader.deep_detect import extract_jsonld_media
    import json as _json
    leaf = {"@type": "VideoObject", "contentUrl": "https://cdn/x/clip.mp4"}
    node = leaf
    for _ in range(4000):
        node = {"@type": "CollectionPage", "hasPart": node}
    html = ('<script type="application/ld+json">' + _json.dumps(node)
            + '</script>')
    try:
        extract_jsonld_media(html)
    except RecursionError:
        raise AssertionError(
            "extract_jsonld_media raised RecursionError on a deep JSON-LD chain")


def test_extract_jsonld_media_shallow_still_found():
    from bulk_downloader.deep_detect import extract_jsonld_media
    html = ('<script type="application/ld+json">'
            '{"@type":"VideoObject","contentUrl":"https://cdn/x/real.mp4"}'
            '</script>')
    out = extract_jsonld_media(html)
    blob = repr(out)
    assert "real.mp4" in blob, out
