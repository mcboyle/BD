from bulk_downloader.template_registry import find_template_for_url
from bulk_downloader.template_assist import (
    build_api_url,
    preferred_resolutions,
    selector_group,
    template_summary,
    template_to_learned_download,
)


def test_template_assist_summary_and_selectors():
    t = find_template_for_url("https://app.reptyle.com/movie/123")
    s = template_summary(t)

    assert s["enabled"] is True
    assert s["host"] == "app.reptyle.com"
    assert "player" in s["selectors"]

    assert selector_group(t, "login")["email"] == 'input[type="email"]'
    assert "video" in selector_group(t, "player")["container"]


def test_template_assist_resolution_order():
    t = find_template_for_url("https://app.reptyle.com/movie/123")
    vals = preferred_resolutions(t)

    assert vals[0] == 2160
    assert 1080 in vals
    assert vals == sorted(vals, reverse=True)


def test_template_to_learned_download_shape():
    t = find_template_for_url("https://app.reptyle.com/movie/123")
    learned = template_to_learned_download(t)

    assert learned["row_selectors"]
    assert learned["trigger_selectors"]
    assert any("Download" in x or "download" in x.lower() for x in learned["row_selectors"])
    assert any("quality" in x.lower() or "2160" in x for x in learned["trigger_selectors"])


def test_template_assist_builds_reviewed_api_url():
    t = find_template_for_url("https://app.reptyle.com/movie/123")

    url = build_api_url(
        t,
        "download_resolution",
        movie_id=123,
        resolution=2160,
    )

    assert url == "https://api2.reptyle.com/api/v1/movie/123/download-resolution/2160"
    assert "token=" not in url
    assert "signature=" not in url
    assert "expires=" not in url
