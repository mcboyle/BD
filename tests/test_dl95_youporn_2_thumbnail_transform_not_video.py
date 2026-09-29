"""dl95-youporn-2: an image-transform (thumbnail-resize) CDN URL is not a video
candidate, and its rejection says "thumbnail".

O1513 youporn on test2: job 14926487 went needs_review with "Rejected navigation
URL (external/unrelated link): https://pix-cdn77.ypncdn.com/.../original_55078555
.mp4/plain/.../rs:fit:320:180". The URL is an imgproxy-style resize of the video
(``/plain/<options>/rs:fit:W:H``) -- a JPEG thumbnail. The fixtures below are real
URLs captured from https://www.youporn.com/watch/14926487/ on 2026-09-28 (HTML
``&amp;`` unescaped). The same transform with a resolution-shaped size
(``rs:fit:640:360``) scored ``resolution_label`` and was ACCEPTED as a download.
"""

import pytest

from bulk_downloader import candidate_filter as cf
from bulk_downloader import disco_triage, runner_util

BD_GATE_SCOPE = "module"

PAGE_HOST = "www.youporn.com"
PAGE_URL = "https://www.youporn.com/watch/14926487/"
THUMB = "thumbnail/image transform"

# Real captures (www.youporn.com/watch/14926487/, 2026-09-28).
REAL_THUMBS = [
    (
        "https://pix-cdn77.ypncdn.com/c6371/videos/202601/17/36345385/"
        "original_36345385.mp4/plain/rs:fit:320:180/vts:675"
        "?hash=Oi3F-gmW0vSJKNwSLCg43iAfT-I=&validto=1790640722"
    ),
    (
        "https://pix-cdn77.ypncdn.com/c6371/videos/202606/12/51998925/"
        "original_51998925.mp4/plain/ex:1:no/bg:0:0:0/rs:fit:320:180/vts:18"
        "?hash=9pRIatgZm99iGs8mpPsSPboO4U4=&validto=1790640722"
    ),
]
# Same CDN transform at a resolution-shaped size: BASE accepted it as a download.
RESOLUTION_SHAPED_THUMB = (
    "https://pix-cdn77.ypncdn.com/c6371/videos/202601/17/36345385/"
    "original_36345385.mp4/plain/rs:fit:640:360/vts:675"
)
# Real media on the same CDN family (captured alongside): must stay a download.
REAL_PREVIEW_MP4 = (
    "https://ev-ph.ypncdn.com/videos/202411/11/460383961/"
    "250507_1903_360P_360K_460383961_fb.mp4?validfrom=1790633522"
    "&validto=1790640722&rate=40k&burst=300k&hash=Cf5imQYNTUPySSotw2O4IdJzUdM%3D"
)


def _diag(url, v):
    return (
        f"DL95_YOUPORN_2_THUMB_NOT_NAMED url={url[:90]} "
        f"accepted={v.accepted} rejections={v.rejections} "
        f"signals={v.positive_signals}"
    )


@pytest.mark.parametrize("url", REAL_THUMBS + [RESOLUTION_SHAPED_THUMB])
def test_thumbnail_transform_rejected_as_thumbnail(url):
    v = cf.classify(url=url, page_host=PAGE_HOST)
    assert not v.accepted, _diag(url, v)
    # The only reason is the true one; "external/unrelated" misnames the
    # site's own thumbnail CDN.
    assert v.rejections == [THUMB], _diag(url, v)
    assert "thumbnail" in v.reason, _diag(url, v)


@pytest.mark.parametrize("url", REAL_THUMBS + [RESOLUTION_SHAPED_THUMB])
def test_thumbnail_transform_scores_no_media_signal(url):
    # The ranker must not score an image transform as video: no URL signal
    # (media_extension / resolution_label from "640:360") survives.
    assert cf.positive_signals(url) == [], (
        f"DL95_YOUPORN_2_THUMB_SCORED url={url[:90]} signals={cf.positive_signals(url)}"
    )


def test_thumbnail_rejected_without_page_host():
    # Row 776 preflight classifies a bare pending URL with no page_host (the
    # same-site branch is skipped): the transform must still not be media.
    v = cf.classify(url=RESOLUTION_SHAPED_THUMB)
    assert not v.accepted and v.rejections == [THUMB], _diag(RESOLUTION_SHAPED_THUMB, v)


class _Locator:
    def __init__(self, href):
        self._href = href

    def get_attribute(self, name):
        return self._href if name == "href" else None


@pytest.mark.parametrize("url", REAL_THUMBS + [RESOLUTION_SHAPED_THUMB])
def test_runtime_gate_names_thumbnail(url):
    # runner_transport writes "Rejected navigation URL (<reason>)" from this.
    abs_url, reason = runner_util.gate_candidate_url(_Locator(url), PAGE_URL)
    assert abs_url == url
    assert reason == THUMB, (
        f"DL95_YOUPORN_2_GATE_REASON url={url[:90]} reason={reason!r}"
    )


@pytest.mark.parametrize("url", REAL_THUMBS + [RESOLUTION_SHAPED_THUMB])
def test_disco_triage_drops_thumbnail(url):
    # Host enumeration must drop a thumbnail as structural junk, never queue it.
    tier, _score = disco_triage.triage_url(url, page_host=PAGE_HOST)
    assert tier == cf.TIER_REJECT, (
        f"DL95_YOUPORN_2_DISCO_TIER url={url[:90]} tier={tier}"
    )


# ── negative controls: real media and real external links are unchanged ──


def test_real_media_on_same_cdn_still_download():
    v = cf.classify(url=REAL_PREVIEW_MP4, page_host=PAGE_HOST)
    assert v.accepted and v.kind == "download", v
    assert "media_extension" in v.positive_signals
    assert runner_util.gate_candidate_url(_Locator(REAL_PREVIEW_MP4), PAGE_URL) == (
        REAL_PREVIEW_MP4,
        "",
    )


def test_unrelated_external_link_still_external():
    url = "https://www.google-analytics.com/collect?v=1"
    assert (
        "external/unrelated link"
        in cf.classify(url=url, page_host=PAGE_HOST).rejections
    )
    assert runner_util.gate_candidate_url(_Locator(url), PAGE_URL) == (
        url,
        "external/unrelated link",
    )


@pytest.mark.parametrize(
    "url",
    [
        # colon segments that are not resize options, and options in the query
        "https://cdn.example.com/v/12/clip_720p.mp4?next=/rs:fit:320:180/x",
        "https://cdn.example.com/video:1080/clip.mp4",
        "https://cdn.example.com/hls/s:720/master.m3u8",
    ],
)
def test_non_transform_media_unaffected(url):
    v = cf.classify(url=url)
    assert THUMB not in v.rejections, (url, v)
