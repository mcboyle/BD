"""O1567 fx-hls-dotleaf-name: an HLS manifest leaf ".m3u8" is not a file name.

Live (wrk-191, hustlerunlimited, results/wrk-191/hustlerunlimited.md): the
dacast manifest URL ends ".../<uuid>.ism/.m3u8?context=...", so the leaf was
".m3u8" and the 7 GB download was saved as the hidden file ".m3u8.mp4". Every
later scene on the site resolves to the same leaf, so skip_if_exists would
treat each one as already on disk.
"""
BD_GATE_SCOPE = "module"

from bulk_downloader.runner_transport import resolve_media_leaf_name


def test_dot_manifest_leaf_falls_back_to_title():
    got = resolve_media_leaf_name(
        ".m3u8", website_title="One Dare Too Many", tier="1080p",
        scene_url="https://hustlerunlimited.com/videos/one-dare-too-many/")
    assert got == "One Dare Too Many [1080p].mp4", got


def test_dot_manifest_leaf_falls_back_to_scene_slug_without_title():
    got = resolve_media_leaf_name(
        ".m3u8", tier="1080p",
        scene_url="https://hustlerunlimited.com/videos/one-dare-too-many/")
    assert got == "one-dare-too-many [1080p].mp4", got


def test_real_name_is_unchanged():
    # negative control: a real stem is the site's own name and stays.
    assert resolve_media_leaf_name(
        "One_Dare_1080p.mp4", website_title="X", tier="1080p") == "One_Dare_1080p.mp4"
