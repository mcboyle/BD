"""O1789 streaming-manifest-url-filter r1 (split from manifest-url-filter A4).

streaming_manifest joins HLS variant/segment/key/map and DASH BaseURL/SegmentTemplate/
Initialization URLs; a public manifest naming a local host (127.0.0.1, [::1], 10.x and
the encoded IPv4 spellings deep_detect.manifests._local_host knows) is refused there.
Each test carries a SMF-<kind> diagnostic; every fix test has a control beside it.
"""

from urllib.parse import urlsplit

import pytest

from bulk_downloader import streaming_manifest as sm

BD_GATE_SCOPE = "module"
PUB = "https://cdn.test/m/master.m3u8"
LOCAL_BASE = "http://127.0.0.1:8080/m/master.m3u8"
LOCAL_HOSTS = ["127.0.0.1:8080", "[::1]", "10.0.0.5", "2130706433", "0x7f.1", "127.1"]


def _on(host, url):
    """url names host (parsed, not substring)."""
    return bool(url) and urlsplit(url).hostname == urlsplit(f"http://{host}/").hostname


# ── HLS master variants ──────────────────────────────────────────────────────
def _master(host):
    return ("#EXTM3U\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=9000,RESOLUTION=1920x1080\n"
            f"http://{host}/admin.m3u8\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=1000,RESOLUTION=640x360\nv360.m3u8\n")


@pytest.mark.parametrize("host", LOCAL_HOSTS)
def test_hls_master_local_variant_refused(host):
    out = sm.parse_streaming_manifest(_master(host), base_url=PUB)
    uris = [v.uri for v in out.variants]
    assert uris == ["https://cdn.test/m/v360.m3u8"], (
        f"SMF-HLS-VARIANT: public master keeps variant on local host {host!r}: {uris}")
    assert out.variants[0].master_index == 1, "SMF-HLS-VARIANT: master_index shifted by the refusal"


def test_hls_master_control_local_master_and_public_variants_kept():
    out = sm.parse_streaming_manifest(_master("127.0.0.1:8080"), base_url=LOCAL_BASE)
    assert [(v.uri, v.master_index) for v in out.variants] == [
        ("http://127.0.0.1:8080/admin.m3u8", 0), ("http://127.0.0.1:8080/m/v360.m3u8", 1)]
    out = sm.parse_streaming_manifest(_master("cdn2.test"), base_url=PUB)
    assert [v.uri for v in out.variants] == ["http://cdn2.test/admin.m3u8", "https://cdn.test/m/v360.m3u8"]


# ── HLS media playlist: segment, key and map URIs ────────────────────────────
def _media(seg="http://cdn.test/s1.ts", key=None, map_uri=None):
    lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:4", "#EXTINF:4,", "s0.ts"]
    if key:
        lines.append(f'#EXT-X-KEY:METHOD=AES-128,URI="{key}"')
    if map_uri:
        lines.append(f'#EXT-X-MAP:URI="{map_uri}"')
    lines += ["#EXTINF:4,", seg, "#EXT-X-ENDLIST"]
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("host", LOCAL_HOSTS)
@pytest.mark.parametrize("where", ["segment", "key", "map"])
def test_hls_media_local_segment_key_or_map_refused(host, where):
    url = f"http://{host}/x"
    text = _media(**{"segment": {"seg": url}, "key": {"key": url}, "map": {"map_uri": url}}[where])
    out = sm.parse_streaming_manifest(text, base_url=PUB)
    leaked = [s.uri for s in out.segments if any(_on(host, u) for u in (s.uri, s.key_uri, s.initialization_uri))]
    assert not leaked, f"SMF-HLS-{where.upper()}: public playlist keeps a segment whose {where} is on {host!r}: {leaked}"
    assert [s.uri for s in out.segments] == ["https://cdn.test/m/s0.ts"]
    assert out.segments[0].sequence_number == 0


def test_hls_media_control_public_and_local_from_local_kept():
    out = sm.parse_streaming_manifest(
        _media(key="https://keys.test/k", map_uri="init.mp4"), base_url=PUB)
    assert [(s.uri, s.key_uri, s.initialization_uri) for s in out.segments] == [
        ("https://cdn.test/m/s0.ts", None, None),
        ("http://cdn.test/s1.ts", "https://keys.test/k", "https://cdn.test/m/init.mp4")]
    out = sm.parse_streaming_manifest(_media(seg="http://127.0.0.1:8080/s1.ts", key="k.bin"), base_url=LOCAL_BASE)
    assert [s.uri for s in out.segments] == ["http://127.0.0.1:8080/m/s0.ts", "http://127.0.0.1:8080/s1.ts"]
    assert out.segments[1].key_uri == "http://127.0.0.1:8080/m/k.bin"


# ── DASH BaseURL / SegmentTemplate / Initialization ──────────────────────────
MPD_URL = "https://cdn.test/d/manifest.mpd"


def _mpd(root_base="", rep_base="", media="seg-$Number$.m4s", init="init.mp4"):
    rb = f"<BaseURL>{root_base}</BaseURL>" if root_base else ""
    pb = f"<BaseURL>{rep_base}</BaseURL>" if rep_base else ""
    return ('<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011" mediaPresentationDuration="PT8S">'
            f'{rb}<Period duration="PT8S"><AdaptationSet mimeType="video/mp4">'
            f'<SegmentTemplate media="{media}" initialization="{init}" duration="4" timescale="1"/>'
            f'<Representation id="a" bandwidth="2000" width="1280" height="720">{pb}</Representation>'
            '<Representation id="b" bandwidth="1000" width="640" height="360"/>'
            "</AdaptationSet></Period></MPD>")


@pytest.mark.parametrize("host", LOCAL_HOSTS)
@pytest.mark.parametrize("where", ["root_base", "rep_base", "media", "init"])
def test_dash_local_address_refused(host, where):
    url = {"root_base": f"http://{host}/", "rep_base": f"http://{host}/a/",
           "media": f"http://{host}/seg-$Number$.m4s", "init": f"http://{host}/init.mp4"}[where]
    out = sm.parse_streaming_manifest(_mpd(**{where: url}), base_url=MPD_URL)
    leaked = sorted({v.stream_id for v in out.variants
                     for u in [v.initialization_uri, v.segment_template.get("base_url")] + [s.uri for s in v.segments]
                     if _on(host, u)})
    assert not leaked, f"SMF-DASH-{where.upper()}: public MPD keeps representation(s) {leaked} addressed at {host!r}"
    if where == "rep_base":  # only representation a names the local host
        assert [v.stream_id for v in out.variants] == ["b"]


def _mpd_list_or_base(kind, url):
    seg = (f'<SegmentList duration="4" timescale="1"><SegmentURL media="{url}"/></SegmentList>'
           if kind == "SegmentList" else f"<BaseURL>{url}</BaseURL><SegmentBase/>")
    return ('<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011" mediaPresentationDuration="PT4S">'
            '<Period duration="PT4S"><AdaptationSet mimeType="video/mp4">'
            f'<Representation id="a" bandwidth="2000" width="1280" height="720">{seg}</Representation>'
            '<Representation id="b" bandwidth="1000" width="640" height="360">'
            "<BaseURL>b.mp4</BaseURL><SegmentBase/></Representation></AdaptationSet></Period></MPD>")


@pytest.mark.parametrize("kind", ["SegmentList", "SegmentBase"])
@pytest.mark.parametrize("host", ["127.0.0.1:8080", "[::1]", "2130706433"])
def test_dash_segment_list_or_base_local_refused(host, kind):
    out = sm.parse_streaming_manifest(_mpd_list_or_base(kind, f"http://{host}/a.mp4"), base_url=MPD_URL)
    ids = [v.stream_id for v in out.variants]
    assert ids == ["b"], f"SMF-DASH-{kind.upper()}: public MPD keeps representations {ids} on {host!r}"


@pytest.mark.parametrize("kind", ["SegmentList", "SegmentBase"])
def test_dash_control_segment_list_or_base_public_kept(kind):
    out = sm.parse_streaming_manifest(_mpd_list_or_base(kind, "https://cdn2.test/a.mp4"), base_url=MPD_URL)
    assert [(v.stream_id, v.uri) for v in out.variants] == [
        ("a", "https://cdn2.test/a.mp4"), ("b", "https://cdn.test/d/b.mp4")]


def test_dash_control_public_mpd_unchanged():
    out = sm.parse_streaming_manifest(_mpd(), base_url=MPD_URL)
    assert [v.stream_id for v in out.variants] == ["a", "b"]
    a = out.variants[0]
    assert a.initialization_uri == "https://cdn.test/d/init.mp4"
    assert [s.uri for s in a.segments] == ["https://cdn.test/d/seg-1.m4s", "https://cdn.test/d/seg-2.m4s"]


def test_dash_control_local_mpd_names_local():
    out = sm.parse_streaming_manifest(_mpd(root_base="http://127.0.0.1:8080/v/"),
                                      base_url="http://127.0.0.1:8080/manifest.mpd")
    assert [v.stream_id for v in out.variants] == ["a", "b"]
    assert out.variants[0].segments[0].uri == "http://127.0.0.1:8080/v/seg-1.m4s"


@pytest.mark.parametrize("host", ["134744072", "cdn2.test", "8.8.8.8"])
def test_control_public_hosts_not_refused(host):
    out = sm.parse_streaming_manifest(_mpd(root_base=f"http://{host}/"), base_url=MPD_URL)
    assert [v.stream_id for v in out.variants] == ["a", "b"]
