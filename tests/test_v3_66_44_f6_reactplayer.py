"""F6 — ReactPlayer player-library config extraction.

ReactPlayer's public API is a `url` prop (`<ReactPlayer url="..." />`),
NOT a `sources:[...]` array, so the generic sources walker in
extract_player_configs never caught it — the module comment promised a
fallback recognizer that didn't exist. These tests cover the recognizer
added for F6.
"""
import pytest


def test_reactplayer_double_quoted_url():
    from bulk_downloader.deep_detect import extract_player_configs
    html = '<ReactPlayer url="https://x.com/v.mp4" controls />'
    cfgs = extract_player_configs(html, base_url="https://x.com/")
    assert any(c["library"] == "react_player" for c in cfgs)
    urls = [c["url"] for c in cfgs]
    assert "https://x.com/v.mp4" in urls
    rp = next(c for c in cfgs if c["library"] == "react_player")
    assert rp["source_type"] == "react_player_source"
    assert rp["found_in"] == "<ReactPlayer url=>"


def test_reactplayer_single_quoted_url_with_preceding_props():
    """url= may not be the first prop; attribute order must not matter."""
    from bulk_downloader.deep_detect import extract_player_configs
    html = "<ReactPlayer playing width='100%' url='https://x.com/a.m3u8' />"
    cfgs = extract_player_configs(html, base_url="https://x.com/")
    urls = [c["url"] for c in cfgs]
    assert "https://x.com/a.m3u8" in urls


def test_reactplayer_jsx_expression_url():
    """url={"..."} — a JSX expression wrapping a string literal."""
    from bulk_downloader.deep_detect import extract_player_configs
    html = '<ReactPlayer url={"https://x.com/b.mpd"} />'
    cfgs = extract_player_configs(html, base_url="https://x.com/")
    assert "https://x.com/b.mpd" in [c["url"] for c in cfgs]


def test_reactplayer_array_url_strings_and_objects():
    """url={["a.mp4", {src:"b.mp4"}]} — multi-source array yields one
    entry per URL; {src} objects are unwrapped."""
    from bulk_downloader.deep_detect import extract_player_configs
    html = '<ReactPlayer url={["a.mp4", {src:"b.mp4"}]} />'
    cfgs = extract_player_configs(html, base_url="https://x.com/")
    urls = {c["url"] for c in cfgs if c["library"] == "react_player"}
    assert "https://x.com/a.mp4" in urls
    assert "https://x.com/b.mp4" in urls


def test_reactplayer_relative_url_resolves_against_base():
    from bulk_downloader.deep_detect import extract_player_configs
    html = '<ReactPlayer url="clips/v.mp4" />'
    cfgs = extract_player_configs(html, base_url="https://x.com/watch/")
    assert "https://x.com/watch/clips/v.mp4" in [c["url"] for c in cfgs]


def test_reactplayer_data_uri_skipped():
    """data:/blob: URIs are not downloadable sources; skip them."""
    from bulk_downloader.deep_detect import extract_player_configs
    html = '<ReactPlayer url="data:video/mp4;base64,AAAA" />'
    cfgs = extract_player_configs(html)
    assert [c for c in cfgs if c["library"] == "react_player"] == []


def test_reactplayer_in_script_body():
    """The prop also appears inside inline script JSX / hydration
    strings, not just rendered markup."""
    from bulk_downloader.deep_detect import extract_player_configs
    html = ('<script>const p = <ReactPlayer url="https://x.com/s.m3u8" '
            'playing />;</script>')
    cfgs = extract_player_configs(html, base_url="https://x.com/")
    assert "https://x.com/s.m3u8" in [c["url"] for c in cfgs]


def test_reactplayer_dedup_with_other_player_same_url():
    """If the same URL is surfaced by both ReactPlayer and another
    path, dedup keeps one entry (first wins)."""
    from bulk_downloader.deep_detect import extract_player_configs
    html = ('<video class="video-js" '
            "data-setup='{\"sources\":[{\"src\":\"https://x.com/v.mp4\"}]}'>"
            '</video>'
            '<ReactPlayer url="https://x.com/v.mp4" />')
    cfgs = extract_player_configs(html, base_url="https://x.com/")
    matching = [c for c in cfgs if c["url"] == "https://x.com/v.mp4"]
    assert len(matching) == 1


def test_reactplayer_no_false_positive_without_tag():
    """A bare url= attribute on some other element must not be picked
    up as ReactPlayer."""
    from bulk_downloader.deep_detect import extract_player_configs
    html = '<a url="https://x.com/not-a-player.mp4">link</a>'
    cfgs = extract_player_configs(html, base_url="https://x.com/")
    assert [c for c in cfgs if c["library"] == "react_player"] == []
