"""v3.66.155 — full pipeline round-trip: capture -> build -> normalize ->
promote -> runtime consumption.

Locks in that the four stages stay shape-compatible: a DOM-bearing capture
builds a rich draft, the normalizer turns it into a runtime-shaped review
candidate, ``promote_template.py --enable`` writes an enabled reviewed
template, and the *runtime* (template_registry + template_assist) loads it and
produces working selectors / resolutions. If anyone changes the normalizer's
output shape or promote's writer in a way the runtime can't consume, this test
fails instead of a live capture session.

It also encodes the one intended manual-review step: the first-party API host
is scrubbed from the page capture (v151 no-host-guessing rule), so
``build_api_url`` returns None on a freshly promoted template and starts
working only once a reviewer adds the ``api{base}`` block.

Synthetic only — no browser, no network. The promote step runs the real CLI as
a subprocess writing into a temp dir (never the repo's templates/reviewed/, so
the gold app.reptyle.com template is never touched).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

import build_template_from_wacz as B  # noqa: E402
from bulk_downloader import template_assist as A  # noqa: E402
from bulk_downloader import template_normalize as N  # noqa: E402
from bulk_downloader import template_registry as R  # noqa: E402

_LADDER = [2160, 1080, 720, 480, 240]

_HLS = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=426x240
v240.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=854x480
v480.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720
v720.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1920x1080
v1080.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=16000000,RESOLUTION=3840x2160
v2160.m3u8
"""

_HTML = """<html><body>
<form><input id="email" type="email"><input id="password" type="password">
<button type="submit">Sign in</button></form>
<div class="theoplayer-skin"><button class="vjs-big-play-button"></button></div>
<button aria-label="Open the video quality settings menu"></button>
<button aria-label="Set video quality to 240p"></button>
<button aria-label="Set video quality to 480p"></button>
<button aria-label="Set video quality to 720p"></button>
<button aria-label="Set video quality to 1080p"></button>
<button aria-label="Set video quality to 2160p"></button>
<button aria-label="Download Full Movie">Download</button>
</body></html>"""


def _capture() -> dict:
    return {
        "url": "https://app.reptyle.com/movies/9",
        "host": "app.reptyle.com",
        "captured_at": "2026-06-05T00:00:00Z",
        "dom_log": [{"type": "full_snapshot", "label": "movie-page", "html": _HTML}],
        "network_log": [
            {"url": "https://api2.reptyle.com/api/v1/movie/9/download-resolution/1080?token=S&sig=A",
             "response_status": 200},
            {"url": "https://cdn.reptyle.com/hls/9/master.m3u8", "response_status": 200,
             "response_body": _HLS},
            {"url": "https://cdn.reptyle.com/m/9/AVC_2160.mp4", "response_status": 200},
        ],
    }


def _run_chain():
    """Build -> normalize -> promote(--enable) -> load. Returns
    (draft, candidate, loaded_template, workdir). Caller removes workdir."""
    work = Path(tempfile.mkdtemp(prefix="rt155_"))
    wacz = work / "reptyle_dom.wacz"
    with zipfile.ZipFile(wacz, "w") as z:
        z.writestr("capture.json", json.dumps(_capture()))

    draft = B.build_template(wacz)
    cand = N.normalize_draft(draft)

    cand_path = work / "cand.json"
    cand_path.write_text(json.dumps(cand, indent=2))
    reviewed = work / "reviewed"
    reviewed.mkdir()
    proc = subprocess.run(
        [sys.executable, str(_REPO / "tools/promote_template.py"),
         str(cand_path), "--out-dir", str(reviewed), "--enable"],
        cwd=str(_REPO), capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"promote failed: {proc.stderr or proc.stdout}"
    tpl = R.find_template_for_url("https://app.reptyle.com/movies/9", [str(reviewed)])
    return draft, cand, tpl, work


def test_build_stage_extracts_selectors_and_ladder() -> None:
    work = None
    try:
        draft, _cand, _tpl, work = _run_chain()
        sel = draft.get("selectors") or {}
        assert set(["login", "player", "quality", "download"]).issubset(sel)
        assert sel["download"].get("button_hint")
        assert draft.get("resolution_priority") == _LADDER
        assert draft.get("confidence") == "high"
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)


def test_normalize_stage_produces_review_ready_candidate() -> None:
    work = None
    try:
        _draft, cand, _tpl, work = _run_chain()
        assert cand.get("status") == "review_ready"
        assert cand["selectors"]["download"].get("trigger") == '[aria-label*="Download" i]'
        q = cand["selectors"]["quality"]
        assert q.get("open_menu") and q.get("resolution_option")
        assert cand.get("resolutions") == _LADDER
        assert cand.get("host") == "app.reptyle.com"
        # API stays relative until review; the m3u8 media pattern is present
        assert "/api/v{version}/movie/{movie_id}/download-resolution/{resolution}" \
            in cand["network_patterns"]
        assert ".../{manifest}.m3u8" in cand["network_patterns"]
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)


def test_promote_writes_enabled_template() -> None:
    work = None
    try:
        _draft, _cand, tpl, work = _run_chain()
        assert tpl is not None, "runtime could not load the promoted template"
        assert tpl.get("status") == "enabled"
        assert tpl.get("host") == "app.reptyle.com"
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)


def test_runtime_consumes_selectors_and_resolutions() -> None:
    work = None
    try:
        _draft, _cand, tpl, work = _run_chain()
        ld = A.template_to_learned_download(tpl)
        # the download trigger reaches the runtime as a click target
        assert '[aria-label*="Download" i]' in ld["trigger_selectors"]
        assert ld["trigger_selectors"], "no trigger selectors reached the runtime"
        assert A.preferred_resolutions(tpl) == _LADDER
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)


def test_api_url_gated_on_review_then_works() -> None:
    work = None
    try:
        _draft, _cand, tpl, work = _run_chain()
        # freshly promoted: no api block -> build_api_url returns None (the
        # scrubbed API host is a deliberate manual-review step)
        assert A.build_api_url(tpl, "download_resolution", movie_id=9, resolution=1080) is None
        # reviewer adds the api block -> the runtime can now build the URL
        tpl["api"] = {
            "base": "https://api2.reptyle.com/api/v1",
            "download_resolution": "movie/{movie_id}/download-resolution/{resolution}",
        }
        url = A.build_api_url(tpl, "download_resolution", movie_id=9, resolution=1080)
        assert url == "https://api2.reptyle.com/api/v1/movie/9/download-resolution/1080"
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)
