from bulk_downloader.template_assist import template_to_learned_download
from bulk_downloader.template_registry import find_template_for_url

ROW_SELECTOR = ".VideoJSPlayer-Modal .VideoJSPlayer-DownloadOption-Link"
RESOLUTIONS = [160, 240, 360, 480, 540, 720, 1080, 2160]


def test_filthykings_template_is_enabled_after_capped_probe():
    template = find_template_for_url(
        "https://members.filthykings.com/en/video/filthykings/example/123"
    )
    assert template is not None
    assert template["status"] == "enabled"
    assert template["selectors"]["download"]["row_selectors"] == [ROW_SELECTOR]
    assert template["resolutions"] == RESOLUTIONS
    assert template_to_learned_download(template)["row_selectors"] == [ROW_SELECTOR]


def test_filthykings_template_matches_canonical_host():
    assert find_template_for_url(
        "https://www.filthykings.com/en/video/filthykings/example/123"
    ) is not None


def test_filthykings_template_rejects_unlisted_sibling():
    assert find_template_for_url(
        "https://billing.filthykings.com/account"
    ) is None


if __name__ == "__main__":
    test_filthykings_template_is_enabled_after_capped_probe()
    test_filthykings_template_matches_canonical_host()
    test_filthykings_template_rejects_unlisted_sibling()
