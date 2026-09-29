"""Unknown learned media height must report a measured below-minimum landing."""
import shutil
import subprocess
from unittest.mock import MagicMock

import pytest
from bulk_downloader import runner_transport as transport
from test_tpl95_cumlouder_2_tier_naming import _TestRunner

BD_GATE_SCOPE = "module"

@pytest.fixture
def video(tmp_path):
    out = tmp_path / "fixture.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=black:s=640x360:r=1", "-t", "1", "-c:v",
                    "mpeg4", str(out)], check=True, capture_output=True)
    assert transport.TransportMixin._probe_video_height(out) == 360
    return out

@pytest.mark.parametrize("leaf,minimum,forced,flag,score", [
    ("video.mp4", 1080, False, True, 0),
    ("scene-download.mp4", 1080, False, True, 0),
    ("video.mp4", 360, False, False, 0),
    ("video.mp4", 0, False, False, 0),
    ("video.mp4", 1080, True, False, 0),
    # tpl95-cumlouder-3-live-1 (DOT95-LANE/live-tpl95-cumlouder-3, .183 9298b1c7): the live learned <source>
    # carried a score -- its bare hash leaf was first named "[1080p]" -- and landed 360p unflagged.
    ("ec525c7ebe8b2a26d784e7ac5b0e0b81.mp4", 1080, False, True, 1080),
    ("video.mp4", 1080, False, True, 1080),
    ("ec525c7ebe8b2a26d784e7ac5b0e0b81.mp4", 360, False, False, 1080),
    ("ec525c7ebe8b2a26d784e7ac5b0e0b81.mp4", 1080, True, False, 1080),
])
def test_learned_unknown_landing_reports_measured_minimum(tmp_path, monkeypatch, video,
                                                        leaf, minimum, forced, flag, score):
    dest = tmp_path / "downloads"
    dest.mkdir()
    runner = _TestRunner(dest)
    runner.config["min_resolution"] = minimum
    runner._probe_video_height = transport.TransportMixin._probe_video_height
    url = "https://www.cumlouder.com/porn-video/fixture-scene/"
    runner.jobs[url] = {"force_download": forced}
    def update(u, status, msg="", **kw):
        runner.jobs.setdefault(u, {}).update(status=status, msg=msg, **kw)
    runner._update_job = update
    events = []
    runner.log_event = lambda *a, **kw: events.append((a, kw))
    history = []
    monkeypatch.setattr(transport, "db_log", lambda *a, **kw: history.append((a, kw)))
    fetched = []
    def transfer(_url, _page, _ctx, media, attempts, final):
        fetched.append(media)
        shutil.copyfile(video, final)
        return final.stat().st_size, final.stat().st_size
    runner._run_http_attempts_with_resume = transfer
    locator = MagicMock()
    locator.get_attribute.side_effect = lambda attr: f"https://media.fixture.test/{leaf}" if attr == "src" else None
    best = {"locator": locator, "score": score, "text": "", "_via_learned": True,
            "_learned_sel": "video source[src*='.mp4']"}
    page = MagicMock()
    page.url = url
    runner._do_download(page, MagicMock(), url, best, dest, "auto")
    job = runner.jobs[url]
    assert len(fetched) == 1 and job["status"] == "done"
    landed = dest / job["filename"]
    assert landed.is_file() and transport.TransportMixin._probe_video_height(landed) == 360
    assert len(list(dest.glob("*.mp4"))) == 1
    warning = "below the 1080p minimum (height unknown before download)"
    assert (warning in job["msg"]) == flag, f"LEARNED_LANDING_MINIMUM_UNREPORTED: {job['msg']}"
    assert len([e for e in events if e[0][0] == "learned_media_below_minimum"]) == int(flag)
