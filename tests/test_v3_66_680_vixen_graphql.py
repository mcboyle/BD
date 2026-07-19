"""v3.66.680 (B1/P1): Vixen extractor Path C — GraphQL POST fallback.

Synthetic structural fixtures only (no site content): we exercise the
GraphQL-response -> VixenResult seam and the query builder. The CDN host
string is the matcher seam, not content.
"""
import types
import bulk_downloader.extractors_vixen as vx


def test_extract_from_graphql_response_finds_url():
    payload = {"data": {"findOneVideo": {
        "title": "Scene Title",
        "videoManifestUrl": "https://cdn.vixen.com/video/mp4_2160/abc123/9999/NAME_2160P.mp4",
        "duration": 1200,
    }}}
    r = vx.extract_from_graphql_response(payload)
    assert r.ok is True
    assert r.url.endswith("_2160P.mp4")
    assert r.via == "graphql"
    assert r.tier == 2160


def test_extract_from_graphql_response_nested_sources():
    payload = {"data": {"findOneVideo": {
        "title": "Scene Title",
        "sources": [
            {"src": "https://cdn.blacked.com/video/mp4_480/x/1/N_480P.mp4", "height": 480},
            {"src": "https://cdn.blacked.com/video/mp4_2160/x/1/N_2160P.mp4", "height": 2160},
        ],
    }}}
    r = vx.extract_from_graphql_response(payload)
    assert r.ok is True
    assert 2160 in r.available_tiers
    assert r.tier == 2160  # highest by default


def test_extract_from_graphql_response_empty():
    assert vx.extract_from_graphql_response({"data": {"findOneVideo": None}}).ok is False
    assert vx.extract_from_graphql_response({}).ok is False


def test_build_graphql_query_carries_scene_ref():
    body = vx.build_graphql_query("https://members.vixen.com/videos/my-scene-slug")
    assert isinstance(body, dict)
    assert "query" in body and "variables" in body
    # the scene slug from the URL path must appear in the variables
    assert "my-scene-slug" in str(body["variables"])


def test_extract_via_graphql_uses_page_request():
    payload = {"data": {"findOneVideo": {
        "title": "S", "videoUrl": "https://cdn.tushy.com/video/mp4_1080/z/2/N_1080P.mp4"}}}

    class _Resp:
        def json(self_inner): return payload
        ok = True
        status = 200

    class _Req:
        def post(self_inner, url, **kw): return _Resp()

    class _Page:
        url = "https://members.tushy.com/videos/some-slug"
        request = _Req()
        def content(self_inner): return "<html>no next data, no video src</html>"

    r = vx.extract_via_graphql(_Page())
    assert r.ok is True
    assert r.via == "graphql"
    assert r.tier == 1080


def test_extract_from_page_falls_through_to_graphql():
    payload = {"data": {"findOneVideo": {
        "title": "S", "videoUrl": "https://cdn.deeper.com/video/mp4_720/z/2/N_720P.mp4"}}}

    class _Resp:
        def json(self_inner): return payload
        ok = True; status = 200
    class _Req:
        def post(self_inner, url, **kw): return _Resp()
    class _Page:
        url = "https://members.deeper.com/videos/slug"
        request = _Req()
        def content(self_inner): return "<html>nothing extractable here</html>"

    r = vx.extract_from_page(_Page())
    assert r.ok is True and r.via == "graphql"
