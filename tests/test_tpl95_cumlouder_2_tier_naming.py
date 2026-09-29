"""tpl95-cumlouder-2 -- bare media leaf tier must come from the element's
label/res or the probed height, never a default/floor value.

Defect observed on test2 (O1517 / live-tpl95-cumlouder-1):
cumlouder scene /porn-video/masked-women-sucking-black-cock/ matched via learned
[video source[src*='.mp4']] and was named 'Masked women sucking black cock _
Cumlouder.com [1080p].mp4', but ffprobe of the landed stream was 640x360 (360p).
The <source> had no quality label; [NNNp] must come from the element's label/res
or the probed height, never a default/floor value.

Contract:
1. <source> with res="720" and no label attribute: _learned_media_row reads res
   attribute, yielding "720p", so the candidate scores 720.
2. <source> with neither label nor res: score is 0; resolve_media_leaf_name
   leaves the suggested filename untagged (no [1080p] or floor tier).
3. Landed file: after download and integrity verification, if the file was a
   bare media leaf and ffprobe measures a real video height (e.g. 360),
   the landed file on disk is renamed to reflect the probed height ([360p]),
   filename is updated, and best["score"] is updated to the probed height.
4. Misleading label: if a <source> claimed 1080p but ffprobe reveals 360p,
   the file is renamed to [360p], correcting the tag to the probed height.
"""

BD_GATE_SCOPE = "module"

import logging
import threading
from pathlib import Path
from unittest.mock import MagicMock

from bulk_downloader import runner_transport as transport
from bulk_downloader.detect import _learned_media_row
from bulk_downloader.runner_auth import AuthMixin

_FIX = Path(__file__).parent / "fixtures" / "tpl95_template_media_rows"
CUMLOUDER_URL = "https://www.cumlouder.com/porn-video/rock-hard-cock/"


class _StubElement:
    def __init__(self, tag, attrs):
        self._tag = tag
        self._attrs = attrs

    def evaluate(self, _script):
        return self._tag

    def get_attribute(self, name):
        return self._attrs.get(name)


class _TestRunner(transport.TransportMixin):
    _login_wall_rejects = getattr(AuthMixin, "_login_wall_rejects", lambda self, *a: False)

    def __init__(self, tmp_path, probed_height=360):
        self.site_id = "tpl95"
        self.config = {
            "name": "CumLouder",
            "use_http_dl": True,
            "verify_hash": False,
            "verify_integrity": True,
            "learned": {
                "download": {
                    "row_selectors": ["video source[src*='.mp4']"],
                    "url_attribute": "src",
                }
            },
        }
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.jobs = {}
        self.log = logging.getLogger("tpl95_test")
        self.dl_dir = tmp_path
        self._mock_probed_height = probed_height

    def _probe_for_higher_tier(self, url, **kw):
        return url

    def _build_mirror_urls(self, url):
        return []

    def _history_title_fields(self, _url):
        return {"title": "Masked women sucking black cock _ Cumlouder.com"}

    def _verify_integrity_or_quarantine(self, page_url, final_path, filename, downloaded_size):
        return True, False, ""

    def _probe_video_height(self, _path):
        return self._mock_probed_height

    def _embed_metadata_if_mp4(self, *a, **kw):
        pass

    def _size_on_disk_after_tagging(self, _path, size):
        return size

    def _update_job(self, url, status, msg="", **kw):
        self.jobs[url] = {"status": status, "msg": msg, **kw}

    def log_event(self, *a, **kw):
        pass


def test_learned_media_row_reads_res_attribute_when_label_absent():
    el = _StubElement("source", {
        "src": "https://m2cdnst.cumlouder.com/video.mp4",
        "res": "720",
    })
    url, label = _learned_media_row(el, "src")
    assert url == "https://m2cdnst.cumlouder.com/video.mp4"
    assert label == "720p", f"expected '720p' from res='720', got {label!r}"


def test_unlabelled_media_source_has_score_0_and_no_floor_tier():
    el = _StubElement("source", {
        "src": "https://z6v2p9a8.bkcdn.net/library/636689/14f30c3279f00cd55586f41cb667efc00ff4ee1b.mp4",
    })
    url, label = _learned_media_row(el, "src")
    assert url.endswith("14f30c3279f00cd55586f41cb667efc00ff4ee1b.mp4")
    assert label == "", f"expected empty label for unlabelled source, got {label!r}"

    # Before download, a score-0 bare media leaf must not get a floor tier like [1080p]
    _named = transport.resolve_media_leaf_name(
        "14f30c3279f00cd55586f41cb667efc00ff4ee1b.mp4",
        website_title="Masked women sucking black cock _ Cumlouder.com",
        tier="",
        scene_url="https://www.cumlouder.com/porn-video/masked-women-sucking-black-cock/",
    )
    assert _named == "Masked women sucking black cock _ Cumlouder.com.mp4"
    assert "[1080p]" not in _named


def test_landed_bare_media_leaf_reconciles_filename_with_probed_height(tmp_path):
    runner = _TestRunner(tmp_path, probed_height=360)
    page_url = "https://www.cumlouder.com/porn-video/masked-women-sucking-black-cock/"

    # Candidate had score=0 (unlabelled source)
    locator = MagicMock()
    locator.get_attribute.side_effect = lambda a: "https://m4cdnst.cumlouder.com/e1490523b8cd2c130b29656613850cf8.mp4" if a == "src" else None
    best = {
        "locator": locator,
        "score": 0,
        "_via_learned": True,
        "_learned_sel": "video source[src*='.mp4']",
        "text": "https://m4cdnst.cumlouder.com/e1490523b8cd2c130b29656613850cf8.mp4",
    }

    # Simulate direct download writing bytes to disk
    def _fake_http(_page_url, _page, _ctx, _file_url, attempt_urls, final_path):
        final_path.write_bytes(b"dummy mp4 bytes" * 100)
        return len(b"dummy mp4 bytes" * 100), len(b"dummy mp4 bytes" * 100)

    runner._run_http_attempts_with_resume = _fake_http

    page = MagicMock()
    page.url = page_url
    ctx = MagicMock()

    # Call _do_download with candidate and bare leaf
    runner._do_download(page, ctx, page_url, best, tmp_path, "auto")

    # The landed file on disk must be renamed to reflect probed height 360p
    expected_name = "Masked women sucking black cock _ Cumlouder.com [360p].mp4"
    landed_files = [f.name for f in tmp_path.glob("*.mp4")]
    assert expected_name in landed_files, f"expected {expected_name!r} on disk, found {landed_files}"
    assert runner.jobs[page_url]["filename"] == expected_name
    assert best["score"] == 360


def test_landed_bare_media_leaf_corrects_misleading_label_with_probed_height(tmp_path):
    runner = _TestRunner(tmp_path, probed_height=360)
    page_url = "https://www.cumlouder.com/porn-video/masked-women-sucking-black-cock/"

    # Candidate claimed score=1080 (misleading label='1080p')
    locator = MagicMock()
    locator.get_attribute.side_effect = lambda a: "https://m4cdnst.cumlouder.com/e1490523b8cd2c130b29656613850cf8.mp4" if a == "src" else None
    best = {
        "locator": locator,
        "score": 1080,
        "_via_learned": True,
        "_learned_sel": "video source[src*='.mp4']",
        "text": "1080p https://m4cdnst.cumlouder.com/e1490523b8cd2c130b29656613850cf8.mp4",
    }

    def _fake_http(_page_url, _page, _ctx, _file_url, attempt_urls, final_path):
        final_path.write_bytes(b"dummy mp4 bytes" * 100)
        return len(b"dummy mp4 bytes" * 100), len(b"dummy mp4 bytes" * 100)

    runner._run_http_attempts_with_resume = _fake_http

    page = MagicMock()
    page.url = page_url
    ctx = MagicMock()

    # Call _do_download with candidate claiming 1080p
    runner._do_download(page, ctx, page_url, best, tmp_path, "1080p")

    # Probed height is 360, so the misleading [1080p] must be corrected to [360p]
    expected_name = "Masked women sucking black cock _ Cumlouder.com [360p].mp4"
    landed_files = [f.name for f in tmp_path.glob("*.mp4")]
    assert expected_name in landed_files, f"expected {expected_name!r} on disk, found {landed_files}"
    assert "Masked women sucking black cock _ Cumlouder.com [1080p].mp4" not in landed_files
    assert runner.jobs[page_url]["filename"] == expected_name
    assert best["score"] == 360


def test_custom_filename_template_preserved_during_tier_reconciliation(tmp_path):
    runner = _TestRunner(tmp_path, probed_height=360)
    runner.config["filename_template"] = "ARCHIVE-{filename}"
    page_url = "https://www.cumlouder.com/porn-video/masked-women-sucking-black-cock/"

    locator = MagicMock()
    locator.get_attribute.side_effect = lambda a: "https://m4cdnst.cumlouder.com/e1490523b8cd2c130b29656613850cf8.mp4" if a == "src" else None
    best = {
        "locator": locator,
        "score": 1080,
        "_via_learned": True,
        "_learned_sel": "video source[src*='.mp4']",
        "text": "1080p https://m4cdnst.cumlouder.com/e1490523b8cd2c130b29656613850cf8.mp4",
    }

    def _fake_http(_page_url, _page, _ctx, _file_url, attempt_urls, final_path):
        final_path.write_bytes(b"dummy mp4 bytes" * 100)
        return len(b"dummy mp4 bytes" * 100), len(b"dummy mp4 bytes" * 100)

    runner._run_http_attempts_with_resume = _fake_http

    page = MagicMock()
    page.url = page_url
    ctx = MagicMock()

    runner._do_download(page, ctx, page_url, best, tmp_path, "1080p")

    # Custom template prefix "ARCHIVE-" must be preserved, and tier corrected to [360p]
    expected_name = "ARCHIVE-Masked women sucking black cock _ Cumlouder.com [360p].mp4"
    landed_files = [f.name for f in tmp_path.glob("*.mp4")]
    assert len(landed_files) == 1, f"expected 1 file, found {landed_files}"
    assert landed_files[0] == expected_name, f"expected {expected_name!r}, got {landed_files[0]!r}"
    assert landed_files[0].startswith("ARCHIVE-"), f"ARCHIVE- prefix dropped: {landed_files}"
    assert "[360p]" in landed_files[0], f"tier not corrected: {landed_files}"
    assert runner.jobs[page_url]["filename"] == expected_name
    assert best["score"] == 360

