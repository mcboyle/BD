"""dl95-porndoe-1-live-3 (RESULT-dl95-porndoe-1-live-3-2342Z.md; DECISION-dispatch-build-B-porndoe-live-3-2358Z.md).

Measured live on .95 (build 1e334559, tester-S127-B/porndoe): porndoe.com/watch/pd9v8b2a8c9s, min_resolution 1080p.
The player loaded ONE rendition's playlist, .../hls/6a9d0005a234b-565-480p-1000(-index).m3u8, the only candidate:
"chose 480p from page-media", then "needs_review: Best is 480p (below 1080p)", 0 B.  The guest master beside it,
.../hls/6a9d0005a234b-565.m3u8, answered HTTP 200 (705 B) with four STREAM-INF up to 1920x1080
(6a9d0005a234b-565-1080p-hd-6000-index.m3u8); that index and its seg0 fetched (ffprobe h264 1920x1080).

Contract after the fix: a porndoe <id>-<n>-<q>-<br>(-index).m3u8 capture is ranked by its master <id>-<n>.m3u8,
read through the page's own session; a master that cannot be read keeps today's 480p hold.
"""
from __future__ import annotations

import hashlib
import re
import threading

import pytest

from bulk_downloader import runner_extractors, spa_media_extract

BD_GATE_SCOPE = "module"

SCENE = "https://porndoe.com/watch/pd9v8b2a8c9s"
HLS = "https://prhls.cdnc.porndoe.com/kmk1hClT-WlVwx7hNs9ZcQ==,1792107542/movie/2/3/8/6/9/4/3/hls/"
CAPTURED = [HLS + "6a9d0005a234b-565-480p-1000.m3u8", HLS + "6a9d0005a234b-565-480p-1000-index.m3u8"]
MASTER = HLS + "6a9d0005a234b-565.m3u8"
# Reconstructed from tester-S127-B/porndoe/master-check.txt (the verbatim 705 B body was not saved): its four
# RESOLUTION values and URIs in order, as recorded.  BANDWIDTH is ASSUMED from each name's kbps field.
MASTER_BODY = (
    "#EXTM3U\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=428x240\n6a9d0005a234b-565-240p-400-index.m3u8\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=854x480\n6a9d0005a234b-565-480p-1000-index.m3u8\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720\n6a9d0005a234b-565-720p-hd-2500-index.m3u8\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1920x1080\n6a9d0005a234b-565-1080p-hd-6000-index.m3u8\n"
)
MEDIA_BODY = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:5.005,\nseg-1-v1-a1.ts\n#EXT-X-ENDLIST\n"
TOP = HLS + "6a9d0005a234b-565-1080p-hd-6000-index.m3u8"


# dl95-porndoe-1-live-3 r2: the stub keeps what the browser keeps -- FETCH_TEXT_JS returns a prefix of
# this many characters (read from the JS itself), with no signal that it was cut.
TEXT_JS_KEEPS = int(re.search(r"\.slice\(0,\s*(\d+)\)", spa_media_extract.FETCH_TEXT_JS).group(1))


class _Page:
    """The page's own fetch answers from *bodies*: FETCH_TEXT_JS as a silently cut prefix, the
    master read (FETCH_MASTER_TEXT_JS, [url, cap]) whole, or None past cap bytes; anything else reads empty."""

    def __init__(self, bodies):
        self.url, self.bodies, self.fetched = SCENE, bodies, []

    def evaluate(self, js, *a):
        if js == spa_media_extract.FETCH_TEXT_JS:
            self.fetched.append(a[0])
            status, text = self.bodies.get(a[0], (404, "Not Found"))
            return [status, text[:TEXT_JS_KEEPS]]
        if js == getattr(spa_media_extract, "FETCH_MASTER_TEXT_JS", None):
            url, cap = a[0]
            self.fetched.append(url)
            status, text = self.bodies.get(url, (404, "Not Found"))
            return [status, None if len(text.encode()) > cap else text]
        return []


class _Refused:
    ok, error, error_detail, bytes_written = False, "stub", "", 0


class _Stub:
    site_id = "dl95porndoe3"

    def __init__(self, tmp_path):
        self.config = {"name": "porndoe", "download_dir": str(tmp_path / "dl"), "min_resolution": 1080}
        self.jobs = {SCENE: {}}
        self._stop = threading.Event()
        self.updates, self.events, self.segmented = [], [], []
        self._spa_api_capture = None

    def _update_job(self, url, status, message, **extra):
        self.updates.append((status, message))

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))

    def _screenshot(self, page, url):
        return ""

    def _hls_download_guarded(self, _hls, manifest_url, output_path, **kwargs):
        self.segmented.append(manifest_url)       # the signed CDN is not reachable from a test
        return _Refused()

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(runner_extractors, "db_log", lambda *a, **k: None)
    return lambda: type("StubRunner", (_Stub, runner_extractors.ExtractorsMixin), {})(tmp_path)


def _held_480(r):
    return [m for s, m in r.updates if s == "needs_review" and "Best is 480p" in m]


def test_control_the_reconstructed_master_ranks_1080p_top():
    """Positive control: the ranker says yes on the recorded master (rule 7)."""
    best = spa_media_extract.best_hls_variant(spa_media_extract.hls_master_variants(MASTER_BODY, MASTER))
    assert best and (best["url"], best["height"]) == (TOP, 1080), best


def test_control_master_name_is_derived_for_porndoe_renditions_only():
    pick = spa_media_extract.porndoe_master_url
    assert pick(CAPTURED[0]) == pick(CAPTURED[1]) == pick(TOP) == MASTER
    assert pick(MASTER) is None and pick(TOP.replace("prhls.cdnc.porndoe.com", "cdn.example.com")) is None


def test_captured_480p_rendition_is_ranked_by_its_1080p_master(make):
    r = make()
    page = _Page({CAPTURED[0]: (200, MEDIA_BODY), CAPTURED[1]: (200, MEDIA_BODY), MASTER: (200, MASTER_BODY)})
    r._try_spa_api_media_extractor(SCENE, page, page_media=list(CAPTURED))
    assert MASTER in page.fetched and r.segmented, (
        f"DL95_PORNDOE_LIVE_3: master {MASTER[-24:]} never read; held={_held_480(r)} fetched={page.fetched}")
    assert r.segmented[0] == TOP, f"DL95_PORNDOE_LIVE_3: transferred {r.segmented[0]!r}, not the 1080p rendition"
    assert not _held_480(r), r.updates


def test_unreadable_master_keeps_the_480p_hold(make):
    """Negative/positive control: no master (404) -> today's behaviour, the 480p hold, nothing transferred."""
    r = make()
    page = _Page({CAPTURED[0]: (200, MEDIA_BODY), CAPTURED[1]: (200, MEDIA_BODY)})
    r._try_spa_api_media_extractor(SCENE, page, page_media=list(CAPTURED))
    assert _held_480(r) and not r.segmented, (r.updates, r.segmented)


def test_same_names_on_another_host_read_no_master(make):
    """Narrowness: the name pattern alone is not porndoe; no master is guessed elsewhere."""
    other = [u.replace("prhls.cdnc.porndoe.com", "cdn.example.com") for u in CAPTURED]
    r = make()
    page = _Page({other[0]: (200, MEDIA_BODY), other[1]: (200, MEDIA_BODY),
                  MASTER.replace("prhls.cdnc.porndoe.com", "cdn.example.com"): (200, MASTER_BODY)})
    r._try_spa_api_media_extractor(SCENE, page, page_media=other)
    assert sorted(page.fetched) == sorted(other) and _held_480(r) and not r.segmented, (page.fetched, r.updates)


# dl95-porndoe-1-live-3 r2 (ROUTE-lensrouter-A-dl95-porndoe-1-live-3-r1-REFUTE-0208Z.md, F1): a valid master of
# 200394 B -- 720p early, a legal 200000-character comment, then 1080p.  The lens's F1-input.m3u8, rebuilt.
_HEAD, _TAIL = MASTER_BODY.split("#EXT-X-STREAM-INF:BANDWIDTH=6000000")
LONG_MASTER_BODY = _HEAD + "#" + "x" * 200000 + "\n#EXT-X-STREAM-INF:BANDWIDTH=6000000" + _TAIL
TIER_720 = HLS + "6a9d0005a234b-565-720p-hd-2500-index.m3u8"


def test_control_long_master_is_the_lens_input_and_ranks_1080p_whole():
    """Positive control: this IS the lens's input, its prefix tops out at 720p, and read whole it ranks 1080p."""
    assert TEXT_JS_KEEPS == 200000
    raw = LONG_MASTER_BODY.encode()
    assert (len(raw), hashlib.sha256(raw).hexdigest()[:8]) == (200394, "7b05bf56")
    rank = lambda text: spa_media_extract.best_hls_variant(spa_media_extract.hls_master_variants(text, MASTER))
    assert rank(LONG_MASTER_BODY[:TEXT_JS_KEEPS])["height"] == 720
    assert (rank(LONG_MASTER_BODY)["url"], rank(LONG_MASTER_BODY)["height"]) == (TOP, 1080)


def test_long_master_is_ranked_whole_not_by_its_prefix(make):
    r = make()
    page = _Page({CAPTURED[0]: (200, MEDIA_BODY), CAPTURED[1]: (200, MEDIA_BODY), MASTER: (200, LONG_MASTER_BODY)})
    r._try_spa_api_media_extractor(SCENE, page, page_media=list(CAPTURED))
    held = [m for s, m in r.updates if s == "needs_review"]
    assert r.segmented and r.segmented[0] == TOP and not held, (
        f"DL95_PORNDOE_LIVE_3_R2 SIBLING-PREFIX: {len(LONG_MASTER_BODY)} B master ranked by a "
        f"{TEXT_JS_KEEPS}-char prefix; transferred={[u[-40:] for u in r.segmented]} held={held}")


@pytest.mark.parametrize("over", [1, 0])
def test_master_past_the_cap_is_unread_not_ranked(make, monkeypatch, over):
    """Past the hls r2 cap the master is unread (the 480p hold), never a partial ranking; at the cap it is read."""
    monkeypatch.setattr(runner_extractors, "_HLS_MASTER_BODY_CAP", len(MASTER_BODY.encode()) - over)
    r = make()
    page = _Page({CAPTURED[0]: (200, MEDIA_BODY), CAPTURED[1]: (200, MEDIA_BODY), MASTER: (200, MASTER_BODY)})
    r._try_spa_api_media_extractor(SCENE, page, page_media=list(CAPTURED))
    assert page.fetched.count(MASTER) == 1, page.fetched
    if over:
        assert _held_480(r) and not r.segmented, (r.updates, r.segmented)
        assert any(k == "spa_api_hls_sibling_master_unread" for k, _ in r.events), r.events
    else:
        assert r.segmented and r.segmented[0] == TOP and not _held_480(r), (r.updates, r.segmented)
