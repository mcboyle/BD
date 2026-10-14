"""O1789 manifest-url-filter r1: A1 + A4 on deep_detect.manifests (stacked on detect-misc-fix r1 F8).

A1: encoded IPv4 loopback spellings (2130706433, 0x7f.1, 127.1, ...) are local hosts.
A4: an #EXT-X-MEDIA URI goes through the same filter as a STREAM-INF variant.
    DASH / Smooth: the deep_detect parsers emit no URL to filter -- pinned below.
Each test carries a MUF-<item> diagnostic; every fix test has a control beside it.
"""

import pytest

from bulk_downloader.deep_detect import candidates as C
from bulk_downloader.deep_detect import manifests as M

BD_GATE_SCOPE = "module"
BASE = "https://cdn.test/master.m3u8"
ENCODED_LOCAL = ["2130706433", "0x7f.1", "127.1", "0x7f000001", "0", "0177.0.0.1", "0x7F.0.0.1", "10.1"]


# ── A1: legacy inet_aton spellings of a local IPv4 address are local ────────
@pytest.mark.parametrize("host", ENCODED_LOCAL)
def test_a1_encoded_ipv4_local_host(host):
    assert M._local_host(host) is True, f"MUF-A1: _local_host({host!r}) -> False (encoded local IPv4 passes)"


@pytest.mark.parametrize("host", ENCODED_LOCAL)
def test_a1_encoded_ipv4_variant_refused_end_to_end(host):
    text = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1,RESOLUTION=1280x720\n"
            f"http://{host}:8080/admin.m3u8\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=1,RESOLUTION=640x360\nv360.m3u8\n")
    out = M.parse_hls_master(text, base_url=BASE)
    urls = [v["url"] for v in out["variants"]]
    assert urls == ["https://cdn.test/v360.m3u8"], (
        f"MUF-A1: variant on encoded local host {host!r} admitted from a public manifest: {urls}")
    assert any("refused" in w for w in out["warnings"]), out["warnings"]


@pytest.mark.parametrize("host", ["134744072", "0x8.0x8.0x8.0x8", "8.8.8.8", "example.com", "cdn.test",
                                  "1.example", "0x7f.example", "deadbeef", "127.0.0.1.example.com"])
def test_a1_control_public_or_name_not_local(host):
    assert M._local_host(host) is False, f"MUF-A1: _local_host({host!r}) -> True (public / name judged local)"


def test_a1_control_encoded_public_variant_admitted_and_localhost_still_local():
    text = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1,RESOLUTION=1280x720\nhttp://134744072/v.m3u8\n"
    assert [v["url"] for v in M.parse_hls_master(text, base_url=BASE)["variants"]] == ["http://134744072/v.m3u8"]
    assert M._local_host("localhost") and M._local_host("127.0.0.1") and M._local_host("::1")


# ── A4: #EXT-X-MEDIA URI through the variant filter ─────────────────────────
def _master(uri, kind="AUDIO"):
    return ("#EXTM3U\n"
            f'#EXT-X-MEDIA:TYPE={kind},GROUP-ID="g",NAME="n",LANGUAGE="en",URI="{uri}"\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=1,RESOLUTION=640x360,AUDIO="g"\nv360.m3u8\n')


@pytest.mark.parametrize("kind,key", [("AUDIO", "audio"), ("SUBTITLES", "subtitles")])
@pytest.mark.parametrize("uri", ["http://127.0.0.1:8080/a.m3u8", "http://[::1]/a.m3u8", "file:///etc/passwd",
                                 "javascript:alert(1)", "http://2130706433/a.m3u8", "http://10.0.0.5/a.m3u8"])
def test_a4_ext_x_media_uri_refused(uri, kind, key):
    out = M.parse_hls_master(_master(uri, kind), base_url=BASE)
    urls = [t["url"] for t in out[key]]
    assert urls == [], f"MUF-A4: EXT-X-MEDIA {kind} URI {uri!r} kept from a public manifest: {urls}"
    assert any("EXT-X-MEDIA URI refused" in w for w in out["warnings"]), out["warnings"]
    assert [v["url"] for v in out["variants"]] == ["https://cdn.test/v360.m3u8"]


@pytest.mark.parametrize("uri,base,want", [
    ("https://cdn.test/a.m3u8", BASE, "https://cdn.test/a.m3u8"),
    ("audio/en.m3u8", BASE, "https://cdn.test/audio/en.m3u8"),
    ("http://127.0.0.1:8080/a.m3u8", "http://127.0.0.1:8080/master.m3u8", "http://127.0.0.1:8080/a.m3u8"),
])
def test_a4_control_public_relative_and_local_from_local_kept(uri, base, want):
    out = M.parse_hls_master(_master(uri), base_url=base)
    assert [t["url"] for t in out["audio"]] == [want] and not any("EXT-X-MEDIA" in w for w in out["warnings"])


def test_a4_control_inline_track_keeps_url_none():
    text = ('#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="g",NAME="inline"\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=1,AUDIO="g"\nv.m3u8\n')
    assert [t["url"] for t in M.parse_hls_master(text, base_url=BASE)["audio"]] == [None]


# ── A4 (DASH / Smooth): no URL is emitted, so none can escape the filter ────
def _strings(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from _strings(v)
    elif isinstance(o, str):
        yield o


_MPD = ('<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011">'
        "<BaseURL>http://127.0.0.1:8080/</BaseURL><Period><AdaptationSet mimeType=\"video/mp4\">"
        '<SegmentTemplate media="http://127.0.0.1:8080/$Number$.m4s" initialization="http://127.0.0.1:8080/i.mp4"/>'
        '<Representation id="v1" bandwidth="1000" width="1280" height="720">'
        "<BaseURL>http://127.0.0.1:8080/v1/</BaseURL></Representation></AdaptationSet></Period></MPD>")
_ISM = ('<?xml version="1.0"?><SmoothStreamingMedia MajorVersion="2" MinorVersion="0">'
        '<StreamIndex Type="video" Name="video" Url="http://127.0.0.1:8080/QualityLevels({bitrate})/Fragments(video={start time})">'
        '<QualityLevel Index="0" Bitrate="1000" FourCC="H264" MaxWidth="1280" MaxHeight="720"/>'
        "</StreamIndex></SmoothStreamingMedia>")


def test_a4_dash_emits_no_url():
    dash = M.parse_dash_mpd(_MPD, base_url=BASE)
    assert dash["kind"] == "dash_mpd" and len(dash["video"]) == 1  # the probe sees the representation
    cands = C._flatten_download_candidates(
        resolution_cards=[], hls_master=None, dash_mpd=dash, state_urls=[], provider_embeds=[],
        player_configs=[], jsonld_media=[], post_reveal=[], base_url=BASE)
    assert cands and all(c["url"] is None for c in cands), f"MUF-A4: DASH candidate carries a URL: {cands}"
    leaked = [s for s in _strings([dash, cands]) if "127.0.0.1" in s]
    assert not leaked, f"MUF-A4: DASH parse emits an unfiltered URL: {leaked}"


def test_a4_smooth_emits_no_url():
    sm = M.parse_smooth_streaming(_ISM, base_url=BASE)
    assert sm["kind"] == "smooth_streaming" and len(sm["video"]) == 1
    leaked = [s for s in _strings(sm) if "127.0.0.1" in s]
    assert not leaked, f"MUF-A4: Smooth parse emits an unfiltered URL: {leaked}"
