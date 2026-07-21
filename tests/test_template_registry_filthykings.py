from bulk_downloader.template_registry import find_template_for_url


def test_filthykings_template_matches_authenticated_member_scene():
    template = find_template_for_url(
        "https://members.filthykings.com/en/video/filthykings/example/123"
    )
    assert template is not None
    assert template["status"] == "enabled"
    assert template["host"] == "www.filthykings.com"
    assert template["selectors"]["download"]["trigger"] == (
        '[title*="Download" i]'
    )


def test_filthykings_template_rejects_unlisted_sibling():
    assert find_template_for_url(
        "https://billing.filthykings.com/account"
    ) is None


if __name__ == "__main__":
    test_filthykings_template_matches_authenticated_member_scene()
    test_filthykings_template_rejects_unlisted_sibling()
