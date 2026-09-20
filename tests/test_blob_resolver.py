"""Row 930 -- HTML5-VIDEO-BLOB-URL-RESOLVER-AND-STREAM-MANIFEST-LOCATOR.

``<video src="blob:https://...">`` cannot be fetched. ``blob_resolver`` maps a
blob URL to the HLS/DASH manifests the page requested (CDP ``Network.*``
events, or a recon ``network_log``), groups master + variant playlists into
representations, and reports not-found instead of raising.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from bulk_downloader import blob_resolver

BD_GATE_SCOPE = "module"

FIXTURE = (Path(__file__).parent / "fixtures" / "recon_corpus"
           / "bd-recon_members_adulttime_com_2026-05-14T00-28-04-267Z_SUMMARY.json")

BLOB = "blob:https://members.adulttime.com/a1d9b40d-c692-48bb-9cdd-044a3374cd38"
MASTER = "https://cdn.example/v/53601_01.m3u8?sig=a"
V720 = "https://cdn.example/v/53601_01_720p.m3u8?sig=a"
V1080 = "https://cdn.example/v/53601_01_1080p.m3u8?sig=a"
MASTER_BODY = (
    "#EXTM3U\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=1280x720,CODECS=\"avc1.4d401f,mp4a.40.2\"\n"
    "53601_01_720p.m3u8?sig=a\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=3200000,RESOLUTION=1920x1080\n"
    "53601_01_1080p.m3u8?sig=a\n"
)
MPD = "https://dash.example/m/asset.mpd"
MPD_BODY = (
    '<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011">'
    '<Period><AdaptationSet mimeType="video/mp4">'
    '<Representation id="v0" bandwidth="800000" width="854" height="480"/>'
    '<Representation id="v1" bandwidth="2500000" width="1920" height="1080"/>'
    '</AdaptationSet><AdaptationSet mimeType="audio/mp4">'
    '<Representation id="a0" bandwidth="128000"/>'
    '</AdaptationSet></Period></MPD>'
)


def _cdp(url, rid, ts, *, mime, document_url="https://members.adulttime.com/en/video/x",
         frame="F1", body=None):
    events = [
        ("Network.requestWillBeSent", {
            "requestId": rid, "frameId": frame, "documentURL": document_url,
            "timestamp": ts, "type": "XHR",
            "request": {"url": url, "method": "GET", "headers": {}},
            "initiator": {"type": "script", "url": "https://members.adulttime.com/player.js"},
        }),
        ("Network.responseReceived", {
            "requestId": rid, "frameId": frame, "timestamp": ts + 0.05, "type": "XHR",
            "response": {"url": url, "status": 200, "mimeType": mime, "headers": {}},
        }),
        ("Network.loadingFinished", {"requestId": rid, "timestamp": ts + 0.1}),
    ]
    return events, body


def _feed(resolver, *streams, bodies=None):
    bodies = dict(bodies or {})
    for events, body in streams:
        rid = events[0][1]["requestId"]
        if body is not None:
            bodies[rid] = body
    resolver.body_fetcher = lambda rid: bodies.get(rid)
    for events, _ in streams:
        for method, params in events:
            resolver.feed(method, params)


def test_real_capture_blob_maps_to_the_proxied_hls_master_and_variant():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    player = data["player_elements"][0]
    blob = player["video_state"]["current_src"]
    assert blob.startswith("blob:https://members.adulttime.com/")
    log = data["network_log"]
    assert len(log) == 114
    manifest_rows = [e for e in log if "m3u8" in e["url"]]
    assert len(manifest_rows) == 2
    assert all(e["response_headers"]["content-type"] == "application/x-mpegURL" for e in manifest_rows)
    assert not any(e["url"].split("?")[0].endswith(".m3u8") for e in manifest_rows), \
        "the real proxy hides the .m3u8 inside ?u=; a path-extension check alone would miss it"

    resolver = blob_resolver.BlobResolver()
    fed = resolver.feed_network_log(log)
    assert fed == 114
    assert resolver.manifest_count == 2
    resolver.observe_blob(blob, document_url=data["url"])
    res = resolver.resolve(blob)
    assert res.found
    assert res.kind == "hls"
    assert res.blob_url == blob
    assert res.master.url == manifest_rows[0]["url"]
    assert res.master.upstream_url.endswith(
        "/hls/53601_01.m3u8?uh=06a0f751db7dace85c132ec09be3d13dbda08808eb09d8abca05ce1f5592f1eb"
        "&cui=29873003&Policy=<scrubbed>&Signature=<scrubbed>&Key-Pair-Id=<scrubbed>")
    assert [m.url for m in res.manifests] == [e["url"] for e in manifest_rows]
    assert [r.label for r in res.representations] == ["720p"]
    assert res.representations[0].url == manifest_rows[1]["url"]
    assert res.reason == "origin:members.adulttime.com"


def test_cdp_events_map_a_blob_to_its_master_with_multi_representation_from_the_body():
    resolver = blob_resolver.BlobResolver()
    _feed(resolver,
          _cdp(MASTER, "r1", 100.0, mime="application/vnd.apple.mpegurl", body=MASTER_BODY),
          _cdp(V720, "r2", 100.4, mime="application/vnd.apple.mpegurl"),
          _cdp("https://cdn.example/v/seg1.ts", "r3", 100.6, mime="video/mp2t"))
    assert resolver.manifest_count == 2
    resolver.observe_blob(BLOB, document_url="https://members.adulttime.com/en/video/x")
    res = resolver.resolve(BLOB)
    assert res.found and res.kind == "hls"
    assert res.master.url == MASTER
    assert res.master.body_seen
    reps = {r.label: r for r in res.representations}
    assert set(reps) == {"720p", "1080p"}
    assert reps["720p"].url == V720
    assert reps["720p"].bandwidth == 1400000
    assert reps["720p"].width == 1280 and reps["720p"].height == 720
    assert reps["720p"].observed is True
    assert reps["1080p"].url == V1080
    assert reps["1080p"].observed is False
    assert reps["1080p"].bandwidth == 3200000


def test_dash_manifest_representations_come_from_the_mpd():
    resolver = blob_resolver.BlobResolver()
    _feed(resolver, _cdp(MPD, "d1", 5.0, mime="application/dash+xml", body=MPD_BODY,
                         document_url="https://dash.example/watch/1"))
    blob = "blob:https://dash.example/0000-1111"
    resolver.observe_blob(blob, document_url="https://dash.example/watch/1")
    res = resolver.resolve(blob)
    assert res.found and res.kind == "dash"
    assert res.master.url == MPD
    labels = [(r.label, r.bandwidth, r.width, r.height) for r in res.representations]
    assert labels == [("480p", 800000, 854, 480), ("1080p", 2500000, 1920, 1080),
                      ("a0", 128000, None, None)]


def test_manifests_from_another_origin_do_not_claim_the_blob():
    resolver = blob_resolver.BlobResolver()
    _feed(resolver,
          _cdp("https://other.example/x.m3u8", "o1", 1.0, mime="application/vnd.apple.mpegurl",
               document_url="https://other.example/embed"),
          _cdp(MASTER, "r1", 2.0, mime="application/vnd.apple.mpegurl",
               document_url="https://members.adulttime.com/en/video/x"))
    resolver.observe_blob(BLOB, document_url="https://members.adulttime.com/en/video/x")
    res = resolver.resolve(BLOB)
    assert res.found
    assert [m.url for m in res.manifests] == [MASTER]


def test_manifests_requested_after_the_blob_was_observed_are_not_its_source():
    resolver = blob_resolver.BlobResolver()
    _feed(resolver, _cdp(MASTER, "r1", 10.0, mime="application/vnd.apple.mpegurl"))
    resolver.observe_blob(BLOB, observed_at=20.0)
    _feed(resolver, _cdp(V1080, "r9", 30.0, mime="application/vnd.apple.mpegurl"))
    res = resolver.resolve(BLOB)
    assert [m.url for m in res.manifests] == [MASTER]
    assert resolver.manifest_count == 2


def test_unknown_blob_and_no_manifests_fail_soft():
    resolver = blob_resolver.BlobResolver()
    res = resolver.resolve("blob:https://nowhere.example/abc")
    assert not res.found
    assert res.reason == "blob not observed"
    assert res.manifests == () and res.representations == () and res.master is None
    resolver.observe_blob("blob:https://nowhere.example/abc")
    res = resolver.resolve("blob:https://nowhere.example/abc")
    assert not res.found
    assert res.reason == "no manifest request observed for origin nowhere.example"
    assert resolver.resolve("not-a-blob").reason == "not a blob: URL"


def test_malformed_events_bodies_and_fetchers_never_raise():
    resolver = blob_resolver.BlobResolver()
    resolver.feed("Network.requestWillBeSent", {})
    resolver.feed("Network.responseReceived", {"requestId": "zz"})
    resolver.feed("Network.loadingFinished", None)
    resolver.feed("Network.requestWillBeSent", {"requestId": 1, "request": "nope"})
    resolver.feed("Bogus.event", {"requestId": "r1"})
    assert resolver.feed_network_log([{"url": 5}, "junk", None]) == 0
    assert resolver.manifest_count == 0

    def _boom(_rid):
        raise RuntimeError("cdp gone")

    resolver.body_fetcher = _boom
    for method, params in _cdp(MASTER, "r1", 1.0, mime="application/vnd.apple.mpegurl")[0]:
        resolver.feed(method, params)
    assert resolver.manifest_count == 1
    assert resolver.errors == ["body r1: RuntimeError: cdp gone"]
    resolver.observe_blob(BLOB)
    res = resolver.resolve(BLOB)
    assert res.found
    assert res.master.body_seen is False
    assert res.representations == ()

    dash = blob_resolver.BlobResolver()
    _feed(dash, _cdp(MPD, "d1", 2.0, mime="application/dash+xml", body="<MPD><unclosed",
                     document_url="https://dash.example/w"))
    dash.observe_blob("blob:https://dash.example/1")
    res = dash.resolve("blob:https://dash.example/1")
    assert res.found and res.kind == "dash"
    assert res.master.body_seen is True
    assert res.representations == ()


def test_live_driver_wires_cdp_collects_blob_sources_and_resolves(monkeypatch):
    class Client:
        def __init__(self):
            self.sent = []
            self.handlers = {}

        def send(self, method, params=None):
            self.sent.append((method, params))
            if method == "Network.getResponseBody":
                # what Chromium actually returns for an HLS playlist (E1)
                return {"body": base64.b64encode(MASTER_BODY.encode()).decode(), "base64Encoded": True}
            return {}

        def on(self, event, handler):
            self.handlers[event] = handler

    class Context:
        def __init__(self):
            self.client = Client()

        def new_cdp_session(self, page):
            return self.client

    class Page:
        url = "https://members.adulttime.com/en/video/x"

        def __init__(self):
            self.context = Context()
            self.evaluated = []

        def evaluate(self, script):
            self.evaluated.append(script)
            return [BLOB, "https://plain.example/direct.mp4", BLOB]

    page = Page()
    session = blob_resolver.attach(page)
    client = page.context.client
    assert client.sent[0] == ("Network.enable", None)
    assert set(client.handlers) == {
        "Network.requestWillBeSent", "Network.responseReceived", "Network.loadingFinished",
        "Network.loadingFailed"}
    for events, _ in (_cdp(MASTER, "r1", 1.0, mime="application/vnd.apple.mpegurl"),):
        for method, params in events:
            client.handlers[method](params)
    results = blob_resolver.resolve_page(page, session)
    assert [r.blob_url for r in results] == [BLOB]
    assert results[0].found and results[0].master.url == MASTER
    assert {r.label for r in results[0].representations} == {"720p", "1080p"}
    assert "blob:" in page.evaluated[0]
    assert ("Network.getResponseBody", {"requestId": "r1"}) in client.sent


def test_labels_are_derived_from_resolution_then_filename_then_id():
    assert blob_resolver.label_for(width=1920, height=1080, url="x", rep_id=None) == "1080p"
    assert blob_resolver.label_for(width=None, height=None,
                                   url="https://c/x/53601_01_720p.m3u8?u=1", rep_id=None) == "720p"
    assert blob_resolver.label_for(width=None, height=None, url="https://c/x/main.m3u8",
                                   rep_id="v3") == "v3"
    assert blob_resolver.label_for(width=None, height=None, url="https://c/x/main.m3u8",
                                   rep_id=None) == "main.m3u8"
    with pytest.raises(TypeError):
        blob_resolver.label_for(1920, 1080, "x", None)


def test_every_recon_capture_with_a_blob_player_resolves_to_hls():
    corpus = sorted((Path(__file__).parent / "fixtures" / "recon_corpus").glob("*.json"))
    assert len(corpus) == 6
    outcomes = {}
    for path in corpus:
        data = json.loads(path.read_text(encoding="utf-8"))
        blobs = sorted({
            (p.get("video_state") or {}).get(k)
            for p in data.get("player_elements") or []
            for k in ("current_src", "src_attribute")
            if str((p.get("video_state") or {}).get(k)).startswith("blob:")
        })
        resolver = blob_resolver.BlobResolver()
        resolver.feed_network_log(data.get("network_log") or [])
        host = path.name.split("_", 1)[1].split("_2026")[0].replace("_", ".")
        for blob in blobs:
            resolver.observe_blob(blob, document_url=data["url"])
            res = resolver.resolve(blob)
            outcomes[host] = (res.found, res.kind, resolver.manifest_count,
                              [r.label for r in res.representations])
        if not blobs:
            outcomes[host] = ("no-blob", resolver.manifest_count)
    assert outcomes == {
        "members.adulttime.com": (True, "hls", 2, ["720p"]),
        "members.dfxtra.com": (True, "hls", 2, ["720p"]),
        "site-ma.brazzers.com": (True, "hls", 2, ["index-v1-a1.m3u8"]),
        "members.naughtyamerica.com": ("no-blob", 0),
        "members.nubilefilms.com": ("no-blob", 0),
        "members.vixen.com": ("no-blob", 0),
    }


def test_a_signed_manifest_url_without_an_extension_is_detected_by_content_type():
    resolver = blob_resolver.BlobResolver()
    signed = "https://cdn.example/stream/playlist?token=abc"
    _feed(resolver, _cdp(signed, "s1", 1.0, mime="application/x-mpegURL"),
          _cdp("https://cdn.example/stream/other?token=abc", "s2", 1.1, mime="application/json"))
    assert resolver.manifest_count == 1
    assert blob_resolver.manifest_kind(signed, "") == ""
    assert blob_resolver.manifest_kind(signed, "application/x-mpegURL") == "hls"
    assert blob_resolver.manifest_kind("https://d.example/a?x=1", "application/dash+xml") == "dash"


def test_the_master_is_the_playlist_that_declares_variants_even_when_requested_second():
    resolver = blob_resolver.BlobResolver()
    _feed(resolver,
          _cdp(V720, "r2", 100.0, mime="application/vnd.apple.mpegurl"),
          _cdp(MASTER, "r1", 100.5, mime="application/vnd.apple.mpegurl", body=MASTER_BODY))
    resolver.observe_blob(BLOB)
    res = resolver.resolve(BLOB)
    assert res.master.url == MASTER
    assert [m.url for m in res.manifests] == [V720, MASTER]
    assert {r.label: r.observed for r in res.representations} == {"720p": True, "1080p": False}


def test_a_manifest_request_that_never_finished_is_not_a_source():
    resolver = blob_resolver.BlobResolver()
    events, _ = _cdp(MASTER, "r1", 1.0, mime="application/vnd.apple.mpegurl")
    for method, params in events[:2]:
        resolver.feed(method, params)
    assert resolver.manifest_count == 0
    resolver.observe_blob(BLOB)
    assert not resolver.resolve(BLOB).found
    resolver.feed(*events[2])
    assert resolver.manifest_count == 1
    assert resolver.resolve(BLOB).found


def test_an_event_that_breaks_inside_the_parser_is_recorded_not_raised():
    resolver = blob_resolver.BlobResolver()
    resolver.feed("Network.requestWillBeSent", {
        "requestId": "bad", "timestamp": "not-a-number",
        "request": {"url": MASTER}})
    assert resolver.errors == [
        "feed Network.requestWillBeSent: ValueError: could not convert string to float: 'not-a-number'"]
    assert resolver.manifest_count == 0


def test_variant_uris_resolve_like_the_player_absolute_protocol_relative_and_dotted():
    """E2: a master at https://cdn.example/hls/abc/master.m3u8 declaring
    variants as an absolute path, a protocol-relative URL, a dotted path and a
    bare name resolves each per RFC 3986 (urljoin), and the observed variant
    is matched by that resolved URL -- not duplicated under a filename label."""
    master = "https://cdn.example/hls/abc/master.m3u8"
    body = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=1280x720\n/hls/abc/720p/index.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=3200000,RESOLUTION=1920x1080\n//cdn2.example/hls/abc/1080p/index.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=854x480\n../v/480p/index.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=640x360\n360p.m3u8\n"
    )
    resolver = blob_resolver.BlobResolver()
    doc = "https://site.example/watch/1"
    _feed(resolver,
          _cdp(master, "m", 10.0, mime="application/vnd.apple.mpegurl", body=body, document_url=doc),
          _cdp("https://cdn.example/hls/abc/720p/index.m3u8", "v", 10.5,
               mime="application/vnd.apple.mpegurl", document_url=doc))
    resolver.observe_blob("blob:https://site.example/1", document_url=doc)
    res = resolver.resolve("blob:https://site.example/1")
    assert res.found and res.master.url == master
    reps = {r.label: r for r in res.representations}
    assert set(reps) == {"720p", "1080p", "480p", "360p"}, reps
    assert reps["720p"].url == "https://cdn.example/hls/abc/720p/index.m3u8" and reps["720p"].observed is True
    assert reps["1080p"].url == "https://cdn2.example/hls/abc/1080p/index.m3u8" and reps["1080p"].observed is False
    assert reps["480p"].url == "https://cdn.example/hls/v/480p/index.m3u8"
    assert reps["360p"].url == "https://cdn.example/hls/abc/360p.m3u8"


def test_an_aborted_manifest_load_is_dropped_from_pending():
    resolver = blob_resolver.BlobResolver()
    events, _ = _cdp(V720, "gone", 5.0, mime="application/vnd.apple.mpegurl")
    resolver.feed(*events[0])
    resolver.feed(*events[1])
    resolver.feed("Network.loadingFailed", {"requestId": "gone", "errorText": "net::ERR_ABORTED", "canceled": True})
    assert resolver.manifest_count == 0 and "gone" not in resolver._pending
    resolver.feed("Network.loadingFinished", {"requestId": "gone", "timestamp": 5.2})
    assert resolver.manifest_count == 0


def test_real_chromium_hls_master_body_is_seen_and_variants_are_listed():
    """E1 on the runtime entry point (the reviewer's probe): headless Chromium
    fetches a master + one variant playlist from a loopback server and sets
    video.src to a MediaSource blob. attach() must see the master BODY
    (Chromium returns it base64Encoded) so both declared variants are listed,
    the fetched one observed. Positive control: without the decode the
    representations collapse to the single observed playlist under a filename
    label -- asserted by the resolution shape, not by mocking."""
    import http.server
    import threading
    from playwright.sync_api import sync_playwright

    master = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=1280x720\n/hls/720p/index.m3u8\n"
              "#EXT-X-STREAM-INF:BANDWIDTH=3200000,RESOLUTION=1920x1080\n/hls/1080p/index.m3u8\n").encode()
    variant = b"#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\nseg0.ts\n#EXT-X-ENDLIST\n"
    html = (b"<html><body><video id=v></video><script>"
            b"(async () => { await fetch('/hls/master.m3u8'); await fetch('/hls/720p/index.m3u8');"
            b" const v = document.getElementById('v'); v.src = URL.createObjectURL(new MediaSource());"
            b" window.__ready = true; })();</script></body></html>")

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/hls/master.m3u8":
                body, ctype = master, "application/vnd.apple.mpegurl"
            elif self.path.startswith("/hls/") and self.path.endswith(".m3u8"):
                body, ctype = variant, "application/x-mpegURL"
            else:
                body, ctype = html, "text/html"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{srv.server_port}"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            resolver = blob_resolver.attach(page)
            page.goto(origin + "/watch")
            page.wait_for_function("window.__ready === true")
            page.wait_for_timeout(200)
            results = blob_resolver.resolve_page(page, resolver)
            browser.close()
    finally:
        srv.shutdown()
    assert len(results) == 1 and results[0].found, results
    res = results[0]
    assert res.master.url == origin + "/hls/master.m3u8"
    assert res.master.body_seen is True, res.master
    assert all(m.body_seen for m in resolver.manifests), [(m.url, m.body_seen) for m in resolver.manifests]
    reps = {r.label: r for r in res.representations}
    assert set(reps) == {"720p", "1080p"}, reps
    assert reps["720p"].url == origin + "/hls/720p/index.m3u8" and reps["720p"].observed is True
    assert reps["1080p"].url == origin + "/hls/1080p/index.m3u8" and reps["1080p"].observed is False
