BD_GATE_SCOPE = "module"

from bulk_downloader.extractors_vixen import extract_from_html

URL = "https://cdn.vixen.com/video/mp4_1080/scene.mp4"


def test_source_tag_inside_video_is_downloadable():
    direct = extract_from_html(f'<video src="{URL}"></video>')
    assert direct.ok is True
    assert direct.url == URL

    nested = extract_from_html(f'<video><source src="{URL}" type="video/mp4"></video>')
    assert nested.ok is True
    assert nested.url == URL


def test_non_video_source_does_not_create_download():
    result = extract_from_html(f'<picture><source src="{URL}"></picture>')
    assert result.ok is False
