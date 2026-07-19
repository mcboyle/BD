"""F4 — expanded source extraction: srcset + bare <video src>.

State-blob (__NEXT_DATA__ / __APOLLO_STATE__ / Nuxt / Relay / Remix /
SvelteKit) and JSON-LD extraction were already implemented
(extract_state_blob_urls, extract_jsonld_media). The remaining F4 gaps
were `srcset` (no handling at all) and a bare `<video src="...">`
attribute with no <source> child (the html5 path only iterated <source>
children). These tests cover the additions.
"""
import pytest

from bulk_downloader.deep_detect import extract_player_configs, _parse_srcset


def test_parse_srcset_width_descriptors():
    out = _parse_srcset("v-720.mp4 720w, v-1080.mp4 1080w")
    assert out == [("v-720.mp4", "720w"), ("v-1080.mp4", "1080w")]


def test_parse_srcset_density_and_bare():
    out = _parse_srcset("a.mp4 2x, b.mp4")
    assert ("a.mp4", "2x") in out
    assert ("b.mp4", "") in out


def test_parse_srcset_empty():
    assert _parse_srcset("") == []
    assert _parse_srcset("   ") == []


def test_source_srcset_yields_one_per_candidate():
    html = ('<video><source type="video/mp4" '
            'srcset="v-720.mp4 720w, v-1080.mp4 1080w"></video>')
    cfgs = extract_player_configs(html, base_url="https://x.com/")
    urls = [c["url"] for c in cfgs]
    assert "https://x.com/v-720.mp4" in urls
    assert "https://x.com/v-1080.mp4" in urls
    assert all(c["found_in"] == "<video><source srcset>" for c in cfgs)


def test_source_srcset_width_becomes_quality_hint():
    html = '<video><source srcset="v.mp4 1920w" type="video/mp4"></video>'
    cfgs = extract_player_configs(html, base_url="https://x.com/")
    q = cfgs[0]["quality"]
    assert q is not None
    assert q["width"] == 1920
    assert q["rank"] == 1920


def test_bare_video_src_attribute():
    """<video src="..."> with no <source> child must be extracted."""
    html = '<video src="https://x.com/direct.mp4"></video>'
    cfgs = extract_player_configs(html, base_url="https://x.com/")
    assert any(c["url"] == "https://x.com/direct.mp4"
               and c["found_in"] == "<video src>" for c in cfgs)


def test_bare_video_data_src_attribute():
    html = '<video data-src="lazy.mp4"></video>'
    cfgs = extract_player_configs(html, base_url="https://x.com/")
    assert "https://x.com/lazy.mp4" in [c["url"] for c in cfgs]


def test_source_src_and_srcset_both_extracted():
    """A <source> with both src and srcset yields all URLs."""
    html = ('<video><source src="main.mp4" '
            'srcset="alt-720.mp4 720w" type="video/mp4"></video>')
    cfgs = extract_player_configs(html, base_url="https://x.com/")
    urls = set(c["url"] for c in cfgs)
    assert "https://x.com/main.mp4" in urls
    assert "https://x.com/alt-720.mp4" in urls


def test_videojs_path_unaffected_by_srcset_addition():
    html = ('<video class="video-js" '
            "data-setup='{\"sources\":[{\"src\":\"v.mp4\"}]}'></video>")
    cfgs = extract_player_configs(html, base_url="https://x.com/")
    assert any(c["library"] == "videojs" for c in cfgs)


def test_plain_source_src_still_works():
    """Regression: the original <source src> path is unchanged."""
    html = '<video><source src="v.webm" type="video/webm"></video>'
    cfgs = extract_player_configs(html, base_url="https://x.com/")
    assert "https://x.com/v.webm" in [c["url"] for c in cfgs]
