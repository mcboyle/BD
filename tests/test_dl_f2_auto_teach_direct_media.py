"""dl-f2 (findings/APP-DOWNLOAD-TEST-20260928.md#F2): a bare direct-media URL on a fresh site must not be
parked in needs_review for selector teaching by the WORKER path.

runner.start()'s auto-teach preflight exempts a pending URL that candidate_filter already accepts as direct
media (Row 776, `_pending_url_already_downloadable`), but the per-URL worker check
`runner_teach._handle_auto_teach_check` had no such exemption, so on test5 (v3.66.1706) the job
https://file-examples.com/wp-content/storage/2017/04/file_example_MP4_480_1_5MG.mp4 on a site with no learned
selectors went to needs_review with "Auto-teach: ... Click the download button by hand".

Calls the REAL method on a real SiteRunner in an isolated BD home (clean_workdir + db_init); no browser and no
network: classify(url=...) is a pure string check.
"""

import pytest

BD_GATE_SCOPE = "module"

MP4 = "https://file-examples.com/wp-content/storage/2017/04/file_example_MP4_480_1_5MG.mp4"
WEBM = "https://file-examples.com/storage/fe/file_example_WEBM_480_900KB.webm"
PAGE = "https://file-examples.com/index.php/sample-video-files/sample-mp4-files/"


@pytest.fixture
def runner(clean_workdir):
    from bulk_downloader.db import db_init
    from bulk_downloader.runner import SiteRunner

    db_init()
    (clean_workdir / "screenshots").mkdir(exist_ok=True)
    # fresh site: no learned download selectors, no template, auto-teach on (the default)
    return SiteRunner("dlf2", {"name": "dlf2", "auto_teach_first_run": True})


def _check(r, url):
    r.jobs[url] = {"status": "pending", "message": ""}
    return r._handle_auto_teach_check(url, r.jobs[url])


@pytest.mark.parametrize("url", [MP4, WEBM])
def test_direct_media_url_is_not_sent_to_teach(runner, url):
    from bulk_downloader.runner import _pending_url_already_downloadable

    # positive control: the start() preflight's own predicate calls this URL direct media
    assert _pending_url_already_downloadable(url), f"control: classifier no longer accepts {url}"
    handled = _check(runner, url)
    job = runner.jobs[url]
    assert handled is False, f"worker auto-teach took a direct-media URL: {job}"
    assert job["status"] == "pending", job
    assert not job.get("auto_teach_seen"), job


def test_page_url_still_goes_to_teach(runner):
    """Negative control: a listing PAGE on a site with no selectors keeps the Phase 19 teach behaviour."""
    handled = _check(runner, PAGE)
    job = runner.jobs[PAGE]
    assert handled is True, job
    assert job["status"] == "needs_review", job
    assert job.get("auto_teach_seen") is True, job


def test_direct_media_does_not_block_a_later_page_teach(runner):
    """The exempted media job must not count as 'a URL already in teach' for the next page URL."""
    assert _check(runner, MP4) is False
    assert _check(runner, PAGE) is True
    assert runner.jobs[PAGE]["status"] == "needs_review"
    assert runner.jobs[MP4]["status"] == "pending"
