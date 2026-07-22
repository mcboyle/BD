from bulk_downloader.template_registry import find_template_for_url


def test_filthykings_template_is_not_runtime_enabled():
    assert find_template_for_url(
        "https://members.filthykings.com/en/video/filthykings/example/123"
    ) is None


def test_filthykings_template_rejects_unlisted_sibling():
    assert find_template_for_url(
        "https://billing.filthykings.com/account"
    ) is None


if __name__ == "__main__":
    test_filthykings_template_is_not_runtime_enabled()
    test_filthykings_template_rejects_unlisted_sibling()
